"""Costs of running servers, the heat saving and the break-even GPU-hour price."""

import pytest

from digitalheat import ServerCosts, TierPricing


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



def test_gas_backup_prices_the_saving_at_gas_over_boiler_efficiency():
    gas = costs(backup="gas", gas_chf_per_kwh=0.135, boiler_efficiency=0.9)
    assert gas.heat_value_chf_per_kwh == pytest.approx(0.15)
    assert costs().heat_value_chf_per_kwh == pytest.approx(0.1)     # heat pump: 0.25 / 2.5
    b = gas.balance(servers=2, electricity_kwh=100, gpu_hours=80, backup_avoided_kwh=50)
    assert b.heat_saving_chf == pytest.approx(7.5)                  # 50 kWh x 0.135 / 0.9
    assert b.cost_chf == pytest.approx(55.0)                        # costs do not change
    for bad in (dict(backup="oil"), dict(backup="gas"),             # no gas price
                dict(backup="gas", gas_chf_per_kwh=0.1, boiler_efficiency=0)):
        with pytest.raises(ValueError):
            costs(**bad)

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


def test_further_costs_are_shares_of_the_hardware_price_and_idle_gpus_draw_power():
    extra = costs(yearly_fractions={"building": 0.02, "staff": 0.10}, installation_fraction=0.10,
                  idle_power_fraction=0.2)
    b = extra.balance(servers=2, electricity_kwh=100, gpu_hours=80, backup_avoided_kwh=50,
                      idle_full_load_kwh=40)
    assert b.other_chf["building"] == pytest.approx(4.0)        # 2% of 73,000 a year, one day
    assert b.other_chf["staff"] == pytest.approx(20.0)
    assert b.other_chf["installation"] == pytest.approx(2.0)    # 10% once, over 10 years
    assert b.other_chf["idle power"] == pytest.approx(2.0)      # 40 kWh x 20% x 0.25
    assert b.cost_chf == pytest.approx(55.0 + 28.0)
    assert b.break_even_chf_per_gpu_hour() == pytest.approx((83 - 5) / 80)
    plain = costs().balance(servers=2, electricity_kwh=100, gpu_hours=80, backup_avoided_kwh=50,
                            idle_full_load_kwh=40)
    assert plain.other_chf == {} and plain.cost_chf == pytest.approx(55.0)   # nothing entered
    with pytest.raises(ValueError):
        costs(yearly_fractions={"staff": -0.1})


# --- prices by tier ---

FACTORS = {"spot": 1.0, "on_demand": 3.0, "reserved": 2.0}
SOLD = {"spot": 40.0, "on_demand": 10.0, "reserved": 15.0}       # GPU-hours billed


def test_tier_prices_are_the_base_price_times_a_factor():
    pricing = TierPricing(0.5, FACTORS)
    assert pricing.price("on_demand") == pytest.approx(1.5)
    assert pricing.weighted_gpu_hours(SOLD) == pytest.approx(100.0)   # 40 + 30 + 30
    assert pricing.income_chf(SOLD) == pytest.approx(50.0)
    with pytest.raises(ValueError):
        TierPricing(-1.0, FACTORS)


def test_the_break_even_base_price_leaves_no_profit_and_a_margin_price_leaves_the_margin():
    b = costs().balance(servers=2, electricity_kwh=100, gpu_hours=65, backup_avoided_kwh=50)
    pricing = TierPricing(1.0, FACTORS)
    base = pricing.base_price_for(b, SOLD)
    assert base == pytest.approx(0.5)                                 # 50 CHF net over 100 weighted hours
    assert TierPricing(base, FACTORS).profit_chf(b, SOLD) == pytest.approx(0.0)
    assert pricing.base_price_for(b, SOLD, with_heat_saving=False) == pytest.approx(0.55)
    at_margin = TierPricing(pricing.base_price_for(b, SOLD, margin=0.2), FACTORS)
    assert at_margin.profit_chf(b, SOLD) / at_margin.income_chf(SOLD) == pytest.approx(0.2)
    assert pricing.profit_chf(b, SOLD) == pytest.approx(50.0)         # 100 income + 5 heat - 55 cost
    assert pricing.base_price_for(b, {"spot": 0.0}) is None           # nothing sold yet
    with pytest.raises(ValueError):
        pricing.base_price_for(b, SOLD, margin=1.0)
