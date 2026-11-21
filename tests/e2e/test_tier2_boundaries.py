"""Tier 2 opaque-box boundary, edge-case and error-handling tests across F1-F10."""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = REPO_ROOT / "data"
ASSETS_DIR: Path = REPO_ROOT / "assets"
GLB_DIR: Path = ASSETS_DIR / "glb"
SRC_DIR: Path = REPO_ROOT / "src"
WEB_DIR: Path = REPO_ROOT / "web"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def wait_until(
    predicate: Callable[[], Any],
    timeout_s: float = 15.0,
    interval_s: float = 0.15,
    description: str = "condition",
) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        res = predicate()
        if res:
            return res
        time.sleep(interval_s)
    raise AssertionError(f"Timed out waiting for {description}")


CONSERVATION_TOL_KWH = 1e-6


def _day_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("hours")
    if rows is None:
        rows = payload.get("hourly")
    assert isinstance(rows, list)
    return rows


def _parse_glb_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    magic, _, _ = struct.unpack("<4sII", raw[:12])
    assert magic == b"glTF"
    chunk_len, _ = struct.unpack("<II", raw[12:20])
    return json.loads(raw[20 : 20 + chunk_len].decode("utf-8"))


# =============================================================================
# F1: DST, Heating-Day & Dataset Boundaries (5 tests)
# =============================================================================


def test_t2_f1_spring_dst_2025_03_30_has_23_hours_no_02am(
    api_client: httpx.Client,
) -> None:
    resp = api_client.get("/api/v1/simulation/day/2025-03-30?strategy=smart&servers=4")
    assert resp.status_code == 200
    data = resp.json()
    rows = _day_rows(data)
    assert data["hours_count"] == 23
    assert len(rows) == 23
    assert data["is_dst_transition"] is True
    timestamps = [r["timestamp_zurich"] for r in rows]
    assert not any("T02:00:00" in ts for ts in timestamps)
    assert "2025-03-30T01:00:00+01:00" in timestamps
    assert "2025-03-30T03:00:00+02:00" in timestamps


def test_t2_f1_autumn_dst_2025_10_26_has_25_hours_dual_02am(
    api_client: httpx.Client,
) -> None:
    resp = api_client.get("/api/v1/simulation/day/2025-10-26?strategy=smart&servers=4")
    assert resp.status_code == 200
    data = resp.json()
    rows = _day_rows(data)
    assert data["hours_count"] == 25
    assert len(rows) == 25
    assert data["is_dst_transition"] is True
    two_am = [r["timestamp_zurich"] for r in rows if "T02:00:00" in r["timestamp_zurich"]]
    assert two_am == [
        "2025-10-26T02:00:00+02:00",
        "2025-10-26T02:00:00+01:00",
    ]


def test_t2_f1_march_and_october_timeseries_hour_counts(
    api_client: httpx.Client,
) -> None:
    mar = api_client.get("/api/v1/simulation/timeseries?resolution=hourly&month=3")
    assert mar.status_code == 200
    assert len(mar.json()["rows"]) == 743

    oct_resp = api_client.get("/api/v1/simulation/timeseries?resolution=hourly&month=10")
    assert oct_resp.status_code == 200
    assert len(oct_resp.json()["rows"]) == 745


def test_t2_f1_heating_day_12c_threshold_excludes_summer_space_heat(
    api_client: httpx.Client,
) -> None:
    summer = api_client.get("/api/v1/simulation/day/2025-07-15?strategy=smart&servers=4").json()
    assert summer["is_heating_day"] is False
    for row in _day_rows(summer):
        assert row["space_heating_kw"] == 0.0
        assert row["dhw_kw"] == pytest.approx(0.5, abs=1e-6)


def test_t2_f1_tampered_csv_detected_by_manifest_verifier(tmp_path: Path) -> None:
    tampered = tmp_path / "data"
    shutil.copytree(DATA_DIR, tampered)
    target = tampered / "open_meteo_basel_2025_utc.csv"
    raw = bytearray(target.read_bytes())
    raw[-12] ^= 0x01
    target.write_bytes(bytes(raw))

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from digitalheat.data_loader import verify_manifest; "
                f"verify_manifest(Path({str(tampered)!r}))"
            ),
        ],
        cwd=str(REPO_ROOT),
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "SHA-256 mismatch" in proc.stderr


# =============================================================================
# F2: Conservation, Tank Clamping & Zero-Division Boundaries (6 tests)
# =============================================================================


@pytest.mark.parametrize("servers", [1, 16])
def test_t2_f2_tank_soc_clamped_to_0_35_at_1_and_16_servers(
    api_client: httpx.Client, servers: int
) -> None:
    resp = api_client.get(
        f"/api/v1/simulation/timeseries?strategy=baseline&servers={servers}&resolution=hourly"
    )
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 8760
    assert all(0.0 <= r["tank_soc_kwh"] <= 35.0 for r in rows)


def test_t2_f2_zero_server_hours_sold_break_even_division_guard(
    api_client: httpx.Client,
) -> None:
    resp = api_client.post(
        "/api/v1/scheduler/adapter",
        json={
            "adapter_name": "all_idle_guard_check",
            "n_servers": 4,
            "site": "davos",
            "hourly_active_servers": [0] * 8760,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    kpis = body["kpis"]
    assert kpis["server_hours_sold"] == 0.0
    assert kpis["break_even_defined"] is False
    assert kpis["break_even_computing_price_chf_per_server_hour"] == 0.0


def test_t2_f2_full_tank_start_35_kwh_preserves_conservation(
    api_client: httpx.Client,
) -> None:
    resp = api_client.post(
        "/api/v1/scheduler/adapter",
        json={
            "adapter_name": "full_tank_start_check",
            "n_servers": 4,
            "site": "davos",
            "s_start_kwh": 35.0,
            "hourly_active_servers": [1] * 8760,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    kpis = body["kpis"]
    assert body["s_start_kwh"] == pytest.approx(35.0, abs=1e-6)
    assert body["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
    lhs = 0.8 * kpis["electricity_consumed_kwh"] + body["s_start_kwh"]
    rhs = kpis["heat_used_kwh"] + kpis["heat_dumped_kwh"] + body["s_end_kwh"]
    assert abs(lhs - rhs) <= CONSERVATION_TOL_KWH


def test_t2_f2_annual_invalid_servers_bounds_return_422(
    api_client: httpx.Client,
) -> None:
    assert api_client.get("/api/v1/simulation/annual?servers=0").status_code == 422
    assert api_client.get("/api/v1/simulation/annual?servers=17").status_code == 422


def test_t2_f2_annual_invalid_strategy_and_site_return_422(
    api_client: httpx.Client,
) -> None:
    assert api_client.get("/api/v1/simulation/annual?strategy=nonexistent").status_code == 422
    assert api_client.get("/api/v1/simulation/annual?site=zurich").status_code == 422


def test_t2_f2_annual_unregistered_adapter_returns_404(
    api_client: httpx.Client,
) -> None:
    resp = api_client.get("/api/v1/simulation/annual?strategy=adapter&servers=15&site=davos")
    assert resp.status_code == 404


# =============================================================================
# F3: Worker Signal, Queue & Error Boundaries (5 tests)
# =============================================================================


def test_t2_f3_worker_start_invalid_job_type_returns_422(
    worker_guard: httpx.Client,
) -> None:
    resp = worker_guard.post("/api/v1/worker/start", json={"job_type": "crypto_mining"})
    assert resp.status_code == 422


def test_t2_f3_worker_heat_signal_out_of_range_soc_returns_422(
    worker_guard: httpx.Client,
) -> None:
    low = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": -0.1, "heat_demand_kw": 2.0},
    )
    assert low.status_code == 422
    high = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 35.1, "heat_demand_kw": 2.0},
    )
    assert high.status_code == 422


def test_t2_f3_worker_heat_signal_zero_queue_depth_pauses_worker(
    worker_guard: httpx.Client,
) -> None:
    worker_guard.post("/api/v1/worker/start", json={"job_type": "iiif_embeddings"})
    resp = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 0.0, "heat_demand_kw": 10.0, "queue_depth": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["heat_signal_on"] is False
    assert body["state"] == "paused"


def test_t2_f3_worker_pause_resume_stop_when_idle_are_safe(
    worker_guard: httpx.Client,
) -> None:
    worker_guard.post("/api/v1/worker/stop")
    for endpoint in ("/api/v1/worker/pause", "/api/v1/worker/resume", "/api/v1/worker/stop"):
        resp = worker_guard.post(endpoint)
        assert resp.status_code == 200
        assert resp.json()["state"] == "idle"


def test_t2_f3_worker_heat_signal_threshold_34_8_pauses_vs_34_0_runs(
    worker_guard: httpx.Client,
) -> None:
    worker_guard.post("/api/v1/worker/start", json={"job_type": "archive_ocr"})
    # 35.0 - 34.8 + 0.5 = 0.7 kWh < 0.8 kW_th -> pause
    pause_resp = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 34.8, "heat_demand_kw": 0.5, "queue_depth": 3},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["state"] == "paused"

    # 35.0 - 34.0 + 0.5 = 1.5 kWh >= 0.8 kW_th -> run
    run_resp = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 34.0, "heat_demand_kw": 0.5, "queue_depth": 3},
    )
    assert run_resp.status_code == 200
    assert run_resp.json()["state"] == "running"


# =============================================================================
# F4: API Date, Query & Adapter 422 Boundaries (6 tests)
# =============================================================================


def test_t2_f4_day_malformed_date_returns_422(api_client: httpx.Client) -> None:
    assert api_client.get("/api/v1/simulation/day/2025-13-45").status_code == 422
    assert api_client.get("/api/v1/simulation/day/not-a-date").status_code == 422


def test_t2_f4_day_outside_2025_returns_404(api_client: httpx.Client) -> None:
    assert api_client.get("/api/v1/simulation/day/2024-07-15").status_code == 404
    assert api_client.get("/api/v1/simulation/day/2026-01-15").status_code == 404


def test_t2_f4_timeseries_invalid_month_and_resolution_return_422(
    api_client: httpx.Client,
) -> None:
    assert api_client.get("/api/v1/simulation/timeseries?month=0").status_code == 422
    assert api_client.get("/api/v1/simulation/timeseries?month=13").status_code == 422
    assert api_client.get("/api/v1/simulation/timeseries?resolution=weekly").status_code == 422


def test_t2_f4_adapter_empty_payload_returns_422(api_client: httpx.Client) -> None:
    resp = api_client.post("/api/v1/scheduler/adapter", json={})
    assert resp.status_code == 422


def test_t2_f4_adapter_wrong_length_schedule_returns_422(
    api_client: httpx.Client,
) -> None:
    resp = api_client.post(
        "/api/v1/scheduler/adapter",
        json={"n_servers": 4, "site": "basel", "hourly_active_servers": [1] * 100},
    )
    assert resp.status_code == 422


def test_t2_f4_adapter_out_of_bounds_servers_or_negative_power_returns_422(
    api_client: httpx.Client,
) -> None:
    over = api_client.post(
        "/api/v1/scheduler/adapter",
        json={"n_servers": 4, "site": "basel", "hourly_active_servers": [5] * 8760},
    )
    assert over.status_code == 422

    neg = api_client.post(
        "/api/v1/scheduler/adapter",
        json={"n_servers": 4, "site": "basel", "hourly_server_power_kw": [-0.5] * 8760},
    )
    assert neg.status_code == 422


# =============================================================================
# F5: 3D Asset, Manifest & Static Serving Boundaries (5 tests)
# =============================================================================


def test_t2_f5_missing_glb_returns_404(api_client: httpx.Client) -> None:
    assert api_client.get("/assets/glb/nonexistent_mesh.glb").status_code == 404


def test_t2_f5_glb_path_traversal_blocked(api_client: httpx.Client) -> None:
    resp = api_client.get("/assets/glb/../../pyproject.toml")
    assert resp.status_code in {400, 404}


def test_t2_f5_hardware_glbs_no_forbidden_helper_nodes() -> None:
    for path in sorted(GLB_DIR.glob("hardware_*.glb")):
        gltf = _parse_glb_json(path)
        names = [n.get("name", "") for n in gltf.get("nodes", [])]
        for forbidden in ("ConnectionPoints", "Connection_Points", "PhysicsMaterials"):
            assert not any(forbidden in name for name in names), f"{path.name} leaked {forbidden}"


def test_t2_f5_house_bim_shell_no_origin_helper_nodes() -> None:
    gltf = _parse_glb_json(GLB_DIR / "house_bim_shell.glb")
    names = [n.get("name", "").lower() for n in gltf.get("nodes", [])]
    for forbidden in ("origin", "geo-reference"):
        assert not any(forbidden in name for name in names), f"Helper node leaked: {forbidden}"


def test_t2_f5_basement_clearance_supports_orv3_rack_height() -> None:
    manifest = json.loads((ASSETS_DIR / "manifest.json").read_text(encoding="utf-8"))
    interior = manifest["assets"]["house_bim_shell"]["basement_interior_bounds"]
    width_x, height_y, depth_z = interior["size_m"]
    assert height_y >= 2.65
    assert height_y - 2.24 >= 0.40
    assert width_x >= 6.0 and depth_z >= 4.5


# =============================================================================
# F6: Cockpit UI Boundary & Zero-Frontend-Math Checks (5 tests)
# =============================================================================


def test_t2_f6_cockpit_servers_select_1_and_12_updates_scorecard(
    cockpit_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    page, errors = cockpit_page
    for count in ("1", "12"):
        expected = api_client.get(
            f"/api/v1/simulation/annual?strategy=smart&servers={count}"
        ).json()["kpis"]
        page.locator("[data-testid='servers-select']").select_option(count)
        wait_until(
            lambda c=count: (
                c in page.locator("[data-testid='kpi-servers-per-house']").inner_text()
            ),
            timeout_s=10.0,
            description=f"servers={count} KPI update",
        )
        covered_pct = f"{expected['heat_demand_covered_share'] * 100:.1f}"
        covered_txt = page.locator("[data-testid='kpi-heat-covered']").inner_text()
        assert covered_pct[:3] in covered_txt or str(expected["servers_per_house"]) in page.locator(
            "[data-testid='kpi-servers-per-house']"
        ).inner_text()
    errors.assert_clean()


def test_t2_f6_cockpit_strategy_select_baseline_shows_summer_dump(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='strategy-select']").select_option("baseline")
    wait_until(
        lambda: any(
            ch.isdigit() and ch != "0"
            for ch in page.locator("[data-testid='kpi-heat-dumped']").inner_text()
        ),
        timeout_s=10.0,
        description="baseline heat dump KPI update",
    )
    dump_text = page.locator("[data-testid='kpi-heat-dumped']").inner_text()
    digits = "".join(ch for ch in dump_text if ch.isdigit() or ch == ".")
    assert float(digits) > 1000.0
    errors.assert_clean()


def test_t2_f6_cockpit_day_picker_spring_dst_23h_no_errors(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    picker = page.locator("[data-testid='day-picker']")
    picker.fill("2025-03-30")
    picker.dispatch_event("change")
    wait_until(
        lambda: page.locator("[data-testid='readout-outdoor-temp']").inner_text().strip() != "",
        timeout_s=10.0,
        description="spring DST day render",
    )
    errors.assert_clean()


def test_t2_f6_cockpit_day_picker_autumn_dst_25h_no_errors(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    picker = page.locator("[data-testid='day-picker']")
    picker.fill("2025-10-26")
    picker.dispatch_event("change")
    wait_until(
        lambda: page.locator("[data-testid='readout-outdoor-temp']").inner_text().strip() != "",
        timeout_s=10.0,
        description="autumn DST day render",
    )
    errors.assert_clean()


def test_t2_f6_cockpit_hour_slider_boundary_0_and_23_no_errors(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    slider = page.locator("[data-testid='hour-slider']")
    for hour in ("0", "23"):
        slider.fill(hour)
        slider.dispatch_event("input")
        slider.dispatch_event("change")
    errors.assert_clean()


# =============================================================================
# F7: Verbund Scene & Boundary Conditions (5 tests)
# =============================================================================


def test_t2_f7_verbund_unknown_scene_returns_404(api_client: httpx.Client) -> None:
    assert api_client.get("/api/v1/verbund/scenes/unknown_site").status_code == 404


def test_t2_f7_verbund_invalid_servers_returns_422(api_client: httpx.Client) -> None:
    assert api_client.get("/api/v1/verbund/scenes?servers=0").status_code == 422
    assert api_client.get("/api/v1/verbund/scenes/basel_erdsonde?servers=17").status_code == 422


def test_t2_f7_verbund_three_way_valve_fractions_sum_to_one(
    api_client: httpx.Client,
) -> None:
    scenes = api_client.get("/api/v1/verbund/scenes?servers=4").json()["scenes"]
    for scene in scenes:
        split = scene["july_valve_split_pct"]
        assert split["primary_pct"] + split["secondary_pct"] + split["dump_pct"] == pytest.approx(
            100.0, abs=0.3
        )
        valve = scene.get("three_way_valve") or scene.get("diverter_valve_state")
        assert valve is not None
        total_frac = sum(float(v) for v in valve.values())
        assert total_frac == pytest.approx(1.0, abs=0.01)


def test_t2_f7_verbund_ashrae_w45_compliance_boundary(api_client: httpx.Client) -> None:
    for sid in ("basel_pool_st_jakob", "sole_uno", "ara_basel_biogas"):
        scene = api_client.get(f"/api/v1/verbund/scenes/{sid}?servers=4").json()
        assert scene["ashrae_w45_compliant"] is True
        sink_t = (
            scene["secondary_supply_temp_c"]
            if scene["secondary_supply_temp_c"] is not None
            else scene["primary_supply_temp_c"]
        )
        assert sink_t <= 45.0
    erd = api_client.get("/api/v1/verbund/scenes/basel_erdsonde?servers=4").json()
    assert erd["primary_w45_compliant"] is False
    assert erd["secondary_w45_compliant"] is True
    assert erd["secondary_supply_temp_c"] <= 45.0


def test_t2_f7_verbund_high_temp_cip_loop_flow_and_delta_t(
    api_client: httpx.Client,
) -> None:
    cip = api_client.get("/api/v1/verbund/scenes/brewery_dairy_cip?servers=4").json()
    assert cip["primary_flow_l_per_min"] > 0.0
    supply_t = (
        cip["secondary_supply_temp_c"]
        if cip["secondary_supply_temp_c"] is not None
        else cip["primary_supply_temp_c"]
    )
    return_t = (
        cip["secondary_return_temp_c"]
        if cip["secondary_return_temp_c"] is not None
        else cip["primary_return_temp_c"]
    )
    assert supply_t > return_t
    assert cip["continuous_mode"]["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH


# =============================================================================
# F8: Sensitivity Boundary & Tariff Monotonicity Checks (5 tests)
# =============================================================================


def test_t2_f8_sensitivity_invalid_servers_returns_422(
    api_client: httpx.Client,
) -> None:
    assert api_client.get("/api/v1/sensitivity/pilot-reality?servers=0").status_code == 422
    assert api_client.get("/api/v1/sensitivity/pilot-reality?servers=17").status_code == 422


def test_t2_f8_sensitivity_heatwise_idle_ratios_bounded(
    api_client: httpx.Client,
) -> None:
    hw = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()["heatwise"]
    assert 0.15 < hw["idle_to_full_ratio_range"]["min"] <= hw["idle_to_full_ratio_range"]["max"]
    assert hw["idle_to_full_ratio_range"]["max"] < 0.35


def test_t2_f8_sensitivity_capex_cliff_monotonic_utilisation_drop(
    api_client: httpx.Client,
) -> None:
    rows = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()[
        "capex_utilisation_cliff"
    ]["cliff_rows"]
    utils = [r["server_utilisation_pct"] for r in rows]
    assert utils == sorted(utils, reverse=True)
    assert utils[0] > utils[-1]


def test_t2_f8_sensitivity_tariff_ordering_h4_exceeds_industrial(
    api_client: httpx.Client,
) -> None:
    tariffs = {
        r["category"]: r["total_incl_vat_chf_per_kwh"]
        for r in api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()["tariff_gap"][
            "tariffs"
        ]
    }
    assert tariffs["H4"] > tariffs["C4"] > tariffs["C5"] > tariffs["C6"]


def test_t2_f8_sensitivity_mlperf_full_reuse_always_cheaper(
    api_client: httpx.Client,
) -> None:
    rows = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()["mlperf_v51"][
        "rows"
    ]
    assert len(rows) == 11
    for row in rows:
        assert row["chf_per_1m_units_at_full_reuse"] < row["chf_per_1m_units_at_zero_reuse"]


# =============================================================================
# F9: Rejected Tools & Scope Guardrails (5 tests)
# =============================================================================


def test_t2_f9_no_rejected_tools_imported_or_referenced() -> None:
    rejected = ("warp.fem", "openfoam", "physicsnemo", "kpis.ts", "dcgm.csv")
    targets = list(SRC_DIR.rglob("*.py"))
    if WEB_DIR.is_dir():
        targets.extend(WEB_DIR.rglob("*.js"))
        targets.extend(WEB_DIR.rglob("*.html"))
    for fpath in targets:
        if "vendor" in fpath.parts:
            continue
        txt = fpath.read_text(encoding="utf-8", errors="ignore").lower()
        for tok in rejected:
            assert tok not in txt, f"Rejected tool/artifact {tok} referenced in {fpath}"


def test_t2_f9_no_district_heating_feed_in_revenue_in_server_income(
    api_client: httpx.Client,
) -> None:
    base = api_client.get("/api/v1/simulation/annual?strategy=baseline&servers=8").json()["kpis"]
    # Computing income equals server_hours_sold * 0.25 CHF/srv-h (no heat feed-in revenue).
    assert base["computing_income_chf"] == pytest.approx(base["server_hours_sold"] * 0.25, rel=1e-4)


def test_t2_f9_no_heat_pump_cop_inside_reference_house_conservation(
    api_client: httpx.Client,
) -> None:
    smart = api_client.get("/api/v1/simulation/annual?strategy=smart&servers=4").json()
    kpis = smart["kpis"]
    # Direct 0.80 recovery without heat pump lifting inside the tank energy equation.
    assert kpis["heat_recovered_kwh"] == pytest.approx(
        0.80 * kpis["electricity_consumed_kwh"], abs=1e-4
    )


def test_t2_f9_provenance_explicit_open_blueprint_disclaimer(
    api_client: httpx.Client,
) -> None:
    prov = api_client.get("/api/v1/provenance").json()
    blob = json.dumps(prov)
    assert "not a surveyed private Basel residence" in blob
    assert "buildingSMART" in blob


def test_t2_f9_cli_simulate_invalid_servers_exits_nonzero(run_cli: Any) -> None:
    proc = run_cli(["simulate", "--strategy", "smart", "--servers", "0"])
    assert proc.returncode != 0
    bad_strat = run_cli(["simulate", "--strategy", "invalid_mode", "--servers", "4"])
    assert bad_strat.returncode != 0


# =============================================================================
# F10: Hygiene & Packaging Edge Cases (5 tests)
# =============================================================================


def test_t2_f10_no_env_file_inside_digitalheat() -> None:
    for fpath in REPO_ROOT.rglob(".env*"):
        if ".venv" in fpath.parts:
            continue
        pytest.fail(f"Forbidden .env file in submission repo: {fpath}")


def test_t2_f10_no_single_file_over_20_mb_in_repo() -> None:
    for fpath in REPO_ROOT.rglob("*"):
        if ".venv" in fpath.parts or ".git" in fpath.parts:
            continue
        if fpath.is_file():
            assert fpath.stat().st_size < 20 * 1024 * 1024, f"Oversized file: {fpath}"


def test_t2_f10_readme_documents_attributions_and_cpu_uv_startup() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "swisstopo" in readme
    assert "OpenStreetMap" in readme
    assert "uv" in readme


def test_t2_f10_pyproject_excludes_gpu_packages() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    for pkg in ("torch", "jax", "cupy", "warp-lang", "nvidia-"):
        assert pkg not in pyproject, f"Unexpected GPU dependency {pkg} in pyproject.toml"


def test_t2_f10_data_manifest_sha256_hex_format_valid() -> None:
    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    hex_pat = re.compile(r"^[0-9a-f]{64}$")
    for name, meta in manifest["files"].items():
        assert hex_pat.match(meta["sha256"]), f"Invalid sha256 for {name}"
    for name, meta in manifest["upstream_sources"].items():
        assert hex_pat.match(meta["sha256"]), f"Invalid upstream sha256 for {name}"
