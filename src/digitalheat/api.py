"""Versioned FastAPI v1 JSON contract and static asset server for CalorGrid."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import mimetypes
import re
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date as date_cls
from datetime import datetime
from functools import lru_cache, wraps
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from digitalheat.cloud_pricing import CloudPricingService
from digitalheat.config import (
    DATA_DIR as CONFIG_DATA_DIR,
)
from digitalheat.config import (
    ELCOM_BASEL_H4_CHF_PER_KWH,
    HEAT_PUMP_REF_COP,
    HEAT_RECOVERY_FRACTION,
    HEAT_VALUE_CHF_PER_KWH,
    PACKAGE_ROOT,
    SERVER_ELECTRICAL_POWER_KW,
    TANK_CAPACITY_KWH,
    TANK_TEMP_MIN_C,
    TANK_TEMP_SPREAD_K,
    HourlySimulationRow,
    HourlyWeather,
    SchedulerDecision,
    SimulationResult,
    WeatherDataset,
)
from digitalheat.configurator import (
    HeaterType,
    compute_configuration_quote,
    fetch_live_swiss_forecast,
    get_configurator_catalog,
)
from digitalheat.data_loader import (
    DatasetIntegrityError,
    get_manifest,
    load_weather_zurich,
    verify_manifest,
)
from digitalheat.fleet import get_swiss_fleet_snapshot, resolve_site_id
from digitalheat.fleet_scheduler import (
    BookingConflictError,
    BookingNotFoundError,
    FleetJobScheduler,
    IdempotencyConflictError,
    QuoteExpiredError,
    ThermalCapacityConflictError,
    ValidationInputError,
)
from digitalheat.physics import (
    VERBUND_SCENE_IDS,
    compute_loop_flow_l_per_min,
    compute_pilot_reality_sensitivity,
    list_verbund_scenes,
    simulate_verbund_scene,
)
from digitalheat.scheduler import (
    SCALING_NOTES,
    ExternalScheduleAdapter,
    run_annual_simulation,
)
from digitalheat.telemetry_log import LogCategory, TelemetryLogStore, get_log_store, log_event
from digitalheat.worker import (
    DEFAULT_MAX_CONTINUOUS_RUNTIME_S,
    JobType,
    WorkerManager,
    get_worker_manager,
)

# Register GLB/GLTF MIME types before mounting static files
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")

API_VERSION = "1.0.0"
DATA_DIR: Path = CONFIG_DATA_DIR
ASSETS_DIR: Path = PACKAGE_ROOT / "assets"
WEB_DIR: Path = PACKAGE_ROOT / "web"
_ADAPTER_LOCK = threading.Lock()
_IMPORTMAP_HASH_CACHE: tuple[int, str] | None = None
_IMPORTMAP_RE = re.compile(
    r'<script\b[^>]*type=["\']importmap["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _get_importmap_sha256_token() -> str:
    global _IMPORTMAP_HASH_CACHE
    index_path = WEB_DIR / "index.html"
    if not index_path.is_file():
        return ""
    try:
        mtime_ns = index_path.stat().st_mtime_ns
        if _IMPORTMAP_HASH_CACHE is not None and _IMPORTMAP_HASH_CACHE[0] == mtime_ns:
            return _IMPORTMAP_HASH_CACHE[1]
        html = index_path.read_text(encoding="utf-8")
        match = _IMPORTMAP_RE.search(html)
        if not match:
            _IMPORTMAP_HASH_CACHE = (mtime_ns, "")
            return ""
        digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
        token = f" 'sha256-{base64.b64encode(digest).decode('ascii')}'"
        _IMPORTMAP_HASH_CACHE = (mtime_ns, token)
        return token
    except OSError:
        return ""

CANONICAL_GLB_KEYS: tuple[str, ...] = (
    "context_neighborhood",
    "verbund_st_jakob",
    "verbund_sole_uno",
    "verbund_ara_basel",
    "verbund_davos",
    "house_bim_shell",
    "hardware_server_rack",
    "hardware_cdu_exchanger",
    "hardware_buffer_tank",
    "hardware_piping",
    "hardware_valve_3way",
    "hardware_power_panel",
    "hardware_basement_enclosure",
)

MANDATORY_NOTICES: list[str] = [
    "(c) swisstopo",
    "(c) OpenStreetMap contributors, ODbL",
    (
        "Open reference IFC 4.3 house blueprint (buildingSMART Simple-Scene), "
        "not a surveyed private Basel residence"
    ),
    "SimReady 3D hardware assets: NVIDIA, Vertiv and ForgeCAD (CC BY 4.0)",
]

DEFAULT_ATTRIBUTIONS: dict[str, dict] = {
    "swisstopo": {
        "datasets": ["swissBUILDINGS3D 3.0", "swissALTI3D 2 m DEM", "swissimage-dop10"],
        "attribution": "(c) swisstopo",
        "license": "swisstopo Open Government Data (OGD) terms of use",
    },
    "openstreetmap": {
        "datasets": ["Overpass API overpass.osm.ch"],
        "attribution": "(c) OpenStreetMap contributors, ODbL",
        "license": "ODbL 1.0",
    },
    "buildingsmart_ifc": {
        "datasets": ["Building-Architecture.ifc", "Building-Structural.ifc", "Building-Hvac.ifc"],
        "attribution": "(C) buildingSMART International Ltd.",
        "license": "CC BY 4.0",
        "disclaimer": (
            "Open reference IFC 4.3 house blueprint (buildingSMART Simple-Scene), "
            "not a surveyed private Basel residence"
        ),
    },
    "nvidia_simready": {
        "datasets": ["nvidia/simready-assets"],
        "attribution": "NVIDIA Corporation",
        "license": "CC BY 4.0",
    },
    "vertiv_simready": {
        "datasets": ["Vertiv/simready-assets"],
        "attribution": "Vertiv",
        "license": "CC BY 4.0",
    },
    "forgecad_simready": {
        "datasets": ["ForgeCAD/simready-assets"],
        "attribution": "ForgeCAD",
        "license": "CC BY 4.0",
    },
}

VERBUND_SCENE_VISUALS: dict[str, dict] = {
    "basel_erdsonde": {
        "glb_key": "context_neighborhood",
        "house_shell": True,
        "camera_preset": {"position": [18.0, 11.0, 22.0], "target": [0.0, 1.0, 0.0], "fov_deg": 45},
    },
    "basel_pool_st_jakob": {
        "glb_key": "verbund_st_jakob",
        "house_shell": False,
        "camera_preset": {"position": [85.0, 55.0, 95.0], "target": [0.0, 4.0, 0.0], "fov_deg": 45},
    },
    "sole_uno": {
        "glb_key": "verbund_sole_uno",
        "house_shell": False,
        "camera_preset": {"position": [80.0, 50.0, 90.0], "target": [0.0, 3.0, 0.0], "fov_deg": 45},
    },
    "hotel_laundry_dhw": {
        "glb_key": "context_neighborhood",
        "house_shell": True,
        "camera_preset": {"position": [24.0, 15.0, 26.0], "target": [0.0, 1.5, 0.0], "fov_deg": 45},
    },
    "brewery_dairy_cip": {
        "glb_key": "context_neighborhood",
        "house_shell": True,
        "camera_preset": {"position": [24.0, 15.0, 26.0], "target": [0.0, 1.5, 0.0], "fov_deg": 45},
    },
    "ara_basel_biogas": {
        "glb_key": "verbund_ara_basel",
        "house_shell": False,
        "camera_preset": {
            "position": [95.0, 62.0, 105.0],
            "target": [0.0, 5.0, 0.0],
            "fov_deg": 45,
        },
    },
    "davos_alpine": {
        "glb_key": "verbund_davos",
        "house_shell": True,
        "camera_preset": {"position": [85.0, 58.0, 95.0], "target": [0.0, 6.0, 0.0], "fov_deg": 45},
    },
}

COCKPIT_PRESETS: dict[str, dict] = {
    "scorecard": {
        "id": "scorecard",
        "label": "Scorecard",
        "position": [68.0, 44.0, 86.0],
        "target": [0.0, 4.0, 0.0],
        "fov_deg": 45,
        "date": "2025-01-15",
        "hour": 12,
        "xray": False,
        "strategy": "smart",
    },
    "jan_peak": {
        "id": "jan_peak",
        "label": "Jan Peak",
        "position": [8.6, 2.6, 10.6],
        "target": [0.3, -0.4, -0.8],
        "fov_deg": 45,
        "date": "2025-01-15",
        "hour": 8,
        "xray": True,
        "strategy": "smart",
    },
    "jul_dump": {
        "id": "jul_dump",
        "label": "Jul Dump",
        "position": [5.4, 0.6, 6.6],
        "target": [0.5, -1.7, -1.2],
        "fov_deg": 45,
        "date": "2025-07-15",
        "hour": 14,
        "xray": True,
        "strategy": "baseline",
    },
    "live_rack": {
        "id": "live_rack",
        "label": "Live Rack",
        "position": [2.2, -1.35, 3.3],
        "target": [-0.3, -1.9, -1.35],
        "fov_deg": 45,
        "date": "2025-01-15",
        "hour": 12,
        "xray": True,
        "strategy": "smart",
    },
    "summer_verbund": {
        "id": "summer_verbund",
        "label": "Summer/Verbund",
        "position": [72.0, 52.0, 82.0],
        "target": [0.0, 4.0, 0.0],
        "fov_deg": 45,
        "date": "2025-07-15",
        "hour": 13,
        "xray": False,
        "strategy": "smart",
        "verbund_scene": "basel_pool_st_jakob",
    },
}


class WorkerStartRequest(BaseModel):
    """Payload to start a local CPU batch worker subprocess."""

    model_config = ConfigDict(allow_inf_nan=False)

    job_type: JobType = "archive_ocr"
    max_units: int | None = Field(default=None, ge=1, le=10000)
    continuous: bool | None = None
    duration_s: float | None = Field(default=None, gt=0.0, le=3600.0)
    tank_soc_kwh: float | None = Field(default=None, ge=0.0, le=TANK_CAPACITY_KWH)
    heat_demand_kw: float | None = Field(default=None, ge=0.0, le=100.0)


class HeatSignalRequest(BaseModel):
    """Thermal heat-signal input evaluated via ReferenceScheduler.decide_live_step."""

    model_config = ConfigDict(allow_inf_nan=False)

    tank_soc_kwh: float = Field(..., ge=0.0, le=TANK_CAPACITY_KWH)
    heat_demand_kw: float = Field(..., ge=0.0, le=100.0)
    outdoor_temp_c: float = 10.0
    queue_depth: int | None = Field(default=None, ge=0)


class ApiSchedulerDecision(SchedulerDecision):
    """Strict JSON-finite decision item rejecting NaN, Infinity and bool-coerced integers."""

    model_config = ConfigDict(allow_inf_nan=False)

    active_servers: StrictInt
    jobs_completed: StrictInt
    jobs_missed_deadline: StrictInt


class AdapterScheduleRequest(BaseModel):
    """External deterministic schedule submission matching Vaibhav/Vilas adapter schema."""

    model_config = ConfigDict(allow_inf_nan=False)

    adapter_name: str = "external_adapter"
    n_servers: int = Field(default=4, ge=1, le=16)
    site: Literal["basel", "davos"] = "basel"
    s_start_kwh: float = Field(default=0.0, ge=0.0, le=TANK_CAPACITY_KWH)
    include_hourly: bool = False
    decisions: list[ApiSchedulerDecision] | None = None
    hourly_active_servers: list[StrictInt] | None = None
    hourly_server_power_kw: list[float] | None = None

    @model_validator(mode="after")
    def _validate_payload(self) -> AdapterScheduleRequest:
        if (
            self.decisions is None
            and self.hourly_active_servers is None
            and self.hourly_server_power_kw is None
        ):
            raise ValueError(
                "At least one of decisions, hourly_active_servers or "
                "hourly_server_power_kw is required"
            )
        if self.decisions is not None:
            for idx, dec in enumerate(self.decisions):
                if dec.jobs_completed < 0:
                    raise ValueError(f"decisions[{idx}].jobs_completed must be >= 0")
                if dec.jobs_missed_deadline < 0:
                    raise ValueError(f"decisions[{idx}].jobs_missed_deadline must be >= 0")
                if not (0 <= dec.active_servers <= self.n_servers):
                    raise ValueError(
                        f"decisions[{idx}].active_servers={dec.active_servers} "
                        f"outside [0, {self.n_servers}]"
                    )
                expected_kw = dec.active_servers * SERVER_ELECTRICAL_POWER_KW
                if (
                    not math.isfinite(dec.server_power_kw)
                    or abs(dec.server_power_kw - expected_kw) > 1e-6
                ):
                    raise ValueError(
                        f"decisions[{idx}].server_power_kw ({dec.server_power_kw}) must equal "
                        f"active_servers * 1.0 ({expected_kw})"
                    )
        if self.hourly_server_power_kw is not None:
            for idx, p_kw in enumerate(self.hourly_server_power_kw):
                if not math.isfinite(p_kw):
                    raise ValueError(f"hourly_server_power_kw[{idx}] ({p_kw}) must be finite")
                if p_kw < -1e-9 or p_kw > self.n_servers * SERVER_ELECTRICAL_POWER_KW + 1e-9:
                    raise ValueError(
                        f"hourly_server_power_kw[{idx}] ({p_kw}) outside "
                        f"[0, {self.n_servers * SERVER_ELECTRICAL_POWER_KW}]"
                    )
                units = p_kw / SERVER_ELECTRICAL_POWER_KW
                if abs(units - round(units)) > 1e-6:
                    raise ValueError(
                        f"hourly_server_power_kw[{idx}] ({p_kw}) must be an integer multiple "
                        f"of {SERVER_ELECTRICAL_POWER_KW} kW"
                    )
        return self


class ComputeQuoteRequest(BaseModel):
    """Customer GPU or full-server compute quote request."""

    model_config = ConfigDict(allow_inf_nan=False)

    tier: Literal["on_demand", "spot"] = "on_demand"
    unit_mode: Literal["gpu", "server"] = "gpu"
    gpu_or_sku: str = Field(..., min_length=1, max_length=64)
    quantity: StrictInt = Field(default=1, ge=1, le=64)
    duration_hours: float = Field(default=4.0, gt=0.0, le=168.0)
    preferred_site_id: str | None = None
    workload_type: str = Field(default="llm_inference", min_length=1, max_length=64)
    job_name: str | None = Field(default=None, min_length=1, max_length=80)


class ComputeBookRequest(BaseModel):
    """Idempotent compute booking request consuming a valid quote_id."""

    model_config = ConfigDict(allow_inf_nan=False)

    quote_id: str = Field(..., min_length=8, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    job_name: str | None = Field(default=None, max_length=80)


class TelemetryTickRequest(BaseModel):
    """Simulation tick advance request for fleet scheduler stepping."""

    model_config = ConfigDict(allow_inf_nan=False)

    hours: float = Field(default=1.0, gt=0.0, le=72.0)


def _sanitize_for_json(obj: object) -> object:
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        if math.isinf(obj):
            return "Infinity" if obj > 0 else "-Infinity"
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, Exception):
        return str(obj)
    return obj


def _safe_read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def compute_solar_position(
    timestamp_utc: str,
    latitude_deg: float,
    longitude_deg: float,
) -> tuple[float, float, list[float]]:
    """Compute (elevation_deg, azimuth_deg, Three.js sun_direction_xyz) via NOAA geometry."""
    dt = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    doy = dt.timetuple().tm_yday
    hour_frac = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    gamma = (2.0 * math.pi / 365.0) * (doy - 1 + (hour_frac - 12.0) / 24.0)

    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.001480 * math.sin(3.0 * gamma)
    )

    true_solar_time_min = (hour_frac * 60.0 + eqtime + 4.0 * longitude_deg) % 1440.0
    ha_deg = true_solar_time_min / 4.0 - 180.0
    ha = math.radians(ha_deg)
    lat_rad = math.radians(latitude_deg)

    cos_zenith = max(
        -1.0,
        min(
            1.0,
            math.sin(lat_rad) * math.sin(decl) + math.cos(lat_rad) * math.cos(decl) * math.cos(ha),
        ),
    )
    zenith_rad = math.acos(cos_zenith)
    elevation_deg = 90.0 - math.degrees(zenith_rad)

    sin_az = -math.cos(decl) * math.sin(ha)
    cos_az = math.sin(decl) * math.cos(lat_rad) - math.cos(decl) * math.sin(lat_rad) * math.cos(ha)
    azimuth_deg = math.degrees(math.atan2(sin_az, cos_az)) % 360.0

    el_rad = math.radians(elevation_deg)
    az_rad = math.radians(azimuth_deg)
    # Three.js Y-up coordinate frame aligned with LV95 (+X East, +Y Up, -Z North)
    sun_xyz = [
        round(math.cos(el_rad) * math.sin(az_rad), 6),
        round(math.sin(el_rad), 6),
        round(-math.cos(el_rad) * math.cos(az_rad), 6),
    ]
    return round(elevation_deg, 3), round(azimuth_deg, 3), sun_xyz


def _single_flight_lru(maxsize: int):
    def decorator(fn):
        cached_fn = lru_cache(maxsize=maxsize)(fn)
        registry_lock = threading.Lock()
        key_locks: dict[tuple, threading.Lock] = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            lock_key = (args, tuple(sorted(kwargs.items()))) if kwargs else args
            with registry_lock:
                key_lock = key_locks.get(lock_key)
                if key_lock is None:
                    key_lock = threading.Lock()
                    key_locks[lock_key] = key_lock
            with key_lock:
                return cached_fn(*args, **kwargs)

        wrapper.cache_clear = cached_fn.cache_clear
        wrapper.cache_info = cached_fn.cache_info
        return wrapper

    return decorator


@_single_flight_lru(maxsize=8)
def _cached_weather(site: Literal["basel", "davos"]) -> WeatherDataset:
    return load_weather_zurich(site=site, data_dir=DATA_DIR)


@_single_flight_lru(maxsize=64)
def _cached_annual_sim(
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline"],
    servers: int,
    site: Literal["basel", "davos"],
) -> SimulationResult:
    return run_annual_simulation(
        strategy=strategy,
        n_servers=servers,
        site=site,
        data_dir=DATA_DIR,
        include_hourly=True,
    )


def _hydronic_display_fields(row: HourlySimulationRow) -> dict:
    tank_soc_pct = round(100.0 * row.tank_soc_kwh / TANK_CAPACITY_KWH, 2)
    tank_temp_c = round(
        TANK_TEMP_MIN_C + TANK_TEMP_SPREAD_K * (row.tank_soc_kwh / TANK_CAPACITY_KWH), 2
    )
    supply_temp_c = round(max(35.0, tank_temp_c), 2) if row.server_power_kw > 0.0 else tank_temp_c
    return_temp_c = round(max(TANK_TEMP_MIN_C, supply_temp_c - 10.0), 2)
    loop_flow_l_per_min = round(compute_loop_flow_l_per_min(row.heat_recovered_kw, 10.0), 3)
    return {
        "tank_soc_pct": tank_soc_pct,
        "tank_temp_c": tank_temp_c,
        "supply_temp_c": supply_temp_c,
        "return_temp_c": return_temp_c,
        "loop_flow_l_per_min": loop_flow_l_per_min,
    }


def _build_daily_rows(sim: SimulationResult, weather: WeatherDataset) -> list[dict]:
    by_day: dict[str, list[tuple[HourlyWeather, HourlySimulationRow]]] = defaultdict(list)
    for w, row in zip(weather.hours, sim.hourly):
        by_day[w.local_date].append((w, row))

    daily_rows: list[dict] = []
    for day_str, pairs in by_day.items():
        n = len(pairs)
        temps = [w.outdoor_temp_c for w, _ in pairs]
        last_row = pairs[-1][1]
        disp = _hydronic_display_fields(last_row)
        daily_rows.append(
            {
                "date": day_str,
                "hours_count": n,
                "is_heating_day": day_str in weather.heating_days,
                "outdoor_temp_mean_c": round(sum(temps) / n, 3),
                "outdoor_temp_min_c": round(min(temps), 2),
                "outdoor_temp_max_c": round(max(temps), 2),
                "solar_irradiance_mean_wm2": round(
                    sum(r.solar_irradiance_wm2 for _, r in pairs) / n, 2
                ),
                "space_heating_kwh": round(sum(r.space_heating_kw for _, r in pairs), 3),
                "dhw_kwh": round(sum(r.dhw_kw for _, r in pairs), 3),
                "heat_demand_kwh": round(sum(r.heat_demand_kw for _, r in pairs), 3),
                "server_electricity_kwh": round(sum(r.server_power_kw for _, r in pairs), 3),
                "heat_recovered_kwh": round(sum(r.heat_recovered_kw for _, r in pairs), 3),
                "heat_used_kwh": round(sum(r.heat_used_kw for _, r in pairs), 3),
                "heat_dumped_kwh": round(sum(r.heat_dumped_kw for _, r in pairs), 3),
                "backup_heat_kwh": round(sum(r.backup_heat_kw for _, r in pairs), 3),
                "tank_soc_end_kwh": last_row.tank_soc_kwh,
                "tank_soc_end_pct": disp["tank_soc_pct"],
                "tank_temp_end_c": disp["tank_temp_c"],
                "basement_air_temp_mean_c": round(
                    sum(r.basement_air_temp_c for _, r in pairs) / n, 2
                ),
                "active_server_hours": sum(r.active_servers for _, r in pairs),
            }
        )
    return daily_rows


def _build_hourly_rows(
    sim: SimulationResult,
    weather: WeatherDataset,
    month: int | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for w, r in zip(weather.hours, sim.hourly):
        if month is not None and int(w.local_date[5:7]) != month:
            continue
        disp = _hydronic_display_fields(r)
        rows.append(
            {
                **r.model_dump(),
                "hour_index": w.hour_index,
                "local_date": w.local_date,
                "local_hour": w.local_hour,
                "timestamp_utc": w.timestamp_utc,
                "utc_offset_hours": w.utc_offset_hours,
                "shortwave_radiation_wm2": w.shortwave_radiation_wm2,
                "direct_normal_irradiance_wm2": w.direct_normal_irradiance_wm2,
                **disp,
            }
        )
    return rows


def _build_day_slice(sim: SimulationResult, weather: WeatherDataset, target_date: str) -> dict:
    pairs = [(w, r) for w, r in zip(weather.hours, sim.hourly) if w.local_date == target_date]
    if not pairs:
        raise HTTPException(
            status_code=404, detail=f"Date '{target_date}' not found in 2025 {sim.site} dataset"
        )

    hours_enriched: list[dict] = []
    for w, r in pairs:
        el_deg, az_deg, sun_xyz = compute_solar_position(
            w.timestamp_utc, weather.latitude, weather.longitude
        )
        disp = _hydronic_display_fields(r)
        hours_enriched.append(
            {
                **r.model_dump(),
                "hour_index": w.hour_index,
                "local_hour": w.local_hour,
                "timestamp_utc": w.timestamp_utc,
                "utc_offset_hours": w.utc_offset_hours,
                "shortwave_radiation_wm2": w.shortwave_radiation_wm2,
                "direct_normal_irradiance_wm2": w.direct_normal_irradiance_wm2,
                "solar_elevation_deg": el_deg,
                "solar_azimuth_deg": az_deg,
                "sun_direction_xyz": sun_xyz,
                **disp,
            }
        )

    n = len(pairs)
    is_dst = n != 24
    dst_note = (
        "Spring DST transition (23 local hours, 02:00 CET skipped to 03:00 CEST)"
        if n == 23
        else ("Autumn DST transition (25 local hours, 02:00-03:00 repeated)" if n == 25 else None)
    )
    summary = {
        "heat_demand_kwh": round(sum(r.heat_demand_kw for _, r in pairs), 4),
        "heat_used_kwh": round(sum(r.heat_used_kw for _, r in pairs), 4),
        "heat_dumped_kwh": round(sum(r.heat_dumped_kw for _, r in pairs), 4),
        "server_electricity_kwh": round(sum(r.server_power_kw for _, r in pairs), 4),
        "heat_recovered_kwh": round(sum(r.heat_recovered_kw for _, r in pairs), 4),
        "backup_heat_kwh": round(sum(r.backup_heat_kw for _, r in pairs), 4),
        "conservation_residual_kwh": round(max(r.conservation_residual_kwh for _, r in pairs), 12),
    }
    return {
        "date": target_date,
        "site": sim.site,
        "strategy": sim.strategy,
        "servers_per_house": sim.kpis.servers_per_house,
        "hours_count": n,
        "is_dst_transition": is_dst,
        "dst_note": dst_note,
        "is_heating_day": target_date in weather.heating_days,
        "daily_mean_temp_c": round(sum(w.outdoor_temp_c for w, _ in pairs) / n, 3),
        "day_summary": dict(summary),
        "daily_summary": dict(summary),
        "hours": hours_enriched,
        "hourly": copy.deepcopy(hours_enriched),
    }


@lru_cache(maxsize=64)
def _cached_daily_timeseries(
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline"],
    servers: int,
    site: Literal["basel", "davos"],
) -> list[dict]:
    return _build_daily_rows(_cached_annual_sim(strategy, servers, site), _cached_weather(site))


@lru_cache(maxsize=128)
def _cached_hourly_timeseries(
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline"],
    servers: int,
    site: Literal["basel", "davos"],
    month: int | None,
) -> list[dict]:
    return _build_hourly_rows(
        _cached_annual_sim(strategy, servers, site), _cached_weather(site), month=month
    )


@lru_cache(maxsize=256)
def _cached_day_slice(
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline"],
    servers: int,
    site: Literal["basel", "davos"],
    target_date: str,
) -> dict:
    return _build_day_slice(
        _cached_annual_sim(strategy, servers, site), _cached_weather(site), target_date
    )


@_single_flight_lru(maxsize=16)
def _cached_verbund_scenes(servers: int) -> list[dict]:
    return list_verbund_scenes(n_servers=servers, data_dir=DATA_DIR)


@_single_flight_lru(maxsize=64)
def _cached_verbund_scene(scene_id: str, servers: int) -> dict:
    return simulate_verbund_scene(scene_id=scene_id, n_servers=servers, data_dir=DATA_DIR)


@_single_flight_lru(maxsize=16)
def _cached_pilot_reality(servers: int) -> dict:
    return compute_pilot_reality_sensitivity(n_servers=servers, data_dir=DATA_DIR)


def _baseline_comparison(res: SimulationResult, base: SimulationResult) -> dict:
    return {
        "baseline_strategy": base.strategy,
        "baseline_heat_dumped_kwh": round(base.kpis.heat_dumped_kwh, 3),
        "baseline_heat_covered_share": round(base.kpis.heat_demand_covered_share, 6),
        "baseline_break_even_chf_per_srv_h": round(
            base.kpis.break_even_computing_price_chf_per_server_hour, 6
        ),
        "heat_dumped_reduction_kwh": round(base.kpis.heat_dumped_kwh - res.kpis.heat_dumped_kwh, 3),
        "heat_covered_delta_share": round(
            res.kpis.heat_demand_covered_share - base.kpis.heat_demand_covered_share, 6
        ),
        "break_even_delta_chf_per_srv_h": round(
            res.kpis.break_even_computing_price_chf_per_server_hour
            - base.kpis.break_even_computing_price_chf_per_server_hour,
            6,
        ),
        "missed_deadlines_delta_pct": round(
            res.kpis.missed_deadlines_pct - base.kpis.missed_deadlines_pct, 4
        ),
        "utilisation_delta": round(res.kpis.server_utilisation - base.kpis.server_utilisation, 6),
    }


def _format_annual_payload(
    res: SimulationResult,
    base: SimulationResult,
    weather: WeatherDataset,
) -> dict:
    return {
        "strategy": res.strategy,
        "site": res.site,
        "servers_per_house": res.kpis.servers_per_house,
        "kpis": res.kpis.model_dump(),
        "baseline_comparison": _baseline_comparison(res, base),
        "negative_payoff_statement": res.negative_payoff_statement,
        "arrival_rate_source": res.arrival_rate_source,
        "scaling_notes": res.scaling_notes,
        "conservation_residual_kwh": res.conservation_residual_kwh,
        "max_hourly_residual_kwh": res.max_hourly_residual_kwh,
        "s_start_kwh": res.s_start_kwh,
        "s_end_kwh": res.s_end_kwh,
        "heating_days_count": res.heating_days_count,
        "dataset_provenance": {
            "weather_sha256": weather.manifest_sha256,
            "site_latitude": weather.latitude,
            "site_longitude": weather.longitude,
            "site_elevation_m": weather.elevation_m,
            "hours_count": len(weather.hours),
            "days_count": weather.days_count,
        },
        "constants": {
            "server_electrical_power_kw": SERVER_ELECTRICAL_POWER_KW,
            "heat_recovery_fraction": HEAT_RECOVERY_FRACTION,
            "tank_capacity_kwh": TANK_CAPACITY_KWH,
            "elcom_basel_h4_chf_per_kwh": ELCOM_BASEL_H4_CHF_PER_KWH,
            "heat_pump_ref_cop": HEAT_PUMP_REF_COP,
            "heat_value_chf_per_kwh": HEAT_VALUE_CHF_PER_KWH,
        },
    }


def _resolve_simulation(
    app_state,
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline", "adapter"],
    servers: int,
    site: Literal["basel", "davos"],
) -> SimulationResult:
    if strategy == "adapter":
        with _ADAPTER_LOCK:
            adapter_map = getattr(app_state, "adapter_results", {})
            res = adapter_map.get((servers, site)) if isinstance(adapter_map, dict) else None
        if res is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No external adapter schedule registered for servers={servers}, site={site}. "
                    "Submit a schedule via POST /api/v1/scheduler/adapter first."
                ),
            )
        return res
    return _cached_annual_sim(strategy, servers, site)


def _format_worker_response(manager: WorkerManager, extra: dict | None = None) -> dict:
    st = manager.get_status()
    payload = st.model_dump()
    payload["os_status"] = st.psutil_status
    payload["units_processed"] = st.units_completed
    payload["energy_per_unit_j"] = st.scaled_1kw.measured_energy_per_unit_j
    payload["scaled_units_per_1kw_server_hour"] = st.scaled_1kw.scaled_units_per_server_hour
    payload["scaling_notes"] = SCALING_NOTES
    if extra:
        payload.update(extra)
    return payload


def _enrich_verbund_scene(scene: dict, assets_manifest: dict) -> dict:
    scene = copy.deepcopy(scene)
    sid = scene["scene_id"]
    vis = VERBUND_SCENE_VISUALS[sid]
    glb_key = vis["glb_key"]
    glb_rel = f"assets/glb/{glb_key}.glb"
    glb_available = (ASSETS_DIR / "glb" / f"{glb_key}.glb").is_file()

    raw_split = scene.get("july_valve_split_pct")
    split = raw_split if isinstance(raw_split, dict) else {}
    p_frac = round(float(split.get("primary_pct", 100.0)) / 100.0, 4)
    s_frac = round(float(split.get("secondary_pct", 0.0)) / 100.0, 4)
    s_frac = min(s_frac, round(max(0.0, 1.0 - p_frac), 4))
    d_frac = round(max(0.0, 1.0 - p_frac - s_frac), 4)

    if d_frac > 0.01 and s_frac > 0.01:
        valve_state = "diverting_with_partial_bypass"
    elif d_frac > 0.01:
        valve_state = "summer_dump_bypass_active"
    elif s_frac > 0.01:
        valve_state = "diverter_to_verbund_sink"
    else:
        valve_state = "primary_sink_absorbing"

    raw_assets = assets_manifest.get("assets")
    assets_map = raw_assets if isinstance(raw_assets, dict) else {}
    raw_meta = assets_map.get(glb_key)
    asset_meta = raw_meta if isinstance(raw_meta, dict) else {}
    cam = vis["camera_preset"]
    return {
        **scene,
        "glb_path": glb_rel,
        "glb_url": f"/{glb_rel}",
        "glb_available": glb_available,
        "house_shell_glb_url": "/assets/glb/house_bim_shell.glb" if vis["house_shell"] else None,
        "three_way_valve": {
            "primary_fraction": p_frac,
            "secondary_fraction": s_frac,
            "dump_fraction": d_frac,
        },
        "diverter_valve_state": valve_state,
        "camera_preset": {**copy.deepcopy(cam), "fov": cam["fov_deg"]},
        "lv95_origin": copy.deepcopy(asset_meta.get("lv95_origin")),
        "wgs84": copy.deepcopy(asset_meta.get("wgs84")),
    }


_STATE_INIT_LOCK = threading.Lock()


def _get_state_lock(app_obj: FastAPI) -> threading.RLock:
    lock = getattr(app_obj.state, "state_lock", None)
    if lock is None:
        with _STATE_INIT_LOCK:
            lock = getattr(app_obj.state, "state_lock", None)
            if lock is None:
                lock = threading.RLock()
                app_obj.state.state_lock = lock
                app_obj.state.scheduler_lock = lock
    return lock


def _get_log_store(app_obj: FastAPI) -> TelemetryLogStore:
    store = getattr(app_obj.state, "log_store", None)
    if store is None:
        with _STATE_INIT_LOCK:
            store = getattr(app_obj.state, "log_store", None)
            if store is None:
                store = get_log_store()
                app_obj.state.log_store = store
    return store


def _get_pricing_service(app_obj: FastAPI) -> CloudPricingService:
    svc = getattr(app_obj.state, "pricing_service", None)
    if svc is None:
        with _STATE_INIT_LOCK:
            svc = getattr(app_obj.state, "pricing_service", None)
            if svc is None:
                svc = CloudPricingService(data_dir=DATA_DIR)
                app_obj.state.pricing_service = svc
    return svc


def _get_fleet_scheduler(app_obj: FastAPI) -> FleetJobScheduler:
    sched = getattr(app_obj.state, "fleet_scheduler", None)
    if sched is None:
        lock = _get_state_lock(app_obj)
        store = _get_log_store(app_obj)
        pricing = _get_pricing_service(app_obj)
        with _STATE_INIT_LOCK:
            sched = getattr(app_obj.state, "fleet_scheduler", None)
            if sched is None:
                sched = FleetJobScheduler(
                    lock=lock,
                    log_store=store,
                    data_dir=DATA_DIR,
                    pricing_service=pricing,
                )
                app_obj.state.fleet_scheduler = sched
    return sched


def _get_manager_from_app(app_obj: FastAPI) -> WorkerManager:
    mgr = getattr(app_obj.state, "worker_manager", None)
    if mgr is None:
        mgr = get_worker_manager()
        mgr.set_log_store(_get_log_store(app_obj))
        app_obj.state.worker_manager = mgr
    return mgr


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    try:
        verify_manifest(data_dir=DATA_DIR)
        app_instance.state.datasets_verified = True
        app_instance.state.dataset_error = None
    except (DatasetIntegrityError, json.JSONDecodeError, OSError, ValueError) as exc:
        app_instance.state.datasets_verified = False
        app_instance.state.dataset_error = str(exc)

    lock = threading.RLock()
    log_store = get_log_store()
    pricing_service = CloudPricingService(data_dir=DATA_DIR)
    fleet_scheduler = FleetJobScheduler(
        lock=lock,
        log_store=log_store,
        data_dir=DATA_DIR,
        pricing_service=pricing_service,
    )
    app_instance.state.state_lock = lock
    app_instance.state.scheduler_lock = lock
    app_instance.state.log_store = log_store
    app_instance.state.pricing_service = pricing_service
    app_instance.state.fleet_scheduler = fleet_scheduler
    app_instance.state.quotes = {}
    app_instance.state.idempotency_bookings = {}
    app_instance.state.state_version = 1

    mgr = get_worker_manager()
    mgr.set_log_store(log_store)
    app_instance.state.worker_manager = mgr
    app_instance.state.adapter_results = {}

    if app_instance.state.datasets_verified:
        _cached_weather("basel")
        _cached_annual_sim("smart", 4, "basel")
        _cached_annual_sim("baseline", 4, "basel")
        _cached_daily_timeseries("smart", 4, "basel")
        _cached_daily_timeseries("baseline", 4, "basel")
        pricing_service.get_pricing_snapshot(trigger_refresh=True)

    try:
        yield
    finally:
        app_instance.state.worker_manager.shutdown()


app = FastAPI(
    title="CalorGrid 3D Digital Twin & Reference Scheduler API",
    version=API_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_stale_ui_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    if not path.startswith(("/docs", "/redoc")):
        importmap_sha_token = _get_importmap_sha256_token()
        csp_parts = [
            "default-src 'self'",
            f"script-src 'self' 'wasm-unsafe-eval'{importmap_sha_token}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' blob: data:",
            "media-src 'self' blob:",
            "worker-src 'self' blob:",
            "connect-src 'self' blob: data:",
            "font-src 'self' data:",
            "object-src 'none'",
            "base-uri 'self'",
            "frame-ancestors 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_parts)
    return response


@app.exception_handler(RequestValidationError)
async def _request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    safe_errors = jsonable_encoder(_sanitize_for_json(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": safe_errors})


@app.exception_handler(DatasetIntegrityError)
async def _dataset_integrity_error_handler(
    _request: Request, exc: DatasetIntegrityError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/api/v1/health")
def get_health() -> dict:
    mgr = _get_manager_from_app(app)
    verified = getattr(app.state, "datasets_verified", None)
    dataset_err = getattr(app.state, "dataset_error", None)
    if verified is None:
        try:
            verify_manifest(data_dir=DATA_DIR)
            verified = True
            dataset_err = None
        except (DatasetIntegrityError, json.JSONDecodeError, OSError, ValueError) as exc:
            verified = False
            dataset_err = str(exc)

    return {
        "status": "ok" if verified else "degraded",
        "version": API_VERSION,
        "scheduler": "ReferenceScheduler",
        "datasets_verified": verified,
        "dataset_error": dataset_err,
        "worker_state": mgr.get_status().state,
        "assets_manifest_present": (ASSETS_DIR / "manifest.json").is_file(),
        "web_mounted": (WEB_DIR / "index.html").is_file(),
    }


@app.get("/api/v1/simulation/annual")
def get_simulation_annual(
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline", "adapter"] = "smart",
    servers: int = Query(4, ge=1, le=16),
    site: Literal["basel", "davos"] = "basel",
) -> dict:
    res = _resolve_simulation(app.state, strategy, servers, site)
    base = _cached_annual_sim("baseline", servers, site)
    weather = _cached_weather(site)
    return _format_annual_payload(res, base, weather)


@app.get("/api/v1/simulation/timeseries")
def get_simulation_timeseries(
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline", "adapter"] = "smart",
    servers: int = Query(4, ge=1, le=16),
    site: Literal["basel", "davos"] = "basel",
    resolution: Literal["hourly", "daily"] = "daily",
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    if strategy == "adapter":
        sim = _resolve_simulation(app.state, strategy, servers, site)
        weather = _cached_weather(site)
        if resolution == "daily":
            rows = _build_daily_rows(sim, weather)
            if month is not None:
                rows = [r for r in rows if int(r["date"][5:7]) == month]
        else:
            rows = _build_hourly_rows(sim, weather, month=month)
    else:
        if resolution == "daily":
            cached_rows = _cached_daily_timeseries(strategy, servers, site)
            if month is not None:
                rows = [dict(r) for r in cached_rows if int(r["date"][5:7]) == month]
            else:
                rows = [dict(r) for r in cached_rows]
        else:
            rows = [dict(r) for r in _cached_hourly_timeseries(strategy, servers, site, month)]

    return {
        "strategy": strategy,
        "site": site,
        "servers_per_house": servers,
        "resolution": resolution,
        "month": month,
        "count": len(rows),
        "rows": rows,
    }


@app.get("/api/v1/simulation/day/{target_date}")
def get_simulation_day(
    target_date: str,
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline", "adapter"] = "smart",
    servers: int = Query(4, ge=1, le=16),
    site: Literal["basel", "davos"] = "basel",
) -> dict:
    try:
        date_cls.fromisoformat(target_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid ISO date '{target_date}': expected YYYY-MM-DD"
        ) from exc

    if strategy == "adapter":
        sim = _resolve_simulation(app.state, strategy, servers, site)
        weather = _cached_weather(site)
        return _build_day_slice(sim, weather, target_date)
    return copy.deepcopy(_cached_day_slice(strategy, servers, site, target_date))


@app.get("/api/v1/worker/status")
def get_worker_status() -> dict:
    mgr = _get_manager_from_app(app)
    return _format_worker_response(mgr)


@app.post("/api/v1/worker/start")
def post_worker_start(req: WorkerStartRequest | None = None) -> dict:
    body = req if req is not None else WorkerStartRequest()
    mgr = _get_manager_from_app(app)
    max_runtime_s = (
        body.duration_s if body.duration_s is not None else DEFAULT_MAX_CONTINUOUS_RUNTIME_S
    )
    mgr.start(
        job_type=body.job_type,
        max_units=body.max_units,
        continuous=body.continuous,
        max_runtime_s=max_runtime_s,
    )
    if body.tank_soc_kwh is not None and body.heat_demand_kw is not None:
        mgr.apply_heat_signal(
            tank_soc_kwh=body.tank_soc_kwh,
            heat_demand_kw=body.heat_demand_kw,
        )
    return _format_worker_response(mgr)


@app.post("/api/v1/worker/heat-signal")
def post_worker_heat_signal(req: HeatSignalRequest) -> dict:
    mgr = _get_manager_from_app(app)
    before = mgr.get_status().state
    after_status = mgr.apply_heat_signal(
        tank_soc_kwh=req.tank_soc_kwh,
        heat_demand_kw=req.heat_demand_kw,
        outdoor_temp_c=req.outdoor_temp_c,
        queue_depth=req.queue_depth,
    )
    if before == "running" and after_status.state == "paused":
        sig_sent = "SIGSTOP"
    elif before == "paused" and after_status.state == "running":
        sig_sent = "SIGCONT"
    else:
        sig_sent = "NONE"

    return _format_worker_response(
        mgr,
        extra={"decision": after_status.heat_signal_on, "signal_sent": sig_sent},
    )


@app.post("/api/v1/worker/pause")
def post_worker_pause() -> dict:
    mgr = _get_manager_from_app(app)
    before = mgr.get_status().state
    after = mgr.pause()
    sig_sent = "SIGSTOP" if before == "running" and after.state == "paused" else "NONE"
    return _format_worker_response(
        mgr, extra={"decision": after.heat_signal_on, "signal_sent": sig_sent}
    )


@app.post("/api/v1/worker/resume")
def post_worker_resume() -> dict:
    mgr = _get_manager_from_app(app)
    before = mgr.get_status().state
    after = mgr.resume()
    sig_sent = "SIGCONT" if before == "paused" and after.state == "running" else "NONE"
    return _format_worker_response(
        mgr, extra={"decision": after.heat_signal_on, "signal_sent": sig_sent}
    )


@app.post("/api/v1/worker/stop")
def post_worker_stop() -> dict:
    mgr = _get_manager_from_app(app)
    mgr.stop()
    return _format_worker_response(mgr)


@app.post("/api/v1/scheduler/adapter")
def post_scheduler_adapter(req: AdapterScheduleRequest) -> dict:
    try:
        adapter = ExternalScheduleAdapter(
            decisions=req.decisions,
            hourly_active_servers=req.hourly_active_servers,
            hourly_server_power_kw=req.hourly_server_power_kw,
            adapter_name=req.adapter_name,
        )
        sim_full = run_annual_simulation(
            strategy="adapter",
            n_servers=req.n_servers,
            site=req.site,
            s_start_kwh=req.s_start_kwh,
            data_dir=DATA_DIR,
            include_hourly=True,
            adapter=adapter,
        )
    except DatasetIntegrityError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with _ADAPTER_LOCK:
        if not hasattr(app.state, "adapter_results") or app.state.adapter_results is None:
            app.state.adapter_results = {}
        app.state.adapter_results[(req.n_servers, req.site)] = sim_full

    weather = _cached_weather(req.site)
    base = _cached_annual_sim("baseline", req.n_servers, req.site)
    payload = _format_annual_payload(sim_full, base, weather)
    payload["adapter_name"] = req.adapter_name
    if req.include_hourly:
        payload["hourly"] = [r.model_dump() for r in sim_full.hourly]
    log_event(
        "simulation_run",
        "external_adapter_evaluated",
        f"Evaluated external adapter '{req.adapter_name}' ({req.n_servers} servers, {req.site})",
        source_module="api",
        site_id="basel_klingental" if req.site == "basel" else "davos_alpine",
        metrics={
            "adapter_name": req.adapter_name,
            "n_servers": req.n_servers,
            "site": req.site,
            "heat_dumped_kwh": round(sim_full.kpis.heat_dumped_kwh, 2),
        },
        store=_get_log_store(app),
    )
    return payload


@app.get("/api/v1/verbund/scenes")
def get_verbund_scenes(servers: int = Query(4, ge=1, le=16)) -> dict:
    assets_manifest = _safe_read_json(ASSETS_DIR / "manifest.json")
    scenes = [_enrich_verbund_scene(s, assets_manifest) for s in _cached_verbund_scenes(servers)]
    return {
        "servers_per_house": servers,
        "count": len(scenes),
        "scenes": scenes,
    }


@app.get("/api/v1/verbund/scenes/{scene_id}")
def get_verbund_scene(scene_id: str, servers: int = Query(4, ge=1, le=16)) -> dict:
    if scene_id not in VERBUND_SCENE_IDS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown Verbund scene_id '{scene_id}'. Expected one of {VERBUND_SCENE_IDS}",
        )
    assets_manifest = _safe_read_json(ASSETS_DIR / "manifest.json")
    raw = _cached_verbund_scene(scene_id, servers)
    return _enrich_verbund_scene(raw, assets_manifest)


@app.get("/api/v1/sensitivity/pilot-reality")
def get_pilot_reality_sensitivity(servers: int = Query(4, ge=1, le=16)) -> dict:
    data_manifest = _safe_read_json(DATA_DIR / "manifest.json")
    result = copy.deepcopy(_cached_pilot_reality(servers))
    raw_files = data_manifest.get("files")
    files_map = raw_files if isinstance(raw_files, dict) else {}
    return {
        **result,
        "provenance_category": "pilot_reality_sensitivity",
        "dataset_sha256": {
            fname: meta.get("sha256")
            for fname, meta in files_map.items()
            if isinstance(meta, dict)
        },
    }


@app.get("/api/v1/cockpit/presets")
def get_cockpit_presets() -> dict:
    return {
        "schema_version": API_VERSION,
        "presets": [copy.deepcopy(p) for p in COCKPIT_PRESETS.values()],
    }


@app.get("/api/v1/provenance")
def get_provenance() -> dict:
    try:
        loaded_manifest = get_manifest(data_dir=DATA_DIR)
        data_manifest = loaded_manifest if isinstance(loaded_manifest, dict) else {}
    except (DatasetIntegrityError, json.JSONDecodeError, OSError, ValueError):
        data_manifest = {}

    assets_manifest = _safe_read_json(ASSETS_DIR / "manifest.json")
    raw_attr = _safe_read_json(ASSETS_DIR / "attributions.json")

    merged_attributions = copy.deepcopy(DEFAULT_ATTRIBUTIONS)
    raw_sources = raw_attr.get("sources")
    if isinstance(raw_sources, dict):
        for key, val in raw_sources.items():
            if isinstance(val, dict):
                merged_attributions[key] = val

    raw_manifest_assets = assets_manifest.get("assets")
    manifest_assets = raw_manifest_assets if isinstance(raw_manifest_assets, dict) else {}
    assets_out: dict[str, dict] = {}
    missing_assets: list[str] = []

    all_keys = list(CANONICAL_GLB_KEYS)
    for k in manifest_assets:
        if k not in all_keys:
            all_keys.append(k)

    for key in all_keys:
        raw_meta = manifest_assets.get(key)
        meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        raw_file = meta.get("file")
        rel_file = raw_file if isinstance(raw_file, str) and raw_file else f"glb/{key}.glb"
        raw_cat = meta.get("provenance_category")
        prov_cat = raw_cat if isinstance(raw_cat, str) and raw_cat else "geodata_cad_blueprint"
        disk_path = ASSETS_DIR / rel_file
        avail = disk_path.is_file()
        if not avail:
            missing_assets.append(key)
        assets_out[key] = {
            **meta,
            "asset_id": key,
            "file": rel_file,
            "url": f"/assets/{rel_file}",
            "available": avail,
            "available_on_disk": avail,
            "provenance_category": prov_cat,
        }

    categories = {
        "measured_pid": {
            "id": "measured_pid",
            "label": "Measured (Live PID Worker)",
            "color_hex": "#22c55e",
            "description": (
                "Live OS subprocess CPU worker telemetry (psutil RSS/CPU %, "
                "measured runtime/energy and SIGSTOP/SIGCONT state)."
            ),
            "mesh_groups": ["live_rack_throttle_overlay", "worker_pid_beacon"],
            "sources": ["local_cpu_worker_psutil", "archive_ocr_tesseract", "iiif_embeddings_fft"],
        },
        "official_benchmark": {
            "id": "official_benchmark",
            "label": "Official Benchmark (Open-Meteo + ElCom + 35 kWh Tank)",
            "color_hex": "#f59e0b",
            "description": (
                "Pinned Basel and Davos 2025 Open-Meteo weather (Europe/Zurich DST), "
                "ElCom 2026 IWB H4 (0.3325 CHF/kWh) and 35 kWh lossless buffer tank."
            ),
            "mesh_groups": [
                "hydronic_pipe_supply",
                "hydronic_pipe_return",
                "hydronic_pipe_dump_bypass",
                "tank_thermal_fluid",
                "solar_sun_rig",
            ],
            "sources": ["open_meteo_2025", "elcom_2026_iwb", "aquasar_0_8_recovery"],
        },
        "geodata_cad_blueprint": {
            "id": "geodata_cad_blueprint",
            "label": "Geodata / CAD / Open Reference Blueprint",
            "color_hex": "#38bdf8",
            "description": (
                "swisstopo + OpenStreetMap context, buildingSMART IFC 4.3 open reference "
                "blueprint (not a surveyed private Basel residence) and CC BY 4.0 "
                "SimReady hardware."
            ),
            "mesh_groups": list(CANONICAL_GLB_KEYS),
            "sources": [
                "swisstopo",
                "openstreetmap",
                "buildingsmart_ifc",
                "nvidia_simready",
                "vertiv_simready",
                "forgecad_simready",
            ],
        },
        "pilot_reality_sensitivity": {
            "id": "pilot_reality_sensitivity",
            "label": "Pilot Reality Sensitivity",
            "color_hex": "#a855f7",
            "description": (
                "HEATWISE Zenodo 23237930, MLPerf Inference v5.1 Offline, ASHRAE W45 45 C limit, "
                "CAPEX vs utilisation cliff and retail H4 vs industrial C2-C6 tariffs."
            ),
            "mesh_groups": ["ashrae_w45_thermal_badge", "pilot_reality_overlay"],
            "sources": ["heatwise_23237930", "mlperf_v51_offline", "ashrae_w45", "elcom_c2_c6"],
        },
    }

    raw_ctx = manifest_assets.get("context_neighborhood")
    ctx_meta = raw_ctx if isinstance(raw_ctx, dict) else {}
    coord_frame = {
        "threejs": "Y-up meters, 1 unit = 1 m",
        "basel_lv95_epsg": "EPSG:2056",
        "basel_origin_lv95": ctx_meta.get(
            "lv95_origin", {"E0": 2611430.949, "N0": 1267910.222, "Z0": 250.118}
        ),
        "basel_origin_wgs84": ctx_meta.get("wgs84", {"lat": 47.561804, "lon": 7.590508}),
    }

    notices = list(MANDATORY_NOTICES)
    raw_notices = raw_attr.get("mandatory_notices")
    if isinstance(raw_notices, list):
        for n in raw_notices:
            if isinstance(n, str) and n not in notices:
                notices.append(n)

    raw_files = data_manifest.get("files")
    datasets_out = raw_files if isinstance(raw_files, dict) else {}
    avail_count = sum(1 for a in assets_out.values() if a["available"])
    return {
        "schema_version": API_VERSION,
        "assets_ready": len(missing_assets) == 0,
        "expected_asset_count": len(CANONICAL_GLB_KEYS),
        "available_asset_count": avail_count,
        "missing_assets": missing_assets,
        "coordinate_frame": coord_frame,
        "categories": categories,
        "provenance_categories": categories,
        "blueprint_disclaimer": MANDATORY_NOTICES[2],
        "mandatory_notices": notices,
        "attributions": merged_attributions,
        "assets": assets_out,
        "datasets": datasets_out,
        "cockpit_presets": copy.deepcopy(COCKPIT_PRESETS),
    }


@app.get("/api/v1/fleet")
def api_swiss_fleet(
    day_of_year: int = Query(196, ge=1, le=365),
    hour: int = Query(14, ge=0, le=23),
    strategy: Literal["smart", "baseline"] = Query("smart"),
) -> dict:
    """Return Switzerland-wide 14-node digital twin fleet state, economics and migration arcs."""
    return get_swiss_fleet_snapshot(day_of_year=day_of_year, hour=hour, strategy=strategy)


@app.get("/api/v1/configurator/catalog")
def api_configurator_catalog() -> dict:
    """Return selectable Swiss deployment archetypes and verified server hardware models."""
    return get_configurator_catalog()


@app.get("/api/v1/configurator/quote")
def api_configurator_quote(
    deployment_id: str = Query("house_basel"),
    server_model_id: str = Query("hydronode_1kw"),
    server_count: int = Query(4, ge=1, le=8),
    heat_target_pct: int = Query(100, ge=20, le=140),
    outdoor_temp_c: float | None = Query(None, ge=-25.0, le=42.0),
    indoor_target_c: float = Query(21.0, ge=18.0, le=24.0),
    has_existing_boiler: bool = Query(False),
    heater_type: HeaterType | None = Query(None),
) -> dict:
    """Compute host boiler quote, thermal output and tomorrow's HeatLedger dispatch plan."""
    fleet_sched = _get_fleet_scheduler(app)
    mgr = _get_manager_from_app(app)
    worker_st = mgr.get_status()

    fleet_site_snap = None
    try:
        resolved_site = resolve_site_id(deployment_id)
        tel = fleet_sched.get_fleet_telemetry(site_id=resolved_site, worker_status=worker_st)
        if tel.get("sites"):
            fleet_site_snap = tel["sites"][0]
    except (ValueError, ValidationInputError):
        fleet_site_snap = None

    try:
        return compute_configuration_quote(
            deployment_id=deployment_id,
            server_model_id=server_model_id,
            server_count=server_count,
            heat_target_pct=heat_target_pct,
            outdoor_temp_c=outdoor_temp_c,
            indoor_target_c=indoor_target_c,
            has_existing_boiler=has_existing_boiler,
            heater_type=heater_type,
            fleet_site_snapshot=fleet_site_snap,
            worker_status=worker_st,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/weather/forecast")
def api_weather_forecast(deployment_id: str = Query("house_basel")) -> dict:
    """Return tomorrow's live Swiss forecast via keyless Open-Meteo (with offline fallback)."""
    try:
        return fetch_live_swiss_forecast(deployment_id=deployment_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/logs")
def api_get_logs(
    category: LogCategory | None = Query(None),
    site_id: str | None = Query(None),
    after_sequence: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=500),
    order: Literal["desc", "asc"] = Query("desc"),
) -> dict:
    """Return chronological structured application, simulation, dispatch and booking logs."""
    store = _get_log_store(app)
    if site_id is not None:
        try:
            resolve_site_id(site_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return store.query(
        category=category,
        site_id=site_id,
        after_sequence=after_sequence,
        limit=limit,
        order=order,
        site_resolver=resolve_site_id,
    )


@app.get("/api/v1/fleet/telemetry")
def api_fleet_telemetry(
    site_id: str | None = Query(None),
    day_of_year: int | None = Query(None, ge=1, le=365),
    hour: int | None = Query(None, ge=0, le=23),
) -> dict:
    """Return copy.deepcopy live per-site, per-server and per-GPU fleet telemetry."""
    sched = _get_fleet_scheduler(app)
    mgr = _get_manager_from_app(app)
    try:
        return sched.get_fleet_telemetry(
            site_id=site_id,
            worker_status=mgr.get_status(),
            day_of_year=day_of_year,
            hour=hour,
        )
    except ValidationInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/fleet/telemetry/tick")
def api_fleet_telemetry_tick(req: TelemetryTickRequest | None = None) -> dict:
    """Advance unified fleet simulation clock, RC building temps, migrations and backfill."""
    body = req if req is not None else TelemetryTickRequest()
    sched = _get_fleet_scheduler(app)
    try:
        return sched.dispatch_tick(hours=body.hours)
    except ValidationInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/compute/pricing")
def api_compute_pricing(refresh: bool = Query(False)) -> dict:
    """Return non-blocking traditional cloud vs CalorGrid On-Demand/Spot pricing."""
    svc = _get_pricing_service(app)
    if refresh:
        # Trigger async background refresh without blocking API response
        return svc.get_public_pricing_payload(trigger_refresh=True)
    return svc.get_public_pricing_payload(trigger_refresh=True)


@app.post("/api/v1/compute/quote")
def api_compute_quote(req: ComputeQuoteRequest) -> dict:
    """Quote On-Demand or Spot GPU/server capacity with thermal headroom checks."""
    sched = _get_fleet_scheduler(app)
    try:
        return sched.create_quote(
            tier=req.tier,
            unit_mode=req.unit_mode,
            gpu_or_sku=req.gpu_or_sku,
            quantity=req.quantity,
            duration_hours=req.duration_hours,
            preferred_site_id=req.preferred_site_id,
            workload_type=req.workload_type,
            job_name=req.job_name,
        )
    except ValidationInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ThermalCapacityConflictError, BookingConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/compute/book")
def api_compute_book(
    req: ComputeBookRequest,
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Book a compute quote atomically with Idempotency-Key deduplication."""
    hdr = idempotency_key_header.strip() if isinstance(idempotency_key_header, str) else None
    body_key = req.idempotency_key.strip() if isinstance(req.idempotency_key, str) else None
    if hdr and body_key and hdr != body_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "Idempotency-Key header and body idempotency_key must match "
                "when both are provided"
            ),
        )
    effective_key = hdr or body_key
    if not effective_key:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key header or body idempotency_key is required",
        )

    sched = _get_fleet_scheduler(app)
    try:
        return sched.book_quote(
            quote_id=req.quote_id,
            idempotency_key=effective_key,
            job_name=req.job_name,
        )
    except ValidationInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QuoteExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except (IdempotencyConflictError, BookingConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

if (WEB_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:

    @app.get("/", include_in_schema=False)
    def root_index() -> dict:
        return {
            "service": "CalorGrid API",
            "version": API_VERSION,
            "docs": "/docs",
            "health": "/api/v1/health",
            "web_mounted": False,
        }


def main() -> None:
    import uvicorn

    uvicorn.run("digitalheat.api:app", host="127.0.0.1", port=8000)
