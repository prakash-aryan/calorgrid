"""The job and power catalogues built from the open datasets."""

from pathlib import Path

import pytest

from digitalheat import (Placement, PowerProfile, TraceJob, gpu_hours_by_step,
                         load_power_profiles, load_trace_jobs)

DATA = Path(__file__).resolve().parent.parent / "data"


def profile(full_load_kw=10.4, gpus=8):
    return PowerProfile("p", "System", "GPU", gpus, "benchmark", "Offline", full_load_kw, "test")


def job(gpus=2.0, duration_h=1.5):
    return TraceJob("j", gpus, duration_h, "worker", "test")


def test_power_is_proportional_to_the_gpus_working():
    p = profile(10.4, 8)
    assert p.power_kw(0) == 0.0
    assert p.power_kw(2) == pytest.approx(2.6)
    assert p.power_kw(8) == pytest.approx(10.4)
    assert p.power_kw(20) == pytest.approx(10.4)        # cannot draw more than full load


def test_a_job_asks_for_gpus_only_while_it_runs():
    placed = Placement(job(2.0, 1.5), start_h=3)
    assert gpu_hours_by_step([placed], 6) == pytest.approx([0, 0, 0, 2.0, 1.0, 0])
    assert sum(gpu_hours_by_step([placed], 6)) == pytest.approx(placed.job.gpu_hours)


def test_kept_running_job_fills_the_whole_window():
    placed = Placement(job(1.0, 0.1), start_h=2, keep_running_h=3)
    assert gpu_hours_by_step([placed], 6) == pytest.approx([0, 0, 1, 1, 1, 0])


def test_jobs_on_one_server_add_up_and_can_overbook_it():
    hours = gpu_hours_by_step([Placement(job(5.0, 2), 0), Placement(job(5.0, 2), 1)], 3)
    assert hours == pytest.approx([5, 10, 5])            # 10 > 8 GPUs: overbooked in hour 1


def test_a_job_is_cut_off_at_the_end_of_the_run():
    assert gpu_hours_by_step([Placement(job(1.0, 10), 22)], 24)[-2:] == pytest.approx([1, 1])


def test_bad_values_are_rejected():
    with pytest.raises(ValueError):
        job(gpus=0)
    with pytest.raises(ValueError):
        profile(full_load_kw=0)


def test_mlperf_file_gives_measured_systems():
    profiles = load_power_profiles(DATA / "mlperf_power.json")
    assert profiles
    for p in profiles:
        assert p.gpus == 8 and 1 < p.full_load_kw < 20
        assert "MLPerf Inference" in p.source and p.url.startswith("https://")


def test_trace_file_gives_jobs_that_fit_one_server():
    jobs = load_trace_jobs(DATA / "alibaba_gpu_jobs.json")
    assert len(jobs) == 15 and len({j.id for j in jobs}) == 15
    for j in jobs:
        assert 0 < j.gpus <= 8
        assert "Alibaba" in j.source
