from dataclasses import replace
from datetime import datetime

import pytest

from conftest import make_run
from digitalheat import (ActionType, ArrivalMode, ArrivalProcess, HeatFollowingScheduler, JobType,
                         Scenario, TaskStatus, WeatherSeries)

COLD, WARM = -5.6, 25.0     # full heating load / no heating at all


def check_conservation(run):
    for record in run.ledger.records:
        f = record.flows
        assert f.server_heat_kwh == pytest.approx(f.direct_kwh + f.to_tank_kwh + f.dumped_kwh)
        assert f.demand_kwh == pytest.approx(f.direct_kwh + f.from_tank_kwh + f.backup_kwh)


def test_cold_day_runs_every_server_and_dumps_nothing(filler):
    job, arrival = filler
    run = make_run([COLD] * 48, 4, [job], [arrival])
    balance = run.run()["ref"]
    check_conservation(run)

    assert balance.utilisation == pytest.approx(1.0)
    assert balance.heat_dumped_kwh == 0
    # 4 servers x 0.8 kWh/h against 10 kW heating + 0.5 kWh/h hot water
    assert balance.covered_share == pytest.approx(3.2 / 10.5)
    assert balance.electricity_kwh == pytest.approx(4 * 48)
    assert balance.computing_income_chf == pytest.approx(4 * 48 * 0.10)


def test_summer_server_follows_hot_water_without_dumping(filler):
    job, arrival = filler
    run = make_run([WARM] * 24 * 30, 1, [job], [arrival])
    balance = run.run()["ref"]
    check_conservation(run)

    assert balance.heat_dumped_kwh == 0
    assert balance.covered_share == pytest.approx(1.0)
    assert 0 < balance.utilisation < 1                     # paused once the tank is full
    pauses = [a for _, a in run.dispatcher.log if a.type is ActionType.PAUSE]
    resumes = [a for _, a in run.dispatcher.log if a.type is ActionType.RESUME]
    assert pauses and resumes
    assert all(a.reason for a in pauses)


def test_no_servers_means_backup_covers_everything(filler):
    job, arrival = filler
    run = make_run([COLD] * 24, 0, [job], [arrival])
    balance = run.run()["ref"]
    assert balance.covered_share == 0
    assert balance.backup_heat_kwh == pytest.approx(balance.heat_demand_kwh)
    assert balance.break_even_chf_per_server_hour is None


def deadline_job():
    job = JobType(id="urgent", name="Urgent job", runtime_h=3, power_kw=1.0, interruptible=True,
                  price_chf_per_server_hour=0.5, source="test", relative_deadline_h=5)
    arrival = ArrivalProcess("urgent", ArrivalMode.TRACE, "test", trace_steps=(2,))
    return job, arrival


def test_deadline_job_runs_even_when_heat_is_dumped():
    job, arrival = deadline_job()
    # No tank and no hot water: a warm day cannot use any heat.
    run = make_run([WARM] * 24, 1, [job], [arrival],
                   overrides={"tank_capacity_kwh": 0.0, "hot_water_kwh_per_day": 0.0})
    balance = run.run()["ref"]
    check_conservation(run)
    task = next(iter(run.demand.tasks.values()))

    assert task.status is TaskStatus.DONE
    assert task.finished_h <= task.deadline_h
    assert balance.missed_deadline_pct == 0
    assert balance.heat_dumped_kwh == pytest.approx(3 * 0.8)


def test_deadline_is_missed_when_dumping_is_forbidden():
    job, arrival = deadline_job()
    run = make_run([WARM] * 24, 1, [job], [arrival],
                   overrides={"tank_capacity_kwh": 0.0, "hot_water_kwh_per_day": 0.0},
                   scheduler=HeatFollowingScheduler(allow_dump_for_deadlines=False))
    balance = run.run()["ref"]
    assert balance.missed_deadline_pct == 100
    assert balance.heat_dumped_kwh == 0


def test_break_even_price_makes_net_zero(filler):
    job, arrival = filler
    run = make_run([COLD] * 48, 2, [job], [arrival])
    b = run.run()["ref"]
    # With all heat used: electricity price x (1 - recovery / COP) per server-hour.
    assert b.break_even_chf_per_server_hour == pytest.approx(0.3325 * (1 - 0.8 / 2.5))
    income_at_break_even = b.break_even_chf_per_server_hour * b.running_server_hours
    assert income_at_break_even + b.heat_value_chf - b.electricity_cost_chf == pytest.approx(0)
    assert not b.pays_off                                  # 0.10 CHF/h is below break-even


def test_region_constraint_is_respected(filler):
    job = JobType(id="private", name="Private data", runtime_h=2, power_kw=1.0,
                  interruptible=True, price_chf_per_server_hour=0.3, source="test",
                  region_constraint="DE-BW")
    arrival = ArrivalProcess("private", ArrivalMode.UNLIMITED, "test", buffer=4)
    run = make_run([COLD] * 24, 2, [job], [arrival])       # the house is in CH-NW
    balance = run.run()["ref"]
    assert balance.running_server_hours == 0


def test_work_moves_to_the_house_that_needs_heat(filler):
    job, arrival = filler
    job = replace(job, runtime_h=5)       # still running when the weather flips at hour 24
    scenario = Scenario("two houses", 1, [job], [arrival])
    scenario.assumptions.override("tank_capacity_kwh", 0.0, "test")
    scenario.assumptions.override("hot_water_kwh_per_day", 0.0, "test")
    start = datetime(2025, 1, 1)
    day_a = WeatherSeries.from_temperatures([COLD] * 24 + [WARM] * 24, start)
    day_b = WeatherSeries.from_temperatures([WARM] * 24 + [COLD] * 24, start)
    run = scenario.build([scenario.reference_house(day_a, "a"),
                          scenario.reference_house(day_b, "b")], start)
    balances = run.run()

    assert balances["a"].running_server_hours == pytest.approx(24)
    assert balances["b"].running_server_hours == pytest.approx(24)
    assert balances["a"].heat_dumped_kwh == 0 and balances["b"].heat_dumped_kwh == 0
    assert any(a.type is ActionType.MIGRATE for _, a in run.dispatcher.log)
