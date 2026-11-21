"""Live demo: the scheduler pauses and resumes a real process on this machine.

    python examples/live_job.py

A CPU-bound Python job is started as a real process. Heat demand is replayed
at 2 seconds per model hour: wanted, not wanted, wanted again. The same
scheduler and dispatcher code as the yearly simulation sends SIGSTOP when the
house cannot use heat and SIGCONT when it can (Linux and macOS).
"""

from __future__ import annotations

import sys
from datetime import datetime

from digitalheat import (ArrivalMode, ArrivalProcess, JobType, ProcessExecutor, Scenario,
                         WeatherSeries)

# The real job: counts primes forever and reports progress once a second.
JOB = """
import time
n, found, last = 2, 0, time.time()
while True:
    if all(n % d for d in range(2, int(n ** 0.5) + 1)):
        found += 1
    n += 1
    if time.time() - last >= 1:
        print(f"    [job] {found} primes found", flush=True)
        last = time.time()
"""

TEMPS_C = [-5.0] * 3 + [25.0] * 3 + [-5.0] * 3     # cold, warm, cold


def main() -> None:
    job = JobType(id="primes", name="Prime counting", runtime_h=1000, power_kw=1.0,
                  interruptible=True, price_chf_per_server_hour=0.0,
                  source="demo job, not used for any reported number")
    arrival = ArrivalProcess("primes", ArrivalMode.UNLIMITED, buffer=1, source="demo")
    scenario = Scenario("live demo", 1, [job], [arrival])
    # No tank and no hot water, and every day counts as a heating day, so the
    # heat signal follows the temperature directly.
    for key, value in (("tank_capacity_kwh", 0.0), ("hot_water_kwh_per_day", 0.0),
                       ("heating_day_threshold_c", 99.0)):
        scenario.assumptions.override(key, value, "live demo")

    start = datetime(2025, 1, 15)
    weather = WeatherSeries.from_temperatures(TEMPS_C, start)
    executor = ProcessExecutor({"primes": [sys.executable, "-u", "-c", JOB]})
    run = scenario.build([scenario.reference_house(weather)], start, executor=executor,
                         seconds_per_step=2.0)
    try:
        for hour, temp in enumerate(TEMPS_C):
            demand_kwh = run.fleet.houses["ref"].profile.demand_kwh(hour)
            print(f"hour {hour}: {temp:+.0f} degC, heat demand {demand_kwh:.1f} kWh", flush=True)
            for action in run.step():
                print(f"  -> {action.type.value.upper()} {action.task_id}: {action.reason}",
                      flush=True)
            run.clock.wait()
    finally:
        executor.shutdown()
    print("\nwall-clock events:")
    for when, event, task_id in executor.events:
        print(f"  {datetime.fromtimestamp(when):%H:%M:%S}  {event:7s} {task_id}")


if __name__ == "__main__":
    main()
