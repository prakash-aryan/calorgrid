"""Trace a small two-house test case and write an interactive HTML viewer.

    python examples/trace_viz.py            # writes out/trace.html
    python examples/trace_viz.py --open     # ...and opens it in the browser

The run uses the unmodified model. Recording wrappers sit around Demand.step,
the Scheduler and the Dispatcher, so every arrival, heat signal, action and
heat flow in the page is what the real code produced. The weather and the job
types are a made-up test case, chosen so that every kind of interaction shows
up within 36 hours. None of its numbers are results.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import datetime
from pathlib import Path

from digitalheat import ArrivalMode, ArrivalProcess, JobType, Scenario, WeatherSeries

HOURS = 36
#            hours 0-9      10-21        22-27       28-35
TEMPS = {
    "a": [16.0] * 10 + [24.0] * 12 + [24.0] * 6 + [12.0] * 8,
    "b": [24.0] * 10 + [14.0] * 12 + [24.0] * 6 + [16.0] * 8,
}
SERVERS_PER_HOUSE = 2
OVERRIDES = {
    "tank_capacity_kwh": (6.0, "small tank so it fills within the test case"),
    "heating_day_threshold_c": (99.0, "every day heats, so demand follows the temperature"),
}
SRC = "test case for the trace viewer, not a measured job"

JOB_TYPES = [
    JobType(id="batch", name="Batch inference", runtime_h=6, power_kw=1.0, interruptible=True,
            price_chf_per_server_hour=0.20, checkpoint_overhead_h=0.25, source=SRC),
    JobType(id="urgent", name="Urgent job", runtime_h=3, power_kw=1.0, interruptible=True,
            price_chf_per_server_hour=0.50, relative_deadline_h=5, priority=1, source=SRC),
    JobType(id="render", name="Render (cannot pause)", runtime_h=4, power_kw=1.0,
            interruptible=False, price_chf_per_server_hour=0.30, source=SRC),
]
ARRIVALS = [
    ArrivalProcess("batch", ArrivalMode.UNLIMITED, SRC, buffer=2),
    ArrivalProcess("urgent", ArrivalMode.TRACE, SRC, trace_steps=(3, 14, 24, 24, 24, 24, 24)),
    ArrivalProcess("render", ArrivalMode.TRACE, SRC, trace_steps=(8, 17)),
]


def r(value: float | None) -> float | None:
    return None if value is None or value == float("inf") else round(value, 3)


class Recorder:
    """Wraps the live objects of a run and collects what passes between them."""

    def __init__(self, run) -> None:
        self.run = run
        self.cur: dict = {}
        demand, dispatcher, scheduler = run.demand, run.dispatcher, run.scheduler

        demand_step = demand.step
        def step(now_h, hour_of_day):
            created = demand_step(now_h, hour_of_day)
            self.cur["arrivals"] = [t.id for t in created]
            return created
        demand.step = step

        drop = dispatcher.drop
        def record_drop(task, now_h):
            self.cur["dropped"].append({"task": task.id, "was": task.status.value,
                                        "slack_h": r(task.slack_h(now_h))})
            drop(task, now_h)
        dispatcher.drop = record_drop

        finish = dispatcher.finish
        def record_finish(task, now_h):
            self.cur["finished"].append({"task": task.id, "server": task.server_id})
            finish(task, now_h)
        dispatcher.finish = record_finish

        apply = dispatcher.apply
        def record_apply(actions, now_h):
            apply(actions, now_h)
            self.cur["placed"] = self.tasks(now_h)
            self.cur["servers_placed"] = self.servers()
        dispatcher.apply = record_apply

        decide = scheduler.decide
        def record_decide(now_h, signals, demand_, registry):
            self.cur["signals"] = {
                hid: {"demand": r(s.demand_kwh), "soc": r(s.tank_soc_kwh),
                      "headroom": r(s.tank_headroom_kwh), "absorbable": r(s.absorbable_kwh)}
                for hid, s in signals.items()}
            self.cur["before"] = self.tasks(now_h)
            self.cur["servers_before"] = self.servers()
            actions = decide(now_h, signals, demand_, registry)
            self.cur["actions"] = [{"type": a.type.value, "task": a.task_id,
                                    "server": a.server_id, "reason": a.reason}
                                   for a in actions]
            return actions
        scheduler.decide = record_decide

    def tasks(self, now_h: float) -> list[dict]:
        return [{"id": t.id, "type": t.job_type_id, "status": t.status.value,
                 "done": r(t.work_done_h), "total": r(t.work_total_h + t.overhead_h),
                 "server": t.server_id, "slack": r(t.slack_h(now_h)),
                 "deadline": r(t.deadline_h), "interruptible": t.interruptible,
                 "interruptions": t.interruptions, "migrations": t.migrations}
                for t in self.run.demand.tasks.values()]

    def servers(self) -> list[dict]:
        return [{"id": s.id, "house": s.house_id, "state": s.state.value, "task": s.task_id}
                for s in self.run.fleet.registry.all()]

    def step(self) -> dict:
        run = self.run
        t = run.clock.t
        self.cur = {"t": t, "time": run.clock.at().strftime("%a %H:%M"),
                    "arrivals": [], "dropped": [], "finished": [], "actions": []}
        run.step()
        cur = self.cur
        cur["after"] = self.tasks(t + 1)
        cur["servers"] = self.servers()
        cur["houses"] = {}
        balances = run.balances()
        for record in run.ledger.records[-len(run.fleet.houses):]:
            f, b = record.flows, balances[record.house_id]
            cur["houses"][record.house_id] = {
                "temp": TEMPS[record.house_id][t],
                "flows": {"demand": r(f.demand_kwh), "server_heat": r(f.server_heat_kwh),
                          "direct": r(f.direct_kwh), "to_tank": r(f.to_tank_kwh),
                          "from_tank": r(f.from_tank_kwh), "dumped": r(f.dumped_kwh),
                          "backup": r(f.backup_kwh), "soc": r(f.tank_soc_kwh)},
                "record": {"electricity": r(record.electricity_kwh),
                           "running_hours": r(record.running_server_hours),
                           "servers_running": record.servers_running,
                           "income": r(record.income_chf)},
                "balance": {"covered_share": r(b.covered_share), "dumped": r(b.heat_dumped_kwh),
                            "utilisation": r(b.utilisation), "missed_pct": r(b.missed_deadline_pct),
                            "cost": r(b.electricity_cost_chf), "heat_value": r(b.heat_value_chf),
                            "income": r(b.computing_income_chf), "net": r(b.net_chf),
                            "break_even": r(b.break_even_chf_per_server_hour)},
            }
        return cur


def build_trace() -> dict:
    scenario = Scenario("trace viewer test case", SERVERS_PER_HOUSE, JOB_TYPES, ARRIVALS)
    for key, (value, why) in OVERRIDES.items():
        scenario.assumptions.override(key, value, why)
    start = datetime(2025, 1, 15)
    houses = [scenario.reference_house(WeatherSeries.from_temperatures(TEMPS[h], start), h)
              for h in TEMPS]
    run = scenario.build(houses, start)
    recorder = Recorder(run)
    steps = [recorder.step() for _ in range(HOURS)]
    a = scenario.assumptions
    return {
        "steps": steps,
        "houses": [{"id": h.id, "servers": list(h.server_ids),
                    "tank_capacity": h.storage.capacity_kwh} for h in houses],
        "job_types": [{"id": j.id, "name": j.name, "runtime_h": j.runtime_h,
                       "interruptible": j.interruptible, "deadline_h": j.relative_deadline_h,
                       "price": j.price_chf_per_server_hour} for j in JOB_TYPES],
        "arrivals": [{"job": p.job_type_id, "mode": p.mode.value,
                      "detail": (f"keeps {p.buffer} waiting" if p.mode is ArrivalMode.UNLIMITED
                                 else f"arrives at hours {sorted(set(p.trace_steps))}")}
                     for p in ARRIVALS],
        "params": {"server_kw": a.value("server_power_kw"),
                   "recovery": a.value("heat_recovery_fraction"),
                   "price": a.value("electricity_price_chf_per_kwh"),
                   "cop": a.value("heat_pump_cop"),
                   "hot_water": a.value("hot_water_kwh_per_day")},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("out/trace.html"))
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    parser.add_argument("--log", action="store_true", help="print a text log of the run")
    args = parser.parse_args()

    trace = build_trace()
    if args.log:
        for s in trace["steps"]:
            flows = "  ".join(
                f"{h}: dem {v['flows']['demand']:.2f} srv {v['flows']['server_heat']:.1f} "
                f"dump {v['flows']['dumped']:.1f} bkp {v['flows']['backup']:.2f} "
                f"soc {v['flows']['soc']:.1f}" for h, v in s["houses"].items())
            print(f"h{s['t']:02d} {flows}")
            for key in ("arrivals", "dropped", "finished"):
                if s[key]:
                    print(f"     {key}: {s[key]}")
            for action in s["actions"]:
                print(f"     {action['type'].upper()} {action['task']} -> {action['server']}: "
                      f"{action['reason']}")

    template = (Path(__file__).parent / "trace_template.html").read_text(encoding="utf-8")
    payload = json.dumps(trace, separators=(",", ":")).replace("</", "<\\/")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(template.replace("/*TRACE_JSON*/null", payload), encoding="utf-8")
    print(f"wrote {args.out} ({HOURS} hours, {len(trace['steps'][-1]['after'])} tasks)")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
