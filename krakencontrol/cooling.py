"""Fan and pump control.

Two strategies, chosen by the configured control sensor:

* `liquid_temp`: the curve is uploaded once to the firmware, which evaluates it
  on its own using the coolant sensor (this is what CAM does by default, and
  the cooler keeps working when the daemon is not running).
* `cpu_temp` / `gpu_temp`: the firmware cannot see those sensors, so the daemon
  evaluates the curve every tick and sends a fixed duty whenever the target
  changes.  CAM's cooling manager does the same through its service with
  `hysteresis_time_seconds: 5.0` (seen in cam.log): a higher target is applied
  at once, a lower one only after it has stayed lower for that long, which keeps
  the fans from hunting on short CPU temperature spikes.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

from . import protocol
from .config import FAN_PRESETS, PUMP_PRESETS

LOGGER = logging.getLogger(__name__)

CHANNELS = ("fan", "pump")
DUTY_MIN = {"fan": protocol.FAN_DUTY_MIN, "pump": protocol.PUMP_DUTY_MIN}


def resolve_points(channel: str, settings: dict) -> list[tuple[float, float]]:
    """Return the (temperature, duty) points implied by a channel's settings."""
    mode = settings.get("mode", "silent")
    presets = FAN_PRESETS if channel == "fan" else PUMP_PRESETS
    if mode == "fixed":
        duty = float(settings.get("fixed", 50))
        return [(20.0, duty), (58.0, duty)]
    if mode == "custom":
        points = settings.get("custom") or presets["silent"]
    else:
        points = presets.get(mode, presets["silent"])
    cleaned = []
    for point in points:
        try:
            temp, duty = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        cleaned.append((max(0.0, min(100.0, temp)), max(0.0, min(100.0, duty))))
    cleaned.sort()
    return cleaned or [(20.0, 50.0), (58.0, 50.0)]


def channel_sensor(cooling: dict, channel: str) -> str:
    """Temperature source for a channel: liquid_temp (firmware) or cpu_temp / gpu_temp (software)."""
    return cooling.get(channel, {}).get("sensor") or cooling.get("sensor", "liquid_temp")


def evaluate_points(points: list[tuple[float, float]], temperature: float) -> float:
    if temperature <= points[0][0]:
        return points[0][1]
    if temperature >= points[-1][0]:
        return points[-1][1]
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if t0 <= temperature <= t1:
            return d0 if t1 == t0 else d0 + (d1 - d0) * (temperature - t0) / (t1 - t0)
    return points[-1][1]


class CoolingController:
    def __init__(self, device, config) -> None:
        self.device = device
        self.config = config
        self._applied_signature: Optional[str] = None
        self._software_duty: dict[str, Optional[int]] = {"fan": None, "pump": None}
        self._samples: dict[str, deque] = {}
        # Time at which the target first dropped below the applied duty (per channel)
        self._lower_since: dict[str, Optional[float]] = {"fan": None, "pump": None}
        self._last_error: Optional[str] = None
        self.last_applied: Optional[float] = None

    # ------------------------------------------------------------------ public
    def apply(self, force: bool = False) -> None:
        """Push the configured curves to the device (idempotent)."""
        cooling = self.config.section("cooling")
        signature = repr(cooling)
        if not force and signature == self._applied_signature:
            return
        try:
            for channel in CHANNELS:
                if channel_sensor(cooling, channel) == "liquid_temp":
                    points = resolve_points(channel, cooling[channel])
                    duties = protocol.KrakenDevice.build_curve(points, DUTY_MIN[channel])
                    self.device.set_speed_curve(channel, duties)
                    LOGGER.info("%s curve uploaded (%s)", channel, cooling[channel].get("mode"))
                # Software-driven channels are re-evaluated on the next tick
                self._software_duty[channel] = None
            self._applied_signature = signature
            self.last_applied = time.time()
            self._last_error = None
        except protocol.ProtocolError as exc:
            self._last_error = str(exc)
            LOGGER.error("applying cooling settings failed: %s", exc)

    def tick(self, snapshot) -> None:
        """Called every poll interval with the latest sensor snapshot."""
        cooling = self.config.section("cooling")
        if self.device is None:
            return
        window = max(1, int(cooling.get("smoothing", 1)))
        hysteresis = max(0.0, float(cooling.get("hysteresis", 5.0)))
        now = time.time()
        for channel in CHANNELS:
            sensor = channel_sensor(cooling, channel)
            if sensor == "liquid_temp":
                self._lower_since[channel] = None
                continue
            value = snapshot.value_for(sensor)
            if value is None:
                continue
            samples = self._samples.setdefault(sensor, deque(maxlen=window))
            if samples.maxlen != window:
                samples = deque(samples, maxlen=window)
                self._samples[sensor] = samples
            samples.append(float(value))
            smoothed = sum(samples) / len(samples)
            points = resolve_points(channel, cooling[channel])
            duty = int(round(evaluate_points(points, smoothed)))
            duty = max(DUTY_MIN[channel], min(protocol.DUTY_MAX, duty))
            current = self._software_duty[channel]
            if current is not None and duty < current:
                # Ramp down only after the target has stayed lower for the hysteresis time
                if self._lower_since[channel] is None:
                    self._lower_since[channel] = now
                if now - self._lower_since[channel] < hysteresis:
                    continue
            else:
                self._lower_since[channel] = None
            if current != duty:
                try:
                    self.device.set_fixed_speed(channel, duty)
                    self._software_duty[channel] = duty
                    self._lower_since[channel] = None
                    self._last_error = None
                except protocol.ProtocolError as exc:
                    self._last_error = str(exc)
                    LOGGER.error("setting %s duty failed: %s", channel, exc)

    def state(self) -> dict:
        cooling = self.config.section("cooling")
        return {
            "fan": {
                "mode": cooling["fan"].get("mode"),
                "sensor": channel_sensor(cooling, "fan"),
                "points": resolve_points("fan", cooling["fan"]),
                "software_duty": self._software_duty["fan"],
            },
            "pump": {
                "mode": cooling["pump"].get("mode"),
                "sensor": channel_sensor(cooling, "pump"),
                "points": resolve_points("pump", cooling["pump"]),
                "software_duty": self._software_duty["pump"],
            },
            "error": self._last_error,
            "last_applied": self.last_applied,
        }
