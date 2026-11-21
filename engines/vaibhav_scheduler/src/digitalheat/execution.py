"""Execution: turning scheduling decisions into running, paused and moved jobs.

Entities
    Action / ActionType  one instruction from the scheduler
    Executor             what actually runs a job on a machine
    SimulatedExecutor    bookkeeping only, for the yearly simulation
    ProcessExecutor      real OS processes, paused with SIGSTOP / SIGCONT
    Dispatcher           applies actions to tasks, servers and the executor

The scheduler never touches a task or server directly. It returns actions, and
the Dispatcher is the only place where state changes, so a simulated run and a
live run go through identical code.
"""

from __future__ import annotations

import signal
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Protocol, Sequence

from .compute import Server, ServerRegistry
from .workload import Checkpoint, Demand, Task, TaskStatus


class ActionType(str, Enum):
    START = "start"       # PENDING task onto a free server
    PAUSE = "pause"       # stop a RUNNING task and free its server
    RESUME = "resume"     # PAUSED task onto a free server
    MIGRATE = "migrate"   # move a RUNNING task to another server in one step


@dataclass(frozen=True)
class Action:
    type: ActionType
    task_id: str
    server_id: str | None = None   # target server; not needed for PAUSE
    reason: str = ""               # plain-language explanation for the log


class Executor(Protocol):
    def start(self, task: Task, server: Server) -> None: ...
    def pause(self, task: Task, server: Server, now_h: float) -> Checkpoint: ...
    def resume(self, task: Task, server: Server, checkpoint: Checkpoint | None) -> None: ...
    def stop(self, task: Task, server: Server | None) -> None: ...
    def is_finished(self, task: Task) -> bool | None:
        """True/False if the executor tracks completion itself; None if the
        simulation should decide from the task's remaining work."""
        ...


class SimulatedExecutor:
    """No real work is done; the simulation advances task progress itself."""

    def start(self, task: Task, server: Server) -> None:
        pass

    def pause(self, task: Task, server: Server, now_h: float) -> Checkpoint:
        return Checkpoint(task.id, task.work_done_h, now_h, server.id)

    def resume(self, task: Task, server: Server, checkpoint: Checkpoint | None) -> None:
        pass

    def stop(self, task: Task, server: Server | None) -> None:
        pass

    def is_finished(self, task: Task) -> bool | None:
        return None


class ProcessExecutor:
    """Runs each task as a real process on this machine (Linux and macOS).

    Pause sends SIGSTOP and resume sends SIGCONT, so the job keeps its memory
    and continues exactly where it stopped. Every server is this one machine,
    so a 'migration' continues the same process; moving a job between real
    machines needs a job that writes its own checkpoint files.
    """

    def __init__(self, commands: dict[str, Sequence[str]],
                 default: Sequence[str] | None = None) -> None:
        self.commands = dict(commands)
        self.default = default
        self._procs: dict[str, subprocess.Popen] = {}
        self.events: list[tuple[float, str, str]] = []   # (wall-clock time, event, task id)

    def _log(self, event: str, task: Task) -> None:
        self.events.append((time.time(), event, task.id))

    def pid(self, task_id: str) -> int | None:
        proc = self._procs.get(task_id)
        return proc.pid if proc else None

    def start(self, task: Task, server: Server) -> None:
        command = self.commands.get(task.job_type_id, self.default)
        if command is None:
            raise KeyError(f"no command configured for job type {task.job_type_id!r}")
        self.stop(task, server)                      # a restart replaces the old process
        self._procs[task.id] = subprocess.Popen(list(command))
        self._log("start", task)

    def pause(self, task: Task, server: Server, now_h: float) -> Checkpoint:
        proc = self._procs[task.id]
        if proc.poll() is None:
            proc.send_signal(signal.SIGSTOP)
        self._log("pause", task)
        return Checkpoint(task.id, task.work_done_h, now_h, server.id, f"pid:{proc.pid}")

    def resume(self, task: Task, server: Server, checkpoint: Checkpoint | None) -> None:
        proc = self._procs[task.id]
        if proc.poll() is None:
            proc.send_signal(signal.SIGCONT)
        self._log("resume", task)

    def stop(self, task: Task, server: Server | None) -> None:
        proc = self._procs.pop(task.id, None)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
            self._log("stop", task)

    def is_finished(self, task: Task) -> bool | None:
        proc = self._procs.get(task.id)
        return proc is not None and proc.poll() is not None

    def shutdown(self) -> None:
        for proc in self._procs.values():
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self._procs.clear()


class Dispatcher:
    """Applies actions. The only writer of task and server state."""

    def __init__(self, demand: Demand, registry: ServerRegistry, executor: Executor) -> None:
        self.demand = demand
        self.registry = registry
        self.executor = executor
        self.log: list[tuple[float, Action]] = []

    def apply(self, actions: Iterable[Action], now_h: float) -> None:
        actions = list(actions)
        # Free servers first so that a server released in this step can be reused in it.
        for action in actions:
            if action.type in (ActionType.PAUSE, ActionType.MIGRATE):
                self._interrupt(self.demand.get(action.task_id), now_h)
        for action in actions:
            if action.type is not ActionType.PAUSE:
                if action.server_id is None:
                    raise ValueError(f"{action.type.value} needs a target server")
                self._place(self.demand.get(action.task_id),
                            self.registry.get(action.server_id), now_h)
            self.log.append((now_h, action))

    def _interrupt(self, task: Task, now_h: float) -> None:
        server = self.registry.get(task.server_id)
        if task.interruptible:
            task.interrupt(now_h, self.executor.pause(task, server, now_h))
        else:
            self.executor.stop(task, server)       # progress is lost
            task.interrupt(now_h)
        server.release()

    def _place(self, task: Task, server: Server, now_h: float) -> None:
        if not task.allowed_in(server.region):
            raise ValueError(f"task {task.id} must stay in {task.region_constraint}, "
                             f"server {server.id} is in {server.region}")
        server.assign(task.id)
        if task.status is TaskStatus.PAUSED:
            task.resume(server.id, now_h)
            self.executor.resume(task, server, task.checkpoint)
        else:
            task.start(server.id, now_h)
            self.executor.start(task, server)

    def finish(self, task: Task, now_h: float) -> None:
        """The task's work is complete."""
        server = self.registry.get(task.server_id)
        task.complete(now_h)
        server.release()

    def drop(self, task: Task, now_h: float) -> None:
        """The task can no longer meet its deadline."""
        server = self.registry.get(task.server_id) if task.server_id else None
        self.executor.stop(task, server)
        task.miss(now_h)
        if server is not None:
            server.release()

    def fail_server(self, server_id: str, now_h: float) -> None:
        """A server breaks: its task goes back to the backlog."""
        server = self.registry.get(server_id)
        if server.task_id is not None:
            self._interrupt(self.demand.get(server.task_id), now_h)
        server.fail()
