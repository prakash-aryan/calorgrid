"""Unit tests for separate Host/Operator ledgers, conservation, DHW normalization, and DST."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from digitalheat.configurator import (
    _FORECAST_CACHE,
    DEPLOYMENT_ARCHETYPES,
    DHW_HOURLY_WEIGHTS_24H,
    SERVER_MODELS,
    _simulate_conserved_horizon,
    compute_configuration_quote,
    fetch_live_swiss_forecast,
)


def test_dhw_weights_normalized_to_24() -> None:
    assert sum(DHW_HOURLY_WEIGHTS_24H) == pytest.approx(24.0, abs=1e-9)


def test_conservation_across_all_deployments_and_server_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _offline_urlopen(*args, **kwargs):
        raise OSError("offline unit test")

    _FORECAST_CACHE.clear()
    monkeypatch.setattr("urllib.request.urlopen", _offline_urlopen)
    for dep in DEPLOYMENT_ARCHETYPES:
        for mdl in SERVER_MODELS:
            for count in (1, mdl.max_units):
                q = compute_configuration_quote(
                    deployment_id=dep.id,
                    server_model_id=mdl.id,
                    server_count=count,
                    heat_target_pct=100,
                    indoor_target_c=21.0,
                )
                assert q["conservation"]["residual_kwh"] <= 1e-6
                assert q["conservation"]["max_hourly_residual_kwh"] <= 1e-6
                assert q["tomorrow_conservation"]["residual_kwh"] <= 1e-6
                assert q["tomorrow_conservation"]["max_hourly_residual_kwh"] <= 1e-6

    # Oversizing 3x B200 (31.2 kW) on house_basel (35 kWh tank) must report non-zero annual dump
    oversized = compute_configuration_quote(
        deployment_id="house_basel",
        server_model_id="b200_ai_10kw",
        server_count=3,
    )
    assert oversized["sizing"]["annual_dumped_kwh"] > 0.0
    assert oversized["host_ledger"]["dhw_preheat_cap_pct"] == pytest.approx(70.0, abs=1e-6)
    assert len(oversized["heatledger_tomorrow"]["processes"]) == 3
    assert len(oversized["heatledger_tomorrow"]["processes"][0]["gpu_slots"]) == 8


def test_separate_ledgers_and_slider_isolation() -> None:
    q20 = compute_configuration_quote(
        deployment_id="house_basel",
        server_model_id="hydronode_1kw",
        server_count=4,
        heat_target_pct=20,
        indoor_target_c=21.0,
    )
    q100 = compute_configuration_quote(
        deployment_id="house_basel",
        server_model_id="hydronode_1kw",
        server_count=4,
        heat_target_pct=100,
        indoor_target_c=21.0,
    )
    q140 = compute_configuration_quote(
        deployment_id="house_basel",
        server_model_id="hydronode_1kw",
        server_count=4,
        heat_target_pct=140,
        indoor_target_c=21.0,
    )

    # Annual metrics must be invariant to tomorrow's heat_target_pct slider
    assert (
        q20["host_ledger"]["heating_bill_savings_chf"]
        == q100["host_ledger"]["heating_bill_savings_chf"]
    )
    assert (
        q100["host_ledger"]["heating_bill_savings_chf"]
        == q140["host_ledger"]["heating_bill_savings_chf"]
    )
    assert (
        q20["operator_ledger"]["calorgrid_net_margin_chf"]
        == q140["operator_ledger"]["calorgrid_net_margin_chf"]
    )
    assert q20["sizing"] == q140["sizing"]

    # Host pays CHF 0 for server hardware and server electricity
    for q in (q20, q100, q140):
        hl = q["host_ledger"]
        ol = q["operator_ledger"]
        assert hl["host_server_hardware_chf"] == 0.0
        assert hl["host_server_electricity_chf"] == 0.0
        assert hl["calorgrid_heat_kwh"] <= hl["building_heat_demand_kwh"] + 1e-6
        assert (
            hl["tomorrow"]["calorgrid_heat_kwh"]
            <= hl["tomorrow"]["building_heat_demand_kwh"] + 1e-6
        )
        assert (
            hl["tomorrow"]["heating_bill_savings_chf"]
            <= hl["tomorrow"]["baseline_heating_bill_chf"] + 1e-6
        )
        # Legacy net_annual_value_chf must NEVER add host savings into operator revenue
        assert q["financials"]["net_annual_value_chf"] == pytest.approx(
            ol["calorgrid_net_margin_chf"], abs=1e-6
        )
        assert q["heatledger_tomorrow"]["tomorrow_net_chf"] == pytest.approx(
            ol["tomorrow"]["calorgrid_net_margin_chf"], abs=1e-6
        )

    # Unclamped negative operator margin is permitted (e.g. residential 3x B200)
    neg = compute_configuration_quote(
        deployment_id="house_basel",
        server_model_id="b200_ai_10kw",
        server_count=3,
    )
    assert neg["operator_ledger"]["calorgrid_net_margin_chf"] < 0.0

    # Indoor target sweep 18 -> 24 strictly increases baseline heating bill
    q_cold = compute_configuration_quote(
        deployment_id="house_basel", server_model_id="hydronode_1kw", indoor_target_c=18.0
    )
    q_warm = compute_configuration_quote(
        deployment_id="house_basel", server_model_id="hydronode_1kw", indoor_target_c=24.0
    )
    assert (
        q_warm["host_ledger"]["baseline_heating_bill_chf"]
        > q_cold["host_ledger"]["baseline_heating_bill_chf"]
    )
    assert (
        q_warm["host_ledger"]["tomorrow"]["baseline_heating_bill_chf"]
        > q_cold["host_ledger"]["tomorrow"]["baseline_heating_bill_chf"]
    )


def test_zero_demand_edge_case_returns_null_shares_without_error() -> None:
    dep = DEPLOYMENT_ARCHETYPES[0]
    mdl = SERVER_MODELS[0]
    zeros = [0.0] * 24
    sim = _simulate_conserved_horizon(
        dep=dep,
        mdl=mdl,
        count=2,
        space_kw_list=zeros,
        dhw_kw_list=zeros,
        sec_kw_list=zeros,
        target_frac=1.0,
        heater_type="gas_boiler",
    )
    hl = sim["host_horizon"]
    assert hl.building_heat_demand_kwh == 0.0
    assert hl.calorgrid_heat_share_pct is None
    assert hl.default_heater_share_pct is None
    assert hl.heating_bill_savings_chf == 0.0
    assert sim["conservation"].residual_kwh <= 1e-6


def test_dst_23h_and_25h_open_meteo_forecast_slicing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResp:
        def __init__(self, payload: dict) -> None:
            self._b = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._b

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    for today_str, tomorrow_str, expected_hours in (
        ("2025-03-29", "2025-03-30", 23),
        ("2025-10-25", "2025-10-26", 25),
    ):
        zurich = ZoneInfo("Europe/Zurich")
        start_dt = datetime.fromisoformat(f"{today_str}T00:00:00").replace(tzinfo=zurich)
        end_dt = datetime.fromisoformat(f"{tomorrow_str}T23:00:00").replace(tzinfo=zurich)
        cur_utc = start_dt.astimezone(ZoneInfo("UTC"))
        end_utc = end_dt.astimezone(ZoneInfo("UTC"))
        times: list[str] = []
        temps: list[float] = []
        while cur_utc <= end_utc:
            loc = cur_utc.astimezone(zurich)
            times.append(loc.strftime("%Y-%m-%dT%H:00"))
            temps.append(round(6.0 + 0.1 * loc.hour, 1))
            cur_utc += timedelta(hours=1)

        payload = {
            "daily": {
                "time": [today_str, tomorrow_str],
                "temperature_2m_min": [4.0, 5.0],
                "temperature_2m_max": [11.0, 12.0],
                "weather_code": [2, 3],
            },
            "hourly": {"time": times, "temperature_2m": temps},
        }
        _FORECAST_CACHE.clear()
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=5: _FakeResp(payload))
        fc = fetch_live_swiss_forecast("house_basel")
        assert fc["hours_count"] == expected_hours
        assert len(fc["hourly_temps_c"]) == expected_hours
        assert fc["is_dst_transition"] is True

        # Confirm quote handles 23h/25h days cleanly and conserves energy
        q = compute_configuration_quote(
            deployment_id="house_basel", server_model_id="hydronode_1kw"
        )
        assert len(q["heatledger_tomorrow"]["hourly"]) == expected_hours
        assert q["tomorrow_conservation"]["residual_kwh"] <= 1e-6

    _FORECAST_CACHE.clear()
    assert io.BytesIO is not None
