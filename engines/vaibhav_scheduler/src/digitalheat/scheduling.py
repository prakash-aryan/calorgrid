"""Scheduling: deciding which task runs on which server, step by step.

Entities
    Scheduler               the interface every policy implements
    HeatFollowingScheduler  baseline policy: run compute only where its heat is useful

A scheduler is a pure decision function. It reads heat signals, the backlog
and server state, and returns Actions; it changes nothing itself. Swap in a
different policy by implementing `decide`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Protocol

from .compute import Server, ServerRegistry, ServerState
from .execution import Action, ActionType
from .heat import HeatSignal
from .workload import Demand, Task, TaskStatus, urgency

EPS = 1e-9


class Scheduler(Protocol):
    def decide(self, now_h: float, signals: Mapping[str, HeatSignal], demand: Demand,
               registry: ServerRegistry) -> list[Action]: ...


@dataclass
class HeatFollowingScheduler:
    """Greedy heat-following policy.

    Each step, per house (neediest first):
      1. Keep running tasks while their heat can be used or stored. Tasks that
         cannot be paused, or would miss their deadline if paused, always stay.
      2. Fill the remaining heat budget with waiting tasks, most urgent first.
         Running tasks displaced from another house are candidates too, which
         is how work migrates to where heat is needed.
      3. Tasks about to miss their deadline run even if their heat is dumped
         (switch off with allow_dump_for_deadlines=False).

    Known limits: no price or weather look-ahead beyond a rough check for
    non-interruptible jobs, and in summer a server cycles on and off as the
    tank fills. `start_margin_kwh` adds hysteresis against that.
    """

    dt_h: float = 1.0
    start_margin_kwh: float = 0.0          # extra absorbable heat required to start a job
    allow_dump_for_deadlines: bool = True

    def decide(self, now_h: float, signals: Mapping[str, HeatSignal], demand: Demand,
               registry: ServerRegistry) -> list[Action]:
        plan: dict[str, str] = {}      # task id -> server it should run on this step
        why: dict[str, str] = {}
        houses: list[list] = []        # [signal, remaining heat budget, free servers]

        # 1. Which running tasks stay where they are?
        for sig in sorted(signals.values(), key=lambda s: (-s.absorbable_kwh, s.house_id)):
            budget = sig.absorbable_kwh
            servers = [s for s in registry.by_house(sig.house_id)
                       if s.state is not ServerState.FAILED]
            running = [(demand.get(s.task_id), s) for s in servers
                       if s.state is ServerState.RUNNING]
            running.sort(key=lambda pair: (not self._must_keep(pair[0], now_h),
                                           urgency(pair[0], now_h)))
            for task, server in running:
                heat = self._heat(server, task)
                if self._must_keep(task, now_h):
                    why[task.id] = "cannot be paused without losing work or its deadline"
                elif budget + EPS >= heat:
                    why[task.id] = f"{sig.house_id} can use the heat"
                else:
                    why[task.id] = f"{sig.house_id} cannot use more heat"
                    continue
                plan[task.id] = server.id
                budget -= heat
            taken = set(plan.values())
            houses.append([sig, budget, [s for s in servers if s.id not in taken]])

        waiting = sorted((task for task in demand.active() if task.id not in plan),
                         key=lambda task: urgency(task, now_h))

        # 2. Fill the heat budget with waiting work.
        for entry in houses:
            sig, budget, free = entry
            for server in list(free):
                task = self._pick(waiting, server, sig, budget)
                if task is None:
                    continue
                plan[task.id] = server.id
                why[task.id] = (f"{sig.house_id} can use {sig.absorbable_kwh:.1f} kWh "
                                f"(demand {sig.demand_kwh:.1f}, tank room {sig.tank_headroom_kwh:.1f})")
                budget -= self._heat(server, task)
                waiting.remove(task)
                free.remove(server)
            entry[1] = budget

        # 3. Deadlines beat heat: run critical tasks even if their heat is dumped.
        if self.allow_dump_for_deadlines:
            for task in list(waiting):
                if task.deadline_h is None or task.slack_h(now_h) >= self.dt_h:
                    continue
                options = [(entry, server) for entry in houses for server in entry[2]
                           if task.allowed_in(server.region)]
                if not options:
                    continue
                entry, server = max(options, key=lambda option: option[0][1])
                plan[task.id] = server.id
                why[task.id] = f"deadline in {task.deadline_h - now_h:.1f} h; heat may be dumped"
                entry[1] -= self._heat(server, task)
                entry[2].remove(server)
                waiting.remove(task)

        return self._diff(plan, why, demand)

    # --- helpers ---

    def _heat(self, server: Server, task: Task) -> float:
        """Recoverable heat one step of this task produces on this server."""
        return server.heat_kw(task.power_kw) * self.dt_h

    def _must_keep(self, task: Task, now_h: float) -> bool:
        if not task.interruptible:
            return True
        return task.slack_h(now_h) < self.dt_h + task.checkpoint_overhead_h

    def _pick(self, waiting: list[Task], server: Server, sig: HeatSignal,
              budget: float) -> Task | None:
        for task in waiting:
            if not task.allowed_in(server.region):
                continue
            heat = self._heat(server, task)
            if budget + EPS < heat + self.start_margin_kwh:
                continue
            if not task.interruptible and not self._fits_ahead(sig, task, heat):
                continue
            return task
        return None

    def _fits_ahead(self, sig: HeatSignal, task: Task, heat: float) -> bool:
        """Rough check that a job which cannot be paused will have a use for its
        heat until it finishes (ignores other servers in the house)."""
        steps = math.ceil(task.remaining_h / self.dt_h - EPS)
        available = sum(sig.forecast_kwh[:steps]) + sig.tank_headroom_kwh
        return available + EPS >= heat * steps

    @staticmethod
    def _diff(plan: dict[str, str], why: dict[str, str], demand: Demand) -> list[Action]:
        """Compare the plan with what is running now and emit only the changes."""
        actions: list[Action] = []
        for task in demand.running():
            target = plan.get(task.id)
            if target is None:
                actions.append(Action(ActionType.PAUSE, task.id, None, why.get(task.id, "")))
            elif target != task.server_id:
                actions.append(Action(ActionType.MIGRATE, task.id, target, why.get(task.id, "")))
        for task_id, server_id in plan.items():
            task = demand.get(task_id)
            if task.status is TaskStatus.PENDING:
                actions.append(Action(ActionType.START, task_id, server_id, why.get(task_id, "")))
            elif task.status is TaskStatus.PAUSED:
                actions.append(Action(ActionType.RESUME, task_id, server_id, why.get(task_id, "")))
        return actions
