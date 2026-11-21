"""Heat-following scheduler.

The SAME code drives (a) the 8,760-hour fleet simulation and (b) the live controller that
pauses / resumes a real job on this machine (live/controller.py).

Core idea - a server is a heater that happens to compute:
  * heat budget  = how many kW of servers can run this hour without dumping heat
                 = (demand_now + free_tank_space) / heat_recovery, capped at installed servers
  * EDF inside the budget: earliest deadline first, interruptible jobs checkpoint when paused
  * forecast-aware admission: a new job is only accepted by a site whose forecast heat sink
    until the job's deadline can absorb it (on top of already committed work)
  * proactive migration: every hour, an EDF feasibility test against the weather forecast;
    jobs that will not fit are checkpointed and moved to the site with most spare heat sink
    (House -> Basel -> Nordwestschweiz -> Schweiz) *before* they are late.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import val
from .jobs import Job

ETA = val("server.heat_recovery")


@dataclass
class SiteState:
    key: str
    servers: float
    tank_max: float
    demand: np.ndarray                 # actual hourly heat demand, kWh
    forecast: np.ndarray               # forecast hourly heat demand, kWh (what the planner sees)
    dump_allowed: bool = False
    price: float = 0.3325              # CHF/kWh electricity at this site (ElCom, municipality)
    down: np.ndarray | None = None     # unplanned outage mask (server / internet fault), not forecastable
    soc: float = 0.0
    queue: list[Job] = field(default_factory=list)
    # logs (filled by apply_heat)
    log: dict = field(default_factory=dict)

    def __post_init__(self):
        T = len(self.demand)
        self.soc = self.tank_max * 0.5
        cap = np.minimum(self.servers, self.forecast / ETA)
        self.cap_cum = np.concatenate([[0.0], np.cumsum(cap)])
        for k in ("compute", "sla", "backfill", "heat_used", "backup", "dump", "soc", "budget", "queue_work"):
            self.log[k] = np.zeros(T)

    # ---- heat model -------------------------------------------------------
    def heat_budget(self, t: int) -> float:
        """kW of servers that may run this hour without producing heat nobody can use."""
        if self.dump_allowed:
            return float(self.servers)
        free = self.demand[t] + (self.tank_max - self.soc)
        return float(np.clip(free / ETA, 0.0, self.servers))

    FORECAST_TRUST = val("scheduler.forecast_trust")   # plan on this share of forecast heat sink

    def forecast_capacity(self, t0: int, t1: int) -> float:
        """Server-hours this site can absorb in [t0, t1) per weather forecast (+ tank headroom)."""
        t1 = min(t1, len(self.cap_cum) - 1)
        if t1 <= t0:
            return 0.0
        now = self.cap_cum[t0 + 1] - self.cap_cum[t0]
        later = self.cap_cum[t1] - self.cap_cum[t0 + 1]
        return now + self.FORECAST_TRUST * later + (self.tank_max - self.soc) / ETA

    def is_down(self, t: int) -> bool:
        return self.down is not None and bool(self.down[t])

    def refresh_index(self) -> None:
        """Sorted deadline index of queued work (rebuilt once per hour) for O(log n) lookups."""
        if self.queue:
            dl = np.fromiter((j.deadline for j in self.queue), float, len(self.queue))
            rem = np.fromiter((j.remaining for j in self.queue), float, len(self.queue))
            o = np.argsort(dl)
            self._dl, self._cum = dl[o], np.cumsum(rem[o])
        else:
            self._dl, self._cum = np.zeros(0), np.zeros(0)
        self._t = getattr(self, "_t", 0)
        # slack of every queued deadline vs forecast capacity; suffix-min = how much extra work
        # can still be inserted before deadline d without breaking any later deadline (EDF test)
        if len(self._dl):
            T = len(self.cap_cum) - 1
            t0 = min(self._t, T - 1)
            cap = (self.cap_cum[t0 + 1] - self.cap_cum[t0]) \
                + self.FORECAST_TRUST * (self.cap_cum[np.minimum(self._dl.astype(int), T)] - self.cap_cum[t0 + 1]) \
                + (self.tank_max - self.soc) / ETA
            slack = cap - self._cum
            self._suffix_min = np.minimum.accumulate(slack[::-1])[::-1]
        else:
            self._suffix_min = np.zeros(0)
        self._pending: list[Job] = []

    def add(self, j: Job) -> None:
        self.queue.append(j)
        if hasattr(self, "_pending"):
            self._pending.append(j)

    def committed_before(self, deadline: int) -> float:
        if not hasattr(self, "_dl"):
            return sum(j.remaining for j in self.queue if j.deadline <= deadline)
        i = np.searchsorted(self._dl, deadline, side="right")
        base = self._cum[i - 1] if i > 0 else 0.0
        return base + sum(j.remaining for j in self._pending if j.deadline <= deadline)

    def apply_heat(self, t: int, compute_kw: float) -> dict:
        heat = ETA * compute_kw
        net = self.soc + heat - self.demand[t]
        backup = dump = 0.0
        if net < 0:
            backup, net = -net, 0.0
        elif net > self.tank_max:
            dump, net = net - self.tank_max, self.tank_max
        self.soc = net
        L = self.log
        L["compute"][t] = compute_kw
        L["heat_used"][t] = heat - dump
        L["backup"][t] = backup
        L["dump"][t] = dump
        L["soc"][t] = self.soc
        return {"heat": heat, "backup": backup, "dump": dump, "soc": self.soc}


# ---- policies --------------------------------------------------------------
BACKFILL = -1   # pseudo job id: best-effort spot work (no deadline) soaking up spare heat budget


class Policy:
    name = "base"
    label = ""
    migrates = False
    admission = False
    backfill = True

    def budget(self, s: SiteState, t: int) -> float:
        raise NotImplementedError

    def order(self, q: list[Job]) -> list[Job]:
        return sorted(q, key=lambda j: j.arrival)          # FIFO

    def allocate(self, s: SiteState, t: int) -> dict[int, float]:
        """Return {job_id: servers (kW) this hour}. Pure function of site state -> reusable live."""
        if s.is_down(t):
            return {}                                     # site offline: nothing runs here
        budget = self.budget(s, t)
        alloc: dict[int, float] = {}
        used = 0.0
        q = [j for j in s.queue if j.hold_until <= t and j.remaining > 1e-9]
        # non-interruptible jobs that already started must finish (no checkpoint)
        for j in q:
            if not j.interruptible and j.started:
                a = min(j.rate, j.remaining)
                alloc[j.id] = a
                used += a
        for j in self.order(q):
            if j.id in alloc:
                continue
            room = budget - used
            if room <= 1e-6:
                break
            if not j.interruptible and not self.can_start_atomic(s, t, j):
                continue
            a = min(j.rate, j.remaining, room)
            if not j.interruptible and a < min(j.rate, j.remaining) - 1e-9:
                continue                                      # atomic jobs need full rate
            alloc[j.id] = a
            used += a
        if self.backfill and budget - used > 1e-6:
            alloc[BACKFILL] = budget - used
        return alloc

    def can_start_atomic(self, s: SiteState, t: int, j: Job) -> bool:
        return True


class AlwaysOn(Policy):
    """Classic data centre: run everything ASAP at full power; surplus heat dumped (needs dry cooler)."""
    name, label = "always_on", "Always-on (data-centre style)"

    def budget(self, s, t):
        return float(s.servers)


class Thermostat(Policy):
    """Naive heat-following: run only while the house needs heat *right now*. No tank, no forecast, FIFO."""
    name, label = "thermostat", "Thermostat (heat-now only)"

    def budget(self, s, t):
        return float(np.clip(s.demand[t] / ETA, 0, s.servers))


class HeatShift(Policy):
    """Ours: tank-aware budget + EDF + forecast admission + proactive migration across the fleet."""
    name, label = "heatshift", "HeatShift (ours)"
    migrates = True
    admission = True

    def budget(self, s, t):
        return s.heat_budget(t)

    def order(self, q):
        # least laxity first: time left minus time still needed at full rate
        return sorted(q, key=lambda j: j.deadline - j.remaining / j.rate)

    def can_start_atomic(self, s, t, j):
        hours = int(np.ceil(j.remaining / j.rate))
        return s.forecast_capacity(t, t + hours) >= j.remaining + s.committed_before(t + hours)


class HeatShiftRescue(HeatShift):
    """HeatShift + rescue: a job that would otherwise miss its deadline may exceed the heat
    budget (needs a small dry cooler). Makes the deadline <-> dumped-heat trade-off explicit."""
    name, label = "heatshift_rescue", "HeatShift + deadline rescue"

    def allocate(self, s, t):
        alloc = super().allocate(s, t)
        for j in s.queue:
            if j.hold_until > t or j.remaining <= 1e-9:
                continue
            laxity = j.deadline - t - j.remaining / j.rate
            if laxity < 1.0:
                need = min(j.rate, j.remaining)
                if alloc.get(j.id, 0.0) < need:
                    alloc[j.id] = need
        # rescued work displaces backfill first
        if BACKFILL in alloc:
            over = sum(a for k, a in alloc.items() if k != BACKFILL) + alloc[BACKFILL] - self.budget(s, t)
            if over > 0:
                alloc[BACKFILL] = max(0.0, alloc[BACKFILL] - over)
        return alloc


POLICIES = {p.name: p for p in (AlwaysOn(), Thermostat(), HeatShift(), HeatShiftRescue())}


# ---- fleet dispatcher ------------------------------------------------------
def site_score(s: SiteState, t: int, j: Job) -> float:
    """Spare forecast heat sink (server-h) at site s for job j - full EDF admission test:
    the job must fit before its own deadline AND must not push any later deadline over."""
    own = s.forecast_capacity(t, j.deadline) - s.committed_before(j.deadline)
    later = np.inf
    if hasattr(s, "_dl") and len(s._dl):
        i = np.searchsorted(s._dl, j.deadline, side="right")
        if i < len(s._dl):
            later = s._suffix_min[i] - sum(p.remaining for p in s._pending)
    # rate limit: the job cannot use more than `rate` servers per hour
    if j.rate * max(0, j.deadline - t - j.hold_until + t) < j.remaining:
        return -np.inf
    return min(own, later)


_rng = np.random.default_rng(0)
PRICE_WEIGHT = val("regional.price_weight")   # module-level so scenarios can switch it off


def admit(sites: list[SiteState], t: int, j: Job, margin: float = val("scheduler.admission_margin")) -> SiteState | None:
    """Send the job to the feasible site with the largest *free fraction* of its forecast heat
    sink until the deadline (load-balances houses, blocks, hotels and pools fairly)."""
    best, best_rel = None, -1e18
    prices = [s.price for s in sites]
    p_lo, p_span = min(prices), (max(prices) - min(prices)) or 1.0
    w = PRICE_WEIGHT
    for s in sites:
        if s.servers < j.rate or s.is_down(t):
            continue
        sc = site_score(s, t, j)
        if sc < j.remaining * margin:
            continue
        cap = s.forecast_capacity(t, j.deadline)
        rel = sc / cap - w * (s.price - p_lo) / p_span + 0.02 * _rng.random()   # cheaper power wins ties
        if rel > best_rel:
            best, best_rel = s, rel
    return best


def failover(sites: list[SiteState], t: int) -> int:
    """A site just went offline: move every job away (interruptible ones resume from their last
    hourly checkpoint, atomic ones restart from zero)."""
    moved = 0
    for s in sites:
        if not s.is_down(t) or not s.queue:
            continue
        others = [o for o in sites if o is not s]
        for j in list(s.queue):
            if j.hold_until > t:
                continue
            if not j.interruptible and j.started:
                j.remaining, j.started = j.work, False
            best = admit(others, t, j, margin=1.0)
            if best is not None:
                s.queue.remove(j)
                j.hold_until, j.site = t + 1, best.key
                j.migrations += 1
                best.add(j)
                moved += 1
    return moved


def proactive_migrate(sites: list[SiteState], t: int, safety: float = 0.95) -> int:
    """EDF feasibility test per site against the forecast; move jobs that will not make it."""
    moved = 0
    for s in sites:
        if not s.queue:
            continue
        q = sorted(s.queue, key=lambda x: x.deadline)
        dl = np.fromiter((j.deadline for j in q), int, len(q))
        cum = np.cumsum(np.fromiter((j.remaining for j in q), float, len(q)))
        T = len(s.cap_cum) - 1
        cap = s.cap_cum[np.minimum(dl, T)] - s.cap_cum[t] + (s.tank_max - s.soc) / ETA
        bad = np.nonzero(cum > cap * safety)[0]
        if len(bad) == 0:
            continue
        shift = 0.0
        for i in bad:
            j = q[i]
            if not j.interruptible or j.hold_until > t:
                continue
            if cum[i] - shift <= cap[i] * safety:
                continue
            # infeasible here -> find a better home
            others = [o for o in sites if o is not s and o.servers >= j.rate]
            best = admit(others, t, j, margin=1.1)
            if best is not None:
                s.queue.remove(j)
                j.hold_until = t + 1           # checkpoint upload/download takes ~1 h slot
                j.migrations += 1
                j.site = best.key
                best.add(j)
                shift += j.remaining
                moved += 1
    return moved
