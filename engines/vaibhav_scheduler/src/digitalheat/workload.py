"""Workload side: what compute work exists and when it must be done.

Entities
    JobType         template for one kind of job
    Measurement     a timed run on a real machine that backs a JobType's numbers
    ArrivalProcess  how many jobs of a type arrive, and when
    Task            one job instance with its lifecycle
    Checkpoint      saved progress of a paused task
    Demand          catalogue + arrivals + backlog; the scheduler reads from it
    TraceJob        a real task from a cluster trace: GPUs requested and run time
    Placement       a trace job put on a server at a given hour

All times are model hours since the start of the run.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

EPS = 1e-9


class InvalidTransition(RuntimeError):
    """A task was asked to change state in a way its lifecycle does not allow."""


# --------------------------------------------------------------------------- JobType


@dataclass(frozen=True)
class JobType:
    id: str
    name: str
    runtime_h: float                 # on the reference server
    power_kw: float                  # draw while running
    interruptible: bool              # False: a pause loses all progress
    price_chf_per_server_hour: float
    source: str                      # where every number above comes from
    relative_deadline_h: float | None = None   # None = no deadline
    checkpoint_overhead_h: float = 0.0         # extra work caused by one pause/resume
    region_constraint: str | None = None       # e.g. "CH-NW": data must stay there
    priority: int = 0                          # higher runs first
    measured: bool = False                     # True if timed on our own machine

    def __post_init__(self) -> None:
        if self.runtime_h <= 0 or self.power_kw <= 0:
            raise ValueError(f"{self.id}: runtime_h and power_kw must be positive")
        if self.relative_deadline_h is not None and self.relative_deadline_h < self.runtime_h:
            raise ValueError(f"{self.id}: deadline is shorter than the runtime")
        if self.checkpoint_overhead_h < 0:
            raise ValueError(f"{self.id}: checkpoint_overhead_h cannot be negative")
        if not self.source.strip():
            raise ValueError(f"{self.id}: name the source of the job type's numbers")

    @property
    def energy_kwh(self) -> float:
        """Electricity per job."""
        return self.runtime_h * self.power_kw


# ----------------------------------------------------------------------- Measurement


@dataclass(frozen=True)
class Measurement:
    """A real run that backs a JobType (the brief asks for at least one)."""

    job_type_id: str
    machine: str                 # e.g. "MacBook Air M2, 8 cores"
    runtime_s: float             # wall-clock time of the whole run
    avg_power_w: float           # average draw during the run
    method: str                  # "software_meter" or "runtime_x_power"
    measured_on: str             # ISO date
    jobs_in_run: int = 1
    notes: str = ""

    def __post_init__(self) -> None:
        if self.method not in ("software_meter", "runtime_x_power"):
            raise ValueError("method must be 'software_meter' or 'runtime_x_power'")
        if self.runtime_s <= 0 or self.avg_power_w <= 0 or self.jobs_in_run < 1:
            raise ValueError("runtime_s, avg_power_w and jobs_in_run must be positive")

    @property
    def energy_kwh_per_job(self) -> float:
        return self.runtime_s / 3600 * self.avg_power_w / 1000 / self.jobs_in_run

    def reference_runtime_h(self, reference_power_kw: float = 1.0,
                            efficiency_ratio: float = 1.0) -> float:
        """Hours one job occupies the reference server.

        Scaling keeps energy per job constant. `efficiency_ratio` is the
        reference server's energy per job divided by the measured machine's
        (1.0 = equally efficient); state it as an assumption if you change it.
        """
        return self.energy_kwh_per_job * efficiency_ratio / reference_power_kw


# -------------------------------------------------------------------- ArrivalProcess


class ArrivalMode(str, Enum):
    UNLIMITED = "unlimited"      # there is always another job waiting
    FIXED_DAILY = "fixed_daily"  # jobs_per_day spread by hourly_shape, deterministic
    POISSON = "poisson"          # same mean rate, random counts (seeded)
    TRACE = "trace"              # explicit arrival steps, e.g. sampled from a cluster trace


@dataclass(frozen=True)
class ArrivalProcess:
    job_type_id: str
    mode: ArrivalMode
    source: str                                  # where the arrival numbers come from
    jobs_per_day: float | None = None            # FIXED_DAILY, POISSON
    hourly_shape: tuple[float, ...] | None = None  # 24 weights summing to 1; None = even
    trace_steps: tuple[int, ...] | None = None   # TRACE: step index of each arrival
    buffer: int = 8                              # UNLIMITED: waiting jobs kept available
    seed: int = 0

    def __post_init__(self) -> None:
        if self.mode in (ArrivalMode.FIXED_DAILY, ArrivalMode.POISSON):
            if self.jobs_per_day is None or self.jobs_per_day < 0:
                raise ValueError(f"{self.job_type_id}: {self.mode.value} needs jobs_per_day")
        if self.mode is ArrivalMode.TRACE and self.trace_steps is None:
            raise ValueError(f"{self.job_type_id}: trace mode needs trace_steps")
        if self.hourly_shape is not None:
            if len(self.hourly_shape) != 24 or abs(sum(self.hourly_shape) - 1) > 1e-6:
                raise ValueError(f"{self.job_type_id}: hourly_shape needs 24 weights summing to 1")
        if not self.source.strip():
            raise ValueError(f"{self.job_type_id}: name the source of the arrival numbers")

    def hourly_rate(self, hour_of_day: int) -> float:
        """Expected arrivals in one hour starting at `hour_of_day`."""
        weight = self.hourly_shape[hour_of_day] if self.hourly_shape else 1 / 24
        return (self.jobs_per_day or 0.0) * weight


# ------------------------------------------------------------------------------ Task


class TaskStatus(str, Enum):
    PENDING = "pending"   # waiting, no progress saved
    RUNNING = "running"
    PAUSED = "paused"     # interrupted, progress saved in a checkpoint
    DONE = "done"
    MISSED = "missed"     # deadline can no longer be met; dropped


@dataclass(frozen=True)
class Checkpoint:
    task_id: str
    work_done_h: float
    taken_at_h: float
    server_id: str
    location: str | None = None   # where a real job's state lives (path, URI, pid)


@dataclass
class Task:
    """One job instance. Job-type fields are copied in at creation so a task
    stays valid if the catalogue changes later."""

    id: str
    job_type_id: str
    arrival_h: float
    work_total_h: float
    power_kw: float
    interruptible: bool
    price_chf_per_server_hour: float
    deadline_h: float | None = None
    checkpoint_overhead_h: float = 0.0
    region_constraint: str | None = None
    priority: int = 0
    # --- state ---
    status: TaskStatus = TaskStatus.PENDING
    work_done_h: float = 0.0
    overhead_h: float = 0.0            # extra work added by interruptions
    server_id: str | None = None
    last_server_id: str | None = None
    checkpoint: Checkpoint | None = None
    started_h: float | None = None
    finished_h: float | None = None
    # --- counters ---
    run_hours: float = 0.0             # server-hours consumed, restarts included
    interruptions: int = 0
    migrations: int = 0                # resumed on a different server
    restarts: int = 0                  # progress lost (non-interruptible job paused)

    @property
    def remaining_h(self) -> float:
        return max(0.0, self.work_total_h + self.overhead_h - self.work_done_h)

    @property
    def is_open(self) -> bool:
        return self.status not in (TaskStatus.DONE, TaskStatus.MISSED)

    def slack_h(self, now_h: float) -> float:
        """Hours the task can still wait and finish on time (inf = no deadline)."""
        if self.deadline_h is None:
            return math.inf
        return self.deadline_h - now_h - self.remaining_h

    def allowed_in(self, region: str | None) -> bool:
        return self.region_constraint is None or self.region_constraint == region

    # --- lifecycle ---

    def _require(self, *allowed: TaskStatus) -> None:
        if self.status not in allowed:
            raise InvalidTransition(f"task {self.id} is {self.status.value}")

    def start(self, server_id: str, now_h: float) -> None:
        self._require(TaskStatus.PENDING)
        self.status = TaskStatus.RUNNING
        self.server_id = server_id
        if self.started_h is None:
            self.started_h = now_h

    def interrupt(self, now_h: float, checkpoint: Checkpoint | None = None) -> None:
        """Stop running. Interruptible tasks keep their progress; others restart."""
        self._require(TaskStatus.RUNNING)
        self.interruptions += 1
        if self.interruptible:
            self.checkpoint = checkpoint or Checkpoint(
                self.id, self.work_done_h, now_h, self.server_id or ""
            )
            self.overhead_h += self.checkpoint_overhead_h   # cost of saving and reloading
            self.status = TaskStatus.PAUSED
        else:
            self.work_done_h = 0.0
            self.overhead_h = 0.0
            self.checkpoint = None
            self.restarts += 1
            self.status = TaskStatus.PENDING
        self.last_server_id, self.server_id = self.server_id, None

    def resume(self, server_id: str, now_h: float) -> None:
        self._require(TaskStatus.PAUSED)
        if self.last_server_id is not None and server_id != self.last_server_id:
            self.migrations += 1
        self.status = TaskStatus.RUNNING
        self.server_id = server_id

    def advance(self, hours: float) -> float:
        """Do up to `hours` of work; returns the hours actually used."""
        self._require(TaskStatus.RUNNING)
        used = min(hours, self.remaining_h)
        self.work_done_h += used
        self.run_hours += used
        return used

    def complete(self, now_h: float) -> None:
        self._require(TaskStatus.RUNNING)
        self.status = TaskStatus.DONE
        self.finished_h = now_h
        self.last_server_id, self.server_id = self.server_id, None

    def miss(self, now_h: float) -> None:
        self._require(TaskStatus.PENDING, TaskStatus.PAUSED, TaskStatus.RUNNING)
        self.status = TaskStatus.MISSED
        self.finished_h = now_h
        self.last_server_id, self.server_id = self.server_id or self.last_server_id, None


# ---------------------------------------------------------------------------- Demand


@dataclass(frozen=True)
class TypeStats:
    job_type_id: str
    created: int
    started: int
    done: int
    missed: int
    open: int
    interruptions: int
    migrations: int

    @property
    def missed_pct(self) -> float:
        closed = self.done + self.missed
        return 100.0 * self.missed / closed if closed else 0.0


class Demand:
    """What work exists at a given hour.

    Owns the job catalogue, the arrival processes and every task. The
    scheduler only reads from it; state changes go through the Dispatcher.
    """

    def __init__(self, catalogue: Iterable[JobType], arrivals: Iterable[ArrivalProcess],
                 dt_h: float = 1.0) -> None:
        self.catalogue: dict[str, JobType] = {jt.id: jt for jt in catalogue}
        self.arrivals: list[ArrivalProcess] = list(arrivals)
        self.dt_h = dt_h
        self.tasks: dict[str, Task] = {}          # every task ever created
        self._open: dict[str, Task] = {}          # not yet DONE or MISSED
        self._serial: Counter[str] = Counter()
        self._carry: dict[str, float] = {}
        self._rng: dict[str, random.Random] = {}
        self._trace: dict[str, Counter[int]] = {}
        for proc in self.arrivals:
            if proc.job_type_id not in self.catalogue:
                raise KeyError(f"arrival process refers to unknown job type {proc.job_type_id!r}")
            self._carry[proc.job_type_id] = 0.0
            self._rng[proc.job_type_id] = random.Random(proc.seed)
            self._trace[proc.job_type_id] = Counter(proc.trace_steps or ())

    # --- arrivals ---

    def step(self, now_h: float, hour_of_day: int) -> list[Task]:
        """Create the tasks that arrive in the step starting at `now_h`."""
        self._prune()
        created: list[Task] = []
        for proc in self.arrivals:
            job_type = self.catalogue[proc.job_type_id]
            for _ in range(self._arrivals(proc, now_h, hour_of_day)):
                created.append(self._create(job_type, now_h))
        return created

    def _arrivals(self, proc: ArrivalProcess, now_h: float, hour_of_day: int) -> int:
        key = proc.job_type_id
        if proc.mode is ArrivalMode.UNLIMITED:
            waiting = sum(
                1 for task in self._open.values()
                if task.job_type_id == key and task.status is not TaskStatus.RUNNING
            )
            return max(0, proc.buffer - waiting)
        if proc.mode is ArrivalMode.TRACE:
            return self._trace[key][round(now_h / self.dt_h)]
        mean = proc.hourly_rate(hour_of_day) * self.dt_h
        if proc.mode is ArrivalMode.POISSON:
            return _poisson(self._rng[key], mean)
        self._carry[key] += mean                     # FIXED_DAILY: carry the fraction
        count = int(self._carry[key] + EPS)
        self._carry[key] -= count
        return count

    def _create(self, job_type: JobType, now_h: float) -> Task:
        self._serial[job_type.id] += 1
        deadline = None
        if job_type.relative_deadline_h is not None:
            deadline = now_h + job_type.relative_deadline_h
        task = Task(
            id=f"{job_type.id}-{self._serial[job_type.id]:06d}",
            job_type_id=job_type.id,
            arrival_h=now_h,
            work_total_h=job_type.runtime_h,
            power_kw=job_type.power_kw,
            interruptible=job_type.interruptible,
            price_chf_per_server_hour=job_type.price_chf_per_server_hour,
            deadline_h=deadline,
            checkpoint_overhead_h=job_type.checkpoint_overhead_h,
            region_constraint=job_type.region_constraint,
            priority=job_type.priority,
        )
        self.tasks[task.id] = task
        self._open[task.id] = task
        return task

    def _prune(self) -> None:
        for task_id in [tid for tid, task in self._open.items() if not task.is_open]:
            del self._open[task_id]

    # --- views ---

    def get(self, task_id: str) -> Task:
        return self.tasks[task_id]

    def active(self) -> list[Task]:
        """Tasks that are PENDING, PAUSED or RUNNING."""
        return [task for task in self._open.values() if task.is_open]

    def backlog(self) -> list[Task]:
        """Tasks waiting for a server."""
        return [task for task in self.active() if task.status is not TaskStatus.RUNNING]

    def running(self) -> list[Task]:
        return [task for task in self.active() if task.status is TaskStatus.RUNNING]

    def eligible(self, now_h: float, region: str | None = None) -> list[Task]:
        """Waiting tasks allowed in `region`, most urgent first."""
        tasks = [task for task in self.backlog() if task.allowed_in(region)]
        return sorted(tasks, key=lambda task: urgency(task, now_h))

    def overdue(self, now_h: float) -> list[Task]:
        """Open tasks that cannot meet their deadline even if run without a break."""
        return [task for task in self.active() if task.slack_h(now_h) < -EPS]

    def stats(self) -> dict[str, TypeStats]:
        out: dict[str, TypeStats] = {}
        for key in self.catalogue:
            tasks = [task for task in self.tasks.values() if task.job_type_id == key]
            out[key] = TypeStats(
                job_type_id=key,
                created=len(tasks),
                started=sum(1 for task in tasks if task.started_h is not None),
                done=sum(1 for task in tasks if task.status is TaskStatus.DONE),
                missed=sum(1 for task in tasks if task.status is TaskStatus.MISSED),
                open=sum(1 for task in tasks if task.is_open),
                interruptions=sum(task.interruptions for task in tasks),
                migrations=sum(task.migrations for task in tasks),
            )
        return out

    def missed_deadline_pct(self) -> float:
        """Missed tasks as a share of finished tasks that had a deadline."""
        closed = [task for task in self.tasks.values()
                  if task.deadline_h is not None and not task.is_open]
        if not closed:
            return 0.0
        missed = sum(1 for task in closed if task.status is TaskStatus.MISSED)
        return 100.0 * missed / len(closed)


def urgency(task: Task, now_h: float) -> tuple:
    """Sort key: higher priority first, then least slack, then oldest."""
    return (-task.priority, task.slack_h(now_h), task.arrival_h, task.id)


def _poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    if mean > 30:
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    limit, count, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


# ------------------------------------------------------------- TraceJob, Placement


@dataclass(frozen=True)
class TraceJob:
    """A real task from a cluster trace: the GPUs it asked for and how long it ran."""

    id: str
    gpus: float                  # GPUs requested, all instances together
    duration_h: float
    task_name: str               # framework role recorded in the trace
    source: str                  # trace and job the numbers come from
    group: str = ""              # size group it was drawn from
    duration_rank: str = ""      # e.g. "median" of that group
    cpu_cores: float = 0.0
    mem_gib: float = 0.0

    def __post_init__(self) -> None:
        if self.gpus <= 0 or self.duration_h <= 0:
            raise ValueError(f"{self.id}: GPUs and duration must be positive")

    @property
    def gpu_hours(self) -> float:
        return self.gpus * self.duration_h


@dataclass(frozen=True)
class Placement:
    """A trace job put on a server.

    It runs once from `start_h`, or, with `keep_running_h`, copies of it run
    back to back for that long.
    """

    job: TraceJob
    start_h: float
    keep_running_h: float = 0.0

    @property
    def end_h(self) -> float:
        return self.start_h + (self.keep_running_h or self.job.duration_h)

    def gpu_hours_in(self, from_h: float, to_h: float) -> float:
        """GPU-hours this placement asks for between two times."""
        overlap = min(self.end_h, to_h) - max(self.start_h, from_h)
        return self.job.gpus * max(0.0, overlap)


def gpu_hours_by_step(placements: Sequence[Placement], steps: int,
                      dt_h: float = 1.0) -> list[float]:
    """GPU-hours requested in each step by everything placed on one server.

    Not capped: a value above the server's GPUs x dt_h means it is overbooked.
    """
    return [sum(p.gpu_hours_in(t * dt_h, (t + 1) * dt_h) for p in placements)
            for t in range(steps)]


def load_trace_jobs(path: str | Path) -> list[TraceJob]:
    """Read the file written by scripts/fetch_workloads.py (data/alibaba_gpu_jobs.json)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        TraceJob(
            id=row["id"], gpus=row["gpus"], duration_h=row["duration_s"] / 3600,
            task_name=row["task_name"], group=row["group"], duration_rank=row["duration_rank"],
            cpu_cores=row["cpu_cores"], mem_gib=row["mem_gib"],
            source=f"{data['source']}, job {row['trace_job']}",
        )
        for row in data["jobs"]
    ]
