"""Unit tests for CloudPricingService live/cached Azure fetcher, circuit breaker, and sinks."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from digitalheat.cloud_pricing import (
    CloudPricingService,
    compute_summer_sink_cost_model,
)
from digitalheat.config import DATA_DIR
from digitalheat.data_loader import verify_manifest


class _FakeHttpResp:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._raw[:n] if n > 0 else self._raw

    def __enter__(self) -> _FakeHttpResp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_cloud_pricing_cache_path_guardrail_blocks_data_dir() -> None:
    with pytest.raises(ValueError, match="NEVER reside inside DATA_DIR"):
        CloudPricingService(data_dir=DATA_DIR, cache_path=DATA_DIR / "bad_cache.json")


def test_cloud_pricing_non_blocking_fallback_and_null_equivalents(tmp_path: Path) -> None:
    cache_file = tmp_path / "pricing_cache.json"
    svc = CloudPricingService(
        data_dir=DATA_DIR,
        cache_path=cache_file,
        enable_live_fetch=False,
    )
    snap = svc.get_public_pricing_payload(trigger_refresh=False)
    assert snap["fx_chf_per_usd"] == pytest.approx(0.83922, abs=1e-5)
    assert snap["spot_price_ratio"] == pytest.approx(0.50, abs=1e-6)
    assert len(snap["gpu_comparisons"]) == 5
    assert len(snap["server_sku_comparisons"]) == 4
    assert len(snap["summer_sinks"]) == 5

    # Verify Spot = 0.50 * On-Demand and null for non-existent offers (RTX PRO 4500 & AWS H100 NVL)
    by_gpu = {g["gpu_id"]: g for g in snap["gpu_comparisons"]}
    rtx = by_gpu["rtx_pro_4500_blackwell"]
    for prov_row in rtx["providers"].values():
        assert prov_row["on_demand_usd_per_gpu_hr"] is None
        assert prov_row["source"] == "unavailable"
        assert prov_row["offer_status"] == "not_offered"

    h100_nvl = by_gpu["h100_nvl_94gb"]
    assert h100_nvl["providers"]["aws"]["on_demand_usd_per_gpu_hr"] is None
    assert h100_nvl["providers"]["gcp"]["on_demand_usd_per_gpu_hr"] is None
    assert h100_nvl["providers"]["azure"]["source"] == "pinned_fallback"

    for g in snap["gpu_comparisons"]:
        cg = g["calorgrid"]
        assert cg["spot_chf_per_gpu_hr"] == pytest.approx(
            0.50 * cg["on_demand_chf_per_gpu_hr"], abs=1e-4
        )

    # Verify CPU Winterfell is compared to cpu_compute_baseline, not GPU instances
    by_srv = {s["server_model_id"]: s for s in snap["server_sku_comparisons"]}
    assert by_srv["ocp_winterfell_3kw"]["cloud_baseline"]["cloud_equivalent_type"] == (
        "cpu_compute_baseline"
    )
    assert by_srv["b200_ai_10kw"]["cloud_baseline"]["cloud_equivalent_type"] == "gpu_8x_node"

    # Data manifest must remain 100% untouched
    assert verify_manifest(DATA_DIR, include_catalogs=True)


def test_azure_live_parse_filters_windows_and_low_priority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_file = tmp_path / "azure_live.json"

    def _mock_urlopen(req, timeout=2.5):
        url = req.full_url
        if "Standard_NC24ads_A100_v4" in url:
            return _FakeHttpResp(
                {
                    "Items": [
                        {
                            "unitOfMeasure": "1 Hour",
                            "currencyCode": "USD",
                            "productName": "NCads A100 v4 Series Windows",
                            "meterName": "NC24ads A100 v4",
                            "skuName": "NC24ads A100 v4",
                            "retailPrice": 9.99,
                        },
                        {
                            "unitOfMeasure": "1 Hour",
                            "currencyCode": "USD",
                            "productName": "NCads A100 v4 Series Linux",
                            "meterName": "NC24ads A100 v4 Low Priority",
                            "skuName": "NC24ads A100 v4 Low Priority",
                            "retailPrice": 0.50,
                        },
                        {
                            "unitOfMeasure": "1 Hour",
                            "currencyCode": "USD",
                            "productName": "NCads A100 v4 Series Linux",
                            "meterName": "NC24ads A100 v4",
                            "skuName": "NC24ads A100 v4",
                            "retailPrice": 4.95,
                        },
                        {
                            "unitOfMeasure": "1 Hour",
                            "currencyCode": "USD",
                            "productName": "NCads A100 v4 Series Linux",
                            "meterName": "NC24ads A100 v4 Spot",
                            "skuName": "NC24ads A100 v4 Spot",
                            "retailPrice": 0.91,
                        },
                    ]
                }
            )
        return _FakeHttpResp({"Items": []})

    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)

    svc = CloudPricingService(data_dir=DATA_DIR, cache_path=cache_file, enable_live_fetch=True)
    snap1 = svc.refresh_now()
    a100 = next(g for g in snap1["gpu_comparisons"] if g["gpu_id"] == "a100_sxm_80gb")
    az = a100["providers"]["azure"]
    assert az["source"] == "fresh_live"
    assert az["on_demand_usd_per_gpu_hr"] == pytest.approx(4.95, abs=1e-4)
    assert az["spot_usd_per_gpu_hr"] == pytest.approx(0.91, abs=1e-4)
    assert az["on_demand_chf_per_gpu_hr"] == pytest.approx(4.95 * 0.83922, abs=1e-3)
    assert cache_file.is_file()

    # New instance loads cached_live from disk without network
    svc2 = CloudPricingService(data_dir=DATA_DIR, cache_path=cache_file, enable_live_fetch=False)
    snap2 = svc2.get_public_pricing_payload(trigger_refresh=False)
    a100_2 = next(g for g in snap2["gpu_comparisons"] if g["gpu_id"] == "a100_sxm_80gb")
    assert a100_2["providers"]["azure"]["source"] == "cached_live"
    assert a100_2["providers"]["azure"]["on_demand_usd_per_gpu_hr"] == pytest.approx(4.95, abs=1e-4)


def test_azure_circuit_breaker_trips_after_three_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def _failing_urlopen(req, timeout=2.5):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            req.full_url, 500, "Server Error", hdrs=None, fp=io.BytesIO(b"")  # type: ignore[arg-type]
        )

    monkeypatch.setattr("urllib.request.urlopen", _failing_urlopen)
    svc = CloudPricingService(
        data_dir=DATA_DIR,
        cache_path=tmp_path / "cb_cache.json",
        enable_live_fetch=True,
    )
    for _ in range(3):
        svc.refresh_now()

    snap = svc.get_public_pricing_payload(trigger_refresh=False)
    assert snap["cache_status"]["circuit_state"] == "open"
    assert snap["cache_status"]["consecutive_failures"] == 3

    # Subsequent refresh_now honors open circuit and skips network calls
    calls_before = calls
    svc.refresh_now()
    assert calls == calls_before


def test_summer_sink_cost_model_accounting() -> None:
    sinks = {s["site_id"]: s for s in compute_summer_sink_cost_model()}
    assert set(sinks.keys()) == {
        "sole_uno",
        "ara_basel_biogas",
        "basel_erdsonde",
        "basel_pool_st_jakob",
        "davos_alpine",
    }
    assert sinks["basel_erdsonde"]["heat_credit_accounting"] == (
        "winter_direct_plus_summer_deferred_cop"
    )
    assert sinks["sole_uno"]["dry_cooler_required"] is False
    assert sinks["davos_alpine"]["seasonality"] == "winter_dominant"
    assert "snowmaking is a cooling load" in sinks["davos_alpine"]["notes"].lower()
