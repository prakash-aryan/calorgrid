"""Unit tests for M1 dataset integrity, SHA-256 verification and DST localization."""

import json
import shutil
from pathlib import Path

import pytest

from digitalheat.config import DATA_DIR
from digitalheat.data_loader import (
    DatasetIntegrityError,
    get_manifest,
    load_elcom_tariffs,
    load_heatwise_summary,
    load_mlperf_summary,
    load_weather_zurich,
    verify_manifest,
)
from digitalheat.physics import compute_house_demand


def test_manifest_sha256_passes_and_detects_tampering(tmp_path: Path) -> None:
    results = verify_manifest(DATA_DIR)
    assert len(results) == 5
    assert all(results.values())

    tampered_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, tampered_dir)
    csv_path = tampered_dir / "open_meteo_basel_2025_utc.csv"
    raw = bytearray(csv_path.read_bytes())
    raw[-10] ^= 0x01
    csv_path.write_bytes(bytes(raw))

    with pytest.raises(DatasetIntegrityError, match="SHA-256 mismatch"):
        verify_manifest(tampered_dir)


def test_manifest_detects_missing_file(tmp_path: Path) -> None:
    tampered_dir = tmp_path / "data"
    shutil.copytree(DATA_DIR, tampered_dir)
    (tampered_dir / "elcom_basel_2026.json").unlink()

    with pytest.raises(DatasetIntegrityError, match="Missing pinned dataset file"):
        verify_manifest(tampered_dir)


def test_basel_2025_zurich_dst_transitions() -> None:
    ds = load_weather_zurich(site="basel")
    assert ds.site == "basel"
    assert len(ds.hours) == 8760
    assert ds.days_count == 365
    assert ds.hours[0].timestamp_zurich == "2025-01-01T00:00:00+01:00"
    assert ds.hours[-1].timestamp_zurich == "2025-12-31T23:00:00+01:00"

    spring_dst = [h for h in ds.hours if h.local_date == "2025-03-30"]
    assert len(spring_dst) == 23
    assert [h.local_hour for h in spring_dst] == [i for i in range(24) if i != 2]
    assert spring_dst[1].timestamp_zurich == "2025-03-30T01:00:00+01:00"
    assert spring_dst[2].timestamp_zurich == "2025-03-30T03:00:00+02:00"

    autumn_dst = [h for h in ds.hours if h.local_date == "2025-10-26"]
    assert len(autumn_dst) == 25
    two_am_rows = [h for h in autumn_dst if h.local_hour == 2]
    assert len(two_am_rows) == 2
    assert two_am_rows[0].timestamp_zurich == "2025-10-26T02:00:00+02:00"
    assert two_am_rows[1].timestamp_zurich == "2025-10-26T02:00:00+01:00"


def test_basel_2025_reference_house_benchmarks() -> None:
    ds = load_weather_zurich(site="basel")
    assert len(ds.heating_days) == 206
    min_temp = min(h.outdoor_temp_c for h in ds.hours)
    assert min_temp == pytest.approx(-5.6, abs=1e-6)

    demand = compute_house_demand(ds.hours, heating_days=ds.heating_days)
    space_total = sum(row[0] for row in demand)
    dhw_total = sum(row[1] for row in demand)
    assert space_total == pytest.approx(26851.0156, abs=0.05)
    assert dhw_total == pytest.approx(4380.0, abs=1e-6)


def test_davos_2025_reference_benchmarks() -> None:
    ds = load_weather_zurich(site="davos")
    assert ds.site == "davos"
    assert len(ds.hours) == 8760
    assert ds.days_count == 365
    assert len(ds.heating_days) == 291
    assert min(h.outdoor_temp_c for h in ds.hours) == pytest.approx(-17.0, abs=1e-6)
    assert sum(1 for h in ds.hours if h.local_date == "2025-03-30") == 23
    assert sum(1 for h in ds.hours if h.local_date == "2025-10-26") == 25


def test_pinned_heatwise_mlperf_elcom_schemas() -> None:
    hw = load_heatwise_summary()
    assert len(hw["cases"]) == 8
    assert len(hw["idle_to_full_ratios"]) == 8
    for ratio_info in hw["idle_to_full_ratios"].values():
        assert 0.20 <= ratio_info["idle_to_full_ratio"] <= 0.28

    ml = load_mlperf_summary()
    assert ml["metadata"]["offline_power_entries"] == 11
    assert len(ml["offline_power_rows"]) == 11
    for row in ml["offline_power_rows"]:
        assert row["joules_per_unit"] > 0.0
        assert row["joules_per_sample"] > 0.0

    elcom = load_elcom_tariffs()
    cats = elcom["categories"]
    assert cats["H4"]["total_chf_per_kwh"] == pytest.approx(0.3325, abs=1e-6)
    assert cats["C5"]["total_chf_per_kwh"] == pytest.approx(0.26014, abs=1e-6)
    assert cats["C6"]["total_chf_per_kwh"] == pytest.approx(0.25267, abs=1e-6)

    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    total_data_bytes = sum(meta["bytes"] for meta in manifest["files"].values())
    assert total_data_bytes < 1_000_000


def test_manifest_rejects_corrupt_and_malformed_structures(tmp_path: Path) -> None:
    empty_dir = tmp_path / "missing_manifest"
    empty_dir.mkdir()
    with pytest.raises(DatasetIntegrityError, match="Missing manifest file"):
        get_manifest(empty_dir)

    bad_dir = tmp_path / "bad_data"
    shutil.copytree(DATA_DIR, bad_dir)
    manifest_path = bad_dir / "manifest.json"

    manifest_path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(DatasetIntegrityError, match="Invalid manifest file"):
        get_manifest(bad_dir)
    with pytest.raises(DatasetIntegrityError, match="Invalid manifest file"):
        verify_manifest(bad_dir)

    for raw_text in ("{corrupt json", "null", "[]", "123", '"string"'):
        manifest_path.write_text(raw_text, encoding="utf-8")
        with pytest.raises(DatasetIntegrityError):
            get_manifest(bad_dir)
        with pytest.raises(DatasetIntegrityError):
            verify_manifest(bad_dir)

    invalid_manifests = [
        {"files": None},
        {"files": []},
        {"files": {}},
        {"files": {"open_meteo_basel_2025_utc.csv": None}},
        {"files": {"open_meteo_basel_2025_utc.csv": []}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc"}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"bytes": 10}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": True}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": False}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": "274930"}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": 10.5}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": -1}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "abc", "bytes": None}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": None, "bytes": 274930}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": 123, "bytes": 274930}}},
        {"files": {"open_meteo_basel_2025_utc.csv": {"sha256": "", "bytes": 274930}}},
    ]
    for payload in invalid_manifests:
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(DatasetIntegrityError):
            verify_manifest(bad_dir)
