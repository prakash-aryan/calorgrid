"""Tier 3 opaque-box cross-feature pairwise combination tests (12 tests)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

ASSETS_DIR: Path = Path(__file__).resolve().parents[2] / "assets"

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


def _worker_cpu_s(status: dict[str, Any]) -> float:
    return float(status.get("cpu_time_s", status.get("active_cpu_s", 0.0)))


def test_t3_scheduler_live_worker_sigstop_freezes_energy_and_sigcont_resumes(
    worker_guard: httpx.Client,
) -> None:
    start = worker_guard.post("/api/v1/worker/start", json={"job_type": "archive_ocr"}).json()
    assert start["state"] == "running"

    wait_until(
        lambda: (
            s
            if _worker_units(s := worker_guard.get("/api/v1/worker/status").json()) >= 1
            and float(s["measured_energy_j"]) > 0.0
            else None
        ),
        timeout_s=15.0,
        description="first archive_ocr unit",
    )

    paused = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 34.8, "heat_demand_kw": 0.5, "outdoor_temp_c": 24.0},
    ).json()
    assert paused["state"] == "paused"
    assert (paused.get("psutil_status") or paused.get("os_status")) == "stopped"

    e_pause_1 = float(paused["measured_energy_j"])
    cpu_pause_1 = _worker_cpu_s(paused)
    time.sleep(0.4)
    paused_later = worker_guard.get("/api/v1/worker/status").json()
    assert float(paused_later["measured_energy_j"]) == pytest.approx(e_pause_1, abs=1e-6)
    assert _worker_cpu_s(paused_later) == pytest.approx(cpu_pause_1, abs=1e-6)

    resumed = worker_guard.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 8.0, "heat_demand_kw": 7.0, "outdoor_temp_c": -3.0},
    ).json()
    assert resumed["state"] == "running"
    time.sleep(0.5)
    running_later = worker_guard.get("/api/v1/worker/status").json()
    assert float(running_later["measured_energy_j"]) > e_pause_1


def test_t3_strategy_and_servers_scorecard_dom_matches_api_exactly(
    cockpit_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    page, errors = cockpit_page
    for strategy in ("smart", "baseline"):
        page.locator("[data-testid='strategy-select']").select_option(strategy)
        for servers in ("1", "8"):
            page.locator("[data-testid='servers-select']").select_option(servers)
            expected = api_client.get(
                f"/api/v1/simulation/annual?strategy={strategy}&servers={servers}"
            ).json()["kpis"]
            wait_until(
                lambda s=servers: (
                    s in page.locator("[data-testid='kpi-servers-per-house']").inner_text()
                ),
                timeout_s=10.0,
                description=f"strategy={strategy} servers={servers}",
            )
            dump_txt = page.locator("[data-testid='kpi-heat-dumped']").inner_text()
            if expected["heat_dumped_kwh"] > 1000.0:
                digits = "".join(ch for ch in dump_txt if ch.isdigit() or ch == ".")
                assert float(digits) > 1000.0
    errors.assert_clean()


def test_t3_jan_peak_vs_jul_dump_solar_vector_and_heat_dump_contrast(
    api_client: httpx.Client,
) -> None:
    jan = api_client.get("/api/v1/simulation/day/2025-01-15?strategy=smart&servers=4").json()
    jul = api_client.get("/api/v1/simulation/day/2025-07-15?strategy=baseline&servers=4").json()

    jan_noon = _day_rows(jan)[12]
    jul_noon = _day_rows(jul)[12]
    assert jul_noon["solar_elevation_deg"] > jan_noon["solar_elevation_deg"] + 30.0
    for vec in (jan_noon["sun_direction_xyz"], jul_noon["sun_direction_xyz"]):
        assert math.sqrt(sum(c * c for c in vec)) == pytest.approx(1.0, abs=1e-3)

    assert sum(r["heat_dumped_kw"] for r in _day_rows(jan)) == pytest.approx(0.0, abs=1e-6)
    assert sum(r["heat_dumped_kw"] for r in _day_rows(jul)) > 10.0


def test_t3_provenance_mode_with_attributions_and_verbund_switch(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='toggle-provenance']").click()
    page.locator("[data-testid='provenance-legend']").wait_for(state="visible", timeout=5000)
    page.locator("[data-testid='verbund-scene-select']").select_option("ara_basel_biogas")
    wait_until(
        lambda: page.locator("[data-testid='verbund-dump-elimination']").inner_text().strip() != "",
        timeout_s=10.0,
        description="verbund update with provenance active",
    )
    assert page.locator("[data-testid='provenance-legend']").is_visible()
    footer = page.locator("[data-testid='attribution-footer']").inner_text()
    assert "swisstopo" in footer and "OpenStreetMap" in footer
    errors.assert_clean()


def test_t3_verbund_scene_switch_glb_mime_and_valve_split_consistency(
    api_client: httpx.Client,
) -> None:
    for sid in ("basel_erdsonde", "basel_pool_st_jakob", "ara_basel_biogas", "davos_alpine"):
        scene = api_client.get(f"/api/v1/verbund/scenes/{sid}?servers=4").json()
        glb_resp = api_client.get(scene["glb_url"])
        assert glb_resp.status_code == 200
        assert glb_resp.headers.get("content-type", "").startswith("model/gltf-binary")
        assert glb_resp.content[:4] == b"glTF"
        split = scene["july_valve_split_pct"]
        assert split["primary_pct"] + split["secondary_pct"] + split["dump_pct"] == pytest.approx(
            100.0, abs=0.3
        )


def test_t3_adapter_hook_persists_to_annual_and_day_endpoints(
    api_client: httpx.Client,
) -> None:
    schedule = [3 if (i % 2 == 0) else 1 for i in range(8760)]
    post = api_client.post(
        "/api/v1/scheduler/adapter",
        json={
            "adapter_name": "t3_pair_adapter",
            "n_servers": 4,
            "site": "basel",
            "hourly_active_servers": schedule,
        },
    )
    assert post.status_code == 200
    posted_kpis = post.json()["kpis"]

    annual = api_client.get("/api/v1/simulation/annual?strategy=adapter&servers=4&site=basel")
    assert annual.status_code == 200
    assert annual.json()["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
    assert annual.json()["kpis"]["server_utilisation"] == pytest.approx(
        posted_kpis["server_utilisation"], abs=1e-6
    )

    day = api_client.get("/api/v1/simulation/day/2025-07-15?strategy=adapter&servers=4&site=basel")
    assert day.status_code == 200
    assert len(_day_rows(day.json())) == 24


def test_t3_pilot_reality_tariff_gap_and_negative_payoff_alignment(
    api_client: httpx.Client,
) -> None:
    annual = api_client.get("/api/v1/simulation/annual?strategy=smart&servers=4&site=basel").json()
    sens = api_client.get("/api/v1/sensitivity/pilot-reality?servers=4").json()
    tariffs = {r["category"]: r for r in sens["tariff_gap"]["tariffs"]}

    h4_excl = tariffs["H4"]["total_excl_vat_chf_per_kwh"]
    h4_price = tariffs["H4"]["total_incl_vat_chf_per_kwh"]
    c6_price = tariffs["C6"]["total_incl_vat_chf_per_kwh"]
    assert h4_excl == pytest.approx(0.3325, abs=1e-5)
    assert h4_price == pytest.approx(0.3325 * 1.081, abs=1e-4)
    assert c6_price < h4_price
    assert (
        annual["kpis"]["break_even_computing_price_chf_per_server_hour"]
        < h4_price
    )


def test_t3_basel_vs_davos_annual_and_dst_transition_parity(
    api_client: httpx.Client,
) -> None:
    basel_yr = api_client.get(
        "/api/v1/simulation/annual?strategy=smart&servers=4&site=basel"
    ).json()
    davos_yr = api_client.get(
        "/api/v1/simulation/annual?strategy=smart&servers=4&site=davos"
    ).json()
    assert davos_yr["heating_days_count"] > basel_yr["heating_days_count"]
    assert davos_yr["kpis"]["server_utilisation"] > basel_yr["kpis"]["server_utilisation"]

    basel_dst = api_client.get("/api/v1/simulation/day/2025-03-30?site=basel").json()
    davos_dst = api_client.get("/api/v1/simulation/day/2025-03-30?site=davos").json()
    assert basel_dst["hours_count"] == 23
    assert davos_dst["hours_count"] == 23


def test_t3_worker_archive_ocr_scaling_formula_and_scheduler_consistency(
    worker_guard: httpx.Client,
) -> None:
    worker_guard.post("/api/v1/worker/start", json={"job_type": "archive_ocr"})
    status = wait_until(
        lambda: (
            s
            if _worker_units(s := worker_guard.get("/api/v1/worker/status").json()) >= 1
            and float(s["measured_energy_j"]) > 0.0
            else None
        ),
        timeout_s=15.0,
        description="archive_ocr scaling measurement",
    )
    units = _worker_units(status)
    energy_per_unit_j = float(status["measured_energy_j"]) / float(units)
    expected_scaled = 3_600_000.0 / energy_per_unit_j
    if isinstance(status.get("scaled_1kw"), dict):
        reported = float(status["scaled_1kw"]["scaled_units_per_server_hour"])
    else:
        reported = float(status["scaled_units_per_1kw_server_hour"])
    assert reported == pytest.approx(expected_scaled, rel=0.05)


def test_t3_worker_iiif_embeddings_cockpit_ui_state_transitions(
    cockpit_page: tuple[Any, Any],
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='worker-job-select']").select_option("iiif_embeddings")
    page.locator("[data-testid='worker-start-btn']").click()
    wait_until(
        lambda: "running" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="UI worker running state",
    )
    page.locator("[data-testid='worker-pause-btn']").click()
    wait_until(
        lambda: "paused" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="UI worker paused state",
    )
    page.locator("[data-testid='worker-resume-btn']").click()
    wait_until(
        lambda: "running" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="UI worker resumed state",
    )
    page.locator("[data-testid='worker-stop-btn']").click()
    wait_until(
        lambda: "idle" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="UI worker idle state",
    )
    errors.assert_clean()


def test_t3_xray_cutaway_live_rack_preset_and_basement_bounds(
    cockpit_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    page, errors = cockpit_page
    page.locator("[data-testid='toggle-xray']").click()
    page.locator("[data-testid='preset-live-rack']").click()

    manifest = json.loads((ASSETS_DIR / "manifest.json").read_text(encoding="utf-8"))
    bounds = manifest["assets"]["house_bim_shell"]["basement_interior_bounds"]
    # Verify provenance endpoint also tracks hardware basement assets.
    prov = api_client.get("/api/v1/provenance").json()
    assert "hardware_server_rack" in prov["assets"]
    assert bounds["y"][0] < -1.2 < bounds["y"][1]
    errors.assert_clean()


def test_t3_cli_and_api_baseline_smart_parity(
    run_cli: Any, api_client: httpx.Client
) -> None:
    for strategy in ("smart", "baseline"):
        proc = run_cli(
            ["simulate", "--strategy", strategy, "--servers", "4", "--site", "basel", "--json"]
        )
        assert proc.returncode == 0
        cli_kpis = json.loads(proc.stdout)["kpis"]
        api_kpis = api_client.get(
            f"/api/v1/simulation/annual?strategy={strategy}&servers=4&site=basel"
        ).json()["kpis"]
        assert cli_kpis["heat_used_kwh"] == pytest.approx(api_kpis["heat_used_kwh"], abs=1e-6)
        assert cli_kpis["heat_dumped_kwh"] == pytest.approx(api_kpis["heat_dumped_kwh"], abs=1e-6)
        assert cli_kpis["electricity_cost_chf"] == pytest.approx(
            api_kpis["electricity_cost_chf"], abs=1e-6
        )
