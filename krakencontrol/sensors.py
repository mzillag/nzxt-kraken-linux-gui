"""System sensor readers: CPU, GPU, memory and the cooler's own hwmon sensors.

Everything in here is read-only and works without special privileges.
Values are refreshed by `SensorHub.poll()`, which the daemon calls once per
tick; consumers read the cached `SensorHub.snapshot()`.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

HWMON_ROOT = "/sys/class/hwmon"
KRAKEN_HWMON_NAMES = ("kraken2023elite", "kraken2023", "krakenz3", "krakenx3", "kraken3")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def find_hwmon_by_name(names: tuple[str, ...]) -> Optional[str]:
    """Return the sysfs directory of the first hwmon device whose name matches."""
    for path in sorted(glob.glob(os.path.join(HWMON_ROOT, "hwmon*"))):
        if _read_text(os.path.join(path, "name")) in names:
            return path
    return None


@dataclass
class CpuStats:
    temperature: Optional[float] = None  # °C, package temperature
    load: Optional[float] = None  # %, all cores
    frequency: Optional[float] = None  # MHz, average of online cores
    name: str = "CPU"


@dataclass
class GpuStats:
    temperature: Optional[float] = None  # °C
    load: Optional[float] = None  # %
    frequency: Optional[float] = None  # MHz
    memory_used: Optional[float] = None  # MiB
    memory_total: Optional[float] = None  # MiB
    power: Optional[float] = None  # W
    name: str = "GPU"
    available: bool = False


@dataclass
class MemoryStats:
    used: Optional[float] = None  # GiB
    total: Optional[float] = None  # GiB
    load: Optional[float] = None  # %


@dataclass
class CoolerStats:
    liquid_temperature: Optional[float] = None  # °C
    pump_rpm: Optional[int] = None
    pump_duty: Optional[int] = None  # %
    fan_rpm: Optional[int] = None
    fan_duty: Optional[int] = None  # %
    available: bool = False


@dataclass
class Snapshot:
    timestamp: float = field(default_factory=time.time)
    cpu: CpuStats = field(default_factory=CpuStats)
    gpu: GpuStats = field(default_factory=GpuStats)
    memory: MemoryStats = field(default_factory=MemoryStats)
    cooler: CoolerStats = field(default_factory=CoolerStats)

    def to_dict(self) -> dict:
        return asdict(self)

    def value_for(self, source: str) -> Optional[float]:
        """Resolve a metric id (as used by the UI/config) to its current value."""
        return {
            "liquid_temp": self.cooler.liquid_temperature,
            "cpu_temp": self.cpu.temperature,
            "cpu_load": self.cpu.load,
            "cpu_freq": self.cpu.frequency,
            "gpu_temp": self.gpu.temperature,
            "gpu_load": self.gpu.load,
            "gpu_freq": self.gpu.frequency,
            "ram_load": self.memory.load,
            "ram_used": self.memory.used,
            "pump_rpm": self.cooler.pump_rpm,
            "pump_duty": self.cooler.pump_duty,
            "fan_rpm": self.cooler.fan_rpm,
            "fan_duty": self.cooler.fan_duty,
        }.get(source)


class CpuReader:
    """Reads package temperature from coretemp/k10temp/zenpower and load from /proc/stat."""

    def __init__(self) -> None:
        self._temp_path: Optional[str] = None
        self._prev_total = 0
        self._prev_idle = 0
        self.name = self._model_name()
        self._locate_temp_sensor()

    @staticmethod
    def _model_name() -> str:
        text = _read_text("/proc/cpuinfo") or ""
        for line in text.splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
        return "CPU"

    def _locate_temp_sensor(self) -> None:
        # Intel: coretemp "Package id 0"; AMD: k10temp "Tctl"/"Tdie"; zenpower "Tdie".
        preferred_labels = ("Package id 0", "Tctl", "Tdie", "CPU Temperature", "CPU")
        for path in sorted(glob.glob(os.path.join(HWMON_ROOT, "hwmon*"))):
            name = _read_text(os.path.join(path, "name"))
            if name not in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
                continue
            inputs = sorted(glob.glob(os.path.join(path, "temp*_input")))
            labelled = {}
            for inp in inputs:
                label = _read_text(inp.replace("_input", "_label")) or ""
                labelled[label] = inp
            for label in preferred_labels:
                if label in labelled:
                    self._temp_path = labelled[label]
                    return
            if inputs and name != "acpitz":
                self._temp_path = inputs[0]
                return

    def read(self) -> CpuStats:
        stats = CpuStats(name=self.name)
        if self._temp_path:
            raw = _read_int(self._temp_path)
            if raw is not None:
                stats.temperature = raw / 1000.0
        stats.load = self._load()
        stats.frequency = self._frequency()
        return stats

    def _load(self) -> Optional[float]:
        text = _read_text("/proc/stat")
        if not text:
            return None
        first = text.splitlines()[0].split()
        if first[0] != "cpu":
            return None
        values = [int(v) for v in first[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        d_total = total - self._prev_total
        d_idle = idle - self._prev_idle
        self._prev_total, self._prev_idle = total, idle
        if d_total <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))

    @staticmethod
    def _frequency() -> Optional[float]:
        freqs = []
        for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
            value = _read_int(path)
            if value:
                freqs.append(value / 1000.0)
        if freqs:
            return sum(freqs) / len(freqs)
        text = _read_text("/proc/cpuinfo") or ""
        mhz = [float(l.split(":")[1]) for l in text.splitlines() if l.startswith("cpu MHz")]
        return sum(mhz) / len(mhz) if mhz else None


class GpuReader:
    """Reads GPU statistics from nvidia-smi (NVIDIA) or hwmon amdgpu (AMD)."""

    def __init__(self) -> None:
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._amdgpu_path = find_hwmon_by_name(("amdgpu",))
        self.name = "GPU"
        self._last_nvidia_poll = 0.0
        self._cached = GpuStats()
        if self._nvidia_smi:
            self._cached = self._read_nvidia()
            self.name = self._cached.name

    def read(self) -> GpuStats:
        if self._nvidia_smi:
            now = time.time()
            if now - self._last_nvidia_poll >= 1.0:
                self._cached = self._read_nvidia()
                self._last_nvidia_poll = now
            return self._cached
        if self._amdgpu_path:
            return self._read_amdgpu()
        return GpuStats(available=False)

    def _read_nvidia(self) -> GpuStats:
        query = "name,temperature.gpu,utilization.gpu,clocks.gr,memory.used,memory.total,power.draw"
        try:
            out = subprocess.run(
                [self._nvidia_smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return GpuStats(available=False)
        if out.returncode != 0 or not out.stdout.strip():
            return GpuStats(available=False)
        parts = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
        if len(parts) < 7:
            return GpuStats(available=False)

        def num(value: str) -> Optional[float]:
            try:
                return float(value)
            except ValueError:
                return None

        return GpuStats(
            name=parts[0], temperature=num(parts[1]), load=num(parts[2]), frequency=num(parts[3]),
            memory_used=num(parts[4]), memory_total=num(parts[5]), power=num(parts[6]), available=True,
        )

    def _read_amdgpu(self) -> GpuStats:
        base = self._amdgpu_path
        stats = GpuStats(name="AMD GPU", available=True)
        for inp in glob.glob(os.path.join(base, "temp*_input")):
            label = _read_text(inp.replace("_input", "_label"))
            if label in ("edge", None):
                raw = _read_int(inp)
                stats.temperature = raw / 1000.0 if raw is not None else None
                break
        device = os.path.join(base, "device")
        busy = _read_int(os.path.join(device, "gpu_busy_percent"))
        stats.load = float(busy) if busy is not None else None
        used = _read_int(os.path.join(device, "mem_info_vram_used"))
        total = _read_int(os.path.join(device, "mem_info_vram_total"))
        stats.memory_used = used / 2**20 if used else None
        stats.memory_total = total / 2**20 if total else None
        freq = _read_int(os.path.join(base, "freq1_input"))
        stats.frequency = freq / 1e6 if freq else None
        power = _read_int(os.path.join(base, "power1_average"))
        stats.power = power / 1e6 if power else None
        return stats


class MemoryReader:
    @staticmethod
    def read() -> MemoryStats:
        text = _read_text("/proc/meminfo")
        if not text:
            return MemoryStats()
        values = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            values[key] = int(rest.split()[0])
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return MemoryStats()
        used = total - available
        return MemoryStats(used=used / 2**20, total=total / 2**20, load=100.0 * used / total)


class CoolerHwmonReader:
    """Reads the Kraken's own sensors through the nzxt-kraken3 kernel driver (no privileges needed)."""

    def __init__(self) -> None:
        self.path = find_hwmon_by_name(KRAKEN_HWMON_NAMES)

    def refresh_path(self) -> None:
        self.path = find_hwmon_by_name(KRAKEN_HWMON_NAMES)

    def read(self) -> CoolerStats:
        if not self.path or not os.path.isdir(self.path):
            self.refresh_path()
            if not self.path:
                return CoolerStats(available=False)
        temp = _read_int(os.path.join(self.path, "temp1_input"))
        pump_rpm = _read_int(os.path.join(self.path, "fan1_input"))
        pump_pwm = _read_int(os.path.join(self.path, "pwm1"))
        fan_rpm = _read_int(os.path.join(self.path, "fan2_input"))
        fan_pwm = _read_int(os.path.join(self.path, "pwm2"))
        if temp is None:
            return CoolerStats(available=False)
        return CoolerStats(
            liquid_temperature=temp / 1000.0,
            pump_rpm=pump_rpm,
            pump_duty=round(pump_pwm * 100 / 255) if pump_pwm is not None else None,
            fan_rpm=fan_rpm,
            fan_duty=round(fan_pwm * 100 / 255) if fan_pwm is not None else None,
            available=True,
        )


class SensorHub:
    """Aggregates all readers and keeps the latest snapshot plus a short history."""

    HISTORY_SECONDS = 600

    def __init__(self) -> None:
        self.cpu = CpuReader()
        self.gpu = GpuReader()
        self.memory = MemoryReader()
        self.cooler = CoolerHwmonReader()
        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        self._history: list[dict] = []
        self.poll()

    def poll(self) -> Snapshot:
        snap = Snapshot(
            cpu=self.cpu.read(), gpu=self.gpu.read(), memory=self.memory.read(), cooler=self.cooler.read()
        )
        with self._lock:
            self._snapshot = snap
            self._history.append({
                "t": snap.timestamp,
                "liquid_temp": snap.cooler.liquid_temperature,
                "cpu_temp": snap.cpu.temperature,
                "gpu_temp": snap.gpu.temperature,
                "pump_rpm": snap.cooler.pump_rpm,
                "fan_rpm": snap.cooler.fan_rpm,
                "pump_duty": snap.cooler.pump_duty,
                "fan_duty": snap.cooler.fan_duty,
            })
            cutoff = snap.timestamp - self.HISTORY_SECONDS
            while self._history and self._history[0]["t"] < cutoff:
                self._history.pop(0)
        return snap

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)
