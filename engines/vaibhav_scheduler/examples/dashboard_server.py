"""Live dashboard of the fleet: houses, servers, running jobs, the queue and the heat.

    python examples/dashboard_server.py --open         # http://127.0.0.1:8765
    python examples/dashboard_server.py --from 2025-01-13 --days 14 --seconds-per-hour 0.5

A simulation shown live. This process steps the fleet model one hour at a
time (one simulated hour every 1.5 seconds by default) and serves a page that
refreshes itself. The fleet is the one of scheduler_viz.py: six houses, each
twice the size of the one before, with the heat-saving scheduler that holds a
job while less than half its heat would be useful. The run covers two weeks
of 2025 weather and then stops; Restart begins it again.

    GET  /          the page (examples/dashboard_template.html)
    GET  /state     the current snapshot as JSON; ?since=H&run=R sends only newer history
    POST /control   {"action": "pause" | "play" | "restart" | "step" | "speed", "value": 2}

Standard library only. It listens on this machine only.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import scheduler_viz
from digitalheat import FleetRun, HeatSavingScheduler, job_stream

TEMPLATE = Path(__file__).parent / "dashboard_template.html"
QUEUE_SHOWN = 60                       # waiting jobs sent to the page, highest priority first
SPEEDS = (0.5, 1, 2, 5, 20)
HOLD_SHARE, START_MARGIN_H = 0.5, 12   # the holding policy
HISTORY = ("must_run", "space_max", "water_max", "to_space", "to_water", "dumped", "backup")


def r(value: float) -> float:
    return round(value, 2)


class LiveFleet:
    """A fleet run that advances in real time and can describe its current state."""

    def __init__(self, world: scheduler_viz.World, start: date, days: int,
                 seconds_per_hour: float) -> None:
        self.world, self.start, self.days = world, start, days
        self.first_day = (start - date(start.year, 1, 1)).days
        self.hours = days * 24
        self.seconds_per_hour, self.speed, self.paused = seconds_per_hour, 1.0, False
        self.lock = threading.RLock()
        self.run_id = 0
        self.restart()

    # --- running the simulation ---

    def restart(self) -> None:
        w = self.world
        with self.lock:
            stream = job_stream(w.jobs, w.weights, self.hours, w.load * w.gpus, w.seed,
                                scheduler_viz.BATCH_H)
            self.run = FleetRun(w.sites(self.first_day, self.days),
                                HeatSavingScheduler(HOLD_SHARE, START_MARGIN_H), stream)
            self.arrived = 0
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
            self.arrived += len(self.run.arrivals[self.run.t])
            self.run.step()
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
        elif action == "speed" and value in SPEEDS:
            self.speed = float(value)
        else:
            raise ValueError(f"unknown action {action!r}")

    # --- describing the state ---

    def snapshot(self, since: int = 0, run_id: int | None = None) -> dict:
        """Everything the page shows. History is sent from hour `since` if the caller
        already has the earlier hours of this same run."""
        with self.lock:
            run, t = self.run, self.run.t
            now = t - 1                              # the hour that has just been settled
            start = since if run_id == self.run_id and 0 <= since <= t else 0
            sites, running_total, busy = [], 0, 0.0
            for site in run.sites:
                record = run.records[site.name]
                jobs = sorted(site.running(now), key=lambda job: job.number)
                running_total += len(jobs)
                busy += site.load[now]
                servers = [[] for _ in range(site.servers)]
                for job in jobs:
                    servers[job.server].append({
                        "n": job.number, "kind": job.job.id, "g": job.gpus, "run": r(job.run_h),
                        "cls": job.job_class, "start": job.started_h,
                        "end": job.started_h + job.hours, "due": r(job.deadline_h)})
                sites.append({
                    "name": site.name, "size": site.size, "servers": servers, "gpus": site.gpus,
                    "tank_full": r(site.model.tank.capacity_kwh),
                    "outdoor": site.temps_c[now], "indoor": r(record.indoor_c[now]),
                    "tank": r(record.tank_kwh[now]), "gpu": r(site.load[now]),
                    "history": {key: [r(v) for v in getattr(record, key)[start:t]]
                                for key in HISTORY},
                })
            waiting = sorted(run.queue, key=lambda job: (-job.priority(t), job.arrival_h))
            by_class: dict[str, int] = {}
            for job in waiting:
                by_class[job.job_class] = by_class.get(job.job_class, 0) + 1
            started = sum(run.started)
            return {
                "run": self.run_id, "t": t, "hours": self.hours, "from": start,
                "start": self.start.isoformat(), "paused": self.paused, "speed": self.speed,
                "speeds": SPEEDS, "seconds_per_hour": self.seconds_per_hour,
                "finished": self.finished,
                "fleet": {"gpus": self.world.gpus, "busy": r(busy), "running": running_total,
                          "waiting": len(waiting), "waiting_by_class": by_class,
                          "arrived": self.arrived, "started": started,
                          "done": started - running_total, "missed": sum(run.missed)},
                "sites": sites,
                "queue": [{"n": job.number, "kind": job.job.id, "g": job.gpus, "run": r(job.run_h),
                           "cls": job.job_class, "waited": t - job.arrival_h,
                           "slack": r(job.slack_h(t)), "priority": r(job.priority(t))}
                          for job in waiting[:QUEUE_SHOWN]],
                "policy": {"hold_share": HOLD_SHARE, "start_margin_h": START_MARGIN_H,
                           "load": self.world.load},
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
                body = json.dumps(live.snapshot(since, run_id), separators=(",", ":"))
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
                        help="share of the fleet's GPU-hours the arriving jobs ask for")
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
