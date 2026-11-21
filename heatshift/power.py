"""Measure whole-system power on a MacBook without sudo.

Apple's battery controller exposes PowerTelemetryData via `ioreg`:
  SystemLoad                 [mW]  whole-system draw (= SystemPowerIn - BatteryPower)
  AccumulatedSystemLoad      [mW*samples] running sum, SystemLoadAccumulatorCount = #samples
The instantaneous value refreshes slowly, so energy windows use the accumulators:
  mean power over a window = d(AccumulatedSystemLoad) / d(SystemLoadAccumulatorCount).
On other machines we fall back to Linux RAPL (/sys/class/powercap) or report None.
"""
from __future__ import annotations

import plistlib
import subprocess
import threading
import time
from pathlib import Path


def _s64(x: int) -> int:
    return x - (1 << 64) if x >= (1 << 63) else x


def _telemetry() -> dict | None:
    try:
        raw = subprocess.run(["ioreg", "-rn", "AppleSmartBattery", "-a"], capture_output=True, timeout=3).stdout
        if raw:
            return plistlib.loads(raw)[0].get("PowerTelemetryData")
    except Exception:
        return None
    return None


def snapshot() -> tuple[int, int] | None:
    """(AccumulatedSystemLoad, SystemLoadAccumulatorCount) - diff two snapshots for a mean."""
    pt = _telemetry()
    if pt and "AccumulatedSystemLoad" in pt:
        return pt["AccumulatedSystemLoad"], pt["SystemLoadAccumulatorCount"]
    return None


def mean_watts_between(a, b) -> float | None:
    if not a or not b or b[1] <= a[1]:
        return None
    return (b[0] - a[0]) / (b[1] - a[1]) / 1000.0


def read_watts() -> float | None:
    pt = _telemetry()
    if pt and "SystemLoad" in pt:
        return pt["SystemLoad"] / 1000.0
    rapl = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
    if rapl.exists():
        e0, t0 = int(rapl.read_text()), time.time()
        time.sleep(0.5)
        return (int(rapl.read_text()) - e0) / 1e6 / (time.time() - t0)
    return None


class PowerSampler:
    """Background 1 Hz sampler; .mean() over the sampling window."""

    def __init__(self, period: float = 1.0):
        self.period, self.samples, self._stop = period, [], threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            w = read_watts()
            if w is not None:
                self.samples.append((time.time(), w))
            self._stop.wait(self.period)

    def __enter__(self):
        self._th.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._th.join(timeout=3)

    def mean(self) -> float | None:
        return sum(w for _, w in self.samples) / len(self.samples) if self.samples else None
