"""Clock: the time base shared by simulation and live runs.

Everything is indexed by an integer step `t`. A simulated year is 8,760 steps
of one hour. A live demo uses the same steps but calls `wait()` between them to
let `seconds_per_step` of wall-clock time pass, so the scheduler code does not
change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Clock:
    start: datetime                       # local time of step 0
    dt_h: float = 1.0                     # model hours per step
    t: int = 0                            # current step
    seconds_per_step: float | None = None  # None = simulate as fast as possible

    @property
    def now_h(self) -> float:
        """Model hours elapsed since `start`."""
        return self.t * self.dt_h

    def at(self, t: int | None = None) -> datetime:
        step = self.t if t is None else t
        return self.start + timedelta(hours=step * self.dt_h)

    def hour_of_day(self, t: int | None = None) -> int:
        return self.at(t).hour

    def day_index(self, t: int | None = None) -> int:
        """Whole days since `start` (0 = first day)."""
        return (self.at(t).date() - self.start.date()).days

    def index_of(self, when: datetime) -> int:
        return int((when - self.start).total_seconds() // (3600 * self.dt_h))

    def advance(self) -> int:
        self.t += 1
        return self.t

    def wait(self) -> None:
        """Let one step of wall-clock time pass (live runs only)."""
        if self.seconds_per_step:
            time.sleep(self.seconds_per_step)
