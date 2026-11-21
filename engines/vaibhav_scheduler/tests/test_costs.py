"""Costs of running servers, the heat saving and the break-even GPU-hour price."""

import pytest

from digitalheat import ServerCosts


def costs(**changes):
    values = dict(electricity_chf_per_kwh=0.25, heat_pump_cop=2.5, hardware_chf_per_server=36_500,
                  hardware_life_years=10, maintenance_fraction_per_year=0.05)
    return ServerCosts(**(values | changes))


def test_each_cost_is_reported_separately():
    b = costs().balance(servers=2, electricity_kwh=100, gpu_hours=80, backup_avoided_kwh=50)
    assert b.hardware_chf == pytest.approx(20.0)        # 2 x 36,500 / (10 x 365)
    assert b.electricity_chf == pytest.approx(25.0)
    assert b.maintenance_chf == pytest.approx(10.0)     # 5% of 73,000 a year
    assert b.heat_saving_chf == pytest.approx(5.0)      # 50 kWh / COP 2.5 x 0.25
    assert b.cost_chf == pytest.approx(55.0)


def test_break_even_price_falls_by_the_heat_saving():
    b = costs().balance(servers=2, electricity_kwh=100, gpu_hours=80, backup_avoided_kwh=50)
    assert b.break_even_chf_per_gpu_hour(with_heat_saving=False) == pytest.approx(55 / 80)
    assert b.break_even_chf_per_gpu_hour() == pytest.approx(50 / 80)
    assert b.profit_chf(b.break_even_chf_per_gpu_hour()) == pytest.approx(0.0)
    assert b.profit_chf(1.0, with_heat_saving=False) == pytest.approx(25.0)
    assert b.profit_chf(1.0) == pytest.approx(30.0)


def test_a_longer_hardware_life_lowers_the_daily_cost():
    short = costs(hardware_life_years=5).balance(1, 0, 10, 0)
    long = costs(hardware_life_years=10).balance(1, 0, 10, 0)
    assert short.hardware_chf == pytest.approx(2 * long.hardware_chf)
    assert short.maintenance_chf == pytest.approx(long.maintenance_chf)


def test_idle_servers_still_cost_money_and_have_no_break_even():
    b = costs().balance(servers=1, electricity_kwh=0, gpu_hours=0, backup_avoided_kwh=0)
    assert b.cost_chf == pytest.approx(15.0)
    assert b.break_even_chf_per_gpu_hour() is None


def test_several_days_scale_the_fixed_costs():
    assert costs().balance(1, 0, 1, 0, days=7).hardware_chf == pytest.approx(70.0)
