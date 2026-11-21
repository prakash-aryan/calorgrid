"""Unit tests for M2 FastAPI simulation, DST, solar geometry and adapter endpoints."""

import math
import time

import pytest
from fastapi.testclient import TestClient

from digitalheat.api import app
from digitalheat.config import CONSERVATION_TOL_KWH
from digitalheat.data_loader import load_weather_zurich


def test_health_endpoint_contract() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["version"] == "1.0.0"
        assert body["datasets_verified"] is True
        assert body["scheduler"] == "ReferenceScheduler"


def test_annual_simulation_kpis_conservation_and_baseline_comparison() -> None:
    with TestClient(app) as client:
        t0 = time.monotonic()
        resp = client.get("/api/v1/simulation/annual?strategy=smart&servers=4&site=basel")
        assert resp.status_code == 200
        body = resp.json()
        assert "hourly" not in body
        assert body["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
        assert body["max_hourly_residual_kwh"] <= CONSERVATION_TOL_KWH
        kpis = body["kpis"]
        assert kpis["servers_per_house"] == 4
        assert kpis["heat_dumped_kwh"] == pytest.approx(0.0, abs=1e-9)
        assert 0.0 < kpis["heat_demand_covered_share"] < 1.0
        assert 0.0 < kpis["server_utilisation"] < 1.0
        assert "missed_deadlines_pct" in kpis
        assert kpis["electricity_cost_chf"] > 0.0
        assert kpis["computing_income_chf"] > 0.0
        assert kpis["break_even_computing_price_chf_per_server_hour"] == pytest.approx(
            0.2261, abs=1e-4
        )
        assert body["baseline_comparison"]["heat_dumped_reduction_kwh"] > 3000.0
        assert "3,816" in body["negative_payoff_statement"]

        # Repeat request should be served from in-memory cache (<50 ms)
        t1 = time.monotonic()
        resp2 = client.get("/api/v1/simulation/annual?strategy=smart&servers=4&site=basel")
        dt_ms = (time.monotonic() - t1) * 1000.0
        assert resp2.status_code == 200
        assert dt_ms < 50.0
        assert t1 >= t0

        assert client.get("/api/v1/simulation/annual?servers=0").status_code == 422
        assert client.get("/api/v1/simulation/annual?servers=99").status_code == 422
        assert client.get("/api/v1/simulation/annual?strategy=invalid").status_code == 422


def test_timeseries_daily_and_hourly_dst_lengths() -> None:
    with TestClient(app) as client:
        daily = client.get("/api/v1/simulation/timeseries?resolution=daily&strategy=smart")
        assert daily.status_code == 200
        d_body = daily.json()
        assert d_body["count"] == 365
        assert len(d_body["rows"]) == 365

        mar = client.get("/api/v1/simulation/timeseries?resolution=hourly&month=3&strategy=smart")
        assert mar.status_code == 200
        assert mar.json()["count"] == 743

        jul = client.get(
            "/api/v1/simulation/timeseries?resolution=hourly&month=7&strategy=baseline"
        )
        assert jul.status_code == 200
        assert jul.json()["count"] == 744

        oct_r = client.get(
            "/api/v1/simulation/timeseries?resolution=hourly&month=10&strategy=smart"
        )
        assert oct_r.status_code == 200
        assert oct_r.json()["count"] == 745


def test_day_endpoint_dst_and_solar_geometry() -> None:
    with TestClient(app) as client:
        jan = client.get("/api/v1/simulation/day/2025-01-15?strategy=smart&servers=4")
        assert jan.status_code == 200
        j_body = jan.json()
        assert j_body["hours_count"] == 24
        assert j_body["is_heating_day"] is True
        assert j_body["is_dst_transition"] is False

        jul = client.get("/api/v1/simulation/day/2025-07-15?strategy=baseline&servers=4")
        assert jul.status_code == 200
        jul_body = jul.json()
        assert jul_body["hours_count"] == 24
        assert jul_body["is_heating_day"] is False
        noon = next(h for h in jul_body["hours"] if h["local_hour"] == 13)
        assert noon["shortwave_radiation_wm2"] > 0.0
        assert noon["solar_elevation_deg"] > 60.0
        sun_norm = math.sqrt(sum(c * c for c in noon["sun_direction_xyz"]))
        assert sun_norm == pytest.approx(1.0, abs=1e-4)
        for field in (
            "tank_temp_c",
            "tank_soc_pct",
            "supply_temp_c",
            "return_temp_c",
            "loop_flow_l_per_min",
        ):
            assert field in noon

        spring_dst = client.get("/api/v1/simulation/day/2025-03-30")
        assert spring_dst.status_code == 200
        assert spring_dst.json()["hours_count"] == 23
        assert spring_dst.json()["is_dst_transition"] is True

        autumn_dst = client.get("/api/v1/simulation/day/2025-10-26")
        assert autumn_dst.status_code == 200
        assert autumn_dst.json()["hours_count"] == 25
        assert autumn_dst.json()["is_dst_transition"] is True

        assert client.get("/api/v1/simulation/day/2025-13-99").status_code == 422
        assert client.get("/api/v1/simulation/day/2024-07-15").status_code == 404


def test_scheduler_adapter_validation_and_strategy_lookup() -> None:
    weather = load_weather_zurich(site="basel")
    with TestClient(app) as client:
        assert client.get("/api/v1/simulation/annual?strategy=adapter&servers=3").status_code == 404

        good_resp = client.post(
            "/api/v1/scheduler/adapter",
            json={
                "adapter_name": "vaibhav_vilas_preview",
                "n_servers": 3,
                "site": "basel",
                "hourly_active_servers": [2] * 8760,
            },
        )
        assert good_resp.status_code == 200
        g_body = good_resp.json()
        assert g_body["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
        # Offered DH backlog is 12.0 server-h/server/day (50% load); cap=2/3 completes all jobs
        assert g_body["kpis"]["server_utilisation"] == pytest.approx(0.50, abs=0.02)

        # Capacity-constrained schedule [1]*8760 with n_servers=3 saturates at 1/3 utilisation
        capped_resp = client.post(
            "/api/v1/scheduler/adapter",
            json={
                "adapter_name": "capacity_capped",
                "n_servers": 3,
                "site": "basel",
                "hourly_active_servers": [1] * 8760,
            },
        )
        assert capped_resp.status_code == 200
        assert capped_resp.json()["kpis"]["server_utilisation"] == pytest.approx(
            1.0 / 3.0, abs=0.02
        )

        assert client.get("/api/v1/simulation/annual?strategy=adapter&servers=3").status_code == 200
        assert (
            client.get("/api/v1/simulation/day/2025-01-15?strategy=adapter&servers=3").status_code
            == 200
        )

        # Reject empty payload, bad length, out-of-bounds server count
        assert client.post("/api/v1/scheduler/adapter", json={}).status_code == 422
        assert (
            client.post(
                "/api/v1/scheduler/adapter",
                json={"n_servers": 4, "hourly_active_servers": [1] * 100},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/scheduler/adapter",
                json={"n_servers": 4, "hourly_active_servers": [5] * 8760},
            ).status_code
            == 422
        )

        # Reject inconsistent raw decision power vs active_servers and negative job counts
        valid_decisions = [
            {
                "timestamp_zurich": h.timestamp_zurich,
                "server_power_kw": 2.0,
                "active_servers": 2,
                "jobs_completed": 0,
                "jobs_missed_deadline": 0,
                "heat_signal_on": True,
            }
            for h in weather.hours
        ]
        bad_power = [dict(d) for d in valid_decisions]
        bad_power[0]["server_power_kw"] = 3.5
        assert (
            client.post(
                "/api/v1/scheduler/adapter",
                json={"n_servers": 4, "decisions": bad_power},
            ).status_code
            == 422
        )

        bad_jobs = [dict(d) for d in valid_decisions]
        bad_jobs[0]["jobs_completed"] = -1
        assert (
            client.post(
                "/api/v1/scheduler/adapter",
                json={"n_servers": 4, "decisions": bad_jobs},
            ).status_code
            == 422
        )


def test_nan_and_infinity_post_requests_return_clean_422() -> None:
    weather = load_weather_zurich(site="basel")
    headers = {"Content-Type": "application/json"}
    with TestClient(app) as client:
        for payload_str in (
            '{"job_type": "iiif_embeddings", "duration_s": NaN}',
            '{"job_type": "iiif_embeddings", "tank_soc_kwh": Infinity, "heat_demand_kw": 2.0}',
        ):
            resp = client.post("/api/v1/worker/start", content=payload_str, headers=headers)
            assert resp.status_code == 422
            assert resp.json()["detail"]

        for payload_str in (
            '{"tank_soc_kwh": 10.0, "heat_demand_kw": NaN, "outdoor_temp_c": 5.0}',
            '{"tank_soc_kwh": 10.0, "heat_demand_kw": 2.0, "outdoor_temp_c": NaN}',
            '{"tank_soc_kwh": 10.0, "heat_demand_kw": 2.0, "outdoor_temp_c": -Infinity}',
        ):
            resp = client.post("/api/v1/worker/heat-signal", content=payload_str, headers=headers)
            assert resp.status_code == 422
            assert resp.json()["detail"]

        nan_s_start = (
            '{"n_servers": 4, "s_start_kwh": NaN, "hourly_active_servers": ['
            + ",".join(["1"] * 8760)
            + "]}"
        )
        resp_s_start = client.post(
            "/api/v1/scheduler/adapter", content=nan_s_start, headers=headers
        )
        assert resp_s_start.status_code == 422

        nan_power = (
            '{"n_servers": 4, "hourly_server_power_kw": [NaN,'
            + ",".join(["1.0"] * 8759)
            + "]}"
        )
        assert (
            client.post("/api/v1/scheduler/adapter", content=nan_power, headers=headers).status_code
            == 422
        )

        dec_list = ",".join(
            (
                f'{{"timestamp_zurich":"{h.timestamp_zurich}","server_power_kw":'
                f'{"NaN" if i == 0 else "1.0"},"active_servers":1,'
                f'"jobs_completed":0,"jobs_missed_deadline":0,"heat_signal_on":true}}'
            )
            for i, h in enumerate(weather.hours)
        )
        nan_dec = f'{{"n_servers": 4, "decisions": [{dec_list}]}}'
        assert (
            client.post("/api/v1/scheduler/adapter", content=nan_dec, headers=headers).status_code
            == 422
        )


def test_adapter_rejects_fractional_hourly_server_power_kw() -> None:
    with TestClient(app) as client:
        for kw_val in (0.5, 1.5):
            resp = client.post(
                "/api/v1/scheduler/adapter",
                json={
                    "n_servers": 4,
                    "hourly_server_power_kw": [kw_val] * 8760,
                },
            )
            assert resp.status_code == 422


def test_cached_endpoints_isolate_returned_mutations() -> None:
    from digitalheat import api as api_module

    day1 = api_module.get_simulation_day("2025-01-15", strategy="smart", servers=4, site="basel")
    orig_demand = day1["day_summary"]["heat_demand_kwh"]
    day1["day_summary"]["heat_demand_kwh"] = -999.0
    day1["hours"][0]["sun_direction_xyz"][0] = 99.0
    day2 = api_module.get_simulation_day("2025-01-15", strategy="smart", servers=4, site="basel")
    assert day2["day_summary"]["heat_demand_kwh"] == orig_demand
    assert day2["daily_summary"]["heat_demand_kwh"] == orig_demand
    assert day2["hours"][0]["sun_direction_xyz"][0] != 99.0

    ts1 = api_module.get_simulation_timeseries(
        strategy="smart", servers=4, site="basel", resolution="daily", month=None
    )
    orig_date = ts1["rows"][0]["date"]
    ts1["rows"][0]["date"] = "MUTATED"
    ts2 = api_module.get_simulation_timeseries(
        strategy="smart", servers=4, site="basel", resolution="daily", month=None
    )
    assert ts2["rows"][0]["date"] == orig_date

    vs1 = api_module.get_verbund_scene("basel_pool_st_jakob", servers=4)
    orig_dump = vs1["continuous_mode"]["heat_dumped_kwh"]
    orig_pfrac = vs1["three_way_valve"]["primary_fraction"]
    vs1["continuous_mode"]["heat_dumped_kwh"] = -123.0
    vs1["three_way_valve"]["primary_fraction"] = 9.9
    vs2 = api_module.get_verbund_scene("basel_pool_st_jakob", servers=4)
    assert vs2["continuous_mode"]["heat_dumped_kwh"] == orig_dump
    assert vs2["three_way_valve"]["primary_fraction"] == orig_pfrac

    pr1 = api_module.get_pilot_reality_sensitivity(servers=4)
    orig_w45 = pr1["ashrae_w45"]["ashrae_w45_supply_limit_c"]
    pr1["ashrae_w45"]["ashrae_w45_supply_limit_c"] = -1
    pr2 = api_module.get_pilot_reality_sensitivity(servers=4)
    assert pr2["ashrae_w45"]["ashrae_w45_supply_limit_c"] == orig_w45


def test_adapter_rejects_boolean_and_non_strict_int_hourly_active_servers() -> None:
    weather = load_weather_zurich(site="basel")
    with TestClient(app) as client:
        for bad_series in (
            [True] * 8760,
            [False] * 8760,
            [1.0] * 8760,
            ["1"] * 8760,
        ):
            resp = client.post(
                "/api/v1/scheduler/adapter",
                json={"n_servers": 4, "hourly_active_servers": bad_series},
            )
            assert resp.status_code == 422

        bool_decisions = [
            {
                "timestamp_zurich": h.timestamp_zurich,
                "server_power_kw": 1.0,
                "active_servers": True if i == 0 else 1,
                "jobs_completed": 0,
                "jobs_missed_deadline": 0,
                "heat_signal_on": True,
            }
            for i, h in enumerate(weather.hours)
        ]
        dec_resp = client.post(
            "/api/v1/scheduler/adapter",
            json={"n_servers": 4, "decisions": bool_decisions},
        )
        assert dec_resp.status_code == 422
