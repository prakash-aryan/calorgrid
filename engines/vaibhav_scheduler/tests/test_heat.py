from datetime import datetime

import pytest

from digitalheat import (HOT_WATER_SHAPES, BackupHeater, Building, HeatDemandProfile,
                         HeatStorage, HotWaterTank, House, Scenario, WeatherSeries,
                         reference_assumptions)

START = datetime(2025, 1, 1)


def reference_profile(temps):
    a = reference_assumptions()
    return HeatDemandProfile.reference_house(
        WeatherSeries.from_temperatures(temps, START),
        design_load_kw=a.value("design_heat_load_kw"),
        indoor_setpoint_c=a.value("indoor_setpoint_c"),
        design_delta_k=a.value("design_delta_k"),
        heating_day_threshold_c=a.value("heating_day_threshold_c"),
        hot_water_kwh_per_day=a.value("hot_water_kwh_per_day"),
    )


def test_design_day_needs_ten_kilowatts():
    profile = reference_profile([-5.6] * 24)
    assert profile.space_heating_kwh[0] == pytest.approx(10.0)
    assert profile.total_hot_water_kwh == pytest.approx(12.0)


def test_no_heating_on_a_warm_day_even_in_a_cold_hour():
    temps = [18.0] * 24
    temps[3] = 5.0                       # one cold hour, daily mean still above 12
    profile = reference_profile(temps)
    assert profile.total_space_heating_kwh == 0


def test_heating_day_is_decided_per_calendar_day():
    profile = reference_profile([5.0] * 24 + [15.0] * 24)
    assert sum(profile.space_heating_kwh[:24]) > 0
    assert sum(profile.space_heating_kwh[24:]) == 0


def test_no_negative_demand_above_setpoint():
    temps = [25.0] * 12 + [-10.0] * 12   # mean 7.5: a heating day with hot hours
    profile = reference_profile(temps)
    assert min(profile.space_heating_kwh) == 0


def test_tank_limits():
    tank = HeatStorage(capacity_kwh=35)
    assert tank.charge(40) == 35
    assert tank.headroom_kwh == 0
    assert tank.discharge(50) == 35
    assert tank.soc_kwh == 0


def test_shape_scales_to_annual_total():
    profile = HeatDemandProfile.from_shape([4.0, 2.0, 0.0, 2.0], annual_space_heating_kwh=800,
                                           hot_water_kwh_per_day=12)
    assert profile.total_space_heating_kwh == pytest.approx(800)
    assert profile.space_heating_kwh[0] == pytest.approx(400)


@pytest.mark.parametrize("server_heat", [0.0, 0.3, 4.0, 60.0])
def test_settle_conserves_energy(server_heat):
    house = House("h", "House", "CH-NW", 47.56, 7.59, reference_profile([0.0] * 24),
                  HeatStorage(35, soc_kwh=5), BackupHeater(cop=2.5))
    flows = house.settle(0, server_heat)
    assert flows.server_heat_kwh == pytest.approx(
        flows.direct_kwh + flows.to_tank_kwh + flows.dumped_kwh)
    assert flows.demand_kwh == pytest.approx(
        flows.direct_kwh + flows.from_tank_kwh + flows.backup_kwh)
    assert 0 <= flows.tank_soc_kwh <= 35


def test_open_meteo_csv(tmp_path):
    path = tmp_path / "weather.csv"
    path.write_text(
        "latitude,longitude,elevation,utc_offset_seconds,timezone,timezone_abbreviation\n"
        "47.55,7.58,260.0,3600,Europe/Zurich,GMT+1\n"
        "\n"
        "time,temperature_2m (°C),shortwave_radiation (W/m²)\n"
        "2025-01-01T00:00,1.5,0.0\n"
        "2025-01-01T01:00,-0.5,0.0\n",
        encoding="utf-8",
    )
    weather = WeatherSeries.from_open_meteo_csv(path)
    assert len(weather) == 2
    assert weather[1].temp_c == -0.5
    assert weather[0].ghi_w_m2 == 0.0
    assert weather.start == datetime(2025, 1, 1)


# --------------------------------------------------------------------------- Building

H = 10.0 / 25.6      # the brief's heat loss per degree


def building(temp_c=20.0, min_c=19.0, max_c=22.0, capacity=20.0):
    return Building(loss_kw_per_k=H, capacity_kwh_per_k=capacity,
                    min_temp_c=min_c, max_temp_c=max_c, temp_c=temp_c)


def test_indoor_temperature_holds_when_heat_equals_loss():
    house = building(20.0)
    assert house.apply(house.loss_kwh(0.0), outdoor_c=0.0) == pytest.approx(20.0)


def test_unheated_house_cools_towards_outdoor():
    house = building(20.0)
    assert house.apply(0.0, outdoor_c=0.0) == pytest.approx(20.0 - H * 20.0 / 20.0)
    for _ in range(500):
        house.apply(0.0, outdoor_c=0.0)
    assert house.temp_c == pytest.approx(0.0, abs=0.01)


def test_no_heat_is_needed_while_the_house_is_warm_enough():
    assert building(20.5).min_heat_kwh(0.0) == 0.0


def test_at_the_floor_the_house_needs_exactly_its_loss():
    house = building(19.0)
    assert house.min_heat_kwh(0.0) == pytest.approx(H * 19.0)


@pytest.mark.parametrize("start_c", [19.0, 19.2, 20.5, 22.0])
def test_min_and_max_heat_land_on_the_band_edges(start_c):
    low, high = building(start_c), building(start_c)
    low.apply(low.min_heat_kwh(-3.0), outdoor_c=-3.0)
    high.apply(high.max_heat_kwh(-3.0), outdoor_c=-3.0)
    assert low.temp_c >= 19.0 - 1e-9
    assert high.temp_c == pytest.approx(22.0)
    if start_c <= 19.2:
        assert low.temp_c == pytest.approx(19.0)


def test_band_stores_heat_above_the_floor():
    assert building(22.0).stored_kwh == pytest.approx(60.0)
    assert building(19.0).stored_kwh == 0.0


@pytest.mark.parametrize("outdoor_c", [-10.0, -5.6, 0.0, 8.0])
def test_zero_width_band_reproduces_the_fixed_setpoint_formula(outdoor_c):
    pinned = building(20.0, min_c=20.0, max_c=20.0)
    profile = reference_profile([outdoor_c] * 24)
    assert pinned.min_heat_kwh(outdoor_c) == pytest.approx(profile.space_heating_kwh[0])
    assert pinned.max_heat_kwh(outdoor_c) == pytest.approx(profile.space_heating_kwh[0])


def test_warm_weather_needs_no_heat_and_warms_the_house():
    house = building(20.0)
    assert house.min_heat_kwh(30.0) == 0.0
    assert house.apply(0.0, outdoor_c=30.0) > 20.0


def test_building_rejects_bad_setup():
    with pytest.raises(ValueError):
        building(18.0)                              # starts below the band
    with pytest.raises(ValueError):
        building(capacity=0.0)
    with pytest.raises(ValueError):
        building(capacity=0.1).apply(1.0, 0.0)      # C / H shorter than the hour step


def test_scenario_builds_the_building_from_the_registry():
    scenario = Scenario("test", 1, [], [])
    house = scenario.building()
    assert house.loss_kw_per_k == pytest.approx(H)
    assert (house.min_temp_c, house.max_temp_c, house.temp_c) == (19.0, 22.0, 20.0)
    assert house.time_constant_h == pytest.approx(51.2)
    scenario.assumptions.override("indoor_max_c", 21.0, "test")
    assert scenario.building().max_temp_c == 21.0


# ----------------------------------------------------------- HotWaterTank, HouseHeatModel


def test_tank_capacity_follows_volume_and_temperature():
    tank = HotWaterTank.from_volume(300, hot_c=55, mains_c=10, reserve_fraction=0.25)
    assert tank.capacity_kwh == pytest.approx(15.7005)
    assert tank.level_kwh == pytest.approx(tank.capacity_kwh)      # starts full
    assert tank.reserve_kwh == pytest.approx(15.7005 / 4)


def test_draws_empty_the_tank_and_open_room_for_heat():
    tank = HotWaterTank(capacity_kwh=16, level_kwh=16, reserve_kwh=4)
    assert tank.max_heat_kwh(0.0) == 0.0                # full tank, nothing drawn
    assert tank.apply(0.0, draw_kwh=5.0) == 0.0
    assert tank.level_kwh == 11.0
    assert tank.max_heat_kwh(1.0) == pytest.approx(6.0)  # headroom plus this hour's draw
    assert tank.min_heat_kwh(1.0) == 0.0                # still above the reserve


def test_tank_needs_heat_once_a_draw_would_cut_into_the_reserve():
    tank = HotWaterTank(capacity_kwh=16, level_kwh=5, reserve_kwh=4)
    assert tank.min_heat_kwh(3.0) == pytest.approx(2.0)
    tank.apply(2.0, draw_kwh=3.0)
    assert tank.level_kwh == pytest.approx(4.0)


def test_tank_rejects_overfilling_and_reports_cold_taps():
    tank = HotWaterTank(capacity_kwh=16, level_kwh=1)
    with pytest.raises(ValueError):
        tank.apply(20.0, draw_kwh=0.0)
    assert tank.apply(0.0, draw_kwh=3.0) == pytest.approx(2.0)
    assert tank.level_kwh == 0.0


@pytest.mark.parametrize("shape", HOT_WATER_SHAPES.values())
def test_draw_shapes_cover_the_whole_day(shape):
    assert len(shape) == 24
    assert sum(shape) == pytest.approx(1.0)


def heat_model(indoor_c=19.0):
    return Scenario("test", 1, [], []).heat_model(indoor_c=indoor_c)


def test_daily_draw_matches_the_brief():
    model = heat_model()
    assert sum(model.draw_kwh(hour) for hour in range(24)) == pytest.approx(12.0)


def test_range_at_the_floor_with_a_full_tank():
    model = heat_model(19.0)
    needs = model.heat_range(outdoor_c=0.0, hour_of_day=0)
    assert needs.space_min_kwh == pytest.approx(H * 19.0)      # replace the loss
    assert needs.space_max_kwh == pytest.approx(10.0)          # heating system limit
    assert needs.water_min_kwh == 0.0
    assert needs.water_max_kwh == pytest.approx(model.draw_kwh(0))
    assert needs.must_run_kwh <= needs.absorbable_kwh


def test_absorbable_heat_grows_as_the_tank_empties():
    model = heat_model()
    absorbable = []
    for hour in range(24):
        absorbable.append(model.heat_range(0.0, hour).water_max_kwh)
        model.deliver(0.0, outdoor_c=0.0, hour_of_day=hour)    # no servers: backup only
    assert absorbable[0] < absorbable[8] < absorbable[20]
    assert model.building.temp_c == pytest.approx(19.0)        # backup holds the floor
    assert model.tank.level_kwh >= model.tank.reserve_kwh - 1e-9


@pytest.mark.parametrize("supplied", [0.0, 2.4, 8.0, 12.0, 40.0])
def test_delivery_conserves_energy_and_stays_in_range(supplied):
    model = heat_model(20.0)
    for hour in range(24):
        level, indoor = model.tank.level_kwh, model.building.temp_c
        loss = model.building.loss_kwh(-2.0)
        d = model.deliver(supplied, outdoor_c=-2.0, hour_of_day=hour)
        assert d.supplied_kwh == pytest.approx(d.to_space_kwh + d.to_water_kwh + d.dumped_kwh)
        assert d.used_kwh <= d.range.absorbable_kwh + 1e-9
        assert d.used_kwh + d.backup_kwh >= d.range.must_run_kwh - 1e-9
        assert d.tank_level_kwh == pytest.approx(
            level + d.to_water_kwh + d.backup_water_kwh - d.water_draw_kwh)
        assert d.indoor_c == pytest.approx(
            indoor + (d.to_space_kwh + d.backup_space_kwh - loss) / 20.0)
        assert 19.0 - 1e-9 <= d.indoor_c <= 22.0 + 1e-9
        if d.dumped_kwh:
            assert d.backup_kwh == 0.0


def test_surplus_fills_the_tank_before_warming_the_house():
    model = heat_model(19.0)
    model.tank.level_kwh = 10.0
    needs = model.heat_range(0.0, 12)
    d = model.deliver(needs.must_run_kwh + 1.0, outdoor_c=0.0, hour_of_day=12)
    assert d.to_water_kwh == pytest.approx(1.0)
    assert d.to_space_kwh == pytest.approx(needs.space_min_kwh)


def test_no_space_heating_on_a_non_heating_day():
    model = heat_model(19.0)
    needs = model.heat_range(outdoor_c=10.0, hour_of_day=7, heating_day=False)
    assert needs.space_min_kwh == needs.space_max_kwh == 0.0
    assert needs.absorbable_kwh == pytest.approx(model.draw_kwh(7))
