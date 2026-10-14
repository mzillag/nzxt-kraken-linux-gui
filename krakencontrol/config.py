"""Persistent settings.

Stored as JSON in `$XDG_CONFIG_HOME/kraken-control/config.json`.  The schema
mirrors the sections of the UI (cooling, LCD, general) and deliberately keeps
only user intent: preset names, custom curve points, chosen LCD mode.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from typing import Any

LOGGER = logging.getLogger(__name__)

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "kraken-control")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
ASSETS_DIR = os.path.join(CONFIG_DIR, "assets")  # uploaded images and GIFs

# ---------------------------------------------------------------------------
# Cooling presets.  Points are (temperature °C, duty %).  The firmware stores
# the curve for liquid 20..59 °C; when CPU or GPU is the control sensor the
# daemon evaluates the same points in software.
# ---------------------------------------------------------------------------
# Taken from NZXT CAM 4.76 itself: the preset tables in its renderer bundle are
# [[0,30],[35,30],[40,35],...] for Silent fans, [[0,50],[35,50],[60,100]] for
# Performance fans, [[0,60],[35,60],[55,100]] for Silent pumps and
# [[0,70],[35,70],[40,80],[60,100]] for Performance pumps; CAM linearly
# interpolates them at 5 °C steps, which is what is written out below.  The Silent
# curves also match the CoolingConfig lines CAM logs when it applies them.
FAN_PRESETS: dict[str, list[list[float]]] = {
    "silent": [[20, 30], [25, 30], [30, 30], [35, 30], [40, 35], [45, 42], [50, 50], [55, 57], [60, 63],
               [65, 69], [70, 74], [75, 78], [80, 84], [85, 90], [90, 100], [95, 100], [100, 100]],
    "performance": [[20, 50], [25, 50], [30, 50], [35, 50], [40, 60], [45, 70], [50, 80], [55, 90], [60, 100],
                    [65, 100], [70, 100], [75, 100], [80, 100], [85, 100], [90, 100], [95, 100], [100, 100]],
}
PUMP_PRESETS: dict[str, list[list[float]]] = {
    "silent": [[20, 60], [25, 60], [30, 60], [35, 60], [40, 70], [45, 80], [50, 90], [55, 100], [60, 100],
               [65, 100], [70, 100], [75, 100], [80, 100], [85, 100], [90, 100], [95, 100], [100, 100]],
    "performance": [[20, 70], [25, 70], [30, 70], [35, 70], [40, 80], [45, 85], [50, 90], [55, 95], [60, 100],
                    [65, 100], [70, 100], [75, 100], [80, 100], [85, 100], [90, 100], [95, 100], [100, 100]],
}
FIXED_DEFAULT = {"fan": 40, "pump": 100}  # CAM's fixedFanPercentage defaults for the Kraken Elite
CURVE_TEMPS = list(range(20, 101, 5))  # the 17 editable nodes CAM shows

# Default settings of every LCD screen type; the keys match the mode ids used by the UI.
DEFAULT_SCREENS: dict[str, dict] = {
    "single": {"reading": "liquid_temp", "color1": "#6B00DE", "color2": "#D600BF", "text_color": "#FFFFFF",
               "background": "#000000", "logo": True, "logo_color": "#FFFFFF"},
    "dual": {"readings": ["cpu_temp", "gpu_temp"], "number_colors": ["#FFFFFF", "#FFFFFF"],
             "text_colors": ["#FFFFFF", "#FFFFFF"], "viz_colors": ["#6B00DE", "#D600BF", "#818181"],
             "background": "#000000", "logo": True, "logo_color": "#FFFFFF"},
    "triple": {"primary": "gpu_temp", "secondary": ["cpu_temp", "liquid_temp"], "viz": ["#2AB8FF", "#C81DFF"],
               "number_color": "#FFFFFF", "text_color": "#FFFFFF", "secondary_number_colors": ["#FFFFFF", "#FFFFFF"],
               "secondary_text_colors": ["#FFFFFF", "#FFFFFF"], "background": "#000000", "logo": True,
               "logo_color": "#FFFFFF"},
    "clock": {"face": "analog", "dial_color": "#FFFFFF", "seconds_color": "#8000FF", "hour24": False,
              "background": "#000000"},
    "image": {"path": ""},
    # CAM renders the readings of the "+ GIF" modes in white by default
    "single_gif": {"path": "", "reading": "liquid_temp", "number_color": "#FFFFFF", "text_color": "#FFFFFF",
                   "background": "#000000"},
    "dual_gif": {"path": "", "readings": ["cpu_temp", "gpu_temp"], "number_colors": ["#FFFFFF", "#FFFFFF"],
                 "text_colors": ["#FFFFFF", "#FFFFFF"], "background": "#000000"},
    "triple_gif": {"path": "", "primary": "gpu_temp", "secondary": ["cpu_temp", "liquid_temp"],
                   "number_color": "#FFFFFF", "text_color": "#FFFFFF", "background": "#000000"},
}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "cooling": {
        "fan": {
            "mode": "silent",              # silent | performance | fixed | custom
            "sensor": "liquid_temp",       # liquid_temp (firmware) | cpu_temp | gpu_temp (software)
            "fixed": FIXED_DEFAULT["fan"],
            "custom": copy.deepcopy(FAN_PRESETS["performance"]),  # CAM seeds Custom with Performance
        },
        "pump": {
            "mode": "silent",
            "sensor": "liquid_temp",
            "fixed": FIXED_DEFAULT["pump"],
            "custom": copy.deepcopy(PUMP_PRESETS["performance"]),
        },
        "smoothing": 1,   # samples averaged for software-driven curves (1 = raw reading, like CAM)
        "hysteresis": 5,  # seconds a lower target must persist before ramping down (CAM: 5.0)
    },
    "lcd": {
        # liquid | image | single | single_gif | dual | dual_gif | triple | triple_gif | clock | carousel
        "mode": "single",
        "brightness": 80,
        "orientation": 0,        # degrees, 30° steps like CAM's rotation dial
        "stream_gif": True,      # "Stream from app" for Image/GIF; otherwise the firmware plays the GIF
        "fps": 10,
        "transport": "auto",     # auto | q565 | bgr888
        "screens": copy.deepcopy(DEFAULT_SCREENS),
        "carousel": {"interval": 5, "items": []},
    },
    "general": {
        "poll_interval": 1.0,
        "api_port": 30100,
        "temperature_unit": "C",
        "dark_mode": True,
        "adopted_device_settings": False,
        "config_version": 2,  # bumped when stored values need migrating (see Config._migrate)
        "active_profile": "Default",
    },
    "profiles": {},  # name -> {"cooling": {...}, "lcd": {...}}
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Thread-safe settings holder with load/save."""

    def __init__(self, path: str = CONFIG_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                if isinstance(stored, dict):
                    self._data = _merge(DEFAULT_CONFIG, stored)
                    self._migrate(stored)
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                LOGGER.warning("could not read %s: %s (using defaults)", self.path, exc)

    def _migrate(self, stored: dict) -> None:
        """Bring settings written by older versions up to date (CAM keeps a similar list)."""
        version = int((stored.get("general") or {}).get("config_version", 1))
        if version < 2:
            # Version 1 had no panel base rotation, so users rotated the display by 270
            # degrees to get CAM's "0".  Fold that offset into the stored value.
            lcd = self._data.setdefault("lcd", {})
            lcd["orientation"] = (int(lcd.get("orientation", 0)) - 270) % 360
            LOGGER.info("config migrated to version 2 (orientation now %s)", lcd["orientation"])
        self._data.setdefault("general", {})["config_version"] = DEFAULT_CONFIG["general"]["config_version"]

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self.path)

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def section(self, name: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data[name])

    def update(self, patch: dict, save: bool = True) -> dict:
        with self._lock:
            self._data = _merge(self._data, patch)
            if save:
                self.save()
            return copy.deepcopy(self._data)

    def replace_section(self, name: str, value: dict, save: bool = True) -> dict:
        with self._lock:
            self._data[name] = copy.deepcopy(value)
            if save:
                self.save()
            return copy.deepcopy(self._data)

    def reset(self, section: str | None = None) -> dict:
        with self._lock:
            if section:
                self._data[section] = copy.deepcopy(DEFAULT_CONFIG[section])
            else:
                self._data = copy.deepcopy(DEFAULT_CONFIG)
            self.save()
            return copy.deepcopy(self._data)
