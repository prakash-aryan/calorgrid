"""Measure runtime and energy per job item for the real workers on THIS machine.

  1. idle baseline: mean system power over a window with nothing running
  2. run each worker for a fixed number of items; mean system power over the run window
     (Apple battery-controller accumulators -> exact window mean, see heatshift/power.py)
  3. energy per item, incremental = (P_run - P_idle) * runtime / items
                      whole system = P_run * runtime / items
Writes data/derived/measured_jobs.json (read by heatshift/jobs.py and the dashboard).

Usage: python scripts/benchmark_jobs.py   (~15 min total, keep the machine otherwise idle)
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from heatshift.config import DERIVED  # noqa: E402
from heatshift.power import mean_watts_between, snapshot  # noqa: E402

PY = sys.executable


def wait_tick(prev: tuple | None = None, timeout: float = 180) -> tuple[tuple, float]:
    """Block until the battery controller publishes new accumulator values (~every 58 s)."""
    prev = prev or snapshot()
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = snapshot()
        if s and s[1] != prev[1]:
            return s, time.time()
        time.sleep(0.25)
    raise TimeoutError("no power telemetry update")


def idle_mean(ticks: int) -> float:
    a, _ = wait_tick()
    b = a
    for _ in range(ticks):
        b, _ = wait_tick(b)
    return mean_watts_between(a, b)


def run_worker(script: str, state: Path, pattern: str = "IJIJI") -> dict:
    """A/B/A protocol robust to drift: the job process is alternately frozen (SIGSTOP = our
    pause mechanism -> 'I' windows) and running (SIGCONT -> 'J' windows). Every window spans
    exactly one full telemetry interval and lies entirely inside one state."""
    import signal
    import threading
    shutil.rmtree(state, ignore_errors=True)
    env = {**os.environ, "HS_REPEAT": "50"}
    t0 = time.time()
    p = subprocess.Popen([PY, str(ROOT / "workers" / script), "--state", str(state)],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    progress: list[tuple[float, int]] = []
    t_run = None

    def reader():
        nonlocal t_run
        for line in p.stdout:
            ev = json.loads(line)
            if ev["event"] == "running":
                t_run = time.time()
            if ev["event"] == "progress":
                progress.append((time.time(), ev["at"]))
    threading.Thread(target=reader, daemon=True).start()
    while t_run is None or len(progress) < 3:
        time.sleep(0.2)

    def items_at(t):
        b = [n for (tt, n) in progress if tt <= t]
        return b[-1] if b else 0

    windows = []
    for st in pattern:
        os.kill(p.pid, signal.SIGCONT if st == "J" else signal.SIGSTOP)
        a, ta = wait_tick()           # first boundary after the state change
        b, tb = wait_tick(a)          # full interval inside the state
        windows.append({"state": st, "w": mean_watts_between(a, b), "s": tb - ta,
                        "items": items_at(tb) - items_at(ta)})
        print(f"    window {st}: {windows[-1]['w']:.1f} W, {windows[-1]['items']} items in {tb-ta:.0f} s", flush=True)
    os.kill(p.pid, signal.SIGCONT)
    p.terminate()
    p.wait()
    # incremental power of each J window vs the mean of its two neighbouring I windows
    incs, rates = [], []
    for i, w in enumerate(windows):
        if w["state"] == "J" and 0 < i < len(windows) - 1:
            incs.append(w["w"] - (windows[i - 1]["w"] + windows[i + 1]["w"]) / 2)
            rates.append(w["items"] / w["s"])
    jw = [w for w in windows if w["state"] == "J"]
    return {"windows": windows, "model_load_s": t_run - t0,
            "items_per_s": sum(rates) / len(rates),
            "mean_w": sum(w["w"] for w in jw) / len(jw),
            "paused_w": sum(w["w"] for w in windows if w["state"] == "I") / (len(windows) - len(jw)),
            "incremental_w": sum(incs) / len(incs), "incremental_w_runs": incs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="IJIJI", help="I = job paused (SIGSTOP), J = job running; IJI = quick check")
    a = ap.parse_args()
    if snapshot() is None:
        print("! no power telemetry on this machine - energy will be missing")
    idle = None
    cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    res = {"machine": f"{cpu or platform.processor()} ({platform.machine()}), CPU only, 4 threads",
           "measured_at": time.strftime("%Y-%m-%d %H:%M"),
           "method": "ioreg AppleSmartBattery PowerTelemetryData AccumulatedSystemLoad/SystemLoadAccumulatorCount; A/B/A protocol: job frozen (SIGSTOP) vs running windows of one full telemetry interval each, incremental = running - mean(neighbouring paused)"}
    for key, script in [("image_tag", "image_worker.py"), ("embed", "embed_worker.py")]:
        print(f"running {key}: {a.pattern} windows (I = paused, J = running)...")
        r = run_worker(script, Path("/tmp/heatshift_bench") / key, a.pattern)
        inc_w = r["incremental_w"]
        r.update({"j_per_item_incremental": inc_w / r["items_per_s"],
                  "j_per_item_system": r["mean_w"] / r["items_per_s"]})
        r["items_per_kwh"] = 3.6e6 / r["j_per_item_incremental"]
        r["items_per_kwh_system"] = 3.6e6 / r["j_per_item_system"] if r["j_per_item_system"] else None
        res[key] = r
        print(f"  {r['items_per_s']:.1f} items/s, {r['mean_w']:.1f} W system ({inc_w:.1f} W above idle), "
              f"{r['j_per_item_incremental']:.3f} J/item -> {r['items_per_kwh']:,.0f} items/kWh")
    res["idle_w"] = min(res[k]["paused_w"] for k in ("image_tag", "embed"))
    DERIVED.mkdir(parents=True, exist_ok=True)
    (DERIVED / "measured_jobs.json").write_text(json.dumps(res, indent=2))
    print(f"wrote {DERIVED/'measured_jobs.json'}")


if __name__ == "__main__":
    main()
