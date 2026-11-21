"""The scheduler pausing and resuming a real process on this machine."""

import sys
import time
from pathlib import Path

import pytest

from conftest import make_run
from digitalheat import ActionType, ArrivalMode, ArrivalProcess, JobType, ProcessExecutor

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="process state is read from /proc")


def process_state(pid: int) -> str:
    """'R' running, 'S' sleeping, 'T' stopped."""
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


def test_scheduler_pauses_and_resumes_a_real_process():
    job = JobType(id="real", name="Real job", runtime_h=100, power_kw=1.0, interruptible=True,
                  price_chf_per_server_hour=0.1, source="test")
    arrival = ArrivalProcess("real", ArrivalMode.UNLIMITED, "test", buffer=1)
    executor = ProcessExecutor({"real": [sys.executable, "-c", "import time; time.sleep(60)"]})
    # Heat is wanted for two hours, not wanted for two, then wanted again.
    temps = [-5.6] * 2 + [25.0] * 2 + [-5.6] * 2
    run = make_run(temps, 1, [job], [arrival], executor=executor,
                   overrides={"tank_capacity_kwh": 0.0, "hot_water_kwh_per_day": 0.0,
                              "heating_day_threshold_c": 99.0})
    try:
        run.step()
        pid = executor.pid("real-000001")
        time.sleep(0.2)
        assert process_state(pid) in "RS"

        run.step()
        run.step()                                  # warm hour: scheduler pauses the job
        time.sleep(0.2)
        assert process_state(pid) == "T"

        run.step()
        run.step()                                  # cold again: the same process continues
        time.sleep(0.2)
        assert process_state(pid) in "RS"
        assert executor.pid("real-000001") == pid

        kinds = [action.type for _, action in run.dispatcher.log]
        assert kinds == [ActionType.START, ActionType.PAUSE, ActionType.RESUME]
        assert [event for _, event, _ in executor.events] == ["start", "pause", "resume"]
    finally:
        executor.shutdown()
