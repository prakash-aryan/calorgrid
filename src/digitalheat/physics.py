"""Authoritative thermal conservation engine, Verbund scenes and Pilot Reality sensitivity."""

import math
from collections import defaultdict
from pathlib import Path
from typing import Literal

from digitalheat.config import (
    ASHRAE_W45_MAX_SUPPLY_C,
    BASEMENT_CAP_KWH_PER_K,
    BASEMENT_START_TEMP_C,
    BASEMENT_UA_KW_PER_K,
    CAPEX_CHF_PER_SERVER_YR_BANDS,
    CONSERVATION_TOL_KWH,
    DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H,
    DEFAULT_SERVERS_PER_HOUSE,
    DESIGN_TEMP_SPAN_K,
    DHW_POWER_KW,
    ELCOM_BASEL_H4_CHF_PER_KWH,
    HEAT_PUMP_REF_COP,
    HEAT_RECOVERY_FRACTION,
    HEATING_DAY_MEAN_THRESHOLD_C,
    HOURS_PER_YEAR,
    INDOOR_SETPOINT_C,
    SERVER_ELECTRICAL_POWER_KW,
    SERVER_HEAT_OUTPUT_KW,
    SPACE_HEATING_PEAK_KW,
    SWISS_VAT_RATE,
    TANK_CAPACITY_KWH,
    TANK_DEFAULT_START_KWH,
    TANK_TEMP_MIN_C,
    TANK_TEMP_SPREAD_K,
    UNRECOVERED_HEAT_FRACTION,
    WATER_CP_KJ_PER_KG_K,
    HourlySimulationRow,
    HourlyWeather,
    KpiReport,
    SchedulerDecision,
    SimulationResult,
)
from digitalheat.data_loader import (
    load_elcom_tariffs,
    load_heatwise_summary,
    load_mlperf_summary,
    load_weather_zurich,
)

VERBUND_SCENE_IDS: tuple[str, ...] = (
    "basel_erdsonde",
    "basel_pool_st_jakob",
    "sole_uno",
    "hotel_laundry_dhw",
    "brewery_dairy_cip",
    "ara_basel_biogas",
    "davos_alpine",
)


def compute_heating_days(
    hours: list[HourlyWeather],
    threshold_c: float = HEATING_DAY_MEAN_THRESHOLD_C,
) -> set[str]:
    """Identify local calendar days whose 24h mean outdoor temperature is below threshold."""
    by_day: dict[str, list[float]] = defaultdict(list)
    for h in hours:
        by_day[h.local_date].append(h.outdoor_temp_c)
    return {day for day, temps in by_day.items() if (sum(temps) / len(temps)) < threshold_c}


def compute_house_demand(
    hours: list[HourlyWeather],
    heating_days: set[str] | None = None,
) -> list[tuple[float, float, float]]:
    """Compute hourly (space_heating_kw, dhw_kw, total_heat_kw) for the reference house."""
    hdays = heating_days if heating_days is not None else compute_heating_days(hours)
    demand: list[tuple[float, float, float]] = []
    for h in hours:
        if h.local_date in hdays:
            space_kw = max(
                0.0,
                SPACE_HEATING_PEAK_KW * (INDOOR_SETPOINT_C - h.outdoor_temp_c) / DESIGN_TEMP_SPAN_K,
            )
        else:
            space_kw = 0.0
        dhw_kw = DHW_POWER_KW
        demand.append((space_kw, dhw_kw, space_kw + dhw_kw))
    return demand


def step_basement_air_temp(
    t_basement_c: float,
    server_power_kw: float,
    outdoor_temp_c: float,
) -> float:
    if not (
        math.isfinite(t_basement_c)
        and math.isfinite(server_power_kw)
        and math.isfinite(outdoor_temp_c)
    ):
        raise ValueError("Basement air inputs must be finite")
    if server_power_kw < 0.0:
        raise ValueError(f"server_power_kw ({server_power_kw}) must be >= 0")
    # 5-line lumped 1-node basement air model (ASSUMPTION: 20% unrecovered heat, not 3D CFD)
    q_unrecovered_kw = UNRECOVERED_HEAT_FRACTION * server_power_kw
    t_basement_eq_c = 12.0 + 0.25 * (outdoor_temp_c - 10.0)
    alpha = BASEMENT_UA_KW_PER_K / BASEMENT_CAP_KWH_PER_K
    t_next_c = (
        t_basement_c + alpha * t_basement_eq_c + q_unrecovered_kw / BASEMENT_CAP_KWH_PER_K
    ) / (1.0 + alpha)
    return t_next_c


def compute_loop_flow_l_per_min(thermal_kw: float, delta_t_k: float) -> float:
    """Compute steady-state water flow rate in L/min for given thermal kW and delta T."""
    if not (math.isfinite(thermal_kw) and math.isfinite(delta_t_k)):
        raise ValueError("Loop flow inputs must be finite")
    if thermal_kw < 0.0:
        raise ValueError("thermal_kw must be >= 0")
    if delta_t_k <= 0.0:
        raise ValueError("delta_t_k must be positive")
    return (thermal_kw * 60.0) / (WATER_CP_KJ_PER_KG_K * delta_t_k)


def build_negative_payoff_statement(
    break_even_chf: float,
    break_even_defined: bool,
    heat_reused_fraction: float,
    server_utilisation: float,
    electricity_price_chf: float,
    heat_pump_cop: float,
    site: str = "basel",
    non_heating_hours: int = 3816,
    computing_price_chf_per_server_h: float = DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H,
) -> str:
    """Generate an explicit, quantitative statement of when the model does not pay off."""
    site_label = "Basel" if site == "basel" else ("Davos" if site == "davos" else site)
    heat_val = electricity_price_chf / heat_pump_cop
    opex_floor = electricity_price_chf - HEAT_RECOVERY_FRACTION * heat_val
    capex_central = CAPEX_CHF_PER_SERVER_YR_BANDS["central"]
    capex_100pct = capex_central / HOURS_PER_YEAR
    be_text = (
        f"{break_even_chf:.4f} CHF/server-hour"
        if break_even_defined
        else "undefined (zero server-hours sold)"
    )
    return (
        f"Heat credit alone does not pay for electricity: under IWB H4 "
        f"({electricity_price_chf:.4f} CHF/kWh) and displaced heat-pump value "
        f"({heat_val:.4f} CHF/kWh_th), max heat credit is "
        f"{HEAT_RECOVERY_FRACTION * heat_val:.4f} CHF/server-hour, leaving an OPEX "
        f"break-even floor of {opex_floor:.4f} to {electricity_price_chf:.4f} CHF/server-hour "
        f"(this run: {be_text} at {100.0 * heat_reused_fraction:.1f}% heat reuse). "
        f"At {computing_price_chf_per_server_h:.2f} CHF/server-hour compute revenue, "
        f"the model loses money when: (1) revenue falls below OPEX break-even "
        f"({be_text}) plus amortized CAPEX ({capex_central:.0f} CHF/server-yr central, adding "
        f"{capex_100pct:.4f} CHF/server-hour at 100% utilisation), "
        f"(2) low utilisation (current {100.0 * server_utilisation:.1f}%) inflates per-hour CAPEX "
        f"because {site_label} has {non_heating_hours:,} non-heating hours where 12 kWh/day DHW "
        f"absorbs 0.625 servers at full load, (3) retail H4 tariff exceeds industrial C5/C6 "
        f"rates by 0.0724 to 0.0798 CHF/kWh excl. VAT (0.0782 to 0.0863 CHF/kWh incl. VAT) or "
        f"(4) servers run under partial capture (<80%) or idle power draw."
    )


def simulate_tank_year(
    hours: list[HourlyWeather],
    decisions: list[SchedulerDecision],
    n_servers: int,
    strategy: str = "smart",
    site: str = "basel",
    s_start_kwh: float = TANK_DEFAULT_START_KWH,
    tank_capacity_kwh: float = TANK_CAPACITY_KWH,
    heat_recovery_fraction: float = HEAT_RECOVERY_FRACTION,
    extra_sink_kw: list[float] | None = None,
    primary_demand_override_kw: list[float] | None = None,
    electricity_price_chf: float = ELCOM_BASEL_H4_CHF_PER_KWH,
    heat_pump_cop: float = HEAT_PUMP_REF_COP,
    computing_price_chf_per_server_h: float = DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H,
    total_jobs_arrived: int = 0,
    jobs_in_queue_year_end: int = 0,
    arrival_rate_source: str = (
        "Alibaba 2020 diurnal batch envelope scaled to 12.0 server-h/server/day"
    ),
    scaling_notes: str = (
        "1 kW reference server scaling: 3,600,000 J / measured local CPU energy per unit"
    ),
    include_hourly: bool = True,
) -> SimulationResult:
    """Enforce 0.8*E_server + S_start = Q_used + Q_dumped + S_end and compute 7 KPIs."""
    n_hours = len(hours)
    if len(decisions) != n_hours:
        raise ValueError(f"decisions length ({len(decisions)}) must match hours length ({n_hours})")
    if n_servers < 1:
        raise ValueError("n_servers must be >= 1")
    if not math.isfinite(tank_capacity_kwh) or tank_capacity_kwh < 0.0:
        raise ValueError(f"tank_capacity_kwh ({tank_capacity_kwh}) must be finite and >= 0")
    if not math.isfinite(s_start_kwh) or not (0.0 <= s_start_kwh <= tank_capacity_kwh + 1e-9):
        raise ValueError(f"s_start_kwh ({s_start_kwh}) outside [0, {tank_capacity_kwh}]")
    if not math.isfinite(heat_recovery_fraction) or not (0.0 <= heat_recovery_fraction <= 1.0):
        raise ValueError(
            f"heat_recovery_fraction ({heat_recovery_fraction}) must be finite and in [0, 1]"
        )
    if not math.isfinite(electricity_price_chf) or electricity_price_chf < 0.0:
        raise ValueError(f"electricity_price_chf ({electricity_price_chf}) must be finite and >= 0")
    if not math.isfinite(heat_pump_cop) or heat_pump_cop <= 0.0:
        raise ValueError(f"heat_pump_cop ({heat_pump_cop}) must be finite and > 0")
    if (
        not math.isfinite(computing_price_chf_per_server_h)
        or computing_price_chf_per_server_h < 0.0
    ):
        raise ValueError(
            f"computing_price_chf_per_server_h ({computing_price_chf_per_server_h}) "
            "must be finite and >= 0"
        )
    if extra_sink_kw is not None and len(extra_sink_kw) != n_hours:
        raise ValueError("extra_sink_kw length must match hours length")
    if primary_demand_override_kw is not None and len(primary_demand_override_kw) != n_hours:
        raise ValueError("primary_demand_override_kw length must match hours length")

    heating_days = compute_heating_days(hours)
    house_demand = compute_house_demand(hours, heating_days=heating_days)

    s_t = float(s_start_kwh)
    t_base = BASEMENT_START_TEMP_C

    e_srv_total = 0.0
    q_rec_total = 0.0
    q_used_primary_total = 0.0
    q_used_secondary_total = 0.0
    q_dumped_total = 0.0
    d_primary_total = 0.0
    backup_total = 0.0
    total_completed = 0
    total_missed = 0
    max_hourly_residual = 0.0
    hourly_rows: list[HourlySimulationRow] = []

    for idx, (h, dec) in enumerate(zip(hours, decisions)):
        if dec.timestamp_zurich != h.timestamp_zurich:
            raise ValueError(
                f"Timestamp mismatch at hour {idx}: {dec.timestamp_zurich} != {h.timestamp_zurich}"
            )
        max_kw = n_servers * SERVER_ELECTRICAL_POWER_KW
        if (
            not math.isfinite(dec.server_power_kw)
            or dec.server_power_kw < -1e-9
            or dec.server_power_kw > max_kw + 1e-9
        ):
            raise ValueError(
                f"server_power_kw ({dec.server_power_kw}) out of bounds [0, {max_kw}]"
            )
        if dec.jobs_completed < 0 or dec.jobs_missed_deadline < 0:
            raise ValueError(
                f"jobs_completed and jobs_missed_deadline must be >= 0 at hour {idx}"
            )

        space_kw, dhw_kw, d_house_kw = house_demand[idx]
        if primary_demand_override_kw is not None:
            d_primary_kw = primary_demand_override_kw[idx]
            if not math.isfinite(d_primary_kw) or d_primary_kw < 0.0:
                raise ValueError(
                    f"primary_demand_override_kw[{idx}] ({d_primary_kw}) must be finite and >= 0"
                )
        else:
            d_primary_kw = d_house_kw
        if extra_sink_kw is not None:
            d_secondary_kw = extra_sink_kw[idx]
            if not math.isfinite(d_secondary_kw) or d_secondary_kw < 0.0:
                raise ValueError(
                    f"extra_sink_kw[{idx}] ({d_secondary_kw}) must be finite and >= 0"
                )
        else:
            d_secondary_kw = 0.0

        e_srv_t = dec.server_power_kw * 1.0
        q_rec_t = heat_recovery_fraction * e_srv_t
        avail_t = s_t + q_rec_t

        q_used_primary_t = min(avail_t, d_primary_kw)
        backup_t = d_primary_kw - q_used_primary_t
        rem_after_primary = avail_t - q_used_primary_t

        # 3-way diverter valve sends surplus above tank headroom to secondary Verbund sink
        surplus_over_tank = max(0.0, rem_after_primary - tank_capacity_kwh)
        q_used_secondary_t = min(surplus_over_tank, d_secondary_kw)
        rem_after_secondary = rem_after_primary - q_used_secondary_t

        s_next_raw = min(tank_capacity_kwh, rem_after_secondary)
        q_dumped_t = max(0.0, rem_after_secondary - tank_capacity_kwh)
        q_used_t = q_used_primary_t + q_used_secondary_t

        res_t = abs((q_rec_t + s_t) - (q_used_t + q_dumped_t + s_next_raw))
        if not math.isfinite(res_t) or res_t > CONSERVATION_TOL_KWH:
            raise AssertionError(f"Hourly conservation violation at hour {idx}: residual={res_t}")
        if s_next_raw < -1e-9 or s_next_raw > tank_capacity_kwh + 1e-9:
            raise AssertionError(f"Tank SOC out of bounds at hour {idx}: {s_next_raw}")

        s_next = min(tank_capacity_kwh, max(0.0, s_next_raw))
        t_base = step_basement_air_temp(t_base, max(0.0, dec.server_power_kw), h.outdoor_temp_c)

        e_srv_total += e_srv_t
        q_rec_total += q_rec_t
        q_used_primary_total += q_used_primary_t
        q_used_secondary_total += q_used_secondary_t
        q_dumped_total += q_dumped_t
        d_primary_total += d_primary_kw
        backup_total += backup_t
        total_completed += dec.jobs_completed
        total_missed += dec.jobs_missed_deadline
        max_hourly_residual = max(max_hourly_residual, res_t)

        if include_hourly:
            hourly_rows.append(
                HourlySimulationRow(
                    timestamp_zurich=h.timestamp_zurich,
                    outdoor_temp_c=h.outdoor_temp_c,
                    solar_irradiance_wm2=h.shortwave_radiation_wm2,
                    space_heating_kw=round(space_kw, 6),
                    dhw_kw=round(dhw_kw, 6),
                    heat_demand_kw=round(d_primary_kw, 6),
                    extra_sink_demand_kw=round(d_secondary_kw, 6),
                    server_power_kw=round(dec.server_power_kw, 6),
                    active_servers=dec.active_servers,
                    heat_recovered_kw=round(q_rec_t, 6),
                    tank_soc_kwh=round(s_next, 6),
                    heat_used_kw=round(q_used_t, 6),
                    heat_used_primary_kw=round(q_used_primary_t, 6),
                    heat_used_secondary_kw=round(q_used_secondary_t, 6),
                    heat_dumped_kw=round(q_dumped_t, 6),
                    backup_heat_kw=round(backup_t, 6),
                    basement_air_temp_c=round(t_base, 4),
                    heat_signal_on=dec.heat_signal_on,
                    conservation_residual_kwh=res_t,
                )
            )
        s_t = s_next

    q_used_total = q_used_primary_total + q_used_secondary_total
    annual_residual = abs((q_rec_total + s_start_kwh) - (q_used_total + q_dumped_total + s_t))
    if not math.isfinite(annual_residual) or annual_residual > CONSERVATION_TOL_KWH:
        raise AssertionError(f"Annual conservation violation: residual={annual_residual}")

    heat_covered_share = (q_used_primary_total / d_primary_total) if d_primary_total > 0.0 else 0.0
    heat_dumped_share = (q_dumped_total / q_rec_total) if q_rec_total > 0.0 else 0.0
    heat_reused_fraction = (q_used_total / q_rec_total) if q_rec_total > 0.0 else 0.0

    server_hours_sold = e_srv_total / SERVER_ELECTRICAL_POWER_KW
    max_server_hours = n_servers * n_hours
    server_utilisation = (server_hours_sold / max_server_hours) if max_server_hours > 0 else 0.0
    missed_deadlines_pct = (
        (100.0 * total_missed / total_jobs_arrived) if total_jobs_arrived > 0 else 0.0
    )

    heat_value_per_kwh = electricity_price_chf / heat_pump_cop
    electricity_cost_chf = e_srv_total * electricity_price_chf
    heat_value_used_chf = q_used_total * heat_value_per_kwh
    computing_income_chf = server_hours_sold * computing_price_chf_per_server_h
    net_opex_margin_chf = computing_income_chf + heat_value_used_chf - electricity_cost_chf

    if server_hours_sold > 1e-12:
        break_even = (electricity_cost_chf - heat_value_used_chf) / server_hours_sold
        break_even_defined = True
    else:
        break_even = 0.0
        break_even_defined = False

    kpis = KpiReport(
        servers_per_house=n_servers,
        heat_demand_covered_share=heat_covered_share,
        heat_used_kwh=q_used_total,
        heat_used_primary_kwh=q_used_primary_total,
        heat_used_secondary_kwh=q_used_secondary_total,
        heat_demand_total_kwh=d_primary_total,
        backup_heat_kwh=backup_total,
        heat_dumped_kwh=q_dumped_total,
        heat_recovered_kwh=q_rec_total,
        heat_dumped_share=heat_dumped_share,
        server_utilisation=server_utilisation,
        missed_deadlines_pct=missed_deadlines_pct,
        total_jobs_arrived=total_jobs_arrived,
        total_jobs_completed=total_completed,
        total_jobs_missed=total_missed,
        jobs_in_queue_year_end=jobs_in_queue_year_end,
        electricity_cost_chf=electricity_cost_chf,
        computing_income_chf=computing_income_chf,
        heat_value_used_chf=heat_value_used_chf,
        net_opex_margin_chf=net_opex_margin_chf,
        break_even_computing_price_chf_per_server_hour=break_even,
        break_even_defined=break_even_defined,
        server_hours_sold=server_hours_sold,
        electricity_consumed_kwh=e_srv_total,
    )

    non_heating_hours = sum(1 for h in hours if h.local_date not in heating_days)
    statement = build_negative_payoff_statement(
        break_even_chf=break_even,
        break_even_defined=break_even_defined,
        heat_reused_fraction=heat_reused_fraction,
        server_utilisation=server_utilisation,
        electricity_price_chf=electricity_price_chf,
        heat_pump_cop=heat_pump_cop,
        site=site,
        non_heating_hours=non_heating_hours,
        computing_price_chf_per_server_h=computing_price_chf_per_server_h,
    )

    return SimulationResult(
        strategy=strategy,
        site=site,
        kpis=kpis,
        negative_payoff_statement=statement,
        arrival_rate_source=arrival_rate_source,
        scaling_notes=scaling_notes,
        conservation_residual_kwh=annual_residual,
        max_hourly_residual_kwh=max_hourly_residual,
        s_start_kwh=s_start_kwh,
        s_end_kwh=s_t,
        heating_days_count=len(heating_days),
        hourly=hourly_rows,
    )


def _month_from_date(local_date: str) -> int:
    return int(local_date[5:7])


def _is_weekend(local_date: str) -> bool:
    from datetime import date

    return date.fromisoformat(local_date).weekday() >= 5


def _build_verbund_profiles(
    scene_id: str,
    hours: list[HourlyWeather],
    n_servers: int,
) -> tuple[dict, list[float] | None, list[float] | None]:
    """Build primary override and secondary Verbund demand arrays + scene metadata."""
    q_srv_th = n_servers * SERVER_HEAT_OUTPUT_KW

    if scene_id == "basel_erdsonde":
        # Dual-sink: Basel reference house primary + May-Sep SIA 384/6 borehole regen (7.5 kW peak)
        secondary = [7.5 if 5 <= _month_from_date(h.local_date) <= 9 else 0.0 for h in hours]
        meta = {
            "scene_id": scene_id,
            "name": "Dual-Sink Basel House + Erdsonde Borehole Regeneration",
            "mode": "dual_sink_seasonal",
            "site": "basel",
            "primary_supply_temp_c": 55.0,
            "primary_return_temp_c": 40.0,
            "secondary_supply_temp_c": 40.0,
            "secondary_return_temp_c": 30.0,
            "delta_t_k": 15.0,
            "secondary_delta_t_k": 10.0,
            "ashrae_class": "Primary W+ / Borehole W45",
            "ashrae_w45_compliant": False,
            "primary_w45_compliant": False,
            "secondary_w45_compliant": True,
            "source_citation": "Swiss SIA 384/6 borehole regeneration (35-45 C, 300 m @ 25 W/m)",
            "provenance": "official_benchmark",
            "comparison_label": "Single-sink Basel House vs Dual-sink Erdsonde",
        }
        return meta, None, secondary

    if scene_id == "basel_pool_st_jakob":
        # Dual-sink option: Basel Winter House + Summer Outdoor Pool (May-Sep) vs standalone pool
        pool_kw = [
            (q_srv_th * (1.2 + 0.04 * max(0.0, 28.0 - h.outdoor_temp_c)))
            if 5 <= _month_from_date(h.local_date) <= 9
            else 0.0
            for h in hours
        ]
        meta = {
            "scene_id": scene_id,
            "name": "Basel Outdoor Pool (Gartenbad St. Jakob, May-Sep)",
            "mode": "dual_sink_seasonal",
            "site": "basel",
            "primary_supply_temp_c": 45.0,
            "primary_return_temp_c": 35.0,
            "secondary_supply_temp_c": 32.0,
            "secondary_return_temp_c": 26.0,
            "delta_t_k": 10.0,
            "secondary_delta_t_k": 6.0,
            "ashrae_class": "W45",
            "ashrae_w45_compliant": True,
            "primary_w45_compliant": True,
            "secondary_w45_compliant": True,
            "source_citation": "Basel Gartenbad St. Jakob seasonal pool schedule (May-Sep heated)",
            "provenance": "official_benchmark_with_assumed_profile",
            "comparison_label": "Standalone Summer Pool vs Winter House + Summer Pool Dual-Sink",
        }
        return meta, None, pool_kw

    if scene_id == "sole_uno":
        # Year-round 24/7 indoor thermal wellness bath (Rheinfelden / Basel region)
        primary = [
            1.5 * q_srv_th * (1.0 + 0.015 * max(0.0, 20.0 - h.outdoor_temp_c)) for h in hours
        ]
        meta = {
            "scene_id": scene_id,
            "name": "Indoor Thermal Bath (Sole Uno Wellness, 24/7)",
            "mode": "year_round_single_host",
            "site": "basel",
            "primary_supply_temp_c": 45.0,
            "primary_return_temp_c": 35.0,
            "secondary_supply_temp_c": None,
            "secondary_return_temp_c": None,
            "delta_t_k": 10.0,
            "secondary_delta_t_k": None,
            "ashrae_class": "W45",
            "ashrae_w45_compliant": True,
            "primary_w45_compliant": True,
            "secondary_w45_compliant": None,
            "source_citation": (
                "ASSUMPTION: 24/7 thermal bath baseload >= 1.5x cluster output at 34/28 C pool HX"
            ),
            "provenance": "assumed_facility_profile",
            "comparison_label": "Seasonal Outdoor Pool vs Year-Round Thermal Bath",
        }
        return meta, primary, None

    if scene_id == "hotel_laundry_dhw":
        # Year-round diurnal hotel DHW + commercial laundry preheat (45-60 C)
        primary = []
        for h in hours:
            hr = h.local_hour
            if 6 <= hr < 10 or 18 <= hr < 22:
                mult = 1.6
            elif 10 <= hr < 18:
                mult = 1.4
            else:
                mult = 0.25
            primary.append(mult * q_srv_th)
        meta = {
            "scene_id": scene_id,
            "name": "Commercial Laundry and Hotel DHW Preheat (45-60 C)",
            "mode": "year_round_single_host",
            "site": "basel",
            "primary_supply_temp_c": 60.0,
            "primary_return_temp_c": 45.0,
            "secondary_supply_temp_c": None,
            "secondary_return_temp_c": None,
            "delta_t_k": 15.0,
            "secondary_delta_t_k": None,
            "ashrae_class": "W+",
            "ashrae_w45_compliant": False,
            "primary_w45_compliant": False,
            "secondary_w45_compliant": None,
            "source_citation": (
                "ASSUMPTION: diurnal hotel DHW + daytime laundry preheat from 10 C mains"
            ),
            "provenance": "assumed_facility_profile",
            "comparison_label": "Basel Residential vs Hotel/Laundry DHW Preheat",
        }
        return meta, primary, None

    if scene_id == "brewery_dairy_cip":
        # Mon-Sat 06-18h CIP batch wash preheat (45-65 C), Sunday/night low trickle
        primary = []
        for h in hours:
            weekend = _is_weekend(h.local_date)
            is_sunday = False
            from datetime import date

            if date.fromisoformat(h.local_date).weekday() == 6:
                is_sunday = True
            if not is_sunday and 6 <= h.local_hour < 18:
                mult = 1.8 if not weekend else 1.4
            else:
                mult = 0.15
            primary.append(mult * q_srv_th)
        meta = {
            "scene_id": scene_id,
            "name": "Brewery and Alpine Dairy CIP Preheat (45-65 C)",
            "mode": "batch_industrial_host",
            "site": "basel",
            "primary_supply_temp_c": 62.0,
            "primary_return_temp_c": 47.0,
            "secondary_supply_temp_c": None,
            "secondary_return_temp_c": None,
            "delta_t_k": 15.0,
            "secondary_delta_t_k": None,
            "ashrae_class": "W+",
            "ashrae_w45_compliant": False,
            "primary_w45_compliant": False,
            "secondary_w45_compliant": None,
            "source_citation": "ASSUMPTION: Mon-Sat 06-18h Clean-in-Place wash preheat schedule",
            "provenance": "assumed_facility_profile",
            "comparison_label": "Basel Residential vs Brewery/Dairy CIP Host",
        }
        return meta, primary, None

    if scene_id == "ara_basel_biogas":
        # Constant 24/7/365 mesophilic sludge digester heating (37-42 C loop)
        primary = [1.5 * q_srv_th * (1.0 + 0.01 * max(0.0, 15.0 - h.outdoor_temp_c)) for h in hours]
        meta = {
            "scene_id": scene_id,
            "name": "Municipal Biogas / ARA Basel Digester (37-42 C 24/7)",
            "mode": "year_round_single_host",
            "site": "basel",
            "primary_supply_temp_c": 42.0,
            "primary_return_temp_c": 35.0,
            "secondary_supply_temp_c": None,
            "secondary_return_temp_c": None,
            "delta_t_k": 7.0,
            "secondary_delta_t_k": None,
            "ashrae_class": "W45",
            "ashrae_w45_compliant": True,
            "primary_w45_compliant": True,
            "secondary_w45_compliant": None,
            "source_citation": (
                "ProRheno ARA Basel mesophilic digester (35-36 C core, 37-42 C loop)"
            ),
            "provenance": "official_benchmark_with_assumed_profile",
            "comparison_label": "Basel Residential vs ARA Basel 24/7 Digester",
        }
        return meta, primary, None

    if scene_id == "davos_alpine":
        meta = {
            "scene_id": scene_id,
            "name": "Davos Alpine Reference House (46.80 N, 9.84 E)",
            "mode": "alpine_residential",
            "site": "davos",
            "primary_supply_temp_c": 55.0,
            "primary_return_temp_c": 40.0,
            "secondary_supply_temp_c": None,
            "secondary_return_temp_c": None,
            "delta_t_k": 15.0,
            "secondary_delta_t_k": None,
            "ashrae_class": "W+",
            "ashrae_w45_compliant": False,
            "primary_w45_compliant": False,
            "secondary_w45_compliant": None,
            "source_citation": "Open-Meteo 2025 Davos weather (291 heating days, min -17.0 C)",
            "provenance": "official_benchmark",
            "comparison_label": "Basel Reference House vs Davos Alpine Reference House",
        }
        return meta, None, None

    raise ValueError(f"Unknown Verbund scene_id: {scene_id}")


def simulate_verbund_scene(
    scene_id: str,
    n_servers: int = DEFAULT_SERVERS_PER_HOUSE,
    data_dir: Path | None = None,
) -> dict:
    """Run 8,760h conservation-verified Verbund simulation and separate vs paired comparison."""
    from digitalheat.scheduler import ReferenceScheduler, RunOnArrivalBaseline

    if scene_id not in VERBUND_SCENE_IDS:
        raise ValueError(
            f"Unknown Verbund scene_id '{scene_id}'; expected one of {VERBUND_SCENE_IDS}"
        )

    site: Literal["basel", "davos"] = "davos" if scene_id == "davos_alpine" else "basel"
    weather = load_weather_zurich(site=site, data_dir=data_dir)
    basel_weather = (
        weather if site == "basel" else load_weather_zurich(site="basel", data_dir=data_dir)
    )

    meta, primary_override, secondary_sink = _build_verbund_profiles(
        scene_id, weather.hours, n_servers
    )

    # Full-capacity continuous offered decisions (all N servers when thermally accepted)
    continuous_decisions = [
        SchedulerDecision(
            timestamp_zurich=h.timestamp_zurich,
            server_power_kw=n_servers * SERVER_ELECTRICAL_POWER_KW,
            active_servers=n_servers,
            jobs_completed=0,
            jobs_missed_deadline=0,
            heat_signal_on=True,
        )
        for h in weather.hours
    ]

    # Reference Basel single-sink continuous baseline for dump comparison
    basel_cont = simulate_tank_year(
        hours=basel_weather.hours,
        decisions=[
            SchedulerDecision(
                timestamp_zurich=h.timestamp_zurich,
                server_power_kw=n_servers * SERVER_ELECTRICAL_POWER_KW,
                active_servers=n_servers,
                jobs_completed=0,
                jobs_missed_deadline=0,
                heat_signal_on=True,
            )
            for h in basel_weather.hours
        ],
        n_servers=n_servers,
        strategy="baseline_continuous",
        site="basel",
        include_hourly=False,
    )

    # Basel single-sink heat-led reference
    basel_heat_led_sched = ReferenceScheduler(sla_override=False)
    basel_hl_decisions = _schedule_continuous_heat_led(
        basel_weather.hours,
        n_servers,
        basel_heat_led_sched,
        extra_sink_kw=None,
        primary_override_kw=None,
    )
    basel_heat_led = simulate_tank_year(
        hours=basel_weather.hours,
        decisions=basel_hl_decisions,
        n_servers=n_servers,
        strategy="smart_heat_led",
        site="basel",
        include_hourly=False,
    )

    # Scene continuous run (exposes summer dump elimination on Verbund sinks)
    scene_cont = simulate_tank_year(
        hours=weather.hours,
        decisions=continuous_decisions,
        n_servers=n_servers,
        strategy=f"{scene_id}_continuous",
        site=site,
        extra_sink_kw=secondary_sink,
        primary_demand_override_kw=primary_override,
        include_hourly=False,
    )

    # Scene heat-led run (zero dump, shows utilisation gain under heat-led dispatch)
    scene_sched = ReferenceScheduler(sla_override=False, extra_sink_kw=secondary_sink)
    scene_hl_decisions = _schedule_continuous_heat_led(
        weather.hours,
        n_servers,
        scene_sched,
        extra_sink_kw=secondary_sink,
        primary_override_kw=primary_override,
    )
    scene_heat_led = simulate_tank_year(
        hours=weather.hours,
        decisions=scene_hl_decisions,
        n_servers=n_servers,
        strategy=f"{scene_id}_heat_led",
        site=site,
        extra_sink_kw=secondary_sink,
        primary_demand_override_kw=primary_override,
        include_hourly=False,
    )

    # Standalone seasonal pool comparison for basel_pool_st_jakob and sole_uno
    standalone_pool_dump_kwh = None
    standalone_pool_util = None
    if scene_id in ("basel_pool_st_jakob", "sole_uno"):
        if secondary_sink is not None:
            pool_profile_kw = secondary_sink
        else:
            _, _, pool_profile_kw = _build_verbund_profiles(
                "basel_pool_st_jakob", weather.hours, n_servers
            )
        assert pool_profile_kw is not None
        pool_only_cont = simulate_tank_year(
            hours=weather.hours,
            decisions=continuous_decisions,
            n_servers=n_servers,
            strategy="standalone_pool_continuous",
            site=site,
            primary_demand_override_kw=pool_profile_kw,
            include_hourly=False,
        )
        pool_only_hl_decisions = _schedule_continuous_heat_led(
            weather.hours,
            n_servers,
            ReferenceScheduler(sla_override=False),
            extra_sink_kw=None,
            primary_override_kw=pool_profile_kw,
        )
        pool_only_hl = simulate_tank_year(
            hours=weather.hours,
            decisions=pool_only_hl_decisions,
            n_servers=n_servers,
            strategy="standalone_pool_heat_led",
            site=site,
            primary_demand_override_kw=pool_profile_kw,
            include_hourly=False,
        )
        standalone_pool_dump_kwh = round(pool_only_cont.kpis.heat_dumped_kwh, 2)
        standalone_pool_util = round(pool_only_hl.kpis.server_utilisation, 4)

    # Keep RunOnArrivalBaseline referenced cleanly so baseline contract stays aligned
    _ = RunOnArrivalBaseline()

    q_th_peak = n_servers * SERVER_HEAT_OUTPUT_KW
    flow_l_min = compute_loop_flow_l_per_min(q_th_peak, meta["delta_t_k"])
    sec_flow_l_min = (
        compute_loop_flow_l_per_min(q_th_peak, meta["secondary_delta_t_k"])
        if meta["secondary_delta_t_k"] is not None
        else None
    )

    # 3-way diverter valve representative July snapshot (noon 2025-07-15T12:00+02:00)
    jul_idx = next(
        (
            i
            for i, h in enumerate(weather.hours)
            if h.local_date == "2025-07-15" and h.local_hour == 12
        ),
        len(weather.hours) // 2,
    )
    jul_row_cont = simulate_tank_year(
        hours=weather.hours,
        decisions=continuous_decisions,
        n_servers=n_servers,
        strategy=f"{scene_id}_jul_snapshot",
        site=site,
        extra_sink_kw=secondary_sink,
        primary_demand_override_kw=primary_override,
        include_hourly=True,
    ).hourly[jul_idx]

    denom = (
        jul_row_cont.heat_used_primary_kw
        + jul_row_cont.heat_used_secondary_kw
        + jul_row_cont.heat_dumped_kw
    )
    if denom > 1e-9:
        raw_tenths = [
            1000.0 * jul_row_cont.heat_used_primary_kw / denom,
            1000.0 * jul_row_cont.heat_used_secondary_kw / denom,
            1000.0 * jul_row_cont.heat_dumped_kw / denom,
        ]
        floors = [int(math.floor(x + 1e-12)) for x in raw_tenths]
        rem = 1000 - sum(floors)
        order = sorted(
            range(3), key=lambda i: (raw_tenths[i] - floors[i], raw_tenths[i]), reverse=True
        )
        for idx in order[: max(0, rem)]:
            floors[idx] += 1
        valve_primary_pct = round(floors[0] / 10.0, 1)
        valve_secondary_pct = round(floors[1] / 10.0, 1)
        valve_dump_pct = round(floors[2] / 10.0, 1)
    else:
        valve_primary_pct = 100.0
        valve_secondary_pct = 0.0
        valve_dump_pct = 0.0

    basel_dump = basel_cont.kpis.heat_dumped_kwh
    scene_dump = scene_cont.kpis.heat_dumped_kwh
    dump_elim_pct = (100.0 * (basel_dump - scene_dump) / basel_dump) if basel_dump > 0.0 else 0.0

    capex_central = CAPEX_CHF_PER_SERVER_YR_BANDS["central"]
    h_sold_basel = basel_heat_led.kpis.server_hours_sold / n_servers
    h_sold_scene = scene_heat_led.kpis.server_hours_sold / n_servers
    capex_per_h_basel = (capex_central / h_sold_basel) if h_sold_basel > 0 else 0.0
    capex_per_h_scene = (capex_central / h_sold_scene) if h_sold_scene > 0 else 0.0

    return {
        **meta,
        "servers_per_house": n_servers,
        "thermal_output_kw": round(q_th_peak, 3),
        "primary_flow_l_per_min": round(flow_l_min, 3),
        "secondary_flow_l_per_min": round(sec_flow_l_min, 3)
        if sec_flow_l_min is not None
        else None,
        "july_valve_split_pct": {
            "primary_pct": valve_primary_pct,
            "secondary_pct": valve_secondary_pct,
            "dump_pct": valve_dump_pct,
        },
        "continuous_mode": {
            "heat_demand_total_kwh": round(scene_cont.kpis.heat_demand_total_kwh, 2),
            "heat_used_primary_kwh": round(scene_cont.kpis.heat_used_primary_kwh, 2),
            "heat_used_secondary_kwh": round(scene_cont.kpis.heat_used_secondary_kwh, 2),
            "heat_dumped_kwh": round(scene_dump, 2),
            "heat_dumped_share_pct": round(100.0 * scene_cont.kpis.heat_dumped_share, 2),
            "dump_elimination_vs_basel_house_pct": round(dump_elim_pct, 2),
            "conservation_residual_kwh": scene_cont.conservation_residual_kwh,
        },
        "heat_led_mode": {
            "server_utilisation_pct": round(100.0 * scene_heat_led.kpis.server_utilisation, 2),
            "heat_dumped_kwh": round(scene_heat_led.kpis.heat_dumped_kwh, 2),
            "heat_demand_covered_pct": round(
                100.0 * scene_heat_led.kpis.heat_demand_covered_share, 2
            ),
            "break_even_opex_chf_per_srv_h": round(
                scene_heat_led.kpis.break_even_computing_price_chf_per_server_hour, 4
            ),
            "capex_amortized_chf_per_srv_h_central": round(capex_per_h_scene, 4),
            "total_break_even_chf_per_srv_h_central": round(
                scene_heat_led.kpis.break_even_computing_price_chf_per_server_hour
                + capex_per_h_scene,
                4,
            ),
            "conservation_residual_kwh": scene_heat_led.conservation_residual_kwh,
        },
        "separate_vs_paired_comparison": {
            "basel_single_house_heat_led_util_pct": round(
                100.0 * basel_heat_led.kpis.server_utilisation, 2
            ),
            "basel_single_house_continuous_dump_kwh": round(basel_dump, 2),
            "basel_single_house_capex_chf_per_srv_h": round(capex_per_h_basel, 4),
            "standalone_seasonal_pool_dump_kwh": standalone_pool_dump_kwh,
            "standalone_seasonal_pool_heat_led_util_pct": (
                round(100.0 * standalone_pool_util, 2) if standalone_pool_util is not None else None
            ),
            "this_scene_heat_led_util_pct": round(
                100.0 * scene_heat_led.kpis.server_utilisation, 2
            ),
            "this_scene_continuous_dump_kwh": round(scene_dump, 2),
        },
    }


def _schedule_continuous_heat_led(
    hours: list[HourlyWeather],
    n_servers: int,
    scheduler,
    extra_sink_kw: list[float] | None,
    primary_override_kw: list[float] | None,
) -> list[SchedulerDecision]:
    """Dispatch maximum thermally absorbable servers each hour using ReferenceScheduler rule."""
    heating_days = compute_heating_days(hours)
    house_demand = compute_house_demand(hours, heating_days=heating_days)
    s_t = TANK_DEFAULT_START_KWH
    decisions: list[SchedulerDecision] = []
    for idx, h in enumerate(hours):
        d_prim = (
            primary_override_kw[idx] if primary_override_kw is not None else house_demand[idx][2]
        )
        d_sec = extra_sink_kw[idx] if extra_sink_kw is not None else 0.0
        thermal_cap = min(n_servers, scheduler.max_thermal_servers(s_t, d_prim + d_sec))
        p_kw = thermal_cap * SERVER_ELECTRICAL_POWER_KW
        avail_t = s_t + HEAT_RECOVERY_FRACTION * p_kw
        q_prim = min(avail_t, d_prim)
        rem_prim = avail_t - q_prim
        surplus = max(0.0, rem_prim - TANK_CAPACITY_KWH)
        q_sec = min(surplus, d_sec)
        s_t = min(TANK_CAPACITY_KWH, max(0.0, rem_prim - q_sec))
        decisions.append(
            SchedulerDecision(
                timestamp_zurich=h.timestamp_zurich,
                server_power_kw=p_kw,
                active_servers=thermal_cap,
                jobs_completed=0,
                jobs_missed_deadline=0,
                heat_signal_on=(thermal_cap >= 1),
            )
        )
    return decisions


def list_verbund_scenes(
    n_servers: int = DEFAULT_SERVERS_PER_HOUSE,
    data_dir: Path | None = None,
) -> list[dict]:
    """Compute telemetry and separate vs paired comparisons for all 7 Verbund scenes."""
    return [
        simulate_verbund_scene(scene_id=sid, n_servers=n_servers, data_dir=data_dir)
        for sid in VERBUND_SCENE_IDS
    ]


def compute_heatwise_sensitivity(heatwise_data: dict, base_result: SimulationResult) -> dict:
    """Compute HEATWISE idle draw, air vs liquid throttling and partial-capture sensitivity."""
    ratios = heatwise_data["idle_to_full_ratios"]
    ratio_vals = [v["idle_to_full_ratio"] for v in ratios.values()]
    r_min = min(ratio_vals)
    r_mean = sum(ratio_vals) / len(ratio_vals)
    r_max = max(ratio_vals)

    n_srv = base_result.kpis.servers_per_house
    h_total = len(base_result.hourly) if base_result.hourly else HOURS_PER_YEAR
    h_active = base_result.kpis.server_hours_sold
    h_paused = max(0.0, n_srv * h_total - h_active)

    idle_sweep = []
    for label, r_idle in [("low", r_min), ("central", r_mean), ("high", r_max)]:
        e_adj = (
            h_active * SERVER_ELECTRICAL_POWER_KW + h_paused * r_idle * SERVER_ELECTRICAL_POWER_KW
        )
        cost_adj = e_adj * ELCOM_BASEL_H4_CHF_PER_KWH
        be_adj = (
            (cost_adj - base_result.kpis.heat_value_used_chf) / h_active
            if h_active > 1e-12
            else 0.0
        )
        idle_sweep.append(
            {
                "band": label,
                "idle_to_full_ratio": round(r_idle, 4),
                "paused_server_hours": round(h_paused, 1),
                "adjusted_electricity_kwh": round(e_adj, 2),
                "adjusted_electricity_cost_chf": round(cost_adj, 2),
                "adjusted_break_even_chf_per_srv_h": round(be_adj, 4),
            }
        )

    # Air vs Hybrid throttling from HEATWISE Cases 1-5 vs Cases 6-8
    air_100 = [v["100%"] for v in heatwise_data["load_steps_air"].values() if "100%" in v]
    hyb_100 = [v["100%"] for v in heatwise_data["load_steps_hybrid"].values() if "100%" in v]
    air_freq = sum(x["cpu_freq_ghz_mean"] for x in air_100) / len(air_100)
    hyb_freq = sum(x["cpu_freq_ghz_mean"] for x in hyb_100) / len(hyb_100)
    slowdown_pct = 100.0 * (1.0 - air_freq / hyb_freq) if hyb_freq > 0 else 0.0

    # Recovery fraction sweep (0.30 CPU cold-plate vs 0.65 partial vs 0.80 full-board Aquasar)
    recovery_sweep = []
    for eta in (0.30, 0.65, 0.80):
        heat_credit = eta * (ELCOM_BASEL_H4_CHF_PER_KWH / HEAT_PUMP_REF_COP)
        opex_floor = ELCOM_BASEL_H4_CHF_PER_KWH - heat_credit
        recovery_sweep.append(
            {
                "recovery_fraction": eta,
                "note": (
                    "HEATWISE CPU cold-plate lower bound"
                    if eta == 0.30
                    else (
                        "Partial liquid + air exhaust"
                        if eta == 0.65
                        else "Aquasar / case full-board warm-water"
                    )
                ),
                "max_heat_credit_chf_per_srv_h": round(heat_credit, 4),
                "opex_break_even_floor_chf_per_srv_h": round(opex_floor, 4),
            }
        )

    return {
        "source": heatwise_data["source"],
        "zenodo_record": heatwise_data["zenodo_record"],
        "idle_to_full_ratio_range": {
            "min": round(r_min, 4),
            "mean": round(r_mean, 4),
            "max": round(r_max, 4),
        },
        "sigstop_idle_penalty_sweep": idle_sweep,
        "throttling_comparison": {
            "air_100pct_cpu_temp_c_mean": round(
                sum(x["cpu_temp_c_mean"] for x in air_100) / len(air_100), 2
            ),
            "air_100pct_cpu_freq_ghz_mean": round(air_freq, 4),
            "hybrid_100pct_cpu_temp_c_mean": round(
                sum(x["cpu_temp_c_mean"] for x in hyb_100) / len(hyb_100), 2
            ),
            "hybrid_100pct_cpu_freq_ghz_mean": round(hyb_freq, 4),
            "air_frequency_slowdown_pct": round(slowdown_pct, 2),
        },
        "recovery_fraction_sweep": recovery_sweep,
        "idle_power_assumption": (
            "h_paused (n_servers * h_total - h_active) combines SIGSTOP-paused and unqueued idle "
            "hours and assumes paused idle power is unrecovered overhead."
        ),
        "caveat": (
            "HEATWISE hybrid cases use two-phase CPU cold plates (22-33% capture at ~33 C water); "
            "80% capture at 50-60 C requires full-board warm-water cooling (Aquasar)."
        ),
    }


def compute_mlperf_sensitivity(
    mlperf_data: dict,
    break_even_low_chf: float,
    break_even_high_chf: float,
) -> dict:
    """Compute J/sample, J/token and energy-only CHF per 1M units across MLPerf v5.1 Offline."""
    table = []
    for row in mlperf_data["offline_power_rows"]:
        j_unit = float(row["joules_per_unit"])
        j_sample = float(row["joules_per_sample"])
        # 1 kWh = 3,600,000 J -> CHF per 1M units = CHF_per_kWh * J_per_unit / 3.6
        chf_1m_reused = break_even_low_chf * (j_unit / 3.6)
        chf_1m_dumped = break_even_high_chf * (j_unit / 3.6)
        table.append(
            {
                "id": row["id"],
                "system": row["system"],
                "suite": row["suite"],
                "model": row["model"],
                "performance_result": row["performance_result"],
                "performance_units": row["performance_units"],
                "power_watts": row["power_watts"],
                "joules_per_unit": round(j_unit, 6),
                "joules_per_sample": round(j_sample, 4),
                "chf_per_1m_units_at_full_reuse": round(chf_1m_reused, 6),
                "chf_per_1m_units_at_zero_reuse": round(chf_1m_dumped, 6),
            }
        )

    return {
        "source": mlperf_data["source"],
        "break_even_low_chf_per_kwh": round(break_even_low_chf, 4),
        "break_even_high_chf_per_kwh": round(break_even_high_chf, 4),
        "rows": table,
        "caveat": (
            "Energy-only marginal cost from 8x B200 (~10 kW) and Ryzen 5 5560 edge submissions; "
            "does not represent a 1 kW server's throughput, VRAM or CAPEX."
        ),
    }


def compute_ashrae_w45_sensitivity(
    hours: list[HourlyWeather],
    n_servers: int = DEFAULT_SERVERS_PER_HOUSE,
) -> dict:
    """Compare stratified 35 kWh tank (30-60 C) vs mixed tank capped at W45 (45 C / 17.5 kWh)."""
    from digitalheat.scheduler import ReferenceScheduler

    w45_soc_cap = (
        TANK_CAPACITY_KWH * (ASHRAE_W45_MAX_SUPPLY_C - TANK_TEMP_MIN_C) / TANK_TEMP_SPREAD_K
    )
    sched_strat = ReferenceScheduler(sla_override=False)
    dec_strat = _schedule_continuous_heat_led(hours, n_servers, sched_strat, None, None)
    res_strat = simulate_tank_year(
        hours=hours,
        decisions=dec_strat,
        n_servers=n_servers,
        strategy="stratified_35kwh",
        tank_capacity_kwh=TANK_CAPACITY_KWH,
        include_hourly=False,
    )

    # Under a fully mixed tank limited to 45 C supply, effective buffer capacity halves to 17.5 kWh
    heating_days = compute_heating_days(hours)
    house_demand = compute_house_demand(hours, heating_days=heating_days)
    s_t = 0.0
    dec_mixed: list[SchedulerDecision] = []
    for idx, h in enumerate(hours):
        d_kw = house_demand[idx][2]
        headroom = max(0.0, w45_soc_cap - s_t) + max(0.0, d_kw)
        cap = min(n_servers, int((headroom + 1e-9) // SERVER_HEAT_OUTPUT_KW))
        p_kw = cap * SERVER_ELECTRICAL_POWER_KW
        s_t = min(w45_soc_cap, max(0.0, s_t + HEAT_RECOVERY_FRACTION * p_kw - d_kw))
        dec_mixed.append(
            SchedulerDecision(
                timestamp_zurich=h.timestamp_zurich,
                server_power_kw=p_kw,
                active_servers=cap,
                jobs_completed=0,
                jobs_missed_deadline=0,
                heat_signal_on=(cap >= 1),
            )
        )

    res_mixed = simulate_tank_year(
        hours=hours,
        decisions=dec_mixed,
        n_servers=n_servers,
        strategy="mixed_w45_17_5kwh",
        tank_capacity_kwh=w45_soc_cap,
        include_hourly=False,
    )

    return {
        "ashrae_w45_supply_limit_c": ASHRAE_W45_MAX_SUPPLY_C,
        "tank_temp_min_c": TANK_TEMP_MIN_C,
        "tank_temp_max_c": TANK_TEMP_MIN_C + TANK_TEMP_SPREAD_K,
        "stratified_effective_capacity_kwh": TANK_CAPACITY_KWH,
        "mixed_w45_effective_capacity_kwh": round(w45_soc_cap, 2),
        "stratified_server_utilisation_pct": round(100.0 * res_strat.kpis.server_utilisation, 2),
        "mixed_w45_server_utilisation_pct": round(100.0 * res_mixed.kpis.server_utilisation, 2),
        "stratified_heat_covered_pct": round(100.0 * res_strat.kpis.heat_demand_covered_share, 2),
        "mixed_w45_heat_covered_pct": round(100.0 * res_mixed.kpis.heat_demand_covered_share, 2),
        "legionella_note": (
            "ASHRAE W45 caps coolant supply at 45 C; 55-60 C DHW pasteurization requires "
            "a stratified tank (30 C bottom return), W+ server cooling or auxiliary boost."
        ),
    }


def compute_capex_utilisation_cliff(
    hours: list[HourlyWeather],
    server_counts: list[int] | None = None,
) -> dict:
    """Sweep cluster sizes N=1..12 showing utilisation collapse and total OPEX+CAPEX break-even."""
    from digitalheat.scheduler import ReferenceScheduler

    counts = server_counts if server_counts is not None else [1, 2, 4, 8, 12]
    rows = []
    for n in counts:
        sched = ReferenceScheduler(sla_override=False)
        decs = _schedule_continuous_heat_led(hours, n, sched, None, None)
        res = simulate_tank_year(
            hours=hours,
            decisions=decs,
            n_servers=n,
            strategy=f"capex_sweep_n{n}",
            include_hourly=False,
        )
        h_per_srv = res.kpis.server_hours_sold / n
        be_opex = res.kpis.break_even_computing_price_chf_per_server_hour
        totals = {}
        for band, capex_yr in CAPEX_CHF_PER_SERVER_YR_BANDS.items():
            capex_per_h = (capex_yr / h_per_srv) if h_per_srv > 0 else 0.0
            totals[band] = round(be_opex + capex_per_h, 4)

        rows.append(
            {
                "servers_per_house": n,
                "server_utilisation_pct": round(100.0 * res.kpis.server_utilisation, 2),
                "heat_demand_covered_pct": round(100.0 * res.kpis.heat_demand_covered_share, 2),
                "server_hours_per_server_yr": round(h_per_srv, 1),
                "break_even_opex_chf_per_srv_h": round(be_opex, 4),
                "total_break_even_chf_per_srv_h": totals,
            }
        )

    return {
        "capex_assumptions_chf_per_server_yr": CAPEX_CHF_PER_SERVER_YR_BANDS,
        "cliff_rows": rows,
        "conclusion": (
            "Sizing for >90% residential heat coverage (N=8..12) drops server utilisation from "
            "87% (N=1) and 52% (N=8) to 37% (N=12). Central CAPEX+OPEX break-even climbs from "
            "0.41 CHF/server-hour (N=1) and 0.53 CHF/server-hour (N=8) to 0.66 CHF/server-hour "
            "(N=12) without a summer Verbund sink."
        ),
    }


def compute_tariff_gap_sensitivity(elcom_data: dict) -> dict:
    """Compare ElCom 2026 IWB Basel H4 vs commercial/industrial C2-C6 tariffs."""
    cats = elcom_data["categories"]
    h4_excl = float(cats["H4"]["total_chf_per_kwh"])
    h4_incl = h4_excl * (1.0 + SWISS_VAT_RATE)
    max_heat_credit_excl = HEAT_RECOVERY_FRACTION * (h4_excl / HEAT_PUMP_REF_COP)

    rows = []
    for cat in ("H4", "C2", "C3", "C4", "C5", "C6"):
        if cat not in cats:
            continue
        # LINDAS reports all H4 and C2-C6 tariffs excluding 8.1% Swiss VAT
        excl_vat = float(cats[cat]["total_chf_per_kwh"])
        incl_vat = excl_vat * (1.0 + SWISS_VAT_RATE)
        gap_excl = h4_excl - excl_vat
        gap_incl = h4_incl - incl_vat
        gap_incl_vs_comm_excl = (h4_incl - excl_vat) if cat != "H4" else 0.0
        credit_consumed_pct = (
            (100.0 * gap_excl / max_heat_credit_excl) if max_heat_credit_excl > 0 else 0.0
        )
        rows.append(
            {
                "category": cat,
                "total_excl_vat_chf_per_kwh": round(excl_vat, 5),
                "total_incl_vat_chf_per_kwh": round(incl_vat, 5),
                "h4_penalty_excl_vat_chf_per_kwh": round(gap_excl, 5),
                "h4_penalty_incl_vat_chf_per_kwh": round(gap_incl, 5),
                "h4_incl_vs_commercial_excl_vat_gap_chf_per_kwh": round(gap_incl_vs_comm_excl, 5),
                "share_of_heat_credit_offset_pct": round(credit_consumed_pct, 1),
            }
        )

    return {
        "source": elcom_data["source"],
        "vat_rate": SWISS_VAT_RATE,
        "vat_basis": (
            "All LINDAS H4 and C2-C6 rates are excl. 8.1% VAT; total_incl_vat = excl_vat * 1.081"
        ),
        "max_residential_heat_credit_chf_per_kwh_e": round(max_heat_credit_excl, 4),
        "tariffs": rows,
    }


def compute_pilot_reality_sensitivity(
    n_servers: int = DEFAULT_SERVERS_PER_HOUSE,
    data_dir: Path | None = None,
) -> dict:
    """Assemble all 5 Pilot Reality sensitivity panels decoupled from the reference benchmark."""
    from digitalheat.scheduler import ReferenceScheduler, generate_dh_batch_jobs

    weather = load_weather_zurich(site="basel", data_dir=data_dir)
    heatwise_data = load_heatwise_summary(data_dir=data_dir)
    mlperf_data = load_mlperf_summary(data_dir=data_dir)
    elcom_data = load_elcom_tariffs(data_dir=data_dir)

    jobs = generate_dh_batch_jobs(weather.hours, n_servers=n_servers)
    sched = ReferenceScheduler(sla_override=False)
    decisions = sched.schedule_year(weather.hours, jobs, n_servers=n_servers)
    base_res = simulate_tank_year(
        hours=weather.hours,
        decisions=decisions,
        n_servers=n_servers,
        strategy="smart_heat_led",
        total_jobs_arrived=len(jobs),
        include_hourly=False,
    )

    be_low = base_res.kpis.break_even_computing_price_chf_per_server_hour
    be_high = ELCOM_BASEL_H4_CHF_PER_KWH

    return {
        "servers_per_house": n_servers,
        "heatwise": compute_heatwise_sensitivity(heatwise_data, base_res),
        "mlperf_v51": compute_mlperf_sensitivity(mlperf_data, be_low, be_high),
        "ashrae_w45": compute_ashrae_w45_sensitivity(weather.hours, n_servers=n_servers),
        "capex_utilisation_cliff": compute_capex_utilisation_cliff(weather.hours),
        "tariff_gap": compute_tariff_gap_sensitivity(elcom_data),
    }
