"""A fake Kraken for developing and testing without device permissions.

It implements the subset of `protocol.KrakenDevice` the daemon uses and logs
every command.  Enable with `--mock` or `KRAKEN_MOCK=1`.
"""

from __future__ import annotations

import logging
import time

from . import protocol

LOGGER = logging.getLogger(__name__)


class MockKrakenDevice:
    def __init__(self) -> None:
        self.info = protocol.DeviceInfo(
            product_id=0x300C, name="NZXT Kraken Elite (mock)", hidraw_path="/dev/null",
            usb_bus=0, usb_address=0, serial="MOCK0000", lcd_resolution=(640, 640),
        )
        self.firmware = (2, 1, 0)
        self.lcd = protocol.LcdInfo(brightness=80, orientation=0, width=640, height=640)
        self.frames_sent = 0
        self.last_frame_bytes = 0
        self.curves: dict[str, list[int]] = {}
        self.lcd_mode = protocol.LCD_MODE_LIQUID
        self._open = False

    # lifecycle
    def open(self) -> None:
        self._open = True

    def open_bulk(self) -> None:
        pass

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def initialize(self, send_init: bool = False) -> None:
        LOGGER.info("mock initialize (send_init=%s)", send_init)

    # queries
    def get_firmware_version(self):
        return self.firmware

    def get_lcd_info(self):
        return self.lcd

    def get_status(self):
        return protocol.Status(liquid_temperature=31.5, pump_rpm=2200, pump_duty=60, fan_rpm=900, fan_duty=35)

    # LCD
    def set_brightness(self, percent: int) -> None:
        self.lcd.brightness = max(0, min(100, int(percent)))
        LOGGER.info("mock brightness=%d", self.lcd.brightness)

    def set_orientation(self, degrees: int) -> None:
        self.lcd.orientation = (int(degrees) // 90) % 4
        LOGGER.info("mock orientation=%d", self.lcd.orientation * 90)

    def set_lcd_mode(self, mode: int, bucket: int = 0) -> bool:
        self.lcd_mode = mode
        LOGGER.info("mock lcd mode=0x%02x bucket=%d", mode, bucket)
        return True

    def show_liquid_screen(self) -> bool:
        return self.set_lcd_mode(protocol.LCD_MODE_LIQUID, 0)

    def prepare_streaming(self) -> None:
        LOGGER.info("mock prepare streaming")

    def send_frame(self, payload: bytes, data_type: int = protocol.DATA_Q565, chunk_size: int = 0,
                   ack_timeout: float = 0.5) -> None:
        self.frames_sent += 1
        self.last_frame_bytes = len(payload)
        # emulate USB transfer time (~35 MB/s) plus panel latency
        time.sleep(min(0.08, len(payload) / 35e6 + 0.01))

    def upload_gif_to_bucket(self, gif_data: bytes, index: int = 0, chunk_size: int = 1024 * 1024) -> None:
        LOGGER.info("mock gif upload %d bytes into bucket %d", len(gif_data), index)
        self.lcd_mode = protocol.LCD_MODE_BUCKET

    def keepalive_bucket(self, index: int = 0) -> None:
        pass

    # speed
    @staticmethod
    def build_curve(points, duty_min):
        return protocol.KrakenDevice.build_curve(points, duty_min)

    def set_speed_curve(self, channel: str, duties: list[int]) -> None:
        self.curves[channel] = list(duties)
        LOGGER.info("mock %s curve: %s", channel, duties)

    def set_fixed_speed(self, channel: str, duty: int) -> None:
        self.curves[channel] = [int(duty)] * protocol.CURVE_POINTS
        LOGGER.info("mock %s fixed %d%%", channel, duty)
