"""Dataset integrity verification and Europe/Zurich weather localization."""

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from digitalheat.config import (
    DATA_DIR,
    HEATING_DAY_MEAN_THRESHOLD_C,
    HOURS_PER_YEAR,
    HourlyWeather,
    WeatherDataset,
)

ZURICH_TZ = ZoneInfo("Europe/Zurich")


class DatasetIntegrityError(ValueError):
    """Raised when a pinned dataset fails SHA-256 or structural checks."""


def _resolve_data_dir(data_dir: Path | None) -> Path:
    return data_dir if data_dir is not None else DATA_DIR


def get_manifest(data_dir: Path | None = None) -> dict:
    """Load data/manifest.json."""
    base = _resolve_data_dir(data_dir)
    manifest_path = base / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetIntegrityError(f"Missing manifest file: {manifest_path}")
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetIntegrityError(f"Invalid manifest file {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise DatasetIntegrityError(
            f"Invalid manifest root in {manifest_path}: expected JSON object, "
            f"got {type(manifest).__name__}"
        )
    return manifest


def verify_manifest(data_dir: Path | None = None) -> dict[str, bool]:
    """Verify SHA-256 digests and byte sizes for all pinned local data files."""
    base = _resolve_data_dir(data_dir)
    manifest = get_manifest(base)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise DatasetIntegrityError("Manifest contains no pinned file entries")

    results: dict[str, bool] = {}
    for filename, meta in files.items():
        if not isinstance(filename, str) or not filename:
            raise DatasetIntegrityError("Manifest contains invalid pinned filename")
        if not isinstance(meta, dict):
            raise DatasetIntegrityError(
                f"Invalid manifest entry for {filename}: expected JSON object, "
                f"got {type(meta).__name__}"
            )
        if "bytes" not in meta or "sha256" not in meta:
            raise DatasetIntegrityError(
                f"Manifest entry for {filename} missing required 'bytes' or 'sha256'"
            )
        expected_bytes = meta["bytes"]
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
        ):
            raise DatasetIntegrityError(
                f"Invalid byte size for {filename}: expected non-negative integer, "
                f"got {expected_bytes!r}"
            )
        expected_sha = meta["sha256"]
        if not isinstance(expected_sha, str) or not expected_sha:
            raise DatasetIntegrityError(
                f"Invalid sha256 for {filename}: expected non-empty string"
            )

        path = base / filename
        if not path.is_file():
            raise DatasetIntegrityError(f"Missing pinned dataset file: {path}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise DatasetIntegrityError(
                f"Failed to read pinned dataset file {path}: {exc}"
            ) from exc
        if len(raw) != expected_bytes:
            raise DatasetIntegrityError(
                f"Byte size mismatch for {filename}: expected {expected_bytes}, got {len(raw)}"
            )
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_sha:
            raise DatasetIntegrityError(
                f"SHA-256 mismatch for {filename}: expected {expected_sha}, got {digest}"
            )
        results[filename] = True
    return results


def load_weather_zurich(
    site: Literal["basel", "davos"] = "basel",
    data_dir: Path | None = None,
    verify: bool = True,
) -> WeatherDataset:
    """Load UTC Open-Meteo CSV and localize strictly to Europe/Zurich calendar year 2025."""
    base = _resolve_data_dir(data_dir)
    if verify:
        verify_manifest(base)

    filename = f"open_meteo_{site}_2025_utc.csv"
    csv_path = base / filename
    if not csv_path.is_file():
        raise DatasetIntegrityError(f"Missing weather CSV: {csv_path}")

    raw_bytes = csv_path.read_bytes()
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    lines = raw_bytes.decode("utf-8").splitlines()

    if len(lines) < 5:
        raise DatasetIntegrityError(f"Unexpected short weather CSV: {csv_path}")

    meta_header = lines[0].split(",")
    meta_values = lines[1].split(",")
    meta_map = dict(zip(meta_header, meta_values))
    latitude = float(meta_map["latitude"])
    longitude = float(meta_map["longitude"])
    elevation_m = float(meta_map["elevation"])

    hdr_idx = next((i for i, line in enumerate(lines) if line.startswith("time,")), None)
    if hdr_idx is None:
        raise DatasetIntegrityError(f"Missing hourly CSV header in {csv_path}")

    reader = csv.DictReader(lines[hdr_idx:])
    parsed_rows: list[tuple[datetime, datetime, float, float, float]] = []
    for row in reader:
        dt_utc = datetime.fromisoformat(row["time"]).replace(tzinfo=timezone.utc)
        dt_zh = dt_utc.astimezone(ZURICH_TZ)
        if dt_zh.year != 2025:
            continue
        temp = float(row["temperature_2m (°C)"])
        sw = float(row["shortwave_radiation (W/m²)"])
        dni = float(row["direct_normal_irradiance (W/m²)"])
        if not (math.isfinite(temp) and math.isfinite(sw) and math.isfinite(dni)):
            raise DatasetIntegrityError(f"Non-finite weather reading at {row['time']}")
        parsed_rows.append((dt_utc, dt_zh, temp, sw, dni))

    parsed_rows.sort(key=lambda item: item[0])
    if len(parsed_rows) != HOURS_PER_YEAR:
        raise DatasetIntegrityError(
            f"Expected {HOURS_PER_YEAR} localized 2025 hours for {site}, got {len(parsed_rows)}"
        )

    hours: list[HourlyWeather] = []
    temps_by_day: dict[str, list[float]] = defaultdict(list)

    for idx, (dt_utc, dt_zh, temp, sw, dni) in enumerate(parsed_rows):
        if idx > 0:
            step_sec = (dt_utc - parsed_rows[idx - 1][0]).total_seconds()
            if step_sec != 3600.0:
                raise DatasetIntegrityError(
                    f"Non-hourly step ({step_sec} s) before {dt_utc.isoformat()}"
                )
        offset = dt_zh.utcoffset()
        offset_hours = (offset.total_seconds() / 3600.0) if offset is not None else 1.0
        local_date = dt_zh.date().isoformat()
        temps_by_day[local_date].append(temp)
        hours.append(
            HourlyWeather(
                hour_index=idx,
                timestamp_utc=dt_utc.isoformat().replace("+00:00", "Z"),
                timestamp_zurich=dt_zh.isoformat(),
                local_date=local_date,
                local_hour=dt_zh.hour,
                utc_offset_hours=offset_hours,
                outdoor_temp_c=temp,
                shortwave_radiation_wm2=sw,
                direct_normal_irradiance_wm2=dni,
            )
        )

    if len(temps_by_day) != 365:
        raise DatasetIntegrityError(f"Expected 365 local days, got {len(temps_by_day)}")
    if len(temps_by_day.get("2025-03-30", [])) != 23:
        raise DatasetIntegrityError("Expected 23 local hours on spring DST day 2025-03-30")
    if len(temps_by_day.get("2025-10-26", [])) != 25:
        raise DatasetIntegrityError("Expected 25 local hours on autumn DST day 2025-10-26")

    heating_days = {
        day
        for day, day_temps in temps_by_day.items()
        if (sum(day_temps) / len(day_temps)) < HEATING_DAY_MEAN_THRESHOLD_C
    }

    return WeatherDataset(
        site=site,
        latitude=latitude,
        longitude=longitude,
        elevation_m=elevation_m,
        hours=hours,
        heating_days=heating_days,
        days_count=len(temps_by_day),
        manifest_sha256=file_sha256,
    )


def _load_verified_json(filename: str, data_dir: Path | None = None, verify: bool = True) -> dict:
    base = _resolve_data_dir(data_dir)
    if verify:
        verify_manifest(base)
    path = base / filename
    if not path.is_file():
        raise DatasetIntegrityError(f"Missing pinned JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_heatwise_summary(data_dir: Path | None = None, verify: bool = True) -> dict:
    """Load pinned HEATWISE Zenodo 23237930 summary JSON."""
    return _load_verified_json("heatwise_summary.json", data_dir=data_dir, verify=verify)


def load_mlperf_summary(data_dir: Path | None = None, verify: bool = True) -> dict:
    """Load pinned MLPerf Inference v5.1 Offline power summary JSON."""
    return _load_verified_json("mlperf_v51_offline_summary.json", data_dir=data_dir, verify=verify)


def load_elcom_tariffs(data_dir: Path | None = None, verify: bool = True) -> dict:
    """Load pinned ElCom 2026 IWB Basel electricity tariffs JSON."""
    return _load_verified_json("elcom_basel_2026.json", data_dir=data_dir, verify=verify)
