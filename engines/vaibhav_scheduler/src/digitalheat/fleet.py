"""A fleet of houses with servers, a queue of jobs, and the policy that places them.

Entities
    Site                 one house: its heat model, weather, servers and the GPUs already booked
    QueuedJob            a trace job with an arrival hour, a deadline and a rising priority
    HeatSavingScheduler  sends each job where its heat replaces the most backup heating
    FirstFreeScheduler   baseline: first come first served, first house with room
    FleetRun             the hourly loop and its records

Built on the heat range model (HouseHeatModel), the measured servers
(PowerProfile) and the trace jobs (TraceJob). A job that has started runs to
its end on the server it was given. Steps are one hour.
"""

from __future__ import annotations

import math
import random
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

    @property
    def gpus(self) -> float:
        return self.job.gpus

    @property
    def hours(self) -> int:
        """Whole hours the GPUs stay booked."""
        return math.ceil(self.run_h - EPS)

    @property
    def gpu_hours(self) -> float:
        return self.gpus * self.run_h

    @property
    def key(self) -> tuple[str, float]:
        """Jobs with the same key put the same load on a server."""
        return self.job.id, self.run_h

    def load(self) -> list[float]:
        """GPU-hours in each hour of the run (the last hour may be partial)."""
        return [self.gpus * min(1.0, self.run_h - i) for i in range(self.hours)]

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
               gpu_hours_per_hour: float, seed: int = 0,
               batch_h: float = 4.0) -> list[list[QueuedJob]]:
    """Random arrivals for every hour of a run.

    Each arrival is one of `jobs`, drawn with the given weights. A job shorter
    than an hour arrives as a batch of copies run back to back for `batch_h`
    hours. The rate is set so the arrivals ask for `gpu_hours_per_hour` on
    average. Class, and with it deadline and starting priority, is drawn from
    JOB_CLASSES.
    """
    rng = random.Random(seed)
    run_h = {j.id: (j.duration_h if j.duration_h >= 1 else batch_h) for j in jobs}
    pick = [weights[j.id] for j in jobs]
    mean_gpu_hours = sum(w * j.gpus * run_h[j.id] for w, j in zip(pick, jobs)) / sum(pick)
    rate = gpu_hours_per_hour / mean_gpu_hours
    classes = list(JOB_CLASSES)
    shares = [JOB_CLASSES[c][0] for c in classes]
    stream, number = [], 0
    for hour in range(hours):
        arrivals = []
        for job in rng.choices(jobs, pick, k=_poisson(rng, rate)):
            job_class = rng.choices(classes, shares)[0]
            _, factor, priority = JOB_CLASSES[job_class]
            number += 1
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
        limit = self.profile.gpus - job.gpus + EPS
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
            booked[start_h + i] += job.gpus
            self.load[start_h + i] += gpu_hours
        job.started_h, job.site, job.server = start_h, self.name, server
        self.jobs.append(job)
        self._scores.clear()

    def running(self, t: int) -> list[QueuedJob]:
        """The jobs whose GPUs are booked in hour `t`. Jobs that ended before `t` are
        forgotten, so ask for hours in order."""
        self.jobs = [job for job in self.jobs if job.started_h + job.hours > t]
        return [job for job in self.jobs if job.started_h <= t]

    def backup_kwh(self, start_h: int, hours: int, extra: Sequence[float] = ()) -> float:
        """Backup heat the house would need over the coming hours with what is booked,
        plus `extra` GPU-hours per hour. Uses the known weather as the forecast."""
        end = start_h + hours
        booked = self.load[start_h:end]
        if extra:
            booked = [b + x for b, x in zip(booked, extra)]
        per_gpu_hour = self.profile.kw_per_gpu * self.heat_recovery_fraction
        return self.model.backup_ahead_kwh([per_gpu_hour * g for g in booked],
                                           self._temps[start_h:end], start_h % 24,
                                           self._heating[start_h:end])

    def saving_kwh(self, job: QueuedJob, start_h: int) -> float:
        """Backup heat avoided over the job's run if it starts here now."""
        key = (start_h, job.key)
        if key not in self._scores:
            base = (start_h, job.hours)
            if base not in self._scores:
                self._scores[base] = self.backup_kwh(start_h, job.hours)
            self._scores[key] = self._scores[base] - self.backup_kwh(start_h, job.hours,
                                                                     job.load())
        return self._scores[key]

    def step(self, t: int):
        """Run hour `t` for real; returns the HeatDelivery."""
        self._scores.clear()
        return self.model.deliver(self.heat_kwh(self.load[t]), self.temps_c[t], t % 24,
                                  self.heating[t])


# ---------------------------------------------------------------------- Schedulers


class Scheduler(Protocol):
    def order(self, queue: Sequence[QueuedJob], now_h: int) -> list[QueuedJob]: ...

    def choose(self, job: QueuedJob, now_h: int,
               sites: Sequence[Site]) -> tuple[Site, int] | None: ...


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


# ------------------------------------------------------------------------ FleetRun


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
    t: int = 0

    def __post_init__(self) -> None:
        self.records = {site.name: SiteRecord() for site in self.sites}

    def step(self) -> None:
        t = self.t
        if t < len(self.arrivals):
            self.queue.extend(self.arrivals[t])
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
                choice[0].place(job, choice[1], t)
                started += 1
                waited += t - job.arrival_h
        self.queue = held
        self.started.append(started)
        self.wait_h.append(waited)
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
