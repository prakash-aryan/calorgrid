"""Live controller: the year-simulation scheduler drives a REAL process on this machine.

One simulated hour of the reference house passes every `sec_per_hour` real seconds.
Every step the controller calls exactly the same `HeatShift.allocate()` used in the
8,760 h simulation. The real job (ResNet-50 image tagging or MiniLM embeddings) is one
entry in the house's queue:
    allocation > 0  -> SIGCONT (resume, frozen process continues instantly)
    allocation = 0  -> SIGSTOP (pause, RAM kept, zero CPU)
    migrate         -> SIGTERM -> worker finishes batch, writes checkpoint, exits
                       -> restart with --server <other site>  -> resumes at the checkpoint
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from heatshift.config import RESULTS, val  # noqa: E402
from heatshift.house import demand as house_demand  # noqa: E402
from heatshift.jobs import Job, generate_jobs  # noqa: E402
from heatshift.power import read_watts  # noqa: E402
from heatshift.scheduler import BACKFILL, ETA, POLICIES, SiteState  # noqa: E402
from heatshift.sim import forecast_temps  # noqa: E402
from heatshift.sites import REFERENCE, SITE_BY_KEY  # noqa: E402

PY = sys.executable
STATE_DIR = Path("/tmp/heatshift_live")
REAL_JOB_ID = 10_000_000
WORKERS = {"image_tag": "image_worker.py", "embed": "embed_worker.py"}


class RealJob:
    """Wraps the worker subprocess and its checkpoint."""

    def __init__(self, kind: str, limit: int, log):
        self.kind, self.limit, self.log = kind, limit, log
        self.state = STATE_DIR / kind
        self.proc: subprocess.Popen | None = None
        self.status = "queued"         # queued | loading | running | paused | checkpointed | finished
        self.server = "basel-ref-1"
        self.at, self.total = 0, limit
        self.items_per_s = 0.0
        self.sample = ""
        self.duty_paused = False
        self.lock = threading.Lock()

    def start(self, server: str):
        self.server = server
        env = {**os.environ, "HS_REPEAT": "5", "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(
            [PY, str(ROOT / "workers" / WORKERS[self.kind]), "--state", str(self.state),
             "--server", server, "--limit", str(self.limit)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        self.status = "loading"
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self.lock:
                self.at, self.total = ev.get("at", self.at), ev.get("total", self.total)
                e = ev["event"]
                if e == "running":
                    if self.status == "loading":
                        self.status = "running"
                    self.log(f"{'RESUMED from checkpoint at item ' + str(ev['at']) if ev.get('resumed') else 'STARTED'} on {ev['server']}")
                elif e == "progress":
                    self.items_per_s, self.sample = ev["items_per_s"], ev.get("sample", "")
                elif e == "checkpointed":
                    self.status = "checkpointed"
                    self.log(f"CHECKPOINT written at item {ev['at']} / {ev['total']} ({self.state}/checkpoint.json)")
                elif e == "finished":
                    self.status = "finished"
                    self.log(f"FINISHED all {ev['total']} items")

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def pause(self, quiet: bool = False):
        if self.alive and self.status == "paused" and self.duty_paused and not quiet:
            self.duty_paused = False      # a duty-cycle freeze becomes a real pause
            self.log(f"PAUSED (SIGSTOP) at item {self.at} - no heat budget for this job")
            return
        if self.alive and self.status in ("running", "loading"):
            self.duty_paused = False
            os.kill(self.proc.pid, signal.SIGSTOP)
            self.status = "paused"
            if not quiet:
                self.log(f"PAUSED (SIGSTOP) at item {self.at} - no heat budget for this job")

    def resume(self, quiet: bool = False):
        if self.alive and self.status == "paused":
            os.kill(self.proc.pid, signal.SIGCONT)
            self.status = "running"
            if not quiet:
                self.log(f"RESUMED (SIGCONT) at item {self.at} - heat needed")
            self.duty_paused = False
        elif self.status == "checkpointed" and not self.alive:
            self.start(self.server)

    def checkpoint_and_stop(self):
        if self.alive:
            if self.status == "paused":
                os.kill(self.proc.pid, signal.SIGCONT)
            self.proc.terminate()
            self.proc.wait(timeout=60)

    def kill(self):
        if self.alive:
            try:
                os.kill(self.proc.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            self.proc.kill()

    def fraction_done(self) -> float:
        return self.at / self.total if self.total else 0.0


class LiveController:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.events: deque = deque(maxlen=200)
        self.state: dict = {"running": False}
        self.override: dict = {}
        self.real: RealJob | None = None
        self.history: list[dict] = []

    # ---- API ---------------------------------------------------------------
    def log(self, msg: str):
        hh = self.state.get("hour_label", "")
        self.events.appendleft({"t": time.strftime("%H:%M:%S"), "sim": hh, "msg": msg})

    def start(self, day: str, job: str = "image_tag", sec_per_hour: float = 4.0, servers: int = 8,
              real_work_h: float = 16.0):
        self.stop()
        self.stop_flag.clear()
        self.state = {"running": False, "hour_label": ""}
        self.events.clear()
        self.history = []
        self.override = {}
        self.thread = threading.Thread(target=self._run, args=(day, job, sec_per_hour, servers, real_work_h),
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
        if self.real:
            self.real.kill()
        self.state["running"] = False

    def set_override(self, **kw):
        self.override.update({k: v for k, v in kw.items()})
        if kw.get("tank_full") and hasattr(self, "site"):
            self.site.soc = self.site.tank_max
            self.log("OVERRIDE: tank forced full (e.g. sunny afternoon, solar thermal) -> heat budget collapses")
        if "heat_factor" in kw:
            self.log(f"OVERRIDE: heat demand x{kw['heat_factor']}")

    def migrate(self, target: str = "davos_hotel"):
        r = self.real
        if not r or not r.alive:
            return
        site = SITE_BY_KEY.get(target)
        self.log(f"MIGRATE requested -> {site.name if site else target}: sending SIGTERM, worker checkpoints...")
        r.checkpoint_and_stop()
        time.sleep(0.5)
        ck = json.loads((r.state / "checkpoint.json").read_text())
        self.log(f"checkpoint: next item {ck['next']}, history {[h['event'] + '@' + h['server'] for h in ck['history']]}")
        r.start(server=f"{target.replace('_', '-')}-1")
        self.state["real_site"] = target

    def snapshot(self) -> dict:
        with self.lock:
            s = dict(self.state)
        s["events"] = list(self.events)[:40]
        s["history"] = self.history
        if self.real:
            s["real"] = {"kind": self.real.kind, "status": self.real.status, "at": self.real.at,
                         "total": self.real.total, "items_per_s": self.real.items_per_s,
                         "sample": self.real.sample, "server": self.real.server,
                         "pid": self.real.proc.pid if self.real.proc else None}
        return s

    # ---- loop --------------------------------------------------------------
    def _run(self, day: str, job_kind: str, sec_per_hour: float, servers: int, real_work_h: float):
        d = house_demand(REFERENCE)
        idx = np.nonzero(d.time.dt.strftime("%Y-%m-%d").to_numpy() == day)[0]
        if len(idx) == 0:
            self.log(f"unknown day {day}")
            return
        h0 = int(idx[0])
        fc = house_demand(REFERENCE, temps=forecast_temps(d.temp_c.to_numpy(), 1)).demand_kwh.to_numpy()
        dem = d.demand_kwh.to_numpy().copy()
        site = SiteState(REFERENCE, servers, SITE_BY_KEY[REFERENCE].tank_kwh, dem, fc, dump_allowed=False)
        self.site = site
        # start from the tank level the year simulation had at that hour
        res_path = RESULTS / "results.json"
        sla_per_day = 60.0
        if res_path.exists():
            R = json.loads(res_path.read_text())
            site.soc = float(R["hourly"]["soc"][max(h0 - 1, 0)])
            m = int(day[5:7]) - 1
            sla_per_day = R["monthly"][m]["sla_server_h"] / 30.4
        # synthetic SLA jobs for the house that day (same generator as the year run)
        synth = generate_jobs(max(sla_per_day, 1.0), hours=48, seed=int(day.replace("-", "")) % 10_000)
        for j in synth:
            j.arrival += h0
            j.deadline += h0
        pending = sorted(synth, key=lambda j: j.arrival)

        # the REAL job: 1 server, `real_work_h` server-hours, deadline in 20 h (a normal SLA job)
        speed = {"image_tag": 12.0, "embed": 130.0}[job_kind]
        meas = ROOT / "data" / "derived" / "measured_jobs.json"
        if meas.exists():
            speed = json.loads(meas.read_text()).get(job_kind, {}).get("items_per_s", speed)
        limit = int(speed * sec_per_hour * real_work_h)
        import shutil
        shutil.rmtree(STATE_DIR / job_kind, ignore_errors=True)
        self.real = RealJob(job_kind, limit, self.log)
        real = Job(REAL_JOB_ID, job_kind, h0, real_work_h, 1.0, h0 + 20, True)
        site.queue.append(real)
        self.log(f"day {day}: tank starts at {site.soc:.1f}/{site.tank_max:.0f} kWh, {len(synth)} SLA jobs arrive, "
                 f"real job = {job_kind} x {limit} items on this machine")
        pol = POLICIES["heatshift"]
        with self.lock:
            self.state = {"running": True, "day": day, "servers": servers, "sec_per_hour": sec_per_hour,
                          "tank_max": site.tank_max, "hour": 0, "real_site": REFERENCE}

        for k in range(24):
            if self.stop_flag.is_set():
                break
            t = h0 + k
            hour_label = f"{k:02d}:00"
            self.state["hour_label"] = hour_label
            while pending and pending[0].arrival <= t:
                site.queue.append(pending.pop(0))
            # heat-signal overrides for Q&A
            hf = self.override.get("heat_factor")
            site.demand[t] = dem[t] * hf if hf is not None else d.demand_kwh.iloc[t]
            site._t = t
            site.refresh_index()
            # real job remaining work follows REAL progress
            real.remaining = max(real_work_h * (1 - self.real.fraction_done()), 0.0)
            if self.real.status == "finished":
                real.remaining = 0
            budget = pol.budget(site, t)
            alloc = pol.allocate(site, t)
            in_house = self.state.get("real_site") == REFERENCE
            a_real = alloc.get(REAL_JOB_ID, 0.0) if in_house else 0.0
            if a_real < 0.1:          # less than 10% of a server is not worth waking the job: pause
                a_real = 0.0
            # act on the real process: full server -> run; fraction -> duty-cycle ("slow down"); 0 -> pause
            if self.real.status == "queued" and a_real > 0:
                self.real.start("basel-ref-1")
            elif a_real > 0:
                self.real.resume(quiet=self.real.duty_paused)
                if a_real < 0.99:
                    self.log(f"THROTTLED to {a_real:.0%} of a server (duty cycle: run {a_real:.0%} of the hour, frozen the rest)")
            elif in_house and self.real.status in ("running", "loading", "paused"):
                self.real.pause()
            # synthetic jobs progress in simulation
            sla = 0.0
            for j in site.queue:
                if j.id == REAL_JOB_ID:
                    continue
                a = alloc.get(j.id, 0.0)
                j.remaining -= a
                sla += a
            site.queue = [j for j in site.queue if j.id == REAL_JOB_ID or j.remaining > 1e-9]
            bf = alloc.get(BACKFILL, 0.0)
            total = sla + a_real + bf
            heat = site.apply_heat(t, total)
            row = {"hour": k, "label": hour_label, "temp_c": float(d.temp_c.iloc[t]),
                   "demand": float(site.demand[t]), "forecast": float(fc[t]), "budget": budget,
                   "sla": sla, "real": a_real, "backfill": bf, "compute": total,
                   "heat": heat["heat"], "backup": heat["backup"], "dump": heat["dump"], "soc": heat["soc"],
                   "queue": len(site.queue) - 1, "real_status": self.real.status}
            self.history.append(row)
            with self.lock:
                self.state.update(hour=k, row=row, watts=read_watts())
            self.log(f"heat demand {row['demand']:.1f} kWh, budget {budget:.1f} kW -> SLA {sla:.1f}, "
                     f"real job {a_real:.1f}, backfill {bf:.1f} kW, tank {heat['soc']:.1f} kWh")
            # let real time pass (the process runs or stays frozen)
            t_start = time.time()
            t_end = t_start + sec_per_hour
            duty_off = t_start + a_real * sec_per_hour if 0 < a_real < 0.99 else None
            while time.time() < t_end and not self.stop_flag.is_set():
                time.sleep(0.1)
                if duty_off and time.time() >= duty_off and self.real.status == "running":
                    self.real.pause(quiet=True)
                    self.real.duty_paused = True
                    duty_off = None
                if self.override.get("tank_full"):
                    self.override["tank_full"] = False
                    if self.real.status in ("running", "loading") and in_house:
                        # immediate re-plan on the new heat signal
                        site.refresh_index()
                        if pol.allocate(site, t).get(REAL_JOB_ID, 0) == 0:
                            self.real.pause()
        if self.real and self.real.alive and self.real.status == "paused":
            self.real.resume()        # day over: let it finish/keep running for inspection
        with self.lock:
            self.state["running"] = False
            self.state["finished"] = True
        self.log("day finished")


CONTROLLER = LiveController()
