"""Configure the servers of one house and their jobs, and see the heat they make.

    python examples/workload_viz.py                    # writes out/workload.html
    python examples/workload_viz.py --date 2025-12-31  # another day of the Basel year
    python examples/workload_viz.py --open             # ...and opens it in the browser

In the page you choose how many servers stand in the house, which measured
system each one is, and which jobs run on it and when. It shows the GPUs
busy on each server, the electricity drawn, the heat produced, and how much of
the heat the house can absorb that day is fulfilled.

    jobs    real tasks from the Alibaba GPU cluster trace 2020 (data/alibaba_gpu_jobs.json)
    power   MLPerf Inference results with power measurement (data/mlperf_power.json)
    house   the heat range model of planner_viz.py, same Basel day and start state

Both data files come from scripts/fetch_workloads.py. A server's power is its
measured full-load power times the share of its GPUs that are busy.

The page recomputes everything in the browser. This script embeds one
configuration computed by the Python model, and the page checks its own
result against it when it loads.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import date
from pathlib import Path

import planner_viz
from digitalheat import (Placement, Scenario, gpu_hours_by_step, load_power_profiles,
                         load_trace_jobs)

DATA = planner_viz.ROOT / "data"
JOBS_FILE = DATA / "alibaba_gpu_jobs.json"
POWER_FILE = DATA / "mlperf_power.json"
MAX_SERVERS = 4

# A ready-made day of trace jobs for every server choice, by the kind of benchmark it runs.
# The jobs are real trace tasks; which ones run when is our arrangement, not trace data.
# "s" is the start hour and "k" the hours a job is repeated back to back (0 = run once).
DEFAULT_JOBS = {
    "language": {
        "name": "Language models", "benchmarks": ("llama", "gptj", "mixtral"),
        "about": "long batch runs, with short jobs filling the hours between them",
        "jobs": [
            {"j": "5-to-8-gpus-90th", "s": 0, "k": 0},
            {"j": "3-to-4-gpus-90th", "s": 6, "k": 0},
            {"j": "1-gpu-median", "s": 6, "k": 12},
            {"j": "3-to-4-gpus-median", "s": 13, "k": 6},
            {"j": "5-to-8-gpus-median", "s": 19, "k": 6},
        ]},
    "images": {
        "name": "Images", "benchmarks": ("stable-diffusion", "retinanet"),
        "about": "many short jobs back to back, busiest during the day",
        "jobs": [
            {"j": "under-1-gpu-median", "s": 0, "k": 24},
            {"j": "3-to-4-gpus-median", "s": 0, "k": 6},
            {"j": "2-gpus-median", "s": 6, "k": 12},
            {"j": "5-to-8-gpus-median", "s": 8, "k": 8},
            {"j": "3-to-4-gpus-median", "s": 18, "k": 6},
        ]},
    "speech": {
        "name": "Speech", "benchmarks": ("whisper",),
        "about": "one GPU busy all day, with a few longer runs",
        "jobs": [
            {"j": "1-gpu-median", "s": 0, "k": 24},
            {"j": "2-gpus-90th", "s": 7, "k": 0},
            {"j": "3-to-4-gpus-median", "s": 12, "k": 4},
            {"j": "2-gpus-90th", "s": 18, "k": 0},
        ]},
    "long": {
        "name": "Long runs", "benchmarks": ("rgat", "3d-unet"),
        "about": "a few jobs that each run for hours",
        "jobs": [
            {"j": "5-to-8-gpus-90th", "s": 1, "k": 0},
            {"j": "3-to-4-gpus-99th", "s": 8, "k": 0},
            {"j": "1-gpu-90th", "s": 8, "k": 0},
            {"j": "2-gpus-90th", "s": 20, "k": 0},
        ]},
}
FIRST_SERVER = "5.1-0061/llama2-70b-99/Offline"       # the server the page opens with


def default_set(benchmark: str) -> str:
    """The key of the ready-made job setup for a benchmark."""
    for key, setup in DEFAULT_JOBS.items():
        if benchmark.startswith(setup["benchmarks"]):
            return key
    return "language"


# Used only to check the page against Python: two systems, an overbooked hour, a long job.
CHECK_CONFIG = [
    {"p": "5.1-0062/stable-diffusion-xl/Offline", "jobs": [
        {"j": "5-to-8-gpus-99th", "s": 2, "k": 0},
        {"j": "2-gpus-90th", "s": 9, "k": 0},
        {"j": "under-1-gpu-median", "s": 0, "k": 24},
    ]},
    {"p": "5.0-0023/retinanet/Offline", "jobs": [
        {"j": "3-to-4-gpus-90th", "s": 5, "k": 0},
        {"j": "1-gpu-90th", "s": 20, "k": 0},
        {"j": "2-gpus-median", "s": 11, "k": 7},
    ]},
]


def r(value: float) -> float:
    return round(value, 4)


def simulate(config: list[dict], day: date) -> dict:
    """Server heat and the house's response for one configuration, from the Python model."""
    scenario = Scenario("workload", 0, [], [])
    a = scenario.assumptions
    profiles = {p.id: p for p in load_power_profiles(POWER_FILE)}
    jobs = {j.id: j for j in load_trace_jobs(JOBS_FILE)}
    recovery, dt_h = a.value("heat_recovery_fraction"), a.value("time_step_h")
    temps, _ = planner_viz.day_temperatures(day)
    heating_day = sum(temps) / len(temps) < a.value("heating_day_threshold_c")

    heat, electricity, gpu_hours = [0.0] * 24, [0.0] * 24, [0.0] * 24
    for server in config:
        profile = profiles[server["p"]]
        placed = [Placement(jobs[e["j"]], e["s"], e["k"]) for e in server["jobs"]]
        for hour, asked in enumerate(gpu_hours_by_step(placed, 24, dt_h)):
            kwh = profile.power_kw(asked / dt_h) * dt_h
            electricity[hour] += kwh
            heat[hour] += kwh * recovery
            gpu_hours[hour] += min(asked, profile.gpus * dt_h)

    model = scenario.heat_model(indoor_c=planner_viz.START_INDOOR_C, tank_fill_fraction=1.0)
    steps = []
    for hour, outdoor in enumerate(temps):
        d = model.deliver(heat[hour], outdoor, hour, heating_day)
        steps.append({"ts": r(d.to_space_kwh), "tw": r(d.to_water_kwh),
                      "bs": r(d.backup_space_kwh), "bw": r(d.backup_water_kwh),
                      "dump": r(d.dumped_kwh), "tin": r(d.indoor_c), "lvl": r(d.tank_level_kwh)})
    return {"config": config, "heat": [r(h) for h in heat], "steps": steps,
            "electricity_kwh": sum(electricity), "gpu_hours": sum(gpu_hours)}


def build(day: date) -> dict:
    house = planner_viz.build(day)
    a = Scenario("workload", 0, [], []).assumptions
    jobs_data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    power_data = json.loads(POWER_FILE.read_text(encoding="utf-8"))
    recovery = a.get("heat_recovery_fraction")
    profiles = load_power_profiles(POWER_FILE)
    jobs = {j.id: j for j in load_trace_jobs(JOBS_FILE)}
    for key, setup in DEFAULT_JOBS.items():           # a ready-made day must fit every server
        placed = [Placement(jobs[e["j"]], e["s"], e["k"]) for e in setup["jobs"]]
        if max(gpu_hours_by_step(placed, 24)) > min(p.gpus for p in profiles) + 1e-9:
            raise SystemExit(f"default jobs '{key}' overbook a server")
    opening = next(p for p in profiles if p.id == FIRST_SERVER)
    return {
        "meta": house["meta"],
        "params": house["params"] | {"recovery": recovery.value, "max_servers": MAX_SERVERS},
        "hours": house["hours"],
        "profiles": [
            {"id": p.id, "system": p.system, "accelerator": p.accelerator, "gpus": p.gpus,
             "benchmark": p.benchmark, "scenario": p.scenario, "kw": p.full_load_kw,
             "source": p.source, "url": p.url, "default": default_set(p.benchmark)}
            for p in profiles
        ],
        "jobs": [
            {"id": j.id, "gpus": j.gpus, "duration_h": j.duration_h, "task": j.task_name,
             "group": j.group, "rank": j.duration_rank, "cores": j.cpu_cores, "mem": j.mem_gib,
             "trace_job": row["trace_job"], "gpu_type": row["gpu_type"],
             "instances": row["instances"]}
            for j, row in zip(load_trace_jobs(JOBS_FILE), jobs_data["jobs"])
        ],
        "sources": {
            "jobs": {k: jobs_data[k] for k in ("source", "source_url", "data_url", "retrieved",
                                               "method", "tasks_in_trace", "tasks_finished",
                                               "groups")},
            "power": {k: power_data[k] for k in ("source", "source_url", "data_urls",
                                                 "retrieved", "method")},
            "recovery": {"value": recovery.value, "source": recovery.source,
                         "note": recovery.note},
        },
        "default_sets": {key: {"name": setup["name"], "about": setup["about"],
                               "jobs": setup["jobs"]} for key, setup in DEFAULT_JOBS.items()},
        "default": [{"p": opening.id,
                     "jobs": DEFAULT_JOBS[default_set(opening.benchmark)]["jobs"]}],
        "check": simulate(CHECK_CONFIG, day),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", default=planner_viz.DEFAULT_DATE,
                        help="day of the Basel year, YYYY-MM-DD")
    parser.add_argument("--out", type=Path, default=Path("out/workload.html"))
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()

    day = date.fromisoformat(args.date)
    data = build(day)
    template = (Path(__file__).parent / "workload_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(template.replace("/*WORKLOAD_JSON*/null", payload), encoding="utf-8")
    first = simulate(data["default"], day)
    print(f"wrote {args.out}: {len(data['jobs'])} trace jobs, {len(data['profiles'])} measured "
          f"results; the opening configuration makes {sum(first['heat']):.0f} kWh of heat, "
          f"{sum(s['ts'] + s['tw'] for s in first['steps']):.0f} kWh of it absorbed")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
