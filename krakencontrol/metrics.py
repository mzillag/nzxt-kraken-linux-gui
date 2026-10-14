"""Metric definitions shared by the LCD renderer, the cooling controller and the UI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    id: str
    name: str          # full name as shown in dropdowns (CAM wording)
    short: str         # label drawn on the LCD
    kind: str          # temperature | percent | frequency | rpm
    gauge_max: float   # value that fills the gauge completely


METRICS: dict[str, Metric] = {
    "cpu_temp": Metric("cpu_temp", "CPU Temperature", "CPU", "temperature", 100),
    "cpu_load": Metric("cpu_load", "CPU Load", "CPU Load", "percent", 100),
    "cpu_freq": Metric("cpu_freq", "CPU Clock Speed", "CPU Clock", "frequency", 6000),
    "gpu_temp": Metric("gpu_temp", "GPU Temperature", "GPU", "temperature", 100),
    "gpu_load": Metric("gpu_load", "GPU Load", "GPU Load", "percent", 100),
    "gpu_freq": Metric("gpu_freq", "GPU Clock Speed", "GPU Clock", "frequency", 3000),
    "liquid_temp": Metric("liquid_temp", "Liquid Temperature", "Liquid", "temperature", 100),
    "ram_load": Metric("ram_load", "RAM Usage", "RAM", "percent", 100),
    "pump_rpm": Metric("pump_rpm", "Pump Speed", "Pump", "rpm", 3200),
    "fan_rpm": Metric("fan_rpm", "Fan Speed", "Fan", "rpm", 2500),
}

# Order used by the UI dropdowns (matches CAM: CPU, GPU, Liquid readings first)
METRIC_ORDER = [
    "cpu_temp", "cpu_load", "cpu_freq", "gpu_temp", "gpu_load", "gpu_freq", "liquid_temp",
    "ram_load", "pump_rpm", "fan_rpm",
]


def celsius_to_display(value: float, unit: str) -> float:
    return value * 9 / 5 + 32 if unit == "F" else value


def format_value(metric: Metric, value: float | None, unit: str = "C") -> tuple[str, str]:
    """Return (main text, suffix) for the LCD, e.g. ("32", "°") or ("5.2", "GHz")."""
    if value is None:
        return ("--", "")
    if metric.kind == "temperature":
        return (f"{round(celsius_to_display(value, unit))}", "°")
    if metric.kind == "percent":
        return (f"{round(value)}", "%")
    if metric.kind == "frequency":
        return (f"{value / 1000:.1f}", "GHz")
    if metric.kind == "rpm":
        return (f"{round(value)}", "RPM")
    return (f"{value:.0f}", "")


def gauge_fraction(metric: Metric, value: float | None) -> float:
    if value is None or metric.gauge_max <= 0:
        return 0.0
    return max(0.0, min(1.0, value / metric.gauge_max))


def as_dict() -> list[dict]:
    return [
        {"id": m.id, "name": m.name, "short": m.short, "kind": m.kind}
        for m in (METRICS[i] for i in METRIC_ORDER)
    ]
