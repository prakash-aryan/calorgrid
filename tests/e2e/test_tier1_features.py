"""Tier 1 opaque-box E2E tests covering happy paths for all 10 features (F1-F10)."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import subprocess
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

CONSERVATION_TOL_KWH = 1e-6


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

EXPECTED_13_GLBS = (
    "context_neighborhood.glb",
    "verbund_st_jakob.glb",
    "verbund_sole_uno.glb",
    "verbund_ara_basel.glb",
    "verbund_davos.glb",
    "house_bim_shell.glb",
    "hardware_server_rack.glb",
    "hardware_cdu_exchanger.glb",
    "hardware_buffer_tank.glb",
    "hardware_piping.glb",
    "hardware_valve_3way.glb",
    "hardware_power_panel.glb",
    "hardware_basement_enclosure.glb",
)

EXPECTED_VERBUND_SCENES = (
    "basel_erdsonde",
    "basel_pool_st_jakob",
    "sole_uno",
    "hotel_laundry_dhw",
    "brewery_dairy_cip",
    "ara_basel_biogas",
    "davos_alpine",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_glb_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    magic, version, total_len = struct.unpack("<4sII", raw[:12])
    assert magic == b"glTF", f"{path.name} bad GLB magic: {magic!r}"
    assert version == 2
    assert total_len == len(raw)
    chunk_len, chunk_type = struct.unpack("<II", raw[12:20])
    assert chunk_type == 0x4E4F534A
    return json.loads(raw[20 : 20 + chunk_len].decode("utf-8"))


def _day_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("hours")
    if rows is None:
        rows = payload.get("hourly")
    assert isinstance(rows, list)
    return rows


def _worker_units(status: dict[str, Any]) -> int:
    if "units_completed" in status:
        return int(status["units_completed"])
    return int(status.get("units_processed", 0))


def _worker_scaled_throughput(status: dict[str, Any]) -> float:
    if isinstance(status.get("scaled_1kw"), dict):
        return float(status["scaled_1kw"].get("scaled_units_per_server_hour", 0.0))
    return float(status.get("scaled_units_per_1kw_server_hour", 0.0))


# =============================================================================
# F1: Pinned Datasets & DST Weather (5 tests)
# =============================================================================


def test_t1_f1_data_manifest_sha256_and_files() -> None:
    manifest_path = DATA_DIR / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest["files"]
    expected = {
        "open_meteo_basel_2025_utc.csv",
        "open_meteo_davos_2025_utc.csv",
        "heatwise_summary.json",
        "mlperf_v51_offline_summary.json",
        "elcom_basel_2026.json",
    }
    assert set(files.keys()) == expected
    for name, meta in files.items():
        target = DATA_DIR / name
        assert target.is_file(), f"Missing dataset {name}"
        assert target.stat().st_size == meta["bytes"]
        assert _sha256(target) == meta["sha256"]


def test_t1_f1_basel_weather_2025_benchmarks(api_client: httpx.Client) -> None:
    resp = api_client.get("/api/v1/simulation/annual?strategy=smart&servers=4&site=basel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["heating_days_count"] == 206
    kpis = data["kpis"]
    assert kpis["heat_demand_total_kwh"] == pytest.approx(31231.0, abs=2.0)

    ts = api_client.get(
        "/api/v1/simulation/timeseries?strategy=smart&servers=4&site=basel&resolution=hourly"
    )
    assert ts.status_code == 200
    rows = ts.json()["rows"]
    assert len(rows) == 8760
    assert min(r["outdoor_temp_c"] for r in rows) == pytest.approx(-5.6, abs=0.1)


def test_t1_f1_davos_weather_2025_benchmarks(api_client: httpx.Client) -> None:
    resp = api_client.get("/api/v1/simulation/annual?strategy=smart&servers=4&site=davos")
    assert resp.status_code == 200
    data = resp.json()
    assert data["heating_days_count"] == 291

    ts = api_client.get(
        "/api/v1/simulation/timeseries?strategy=smart&servers=4&site=davos&resolution=hourly"
    )
    assert ts.status_code == 200
    rows = ts.json()["rows"]
    assert len(rows) == 8760
    assert min(r["outdoor_temp_c"] for r in rows) == pytest.approx(-17.0, abs=0.1)


def test_t1_f1_elcom_2026_tariffs_h4_and_commercial() -> None:
    elcom = json.loads((DATA_DIR / "elcom_basel_2026.json").read_text(encoding="utf-8"))
    cats = elcom["categories"]
    assert cats["H4"]["total_chf_per_kwh"] == pytest.approx(0.3325, abs=1e-6)
    assert cats["C5"]["total_chf_per_kwh"] == pytest.approx(0.26014, abs=1e-6)
    assert cats["C6"]["total_chf_per_kwh"] == pytest.approx(0.25267, abs=1e-6)
    assert elcom["vat_rate"] == pytest.approx(0.081, abs=1e-6)


def test_t1_f1_heatwise_and_mlperf_summaries() -> None:
    hw = json.loads((DATA_DIR / "heatwise_summary.json").read_text(encoding="utf-8"))
    assert len(hw["cases"]) == 8
    assert len(hw["idle_to_full_ratios"]) == 8

    ml = json.loads((DATA_DIR / "mlperf_v51_offline_summary.json").read_text(encoding="utf-8"))
    assert ml["metadata"]["offline_power_entries"] == 11
    assert len(ml["offline_power_rows"]) == 11


# =============================================================================
# F2: Reference Thermal Model & Scheduler (6 tests)
# =============================================================================


def test_t1_f2_health_endpoint_ok(api_client: httpx.Client) -> None:
    resp = api_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert body["datasets_verified"] is True
    assert body.get("scheduler")


def test_t1_f2_annual_smart_kpis_and_conservation(api_client: httpx.Client) -> None:
    resp = api_client.get("/api/v1/simulation/annual?strategy=smart&servers=4&site=basel")
    assert resp.status_code == 200
    body = resp.json()
    assert "hourly" not in body or not body["hourly"]
    assert body["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
    assert body["max_hourly_residual_kwh"] <= CONSERVATION_TOL_KWH

    kpis = body["kpis"]
    for key in (
        "servers_per_house",
        "heat_demand_covered_share",
        "heat_used_kwh",
        "heat_demand_total_kwh",
        "backup_heat_kwh",
        "heat_dumped_kwh",
        "server_utilisation",
        "missed_deadlines_pct",
        "electricity_cost_chf",
        "computing_income_chf",
        "heat_value_used_chf",
        "break_even_computing_price_chf_per_server_hour",
        "server_hours_sold",
        "electricity_consumed_kwh",
    ):
        assert key in kpis

    lhs = 0.8 * kpis["electricity_consumed_kwh"] + body["s_start_kwh"]
    rhs = kpis["heat_used_kwh"] + kpis["heat_dumped_kwh"] + body["s_end_kwh"]
    assert abs(lhs - rhs) <= CONSERVATION_TOL_KWH


def test_t1_f2_annual_baseline_vs_smart_and_negative_payoff(api_client: httpx.Client) -> None:
    smart = api_client.get("/api/v1/simulation/annual?strategy=smart&servers=4").json()
    base = api_client.get("/api/v1/simulation/annual?strategy=baseline&servers=4").json()

    assert base["kpis"]["heat_dumped_kwh"] > 3000.0
    assert smart["kpis"]["heat_dumped_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert (
        smart["kpis"]["break_even_computing_price_chf_per_server_hour"]
        < base["kpis"]["break_even_computing_price_chf_per_server_hour"]
    )
    assert "negative_payoff_statement" in smart
    assert len(smart["negative_payoff_statement"]) > 20
    assert "baseline_comparison" in smart
    assert smart["baseline_comparison"]["heat_dumped_reduction_kwh"] > 3000.0


def test_t1_f2_timeseries_daily_and_july_hourly(api_client: httpx.Client) -> None:
    daily = api_client.get(
        "/api/v1/simulation/timeseries?strategy=smart&servers=4&resolution=daily"
    )
    assert daily.status_code == 200
    assert len(daily.json()["rows"]) == 365

    july = api_client.get(
        "/api/v1/simulation/timeseries?strategy=baseline&servers=4&resolution=hourly&month=7"
    )
    assert july.status_code == 200
    july_rows = july.json()["rows"]
    assert len(july_rows) == 744
    assert sum(r["heat_dumped_kw"] for r in july_rows) > 0.0


def test_t1_f2_day_jan_peak_2025_01_15(api_client: httpx.Client) -> None:
    resp = api_client.get("/api/v1/simulation/day/2025-01-15?strategy=smart&servers=4")
    assert resp.status_code == 200
    data = resp.json()
    rows = _day_rows(data)
    assert len(rows) == 24
    assert data["is_heating_day"] is True
    assert all(r["space_heating_kw"] > 0.0 for r in rows)
    assert all(r["dhw_kw"] == pytest.approx(0.5, abs=1e-6) for r in rows)


def test_t1_f2_scheduler_adapter_valid_schedule(api_client: httpx.Client) -> None:
    payload = {
        "adapter_name": "e2e_test_adapter",
        "n_servers": 4,
        "site": "basel",
        "hourly_active_servers": [2] * 8760,
    }
    resp = api_client.post("/api/v1/scheduler/adapter", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
    assert body["kpis"]["server_utilisation"] == pytest.approx(0.5, abs=0.02)


# =============================================================================
# F3: Real CPU Worker & Live Control (5 tests)
# =============================================================================


def test_t1_f3_worker_initial_status_and_catalog_metadata(
    worker_guard: httpx.Client,
) -> None:
    worker_guard.post("/api/v1/worker/stop")
    resp = worker_guard.get("/api/v1/worker/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "idle"
    assert body["pid"] is None
    assert "arrival_rate_source" in body and len(body["arrival_rate_source"]) > 10


def test_t1_f3_worker_archive_ocr_real_cpu_execution(worker_guard: httpx.Client) -> None:
    resp = worker_guard.post("/api/v1/worker/start", json={"job_type": "archive_ocr"})
    assert resp.status_code == 200
    started = resp.json()
    assert started["state"] == "running"
    assert isinstance(started["pid"], int) and started["pid"] > 0

    status = wait_until(
        lambda: (
            s
            if _worker_units(s := worker_guard.get("/api/v1/worker/status").json()) >= 1
            and float(s.get("measured_energy_j", 0.0)) > 0.0
            else None
        ),
        timeout_s=15.0,
        description="archive_ocr unit completion",
    )
    assert _worker_scaled_throughput(status) > 0.0


def test_t1_f3_worker_iiif_embeddings_real_cpu_execution(
    worker_guard: httpx.Client,
) -> None:
    resp = worker_guard.post("/api/v1/worker/start", json={"job_type": "iiif_embeddings"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "running"

    status = wait_until(
        lambda: (
            s
            if _worker_units(s := worker_guard.get("/api/v1/worker/status").json()) >= 1
            and float(s.get("measured_energy_j", 0.0)) > 0.0
            else None
        ),
        timeout_s=15.0,
        description="iiif_embeddings unit completion",
    )
    assert status["job_type"] == "iiif_embeddings"
    assert _worker_scaled_throughput(status) > 0.0


def test_t1_f3_worker_heat_signal_pause_and_resume_pid(
    worker_guard: httpx.Client,
) -> None:
    start = worker_guard.post("/api/v1/worker/start", json={"job_type": "archive_ocr"}).json()
    pid = start["pid"]
    assert pid is not None

    paused = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 34.8, "heat_demand_kw": 0.5, "outdoor_temp_c": 22.0},
    )
    assert paused.status_code == 200
    p_body = paused.json()
    assert p_body["state"] == "paused"
    assert p_body["heat_signal_on"] is False
    os_state = p_body.get("psutil_status") or p_body.get("os_status")
    assert os_state == "stopped"

    resumed = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 10.0, "heat_demand_kw": 6.0, "outdoor_temp_c": -2.0},
    )
    assert resumed.status_code == 200
    r_body = resumed.json()
    assert r_body["state"] == "running"
    assert r_body["heat_signal_on"] is True
    assert r_body["pid"] == pid


def test_t1_f3_worker_manual_pause_resume_stop_cleanup(
    worker_guard: httpx.Client,
) -> None:
    worker_guard.post("/api/v1/worker/start", json={"job_type": "iiif_embeddings"})
    paused = worker_guard.post("/api/v1/worker/pause")
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"

    resumed = worker_guard.post("/api/v1/worker/resume")
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "running"

    stopped = worker_guard.post("/api/v1/worker/stop")
    assert stopped.status_code == 200
    s_body = stopped.json()
    assert s_body["state"] == "idle"
    assert s_body["pid"] is None


# =============================================================================
# F4: Versioned FastAPI JSON Contract (5 tests)
# =============================================================================


def test_t1_f4_openapi_schema_exposes_v1_endpoints(api_client: httpx.Client) -> None:
    resp = api_client.get("/openapi.json")
    assert resp.status_code == 200
    paths = set(resp.json()["paths"].keys())
    expected = {
        "/api/v1/health",
        "/api/v1/simulation/annual",
        "/api/v1/simulation/timeseries",
        "/api/v1/worker/status",
        "/api/v1/worker/start",
        "/api/v1/worker/heat-signal",
        "/api/v1/worker/pause",
        "/api/v1/worker/resume",
        "/api/v1/worker/stop",
        "/api/v1/scheduler/adapter",
        "/api/v1/verbund/scenes",
        "/api/v1/verbund/scenes/{scene_id}",
        "/api/v1/sensitivity/pilot-reality",
        "/api/v1/provenance",
    }
    assert expected <= paths
    assert any(p.startswith("/api/v1/simulation/day/{") for p in paths)


def test_t1_f4_day_jul_dump_solar_geometry_and_sun_vector(
    api_client: httpx.Client,
) -> None:
    resp = api_client.get("/api/v1/simulation/day/2025-07-15?strategy=baseline&servers=4")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_heating_day"] is False
    rows = _day_rows(data)
    assert len(rows) == 24
    noon_row = max(rows, key=lambda r: r.get("solar_elevation_deg", -90.0))
    assert noon_row["solar_elevation_deg"] > 60.0
    irrad = noon_row.get("shortwave_radiation_wm2", noon_row.get("solar_irradiance_wm2", 0.0))
    assert irrad > 100.0
    sun_vec = noon_row["sun_direction_xyz"]
    assert len(sun_vec) == 3
    assert math.sqrt(sum(c * c for c in sun_vec)) == pytest.approx(1.0, abs=1e-3)


def test_t1_f4_static_glb_mime_type_and_magic_header(api_client: httpx.Client) -> None:
    resp = api_client.get("/assets/glb/context_neighborhood.glb")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("model/gltf-binary")
    assert resp.content[:4] == b"glTF"


def test_t1_f4_cli_verify_exits_zero(run_cli: Any) -> None:
    proc = run_cli(["verify"])
    assert proc.returncode == 0, f"cli verify failed: {proc.stderr}\n{proc.stdout}"


def test_t1_f4_cli_simulate_json_matches_api(
    run_cli: Any, api_client: httpx.Client
) -> None:
    proc = run_cli(
        ["simulate", "--strategy", "smart", "--servers", "4", "--site", "basel", "--json"]
    )
    assert proc.returncode == 0, f"cli simulate failed: {proc.stderr}"
    cli_data = json.loads(proc.stdout)
    api_data = api_client.get(
        "/api/v1/simulation/annual?strategy=smart&servers=4&site=basel"
    ).json()
    assert cli_data["kpis"]["heat_dumped_kwh"] == pytest.approx(
        api_data["kpis"]["heat_dumped_kwh"], abs=1e-6
    )
    assert cli_data["kpis"]["break_even_computing_price_chf_per_server_hour"] == pytest.approx(
        api_data["kpis"]["break_even_computing_price_chf_per_server_hour"], abs=1e-6
    )


# =============================================================================
# F5: 3D Geodata, BIM/IFC & SimReady GLBs (6 tests)
# =============================================================================


def test_t1_f5_all_13_glb_files_exist() -> None:
    for fname in EXPECTED_13_GLBS:
        path = GLB_DIR / fname
        assert path.is_file(), f"Missing GLB: {fname}"
        assert path.stat().st_size > 1024


def test_t1_f5_geodata_glbs_size_budgets_and_draco_webp() -> None:
    assert (GLB_DIR / "context_neighborhood.glb").stat().st_size < 5 * 1024 * 1024
    for fname in EXPECTED_13_GLBS[:5]:
        path = GLB_DIR / fname
        if fname != "context_neighborhood.glb":
            assert path.stat().st_size < int(1.5 * 1024 * 1024)
        gltf = _parse_glb_json(path)
        exts = set(gltf.get("extensionsUsed", []))
        assert "KHR_draco_mesh_compression" in exts
        assert "EXT_texture_webp" in exts


def test_t1_f5_house_bim_shell_cutaway_groups_and_clearance() -> None:
    path = GLB_DIR / "house_bim_shell.glb"
    assert path.stat().st_size < 1024 * 1024
    gltf = _parse_glb_json(path)
    assert "KHR_draco_mesh_compression" in set(gltf.get("extensionsUsed", []))
    names = {n.get("name") for n in gltf.get("nodes", []) if n.get("name")}
    assert {
        "shell_roof",
        "shell_walls_front",
        "shell_walls_rear_side",
        "shell_slabs",
        "shell_interior",
        "shell_hvac_chimney",
    } <= names


def test_t1_f5_hardware_simready_glbs_draco_and_meshes() -> None:
    hw_files = EXPECTED_13_GLBS[6:]
    total_hw_bytes = sum((GLB_DIR / f).stat().st_size for f in hw_files)
    assert total_hw_bytes <= 16 * 1024 * 1024
    for fname in hw_files:
        gltf = _parse_glb_json(GLB_DIR / fname)
        assert "KHR_draco_mesh_compression" in set(gltf.get("extensionsUsed", []))
        assert len(gltf.get("meshes", [])) >= 1


def test_t1_f5_assets_manifest_sha256_integrity() -> None:
    manifest = json.loads((ASSETS_DIR / "manifest.json").read_text(encoding="utf-8"))
    assets = manifest["assets"]
    for fname in EXPECTED_13_GLBS:
        key = Path(fname).stem
        assert key in assets
        glb_path = ASSETS_DIR / assets[key]["file"]
        assert glb_path.stat().st_size == assets[key]["bytes"]
        assert _sha256(glb_path) == assets[key]["sha256"]


def test_t1_f5_assets_attributions_mandatory_notices() -> None:
    attrib_text = (ASSETS_DIR / "attributions.json").read_text(encoding="utf-8")
    for token in (
        "(c) swisstopo",
        "(c) OpenStreetMap contributors, ODbL",
        "CC BY 4.0",
        "not a surveyed private Basel residence",
    ):
        assert token in attrib_text


# =============================================================================
# F6: DSX-Style Three.js Cockpit & Provenance (7 tests)
# =============================================================================


def test_t1_f6_cockpit_loads_cleanly_zero_errors(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    assert page.locator("[data-testid='cockpit-header']").is_visible()
    health_txt = page.locator("[data-testid='api-health-badge']").inner_text().lower()
    assert "ok" in health_txt or "healthy" in health_txt or "online" in health_txt
    errors.assert_clean()


def test_t1_f6_webgl_canvas_renders_nonzero_viewport(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    canvas = page.locator("[data-testid='canvas-3d']")
    assert canvas.is_visible()
    box = canvas.bounding_box()
    assert box is not None and box["width"] > 400 and box["height"] > 300
    has_webgl = page.evaluate(
        """() => {
            const c = document.querySelector("[data-testid='canvas-3d']");
            if (!c) return false;
            return !!(c.getContext("webgl2") || c.getContext("webgl"));
        }"""
    )
    assert has_webgl is True
    errors.assert_clean()


def test_t1_f6_scorecard_renders_all_7_kpis_from_api(
    cockpit_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    page, errors = cockpit_page
    kpis = api_client.get("/api/v1/simulation/annual?strategy=smart&servers=4").json()["kpis"]
    for tid in (
        "kpi-servers-per-house",
        "kpi-heat-covered",
        "kpi-heat-dumped",
        "kpi-server-utilisation",
        "kpi-missed-deadlines",
        "kpi-electricity-cost",
        "kpi-computing-income",
        "kpi-break-even",
        "negative-payoff-banner",
    ):
        loc = page.locator(f"[data-testid='{tid}']")
        assert loc.is_visible(), f"Missing {tid}"
        assert loc.inner_text().strip() != ""

    srv_text = page.locator("[data-testid='kpi-servers-per-house']").inner_text()
    assert str(kpis["servers_per_house"]) in srv_text
    errors.assert_clean()


def test_t1_f6_camera_presets_present_and_clickable(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    assert page.locator("[data-testid='camera-presets']").is_visible()
    for pid in (
        "preset-scorecard",
        "preset-jan-peak",
        "preset-jul-dump",
        "preset-live-rack",
        "preset-summer-verbund",
    ):
        btn = page.locator(f"[data-testid='{pid}']")
        assert btn.is_visible()
        btn.click()
    errors.assert_clean()


def test_t1_f6_xray_cutaway_toggle_interactive(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    toggle = page.locator("[data-testid='toggle-xray']")
    assert toggle.is_visible()
    before = toggle.get_attribute("aria-pressed") or toggle.get_attribute("data-active") or ""
    toggle.click()
    after = toggle.get_attribute("aria-pressed") or toggle.get_attribute("data-active") or ""
    if before != "" or after != "":
        assert after != before
    errors.assert_clean()


def test_t1_f6_provenance_mode_toggle_shows_4_categories(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='toggle-provenance']").click()
    legend = page.locator("[data-testid='provenance-legend']")
    legend.wait_for(state="visible", timeout=5000)
    legend_text = legend.inner_text().lower()
    assert "measured" in legend_text
    assert "benchmark" in legend_text
    assert "geodata" in legend_text or "blueprint" in legend_text
    assert "sensitivity" in legend_text or "pilot" in legend_text
    errors.assert_clean()


def test_t1_f6_thermal_telemetry_and_attribution_footer(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    assert page.locator("[data-testid='thermal-telemetry']").is_visible()
    for tid in (
        "readout-tank-soc",
        "readout-water-temps",
        "readout-outdoor-temp",
        "readout-basement-air-temp",
        "readout-heat-dump",
    ):
        assert page.locator(f"[data-testid='{tid}']").inner_text().strip() != ""
    footer = page.locator("[data-testid='attribution-footer']").inner_text()
    assert "swisstopo" in footer
    assert "OpenStreetMap" in footer
    errors.assert_clean()


# =============================================================================
# F7: Summer Verbund 3D Scenes & Telemetry (5 tests)
# =============================================================================


def test_t1_f7_verbund_scenes_list_returns_7_scenes(api_client: httpx.Client) -> None:
    resp = api_client.get("/api/v1/verbund/scenes?servers=4")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 7
    assert [s["scene_id"] for s in body["scenes"]] == list(EXPECTED_VERBUND_SCENES)
    for scene in body["scenes"]:
        assert scene["glb_url"].startswith("/assets/glb/")
        assert len(scene["camera_preset"]["position"]) == 3


def test_t1_f7_verbund_basel_erdsonde_dual_sink_35_to_45c(
    api_client: httpx.Client,
) -> None:
    resp = api_client.get("/api/v1/verbund/scenes/basel_erdsonde?servers=4")
    assert resp.status_code == 200
    scene = resp.json()
    assert 35.0 <= scene["secondary_supply_temp_c"] <= 45.0
    assert scene["continuous_mode"]["dump_elimination_vs_basel_house_pct"] > 85.0
    assert scene["heat_led_mode"]["server_utilisation_pct"] > 90.0


def test_t1_f7_verbund_basel_pool_st_jakob_and_sole_uno(
    api_client: httpx.Client,
) -> None:
    pool = api_client.get("/api/v1/verbund/scenes/basel_pool_st_jakob?servers=4").json()
    assert 26.0 <= pool["secondary_supply_temp_c"] <= 34.0
    assert pool["separate_vs_paired_comparison"]["standalone_seasonal_pool_dump_kwh"] > 10000.0

    sole = api_client.get("/api/v1/verbund/scenes/sole_uno?servers=4").json()
    assert sole["continuous_mode"]["heat_dumped_kwh"] == pytest.approx(0.0, abs=1e-6)
    assert sole["ashrae_w45_compliant"] is True


def test_t1_f7_verbund_hotel_brewery_ara_industrial_hosts(
    api_client: httpx.Client,
) -> None:
    hotel = api_client.get("/api/v1/verbund/scenes/hotel_laundry_dhw?servers=4").json()
    hotel_t = hotel["secondary_supply_temp_c"] or hotel["primary_supply_temp_c"]
    assert 45.0 <= hotel_t <= 60.0

    brewery = api_client.get("/api/v1/verbund/scenes/brewery_dairy_cip?servers=4").json()
    brewery_t = brewery["secondary_supply_temp_c"] or brewery["primary_supply_temp_c"]
    assert 45.0 <= brewery_t <= 65.0

    ara = api_client.get("/api/v1/verbund/scenes/ara_basel_biogas?servers=4").json()
    ara_t = ara["secondary_supply_temp_c"] or ara["primary_supply_temp_c"]
    assert 37.0 <= ara_t <= 42.0
    assert ara["continuous_mode"]["heat_dumped_kwh"] == pytest.approx(0.0, abs=1e-6)


def test_t1_f7_verbund_davos_and_cockpit_verbund_panel(
    api_client: httpx.Client,
    cockpit_page: tuple[Any, Any],
) -> None:
    davos = api_client.get("/api/v1/verbund/scenes/davos_alpine?servers=4").json()
    assert davos["heat_led_mode"]["server_utilisation_pct"] > 80.0

    page, errors = cockpit_page
    assert page.locator("[data-testid='verbund-panel']").is_visible()
    select = page.locator("[data-testid='verbund-scene-select']")
    select.select_option("basel_pool_st_jakob")
    wait_until(
        lambda: page.locator("[data-testid='verbund-dump-elimination']").inner_text().strip() != "",
        timeout_s=10.0,
        description="verbund telemetry update",
    )
    assert page.locator("[data-testid='verbund-valve-split']").inner_text().strip() != ""
    assert page.locator("[data-testid='verbund-comparison-table']").inner_text().strip() != ""
    errors.assert_clean()


# =============================================================================
# F8: Pilot Reality Sensitivity Module (5 tests)
# =============================================================================


def test_t1_f8_sensitivity_returns_all_5_panels(api_client: httpx.Client) -> None:
    resp = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("heatwise", "mlperf_v51", "ashrae_w45", "capex_utilisation_cliff", "tariff_gap"):
        assert key in body


def test_t1_f8_sensitivity_heatwise_idle_and_recovery_sweep(
    api_client: httpx.Client,
) -> None:
    hw = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()["heatwise"]
    assert 0.20 <= hw["idle_to_full_ratio_range"]["min"] <= 0.26
    assert len(hw["sigstop_idle_penalty_sweep"]) >= 3
    assert [r["recovery_fraction"] for r in hw["recovery_fraction_sweep"]] == [0.3, 0.65, 0.8]


def test_t1_f8_sensitivity_mlperf_j_per_sample_and_chf_reuse(
    api_client: httpx.Client,
) -> None:
    ml = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()["mlperf_v51"]
    assert len(ml["rows"]) == 11
    for row in ml["rows"]:
        assert row["chf_per_1m_units_at_full_reuse"] < row["chf_per_1m_units_at_zero_reuse"]


def test_t1_f8_sensitivity_ashrae_w45_and_capex_cliff(
    api_client: httpx.Client,
) -> None:
    sens = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()
    w45 = sens["ashrae_w45"]
    assert w45["mixed_w45_effective_capacity_kwh"] == pytest.approx(17.5, abs=1e-6)
    assert w45["mixed_w45_server_utilisation_pct"] <= w45["stratified_server_utilisation_pct"]

    cliff = sens["capex_utilisation_cliff"]["cliff_rows"]
    assert [r["servers_per_house"] for r in cliff] == [1, 2, 4, 8, 12]


def test_t1_f8_sensitivity_tariff_gap_and_cockpit_drawer(
    api_client: httpx.Client,
    cockpit_page: tuple[Any, Any],
) -> None:
    tariffs = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()["tariff_gap"]
    by_cat = {r["category"]: r for r in tariffs["tariffs"]}
    assert by_cat["H4"]["total_excl_vat_chf_per_kwh"] == pytest.approx(0.3325, abs=1e-5)
    assert by_cat["H4"]["total_incl_vat_chf_per_kwh"] == pytest.approx(0.3325 * 1.081, abs=1e-4)
    assert by_cat["C5"]["h4_penalty_incl_vat_chf_per_kwh"] > 0.04

    page, errors = cockpit_page
    page.locator("[data-testid='toggle-sensitivity']").click()
    panel = page.locator("[data-testid='sensitivity-panel']")
    panel.wait_for(state="visible", timeout=5000)
    for tid in ("sens-heatwise", "sens-mlperf", "sens-ashrae", "sens-capex", "sens-tariff"):
        assert page.locator(f"[data-testid='{tid}']").is_visible()
    errors.assert_clean()


# =============================================================================
# F9: Walkthrough Coverage & Reproducibility Readiness (5 tests)
# =============================================================================


def test_t1_f9_jan_peak_preset_sets_winter_day_telemetry(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='preset-jan-peak']").click()
    wait_until(
        lambda: page.locator("[data-testid='day-picker']").input_value() == "2025-01-15",
        timeout_s=5.0,
        description="Jan Peak day selection",
    )
    assert page.locator("[data-testid='readout-outdoor-temp']").inner_text().strip() != ""
    errors.assert_clean()


def test_t1_f9_jul_dump_preset_sets_summer_dump_telemetry(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='preset-jul-dump']").click()
    wait_until(
        lambda: page.locator("[data-testid='day-picker']").input_value() == "2025-07-15",
        timeout_s=5.0,
        description="Jul Dump day selection",
    )
    assert page.locator("[data-testid='readout-heat-dump']").inner_text().strip() != ""
    errors.assert_clean()


def test_t1_f9_live_rack_preset_and_worker_panel_ready(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='preset-live-rack']").click()
    assert page.locator("[data-testid='worker-panel']").is_visible()
    assert page.locator("[data-testid='worker-start-btn']").is_visible()
    assert page.locator("[data-testid='worker-heat-signal-btn']").is_visible()
    errors.assert_clean()


def test_t1_f9_summer_verbund_preset_switches_context(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='preset-summer-verbund']").click()
    assert page.locator("[data-testid='verbund-panel']").is_visible()
    errors.assert_clean()


def test_t1_f9_provenance_endpoint_exposes_coordinate_frame_and_groups(
    api_client: httpx.Client,
) -> None:
    resp = api_client.get("/api/v1/provenance")
    assert resp.status_code == 200
    body = resp.json()
    cats = body.get("categories") or body.get("provenance_categories")
    assert {
        "measured_pid",
        "official_benchmark",
        "geodata_cad_blueprint",
        "pilot_reality_sensitivity",
    } <= set(cats.keys())
    notices_blob = " | ".join(body["mandatory_notices"])
    assert "(c) swisstopo" in notices_blob
    assert "(c) OpenStreetMap contributors, ODbL" in notices_blob
    assert "not a surveyed private Basel residence" in notices_blob


# =============================================================================
# F10: Repo Size, CPU-Only Dependencies & Hygiene (5 tests)
# =============================================================================


def test_t1_f10_repo_size_under_100_mb() -> None:
    total = 0
    for path in REPO_ROOT.rglob("*"):
        if ".venv" in path.parts or ".git" in path.parts:
            continue
        if path.is_file():
            total += path.stat().st_size
    assert total < 100 * 1024 * 1024, f"digitalheat size {total} bytes >= 100 MB"


def test_t1_f10_uv_lock_cpu_only_no_gpu_cuda_wheels() -> None:
    lock_txt = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8").lower()
    for gpu_pkg in ("nvidia-cublas", "nvidia-cuda", "triton", "cupy", "warp-lang", "torch"):
        assert f'name = "{gpu_pkg}"' not in lock_txt, f"Unexpected GPU wheel {gpu_pkg}"


def test_t1_f10_no_claude_dir_tracked_or_gitignored() -> None:
    assert not (REPO_ROOT / ".claude").exists()
    gitignore_txt = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude" not in gitignore_txt
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".claude" not in tracked


def test_t1_f10_no_secrets_or_api_keys_in_repo() -> None:
    secret_pat = re.compile(
        r"(sk-ant-|sk-proj-|xi-api|ANTHROPIC_API_KEY\s*=\s*['\"]?[A-Za-z0-9]|"
        r"OPENAI_API_KEY\s*=\s*['\"]?[A-Za-z0-9])"
    )
    for root_dir in (SRC_DIR, WEB_DIR, DATA_DIR):
        if not root_dir.exists():
            continue
        for fpath in root_dir.rglob("*"):
            if not fpath.is_file() or fpath.suffix in {".png", ".jpg", ".glb", ".woff2"}:
                continue
            txt = fpath.read_text(encoding="utf-8", errors="ignore")
            assert not secret_pat.search(txt), f"Potential secret in {fpath}"


def test_t1_f10_writing_hygiene_no_em_dash_oxford_comma_or_ai_names() -> None:
    # Build forbidden AI tool tokens dynamically to keep this file free of literal strings.
    forbidden_tokens = (
        "".join(["cla", "ude"]),
        "".join(["ant", "hropic"]),
        "".join(["ope", "nai"]),
        "".join(["chat", "gpt"]),
        "".join(["co", "pilot"]),
        "".join(["ge", "mini"]),
    )
    prose_files: list[Path] = [REPO_ROOT / "README.md"]
    prose_files.extend(SRC_DIR.rglob("*.py"))
    if WEB_DIR.is_dir():
        for ext in ("*.html", "*.css", "*.js"):
            for fpath in WEB_DIR.rglob(ext):
                if "vendor" in fpath.parts or fpath.name.endswith(".min.js"):
                    continue
                prose_files.append(fpath)

    for fpath in prose_files:
        if not fpath.is_file():
            continue
        txt = fpath.read_text(encoding="utf-8")
        assert "\u2014" not in txt, f"Em dash found in {fpath}"
        lower_txt = txt.lower()
        for tok in forbidden_tokens:
            assert tok not in lower_txt, f"Forbidden AI mention in {fpath}"
        if fpath.suffix in {".md", ".html"}:
            assert ", and " not in txt, f"Oxford comma found in {fpath}"
