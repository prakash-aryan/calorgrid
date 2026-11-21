"""Unit tests for M2 real CPU batch workers and live SIGSTOP/SIGCONT control."""

import time

import psutil
import pytest

from digitalheat.scheduler import ARRIVAL_RATE_SOURCE, WORKLOAD_CATALOG
from digitalheat.worker import WorkerManager, run_single_unit


def _wait_until_idle(mgr: WorkerManager, timeout_s: float = 25.0) -> object:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = mgr.get_status()
        if st.state == "idle":
            return st
        time.sleep(0.1)
    raise AssertionError("Worker did not finish within timeout")


def test_workload_catalog_has_two_runnable_jobs_and_scaling_contract() -> None:
    runnable = [k for k, spec in WORKLOAD_CATALOG.items() if spec.local_cpu_runnable]
    assert len(runnable) >= 2
    assert "archive_ocr" in runnable
    assert "iiif_embeddings" in runnable
    for key in runnable:
        assert "3,600,000 J" in WORKLOAD_CATALOG[key].scaling_formula
    assert "12.0 server-hours" in ARRIVAL_RATE_SOURCE


def test_real_archive_ocr_subprocess_completes_and_scales_to_1kw() -> None:
    mgr = WorkerManager()
    try:
        started = mgr.start(job_type="archive_ocr", max_units=2)
        assert started.pid is not None
        assert started.state == "running"

        done = _wait_until_idle(mgr, timeout_s=25.0)
        assert done.last_exit_code == 0
        assert done.units_completed == 2
        assert done.last_unit_output is not None
        assert done.last_unit_output["pages_processed"] == 1
        assert "STAATSARCHIV" in done.last_unit_output["sample_text"].upper()
        assert done.measured_energy_j > 0.0
        assert done.active_runtime_s > 0.0
        assert done.scaled_1kw.measured_energy_per_unit_j is not None
        assert done.scaled_1kw.measured_energy_per_unit_j > 0.0
        assert done.scaled_1kw.scaled_units_per_server_hour is not None
        assert done.scaled_1kw.scaled_units_per_server_hour > 0.0
    finally:
        mgr.shutdown()


def test_real_iiif_embeddings_subprocess_deterministic_and_normalized() -> None:
    mgr = WorkerManager()
    try:
        mgr.start(job_type="iiif_embeddings", max_units=2)
        done = _wait_until_idle(mgr, timeout_s=15.0)
        assert done.last_exit_code == 0
        assert done.units_completed == 2
        assert done.last_unit_output is not None
        assert done.last_unit_output["plates_processed"] == 32
        assert done.last_unit_output["embedding_dim"] == 256
        assert done.last_unit_output["l2_norm"] == pytest.approx(1.0, abs=1e-4)
        assert done.scaled_1kw.scaled_units_per_server_hour is not None
        assert done.scaled_1kw.scaled_units_per_server_hour > 0.0

        direct = run_single_unit("iiif_embeddings", 1)
        assert direct["plate_digest"] == done.last_unit_output["plate_digest"]
    finally:
        mgr.shutdown()


def test_real_audio_transcription_unit_runs_on_cpu() -> None:
    res = run_single_unit("audio_transcription", 0)
    assert res["unit_type"] == "audio_transcription"
    assert res["audio_seconds_indexed"] == 80.0
    assert res["feature_dim"] == 26
    assert res["mfcc_frames"] > 1000


def test_live_sigstop_sigcont_freezes_energy_and_psutil_status() -> None:
    mgr = WorkerManager()
    pid = None
    tracked_pids: set[int] = set()
    try:
        started = mgr.start(job_type="archive_ocr", continuous=True, max_runtime_s=30.0)
        pid = started.pid
        assert pid is not None
        assert psutil.pid_exists(pid)
        tracked_pids.add(pid)
        time.sleep(0.3)
        try:
            for child in psutil.Process(pid).children(recursive=True):
                tracked_pids.add(child.pid)
        except psutil.NoSuchProcess:
            pass

        paused = mgr.apply_heat_signal(tank_soc_kwh=34.8, heat_demand_kw=0.5, outdoor_temp_c=22.0)
        assert paused.state == "paused"
        assert paused.heat_signal_on is False
        assert paused.psutil_status == psutil.STATUS_STOPPED
        assert psutil.Process(pid).status() == psutil.STATUS_STOPPED
        assert paused.scaled_1kw.reference_server_power_kw == 0.0

        time.sleep(0.45)
        still_paused = mgr.get_status()
        assert still_paused.state == "paused"
        assert still_paused.measured_energy_j == pytest.approx(paused.measured_energy_j, abs=1e-9)
        assert still_paused.active_runtime_s == pytest.approx(paused.active_runtime_s, abs=1e-9)
        assert still_paused.cpu_time_s == pytest.approx(paused.cpu_time_s, abs=1e-9)
        assert still_paused.paused_time_s >= paused.paused_time_s + 0.40

        resumed = mgr.apply_heat_signal(tank_soc_kwh=10.0, heat_demand_kw=8.5, outdoor_temp_c=-2.0)
        assert resumed.state == "running"
        assert resumed.heat_signal_on is True
        assert resumed.psutil_status != psutil.STATUS_STOPPED
        assert resumed.scaled_1kw.reference_server_power_kw == 1.0

        mgr.pause()
        stopped = mgr.stop()
        assert stopped.state == "idle"
        assert stopped.pid is None
        assert not psutil.pid_exists(pid)
    finally:
        mgr.shutdown()

    assert tracked_pids
    assert not any(psutil.pid_exists(p) for p in tracked_pids)


def test_finite_worker_post_exit_sleep_does_not_inflate_runtime_or_energy() -> None:
    mgr = WorkerManager()
    try:
        t0 = time.monotonic()
        started = mgr.start(job_type="iiif_embeddings", max_units=1)
        assert started.pid is not None
        proc = psutil.Process(started.pid)

        # Wait for subprocess exit at the OS level WITHOUT polling WorkerManager.get_status()
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            try:
                if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Worker subprocess did not exit within timeout")

        # Sleep well past child exit without calling get_status()
        time.sleep(1.5)
        total_slept = time.monotonic() - t0
        st = mgr.get_status()
        assert st.state == "idle"
        assert st.last_exit_code == 0
        assert st.units_completed == 1
        assert st.active_runtime_s < total_slept - 1.0
        assert st.wall_elapsed_s < total_slept - 1.0
        assert st.measured_energy_j > 0.0
        assert st.scaled_1kw.measured_energy_per_unit_j is not None

        time.sleep(0.5)
        st2 = mgr.get_status()
        assert st2.active_runtime_s == pytest.approx(st.active_runtime_s, abs=1e-9)
        assert st2.wall_elapsed_s == pytest.approx(st.wall_elapsed_s, abs=1e-9)
        assert st2.measured_energy_j == pytest.approx(st.measured_energy_j, abs=1e-9)
    finally:
        mgr.shutdown()
