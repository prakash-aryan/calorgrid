"""Run the heat-saving scheduler over a year in six houses and compare it with a baseline.

    python examples/scheduler_viz.py            # writes out/scheduler.html (about a minute)
    python examples/scheduler_viz.py --open     # ...and opens it in the browser
    python examples/scheduler_viz.py --load 0.7 # jobs ask for 70% of the fleet instead of 85%

Six houses in alpine towns, each twice the size of the one before. Every
house gets enough servers that 85% usage covers its heat need at the town's
mean outdoor temperature. Jobs from the Alibaba trace arrive at random with a
deadline and a priority, and three policies place them:

    first   first come first served, first house with room (the baseline)
    heat    highest priority first, to the house where the job's heat avoids the
            most backup heating over its run
    hold    as `heat`, but a job waits while less than half its heat would be useful

Each policy runs the whole year once. The page then adds up any range of
dates from the hourly results, so its presets and its date fields need no
scheduling in the browser.

    weather  data/ch_weather_2025.json (Open-Meteo, 2025)
    jobs     data/alibaba_gpu_jobs.json        power  data/mlperf_power.json
    prices   data/prices_2020.json             model  digitalheat.fleet
"""

from __future__ import annotations

import argparse
import json
import time
import webbrowser
from datetime import date, timedelta
from pathlib import Path

from digitalheat import (FirstFreeScheduler, FleetRun, HeatSavingScheduler, Scenario, Site,
                         job_stream, load_power_profiles, load_trace_jobs, mean_heat_need_kw,
                         servers_needed)
from digitalheat.fleet import JOB_CLASSES, URGENCY_WEIGHT

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOWNS = ["St. Moritz", "Bivio", "Zuoz", "Zermatt", "Samnaun", "Saas-Fee"]
SIZES = [0.5, 1, 2, 4, 8, 16]                # each house twice the size of the one before
SERVER = "5.0-0023/llama2-70b-99/Offline"    # the measured 8-GPU H100 system
USAGE = 0.85                                 # usage at which the servers cover the mean heat need
RANK_SHARE = {"median": 0.70, "90th percentile": 0.25, "99th percentile": 0.05}
BATCH_H = 4.0                                # jobs under an hour arrive as a batch this long
HARDWARE_LIFE_YEARS = 10
MAINTENANCE_PER_YEAR = 0.05                  # of the hardware price; team assumption
POLICIES = [
    ("first", "First free", "First come, first served: each job starts on arrival in the "
     "first house on the list that has room.", FirstFreeScheduler),
    ("heat", "Heat saving", "Highest priority first: each job goes to the house where its "
     "heat avoids the most backup heating over its run.", HeatSavingScheduler),
    ("hold", "Heat saving, holding jobs", "As heat saving, but a job waits while less than "
     "half its heat would be useful, until it has under 12 hours of slack.",
     lambda: HeatSavingScheduler(min_useful_share=0.5, start_margin_h=12)),
]


def r(values) -> list[float]:
    return [round(v, 2) for v in values]


class World:
    """Everything the runs share: weather, the house model, the server and the job mix."""

    def __init__(self, load: float, seed: int) -> None:
        weather = json.loads((DATA / "ch_weather_2025.json").read_text(encoding="utf-8"))
        self.weather_source = weather["source"]
        self.towns = {loc["name"]: loc for loc in weather["locations"]}
        self.scenario = Scenario("fleet", 0, [], [])
        a = self.scenario.assumptions
        self.recovery = a.value("heat_recovery_fraction")
        self.threshold = a.value("heating_day_threshold_c")
        self.profile = next(p for p in load_power_profiles(DATA / "mlperf_power.json")
                            if p.id == SERVER)
        jobs_file = json.loads((DATA / "alibaba_gpu_jobs.json").read_text(encoding="utf-8"))
        self.jobs_source = jobs_file
        group_share = {g["group"]: g["share_of_finished"] for g in jobs_file["groups"]}
        self.jobs = load_trace_jobs(DATA / "alibaba_gpu_jobs.json")
        self.weights = {j.id: group_share[j.group] * RANK_SHARE[j.duration_rank]
                        for j in self.jobs}
        self.hours = len(self.towns[TOWNS[0]]["temps_c"])
        self.load, self.seed = load, seed
        self.gpus = sum(site.gpus for site in self.sites())

    def sites(self, first_day: int = 0, days: int | None = None) -> list[Site]:
        """A fresh fleet: sites keep state, so every run builds its own.

        `first_day` and `days` cut a window out of the year's weather (hour 0 of
        the run is then midnight of that day). Servers are always sized on the
        whole year's mean temperature.
        """
        fleet = []
        for name, size in zip(TOWNS, SIZES):
            temps = self.towns[name]["temps_c"]
            heating = []
            for day in range(len(temps) // 24):
                mean = sum(temps[day * 24:(day + 1) * 24]) / 24
                heating += [mean < self.threshold] * 24
            model = self.scenario.heat_model().scaled(size)
            need = mean_heat_need_kw(model, sum(temps) / len(temps))
            servers = servers_needed(need, self.profile, self.recovery, USAGE)
            window = slice(first_day * 24, None if days is None else (first_day + days) * 24)
            fleet.append(Site(name, model, temps[window], heating[window], self.profile,
                              servers, self.recovery, size))
        return fleet

    def run(self, scheduler) -> FleetRun:
        stream = job_stream(self.jobs, self.weights, self.hours, self.load * self.gpus,
                            self.seed, BATCH_H) if scheduler is not None else ()
        return FleetRun(self.sites(), scheduler, stream).run(self.hours)


def presets(world: World) -> list[dict]:
    """Date ranges offered on the page, as (first day, last day) of 2025."""
    mean = [sum(world.towns[t]["temps_c"][h] for t in TOWNS) / len(TOWNS)
            for h in range(world.hours)]
    coldest = min(range(25), key=lambda d: sum(mean[d * 24:(d + 7) * 24]))
    first = date(2025, 1, 1)

    def week(start: date, label: str) -> dict:
        return {"label": label, "from": start.isoformat(),
                "to": (start + timedelta(days=6)).isoformat()}

    return [
        week(first + timedelta(days=coldest), "Coldest week of January"),
        week(date(2025, 1, 13), "Winter week"), week(date(2025, 4, 14), "Spring week"),
        week(date(2025, 7, 14), "Summer week"), week(date(2025, 10, 13), "Autumn week"),
        {"label": "Full year", "from": "2025-01-01", "to": "2025-12-31"},
    ]


def build(load: float, seed: int) -> dict:
    world = World(load, seed)
    a = world.scenario.assumptions
    prices = json.loads((DATA / "prices_2020.json").read_text(encoding="utf-8"))
    fx = prices["exchange"]["chf_per_usd"]
    gpu = next(g for g in prices["gpu_hour"] if g["id"] == "v100-2020")

    started = time.perf_counter()
    reference = world.run(None)
    runs = {}
    for key, _, _, make in POLICIES:
        runs[key] = world.run(make())
        print(f"  {key}: done after {time.perf_counter() - started:.0f} s")
    arrivals = [len(hour) for hour in runs["first"].arrivals]

    sites = world.sites()
    policies = []
    for key, name, about, _ in POLICIES:
        run = runs[key]
        policies.append({
            "key": key, "name": name, "about": about,
            "sites": [{"gpu": r(run.records[s.name].gpu_hours),
                       "absorbed": r(run.records[s.name].absorbed),
                       "dumped": r(run.records[s.name].dumped),
                       "backup": r(run.records[s.name].backup)} for s in sites],
            "started": run.started, "missed": run.missed, "wait": r(run.wait_h),
            "totals": {k: sum(sum(getattr(rec, k)) for rec in run.records.values())
                       for k in ("gpu_hours", "absorbed", "dumped", "backup")},
        })
    mean_temp = [sum(world.towns[t]["temps_c"][h] for t in TOWNS) / len(TOWNS)
                 for h in range(world.hours)]
    return {
        "year": 2025, "hours": world.hours,
        "sites": [{
            "name": s.name, "size": s.size, "servers": s.servers, "gpus": s.gpus,
            "elevation_m": world.towns[s.name]["elevation_m"],
            "mean_c": round(sum(s.temps_c) / len(s.temps_c), 1),
            "need_kw": round(mean_heat_need_kw(s.model, sum(s.temps_c) / len(s.temps_c)), 1),
            "reference": r(reference.records[s.name].backup),
        } for s in sites],
        "policies": policies, "arrivals": arrivals,
        "mean_temp": [round(v, 1) for v in mean_temp],
        "presets": presets(world),
        "server": {"system": world.profile.system, "accelerator": world.profile.accelerator,
                   "gpus": world.profile.gpus, "kw": world.profile.full_load_kw,
                   "benchmark": world.profile.benchmark, "source": world.profile.source,
                   "url": world.profile.url, "recovery": world.recovery, "usage": USAGE},
        "jobs": {"load": load, "seed": seed, "batch_h": BATCH_H, "total": sum(arrivals),
                 "source": world.jobs_source["source"], "source_url": world.jobs_source["source_url"],
                 "rank_share": RANK_SHARE, "urgency_weight": URGENCY_WEIGHT,
                 "classes": [{"name": name, "share": share, "deadline_factor": factor,
                              "priority": priority}
                             for name, (share, factor, priority) in JOB_CLASSES.items()]},
        "money": {
            "tariff": next(e for e in prices["electricity"] if e["year"] == 2020)["chf_per_kwh"],
            "tariff_source": next(e for e in prices["electricity"] if e["year"] == 2020)["source"],
            "hardware_chf": round(prices["hardware"]["usd"] * fx),
            "hardware_source": f"{prices['hardware']['system']}, "
                               f"{prices['hardware']['usd']:,} USD, 2020",
            "life_years": HARDWARE_LIFE_YEARS, "maintenance": MAINTENANCE_PER_YEAR,
            "cop": a.value("heat_pump_cop"),
            "on_demand": round(gpu["on_demand_usd"] * fx, 3), "spot": round(gpu["spot_usd"] * fx, 3),
            "gpu_source": f"{gpu['gpu']}, {gpu['provider']}, July 2020",
        },
        "weather_source": world.weather_source,
        "reference_total": sum(sum(rec.backup) for rec in reference.records.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--load", type=float, default=USAGE,
                        help="share of the fleet's GPU-hours the arriving jobs ask for")
    parser.add_argument("--seed", type=int, default=1, help="seed of the random job arrivals")
    parser.add_argument("--out", type=Path, default=Path("out/scheduler.html"))
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()

    print("running the year for each policy")
    data = build(args.load, args.seed)
    template = (Path(__file__).parent / "scheduler_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(template.replace("/*FLEET_JSON*/null", payload), encoding="utf-8")

    reference = data["reference_total"]
    print(f"{sum(s['servers'] for s in data['sites'])} servers in {len(data['sites'])} houses, "
          f"{data['jobs']['total']:,} jobs; without servers the houses need "
          f"{reference:,.0f} kWh of heating")
    for policy in data["policies"]:
        t = policy["totals"]
        print(f"  {policy['name']:<26} backup avoided {reference - t['backup']:>10,.0f} kWh, "
              f"dumped {t['dumped']:>8,.0f} kWh, missed {sum(policy['missed']):>5} jobs")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
