"""LCD controller: turns the configured display mode into frames on the screen.

Runs in its own thread.  Depending on the mode it either:

* tells the firmware to show its built-in liquid-temperature screen,
* uploads a GIF into a device bucket and lets the firmware play it
  ("render on device", the unchecked "Stream from CAM" option), or
* renders frames on the PC and streams them through the fast image path at
  the configured frame rate (infographics, clock, images, carousel, and
  GIFs with "stream from app").

Brightness and orientation changes are pushed as they appear in the config.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from typing import Optional

from PIL import Image

from .. import protocol
from ..config import ASSETS_DIR
from . import q565
from .renderer import SIZE, Renderer

LOGGER = logging.getLogger(__name__)

STREAMED_MODES = ("single", "dual", "triple", "clock", "image", "single_gif", "dual_gif", "triple_gif", "carousel")
MAX_CONSECUTIVE_FAILURES = 3
KEEPALIVE_SECONDS = 10.0
STATIC_RESEND_SECONDS = 10.0


# The Kraken Elite (2023) panel is mounted 90 degrees off: frames pushed through the
# bulk path and GIFs played from the device buckets come out rotated unless the
# content is pre-rotated by 270 degrees.  CAM does exactly this ("rotated-0-<id>.gif"
# in its media folder is the upload rotated 90 degrees counter-clockwise), and the
# firmware's own liquid screen does not need it.  Rotate Display is applied on top.
PANEL_BASE_ROTATION = 270


def panel_frame(frame: Image.Image) -> Image.Image:
    """Rotate a rendered frame into the panel's native orientation (see PANEL_BASE_ROTATION)."""
    transposes = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
    op = transposes.get(PANEL_BASE_ROTATION % 360)
    return frame.transpose(op) if op is not None else frame


class LcdController(threading.Thread):
    def __init__(self, daemon) -> None:
        super().__init__(name="lcd", daemon=True)
        self.app = daemon
        self.renderer = Renderer()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._preview: Optional[bytes] = None
        self._preview_id = 0
        self._last_frame: Optional[Image.Image] = None
        self.frames_sent = 0
        self.measured_fps = 0.0
        self.error: Optional[str] = None
        self.transport_used: Optional[str] = None
        self._streaming_ready = False
        self._device_mode: Optional[str] = None      # what the device currently shows
        self._applied_brightness: Optional[int] = None
        self._applied_orientation: Optional[int] = None
        self._gif_uploaded_path: Optional[str] = None
        self._last_keepalive = 0.0
        self._failures = 0
        self._carousel_index = 0
        self._carousel_started = 0.0
        self._media_start = time.time()
        self._fps_window: list[float] = []

    # ------------------------------------------------------------------ control
    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def notify(self) -> None:
        """Config changed: re-evaluate immediately."""
        self._wake.set()

    def device_reset(self) -> None:
        """Device was (re)connected: forget what we think it shows."""
        self._streaming_ready = False
        self._device_mode = None
        self._applied_brightness = None
        self._applied_orientation = None
        self._gif_uploaded_path = None
        self._failures = 0
        self._wake.set()

    # ------------------------------------------------------------------ preview
    def preview_jpeg(self) -> tuple[Optional[bytes], int]:
        with self._lock:
            return self._preview, self._preview_id

    def _set_preview(self, img: Image.Image) -> None:
        buf = io.BytesIO()
        img.resize((320, 320), Image.BILINEAR).save(buf, format="JPEG", quality=82)
        with self._lock:
            self._preview = buf.getvalue()
            self._preview_id += 1
            self._last_frame = img

    def state(self) -> dict:
        lcd = self.app.config.section("lcd")
        return {
            "mode": lcd.get("mode"),
            "device_mode": self._device_mode,
            "frames_sent": self.frames_sent,
            "fps": round(self.measured_fps, 1),
            "transport": self.transport_used,
            "error": self.error,
            "preview_id": self._preview_id,
            "native_encoder": q565.native_available(),
        }

    # ------------------------------------------------------------------ main loop
    def run(self) -> None:
        while not self._stop.is_set():
            device = self.app.device
            if device is None or not device.is_open:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            lcd = self.app.config.section("lcd")
            unit = self.app.config.section("general").get("temperature_unit", "C")
            try:
                self._apply_brightness(device, lcd)
                mode = lcd.get("mode", "single")
                if mode == "liquid":
                    self._apply_orientation(device, lcd, firmware_screen=True)
                    self._show_liquid(device)
                    self._wait(1.0)
                elif mode == "image" and not lcd.get("stream_gif", True) and self._is_gif(lcd):
                    self._apply_orientation(device, lcd, firmware_screen=False)
                    self._device_gif(device, lcd)
                    self._wait(1.0)
                else:
                    self._apply_orientation(device, lcd, firmware_screen=False)
                    self._stream_one(device, lcd, unit)
                self._failures = 0
            except protocol.DevicePermissionError as exc:
                self.error = str(exc)
                self._wait(5.0)
            except protocol.DeviceNotFound as exc:
                self.error = str(exc)
                self.app.device_lost()
                self._wait(2.0)
            except protocol.ProtocolError as exc:
                self._failures += 1
                self.error = str(exc)
                LOGGER.warning("LCD error (%d): %s", self._failures, exc)
                self._streaming_ready = False
                self._device_mode = None
                if self._failures >= MAX_CONSECUTIVE_FAILURES:
                    LOGGER.error("too many LCD failures, pausing for 5 s")
                    self._failures = 0
                    self._wait(5.0)
                else:
                    self._wait(0.5)
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                LOGGER.exception("unexpected LCD error: %s", exc)
                self.error = str(exc)
                self._wait(2.0)

    def _wait(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    # ------------------------------------------------------------------ brightness / orientation
    def _apply_brightness(self, device, lcd: dict) -> None:
        brightness = int(lcd.get("brightness", 80))
        if brightness != self._applied_brightness:
            device.set_brightness(brightness)
            self._applied_brightness = brightness

    def _apply_orientation(self, device, lcd: dict, firmware_screen: bool) -> None:
        # The firmware only rotates its own screens; streamed frames are rotated in software.
        degrees = int(lcd.get("orientation", 0)) % 360
        target = (round(degrees / 90) * 90) % 360 if firmware_screen else 0
        if target != self._applied_orientation:
            device.set_orientation(target)
            self._applied_orientation = target

    # ------------------------------------------------------------------ firmware liquid screen
    def _show_liquid(self, device) -> None:
        if self._device_mode != "liquid":
            device.show_liquid_screen()
            self._device_mode = "liquid"
            self._streaming_ready = False
            self.error = None
            self.transport_used = "firmware"
            self._set_preview(self._liquid_preview())

    def _liquid_preview(self) -> Image.Image:
        # Approximation of the firmware's own liquid temperature screen for the UI preview.
        snap = self.app.sensors.snapshot()
        spec = {"mode": "single", "reading": "liquid_temp", "color1": "#69fdfd", "color2": "#9802ff"}
        return self.renderer.render(spec, snap, self.app.config.section("general").get("temperature_unit", "C"))

    # ------------------------------------------------------------------ device-side GIF
    @staticmethod
    def _is_gif(lcd: dict) -> bool:
        path = lcd.get("screens", {}).get("image", {}).get("path", "")
        return path.lower().endswith(".gif")

    def _device_gif(self, device, lcd: dict) -> None:
        path = lcd.get("screens", {}).get("image", {}).get("path", "")
        if self._gif_uploaded_path != path or self._device_mode != "gif":
            with open(path, "rb") as fh:
                data = fh.read()
            prepared = self._prepare_gif_for_device(data, (int(lcd.get("orientation", 0)) + PANEL_BASE_ROTATION) % 360)
            LOGGER.info("uploading GIF to device (%d bytes)", len(prepared))
            device.upload_gif_to_bucket(prepared, index=0)
            self._gif_uploaded_path = path
            self._device_mode = "gif"
            self._streaming_ready = False
            self._last_keepalive = time.monotonic()
            self.transport_used = "device gif"
            self.error = None
            media = self.renderer.media(path)
            if media:
                self._set_preview(media.frame_at(0).image)
        elif time.monotonic() - self._last_keepalive > KEEPALIVE_SECONDS:
            device.keepalive_bucket(0)
            self._last_keepalive = time.monotonic()

    @staticmethod
    def _prepare_gif_for_device(data: bytes, rotation: int) -> bytes:
        """Resize/rotate a GIF to 640x640 for the firmware player (max ~24 MB)."""
        from PIL import ImageOps, ImageSequence
        img = Image.open(io.BytesIO(data))
        frames = []
        durations = []
        for frame in ImageSequence.Iterator(img):
            fitted = ImageOps.fit(frame.convert("RGB"), (SIZE, SIZE), Image.LANCZOS)
            if rotation % 360:
                fitted = fitted.rotate(-rotation, resample=Image.BICUBIC)
            frames.append(fitted.quantize(colors=256, method=Image.Quantize.MEDIANCUT))
            durations.append(frame.info.get("duration", 100))
        out = io.BytesIO()
        frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], loop=0,
                       duration=durations, disposal=1, optimize=False)
        result = out.getvalue()
        if len(result) > 24 * 1000 * 1000:
            raise protocol.ProtocolError("GIF too large for the device memory after conversion (24 MB max); use streaming")
        return result

    # ------------------------------------------------------------------ streaming
    def _current_spec(self, lcd: dict) -> dict:
        mode = lcd.get("mode", "single")
        screens = lcd.get("screens", {})
        if mode == "carousel":
            items = lcd.get("carousel", {}).get("items") or []
            interval = max(1.0, float(lcd.get("carousel", {}).get("interval", 5)))
            if not items:
                return {"mode": "blank"}
            now = time.monotonic()
            if now - self._carousel_started >= interval:
                self._carousel_started = now
                self._carousel_index = (self._carousel_index + 1) % len(items)
                self._media_start = time.time()
            self._carousel_index %= len(items)
            return dict(items[self._carousel_index])
        spec = dict(screens.get(mode, {}))
        spec["mode"] = mode
        return spec

    def _stream_one(self, device, lcd: dict, unit: str) -> None:
        if not self._streaming_ready:
            device.prepare_streaming()
            self._streaming_ready = True
            self._device_mode = "stream"
            self._media_start = time.time()
            self._carousel_started = time.monotonic()
            self.error = None
        spec = self._current_spec(lcd)
        spec["_start"] = self._media_start
        snapshot = self.app.sensors.snapshot()
        started = time.perf_counter()
        frame = self.renderer.render(spec, snapshot, unit, rotation=int(lcd.get("orientation", 0)))
        self._set_preview(frame)  # the preview shows the screen as the eye sees it
        self._send(device, panel_frame(frame), lcd)
        self.frames_sent += 1
        elapsed = time.perf_counter() - started
        # pacing: configured fps, but never faster than the media's own frame rate
        fps = max(1.0, min(30.0, float(lcd.get("fps", 10))))
        interval = 1.0 / fps
        natural = self.renderer.frame_duration(spec)
        mode = spec.get("mode")
        if mode in ("image", "single_gif", "dual_gif", "triple_gif"):
            if natural:
                interval = max(min(interval, natural), 1.0 / 30.0)
            elif mode == "image":
                interval = STATIC_RESEND_SECONDS  # still image: only a periodic refresh is needed
            else:
                interval = max(interval, 0.5)   # still image with live readings
        elif mode in ("single", "dual", "triple"):
            interval = max(interval, 0.5)  # sensor values change once per second anyway
        elif mode == "clock":
            interval = max(interval, 0.2)
        if lcd.get("mode") == "carousel":
            interval = min(interval, 1.0)  # keep carousel transitions on time
        self._fps_window.append(time.monotonic())
        self._fps_window = [t for t in self._fps_window if t > time.monotonic() - 5]
        if len(self._fps_window) > 1:
            self.measured_fps = (len(self._fps_window) - 1) / (self._fps_window[-1] - self._fps_window[0])
        self._wait(max(0.0, interval - elapsed))

    def _send(self, device, frame: Image.Image, lcd: dict) -> None:
        transport = lcd.get("transport", "auto")
        if transport == "auto":
            transport = "q565"
        if transport == "q565":
            payload = q565.encode(frame.tobytes(), frame.width, frame.height)
            device.send_frame(payload, protocol.DATA_Q565)
            self.transport_used = "q565" + ("" if q565.native_available() else " (fallback encoder)")
        else:
            bgr = frame.convert("RGB")
            b, g, r = bgr.split()
            payload = Image.merge("RGB", (b, g, r)).tobytes()
            device.send_frame(payload, protocol.DATA_BGR888, chunk_size=245760)
            self.transport_used = "bgr888"
