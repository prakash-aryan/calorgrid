"""Run the reference house for a full year and print the yearly balance.

    python scripts/fetch_weather.py
    python examples/reference_year.py data/open_meteo_basel_2025.csv --servers 3

The two job types below are PLACEHOLDERS. Replace their numbers with your own
measurements and cite the source of each before reporting any result.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from digitalheat import ArrivalMode, ArrivalProcess, JobType, Scenario, WeatherSeries

PLACEHOLDER = "PLACEHOLDER - replace with a measured runtime and a cited price"

JOB_TYPES = [
    JobType(id="image_tagging", name="Image tagging batch", runtime_h=1.0, power_kw=1.0,
            interruptible=True, price_chf_per_server_hour=0.30, relative_deadline_h=24,
            checkpoint_overhead_h=0.02, region_constraint="CH-NW", priority=1,
            source=PLACEHOLDER),
    JobType(id="llm_batch", name="Batch LLM inference", runtime_h=4.0, power_kw=1.0,
            interruptible=True, price_chf_per_server_hour=0.20, checkpoint_overhead_h=0.05,
            source=PLACEHOLDER),
]
ARRIVALS = [
    ArrivalProcess("image_tagging", ArrivalMode.FIXED_DAILY, jobs_per_day=12,
                   source="PLACEHOLDER - state where the daily job count comes from"),
    ArrivalProcess("llm_batch", ArrivalMode.UNLIMITED, buffer=16,
                   source="Assumption: unlimited deadline-free jobs are always available"),
]


def print_day(run, label: str, month: int, day: int) -> None:
    first = run.clock.index_of(run.clock.start.replace(month=month, day=day))
    records = run.ledger.window("ref", first, 24)
    if not records:
        return
    print(f"\n{label}")
    print(" hour  demand  servers  direct  to tank  from tank  dumped  backup  tank")
    for hour, r in enumerate(records):
        f = r.flows
        print(f"  {hour:02d}  {f.demand_kwh:6.2f}  {r.servers_running:7d}  {f.direct_kwh:6.2f}  "
              f"{f.to_tank_kwh:7.2f}  {f.from_tank_kwh:9.2f}  {f.dumped_kwh:6.2f}  "
              f"{f.backup_kwh:6.2f}  {f.tank_soc_kwh:4.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("weather_csv", type=Path)
    parser.add_argument("--servers", type=int, default=3)
    parser.add_argument("--ledger", type=Path, default=Path("out/ledger.csv"))
    args = parser.parse_args()

    weather = WeatherSeries.from_open_meteo_csv(args.weather_csv)
    scenario = Scenario("reference house", args.servers, JOB_TYPES, ARRIVALS)
    house = scenario.reference_house(weather)
    run = scenario.build([house], weather.start)

    print(f"Weather: {weather.source}, {len(weather)} hours")
    print(f"Space heating {house.profile.total_space_heating_kwh:.0f} kWh, "
          f"hot water {house.profile.total_hot_water_kwh:.0f} kWh\n")
    balance = run.run()["ref"]
    print(balance.summary())

    print("\nJobs")
    for stats in run.demand.stats().values():
        print(f"  {stats.job_type_id}: {stats.done} done, {stats.missed} missed, "
              f"{stats.interruptions} interruptions, {stats.migrations} migrations")

    print_day(run, "15 January", 1, 15)
    print_day(run, "15 July", 7, 15)

    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    run.ledger.to_csv(args.ledger)
    print(f"\nHourly ledger written to {args.ledger}")
    print("\nAssumptions\n" + scenario.assumptions.to_markdown())


if __name__ == "__main__":
    main()
