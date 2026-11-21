import pytest

from digitalheat import (ArrivalMode, ArrivalProcess, Demand, InvalidTransition, JobType,
                         Measurement, TaskStatus)


def job(**overrides):
    values = dict(id="tag", name="Image tagging", runtime_h=2, power_kw=1.0, interruptible=True,
                  price_chf_per_server_hour=0.2, source="test", relative_deadline_h=6,
                  checkpoint_overhead_h=0.1)
    values.update(overrides)
    return JobType(**values)


def test_job_type_needs_a_source_and_a_feasible_deadline():
    with pytest.raises(ValueError):
        job(source=" ")
    with pytest.raises(ValueError):
        job(relative_deadline_h=1)
    assert job().energy_kwh == 2.0


def test_measurement_scales_to_reference_server():
    m = Measurement("tag", "laptop", runtime_s=1800, avg_power_w=40, method="runtime_x_power",
                    measured_on="2026-11-21", jobs_in_run=10)
    assert m.energy_kwh_per_job == pytest.approx(0.002)
    assert m.reference_runtime_h(reference_power_kw=1.0) == pytest.approx(0.002)


def test_fixed_daily_arrivals_are_exact_and_repeatable():
    def arrivals_per_day():
        demand = Demand([job()], [ArrivalProcess("tag", ArrivalMode.FIXED_DAILY, "test",
                                                 jobs_per_day=10)])
        return sum(len(demand.step(float(h), h % 24)) for h in range(24))

    assert arrivals_per_day() == 10
    assert arrivals_per_day() == 10


def test_poisson_arrivals_follow_the_seed():
    def counts(seed):
        demand = Demand([job()], [ArrivalProcess("tag", ArrivalMode.POISSON, "test",
                                                 jobs_per_day=48, seed=seed)])
        return [len(demand.step(float(h), h % 24)) for h in range(48)]

    assert counts(1) == counts(1)
    assert counts(1) != counts(2)


def test_unlimited_keeps_a_buffer_of_waiting_jobs():
    demand = Demand([job(relative_deadline_h=None)],
                    [ArrivalProcess("tag", ArrivalMode.UNLIMITED, "test", buffer=3)])
    assert len(demand.step(0.0, 0)) == 3
    assert len(demand.step(1.0, 1)) == 0
    demand.backlog()[0].start("s1", 1.0)
    assert len(demand.step(2.0, 2)) == 1


def test_trace_arrivals():
    demand = Demand([job()], [ArrivalProcess("tag", ArrivalMode.TRACE, "test",
                                             trace_steps=(0, 0, 5))])
    assert [len(demand.step(float(h), h)) for h in range(6)] == [2, 0, 0, 0, 0, 1]


def test_task_lifecycle_with_pause_and_migration():
    demand = Demand([job()], [ArrivalProcess("tag", ArrivalMode.TRACE, "test", trace_steps=(0,))])
    task = demand.step(0.0, 0)[0]
    assert task.deadline_h == 6 and task.slack_h(0.0) == 4

    task.start("s1", 0.0)
    assert task.advance(1.0) == 1.0
    task.interrupt(1.0)
    assert task.status is TaskStatus.PAUSED and task.checkpoint.work_done_h == 1.0
    assert task.remaining_h == pytest.approx(1.1)        # one hour left plus the overhead

    task.resume("s2", 2.0)
    assert task.migrations == 1 and task.interruptions == 1
    assert task.advance(5.0) == pytest.approx(1.1)
    task.complete(3.1)
    assert task.status is TaskStatus.DONE and not task.is_open
    with pytest.raises(InvalidTransition):
        task.start("s1", 4.0)


def test_non_interruptible_task_restarts_from_zero():
    demand = Demand([job(interruptible=False, checkpoint_overhead_h=0)],
                    [ArrivalProcess("tag", ArrivalMode.TRACE, "test", trace_steps=(0,))])
    task = demand.step(0.0, 0)[0]
    task.start("s1", 0.0)
    task.advance(1.5)
    task.interrupt(1.5)
    assert task.status is TaskStatus.PENDING
    assert task.work_done_h == 0 and task.restarts == 1


def test_overdue_and_region_filter():
    demand = Demand([job(region_constraint="CH-NW")],
                    [ArrivalProcess("tag", ArrivalMode.TRACE, "test", trace_steps=(0,))])
    task = demand.step(0.0, 0)[0]
    assert demand.eligible(0.0, region="DE-BW") == []
    assert demand.eligible(0.0, region="CH-NW") == [task]
    assert demand.overdue(4.0) == []          # 2 h of work, deadline at 6: just feasible
    assert demand.overdue(4.5) == [task]
