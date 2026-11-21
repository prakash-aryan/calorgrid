"""Unit and contract tests for Milestone M4 DSX 3D Web Cockpit."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from digitalheat.api import app

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"
VENDOR_DIR = WEB_DIR / "vendor" / "three"

REQUIRED_VENDOR_FILES = (
    "LICENSE",
    "build/three.core.js",
    "build/three.module.js",
    "jsm/controls/OrbitControls.js",
    "jsm/loaders/DRACOLoader.js",
    "jsm/loaders/GLTFLoader.js",
    "jsm/utils/BufferGeometryUtils.js",
    "jsm/utils/SkeletonUtils.js",
    "jsm/libs/draco/gltf/draco_decoder.js",
    "jsm/libs/draco/gltf/draco_decoder.wasm",
    "jsm/libs/draco/gltf/draco_wasm_wrapper.js",
)

REQUIRED_TESTIDS = (
    "cockpit-header",
    "api-health-badge",
    "canvas-3d",
    "camera-presets",
    "preset-scorecard",
    "preset-jan-peak",
    "preset-jul-dump",
    "preset-live-rack",
    "preset-summer-verbund",
    "preset-swiss-network",
    "swiss-network-overlay",
    "swiss-rtx-stage",
    "swiss-twin-bar",
    "app-top-tabs",
    "app-bottom-nav",
    "configurator-overlay",
    "cfg-deployment-select",
    "cfg-server-models",
    "cfg-count-minus",
    "cfg-count-plus",
    "cfg-live-kpis",
    "heatledger-overlay",
    "hl-weather-card",
    "hl-heat-slider",
    "hl-indoor-slider",
    "hl-hourly-chart",
    "hl-process-list",
    "strategy-select",
    "servers-select",
    "toggle-xray",
    "toggle-provenance",
    "toggle-sensitivity",
    "toggle-panels",
    "toggle-panels-mobile",
    "toggle-mobile-controls",
    "mobile-panel-dock",
    "kpi-scorecard",
    "kpi-servers-per-house",
    "kpi-heat-covered",
    "kpi-heat-dumped",
    "kpi-server-utilisation",
    "kpi-missed-deadlines",
    "kpi-electricity-cost",
    "kpi-computing-income",
    "kpi-break-even",
    "negative-payoff-banner",
    "thermal-telemetry",
    "day-picker",
    "hour-slider",
    "readout-tank-soc",
    "readout-water-temps",
    "readout-outdoor-temp",
    "readout-basement-air-temp",
    "readout-heat-dump",
    "worker-panel",
    "worker-job-select",
    "worker-start-btn",
    "worker-pause-btn",
    "worker-resume-btn",
    "worker-stop-btn",
    "worker-heat-signal-btn",
    "worker-pid",
    "worker-state",
    "worker-scaled-throughput",
    "verbund-panel",
    "verbund-scene-select",
    "verbund-dump-elimination",
    "verbund-valve-split",
    "verbund-comparison-table",
    "provenance-legend",
    "sensitivity-panel",
    "sens-heatwise",
    "sens-mlperf",
    "sens-ashrae",
    "sens-capex",
    "sens-tariff",
    "attribution-footer",
    "tab-compute-gpus",
    "bnav-compute-gpus",
    "sn-book-compute-btn",
    "sn-tb-book-compute",
    "cfg-heater-select",
    "cfg-host-breakdown",
    "hl-heater-select",
    "hl-host-breakdown",
    "hl-live-telemetry",
    "cg-app-logs",
    "compute-overlay",
    "compute-pricing-meta",
    "compute-quote-panel",
    "compute-tier-toggle",
    "compute-unit-mode-toggle",
    "compute-sku-select",
    "compute-qty-input",
    "compute-hours-input",
    "compute-site-select",
    "compute-workload-select",
    "compute-job-name-input",
    "compute-quote-btn",
    "compute-book-btn",
    "compute-tick-btn",
    "compute-quote-result",
    "compute-booking-receipt",
    "compute-cloud-comparison",
    "compute-summer-sinks",
    "compute-fleet-telemetry",
    "cc-app-logs",
)


def test_m4_offline_three_vendor_bundle_complete_and_compact() -> None:
    assert VENDOR_DIR.is_dir()
    total_bytes = 0
    for rel in REQUIRED_VENDOR_FILES:
        fpath = VENDOR_DIR / rel
        assert fpath.is_file(), f"Missing Three.js vendor file: {rel}"
        total_bytes += fpath.stat().st_size
    assert total_bytes <= 4 * 1024 * 1024, f"Vendor bundle too large: {total_bytes} bytes"


def test_m4_html_contains_all_55_testids_and_offline_imports() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    found = set(re.findall(r'data-testid=["\']([^"\']+)["\']', html))
    missing = [tid for tid in REQUIRED_TESTIDS if tid not in found]
    assert not missing, f"Missing data-testid attributes: {missing}"

    assert "swisstopo" in html
    assert "OpenStreetMap" in html
    assert "not a surveyed private Basel residence" in html

    for fpath in [*WEB_DIR.rglob("*.html"), *WEB_DIR.rglob("*.css"), *WEB_DIR.rglob("*.js")]:
        if "vendor" in fpath.parts:
            continue
        txt = fpath.read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in txt and "http://" not in txt, f"External URL in {fpath}"
        assert "digitalheat" not in txt.lower(), f"Legacy brand token in {fpath}"


def test_m4_scene_coordinates_and_hygiene_compliance() -> None:
    scene_js = (WEB_DIR / "js" / "scene.js").read_text(encoding="utf-8")
    assert "[-2.25, Y_FLOOR, -1.35]" in scene_js
    assert "[-2.85" not in scene_js
    assert "cloneNodeMaterials" in scene_js
    assert "cutaway_house_walls" in scene_js
    assert "cutaway_box" in scene_js
    assert "sun_direction_xyz" in scene_js

    main_js = (WEB_DIR / "js" / "main.js").read_text(encoding="utf-8")
    assert "window.__CALORGRID_RAW_SCENE__" in main_js
    assert "window.CalorGridScene" in main_js
    assert "DigitalHeatScene" not in main_js

    forbidden_ai = (
        "".join(["cla", "ude"]),
        "".join(["ant", "hropic"]),
        "".join(["ope", "nai"]),
        "".join(["chat", "gpt"]),
        "".join(["co", "pilot"]),
        "".join(["ge", "mini"]),
    )
    rejected = ("warp.fem", "openfoam", "physicsnemo", "kpis.ts", "dcgm.csv")
    for fpath in [*WEB_DIR.rglob("*.html"), *WEB_DIR.rglob("*.css"), *WEB_DIR.rglob("*.js")]:
        if "vendor" in fpath.parts:
            continue
        txt = fpath.read_text(encoding="utf-8")
        assert "\u2014" not in txt, f"Em dash found in {fpath}"
        lower = txt.lower()
        for tok in forbidden_ai + rejected:
            assert tok not in lower, f"Forbidden token {tok} in {fpath}"
        if fpath.suffix == ".html":
            assert ", and " not in txt, f"Oxford comma found in {fpath}"


def test_m4_fastapi_serves_cockpit_static_files_and_vendor() -> None:
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["web_mounted"] is True

        for url in (
            "/",
            "/assets/glb/switzerland_3d_relief.glb",
            "/css/cockpit.css",
            "/js/main.js",
            "/js/scene.js",
            "/js/hydronics.js",
            "/js/worker_rack.js",
            "/js/provenance.js",
            "/js/fleet_twins.js",
            "/js/swiss_network.js",
            "/js/configurator.js",
            "/js/compute_customer.js",
            "/js/telemetry_logs.js",
            "/js/hud.js",
            "/vendor/three/build/three.module.js",
            "/vendor/three/build/three.core.js",
            "/vendor/three/jsm/controls/OrbitControls.js",
            "/vendor/three/jsm/loaders/GLTFLoader.js",
            "/vendor/three/jsm/loaders/DRACOLoader.js",
            "/vendor/three/jsm/utils/BufferGeometryUtils.js",
            "/vendor/three/jsm/utils/SkeletonUtils.js",
            "/vendor/three/jsm/libs/draco/gltf/draco_decoder.wasm",
        ):
            resp = client.get(url)
            assert resp.status_code == 200, f"Failed static route {url}: {resp.status_code}"

        fleet_jul = client.get("/api/v1/fleet?day_of_year=196&hour=14&strategy=smart")
        assert fleet_jul.status_code == 200
        fj = fleet_jul.json()
        assert fj["fleet_annual"]["total_nodes"] == 14
        assert fj["fleet_annual"]["total_cantons"] == 10
        assert fj["fleet_annual"]["annual_dumped_kwh"] == 0.0
        assert fj["baseline_dumped_kwh"] > 20000.0
        assert len(fj["nodes"]) == 14
        assert len(fj["flows"]) >= 4
        for node in fj["nodes"]:
            vid = client.get(f"/media/twins/{node['node_id']}.mp4")
            assert vid.status_code == 200, f"Missing RTX loop for {node['node_id']}"
            assert len(vid.content) > 50_000

        cat = client.get("/api/v1/configurator/catalog")
        assert cat.status_code == 200
        cat_j = cat.json()
        assert len(cat_j["deployments"]) >= 8
        assert len(cat_j["server_models"]) == 4

        q_low = client.get(
            "/api/v1/configurator/quote?deployment_id=house_basel&server_model_id=hydronode_1kw"
            "&server_count=2&heat_target_pct=30&indoor_target_c=20"
        )
        q_high = client.get(
            "/api/v1/configurator/quote?deployment_id=house_basel&server_model_id=hydronode_1kw"
            "&server_count=6&heat_target_pct=130&indoor_target_c=22.5"
        )
        assert q_low.status_code == 200 and q_high.status_code == 200
        ql, qh = q_low.json(), q_high.json()
        assert qh["sizing"]["peak_water_kw"] > ql["sizing"]["peak_water_kw"]
        assert qh["financials"]["total_upfront_chf"] > ql["financials"]["total_upfront_chf"]
        assert ql["financials"]["server_host_cost_chf"] == 0
        assert ql["financials"]["boiler_cost_chf"] < ql["financials"]["boiler_retail_chf"]
        assert ql["host_ledger"]["host_server_hardware_chf"] == 0.0
        assert ql["host_ledger"]["host_server_electricity_chf"] == 0.0
        assert "tomorrow" in qh["host_ledger"]
        assert "calorgrid_operating_cost_chf" in qh["operator_ledger"]["tomorrow"]
        q_existing = client.get(
            "/api/v1/configurator/quote?deployment_id=house_basel&server_model_id=hydronode_1kw"
            "&server_count=4&has_existing_boiler=true"
        )
        q_new = client.get(
            "/api/v1/configurator/quote?deployment_id=house_basel&server_model_id=hydronode_1kw"
            "&server_count=4&has_existing_boiler=false"
        )
        assert q_existing.status_code == 200 and q_new.status_code == 200
        qe, qn = q_existing.json(), q_new.json()
        assert qe["financials"]["boiler_cost_chf"] == 0
        assert qe["financials"]["server_host_cost_chf"] == 0
        assert qn["financials"]["boiler_cost_chf"] > 0
        assert qe["financials"]["host_upfront_chf"] < qn["financials"]["host_upfront_chf"]
        assert qh["heatledger_tomorrow"]["target_kwh"] > ql["heatledger_tomorrow"]["target_kwh"]
        assert len(qh["heatledger_tomorrow"]["hourly"]) == 24
        assert len(qh["heatledger_tomorrow"]["processes"]) == 6

        fc = client.get("/api/v1/weather/forecast?deployment_id=davos_alpine")
        assert fc.status_code == 200
        fc_j = fc.json()
        assert len(fc_j["hourly_temps_c"]) == 24
        assert fc_j["temp_min_c"] <= fc_j["temp_max_c"]

        pricing = client.get("/api/v1/compute/pricing")
        assert pricing.status_code == 200
        pj = pricing.json()
        assert len(pj["gpu_comparisons"]) >= 4
        assert len(pj["server_sku_comparisons"]) == 4
        assert len(pj["summer_sinks"]) >= 4

        cq = client.post(
            "/api/v1/compute/quote",
            json={
                "tier": "on_demand",
                "unit_mode": "gpu",
                "gpu_or_sku": "h100_sxm_80gb",
                "quantity": 1,
                "duration_hours": 2.0,
            },
        )
        assert cq.status_code == 200
        cq_j = cq.json()
        assert cq_j["wait_time_min"] == 0

        cb = client.post(
            "/api/v1/compute/book",
            headers={"Idempotency-Key": "m3-cockpit-test-key-1"},
            json={"quote_id": cq_j["quote_id"]},
        )
        assert cb.status_code == 200
        assert cb.json()["job_id"]

        logs = client.get("/api/v1/logs?limit=20")
        assert logs.status_code == 200
        assert logs.json()["count"] >= 1

        tel = client.get("/api/v1/fleet/telemetry")
        assert tel.status_code == 200
        assert tel.json()["summary"]["total_sites"] >= 14
