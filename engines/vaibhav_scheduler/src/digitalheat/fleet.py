"""A fleet of houses with servers, a queue of jobs, and the policy that places them.

Entities
    Site                 one house: its heat model, weather, servers and the GPUs already booked
    QueuedJob            a trace job with an arrival hour, a deadline and a rising priority
    Tier                 a way of buying GPU-hours: its price, and how its jobs are placed
    HeatSavingScheduler  sends each job where its heat replaces the most backup heating
    FirstFreeScheduler   baseline: first come first served, first house with room
    TierScheduler        places jobs by tier: firm jobs at once, spot only where its heat fits
    FleetRun             the hourly loop and its records

Built on the heat range model (HouseHeatModel), the measured servers
(PowerProfile) and the trace jobs (TraceJob). A job that has started runs to
its end on the server it was given, except a spot job, which a firm job may
push off its server. Steps are one hour.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from .compute import PowerProfile
from .heat import HouseHeatModel
from .workload import TraceJob, _poisson

EPS = 1e-9
URGENCY_WEIGHT = 10.0       # priority gained by the time a job has used all its slack

# class -> (share of jobs, deadline as a multiple of the run time, starting priority)
JOB_CLASSES: dict[str, tuple[float, float, int]] = {
    "urgent": (0.2, 1.5, 3),
    "normal": (0.5, 3.0, 2),
    "relaxed": (0.3, 6.0, 1),
}


@dataclass(frozen=True)
class Tier:
    """One way of buying GPU-hours.

    A firm tier's jobs start as soon as a server has room, in the house that
    needs their heat most. Spot jobs start only where their heat adds no
    overflow, and wait otherwise.
    """

    key: str
    name: str
    price_factor: float           # GPU-hour price as a multiple of the spot price
    firm: bool = True
    whole_server: bool = False    # holds every GPU of its server and pays for all of them
    busy_share: float = 1.0       # share of the held GPUs that work; only those are paid for
    lead_h: int = 0               # booked this many hours before it starts

    @property
    def evicts(self) -> bool:
        """Whether its jobs may push a spot job off a server to start now."""
        return self.firm and self.lead_h == 0


# Price of four tiers as a multiple of the on-demand price: a commitment is cheaper, a
# whole machine or pay-per-use dearer. Team assumptions, from the usual ranges.
TIER_VS_ON_DEMAND = {"reserved": 0.6, "block": 0.9, "dedicated": 1.1, "serverless": 1.5}
ON_DEMAND_FACTOR = 3.33         # on-demand over spot when no market price is at hand


def tier_factors(on_demand_factor: float = ON_DEMAND_FACTOR) -> dict[str, float]:
    """Every tier's price as a multiple of the spot price, given how many times dearer
    on-demand is than spot on the market. Spot is 1; the other tiers follow on-demand."""
    if on_demand_factor <= 0:
        raise ValueError("on-demand must cost a positive multiple of spot")
    factors = {"spot": 1.0, "on_demand": round(on_demand_factor, 2)}
    factors |= {key: round(share * on_demand_factor, 2) for key, share in TIER_VS_ON_DEMAND.items()}
    return factors


# In the order they are placed each hour: reserved first, then by price. The price
# factors are the defaults of tier_factors; the busy share and the lead time are team
# assumptions.
TIERS: dict[str, Tier] = {tier.key: tier for tier in (
    Tier("reserved", "Reserved", tier_factors()["reserved"]),
    Tier("serverless", "Serverless", tier_factors()["serverless"], busy_share=0.5),
    Tier("dedicated", "Dedicated host", tier_factors()["dedicated"], whole_server=True),
    Tier("on_demand", "On-demand", tier_factors()["on_demand"]),
    Tier("block", "Capacity block", tier_factors()["block"], lead_h=24),
    Tier("spot", "Spot", 1.0, firm=False),
)}


def mean_heat_need_kw(model: HouseHeatModel, mean_outdoor_c: float) -> float:
    """Heat the house needs at a steady outdoor temperature: the loss at the lower
    edge of the comfort band plus the average hot water draw."""
    building = model.building
    loss = building.loss_kw_per_k * max(0.0, building.min_temp_c - mean_outdoor_c)
    return loss + model.hot_water_kwh_per_day / 24


def servers_needed(need_kw: float, profile: PowerProfile, heat_recovery_fraction: float,
                   usage: float = 0.85) -> int:
    """Servers whose heat at `usage` of full load covers `need_kw`; at least one."""
    heat_per_server = profile.full_load_kw * heat_recovery_fraction * usage
    return max(1, math.ceil(need_kw / heat_per_server - EPS))


# ----------------------------------------------------------------------- QueuedJob


@dataclass
class QueuedJob:
    job: TraceJob
    arrival_h: int
    run_h: float                # hours its GPUs are busy
    deadline_h: float
    base_priority: int = 1
    job_class: str = "normal"
    started_h: int | None = None
    site: str | None = None
    server: int | None = None   # index of the server it runs on at that site
    missed_h: int | None = None  # hour it was dropped because its deadline was out of reach
    number: int = 0             # position in the arrival stream, for telling jobs apart
    tier: str = ""              # key in TIERS; empty for jobs placed by class and deadline
    held: float | None = None   # GPUs kept for it on its server; the job's own by default
    busy_share: float = 1.0     # share of its GPUs that work while it runs
    queued_h: int | None = None  # hour it last joined the queue, if it was pushed off a server
    evictions: int = 0          # times a firm job pushed it off a server

    @property
    def gpus(self) -> float:
        return self.job.gpus

    @property
    def held_gpus(self) -> float:
        """GPUs no other job can use while it runs."""
        return self.job.gpus if self.held is None else self.held

    @property
    def billed_gpus(self) -> float:
        """GPUs the client pays for in a full hour of the run."""
        return self.held_gpus * self.busy_share

    @property
    def waiting_since_h(self) -> int:
        return self.arrival_h if self.queued_h is None else self.queued_h

    def share_of_hour(self, t: int) -> float:
        """Part of hour `t` the job runs for; its last hour may be partial."""
        if self.started_h is None or t < self.started_h:
            return 0.0
        return max(0.0, min(1.0, self.run_h - (t - self.started_h)))

    @property
    def hours(self) -> int:
        """Whole hours the GPUs stay booked."""
        return math.ceil(self.run_h - EPS)

    @property
    def gpu_hours(self) -> float:
        return self.gpus * self.run_h

    @property
    def key(self) -> tuple[str, float, float]:
        """Jobs with the same key put the same load on a server."""
        return self.job.id, self.run_h, self.busy_share

    def load(self) -> list[float]:
        """GPU-hours of work in each hour of the run (the last hour may be partial)."""
        busy = self.gpus * self.busy_share
        return [busy * min(1.0, self.run_h - i) for i in range(self.hours)]

    @property
    def latest_start_h(self) -> float:
        return self.deadline_h - self.run_h

    def slack_h(self, now_h: int) -> float:
        """Hours the job can still wait and finish on time."""
        return self.latest_start_h - now_h

    def must_start(self, now_h: int, margin_h: float = 1.0) -> bool:
        """True when less than `margin_h` of slack is left; with the default, waiting
        one more hour would miss the deadline."""
        return self.slack_h(now_h) < margin_h

    def missed(self, now_h: int) -> bool:
        return self.slack_h(now_h) < -EPS

    def priority(self, now_h: int) -> float:
        """Rises by one for every hour waited, and by up to URGENCY_WEIGHT as the
        slack to the deadline is used up."""
        waited = now_h - self.arrival_h
        slack = self.latest_start_h - self.arrival_h
        used = min(1.0, waited / slack) if slack > EPS else 1.0
        return self.base_priority + waited + URGENCY_WEIGHT * used


def job_stream(jobs: Sequence[TraceJob], weights: Mapping[str, float], hours: int,
               gpu_hours_per_hour: float, seed: int = 0, batch_h: float = 4.0,
               tier_mix: Mapping[str, float] | None = None,
               server_gpus: int = 8) -> list[list[QueuedJob]]:
    """Random arrivals for every hour of a run.

    Each arrival is one of `jobs`, drawn with the given weights. A job shorter
    than an hour arrives as a batch of copies run back to back for `batch_h`
    hours. The rate is set so the arrivals ask to hold `gpu_hours_per_hour` on
    average.

    Without `tier_mix`, class, and with it deadline and starting priority, is
    drawn from JOB_CLASSES. With it, every arrival draws a tier from TIERS in
    those shares instead and has no deadline; a job on a dedicated host holds
    a whole server of `server_gpus`.
    """
    rng = random.Random(seed)
    run_h = {j.id: (j.duration_h if j.duration_h >= 1 else batch_h) for j in jobs}
    pick = [weights[j.id] for j in jobs]
    mean_gpu_hours = sum(w * j.gpus * run_h[j.id] for w, j in zip(pick, jobs)) / sum(pick)
    classes = list(JOB_CLASSES)
    shares = [JOB_CLASSES[c][0] for c in classes]
    tiers, tier_shares = [], []
    if tier_mix is not None:
        tiers = [TIERS[key] for key, share in tier_mix.items() if share > 0]
        tier_shares = [tier_mix[tier.key] for tier in tiers]
        if not tiers or min(tier_mix.values()) < 0:
            raise ValueError("the tier mix needs shares of zero or more, one of them positive")
        whole = sum(share for tier, share in zip(tiers, tier_shares) if tier.whole_server)
        mean_run_h = sum(w * run_h[j.id] for w, j in zip(pick, jobs)) / sum(pick)
        mean_gpu_hours = (mean_gpu_hours * (sum(tier_shares) - whole)
                          + server_gpus * mean_run_h * whole) / sum(tier_shares)
    rate = gpu_hours_per_hour / mean_gpu_hours
    stream, number = [], 0
    for hour in range(hours):
        arrivals = []
        for job in rng.choices(jobs, pick, k=_poisson(rng, rate)):
            number += 1
            if tiers:
                tier = rng.choices(tiers, tier_shares)[0]
                arrivals.append(QueuedJob(
                    job, hour, run_h[job.id], math.inf, job_class=tier.key, number=number,
                    tier=tier.key, held=float(server_gpus) if tier.whole_server else None,
                    busy_share=tier.busy_share))
                continue
            job_class = rng.choices(classes, shares)[0]
            _, factor, priority = JOB_CLASSES[job_class]
            arrivals.append(QueuedJob(job, hour, run_h[job.id], hour + factor * run_h[job.id],
                                      priority, job_class, number=number))
        stream.append(arrivals)
    return stream


# ---------------------------------------------------------------------------- Site


@dataclass
class Site:
    """One house with its servers.

    `used[server][hour]` is the GPUs booked on a server and `load[hour]` the
    GPU-hours running on all of them; both are known ahead because a started
    job runs to its end.
    """

    name: str
    model: HouseHeatModel
    temps_c: Sequence[float]            # outdoor temperature in each hour
    heating: Sequence[bool]             # whether each hour falls on a heating day
    profile: PowerProfile
    servers: int
    heat_recovery_fraction: float
    size: float = 1.0
    used: list[list[float]] = field(init=False, repr=False)
    load: list[float] = field(init=False, repr=False)
    jobs: list[QueuedJob] = field(init=False, repr=False, default_factory=list)   # not yet finished
    now_h: int = field(init=False, default=0)       # the hour the heat model stands at
    booked_until_h: int = field(init=False, default=0)   # end of the last job placed here
    _scores: dict = field(init=False, repr=False, default_factory=dict)
    _temps: list[float] = field(init=False, repr=False)
    _heating: list[bool] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.model.dt_h != 1.0:
            raise ValueError("the fleet model works in one-hour steps")
        pad = 72                                   # room for jobs that run past the end
        horizon = len(self.temps_c) + pad
        self.used = [[0.0] * horizon for _ in range(self.servers)]
        self.load = [0.0] * horizon
        self._temps = list(self.temps_c) + [self.temps_c[-1]] * pad
        self._heating = list(self.heating) + [self.heating[-1]] * pad

    @property
    def gpus(self) -> int:
        return self.servers * self.profile.gpus

    def heat_kwh(self, gpu_hours: float) -> float:
        """Useful heat from this many GPU-hours of work."""
        return self.profile.kw_per_gpu * gpu_hours * self.heat_recovery_fraction

    def server_with_room(self, job: QueuedJob, start_h: int) -> int | None:
        """The first server that has the job's GPUs free for its whole run."""
        limit = self.profile.gpus - job.held_gpus + EPS
        end = start_h + job.hours
        if end > len(self.load):
            return None
        for index, booked in enumerate(self.used):
            if all(booked[h] <= limit for h in range(start_h, end)):
                return index
        return None

    def place(self, job: QueuedJob, server: int, start_h: int) -> None:
        booked = self.used[server]
        for i, gpu_hours in enumerate(job.load()):
            booked[start_h + i] += job.held_gpus
            self.load[start_h + i] += gpu_hours
        job.started_h, job.site, job.server = start_h, self.name, server
        self.jobs.append(job)
        self.booked_until_h = max(self.booked_until_h, start_h + job.hours)
        self._scores.clear()

    def spot_in_the_way(self, job: QueuedJob, start_h: int) -> tuple[int, list[QueuedJob]] | None:
        """The server where `job` fits once the fewest spot jobs are pushed off, and
        those jobs, most recently started first. None if no server can be freed."""
        end = start_h + job.hours
        if end > len(self.load):
            return None
        spot: dict[int, list[QueuedJob]] = defaultdict(list)
        for other in self.jobs:
            if (other.tier and not TIERS[other.tier].firm and other.started_h < end
                    and other.started_h + other.hours > start_h):
                spot[other.server].append(other)
        best = None
        for index, running in spot.items():
            booked = self.used[index]
            over = [booked[h] + job.held_gpus - self.profile.gpus for h in range(start_h, end)]
            out = []
            for victim in sorted(running, key=lambda j: (-j.started_h, -j.number)):
                if max(over) <= EPS:
                    break
                span = range(max(start_h, victim.started_h) - start_h,
                             min(end, victim.started_h + victim.hours) - start_h)
                if all(over[i] <= EPS for i in span):
                    continue                          # frees nothing where room is short
                out.append(victim)
                for i in span:
                    over[i] -= victim.held_gpus
            if max(over) <= EPS and (best is None or len(out) < len(best[1])):
                best = (index, out)
        return best

    def evict(self, job: QueuedJob, now_h: int) -> None:
        """Push a running job off its server from hour `now_h` on. It keeps the work
        done so far: only the hours still to run are left on it."""
        done = max(0, now_h - job.started_h)
        booked = self.used[job.server]
        for i, gpu_hours in enumerate(job.load()):
            if i >= done:
                h = job.started_h + i
                booked[h] = max(0.0, booked[h] - job.held_gpus)
                self.load[h] = max(0.0, self.load[h] - gpu_hours)
        self.jobs.remove(job)
        job.run_h -= done
        job.started_h = job.site = job.server = None
        job.queued_h, job.evictions = now_h, job.evictions + 1
        self._scores.clear()

    def running(self, t: int) -> list[QueuedJob]:
        """The jobs whose GPUs are booked in hour `t`. Jobs that ended before `t` are
        forgotten, so ask for hours in order."""
        self.jobs = [job for job in self.jobs if job.started_h + job.hours > t]
        return [job for job in self.jobs if job.started_h <= t]

    def ahead_kwh(self, start_h: int, hours: int,
                  extra: Sequence[float] = ()) -> tuple[float, float]:
        """Backup heat the house would need, and server heat it would dump, over the
        coming hours with what is booked plus `extra` GPU-hours per hour. Uses the
        known weather as the forecast."""
        end = start_h + hours
        booked = self.load[start_h:end]
        if extra:
            booked = [b + x for b, x in zip(booked, extra)]
        per_gpu_hour = self.profile.kw_per_gpu * self.heat_recovery_fraction
        return self.model.ahead_kwh([per_gpu_hour * g for g in booked],
                                    self._temps[start_h:end], start_h % 24,
                                    self._heating[start_h:end])

    def backup_kwh(self, start_h: int, hours: int, extra: Sequence[float] = ()) -> float:
        """Backup heat the house would need over the coming hours."""
        return self.ahead_kwh(start_h, hours, extra)[0]

    def _outlook(self, job: QueuedJob, start_h: int) -> tuple[float, float]:
        """Backup heat avoided and dumped heat added over the job's run if it starts
        here at `start_h`. A start in the future is looked at from the present hour,
        the state the heat model is in."""
        key = (start_h, job.key)
        if key not in self._scores:
            first = min(self.now_h, start_h)
            base = (first, start_h, job.hours)
            if base not in self._scores:
                self._scores[base] = self.ahead_kwh(first, start_h - first + job.hours)
            backup, dumped = self._scores[base]
            with_job = self.ahead_kwh(first, start_h - first + job.hours,
                                      [0.0] * (start_h - first) + job.load())
            self._scores[key] = (backup - with_job[0], with_job[1] - dumped)
        return self._scores[key]

    def saving_kwh(self, job: QueuedJob, start_h: int) -> float:
        """Backup heat avoided over the job's run if it starts here at `start_h`."""
        return self._outlook(job, start_h)[0]

    def dump_added_kwh(self, job: QueuedJob, start_h: int) -> float:
        """Server heat the house could not absorb because of this job. Looks past the
        job's own run to the end of everything booked here: a house the job leaves
        warmer can make the jobs after it overflow."""
        key = ("dump", start_h, job.key)
        if key not in self._scores:
            first = min(self.now_h, start_h)
            hours = max(start_h + job.hours, self.booked_until_h) - first
            base = ("dump", first, hours)
            if base not in self._scores:
                self._scores[base] = self.ahead_kwh(first, hours)[1]
            extra = [0.0] * (start_h - first) + job.load()
            extra += [0.0] * (hours - len(extra))
            self._scores[key] = self.ahead_kwh(first, hours, extra)[1] - self._scores[base]
        return self._scores[key]

    def step(self, t: int):
        """Run hour `t` for real; returns the HeatDelivery."""
        self._scores.clear()
        self.now_h = t + 1
        return self.model.deliver(self.heat_kwh(self.load[t]), self.temps_c[t], t % 24,
                                  self.heating[t])


# ---------------------------------------------------------------------- Schedulers


class Scheduler(Protocol):
    def order(self, queue: Sequence[QueuedJob], now_h: int) -> list[QueuedJob]: ...

    def choose(self, job: QueuedJob, now_h: int,
               sites: Sequence[Site]) -> tuple | None:
        """(site, server) to start the job now, (site, server, start hour) to book it
        for later, or None to leave it in the queue."""


@dataclass
class HeatSavingScheduler:
    """Highest priority first; each job goes to the house where its heat avoids the
    most backup heating over its run.

    With `min_useful_share` above zero a job is also held while even the best
    house would use less than that share of its heat. Once it has less than
    `start_margin_h` of slack left it starts wherever it fits; the margin gives
    it time to find a free server before the deadline is lost. Holding is off
    by default: in the reference runs it saved little heat and cost deadlines.
    """

    min_useful_share: float = 0.0
    start_margin_h: float = 12.0

    def order(self, queue: Sequence[QueuedJob], now_h: int) -> list[QueuedJob]:
        return sorted(queue, key=lambda job: (-job.priority(now_h), job.arrival_h))

    def choose(self, job: QueuedJob, now_h: int,
               sites: Sequence[Site]) -> tuple[Site, int] | None:
        best = None
        for site in sites:
            server = site.server_with_room(job, now_h)
            if server is None:
                continue
            saved = site.saving_kwh(job, now_h)
            if best is None or saved > best[0] + EPS:
                best = (saved, site, server)
        if best is None:
            return None
        saved, site, server = best
        useful = saved >= self.min_useful_share * site.heat_kwh(job.gpu_hours) - EPS
        return (site, server) if useful or job.must_start(now_h, self.start_margin_h) else None


@dataclass
class FirstFreeScheduler:
    """Baseline: first come first served, first house in the list with room, no waiting
    for heat."""

    def order(self, queue: Sequence[QueuedJob], now_h: int) -> list[QueuedJob]:
        return sorted(queue, key=lambda job: job.arrival_h)

    def choose(self, job: QueuedJob, now_h: int,
               sites: Sequence[Site]) -> tuple[Site, int] | None:
        for site in sites:
            server = site.server_with_room(job, now_h)
            if server is not None:
                return site, server
        return None


@dataclass
class TierScheduler:
    """Places jobs by the tier they were bought in.

    Each hour the firm tiers go first, in the order of TIERS, then spot; within
    a tier the job that has waited longest goes first.

    A firm job goes to the house where its heat avoids the most backup heating
    over its run, which is the house whose minimum heat is not yet met; between
    equal houses, to the one where it adds the least dumped heat. If no server
    has room, spot jobs are pushed off the server that needs the fewest pushed
    off; they go back to the queue with the hours they still have to run. A
    capacity block is booked `lead_h` ahead instead and pushes nothing off. A
    firm job that still finds no room waits.

    A spot job starts only in a house that can absorb all its heat over its
    run (at most `overflow_kwh` more dumped), the one with the largest saving.
    Otherwise it waits; it has no deadline.
    """

    overflow_kwh: float = 1e-6
    evicted: list[QueuedJob] = field(default_factory=list)   # pushed off this hour; FleetRun re-queues them
    _no_house: tuple[int, set] = field(default=(-1, set()), repr=False)

    def order(self, queue: Sequence[QueuedJob], now_h: int) -> list[QueuedJob]:
        rank = {key: i for i, key in enumerate(TIERS)}
        return sorted(queue, key=lambda job: (rank[job.tier], job.waiting_since_h, job.number))

    def choose(self, job: QueuedJob, now_h: int, sites: Sequence[Site]) -> tuple | None:
        tier = TIERS[job.tier]
        start_h = now_h + tier.lead_h
        # Spot goes last in the hour, and every start only fills the houses further. A
        # spot job that found no house therefore rules out every later one like it.
        if self._no_house[0] != now_h:
            self._no_house = (now_h, set())
        if not tier.firm and job.key in self._no_house[1]:
            return None
        best = None
        for site in sites:
            server = site.server_with_room(job, start_h)
            if server is None:
                continue
            saved, dumped = site.saving_kwh(job, start_h), site.dump_added_kwh(job, start_h)
            if not tier.firm and dumped > self.overflow_kwh:
                continue
            if (best is None or saved > best[0] + EPS
                    or (saved > best[0] - EPS and dumped < best[1] - EPS)):
                best = (saved, dumped, site, server)
        if best is not None:
            return best[2], best[3], start_h
        if not tier.firm:
            self._no_house[1].add(job.key)
        if not tier.evicts:
            return None
        freed = None
        for site in sites:
            option = site.spot_in_the_way(job, now_h)
            if option is not None and (freed is None or len(option[1]) < len(freed[2])):
                freed = (site, option[0], option[1])
        if freed is None:
            return None
        site, server, victims = freed
        for victim in victims:
            site.evict(victim, now_h)
        self.evicted.extend(victims)
        return site, server, now_h


# ------------------------------------------------------------------------ FleetRun


@dataclass
class TierTotals:
    """Running totals of one tier (or one job class) over a run."""

    arrived: int = 0
    started: int = 0             # jobs that have started at least once
    wait_h: float = 0.0          # hours in the queue before each start, restarts included
    evictions: int = 0
    gpu_hours: float = 0.0       # GPU-hours of work done
    billed_gpu_hours: float = 0.0   # GPU-hours the clients pay for


@dataclass
class SiteRecord:
    """Hourly results of one site, in kWh except `gpu_hours`."""

    gpu_hours: list[float] = field(default_factory=list)
    absorbed: list[float] = field(default_factory=list)     # server heat the house took
    dumped: list[float] = field(default_factory=list)
    backup: list[float] = field(default_factory=list)
    must_run: list[float] = field(default_factory=list)     # least heat the house had to get
    space_max: list[float] = field(default_factory=list)    # most the rooms could absorb
    water_max: list[float] = field(default_factory=list)    # most the hot water tank could absorb
    to_space: list[float] = field(default_factory=list)     # server heat into the rooms
    to_water: list[float] = field(default_factory=list)     # server heat into the tank
    indoor_c: list[float] = field(default_factory=list)     # after the hour
    tank_kwh: list[float] = field(default_factory=list)     # after the hour


@dataclass
class FleetRun:
    """Each hour: jobs arrive, hopeless ones are dropped, the scheduler places what it
    wants to start now, and every house settles its heat.

    Without a scheduler nothing runs, which gives each house's backup heat with
    no servers: the reference for the heat savings.
    """

    sites: Sequence[Site]
    scheduler: Scheduler | None = None
    arrivals: Sequence[Sequence[QueuedJob]] = ()
    queue: list[QueuedJob] = field(default_factory=list)
    records: dict[str, SiteRecord] = field(default_factory=dict)
    started: list[int] = field(default_factory=list)        # jobs started in each hour
    missed: list[int] = field(default_factory=list)         # jobs dropped in each hour
    wait_h: list[float] = field(default_factory=list)       # waiting of the jobs started
    tiers: dict[str, TierTotals] = field(default_factory=lambda: defaultdict(TierTotals))
    t: int = 0

    def __post_init__(self) -> None:
        self.records = {site.name: SiteRecord() for site in self.sites}

    def step(self) -> None:
        t = self.t
        if t < len(self.arrivals):
            self.queue.extend(self.arrivals[t])
            for job in self.arrivals[t]:
                self.tiers[job.tier or job.job_class].arrived += 1
        waiting = []
        for job in self.queue:
            if job.missed(t):
                job.missed_h = t
            else:
                waiting.append(job)
        self.missed.append(len(self.queue) - len(waiting))
        started, waited, held = 0, 0.0, []
        if self.scheduler is not None:
            for job in self.scheduler.order(waiting, t):
                choice = self.scheduler.choose(job, t, self.sites)
                if choice is None:
                    held.append(job)
                    continue
                site, server, *later = choice
                site.place(job, server, later[0] if later else t)
                totals = self.tiers[job.tier or job.job_class]
                totals.wait_h += t - job.waiting_since_h
                if not job.evictions:               # a restart is not another job started
                    started += 1
                    waited += t - job.arrival_h
                    totals.started += 1
            for job in getattr(self.scheduler, "evicted", ()):
                self.tiers[job.tier].evictions += 1
                held.append(job)
            if hasattr(self.scheduler, "evicted"):
                self.scheduler.evicted.clear()
        self.queue = held
        self.started.append(started)
        self.wait_h.append(waited)
        for site in self.sites:
            for job in site.running(t):
                totals, share = self.tiers[job.tier or job.job_class], job.share_of_hour(t)
                totals.gpu_hours += job.gpus * job.busy_share * share
                totals.billed_gpu_hours += job.billed_gpus * share
        for site in self.sites:
            d, record = site.step(t), self.records[site.name]
            record.gpu_hours.append(site.load[t])
            record.absorbed.append(d.used_kwh)
            record.dumped.append(d.dumped_kwh)
            record.backup.append(d.backup_kwh)
            record.must_run.append(d.range.must_run_kwh)
            record.space_max.append(d.range.space_max_kwh)
            record.water_max.append(d.range.water_max_kwh)
            record.to_space.append(d.to_space_kwh)
            record.to_water.append(d.to_water_kwh)
            record.indoor_c.append(d.indoor_c)
            record.tank_kwh.append(d.tank_level_kwh)
        self.t += 1

    def run(self, hours: int) -> "FleetRun":
        for _ in range(hours):
            self.step()
        return self
