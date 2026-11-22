"""Live dashboard of the fleet: workload tiers, prices, houses, servers, the queue and the heat.

    python examples/dashboard_server.py --open         # http://127.0.0.1:8765
    python examples/dashboard_server.py --from 2025-01-13 --days 14 --seconds-per-hour 0.5

A simulation shown live. This process steps the fleet model one hour at a
time (one simulated hour every 1.5 seconds by default) and serves a page that
refreshes itself. The fleet is the one of scheduler_viz.py: six houses, each
twice the size of the one before. Jobs arrive in six tiers (on-demand, spot,
reserved, capacity block, dedicated host, serverless) in shares set on the
page, and the TierScheduler places them: firm jobs at once in the house that
needs their heat, pushing spot jobs off if there is no room; spot jobs only
where the house can absorb all their heat.

Every tier's GPU-hour price is the base price P (the spot price) times a
factor. The page shows what a GPU-hour sold has cost so far, the income and
profit at P, and the P that covers the costs or reaches a target margin.

Power, hardware price and market prices all describe the fleet machine of
data/fleet_machine.json. P opens at the lowest-tier price of its GPU on
specialist GPU clouds, and the on-demand factor at their on-demand price over
that. The hardware price is an estimate, so the cost figures also come for its
low and high case.

    GET  /          the page (examples/dashboard_template.html)
    GET  /state     the current snapshot as JSON; ?since=H&run=R sends only newer history
    POST /control   {"action": ..., "value": ...} with the actions
                      pause, play, step, restart, finish (run to the end at once)
                      speed      value: one of SPEEDS
                      configure  value: {"mix": {tier: percent}, "load": percent,
                                         "start": "YYYY-MM-DD", "days": n}; restarts the run
                      prices     value: {"base": CHF, "factors": {tier: x}, "margin": percent};
                                 no restart, prices do not change where jobs go
                      costs      value: {name: percent} for names in COST_INPUTS, and
                                 "backup": "gas" or "heat_pump"; no restart

    machine  data/fleet_machine.json (as scheduler_viz.py)   model  digitalheat.fleet

Standard library only. It listens on this machine only.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import scheduler_viz
from digitalheat import (TIERS, FleetRun, ServerCosts, TierPricing, TierScheduler, job_stream,
                         tier_factors)

TEMPLATE = Path(__file__).parent / "dashboard_template.html"
QUEUE_SHOWN = 60                       # waiting jobs sent to the page, in the order they are placed
SPEEDS = (0.5, 1, 2, 5, 20)
HISTORY = ("must_run", "space_max", "water_max", "to_space", "to_water", "dumped", "backup")
YEAR = 2025                            # the weather year
# percent of the arriving jobs in each tier when the page opens; a team assumption
DEFAULT_MIX = {"on_demand": 30, "spot": 40, "reserved": 15, "block": 5, "dedicated": 5,
               "serverless": 5}
DEFAULT_MARGIN = 0.20                  # profit as a share of income that the page prices for
# costs the page takes as percentages: what each is a share of. Only maintenance opens with a
# value (from data/fleet_machine.json, itself a guess); the others count once one is entered.
COST_INPUTS = {
    "maintenance": "of the hardware price, each year; a guess with no source",
    "building": "of the hardware price, each year",
    "network": "of the hardware price, each year",
    "staff": "of the hardware price, each year",
    "installation": "of the hardware price, once; spread over its life",
    "idle_power": "of full-load power, drawn by each idle GPU",
}
YEARLY = ("building", "network", "staff")
# how the houses would heat without servers; the real house's bills are for gas
BACKUP_LABELS = {"gas": "Gas boiler", "heat_pump": "Heat pump"}
DEFAULT_BACKUP = "gas"


def r(value: float) -> float:
    return round(value, 2)


class LiveFleet:
    """A fleet run that advances in real time and can describe its current state."""

    def __init__(self, world: scheduler_viz.World, start: date, days: int,
                 seconds_per_hour: float, mix: dict[str, float] | None = None) -> None:
        self.world = world
        self.seconds_per_hour, self.speed, self.paused = seconds_per_hour, 1.0, False
        self.lock = threading.RLock()
        self.run_id = 0
        self.money = scheduler_viz.money(world)      # the fleet machine's costs and markets
        # the hardware price is an estimate: the same costs at its low, typical and high case
        self.costs = {case: ServerCosts(
            electricity_chf_per_kwh=self.money["tariff"], heat_pump_cop=self.money["cop"],
            hardware_chf_per_server=self.money[key],
            hardware_life_years=self.money["life_years"],
            maintenance_fraction_per_year=self.money["maintenance"], backup=DEFAULT_BACKUP,
            gas_chf_per_kwh=self.money["gas"],
            boiler_efficiency=self.money["boiler_efficiency"])
            for case, key in (("low", "hardware_low_chf"), ("mid", "hardware_chf"),
                              ("high", "hardware_high_chf"))}
        self.base = self.money["spot"]               # lowest tier on specialist GPU clouds
        self.default_factors = tier_factors(self.money["on_demand"] / self.money["spot"])
        self.factors = dict(self.default_factors)
        self.margin = DEFAULT_MARGIN
        self.default_costs = {name: 0.0 for name in COST_INPUTS}
        self.default_costs["maintenance"] = round(self.money["maintenance"] * 100, 6)
        self.set_costs(self.default_costs)
        self.configure({"mix": mix or DEFAULT_MIX, "load": world.load * 100,
                        "start": start.isoformat(), "days": days})

    # --- what is run, and at what prices ---

    def configure(self, value: dict) -> None:
        """Set the tier mix, the demand and the period, then start a new run."""
        try:
            mix = {key: float(value["mix"].get(key, 0)) for key in TIERS}
            load = float(value["load"]) / 100
            start, days = date.fromisoformat(str(value["start"])), int(value["days"])
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"configure needs mix, load, start and days ({error})") from None
        if set(value["mix"]) - set(TIERS):
            raise ValueError(f"unknown tier in the mix: {sorted(set(value['mix']) - set(TIERS))}")
        if min(mix.values()) < 0 or abs(sum(mix.values()) - 100) > 0.01:
            raise ValueError("the tier shares must be zero or more and add up to 100%")
        if not 0 < load <= 3:
            raise ValueError("demand must be above 0% and at most 300% of the fleet")
        first_day = (start - date(YEAR, 1, 1)).days
        if start.year != YEAR or days < 1 or first_day + days > self.world.hours // 24:
            raise ValueError(f"the run must lie inside {YEAR}, the year of the weather data")
        with self.lock:
            self.mix, self.load = mix, load
            self.start, self.days, self.first_day, self.hours = start, days, first_day, days * 24
            self.paused = False
            self.restart()

    def set_prices(self, value: dict) -> None:
        """Change the base price, the tier factors or the target margin; the run goes on."""
        try:
            base = float(value.get("base", self.base))
            factors = self.factors | {key: float(f) for key, f in value.get("factors", {}).items()}
            margin = float(value["margin"]) / 100 if "margin" in value else self.margin
        except (TypeError, AttributeError) as error:
            raise ValueError(f"prices needs numbers ({error})") from None
        if set(factors) - set(TIERS):
            raise ValueError(f"unknown tier in the factors: {sorted(set(factors) - set(TIERS))}")
        if base < 0 or min(factors.values()) < 0 or not 0 <= margin < 1:
            raise ValueError("prices cannot be negative and the margin must be below 100%")
        with self.lock:
            self.base, self.factors, self.margin = base, factors, margin

    def set_costs(self, value: dict) -> None:
        """Change the cost percentages of COST_INPUTS or the backup heating; the run goes on."""
        if not isinstance(value, dict) or set(value) - set(COST_INPUTS) - {"backup"}:
            raise ValueError(f"costs takes percentages for {sorted(COST_INPUTS)} and a backup")
        value = dict(value)
        backup = value.pop("backup", self.costs["mid"].backup)
        if not isinstance(backup, str) or backup not in BACKUP_LABELS:
            raise ValueError(f"backup must be one of {sorted(BACKUP_LABELS)}")
        try:
            percent = getattr(self, "cost_percent", self.default_costs) | {
                name: float(v) for name, v in value.items()}
        except (TypeError, ValueError) as error:
            raise ValueError(f"costs needs numbers ({error})") from None
        if min(percent.values()) < 0 or max(percent.values()) > 1000:
            raise ValueError("cost percentages must lie between 0 and 1000")
        share = {name: v / 100 for name, v in percent.items()}
        with self.lock:
            self.cost_percent = percent
            self.costs = {case: dataclasses.replace(
                costs, maintenance_fraction_per_year=share["maintenance"],
                yearly_fractions={name: share[name] for name in YEARLY},
                installation_fraction=share["installation"],
                idle_power_fraction=share["idle_power"], backup=backup)
                for case, costs in self.costs.items()}

    # --- running the simulation ---

    def restart(self) -> None:
        w = self.world
        with self.lock:
            stream = job_stream(w.jobs, w.weights, self.hours, self.load * w.gpus, w.seed,
                                scheduler_viz.BATCH_H, tier_mix=self.mix,
                                server_gpus=w.profile.gpus)
            self.run = FleetRun(w.sites(self.first_day, self.days), TierScheduler(), stream)
            # the same houses with no servers: what the backup heater would have supplied
            self.reference = FleetRun(w.sites(self.first_day, self.days))
            self.gpu_hours = self.avoided_kwh = 0.0
            self.run_id += 1
            self.step()                # so there is always a current hour to show

    @property
    def finished(self) -> bool:
        return self.run.t >= self.hours

    def step(self) -> bool:
        """Advance one simulated hour; False once the run is over."""
        with self.lock:
            if self.finished:
                return False
            t = self.run.t
            self.run.step()
            self.reference.step()
            self.gpu_hours += sum(rec.gpu_hours[t] for rec in self.run.records.values())
            self.avoided_kwh += (sum(rec.backup[t] for rec in self.reference.records.values())
                                 - sum(rec.backup[t] for rec in self.run.records.values()))
            return True

    def loop(self) -> None:
        """Runs in a background thread for the life of the server."""
        while True:
            time.sleep(self.seconds_per_hour / self.speed)
            if not self.paused:
                self.step()

    def control(self, action: str, value=None) -> None:
        if action == "pause":
            self.paused = True
        elif action == "play":
            self.paused = False
        elif action == "step":
            self.step()
        elif action == "restart":
            self.restart()
        elif action == "finish":
            with self.lock:
                while self.step():
                    pass
        elif action == "speed" and value in SPEEDS:
            self.speed = float(value)
        elif action == "configure" and isinstance(value, dict):
            self.configure(value)
        elif action == "prices" and isinstance(value, dict):
            self.set_prices(value)
        elif action == "costs":
            self.set_costs(value)
        else:
            raise ValueError(f"unknown action {action!r}")

    # --- describing the state ---

    def economics(self, waiting: dict[str, int]) -> tuple[list[dict], dict]:
        """The tiers' results and the money so far, at the current prices."""
        run, t = self.run, self.run.t
        sold = {key: run.tiers[key].billed_gpu_hours for key in TIERS}
        servers = sum(site.servers for site in run.sites)
        kw = self.world.profile.kw_per_gpu
        kwh = self.gpu_hours * kw
        idle_kwh = max(0.0, self.world.gpus * t - self.gpu_hours) * kw   # idle GPUs at full load
        cases = {case: costs.balance(servers, kwh, sum(sold.values()), self.avoided_kwh,
                                     days=t / 24, idle_full_load_kwh=idle_kwh)
                 for case, costs in self.costs.items()}
        balance = cases["mid"]
        pricing = TierPricing(self.base, self.factors)
        break_even = pricing.base_price_for(balance, sold)
        target = pricing.base_price_for(balance, sold, self.margin)
        spread = {}                              # the headline figures at the low and high price
        for case in ("low", "high"):
            spread |= {f"net_per_gpu_hour_{case}": cases[case].break_even_chf_per_gpu_hour(),
                       f"break_even_base_{case}": pricing.base_price_for(cases[case], sold),
                       f"target_base_{case}": pricing.base_price_for(cases[case], sold, self.margin),
                       f"hardware_{case}": r(cases[case].hardware_chf + cases[case].maintenance_chf)}
        income = pricing.income_chf(sold)
        profit = pricing.profit_chf(balance, sold)
        tiers = []
        for key, tier in TIERS.items():
            totals, factor = run.tiers[key], self.factors[key]
            tiers.append({
                "key": key, "name": tier.name, "firm": tier.firm, "share": self.mix[key],
                "factor": factor, "price": round(pricing.price(key), 4),
                "arrived": totals.arrived, "started": totals.started,
                "waiting": waiting.get(key, 0), "evictions": totals.evictions,
                "avg_wait_h": r(totals.wait_h / totals.started) if totals.started else 0.0,
                "gpu_hours": r(totals.gpu_hours), "sold": r(sold[key]),
                "income": r(sold[key] * pricing.price(key)),
                "break_even_price": None if break_even is None else round(break_even * factor, 4),
                "target_price": None if target is None else round(target * factor, 4),
            })
        money = {
            "days": r(t / 24), "servers": servers, "kwh": r(kwh), "avoided_kwh": r(self.avoided_kwh),
            "hardware": r(balance.hardware_chf), "maintenance": r(balance.maintenance_chf),
            "electricity": r(balance.electricity_chf),
            "other": {name: r(v) for name, v in balance.other_chf.items()},
            "cost": r(balance.cost_chf),
            "heat_saving": r(balance.heat_saving_chf),
            "net_cost": r(balance.cost_chf - balance.heat_saving_chf),
            "gpu_hours": r(self.gpu_hours), "sold": r(balance.gpu_hours),
            "cost_per_gpu_hour": balance.break_even_chf_per_gpu_hour(with_heat_saving=False),
            "net_per_gpu_hour": balance.break_even_chf_per_gpu_hour(),
            "base": self.base, "income": r(income), "profit": r(profit),
            "margin": profit / income if income > 0 else None,
            "break_even_base": break_even, "target_margin": self.margin, "target_base": target,
        } | spread
        return tiers, money

    def snapshot(self, since: int = 0, run_id: int | None = None) -> dict:
        """Everything the page shows. History is sent from hour `since` if the caller
        already has the earlier hours of this same run."""
        with self.lock:
            run, t = self.run, self.run.t
            now = t - 1                              # the hour that has just been settled
            start = since if run_id == self.run_id and 0 <= since <= t else 0
            sites, running_total, booked_total, busy, held = [], 0, 0, 0.0, 0.0
            for site in run.sites:
                record = run.records[site.name]
                jobs = sorted(site.running(now), key=lambda job: job.number)
                running_total += len(jobs)
                booked = len(site.jobs) - len(jobs)       # capacity blocks that start later
                booked_total += booked
                busy += site.load[now]
                held += sum(used[now] for used in site.used)
                servers = [[] for _ in range(site.servers)]
                for job in jobs:
                    servers[job.server].append({
                        "n": job.number, "g": job.held_gpus, "work": r(job.gpus * job.busy_share),
                        "run": r(job.run_h), "tier": job.tier, "start": job.started_h,
                        "end": job.started_h + job.hours, "ev": job.evictions})
                sites.append({
                    "name": site.name, "size": site.size, "servers": servers, "gpus": site.gpus,
                    "per_server": site.profile.gpus, "booked": booked,
                    "tank_full": r(site.model.tank.capacity_kwh),
                    "outdoor": site.temps_c[now], "indoor": r(record.indoor_c[now]),
                    "tank": r(record.tank_kwh[now]), "gpu": r(site.load[now]),
                    "history": {key: [r(v) for v in getattr(record, key)[start:t]]
                                for key in HISTORY},
                })
            waiting = run.scheduler.order(run.queue, t)
            by_tier: dict[str, int] = {}
            for job in waiting:
                by_tier[job.tier] = by_tier.get(job.tier, 0) + 1
            tiers, money = self.economics(by_tier)
            started = sum(tier["started"] for tier in tiers)
            pushed_back = sum(1 for job in waiting if job.evictions)
            return {
                "run": self.run_id, "t": t, "hours": self.hours, "from": start,
                "start": self.start.isoformat(), "paused": self.paused, "speed": self.speed,
                "speeds": SPEEDS, "seconds_per_hour": self.seconds_per_hour,
                "finished": self.finished, "year": YEAR,
                "fleet": {"gpus": self.world.gpus, "busy": r(busy), "held": r(held),
                          "running": running_total, "booked": booked_total,
                          "waiting": len(waiting), "waiting_by_tier": by_tier,
                          "arrived": sum(tier["arrived"] for tier in tiers), "started": started,
                          "done": started - running_total - booked_total - pushed_back,
                          "evictions": sum(tier["evictions"] for tier in tiers)},
                "sites": sites,
                "queue": [{"n": job.number, "g": job.held_gpus, "run": r(job.run_h),
                           "tier": job.tier, "waited": t - job.waiting_since_h,
                           "ev": job.evictions} for job in waiting[:QUEUE_SHOWN]],
                "config": {"mix": self.mix, "load": r(self.load * 100), "days": self.days,
                           "start": self.start.isoformat(), "seed": self.world.seed},
                "tiers": tiers, "money": money,
                "rules": {key: {"busy_share": tier.busy_share, "lead_h": tier.lead_h,
                                "whole_server": tier.whole_server,
                                "default_factor": self.default_factors[key]}
                          for key, tier in TIERS.items()},
                "inputs": self.money,
                "heating": {"backup": self.costs["mid"].backup, "options": {
                    key: {"label": label, "chf_per_heat_kwh": round(dataclasses.replace(
                        self.costs["mid"], backup=key).heat_value_chf_per_kwh, 4)}
                    for key, label in BACKUP_LABELS.items()}},
                "cost_inputs": {name: {"percent": self.cost_percent[name],
                                       "default": self.default_costs[name], "of": of}
                                for name, of in COST_INPUTS.items()},
            }


def make_server(live: LiveFleet, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                self.reply(200, TEMPLATE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/state":
                query = parse_qs(url.query)
                since = int(query.get("since", ["0"])[0])
                run_id = int(query["run"][0]) if "run" in query else None
                body = json.dumps(live.snapshot(since, run_id), separators=(",", ":"),
                                  allow_nan=False)
                self.reply(200, body.encode(), "application/json")
            else:
                self.reply(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/control":
                self.reply(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length) or b"{}")
                live.control(request.get("action", ""), request.get("value"))
                self.reply(200, b'{"ok":true}', "application/json")
            except (ValueError, json.JSONDecodeError) as error:
                self.reply(400, json.dumps({"error": str(error)}).encode(), "application/json")

        def log_message(self, *args) -> None:      # the page polls every second: stay quiet
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", default="2025-04-14",
                        help="first day of the run, YYYY-MM-DD (2025 weather)")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seconds-per-hour", type=float, default=1.5,
                        help="real seconds per simulated hour at speed 1x")
    parser.add_argument("--load", type=float, default=scheduler_viz.USAGE,
                        help="share of the fleet's GPU-hours the arriving jobs ask to hold")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()

    live = LiveFleet(scheduler_viz.World(args.load, args.seed), date.fromisoformat(args.start),
                     args.days, args.seconds_per_hour)
    server = make_server(live, args.port)
    threading.Thread(target=live.loop, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"fleet dashboard at {address}  ({args.days} days from {args.start}; Ctrl+C to stop)")
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
