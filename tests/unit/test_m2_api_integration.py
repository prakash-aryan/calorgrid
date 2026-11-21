"""Integration tests for M2 Verbund, Pilot Reality, provenance, static GLBs, worker API and CLI."""

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

import digitalheat.api as api_module
from digitalheat import cli
from digitalheat.physics import VERBUND_SCENE_IDS


def test_verbund_scenes_list_and_detail_contracts() -> None:
    with TestClient(api_module.app) as client:
        resp = client.get("/api/v1/verbund/scenes?servers=4")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 7
        assert len(body["scenes"]) == 7

        for sid in VERBUND_SCENE_IDS:
            detail = client.get(f"/api/v1/verbund/scenes/{sid}?servers=4")
            assert detail.status_code == 200
            d = detail.json()
            assert d["glb_url"].startswith("/assets/glb/")
            assert "position" in d["camera_preset"]
            assert "target" in d["camera_preset"]
            valve = d["three_way_valve"]
            frac_sum = (
                valve["primary_fraction"] + valve["secondary_fraction"] + valve["dump_fraction"]
            )
            assert frac_sum == pytest.approx(1.0, abs=0.01)
            assert d["primary_flow_l_per_min"] > 0.0

        assert client.get("/api/v1/verbund/scenes/unknown_scene").status_code == 404


def test_pilot_reality_sensitivity_endpoint() -> None:
    with TestClient(api_module.app) as client:
        resp = client.get("/api/v1/sensitivity/pilot-reality?servers=4")
        assert resp.status_code == 200
        body = resp.json()
        for panel in (
            "heatwise",
            "mlperf_v51",
            "ashrae_w45",
            "capex_utilisation_cliff",
            "tariff_gap",
        ):
            assert panel in body
        assert body["provenance_category"] == "pilot_reality_sensitivity"
        assert len(body["dataset_sha256"]) >= 5


def test_provenance_endpoint_and_graceful_missing_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with TestClient(api_module.app) as client:
        resp = client.get("/api/v1/provenance")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["categories"].keys()) == {
            "measured_pid",
            "official_benchmark",
            "geodata_cad_blueprint",
            "pilot_reality_sensitivity",
        }
        assert body["expected_asset_count"] == 13
        notices_text = " | ".join(body["mandatory_notices"])
        assert "(c) swisstopo" in notices_text
        assert "(c) OpenStreetMap contributors, ODbL" in notices_text

        monkeypatch.setattr(api_module, "ASSETS_DIR", tmp_path)
        partial = client.get("/api/v1/provenance")
        assert partial.status_code == 200
        p_body = partial.json()
        assert p_body["assets_ready"] is False
        assert p_body["available_asset_count"] == 0
        assert len(p_body["missing_assets"]) == 13
        assert len(p_body["mandatory_notices"]) >= 4


def test_glb_static_mount_mime_type_and_magic() -> None:
    with TestClient(api_module.app) as client:
        resp = client.get("/assets/glb/context_neighborhood.glb")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("model/gltf-binary")
        assert resp.content[:4] == b"glTF"


def test_cockpit_presets_endpoint() -> None:
    with TestClient(api_module.app) as client:
        resp = client.get("/api/v1/cockpit/presets")
        assert resp.status_code == 200
        presets = {p["id"]: p for p in resp.json()["presets"]}
        assert set(presets.keys()) == {
            "scorecard",
            "jan_peak",
            "jul_dump",
            "live_rack",
            "summer_verbund",
        }
        rack_y = presets["live_rack"]["position"][1]
        assert -3.2 < rack_y < -0.362


def test_live_worker_api_lifecycle() -> None:
    pid = None
    with TestClient(api_module.app) as client:
        start = client.post("/api/v1/worker/start", json={"job_type": "iiif_embeddings"})
        assert start.status_code == 200
        s_body = start.json()
        assert s_body["state"] == "running"
        pid = s_body["pid"]
        assert pid is not None

        status = client.get("/api/v1/worker/status")
        assert status.status_code == 200
        assert status.json()["state"] == "running"

        sig_pause = client.post(
            "/api/v1/worker/heat-signal",
            json={"tank_soc_kwh": 34.8, "heat_demand_kw": 0.5, "outdoor_temp_c": 22.0},
        )
        assert sig_pause.status_code == 200
        p_body = sig_pause.json()
        assert p_body["state"] == "paused"
        assert p_body["os_status"] == "stopped"
        assert p_body["decision"] is False
        assert p_body["signal_sent"] == "SIGSTOP"

        sig_resume = client.post(
            "/api/v1/worker/heat-signal",
            json={"tank_soc_kwh": 5.0, "heat_demand_kw": 6.0, "outdoor_temp_c": 0.0},
        )
        assert sig_resume.status_code == 200
        r_body = sig_resume.json()
        assert r_body["state"] == "running"
        assert r_body["decision"] is True
        assert r_body["signal_sent"] == "SIGCONT"

        assert client.post("/api/v1/worker/pause").json()["state"] == "paused"
        assert client.post("/api/v1/worker/resume").json()["state"] == "running"

        stopped = client.post("/api/v1/worker/stop")
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "idle"
        assert stopped.json()["pid"] is None

    if pid is not None:
        assert not psutil.pid_exists(pid)


def test_cli_verify_and_simulate(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["verify"]) == 0
    out_verify = capsys.readouterr().out
    assert "Datasets verified" in out_verify
    assert "Conservation check" in out_verify

    assert (
        cli.main(["simulate", "--strategy", "smart", "--servers", "4", "--site", "basel", "--json"])
        == 0
    )
    sim_json = json.loads(capsys.readouterr().out)
    assert sim_json["conservation_residual_kwh"] <= 1e-6
    assert sim_json["kpis"]["servers_per_house"] == 4


def test_provenance_handles_null_and_corrupt_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_data = tmp_path / "data"
    bad_data.mkdir()
    (bad_data / "manifest.json").write_text("{corrupt json", encoding="utf-8")

    bad_assets = tmp_path / "assets"
    bad_assets.mkdir()
    (bad_assets / "manifest.json").write_text(
        json.dumps({"assets": None}), encoding="utf-8"
    )
    (bad_assets / "attributions.json").write_text(
        json.dumps({"sources": None, "mandatory_notices": None}), encoding="utf-8"
    )

    monkeypatch.setattr(api_module, "DATA_DIR", bad_data)
    monkeypatch.setattr(api_module, "ASSETS_DIR", bad_assets)

    with TestClient(api_module.app) as client:
        resp = client.get("/api/v1/provenance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["assets_ready"] is False
        assert body["datasets"] == {}
        assert len(body["mandatory_notices"]) >= 4


def test_dataset_integrity_error_returns_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_data = tmp_path / "empty_data"
    empty_data.mkdir()

    def _clear_caches() -> None:
        api_module._cached_weather.cache_clear()
        api_module._cached_annual_sim.cache_clear()
        api_module._cached_daily_timeseries.cache_clear()
        api_module._cached_hourly_timeseries.cache_clear()
        api_module._cached_day_slice.cache_clear()
        api_module._cached_verbund_scenes.cache_clear()
        api_module._cached_verbund_scene.cache_clear()
        api_module._cached_pilot_reality.cache_clear()

    _clear_caches()
    monkeypatch.setattr(api_module, "DATA_DIR", empty_data)
    try:
        with TestClient(api_module.app) as client:
            assert client.get("/api/v1/simulation/annual").status_code == 503
            assert client.get("/api/v1/verbund/scenes").status_code == 503
            assert client.get("/api/v1/sensitivity/pilot-reality").status_code == 503
    finally:
        monkeypatch.undo()
        _clear_caches()


def test_cli_bounds_validation() -> None:
    for bad_argv in (
        ["simulate", "--servers", "0"],
        ["simulate", "--servers", "17"],
        ["serve", "--port", "0"],
        ["serve", "--port", "65536"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(bad_argv)
        assert exc_info.value.code == 2


def test_corrupt_and_null_data_manifest_degrades_health_and_returns_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _clear_caches() -> None:
        api_module._cached_weather.cache_clear()
        api_module._cached_annual_sim.cache_clear()
        api_module._cached_daily_timeseries.cache_clear()
        api_module._cached_hourly_timeseries.cache_clear()
        api_module._cached_day_slice.cache_clear()
        api_module._cached_verbund_scenes.cache_clear()
        api_module._cached_verbund_scene.cache_clear()
        api_module._cached_pilot_reality.cache_clear()

    orig_data_dir = api_module.DATA_DIR
    bad_dir = tmp_path / "data"
    shutil.copytree(orig_data_dir, bad_dir)
    variants = (
        "null",
        "[]",
        "{bad json",
        json.dumps({"schema_version": "1.0.0", "files": {"open_meteo_basel_2025_utc.csv": None}}),
        json.dumps(
            {
                "schema_version": "1.0.0",
                "files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": "not_int"}},
            }
        ),
        json.dumps(
            {
                "schema_version": "1.0.0",
                "files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": True}},
            }
        ),
    )

    monkeypatch.setattr(api_module, "DATA_DIR", bad_dir)
    try:
        for content in variants:
            (bad_dir / "manifest.json").write_text(content, encoding="utf-8")
            _clear_caches()
            with TestClient(api_module.app, raise_server_exceptions=False) as client:
                health = client.get("/api/v1/health")
                assert health.status_code == 200
                assert health.json()["status"] == "degraded"
                assert health.json()["datasets_verified"] is False

                prov = client.get("/api/v1/provenance")
                assert prov.status_code == 200
                if content in ("null", "[]", "{bad json"):
                    assert prov.json()["datasets"] == {}
                else:
                    assert isinstance(prov.json()["datasets"], dict)

                assert client.get("/api/v1/simulation/annual").status_code == 503
                assert client.get("/api/v1/simulation/day/2025-01-15").status_code == 503
                assert client.get("/api/v1/verbund/scenes").status_code == 503
                assert client.get("/api/v1/sensitivity/pilot-reality").status_code == 503
                adapter_resp = client.post(
                    "/api/v1/scheduler/adapter",
                    json={"n_servers": 4, "hourly_active_servers": [0] * 8760},
                )
                assert adapter_resp.status_code == 503
    finally:
        monkeypatch.undo()
        _clear_caches()


def test_provenance_null_asset_file_and_cli_verify_corrupt_assets_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_assets = tmp_path / "assets"
    bad_assets.mkdir()
    (bad_assets / "manifest.json").write_text(
        json.dumps(
            {
                "assets": {
                    "context_neighborhood": {
                        "file": None,
                        "provenance_category": None,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api_module, "ASSETS_DIR", bad_assets)
    with TestClient(api_module.app) as client:
        resp = client.get("/api/v1/provenance")
        assert resp.status_code == 200
        ctx = resp.json()["assets"]["context_neighborhood"]
        assert ctx["file"] == "glb/context_neighborhood.glb"
        assert ctx["url"] == "/assets/glb/context_neighborhood.glb"
        assert ctx["provenance_category"] == "geodata_cad_blueprint"

    monkeypatch.setattr(cli, "PACKAGE_ROOT", tmp_path)
    for bad_manifest in ("{corrupt", "null", "[]", json.dumps({"assets": None})):
        (bad_assets / "manifest.json").write_text(bad_manifest, encoding="utf-8")
        assert cli.main(["verify"]) == 1
        err = capsys.readouterr().err
        assert "ERROR: Invalid assets manifest" in err


def test_verbund_scenes_single_flight_coalesces_cold_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0
    count_lock = threading.Lock()
    orig_list = api_module.list_verbund_scenes

    def _counting_list(*, n_servers: int, data_dir: Path | None = None) -> list[dict]:
        nonlocal call_count
        with count_lock:
            call_count += 1
        return orig_list(n_servers=n_servers, data_dir=data_dir)

    monkeypatch.setattr(api_module, "list_verbund_scenes", _counting_list)
    api_module._cached_verbund_scenes.cache_clear()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: api_module._cached_verbund_scenes(4), range(8)))
        assert call_count == 1
        assert all(len(r) == 7 for r in results)
    finally:
        monkeypatch.undo()
        api_module._cached_verbund_scenes.cache_clear()


def test_m2_logs_telemetry_and_fleet_immutability() -> None:
    with TestClient(api_module.app) as client:
        logs = client.get("/api/v1/logs?limit=50")
        assert logs.status_code == 200
        lj = logs.json()
        assert lj["buffer_capacity"] == 500
        assert lj["total_buffered"] >= 1
        assert set(lj["category_counts"].keys()) == {
            "simulation_run",
            "analysis_task",
            "scheduler_dispatch",
            "worker_lifecycle",
            "customer_booking",
            "thermal_rebalance",
        }

        alias_logs = client.get("/api/v1/logs?site_id=basel_pool_st_jakob")
        assert alias_logs.status_code == 200
        assert client.get("/api/v1/logs?site_id=unknown_canton_xyz").status_code == 422

        tel = client.get("/api/v1/fleet/telemetry")
        assert tel.status_code == 200
        tj = tel.json()
        assert tj["summary"]["total_sites"] == 18
        assert tj["summary"]["total_gpus"] > 0
        assert client.get("/api/v1/fleet/telemetry?site_id=unknown_site").status_code == 422

        tick = client.post("/api/v1/fleet/telemetry/tick", json={"hours": 1.0})
        assert tick.status_code == 200
        assert tick.json()["state_version"] > tj["state_version"]

        # Verify /api/v1/fleet @lru_cache remains clean & untouched
        fl = client.get("/api/v1/fleet?day_of_year=196&hour=14&strategy=smart")
        assert fl.status_code == 200
        fj = fl.json()
        assert fj["fleet_annual"]["total_nodes"] == 14
        assert fj["fleet_annual"]["annual_dumped_kwh"] == 0.0


def test_m2_compute_pricing_quote_book_and_error_codes(tmp_path: Path) -> None:
    isolated_pricing = api_module.CloudPricingService(
        data_dir=api_module.DATA_DIR,
        cache_path=tmp_path / "api_pricing.json",
        enable_live_fetch=False,
    )

    with TestClient(api_module.app) as client:
        orig_pricing = api_module.app.state.pricing_service
        api_module.app.state.pricing_service = isolated_pricing
        api_module.app.state.fleet_scheduler.set_pricing_service(isolated_pricing)
        try:
            _run_compute_pricing_quote_book_assertions(client)
        finally:
            api_module.app.state.pricing_service = orig_pricing
            api_module.app.state.fleet_scheduler.set_pricing_service(orig_pricing)


def _run_compute_pricing_quote_book_assertions(client: TestClient) -> None:
    if True:

        pr = client.get("/api/v1/compute/pricing")
        assert pr.status_code == 200
        pj = pr.json()
        assert len(pj["gpu_comparisons"]) == 5
        assert len(pj["server_sku_comparisons"]) == 4
        assert len(pj["summer_sinks"]) == 5

        # Invalid GPU SKU / site -> 422
        assert (
            client.post(
                "/api/v1/compute/quote",
                json={"tier": "on_demand", "unit_mode": "gpu", "gpu_or_sku": "bad_gpu"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/compute/quote",
                json={
                    "tier": "on_demand",
                    "unit_mode": "gpu",
                    "gpu_or_sku": "b200_sxm_180gb",
                    "preferred_site_id": "invalid_site_99",
                },
            ).status_code
            == 422
        )

        # Quote + Book flow with Idempotency-Key
        q_resp = client.post(
            "/api/v1/compute/quote",
            json={
                "tier": "on_demand",
                "unit_mode": "gpu",
                "gpu_or_sku": "b200_sxm_180gb",
                "quantity": 2,
                "duration_hours": 2.0,
                "preferred_site_id": "sole_uno",
                "job_name": "api-test-b200",
            },
        )
        assert q_resp.status_code == 200
        qj = q_resp.json()
        assert qj["assigned_site_id"] == "sole_uno"
        assert qj["wait_time_min"] == 0

        # Missing Idempotency-Key -> 422
        assert (
            client.post("/api/v1/compute/book", json={"quote_id": qj["quote_id"]}).status_code
            == 422
        )
        # Mismatched header vs body key -> 400
        assert (
            client.post(
                "/api/v1/compute/book",
                headers={"Idempotency-Key": "hdr-key"},
                json={"quote_id": qj["quote_id"], "idempotency_key": "body-key"},
            ).status_code
            == 400
        )
        # Unknown quote_id -> 404
        assert (
            client.post(
                "/api/v1/compute/book",
                headers={"Idempotency-Key": "missing-q"},
                json={"quote_id": "nonexistent_quote_1234"},
            ).status_code
            == 404
        )

        # Valid book -> 200, replay -> 200 idempotent_replay=True
        b1 = client.post(
            "/api/v1/compute/book",
            headers={"Idempotency-Key": "api-idem-1"},
            json={"quote_id": qj["quote_id"]},
        )
        assert b1.status_code == 200
        assert b1.json()["idempotent_replay"] is False
        b2 = client.post(
            "/api/v1/compute/book",
            headers={"Idempotency-Key": "api-idem-1"},
            json={"quote_id": qj["quote_id"]},
        )
        assert b2.status_code == 200
        assert b2.json()["idempotent_replay"] is True
        assert b2.json()["booking_id"] == b1.json()["booking_id"]

        # Same Idempotency-Key + different quote -> 409
        q2 = client.post(
            "/api/v1/compute/quote",
            json={
                "tier": "spot",
                "unit_mode": "gpu",
                "gpu_or_sku": "a100_sxm_80gb",
                "quantity": 1,
                "duration_hours": 1.0,
            },
        ).json()
        assert (
            client.post(
                "/api/v1/compute/book",
                headers={"Idempotency-Key": "api-idem-1"},
                json={"quote_id": q2["quote_id"]},
            ).status_code
            == 409
        )

        # Expired quote -> 410
        api_module.app.state.fleet_scheduler._quotes[q2["quote_id"]].expires_at_mono -= 1000.0
        assert (
            client.post(
                "/api/v1/compute/book",
                headers={"Idempotency-Key": "api-idem-exp"},
                json={"quote_id": q2["quote_id"]},
            ).status_code
            == 410
        )


def test_m2_configurator_strict_bounds_and_csp_headers() -> None:
    with TestClient(api_module.app) as client:
        for bad_url in (
            "/api/v1/configurator/quote?heat_target_pct=19",
            "/api/v1/configurator/quote?heat_target_pct=141",
            "/api/v1/configurator/quote?indoor_target_c=17.9",
            "/api/v1/configurator/quote?indoor_target_c=24.1",
            "/api/v1/configurator/quote?deployment_id=unknown_site_xyz",
            "/api/v1/configurator/quote?server_model_id=unknown_srv_xyz",
            "/api/v1/configurator/quote?server_model_id=b200_ai_10kw&server_count=4",
            "/api/v1/weather/forecast?deployment_id=unknown_site_xyz",
        ):
            assert client.get(bad_url).status_code == 422, f"Expected 422 for {bad_url}"

        # CSP header assertions
        for route in ("/", "/api/v1/health"):
            resp = client.get(route)
            assert resp.status_code == 200
            csp = resp.headers.get("content-security-policy", "")
            assert "default-src 'self'" in csp
            assert "'wasm-unsafe-eval'" in csp
            assert "'sha256-" in csp
            assert "worker-src 'self' blob:" in csp
