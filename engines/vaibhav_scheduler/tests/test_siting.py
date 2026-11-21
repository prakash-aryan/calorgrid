import pytest

from digitalheat.siting import (SitingParams, heat_requirement, rank_second_site, run_sites,
                                window_hours)

NO_TANK = SitingParams(tank_kwh=0.0)


def check_conservation(result):
    eta = result.params.recovery
    for site in result.sites:
        assert site.requirement == pytest.approx(site.direct + site.from_tank + site.backup)
        # server heat = used directly + dumped + stored; what was stored is either
        # taken out again or still in the tank at the end
        assert eta * site.compute >= site.direct + site.dumped - 1e-9
        assert eta * site.compute - site.direct - site.dumped >= site.from_tank - 1e-9


def test_heat_requirement_matches_the_brief_formula():
    cold_day, warm_day = [-5.6] * 24, [25.0] * 24
    need = heat_requirement(cold_day + warm_day)
    assert need[0] == pytest.approx(10.0 + 0.5)       # full load plus hot water
    assert need[30] == pytest.approx(0.5)             # hot water only


def test_window_hours_cover_whole_months_and_wrap():
    assert len(window_hours(1, 6)) == (31 + 28 + 31 + 30 + 31 + 30) * 24
    wrapped = window_hours(10, 6)                     # October to March
    assert wrapped[0] == (365 - 92) * 24 and wrapped[-1] == (31 + 28 + 31) * 24 - 1
    assert len(window_hours(1, 12)) == 8760


def test_single_site_without_tank_uses_min_of_heat_and_requirement():
    need = [4.0, 1.0, 0.0]
    result = run_sites([need], compute_kw=2.0, params=NO_TANK)
    check_conservation(result)
    # 2 kW of compute gives 1.7 kWh of heat per hour
    assert result.covered_kwh == pytest.approx(1.7 + 1.0 + 0.0)
    assert result.dumped_kwh == pytest.approx(0.0 + 0.7 + 1.7)
    assert result.compute_kwh == pytest.approx(6.0)
    assert result.savings_chf == pytest.approx(2.7 * 0.3325 / 2.5)


def test_follow_heat_saves_the_same_money_without_dumping():
    need = [4.0, 1.0, 0.0]
    flat = run_sites([need], 2.0, NO_TANK)
    follow = run_sites([need], 2.0, SitingParams(tank_kwh=0.0, follow_heat=True))
    assert follow.savings_chf == pytest.approx(flat.savings_chf)
    assert follow.dumped_kwh == pytest.approx(0)
    assert follow.compute_kwh + follow.paused_compute == pytest.approx(flat.compute_kwh)
    assert follow.bill_recovered == pytest.approx(0.85 / 2.5)


def test_tank_carries_surplus_into_a_later_hour():
    need = [0.0, 3.0]
    result = run_sites([need], 2.0, SitingParams(tank_kwh=35.0))
    check_conservation(result)
    assert result.sites[0].from_tank == pytest.approx(1.3)     # 1.7 stored, 1.3 still missing
    assert result.dumped_kwh == pytest.approx(0)


def test_compute_moves_to_the_site_that_needs_heat():
    first = [5.0] * 4 + [0.0] * 4        # cold, then warm
    second = [0.0] * 4 + [5.0] * 4       # the mirror image
    alone = run_sites([first], 4.0, NO_TANK)
    pair = run_sites([first, second], 4.0, NO_TANK)
    check_conservation(pair)
    assert alone.covered_kwh == pytest.approx(4 * 3.4)
    assert pair.covered_kwh == pytest.approx(8 * 3.4)          # nothing dumped any more
    assert pair.dumped_kwh == pytest.approx(0)
    assert pair.sites[0].compute == pytest.approx(16) and pair.sites[1].compute == pytest.approx(16)


def test_ranking_prefers_the_complementary_site():
    first = [5.0] * 4 + [0.0] * 4
    sites = {"first": first, "twin": list(first), "mirror": [0.0] * 4 + [5.0] * 4}
    ranking = rank_second_site(sites, "first", 4.0, NO_TANK)
    assert [score.name for score in ranking] == ["mirror", "twin"]
    assert ranking[0].gain_chf > 0 and ranking[1].gain_chf == pytest.approx(0)


def test_capacity_limits_what_a_site_can_host():
    result = run_sites([[10.0] * 2, [0.0] * 2], 6.0, NO_TANK, capacity_kw=2.0)
    assert result.sites[0].compute == pytest.approx(4.0)       # 2 kW for 2 hours
    assert result.unplaced_compute == pytest.approx(4.0)       # 6 kW asked, 4 kW of servers
