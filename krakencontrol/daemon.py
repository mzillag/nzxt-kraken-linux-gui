"""The background service: owns the device, sensors, cooling and LCD controllers."""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import threading
import time
from typing import Optional

from . import __version__, protocol
from .config import Config
from .cooling import CoolingController
from .lcd.controller import LcdController
from .sensors import SensorHub, find_hwmon_by_name, KRAKEN_HWMON_NAMES

LOGGER = logging.getLogger(__name__)
RECONNECT_INTERVAL = 5.0


class Daemon:
    def __init__(self, config: Config, mock: bool = False) -> None:
        self.config = config
        self.mock = mock
        self.sensors = SensorHub()
        self.device = None
        self.device_error: Optional[str] = None
        self.device_error_kind: Optional[str] = None  # permission | missing | protocol | bootloader
        self.kernel_driver_bound = find_hwmon_by_name(KRAKEN_HWMON_NAMES) is not None
        self.cooling: Optional[CoolingController] = None
        self.lcd = LcdController(self)
        self._stop = threading.Event()
        self._listeners: list = []
        self._listeners_lock = threading.Lock()
        self._last_connect_attempt = 0.0
        self._lock = threading.RLock()
        self.started_at = time.time()
        self.cooling = CoolingController(None, self.config)

    # ------------------------------------------------------------------ device lifecycle
    def connect(self) -> bool:
        with self._lock:
            if self.device is not None and self.device.is_open:
                return True
            self._last_connect_attempt = time.monotonic()
            try:
                if self.mock:
                    from .mock import MockKrakenDevice
                    device = MockKrakenDevice()
                else:
                    if protocol.bootloader_present():
                        self.device_error = ("The Kraken is in bootloader mode (USB 1e71:3011). Shut the PC down, "
                                             "cut power for 30 seconds and start again.")
                        self.device_error_kind = "bootloader"
                        return False
                    found = protocol.discover()
                    if not found:
                        self.device_error = "No supported Kraken found on USB."
                        self.device_error_kind = "missing"
                        return False
                    device = protocol.KrakenDevice(found[0])
                device.open()
                device.initialize(send_init=not self.kernel_driver_bound)
            except protocol.DevicePermissionError as exc:
                self.device_error = str(exc)
                self.device_error_kind = "permission"
                LOGGER.error("%s", exc)
                return False
            except protocol.ProtocolError as exc:
                self.device_error = str(exc)
                self.device_error_kind = "protocol"
                LOGGER.error("device initialisation failed: %s", exc)
                return False
            self.device = device
            self.device_error = None
            self.device_error_kind = None
            LOGGER.info("connected to %s (firmware %s, serial %s)", device.info.name,
                        ".".join(map(str, device.firmware or ())), device.info.serial)
            self._adopt_device_lcd_settings(device)
            self.cooling.device = device
            self.cooling.apply(force=True)
            self.lcd.device_reset()
            return True

    def _adopt_device_lcd_settings(self, device) -> None:
        """On first run take brightness/orientation from the device like CAM shows them."""
        general = self.config.section("general")
        if general.get("adopted_device_settings") or device.lcd is None:
            return
        self.config.update({
            "lcd": {"brightness": int(device.lcd.brightness), "orientation": int(device.lcd.orientation) * 90},
            "general": {"adopted_device_settings": True},
        })

    def device_lost(self) -> None:
        with self._lock:
            if self.device is not None:
                try:
                    self.device.close()
                except Exception:  # noqa: BLE001
                    pass
            self.device = None
            self.cooling.device = None
            self.device_error = "Kraken disconnected."
            self.device_error_kind = "missing"

    def reconnect(self) -> bool:
        self.device_lost()
        return self.connect()

    # ------------------------------------------------------------------ config changes
    def update_config(self, patch: dict) -> dict:
        data = self.config.update(patch)
        if "cooling" in patch and self.device is not None:
            self.cooling.apply()
        if "lcd" in patch or "general" in patch:
            self.lcd.notify()
        self.broadcast()
        return data

    def replace_config_section(self, name: str, value: dict) -> dict:
        data = self.config.replace_section(name, value)
        if name == "cooling" and self.device is not None:
            self.cooling.apply()
        self.lcd.notify()
        self.broadcast()
        return data

    # ------------------------------------------------------------------ state for the UI
    def state(self) -> dict:
        snap = self.sensors.snapshot()
        device = self.device
        info = None
        if device is not None:
            info = {
                "name": device.info.name,
                "serial": device.info.serial,
                "firmware": ".".join(map(str, device.firmware)) if device.firmware else None,
                "product_id": f"{device.info.product_id:04x}",
                "lcd": {
                    "width": device.lcd.width if device.lcd else 640,
                    "height": device.lcd.height if device.lcd else 640,
                    "brightness": device.lcd.brightness if device.lcd else None,
                    "orientation": device.lcd.orientation * 90 if device.lcd else None,
                },
                "mock": self.mock,
            }
        return {
            "version": __version__,
            "time": time.time(),
            "device": {
                "connected": device is not None and device.is_open,
                "info": info,
                "error": self.device_error,
                "error_kind": self.device_error_kind,
                "kernel_driver": self.kernel_driver_bound,
                "udev_hint": "sudo sh packaging/install-udev.sh",
            },
            "sensors": snap.to_dict(),
            "cooling": self.cooling.state(),
            "lcd": self.lcd.state(),
            "config": self.config.get(),
        }

    # ------------------------------------------------------------------ SSE listeners
    def add_listener(self, queue) -> None:
        with self._listeners_lock:
            self._listeners.append(queue)

    def remove_listener(self, queue) -> None:
        with self._listeners_lock:
            if queue in self._listeners:
                self._listeners.remove(queue)

    def broadcast(self) -> None:
        state = self.state()
        with self._listeners_lock:
            for queue in list(self._listeners):
                try:
                    queue.put_nowait(state)
                except Exception:  # noqa: BLE001 - full queue, drop the update
                    pass

    # ------------------------------------------------------------------ main loop
    def run(self) -> None:
        self.lcd.start()
        self.connect()
        interval = float(self.config.section("general").get("poll_interval", 1.0))
        while not self._stop.is_set():
            started = time.monotonic()
            snap = self.sensors.poll()
            if self.device is not None:
                self.cooling.tick(snap)
            elif time.monotonic() - self._last_connect_attempt > RECONNECT_INTERVAL:
                self.connect()
            self.broadcast()
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.1, interval - elapsed))
        self.lcd.stop()
        if self.device is not None:
            try:
                # Leave the firmware screen on so the LCD keeps showing something useful.
                if self.config.section("lcd").get("mode") != "liquid":
                    self.device.show_liquid_screen()
            except Exception:  # noqa: BLE001
                pass
            self.device.close()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------- system info helpers
def system_specs(daemon: Daemon) -> dict:
    def read(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return None

    os_name = None
    for line in (read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    cpu_count = os.cpu_count()
    snap = daemon.sensors.snapshot()
    device = daemon.device
    return {
        "os": os_name or platform.system(),
        "kernel": platform.release(),
        "hostname": platform.node(),
        "cpu": {"name": daemon.sensors.cpu.name, "threads": cpu_count},
        "gpu": {"name": snap.gpu.name if snap.gpu.available else None, "memory_total": snap.gpu.memory_total},
        "memory_total": snap.memory.total,
        "board": {
            "vendor": read("/sys/devices/virtual/dmi/id/board_vendor"),
            "name": read("/sys/devices/virtual/dmi/id/board_name"),
        },
        "kraken": {
            "name": device.info.name if device else None,
            "firmware": ".".join(map(str, device.firmware)) if device and device.firmware else None,
            "serial": device.info.serial if device else None,
            "kernel_driver": daemon.kernel_driver_bound,
        },
    }


def top_processes(limit: int = 6) -> list[dict]:
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,comm,%cpu,%mem,rss", "--sort=-%cpu", "--no-headers"],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines()[:limit]:
        parts = re.split(r"\s+", line.strip(), maxsplit=4)
        if len(parts) < 5:
            continue
        pid, comm, cpu, mem, rss = parts
        try:
            rows.append({"pid": int(pid), "name": comm, "cpu": float(cpu), "mem": float(mem),
                         "rss_mb": round(int(rss) / 1024)})
        except ValueError:
            continue
    return rows
