"""Unit tests for M1 conservation physics, 7 KPIs, ReferenceScheduler and adapter hook."""

import pytest

from digitalheat.config import (
    CONSERVATION_TOL_KWH,
    TANK_CAPACITY_KWH,
    JobSpec,
    SchedulerDecision,
)
from digitalheat.data_loader import load_weather_zurich
from digitalheat.physics import simulate_tank_year, step_basement_air_temp
from digitalheat.scheduler import (
    WORKLOAD_CATALOG,
    ExternalScheduleAdapter,
    ReferenceScheduler,
    RunOnArrivalBaseline,
    generate_dh_batch_jobs,
    run_annual_simulation,
)


@pytest.mark.parametrize("strategy", ["baseline", "smart_heat_led", "smart_sla"])
@pytest.mark.parametrize("s_start_kwh", [0.0, 17.5, 35.0])
def test_annual_and_hourly_conservation_across_strategies(
    strategy: str, s_start_kwh: float
) -> None:
    res = run_annual_simulation(
        strategy=strategy,
        n_servers=4,
        s_start_kwh=s_start_kwh,
        include_hourly=True,
    )
    assert len(res.hourly) == 8760
    assert res.conservation_residual_kwh <= CONSERVATION_TOL_KWH
    assert res.max_hourly_residual_kwh <= CONSERVATION_TOL_KWH

    for row in res.hourly:
        assert 0.0 <= row.tank_soc_kwh <= TANK_CAPACITY_KWH
        assert row.conservation_residual_kwh <= CONSERVATION_TOL_KWH

    lhs = 0.8 * res.kpis.electricity_consumed_kwh + res.s_start_kwh
    rhs = res.kpis.heat_used_kwh + res.kpis.heat_dumped_kwh + res.s_end_kwh
    assert abs(lhs - rhs) <= CONSERVATION_TOL_KWH


def test_smart_heat_led_eliminates_dump_and_beats_baseline_break_even() -> None:
    base = run_annual_simulation(strategy="baseline", n_servers=4, include_hourly=False)
    smart = run_annual_simulation(strategy="smart_heat_led", n_servers=4, include_hourly=False)

    assert base.kpis.heat_dumped_kwh > 3000.0
    assert smart.kpis.heat_dumped_kwh == pytest.approx(0.0, abs=1e-9)
    assert smart.kpis.heat_demand_covered_share > base.kpis.heat_demand_covered_share
    assert (
        smart.kpis.break_even_computing_price_chf_per_server_hour
        < base.kpis.break_even_computing_price_chf_per_server_hour
    )
    assert smart.kpis.break_even_computing_price_chf_per_server_hour == pytest.approx(
        0.2261, abs=1e-4
    )
    assert (
        "negative" not in smart.negative_payoff_statement.lower()
        or "loses money" in smart.negative_payoff_statement.lower()
    )
    assert "0.2261" in smart.negative_payoff_statement
    assert "3,816" in smart.negative_payoff_statement


def test_decide_live_step_matches_thermal_headroom_boundary() -> None:
    sched = ReferenceScheduler(sla_override=False)
    # Empty queue -> always pause
    assert (
        sched.decide_live_step(
            tank_soc_kwh=0.0, heat_demand_kw=5.0, outdoor_temp_c=0.0, queue_depth=0
        )
        is False
    )
    # 35.0 - 34.8 + 0.5 = 0.7 kWh < 0.8 kW_th -> pause
    assert (
        sched.decide_live_step(
            tank_soc_kwh=34.8, heat_demand_kw=0.5, outdoor_temp_c=20.0, queue_depth=3
        )
        is False
    )
    # 35.0 - 34.0 + 0.5 = 1.5 kWh >= 0.8 kW_th -> run 1 server
    assert (
        sched.decide_live_step(
            tank_soc_kwh=34.0, heat_demand_kw=0.5, outdoor_temp_c=20.0, queue_depth=3
        )
        is True
    )


def test_zero_server_hours_break_even_zero_division_guard() -> None:
    weather = load_weather_zurich(site="basel")
    idle_decisions = [
        SchedulerDecision(
            timestamp_zurich=h.timestamp_zurich,
            server_power_kw=0.0,
            active_servers=0,
            jobs_completed=0,
            jobs_missed_deadline=0,
            heat_signal_on=False,
        )
        for h in weather.hours
    ]
    res = simulate_tank_year(
        hours=weather.hours,
        decisions=idle_decisions,
        n_servers=4,
        strategy="all_idle",
        include_hourly=False,
    )
    assert res.kpis.server_hours_sold == 0.0
    assert res.kpis.break_even_defined is False
    assert res.kpis.break_even_computing_price_chf_per_server_hour == 0.0
    assert res.kpis.missed_deadlines_pct == 0.0


def test_lumped_basement_air_model_bounded_and_converges() -> None:
    t_idle = 12.0
    t_loaded = 12.0
    for _ in range(200):
        t_idle = step_basement_air_temp(t_idle, server_power_kw=0.0, outdoor_temp_c=10.0)
        t_loaded = step_basement_air_temp(t_loaded, server_power_kw=4.0, outdoor_temp_c=10.0)
    # Steady state at 10 C outdoor: T_eq=12 C; +0.2 * 4 / 0.12 = +6.67 K -> 18.67 C
    assert t_idle == pytest.approx(12.0, abs=1e-3)
    assert t_loaded == pytest.approx(12.0 + (0.2 * 4.0) / 0.12, abs=1e-3)


def test_workload_catalog_and_external_schedule_adapter_validation() -> None:
    assert {"archive_ocr", "iiif_embeddings", "audio_transcription"}.issubset(
        WORKLOAD_CATALOG.keys()
    )
    weather = load_weather_zurich(site="basel")
    jobs = generate_dh_batch_jobs(weather.hours, n_servers=4)
    assert len(jobs) > 10000

    # Valid compact external capacity schedule
    adapter = ExternalScheduleAdapter(hourly_active_servers=[2] * len(weather.hours))
    decs = adapter.schedule_year(weather.hours, jobs, n_servers=4)
    res = simulate_tank_year(
        hours=weather.hours,
        decisions=decs,
        n_servers=4,
        strategy="adapter",
        total_jobs_arrived=len(jobs),
        include_hourly=False,
    )
    assert res.conservation_residual_kwh <= CONSERVATION_TOL_KWH
    assert res.kpis.server_utilisation == pytest.approx(0.5, abs=0.02)

    # Reject wrong length or out-of-bounds server count
    bad_len = ExternalScheduleAdapter(hourly_active_servers=[1] * 100)
    with pytest.raises(ValueError, match="length"):
        bad_len.schedule_year(weather.hours, jobs, n_servers=4)

    bad_cap = ExternalScheduleAdapter(hourly_active_servers=[5] * len(weather.hours))
    with pytest.raises(ValueError, match="out of bounds"):
        bad_cap.schedule_year(weather.hours, jobs, n_servers=4)

    # Reject bool in hourly_active_servers and NaN in hourly_server_power_kw
    bad_bool = ExternalScheduleAdapter(hourly_active_servers=[True] * len(weather.hours))
    with pytest.raises(ValueError, match="integer"):
        bad_bool.schedule_year(weather.hours, jobs, n_servers=4)

    bad_nan_kw = ExternalScheduleAdapter(
        hourly_server_power_kw=[float("nan")] + [1.0] * (len(weather.hours) - 1)
    )
    with pytest.raises(ValueError, match="finite"):
        bad_nan_kw.schedule_year(weather.hours, jobs, n_servers=4)

    # Reject mismatched server_power_kw vs active_servers and negative job counts
    base_decs = adapter.schedule_year(weather.hours, jobs, n_servers=4)
    mismatch_decs = [d.model_copy() for d in base_decs]
    mismatch_decs[0] = mismatch_decs[0].model_copy(update={"server_power_kw": 3.0})
    with pytest.raises(ValueError, match="does not match"):
        ExternalScheduleAdapter(decisions=mismatch_decs).schedule_year(
            weather.hours, jobs, n_servers=4
        )

    neg_job_decs = [d.model_copy() for d in base_decs]
    neg_job_decs[0] = neg_job_decs[0].model_copy(update={"jobs_completed": -1})
    with pytest.raises(ValueError, match=">= 0"):
        ExternalScheduleAdapter(decisions=neg_job_decs).schedule_year(
            weather.hours, jobs, n_servers=4
        )

    nan_dec_list = [d.model_copy() for d in base_decs]
    nan_dec_list[0] = nan_dec_list[0].model_copy(update={"server_power_kw": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        ExternalScheduleAdapter(decisions=nan_dec_list).schedule_year(
            weather.hours, jobs, n_servers=4
        )

    # run_annual_simulation strategy='adapter' integration
    sim_adp = run_annual_simulation(
        strategy="adapter",
        n_servers=4,
        include_hourly=False,
        adapter=adapter,
    )
    assert sim_adp.conservation_residual_kwh <= CONSERVATION_TOL_KWH
    assert sim_adp.kpis.server_utilisation == pytest.approx(0.5, abs=0.02)
    with pytest.raises(ValueError, match="non-None adapter"):
        run_annual_simulation(strategy="adapter", n_servers=4, include_hourly=False)


@pytest.mark.parametrize("n_servers", [1, 2, 4, 8, 12, 16, 20])
def test_smart_sla_zero_missed_deadlines_across_cluster_sizes(n_servers: int) -> None:
    res = run_annual_simulation(strategy="smart_sla", n_servers=n_servers, include_hourly=False)
    assert res.kpis.total_jobs_missed == 0
    assert res.kpis.missed_deadlines_pct == 0.0
    assert res.conservation_residual_kwh <= CONSERVATION_TOL_KWH


@pytest.mark.parametrize("offered", [6.0, 12.0])
def test_smart_sla_zero_missed_deadlines_with_audio_jobs(offered: float) -> None:
    weather = load_weather_zurich(site="basel")
    jobs = generate_dh_batch_jobs(
        weather.hours,
        n_servers=4,
        offered_hours_per_server_per_day=offered,
        include_audio=True,
    )
    decs, qe = ReferenceScheduler(sla_override=True).schedule_year_with_queue_end(
        weather.hours, jobs, 4
    )
    res = simulate_tank_year(
        weather.hours,
        decs,
        4,
        total_jobs_arrived=len(jobs),
        jobs_in_queue_year_end=qe,
        include_hourly=False,
    )
    assert res.kpis.total_jobs_missed == 0
    assert res.conservation_residual_kwh <= CONSERVATION_TOL_KWH


def test_nan_inf_and_negative_input_rejections() -> None:
    weather = load_weather_zurich(site="basel")
    zero_decs = [
        SchedulerDecision(
            timestamp_zurich=h.timestamp_zurich,
            server_power_kw=0.0,
            active_servers=0,
            jobs_completed=0,
            jobs_missed_deadline=0,
            heat_signal_on=False,
        )
        for h in weather.hours
    ]

    with pytest.raises(ValueError, match="finite"):
        step_basement_air_temp(float("nan"), 1.0, 10.0)
    with pytest.raises(ValueError, match="finite"):
        generate_dh_batch_jobs(weather.hours, 4, offered_hours_per_server_per_day=float("inf"))

    for bad_kwargs in [
        {"s_start_kwh": float("nan")},
        {"tank_capacity_kwh": -1.0},
        {"heat_recovery_fraction": 1.5},
        {"electricity_price_chf": -0.1},
        {"heat_pump_cop": 0.0},
        {"computing_price_chf_per_server_h": float("inf")},
        {"extra_sink_kw": [-0.1] + [0.0] * (len(weather.hours) - 1)},
        {"primary_demand_override_kw": [float("nan")] + [1.0] * (len(weather.hours) - 1)},
    ]:
        with pytest.raises(ValueError):
            simulate_tank_year(
                weather.hours, zero_decs, n_servers=4, include_hourly=False, **bad_kwargs
            )

    sched = ReferenceScheduler(sla_override=False)
    for soc, dem, tout in [
        (float("nan"), 1.0, 10.0),
        (-0.5, 1.0, 10.0),
        (40.0, 1.0, 10.0),
        (10.0, -1.0, 10.0),
        (10.0, float("inf"), 10.0),
        (10.0, 1.0, float("nan")),
    ]:
        with pytest.raises(ValueError):
            sched.decide_live_step(soc, dem, tout, queue_depth=0)
        with pytest.raises(ValueError):
            ExternalScheduleAdapter(hourly_active_servers=[0]).decide_live_step(
                soc, dem, tout, queue_depth=0
            )


def test_davos_negative_payoff_statement_uses_site_non_heating_hours() -> None:
    res = run_annual_simulation(
        strategy="smart_heat_led", n_servers=4, site="davos", include_hourly=False
    )
    assert "Davos" in res.negative_payoff_statement
    assert "1,776" in res.negative_payoff_statement
    assert "Basel" not in res.negative_payoff_statement


def test_smart_sla_july_full_tank_burst_zero_missed_deadlines() -> None:
    weather = load_weather_zurich(site="basel")
    july_slice = weather.hours[4700:4748]
    burst_jobs = [
        JobSpec(
            job_id=i + 1,
            job_type="archive_ocr",
            arrival_hour=0,
            duration_hours=1,
            remaining_hours=1,
            deadline_hour=24,
            interruptible=True,
        )
        for i in range(24)
    ]
    sched = ReferenceScheduler(sla_override=True, s_start_kwh=35.0)
    decs, qe = sched.schedule_year_with_queue_end(july_slice, burst_jobs, n_servers=1)
    assert sum(d.jobs_missed_deadline for d in decs) == 0
    assert sum(d.jobs_completed for d in decs) == 24
    assert qe == 0

    res = simulate_tank_year(
        july_slice,
        decs,
        n_servers=1,
        strategy="smart_sla",
        s_start_kwh=35.0,
        total_jobs_arrived=24,
        jobs_in_queue_year_end=qe,
        include_hourly=False,
    )
    assert res.kpis.total_jobs_missed == 0
    assert res.conservation_residual_kwh <= CONSERVATION_TOL_KWH


def test_defense_in_depth_scheduler_and_physics_validation_guards() -> None:
    weather = load_weather_zurich(site="basel")
    baseline = RunOnArrivalBaseline()

    for bad_n in (0, -1, True):
        with pytest.raises(ValueError, match="n_servers must be >= 1"):
            baseline.schedule_year_with_queue_end(weather.hours[:10], [], bad_n)

    for soc, dem, tout in [
        (-0.5, 1.0, 10.0),
        (40.0, 1.0, 10.0),
        (10.0, -1.0, 10.0),
    ]:
        with pytest.raises(ValueError):
            baseline.decide_live_step(soc, dem, tout, queue_depth=1)

    with pytest.raises(ValueError, match="bool"):
        ExternalScheduleAdapter(hourly_server_power_kw=[True] * len(weather.hours)).schedule_year(
            weather.hours, [], n_servers=4
        )
    with pytest.raises(ValueError, match="integer multiple"):
        ExternalScheduleAdapter(hourly_server_power_kw=[0.5] * len(weather.hours)).schedule_year(
            weather.hours, [], n_servers=4
        )

    with pytest.raises(ValueError, match=">= 0"):
        step_basement_air_temp(12.0, -1.0, 10.0)

    # simulate_tank_year still allows continuous fractional server_power_kw
    cont_decs = [
        SchedulerDecision(
            timestamp_zurich=h.timestamp_zurich,
            server_power_kw=0.5,
            active_servers=0,
            jobs_completed=0,
            jobs_missed_deadline=0,
            heat_signal_on=True,
        )
        for h in weather.hours
    ]
    cont_res = simulate_tank_year(weather.hours, cont_decs, n_servers=4, include_hourly=False)
    assert cont_res.conservation_residual_kwh <= CONSERVATION_TOL_KWH

    for field in ("jobs_completed", "jobs_missed_deadline"):
        bad_decs = list(cont_decs)
        bad_decs[0] = bad_decs[0].model_copy(update={field: -1})
        with pytest.raises(ValueError, match=">= 0"):
            simulate_tank_year(weather.hours, bad_decs, n_servers=4, include_hourly=False)


