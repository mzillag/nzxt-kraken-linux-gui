"""USB protocol for the NZXT Kraken Elite (2023) and related fourth-generation Krakens.

Two USB interfaces are involved:

* interface 1: HID.  64-byte unnumbered reports; a request `(a, b, ...)` is
  answered by a report whose first two bytes are `(a + 1, b)`.  The device also
  broadcasts unsolicited status reports (`0x75 0x02`) about once per second,
  so every reply must be matched by prefix after draining stale reports.
  We talk to it through `/dev/hidrawN`, which the `nzxt_kraken3` kernel
  driver leaves available (it needs no detaching).
* interface 0: vendor specific with a single bulk OUT endpoint (0x02) used for
  image data.  No kernel driver binds it, so claiming it with libusb does not
  disturb the kernel hwmon driver.

Protocol knowledge comes from liquidctl, the nzxt-kraken3 kernel driver and
the reverse engineering done by the AIOLCDUnchained, kraken-elite-screen-manager
and nzxt-ctl projects.  See README for details and credits.
"""

from __future__ import annotations

import glob
import logging
import os
import select
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import usb.core
import usb.util

LOGGER = logging.getLogger(__name__)

VENDOR_ID = 0x1E71
PRODUCTS = {
    0x300C: ("NZXT Kraken Elite", (640, 640)),
    0x3012: ("NZXT Kraken Elite RGB", (640, 640)),
    0x300E: ("NZXT Kraken", (240, 240)),
    0x3014: ("NZXT Kraken Plus", (240, 240)),
}

REPORT_LENGTH = 64
BULK_ENDPOINT = 0x02
BULK_INTERFACE = 0
BULK_MAGIC = bytes([0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98, 0x76, 0x54, 0x32, 0x10])

# Bulk data types (byte 12 of the bulk header and last byte of the 0x36 0x01 request)
DATA_GIF = 0x01          # GIF file into a bucket; firmware plays it
DATA_RGBX = 0x02         # raw RGBX8888 into a bucket
DATA_RGB565_FW2 = 0x06   # raw RGB565 (non-Elite 2023, firmware 2.x)
DATA_Q565 = 0x08         # q565 compressed frame, direct "fast memory" (Elite)
DATA_BGR888 = 0x09       # raw BGR888 frame, direct "fast memory" (what CAM streams)

# LCD modes for 0x38 0x01
LCD_MODE_LIQUID = 0x02
LCD_MODE_BUCKET = 0x04

CURVE_TEMP_MIN = 20
CURVE_TEMP_MAX = 59  # critical temperature, the firmware forces 100% here
CURVE_POINTS = CURVE_TEMP_MAX - CURVE_TEMP_MIN + 1  # 40

PUMP_DUTY_MIN = 20
FAN_DUTY_MIN = 0
DUTY_MAX = 100

# Speed channel headers: pump / fan (2023 family)
SPEED_CHANNEL_PUMP = bytes([0x72, 0x01, 0x01, 0x00])
SPEED_CHANNEL_FAN = bytes([0x72, 0x02, 0x01, 0x01])

BOOTLOADER_PRODUCT_ID = 0x3011  # "NZXT BOOTarea": the device wedged itself


class ProtocolError(RuntimeError):
    pass


class DeviceNotFound(ProtocolError):
    pass


class DevicePermissionError(ProtocolError):
    pass


@dataclass
class DeviceInfo:
    product_id: int
    name: str
    hidraw_path: str
    usb_bus: int
    usb_address: int
    serial: str
    lcd_resolution: tuple[int, int]


@dataclass
class Status:
    liquid_temperature: float
    pump_rpm: int
    pump_duty: int
    fan_rpm: int
    fan_duty: int


@dataclass
class LcdInfo:
    brightness: int
    orientation: int  # 0..3 => 0/90/180/270 degrees
    width: int
    height: int


def discover() -> list[DeviceInfo]:
    """Find supported Krakens through sysfs (no privileges needed)."""
    found = []
    for hidraw in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = _read(os.path.join(hidraw, "device", "uevent")) or ""
        hid_id = None
        for line in uevent.splitlines():
            if line.startswith("HID_ID="):
                hid_id = line.split("=", 1)[1]
        if not hid_id:
            continue
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        try:
            vid, pid = int(parts[1], 16), int(parts[2], 16)
        except ValueError:
            continue
        if vid != VENDOR_ID or pid not in PRODUCTS:
            continue
        usb_dir = os.path.realpath(os.path.join(hidraw, "device"))
        # Walk up to the USB device directory (the one with busnum/devnum)
        while usb_dir != "/" and not os.path.exists(os.path.join(usb_dir, "busnum")):
            usb_dir = os.path.dirname(usb_dir)
        bus = int(_read(os.path.join(usb_dir, "busnum")) or 0)
        address = int(_read(os.path.join(usb_dir, "devnum")) or 0)
        serial = (_read(os.path.join(usb_dir, "serial")) or "").strip("\x00")
        name, resolution = PRODUCTS[pid]
        found.append(DeviceInfo(
            product_id=pid, name=name, hidraw_path="/dev/" + os.path.basename(hidraw),
            usb_bus=bus, usb_address=address, serial=serial, lcd_resolution=resolution,
        ))
    return found


def bootloader_present() -> bool:
    """True when a Kraken is enumerated as its bootloader (needs a power cycle)."""
    try:
        return usb.core.find(idVendor=VENDOR_ID, idProduct=BOOTLOADER_PRODUCT_ID) is not None
    except usb.core.USBError:
        return False


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


class KrakenDevice:
    """Thread-safe low level access to one Kraken."""

    def __init__(self, info: DeviceInfo) -> None:
        self.info = info
        self._hid_fd: Optional[int] = None
        self._usb: Optional[usb.core.Device] = None
        self._usb_claimed = False
        self._lock = threading.RLock()
        self.firmware: Optional[tuple[int, int, int]] = None
        self.lcd: Optional[LcdInfo] = None

    # ------------------------------------------------------------------ lifecycle
    def open(self) -> None:
        with self._lock:
            if self._hid_fd is None:
                try:
                    self._hid_fd = os.open(self.info.hidraw_path, os.O_RDWR | os.O_NONBLOCK)
                except PermissionError as exc:
                    raise DevicePermissionError(
                        f"no permission to open {self.info.hidraw_path}; install the udev rule"
                    ) from exc
                except FileNotFoundError as exc:
                    raise DeviceNotFound(str(exc)) from exc

    def open_bulk(self) -> None:
        """Claim the vendor interface for image uploads (lazy, only when needed)."""
        with self._lock:
            if self._usb_claimed:
                return
            dev = usb.core.find(
                idVendor=VENDOR_ID, idProduct=self.info.product_id,
                bus=self.info.usb_bus, address=self.info.usb_address,
            )
            if dev is None:
                raise DeviceNotFound("bulk interface of the Kraken not found")
            try:
                if dev.is_kernel_driver_active(BULK_INTERFACE):
                    dev.detach_kernel_driver(BULK_INTERFACE)
                usb.util.claim_interface(dev, BULK_INTERFACE)
            except usb.core.USBError as exc:
                if exc.errno in (13, 1):
                    raise DevicePermissionError(
                        "no permission to access the Kraken USB interface; install the udev rule"
                    ) from exc
                raise ProtocolError(f"cannot claim bulk interface: {exc}") from exc
            self._usb = dev
            self._usb_claimed = True

    def close(self) -> None:
        with self._lock:
            if self._usb is not None and self._usb_claimed:
                try:
                    usb.util.release_interface(self._usb, BULK_INTERFACE)
                    usb.util.dispose_resources(self._usb)
                except usb.core.USBError:
                    pass
            self._usb = None
            self._usb_claimed = False
            if self._hid_fd is not None:
                os.close(self._hid_fd)
                self._hid_fd = None

    @property
    def is_open(self) -> bool:
        return self._hid_fd is not None

    # ------------------------------------------------------------------ HID primitives
    def _write_report(self, payload: bytes) -> None:
        assert self._hid_fd is not None, "device not open"
        data = bytes(payload) + bytes(REPORT_LENGTH - len(payload))
        LOGGER.debug("HID -> %s", data[:12].hex(" "))
        written = os.write(self._hid_fd, data)
        if written != REPORT_LENGTH:
            raise ProtocolError(f"short HID write ({written} bytes)")

    def _read_report(self, timeout: float) -> Optional[bytes]:
        assert self._hid_fd is not None, "device not open"
        ready, _, _ = select.select([self._hid_fd], [], [], timeout)
        if not ready:
            return None
        try:
            data = os.read(self._hid_fd, REPORT_LENGTH)
        except BlockingIOError:
            return None
        LOGGER.debug("HID <- %s", data[:12].hex(" "))
        return data

    def drain(self) -> int:
        """Discard queued reports (status broadcasts) so replies are not misattributed."""
        count = 0
        while self._read_report(0) is not None:
            count += 1
        return count

    def command(self, payload: bytes, expect: Optional[bytes] = None, timeout: float = 1.0,
                max_reports: int = 48) -> Optional[bytes]:
        """Send a report and wait for the matching reply.

        `expect` defaults to `(payload[0] + 1, payload[1])`.  Returns the reply or
        raises ProtocolError when nothing matching arrives in time.
        """
        if expect is None:
            expect = bytes([(payload[0] + 1) & 0xFF, payload[1]])
        with self._lock:
            self.drain()
            self._write_report(payload)
            deadline = time.monotonic() + timeout
            for _ in range(max_reports):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                msg = self._read_report(remaining)
                if msg is None:
                    break
                if msg[:2] == expect:
                    return msg
            raise ProtocolError(
                f"no reply {expect.hex(' ')} to command {bytes(payload[:5]).hex(' ')}"
            )

    def write_only(self, payload: bytes) -> None:
        with self._lock:
            self._write_report(payload)

    # ------------------------------------------------------------------ bulk primitive
    def bulk_write(self, data: bytes, timeout_ms: int = 5000) -> None:
        assert self._usb is not None, "bulk interface not claimed"
        try:
            written = self._usb.write(BULK_ENDPOINT, data, timeout=timeout_ms)
        except usb.core.USBError as exc:
            raise ProtocolError(f"bulk write failed: {exc}") from exc
        if written != len(data):
            raise ProtocolError(f"short bulk write ({written}/{len(data)} bytes)")

    # ------------------------------------------------------------------ commands
    def initialize(self, send_init: bool = False) -> None:
        """Read firmware and LCD info.  `send_init` also sends the init handshake,
        which the kernel driver already did if it is bound."""
        with self._lock:
            if send_init:
                self.write_only(bytes([0x70, 0x02, 0x01, 0xB8, 0x01]))  # status every 0.5 s
                self.write_only(bytes([0x70, 0x01]))
            self.firmware = self.get_firmware_version()
            self.lcd = self.get_lcd_info()

    def get_firmware_version(self) -> tuple[int, int, int]:
        msg = self.command(bytes([0x10, 0x01]))
        return msg[0x11], msg[0x12], msg[0x13]

    def get_status(self) -> Status:
        msg = self.command(bytes([0x74, 0x01]))
        if msg[15] == 0xFF and msg[16] == 0xFF:
            raise ProtocolError("device reports invalid temperature (is SATA power connected?)")
        return Status(
            liquid_temperature=msg[15] + msg[16] / 10.0,
            pump_rpm=msg[17] | (msg[18] << 8),
            pump_duty=msg[19],
            fan_rpm=msg[23] | (msg[24] << 8),
            fan_duty=msg[25],
        )

    def get_lcd_info(self) -> LcdInfo:
        msg = self.command(bytes([0x30, 0x01]))
        width = msg[20] | (msg[21] << 8)
        height = msg[22] | (msg[23] << 8)
        if not (64 <= width <= 1024 and 64 <= height <= 1024):
            width, height = self.info.lcd_resolution
        info = LcdInfo(brightness=msg[0x18], orientation=msg[0x1A] & 0x3, width=width, height=height)
        self.lcd = info
        return info

    def set_brightness(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        orientation = self.lcd.orientation if self.lcd else 0
        with self._lock:
            self.write_only(bytes([0x30, 0x02, 0x01, percent, 0x00, 0x00, 0x01, orientation]))
            if self.lcd:
                self.lcd.brightness = percent

    def set_orientation(self, degrees: int) -> None:
        index = (int(degrees) // 90) % 4
        brightness = self.lcd.brightness if self.lcd else 50
        with self._lock:
            self.write_only(bytes([0x30, 0x02, 0x01, brightness, 0x00, 0x00, 0x01, index]))
            if self.lcd:
                self.lcd.orientation = index

    def set_lcd_mode(self, mode: int, bucket: int = 0) -> bool:
        msg = self.command(bytes([0x38, 0x01, mode, bucket]))
        return msg[14] == 0x01

    def show_liquid_screen(self) -> bool:
        return self.set_lcd_mode(LCD_MODE_LIQUID, 0)

    # ------------------------------------------------------------------ speed control
    @staticmethod
    def build_curve(points: list[tuple[float, float]], duty_min: int) -> list[int]:
        """Interpolate (temperature, duty) points into 40 integer duties for 20..59 °C."""
        pts = sorted((float(t), float(d)) for t, d in points)
        if not pts:
            raise ValueError("empty curve")
        out = []
        for temp in range(CURVE_TEMP_MIN, CURVE_TEMP_MAX + 1):
            if temp <= pts[0][0]:
                duty = pts[0][1]
            elif temp >= pts[-1][0]:
                duty = pts[-1][1]
            else:
                duty = pts[-1][1]
                for (t0, d0), (t1, d1) in zip(pts, pts[1:]):
                    if t0 <= temp <= t1:
                        duty = d0 if t1 == t0 else d0 + (d1 - d0) * (temp - t0) / (t1 - t0)
                        break
            out.append(int(max(duty_min, min(DUTY_MAX, round(duty)))))
        out[-1] = DUTY_MAX  # critical temperature failsafe, mirrors CAM/liquidctl
        return out

    def set_speed_curve(self, channel: str, duties: list[int]) -> None:
        """Upload a 40-point duty curve keyed on liquid temperature (20..59 °C)."""
        if len(duties) != CURVE_POINTS:
            raise ValueError(f"curve needs {CURVE_POINTS} points")
        header = SPEED_CHANNEL_PUMP if channel == "pump" else SPEED_CHANNEL_FAN
        duty_min = PUMP_DUTY_MIN if channel == "pump" else FAN_DUTY_MIN
        payload = header + bytes(max(duty_min, min(DUTY_MAX, int(d))) for d in duties)
        with self._lock:
            self.write_only(payload)

    def set_fixed_speed(self, channel: str, duty: int) -> None:
        duty_min = PUMP_DUTY_MIN if channel == "pump" else FAN_DUTY_MIN
        duty = max(duty_min, min(DUTY_MAX, int(duty)))
        curve = [duty] * CURVE_POINTS
        curve[-1] = DUTY_MAX
        self.set_speed_curve(channel, curve)

    # ------------------------------------------------------------------ LCD streaming
    def prepare_streaming(self) -> None:
        """Put the LCD into the state the fast frame path expects.

        Mirrors AIOLCDUnchained: switch to the liquid screen, then to bucket
        mode 0 so the firmware does not own the screen any more.
        """
        with self._lock:
            self.open_bulk()
            try:
                self.command(bytes([0x36, 0x03]), timeout=0.5)  # cancel any pending transfer
            except ProtocolError:
                pass
            try:
                self.set_lcd_mode(LCD_MODE_LIQUID, 0)
            except ProtocolError:
                pass
            time.sleep(0.1)
            try:
                self.set_lcd_mode(LCD_MODE_BUCKET, 0)
            except ProtocolError:
                pass

    def send_frame(self, payload: bytes, data_type: int = DATA_Q565, chunk_size: int = 0,
                   ack_timeout: float = 0.5) -> None:
        """Stream one frame through the direct ("fast memory") path.

        payload is a q565 stream (DATA_Q565) or a raw BGR888 buffer (DATA_BGR888).
        The firmware must acknowledge the start of transfer before any bulk data
        is sent; streaming without the ack is what pushes units into the bootloader.
        """
        with self._lock:
            self.open_bulk()
            msg = self.command(bytes([0x36, 0x01, 0x00, 0x01, data_type]), timeout=ack_timeout)
            if msg[14] != 0x01:
                # Firmware 2.x units have been seen answering with other status values while
                # still accepting the frame; only a missing ack is treated as a refusal.
                LOGGER.debug("frame transfer ack status 0x%02x", msg[14])
            header = BULK_MAGIC + bytes([data_type, 0, 0, 0]) + struct.pack("<I", len(payload))
            self.bulk_write(header)  # header must be its own short USB transfer
            if chunk_size and chunk_size < len(payload):
                for offset in range(0, len(payload), chunk_size):
                    self.bulk_write(payload[offset:offset + chunk_size])
            else:
                self.bulk_write(payload)
            try:
                self.command(bytes([0x36, 0x02]), timeout=ack_timeout)
            except ProtocolError:
                # Some firmware revisions do not answer the end-of-transfer report; the
                # frame is displayed regardless (kraken-unleashed never sends it).
                LOGGER.debug("no ack for end of transfer")

    # ------------------------------------------------------------------ buckets (GIF upload)
    def query_bucket(self, index: int) -> bytes:
        return self.command(bytes([0x30, 0x04, index]))

    def delete_bucket(self, index: int) -> bool:
        msg = self.command(bytes([0x32, 0x02, index]))
        return msg[14] == 0x01

    def setup_bucket(self, index: int, start_kib: int, size_kib: int) -> bool:
        msg = self.command(bytes([
            0x32, 0x01, index, index + 1,
            start_kib & 0xFF, (start_kib >> 8) & 0xFF,
            size_kib & 0xFF, (size_kib >> 8) & 0xFF, 0x01,
        ]))
        return msg[14] == 0x01

    def upload_gif_to_bucket(self, gif_data: bytes, index: int = 0, chunk_size: int = 1024 * 1024) -> None:
        """Upload a GIF into a bucket and display it; the firmware then animates it.

        Uses a fixed bucket and memory start to avoid the fragile bucket table
        arithmetic.  Aborts before sending bulk data if the firmware refuses.
        """
        with self._lock:
            self.open_bulk()
            try:
                self.command(bytes([0x36, 0x03]), timeout=0.5)
            except ProtocolError:
                pass
            try:
                self.set_lcd_mode(LCD_MODE_LIQUID, 0)
            except ProtocolError:
                pass
            self.delete_bucket(index)
            time.sleep(0.05)
            size_kib = (len(gif_data) + len(BULK_MAGIC) + 8 + 1023) // 1024 + 1
            start_kib = 0 if index == 0 else size_kib * index
            if not self.setup_bucket(index, start_kib, size_kib):
                raise ProtocolError("device refused bucket setup for GIF upload; enable 'Stream from app' instead")
            msg = self.command(bytes([0x36, 0x01, index]))
            if msg[14] != 0x01:
                raise ProtocolError("device refused GIF transfer start")
            header = BULK_MAGIC + bytes([DATA_GIF, 0, 0, 0]) + struct.pack("<I", len(gif_data))
            self.bulk_write(header)
            for offset in range(0, len(gif_data), chunk_size):
                self.bulk_write(gif_data[offset:offset + chunk_size], timeout_ms=15000)
            self.command(bytes([0x36, 0x02]))
            if not self.set_lcd_mode(LCD_MODE_BUCKET, index):
                raise ProtocolError("device refused to switch to the GIF bucket")

    def keepalive_bucket(self, index: int = 0) -> None:
        """Re-select the bucket; uploaded content reverts to the liquid screen after ~30 s otherwise."""
        try:
            self.set_lcd_mode(LCD_MODE_BUCKET, index)
        except ProtocolError as exc:
            LOGGER.debug("bucket keepalive failed: %s", exc)
