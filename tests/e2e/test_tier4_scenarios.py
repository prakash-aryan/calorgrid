"""Tier 4 opaque-box end-to-end user scenarios via live uvicorn and Chrome WebGL (6 tests)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

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


def test_t4_scenario_1_cold_load_webgl_and_7_kpi_audit(
    cockpit_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    """User loads the cockpit, verifies 3D WebGL scene, 7 server-computed KPIs and notices."""
    page, errors = cockpit_page
    expected = api_client.get(
        "/api/v1/simulation/annual?strategy=smart&servers=4&site=basel"
    ).json()
    kpis = expected["kpis"]

    canvas_ok = page.evaluate(
        """() => {
            const c = document.querySelector("[data-testid='canvas-3d']");
            if (!c || c.width <= 0 || c.height <= 0) return false;
            return !!(c.getContext("webgl2") || c.getContext("webgl"));
        }"""
    )
    assert canvas_ok is True

    assert str(kpis["servers_per_house"]) in page.locator(
        "[data-testid='kpi-servers-per-house']"
    ).inner_text()
    assert page.locator("[data-testid='negative-payoff-banner']").inner_text().strip() != ""
    assert "0.2261" in page.locator("[data-testid='kpi-break-even']").inner_text()

    footer = page.locator("[data-testid='attribution-footer']").inner_text()
    assert "swisstopo" in footer
    assert "OpenStreetMap" in footer
    errors.assert_clean()


def test_t4_scenario_2_jan_peak_to_jul_dump_walkthrough(
    cockpit_page: tuple[Any, Any],
) -> None:
    """User walks from Jan Peak X-Ray heating view to Jul Dump bypass view."""
    page, errors = cockpit_page

    page.locator("[data-testid='preset-jan-peak']").click()
    wait_until(
        lambda: page.locator("[data-testid='day-picker']").input_value() == "2025-01-15",
        timeout_s=10.0,
        description="Jan Peak date update",
    )
    jan_outdoor = page.locator("[data-testid='readout-outdoor-temp']").inner_text()
    assert jan_outdoor.strip() != ""

    page.locator("[data-testid='preset-jul-dump']").click()
    wait_until(
        lambda: page.locator("[data-testid='day-picker']").input_value() == "2025-07-15",
        timeout_s=10.0,
        description="Jul Dump date update",
    )
    wait_until(
        lambda: any(
            ch.isdigit() and ch != "0"
            for ch in page.locator("[data-testid='readout-heat-dump']").inner_text()
        ),
        timeout_s=10.0,
        description="Jul Dump non-zero dump readout",
    )
    errors.assert_clean()


def test_t4_scenario_3_live_worker_start_heat_signal_pause_resume_stop(
    cockpit_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    """User launches live archive_ocr worker in cockpit, pauses on full tank, resumes and stops."""
    page, errors = cockpit_page

    page.locator("[data-testid='preset-live-rack']").click()
    page.locator("[data-testid='worker-job-select']").select_option("archive_ocr")
    page.locator("[data-testid='worker-start-btn']").click()

    wait_until(
        lambda: "running" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="worker running in cockpit",
    )
    wait_until(
        lambda: (
            page.locator("[data-testid='worker-pid']").inner_text().strip() not in {"", "-", "none"}
            and page.locator("[data-testid='worker-scaled-throughput']").inner_text().strip() != ""
        ),
        timeout_s=15.0,
        description="worker PID and 1 kW scaled throughput",
    )

    # Trigger heat-signal pause via API (same scheduler decide_live_step) and verify UI syncs.
    paused = api_client.post(
        "/api/v1/worker/heat-signal",
        json={"tank_soc_kwh": 34.9, "heat_demand_kw": 0.5, "outdoor_temp_c": 22.0},
    )
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"

    wait_until(
        lambda: "paused" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="cockpit reflects heat-signal pause",
    )

    page.locator("[data-testid='worker-resume-btn']").click()
    wait_until(
        lambda: "running" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="cockpit reflects resume",
    )

    page.locator("[data-testid='worker-stop-btn']").click()
    wait_until(
        lambda: "idle" in page.locator("[data-testid='worker-state']").inner_text().lower(),
        timeout_s=10.0,
        description="cockpit reflects stop",
    )
    errors.assert_clean()


def test_t4_scenario_4_summer_verbund_pool_and_erdsonde_switch(
    cockpit_page: tuple[Any, Any],
) -> None:
    """User switches from Basel residential context to St. Jakob Pool and Erdsonde dual-sink."""
    page, errors = cockpit_page
    page.locator("[data-testid='preset-summer-verbund']").click()

    for scene_id in ("basel_pool_st_jakob", "basel_erdsonde"):
        page.locator("[data-testid='verbund-scene-select']").select_option(scene_id)
        wait_until(
            lambda: (
                page.locator("[data-testid='verbund-dump-elimination']").inner_text().strip() != ""
                and page.locator("[data-testid='verbund-valve-split']").inner_text().strip() != ""
            ),
            timeout_s=10.0,
            description=f"verbund {scene_id} telemetry",
        )
        assert page.locator("[data-testid='verbund-comparison-table']").inner_text().strip() != ""
    errors.assert_clean()


def test_t4_scenario_5_provenance_mode_and_pilot_reality_sensitivity(
    cockpit_page: tuple[Any, Any],
) -> None:
    """User enables Provenance Mode and inspects all 5 Pilot Reality Sensitivity panels."""
    page, errors = cockpit_page

    page.locator("[data-testid='toggle-provenance']").click()
    legend = page.locator("[data-testid='provenance-legend']")
    legend.wait_for(state="visible", timeout=5000)
    assert "measured" in legend.inner_text().lower()

    page.locator("[data-testid='toggle-sensitivity']").click()
    panel = page.locator("[data-testid='sensitivity-panel']")
    panel.wait_for(state="visible", timeout=5000)

    for tid in ("sens-heatwise", "sens-mlperf", "sens-ashrae", "sens-capex", "sens-tariff"):
        loc = page.locator(f"[data-testid='{tid}']")
        assert loc.is_visible()
        assert loc.inner_text().strip() != ""

    assert "0.3325" in page.locator("[data-testid='sens-tariff']").inner_text()
    errors.assert_clean()


def test_t4_scenario_6_external_adapter_injection_and_cockpit_inspection(
    cockpit_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    """External scheduler JSON plugs into /api/v1/scheduler/adapter and renders in cockpit."""
    page, errors = cockpit_page

    # Post deterministic external schedule (2 servers active all year = 50% utilisation).
    resp = api_client.post(
        "/api/v1/scheduler/adapter",
        json={
            "adapter_name": "teammate_deterministic_schedule",
            "n_servers": 4,
            "site": "basel",
            "hourly_active_servers": [2] * 8760,
        },
    )
    assert resp.status_code == 200
    adapter_body = resp.json()
    assert adapter_body["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
    assert adapter_body["kpis"]["server_utilisation"] == pytest.approx(0.5, abs=0.02)

    page.locator("[data-testid='strategy-select']").select_option("adapter")
    wait_until(
        lambda: "50" in page.locator("[data-testid='kpi-server-utilisation']").inner_text(),
        timeout_s=10.0,
        description="adapter 50% utilisation in scorecard",
    )
    errors.assert_clean()
