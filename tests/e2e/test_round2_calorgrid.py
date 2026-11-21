"""Round 2 CalorGrid E2E tests covering Tiers 1-5 across Host, Customer, API and Mobile flows."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx


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


def _assert_no_legacy_branding_visible(page: Any) -> None:
    title = page.title().lower()
    assert "calorgrid" in title
    assert "digitalheat" not in title
    assert "prakasharyan.com" not in title

    body_text = page.evaluate("() => document.body ? document.body.innerText : ''").lower()
    assert "digitalheat" not in body_text
    assert "prakasharyan.com" not in body_text


def test_r2_t1_two_user_navigation_branding_and_globals(
    app_page: tuple[Any, Any],
) -> None:
    """Verify two-user nav, zero DigitalHeat/prakasharyan.com leaks and CalorGridScene."""
    page, errors = app_page

    nav = page.locator("[data-testid='app-top-tabs']")
    assert nav.is_visible()
    nav_txt = nav.inner_text().lower()
    for token in ("hosts", "explore", "configure", "heatledger", "customers", "compute / gpus"):
        assert token in nav_txt

    globals_info = page.evaluate(
        """() => ({
            hasCalorGridScene: typeof window.CalorGridScene === "object",
            hasCalorGridApp: typeof window.CalorGridApp === "object",
            hasLegacyScene: typeof window.DigitalHeatScene !== "undefined",
            hasLegacyRaw: typeof window.__DIGITALHEAT_RAW_SCENE__ !== "undefined",
        })"""
    )
    assert globals_info["hasCalorGridScene"] is True
    assert globals_info["hasCalorGridApp"] is True
    assert globals_info["hasLegacyScene"] is False
    assert globals_info["hasLegacyRaw"] is False
    _assert_no_legacy_branding_visible(page)

    # Walk Configure -> HeatLedger -> Compute / GPUs -> Explore
    page.locator("[data-testid='app-top-tabs'] [data-app-tab='configure']").click()
    page.locator("[data-testid='configurator-overlay']").wait_for(state="visible", timeout=10_000)
    _assert_no_legacy_branding_visible(page)

    page.locator("[data-testid='app-top-tabs'] [data-app-tab='heatledger']").click()
    page.locator("[data-testid='heatledger-overlay']").wait_for(state="visible", timeout=10_000)
    _assert_no_legacy_branding_visible(page)

    page.locator("[data-testid='tab-compute-gpus']").click()
    page.locator("[data-testid='compute-overlay']").wait_for(state="visible", timeout=10_000)
    _assert_no_legacy_branding_visible(page)

    # Return to Explore and verify Book GPUs CTA from both map header and node twin bar
    page.locator("[data-testid='app-top-tabs'] [data-app-tab='explore']").click()
    page.locator("[data-testid='swiss-network-overlay']").wait_for(
        state="visible", timeout=10_000
    )

    page.locator("[data-testid='sn-book-compute-btn']").click()
    page.locator("[data-testid='compute-overlay']").wait_for(state="visible", timeout=10_000)

    page.locator("[data-testid='app-top-tabs'] [data-app-tab='explore']").click()
    page.locator("[data-testid='swiss-network-overlay']").wait_for(
        state="visible", timeout=10_000
    )
    page.locator("[data-roster-id='thurgau_greenhouse']").click()
    page.locator("[data-testid='swiss-twin-bar']").wait_for(state="visible", timeout=10_000)
    page.locator("[data-testid='sn-tb-book-compute']").click()
    page.locator("[data-testid='compute-overlay']").wait_for(state="visible", timeout=10_000)
    wait_until(
        lambda: page.locator("[data-testid='compute-site-select']").input_value()
        == "thurgau_greenhouse",
        timeout_s=10.0,
        description="preferred site preselected from Swiss twin bar",
    )
    wait_until(
        lambda: (
            "0 min (Instant)"
            in page.locator("[data-testid='compute-quote-result']").inner_text()
        ),
        timeout_s=10.0,
        description="On-Demand quote succeeded at year-round sink",
    )
    errors.assert_clean()


def test_r2_t1_r1_host_telemetry_gpu_slots_and_app_logs(
    app_page: tuple[Any, Any],
) -> None:
    """Verify R1 live server/GPU thermal telemetry, nested GPU slots and log filters."""
    page, errors = app_page

    page.locator("[data-testid='app-top-tabs'] [data-app-tab='heatledger']").click()
    page.locator("[data-testid='heatledger-overlay']").wait_for(state="visible", timeout=10_000)

    # Switch to municipal_pool (8x B200 servers) to inspect nested GPU slots and site telemetry
    page.locator("[data-testid='hl-deployment-select']").select_option("municipal_pool")
    wait_until(
        lambda: (
            "kW_th" in page.locator("[data-testid='hl-live-telemetry']").inner_text()
            and page.locator("[data-testid='hl-process-list'] .hl-gpu-chip").count() >= 8
        ),
        timeout_s=12.0,
        description="HeatLedger live telemetry and nested GPU slots",
    )

    first_chip = page.locator("[data-testid='hl-process-list'] .hl-gpu-chip").first.inner_text()
    assert "kW_e" in first_chip and "kW_th" in first_chip

    # Verify Application & Simulation Logs panel and interactive category filtering
    logs_box = page.locator("[data-testid='cg-app-logs']")
    wait_until(
        lambda: logs_box.locator(".cg-log-row").count() >= 1,
        timeout_s=10.0,
        description="Application & Simulation Logs rows populated",
    )
    pill_texts = logs_box.locator(".cg-log-pill").all_inner_texts()
    joined_pills = " ".join(pill_texts)
    for label in ("All", "Simulations", "Analysis", "Scheduler", "Bookings", "Workers"):
        assert label in joined_pills

    # Filter to Analysis tasks (triggered by configurator quote)
    logs_box.locator("[data-log-cat='analysis_task']").click()
    wait_until(
        lambda: (
            logs_box.locator("[data-log-cat='analysis_task'].is-active").count() == 1
            and logs_box.locator(".cg-log-row").count() >= 1
        ),
        timeout_s=10.0,
        description="Analysis log category filter active",
    )
    cats = logs_box.locator(".cg-log-row .cg-log-cat").all_inner_texts()
    assert all(c.strip() == "ANALYSIS" for c in cats)

    # Reset back to All
    logs_box.locator("[data-log-cat='']").click()
    wait_until(
        lambda: logs_box.locator("[data-log-cat=''].is-active").count() == 1,
        timeout_s=10.0,
        description="All log category filter restored",
    )
    errors.assert_clean()


def test_r2_t2_r2_host_heating_bill_savings_and_default_heater_breakdown(
    app_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    """Verify R2 Host heating-bill savings, CHF 0 hardware/power, heater switch and sliders."""
    page, errors = app_page

    # Configure tab defaults to Inside 3D view and CHF 0 server hardware/electricity
    page.locator("[data-testid='app-top-tabs'] [data-app-tab='configure']").click()
    page.locator("[data-testid='configurator-overlay']").wait_for(state="visible", timeout=10_000)

    mode_cls = page.locator("[data-testid='cfg-mode-basement']").get_attribute("class") or ""
    assert "is-active" in mode_cls
    wait_until(
        lambda: page.locator("#cfg-3d-host canvas").count() == 1,
        timeout_s=10.0,
        description="Configurator 3D Inside canvas mounted",
    )
    wait_until(
        lambda: "CHF 0" in page.locator("[data-testid='cfg-live-kpis']").inner_text(),
        timeout_s=10.0,
        description="Configurator live KPIs rendered",
    )
    cfg_kpis = page.locator("[data-testid='cfg-live-kpis']").inner_text()
    assert "CHF 0" in cfg_kpis
    assert "paid by CalorGrid" in cfg_kpis

    cfg_bd = page.locator("[data-testid='cfg-host-breakdown']")
    wait_until(
        lambda: "Host Heating-Bill Savings" in cfg_bd.inner_text(),
        timeout_s=10.0,
        description="Configure host breakdown rendered",
    )
    bd_txt = cfg_bd.inner_text()
    assert "Saves on heating bill (not electricity bill)" in bd_txt
    assert "Server hardware is CHF 0 and server electricity is CHF 0 on your bill" in bd_txt

    # Switch default heater to Heat Pump and Electric Resistance and verify breakdown updates
    page.locator("[data-testid='cfg-heater-select']").select_option("heat_pump")
    wait_until(
        lambda: "Heat Pump" in cfg_bd.inner_text(),
        timeout_s=10.0,
        description="Configure heater changed to Heat Pump",
    )
    hp_text = cfg_bd.inner_text()

    page.locator("[data-testid='cfg-heater-select']").select_option("electric_backup")
    wait_until(
        lambda: "Electric" in cfg_bd.inner_text(),
        timeout_s=10.0,
        description="Configure heater changed to Electric Resistance",
    )
    elec_text = cfg_bd.inner_text()
    assert hp_text != elec_text

    # Toggle existing boiler -> upfront cost drops while server hardware remains CHF 0
    upfront_before = page.locator("[data-testid='cfg-live-kpis']").inner_text()
    page.locator("label[for='cfg-existing-boiler-toggle']").click()
    wait_until(
        lambda: (
            "Existing heater CHF 0"
            in page.locator("[data-testid='cfg-live-kpis']").inner_text()
        ),
        timeout_s=10.0,
        description="Existing boiler toggle applied",
    )
    assert page.locator("[data-testid='cfg-live-kpis']").inner_text() != upfront_before

    # Select OCP Winterfell (4 units) so heat target slider 30% vs 130% shifts dispatch & cost
    page.locator("[data-testid='cfg-server-models'] [data-model-id='ocp_winterfell_3kw']").click()
    wait_until(
        lambda: "2.9" in page.locator("#cfg-3d-badge").inner_text()
        or "Winterfell" in page.locator("#cfg-3d-badge").inner_text(),
        timeout_s=10.0,
        description="OCP Winterfell selected in Configure",
    )
    for target_cnt in range(2, 5):
        if int(page.locator("#cfg-count-val").inner_text().strip()) < target_cnt:
            page.locator("[data-testid='cfg-count-plus']").click()
            wait_until(
                lambda tc=target_cnt: (
                    int(page.locator("#cfg-count-val").inner_text().strip()) >= tc
                ),
                timeout_s=5.0,
                description=f"server count reached {target_cnt}",
            )

    # Open HeatLedger and verify reactive heat target and indoor target sliders
    page.locator("[data-testid='cfg-test-heatledger-btn']").click()
    page.locator("[data-testid='heatledger-overlay']").wait_for(state="visible", timeout=10_000)

    hl_bd = page.locator("[data-testid='hl-host-breakdown']")
    wait_until(
        lambda: "Host Heating-Bill Savings" in hl_bd.inner_text(),
        timeout_s=10.0,
        description="HeatLedger host breakdown rendered",
    )
    base_bd_txt = hl_bd.inner_text()

    # Set heat target slider to 30% (Away) and wait for debounced API update, then 130% (Boost)
    heat_slider = page.locator("[data-testid='hl-heat-slider']")
    heat_slider.fill("30")
    heat_slider.dispatch_event("input")
    wait_until(
        lambda: "(30%)" in page.locator("[data-testid='hl-heat-readout']").inner_text(),
        timeout_s=10.0,
        description="Heat slider updated to 30%",
    )
    wait_until(
        lambda: hl_bd.inner_text() != base_bd_txt,
        timeout_s=10.0,
        description="Host breakdown refreshed after 30% slider",
    )
    low_bd_txt = hl_bd.inner_text()

    heat_slider.fill("130")
    heat_slider.dispatch_event("input")
    wait_until(
        lambda: "(130%)" in page.locator("[data-testid='hl-heat-readout']").inner_text(),
        timeout_s=10.0,
        description="Heat slider updated to 130%",
    )
    wait_until(
        lambda: hl_bd.inner_text() != low_bd_txt,
        timeout_s=10.0,
        description="Host breakdown changed between 30% and 130% heat target",
    )

    # Adjust indoor temperature slider from 18.5 C to 23.5 C
    indoor_slider = page.locator("[data-testid='hl-indoor-slider']")
    indoor_slider.fill("18.5")
    indoor_slider.dispatch_event("input")
    wait_until(
        lambda: "18.5 C" in page.locator("[data-testid='hl-indoor-readout']").inner_text(),
        timeout_s=10.0,
        description="Indoor slider 18.5 C",
    )
    cold_weather = page.locator("[data-testid='hl-weather-card']").inner_text()

    indoor_slider.fill("23.5")
    indoor_slider.dispatch_event("input")
    wait_until(
        lambda: (
            "23.5 C" in page.locator("[data-testid='hl-indoor-readout']").inner_text()
            and page.locator("[data-testid='hl-weather-card']").inner_text() != cold_weather
        ),
        timeout_s=10.0,
        description="Indoor slider 23.5 C updates demand",
    )

    # API conservation and ledger separation sanity check across heater types
    for ht in ("gas_boiler", "heat_pump", "electric_backup"):
        q = api_client.get(
            f"/api/v1/configurator/quote?deployment_id=house_basel&server_model_id=hydronode_1kw"
            f"&server_count=4&heat_target_pct=130&indoor_target_c=23.5&heater_type={ht}"
        )
        assert q.status_code == 200
        qj = q.json()
        assert qj["conservation"]["residual_kwh"] <= 1e-6
        assert qj["tomorrow_conservation"]["residual_kwh"] <= 1e-6
        assert qj["host_ledger"]["host_server_hardware_chf"] == 0.0
        assert qj["host_ledger"]["host_server_electricity_chf"] == 0.0

    errors.assert_clean()


def test_r2_t3_r3_compute_customer_pricing_quote_book_tick_and_sinks(
    app_page: tuple[Any, Any],
) -> None:
    """Verify R3 Compute / GPUs cloud comparison, quoting, live dispatch, tick and summer sinks."""
    page, errors = app_page

    page.locator("[data-testid='tab-compute-gpus']").click()
    page.locator("[data-testid='compute-overlay']").wait_for(state="visible", timeout=10_000)

    # 1. Pricing meta & cloud comparison tables (H100, A100, B200, RTX PRO 4500 + 4 server SKUs)
    wait_until(
        lambda: page.locator("#cc-gpu-table-host tbody tr").count() >= 4,
        timeout_s=12.0,
        description="GPU pricing comparison table populated",
    )
    gpu_table_txt = page.locator("#cc-gpu-table-host").inner_text()
    for gpu_name in ("H100", "A100", "B200", "RTX PRO 4500"):
        assert gpu_name in gpu_table_txt

    sku_rows = page.locator("#cc-sku-table-host tbody tr")
    assert sku_rows.count() == 4
    sku_table_txt = page.locator("#cc-sku-table-host").inner_text()
    for sku_tok in ("HydroNode", "Winterfell", "B200", "Edge"):
        assert sku_tok in sku_table_txt

    meta_txt = page.locator("[data-testid='compute-pricing-meta']").inner_text()
    assert "FX 1 USD =" in meta_txt and "Spot discount:" in meta_txt

    # 2. On-Demand (0 min instant) vs Spot (-50% Swiss building) quoting
    quote_box = page.locator("[data-testid='compute-quote-result']")
    wait_until(
        lambda: "0 min (Instant)" in quote_box.inner_text(),
        timeout_s=10.0,
        description="Default On-Demand quote 0 min instant",
    )
    ondemand_txt = quote_box.inner_text()

    page.locator("[data-testid='cc-tier-spot']").click()
    wait_until(
        lambda: "Spot window" in quote_box.inner_text(),
        timeout_s=10.0,
        description="Spot quote rendered",
    )
    spot_txt = quote_box.inner_text()
    assert spot_txt != ondemand_txt

    # Switch Unit Mode to Full Server and confirm 4 server SKUs appear in select
    page.locator("[data-testid='cc-mode-server']").click()
    wait_until(
        lambda: page.locator("[data-testid='compute-sku-select'] option").count() == 4,
        timeout_s=10.0,
        description="Full server mode SKU options",
    )

    # Switch back to Individual GPU On-Demand for live booking dispatch
    page.locator("[data-testid='cc-mode-gpu']").click()
    page.locator("[data-testid='cc-tier-ondemand']").click()
    wait_until(
        lambda: "0 min (Instant)" in quote_box.inner_text(),
        timeout_s=10.0,
        description="Restored Individual GPU On-Demand quote",
    )

    custom_job = f"r2-e2e-llama-{uuid.uuid4().hex[:6]}"
    page.locator("[data-testid='compute-job-name-input']").fill(custom_job)
    page.locator("[data-testid='compute-book-btn']").click()

    receipt = page.locator("[data-testid='compute-booking-receipt']")
    receipt.wait_for(state="visible", timeout=10_000)
    assert "Dispatched job-" in receipt.inner_text()
    assert "RUNNING" in receipt.inner_text()

    # Verify fleet telemetry and logs show the newly dispatched job
    wait_until(
        lambda: custom_job in page.locator("#cc-active-jobs").inner_text(),
        timeout_s=10.0,
        description="Dispatched job appears in active jobs telemetry",
    )
    wait_until(
        lambda: page.locator("#cc-fleet-sites .hl-gpu-chip").count() >= 4,
        timeout_s=10.0,
        description="Per-site GPU slot chips rendered in Compute telemetry",
    )
    wait_until(
        lambda: "BOOKING" in page.locator("[data-testid='cc-app-logs']").inner_text(),
        timeout_s=10.0,
        description="Booking logged into Compute Application Logs",
    )

    # 3. Advance simulation tick (+1h) and verify clock advances
    clock_before = page.locator("#cc-fleet-summary").inner_text()
    page.locator("[data-testid='compute-tick-btn']").click()
    wait_until(
        lambda: page.locator("#cc-fleet-summary").inner_text() != clock_before,
        timeout_s=10.0,
        description="Simulation clock updated after +1h tick",
    )

    # 4. Summer Sink Cost Model cards (5 cards) and interactive sink selection
    sink_cards = page.locator("[data-testid='compute-summer-sinks'] .cc-sink-card")
    assert sink_cards.count() == 5
    sinks_txt = page.locator("[data-testid='compute-summer-sinks']").inner_text()
    for site_tok in ("Sole Uno", "ARA Basel", "Dreispitz", "St. Jakob", "Davos"):
        assert site_tok in sinks_txt

    sink_cards.first.click()
    wait_until(
        lambda: page.locator("[data-testid='compute-site-select']").input_value() != "",
        timeout_s=10.0,
        description="Clicking summer sink card selects preferred site",
    )

    errors.assert_clean()


def test_r2_t3_api_compute_idempotency_and_pricing_contract(
    api_client: httpx.Client,
) -> None:
    """Verify /api/v1/compute/pricing schema and /api/v1/compute/book idempotency contract."""
    pricing = api_client.get("/api/v1/compute/pricing")
    assert pricing.status_code == 200
    pj = pricing.json()
    assert len(pj["gpu_comparisons"]) >= 4
    assert len(pj["server_sku_comparisons"]) == 4
    assert len(pj["summer_sinks"]) == 5
    gpu_ids = {g["gpu_id"] for g in pj["gpu_comparisons"]}
    assert {"h100_sxm_80gb", "a100_sxm_80gb", "b200_sxm_180gb", "rtx_pro_4500_blackwell"} <= gpu_ids

    q1 = api_client.post(
        "/api/v1/compute/quote",
        json={
            "tier": "on_demand",
            "unit_mode": "gpu",
            "gpu_or_sku": "h100_sxm_80gb",
            "quantity": 1,
            "duration_hours": 2.0,
        },
    )
    assert q1.status_code == 200
    q1_j = q1.json()
    assert q1_j["wait_time_min"] == 0

    q2 = api_client.post(
        "/api/v1/compute/quote",
        json={
            "tier": "spot",
            "unit_mode": "gpu",
            "gpu_or_sku": "h100_sxm_80gb",
            "quantity": 1,
            "duration_hours": 2.0,
        },
    )
    assert q2.status_code == 200
    q2_j = q2.json()
    assert q2_j["total_cost_chf"] < q1_j["total_cost_chf"]

    idem_key = f"r2-idem-{uuid.uuid4().hex[:10]}"
    b1 = api_client.post(
        "/api/v1/compute/book",
        headers={"Idempotency-Key": idem_key},
        json={"quote_id": q1_j["quote_id"]},
    )
    assert b1.status_code == 200
    b1_j = b1.json()

    # Exact replay with same Idempotency-Key and quote_id returns same booking_id and job_id
    b_replay = api_client.post(
        "/api/v1/compute/book",
        headers={"Idempotency-Key": idem_key},
        json={"quote_id": q1_j["quote_id"]},
    )
    assert b_replay.status_code == 200
    b_replay_j = b_replay.json()
    assert b_replay_j["booking_id"] == b1_j["booking_id"]
    assert b_replay_j["job_id"] == b1_j["job_id"]

    # Reusing the same Idempotency-Key for a different quote_id must return 409 Conflict
    b_conflict = api_client.post(
        "/api/v1/compute/book",
        headers={"Idempotency-Key": idem_key},
        json={"quote_id": q2_j["quote_id"]},
    )
    assert b_conflict.status_code == 409


def test_r2_t5_adversarial_csp_xss_and_invalid_api_boundaries(
    app_page: tuple[Any, Any],
    api_client: httpx.Client,
) -> None:
    """Tier 5 adversarial checks: CSP header, zero external requests, XSS safety and 4xx guards."""
    page, errors = app_page

    # 1. Verify strict Content-Security-Policy header on /
    root_resp = api_client.get("/")
    assert root_resp.status_code == 200
    csp = root_resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp

    # 2. XSS safety check via job_name input rendered in receipt, active jobs and logs
    page.locator("[data-testid='tab-compute-gpus']").click()
    page.locator("[data-testid='compute-overlay']").wait_for(state="visible", timeout=10_000)

    xss_payload = "<img src=x onerror=window.__xss_probe=1>"
    page.locator("[data-testid='compute-job-name-input']").fill(xss_payload)
    page.locator("[data-testid='compute-quote-btn']").click()
    wait_until(
        lambda: "CHF" in page.locator("[data-testid='compute-quote-result']").inner_text(),
        timeout_s=10.0,
        description="Quote updated before XSS booking probe",
    )
    page.locator("[data-testid='compute-book-btn']").click()
    page.locator("[data-testid='compute-booking-receipt']").wait_for(
        state="visible", timeout=10_000
    )
    wait_until(
        lambda: xss_payload in page.locator("[data-testid='cc-app-logs']").inner_text(),
        timeout_s=10.0,
        description="XSS payload rendered literally as text in app logs",
    )
    assert xss_payload in page.locator("#cc-fleet-sites").inner_text()
    assert page.evaluate("() => window.__xss_probe") is None
    assert page.locator("#cc-active-jobs img, #cc-fleet-sites img, #cc-app-logs img").count() == 0

    # 3. API invalid boundary checks (expect clean 4xx without 500)
    assert api_client.get("/api/v1/logs?category=bogus_cat").status_code == 422
    assert (
        api_client.get("/api/v1/configurator/quote?heater_type=nuclear_reactor").status_code
        == 422
    )
    assert (
        api_client.post(
            "/api/v1/compute/quote",
            json={
                "tier": "on_demand",
                "unit_mode": "gpu",
                "gpu_or_sku": "h100_sxm_80gb",
                "quantity": 64,
                "duration_hours": 4.0,
            },
        ).status_code
        == 409
    )
    assert (
        api_client.post(
            "/api/v1/compute/book",
            json={"quote_id": "nonexistent_quote_id_12345"},
        ).status_code
        == 422
    )
    assert (
        api_client.post(
            "/api/v1/compute/book",
            headers={"Idempotency-Key": "mismatch-key-1"},
            json={"quote_id": "nonexistent_quote_id_12345", "idempotency_key": "mismatch-key-2"},
        ).status_code
        == 400
    )
    assert (
        api_client.post(
            "/api/v1/compute/book",
            headers={"Idempotency-Key": f"missing-q-{uuid.uuid4().hex[:8]}"},
            json={"quote_id": "nonexistent_quote_id_12345"},
        ).status_code
        == 404
    )

    errors.assert_clean()


def test_r2_t5_mobile_390x844_four_tab_navigation(
    mobile_app_page: tuple[Any, Any],
) -> None:
    """Verify 390x844 mobile layout renders all 4 bottom nav tabs cleanly without overflow."""
    page, errors = mobile_app_page

    bnav = page.locator("[data-testid='app-bottom-nav']")
    assert bnav.is_visible()
    btns = bnav.locator("[data-app-tab]")
    assert btns.count() == 4
    tabs = [btns.nth(i).get_attribute("data-app-tab") for i in range(4)]
    assert tabs == ["explore", "configure", "heatledger", "compute"]

    targets = (
        ("configure", "[data-testid='configurator-overlay']"),
        ("heatledger", "[data-testid='heatledger-overlay']"),
        ("compute", "[data-testid='compute-overlay']"),
        ("explore", "[data-testid='swiss-network-overlay']"),
    )
    for tab_id, overlay_sel in targets:
        bnav.locator(f"[data-app-tab='{tab_id}']").click()
        page.locator(overlay_sel).wait_for(state="visible", timeout=10_000)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert overflow <= 2, f"Horizontal overflow on mobile tab {tab_id}: {overflow}px"

    _assert_no_legacy_branding_visible(page)
    errors.assert_clean()
