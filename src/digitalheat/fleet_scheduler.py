"""Data-center fleet job scheduler unifying Vaibhav GPU slots and Vilas LLF/BACKFILL."""

from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from digitalheat.cloud_pricing import CloudPricingService
from digitalheat.config import DATA_DIR as CONFIG_DATA_DIR
from digitalheat.config import HEAT_PUMP_REF_COP
from digitalheat.data_loader import DatasetIntegrityError, load_hardware_catalog
from digitalheat.fleet import CANONICAL_SITES, CanonicalSiteSpec, resolve_site_id
from digitalheat.telemetry_log import TelemetryLogStore, get_log_store, log_event

ZURICH_TZ = ZoneInfo("Europe/Zurich")

FORECAST_TRUST = 0.70
ADMISSION_MARGIN = 1.50
SPOT_PRICE_RATIO = 0.50
QUOTE_TTL_SECONDS = 300.0
MAX_STORED_QUOTES = 1000
MAX_STORED_BOOKINGS = 1000
MAX_SIM_HORIZON_H = 192


class FleetSchedulerError(Exception):
    """Base error for FleetJobScheduler operations."""


class ValidationInputError(FleetSchedulerError):
    """Invalid GPU SKU, server SKU, site_id, or idempotency parameter (HTTP 422)."""


class BookingNotFoundError(FleetSchedulerError):
    """Requested quote_id does not exist (HTTP 404)."""


class QuoteExpiredError(FleetSchedulerError):
    """Quote TTL elapsed before booking (HTTP 410)."""


class BookingConflictError(FleetSchedulerError):
    """Slots or thermal headroom changed before booking completed (HTTP 409)."""


class ThermalCapacityConflictError(FleetSchedulerError):
    """Zero-dump thermal headroom cannot absorb immediate On-Demand workload (HTTP 409)."""


class IdempotencyConflictError(FleetSchedulerError):
    """Same Idempotency-Key reused with a different quote_id (HTTP 409)."""


@dataclass
class GpuSlot:
    slot_index: int
    gpu_id: str
    gpu_model_id: str
    gpu_model_name: str
    idle_kw: float
    min_active_kw: float
    typical_kw: float
    max_kw: float
    eta: float
    supply_temp_c: float
    status: Literal["idle", "busy", "offline"] = "idle"
    utilization_pct: float = 0.0
    kw_e: float = 0.0
    kw_th: float = 0.0
    job_id: str | None = None
    job_name: str | None = None
    source: Literal["simulated", "live_pid"] = "simulated"

    def __post_init__(self) -> None:
        if self.kw_e <= 0.0:
            self.kw_e = round(self.idle_kw, 4)
        if self.kw_th <= 0.0:
            self.kw_th = round(self.idle_kw * self.eta, 4)


@dataclass
class ServerInstance:
    server_id: str
    site_id: str
    server_model_id: str
    server_name: str
    gpu_model_id: str | None
    gpu_count: int
    idle_kw: float
    min_active_kw: float
    typical_kw: float
    max_kw: float
    eta: float
    supply_temp_c: float
    status: Literal["running", "standby", "offline"] = "standby"
    gpu_slots: list[GpuSlot] = field(default_factory=list)
    active_job_ids: list[str] = field(default_factory=list)
    # Hour offset -> set of busy slot indices (or {-1} when whole CPU-only server is reserved)
    schedule_bitmap: dict[int, set[int]] = field(default_factory=dict)


@dataclass
class SiteThermalState:
    site_id: str
    indoor_temp_c: float
    target_temp_c: float
    comfort_band_max_c: float
    outdoor_temp_c: float
    heat_demand_kw: float
    tank_soc_kwh: float
    tank_capacity_kwh: float
    target_supply_c: float
    heater_type: Literal["heat_pump", "gas_boiler", "electric_backup"]
    building_ua_kw_per_k: float
    building_cap_kwh_per_k: float
    dump_allowed: bool = False
    offline_until_hour: float | None = None


@dataclass
class ScheduledJob:
    job_id: str
    job_name: str
    tier: Literal["on_demand", "spot", "backfill"]
    unit_mode: Literal["gpu", "server"]
    gpu_or_sku: str
    quantity: int
    workload_type: str
    site_id: str
    server_ids: list[str]
    gpu_slot_refs: list[tuple[str, int]]
    arrival_hour: float
    start_hour: float
    duration_hours: float
    remaining_hours: float
    deadline_hour: float
    interruptible: bool
    kw_e: float
    kw_th: float
    status: Literal["running", "queued", "completed", "migrated", "missed"]
    hold_until_hour: float | None = None
    source: Literal["customer_booking", "backfill_pool", "seed_workload"] = "customer_booking"


@dataclass
class ComputeQuote:
    quote_id: str
    created_at_mono: float
    expires_at_mono: float
    created_at_utc: str
    expires_at_utc: str
    state_version: int
    tier: Literal["on_demand", "spot"]
    unit_mode: Literal["gpu", "server"]
    gpu_or_sku: str
    quantity: int
    duration_hours: float
    workload_type: str
    job_name: str
    assigned_site_id: str
    assigned_site_name: str
    assigned_server_ids: list[str]
    assigned_gpu_slots: list[tuple[str, int]]
    start_offset_h: int
    wait_time_min: int
    wait_time_unknown: bool
    kw_e: float
    kw_th: float
    total_heat_kwh: float
    unit_rate_chf_per_hr: float
    unit_rate_usd_per_hr: float
    total_cost_chf: float
    total_cost_usd: float
    fx_chf_per_usd: float
    cloud_savings_pct: float | None
    cloud_benchmark_provider: str | None
    cloud_benchmark_cost_usd: float | None
    sink_type: str
    preheat_only: bool
    warnings: list[str]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_clock_utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _site_ambient_and_demand(
    spec: CanonicalSiteSpec,
    dt_utc: datetime,
    target_temp_c: float = 21.0,
) -> tuple[float, float, float, bool]:
    """Compute (outdoor_temp_c, space_dhw_kw, process_kw, sink_active_now) at dt_utc."""
    local_dt = dt_utc.astimezone(ZURICH_TZ)
    doy = local_dt.timetuple().tm_yday
    month = local_dt.month
    hour = local_dt.hour

    # Seasonal Basel / Davos temperature envelope
    mean_annual = 1.5 if spec.climate_base == "davos" else 10.5
    amp = 9.5 if spec.climate_base == "davos" else 9.0
    base_temp = mean_annual - amp * math.cos(2.0 * math.pi * (doy - 15) / 365.0)
    diurnal = -2.6 * math.cos(2.0 * math.pi * (hour - 14) / 24.0)
    t_out = round(base_temp + diurnal + spec.temp_offset_c, 2)

    balance_c = target_temp_c - 6.0  # ~15 C heating threshold at 21 C indoor
    space_kw = max(0.0, (balance_c - t_out) * spec.base_heating_ua_kw_per_k)
    dhw_weight = 1.65 if hour in (7, 8, 19, 20) else 0.87
    dhw_kw = (spec.dhw_daily_kwh / 24.0) * dhw_weight

    summer_wave = 0.5 - 0.5 * math.cos(2.0 * math.pi * (doy - 15) / 365.0)
    is_summer_may_sep = 5 <= month <= 9
    proc_kw = spec.process_base_kw
    sink_active_now = month in spec.active_months

    if spec.sink_type == "summer_pool":
        if is_summer_may_sep:
            night_boost = 1.18 if (hour < 9 or hour >= 20) else 0.88
            proc_kw = spec.process_summer_kw * night_boost
            sink_active_now = True
        else:
            proc_kw = 1.8
            sink_active_now = False
    elif spec.sink_type == "geothermal_regen":
        proc_kw = spec.process_base_kw + spec.process_summer_kw * (summer_wave**1.4)
        sink_active_now = True
    elif spec.sink_type in ("indoor_thermal_spa", "biogas_digester", "hospital_24_7"):
        shift = 1.08 if 6 <= hour <= 22 else 0.92
        proc_kw = (
            spec.process_base_kw + (spec.process_summer_kw - spec.process_base_kw) * summer_wave
        ) * shift
        sink_active_now = True
    elif spec.sink_type == "commercial_laundry":
        shift = 1.35 if 4 <= hour <= 18 else 0.52
        proc_kw = (spec.process_base_kw + 0.6 * summer_wave) * shift
    elif spec.sink_type == "cip_preheat":
        is_weekday = local_dt.weekday() < 6
        shift = 1.30 if (is_weekday and 6 <= hour <= 18) else 0.25
        proc_kw = spec.process_base_kw * shift
    elif spec.sink_type == "dairy_pasteurisation":
        shift = 1.28 if (5 <= hour <= 11 or 14 <= hour <= 18) else 0.78
        proc_kw = (
            spec.process_base_kw + (spec.process_summer_kw - spec.process_base_kw) * summer_wave
        ) * shift
    elif spec.sink_type == "greenhouse_rootzone":
        night_factor = 1.32 if (hour < 8 or hour >= 19) else 0.72
        shoulder = 1.0 - 0.35 * max(0.0, summer_wave - 0.65)
        proc_kw = (spec.process_base_kw + 3.2 * shoulder) * night_factor
    elif spec.sink_type == "agricultural_drying":
        harvest = 1.0 if 5 <= month <= 10 else 0.38
        proc_kw = spec.process_base_kw * (1.0 - harvest) + spec.process_summer_kw * harvest
    elif spec.sink_type == "alpine_spa_pool":
        proc_kw = (
            spec.process_base_kw + (spec.process_summer_kw - spec.process_base_kw) * summer_wave
        )
        sink_active_now = (not is_summer_may_sep) or (space_kw > 0.5)

    return t_out, round(space_kw + dhw_kw, 3), round(proc_kw, 3), sink_active_now


class FleetJobScheduler:
    """Thread-safe decentralized fleet scheduler for GPU and server compute jobs."""

    def __init__(
        self,
        *,
        lock: threading.RLock | None = None,
        log_store: TelemetryLogStore | None = None,
        data_dir: Path | None = None,
        pricing_service: CloudPricingService | None = None,
        simulation_clock_utc: str = "2025-11-21T14:00:00Z",
        seed_initial_workloads: bool = True,
    ) -> None:
        self._lock = lock if lock is not None else threading.RLock()
        self._log_store = log_store if log_store is not None else get_log_store()
        self._data_dir = (data_dir if data_dir is not None else CONFIG_DATA_DIR).resolve()
        self._pricing_service = pricing_service

        try:
            self._catalog = load_hardware_catalog(data_dir=self._data_dir, verify=False)
        except DatasetIntegrityError:
            self._catalog = load_hardware_catalog(data_dir=CONFIG_DATA_DIR, verify=False)

        self._server_catalog: dict[str, dict[str, Any]] = {
            s["id"]: s for s in self._catalog.get("server_models", [])
        }
        self._gpu_catalog: dict[str, dict[str, Any]] = {
            g["id"]: g for g in self._catalog.get("gpu_products", [])
        }
        fx_ref = self._catalog.get("fx_reference", {})
        self._fx_chf_per_usd = float(fx_ref.get("chf_per_usd", 0.83922))

        self._sim_dt_utc: datetime = _parse_clock_utc(simulation_clock_utc)
        self._sim_hours_elapsed: float = 0.0
        self._state_version: int = 1

        self._sites: dict[str, SiteThermalState] = {}
        self._servers: dict[str, ServerInstance] = {}
        self._site_servers: dict[str, list[str]] = {sid: [] for sid in CANONICAL_SITES}
        self._jobs: dict[str, ScheduledJob] = {}
        self._quotes: dict[str, ComputeQuote] = {}
        self._idempotency_cache: dict[str, dict[str, Any]] = {}

        self._init_sites_and_inventory()
        if seed_initial_workloads:
            self._seed_realistic_workloads()

    @property
    def state_version(self) -> int:
        with self._lock:
            return self._state_version

    @property
    def simulation_clock_utc(self) -> str:
        with self._lock:
            return self._sim_dt_utc.isoformat().replace("+00:00", "Z")

    def set_pricing_service(self, pricing_service: CloudPricingService) -> None:
        with self._lock:
            self._pricing_service = pricing_service

    def set_simulation_clock(self, timestamp_utc: str) -> None:
        with self._lock:
            self._sim_dt_utc = _parse_clock_utc(timestamp_utc)
            self._refresh_all_site_demands_locked()
            self._state_version += 1

    def _refresh_all_site_demands_locked(self) -> None:
        for sid, site in self._sites.items():
            spec = CANONICAL_SITES[sid]
            t_out, space_dhw_kw, proc_kw, _ = _site_ambient_and_demand(
                spec, self._sim_dt_utc, site.target_temp_c
            )
            site.outdoor_temp_c = t_out
            site.heat_demand_kw = round(space_dhw_kw + proc_kw, 3)

    def _add_server_to_site_locked(
        self,
        site_id: str,
        idx: int,
        *,
        server_model_id: str | None = None,
        gpu_product_id: str | None = None,
    ) -> None:
        if gpu_product_id is not None:
            gpu = self._gpu_catalog[gpu_product_id]
            gpu_count = int(gpu["node_gpu_count"])
            ref_srv_id = gpu.get("reference_server_model_id")
            model_id = (
                ref_srv_id
                if ref_srv_id in self._server_catalog
                else f"{gpu_product_id}_node"
            )
            npow = gpu.get("node_power_kw", {})
            srv_name = gpu.get("reference_system", gpu["name"])
            idle_kw = float(npow.get("idle_kw", gpu["idle_kw_per_gpu"] * gpu_count))
            min_act_kw = float(npow.get("min_active_kw", gpu["min_active_kw_per_gpu"] * gpu_count))
            typ_kw = float(npow.get("typical_kw", gpu["typical_kw_per_gpu"] * gpu_count))
            max_kw = float(npow.get("max_kw", gpu["max_kw_per_gpu"] * gpu_count))
            eta = float(gpu["eta"])
            sup_c = float(gpu["supply_temp_c"])
            slots = [
                GpuSlot(
                    slot_index=s_i,
                    gpu_id=f"{site_id}-srv-{idx:02d}-gpu-{s_i}",
                    gpu_model_id=gpu_product_id,
                    gpu_model_name=gpu["name"],
                    idle_kw=float(gpu["idle_kw_per_gpu"]),
                    min_active_kw=float(gpu["min_active_kw_per_gpu"]),
                    typical_kw=float(gpu["typical_kw_per_gpu"]),
                    max_kw=float(gpu["max_kw_per_gpu"]),
                    eta=eta,
                    supply_temp_c=sup_c,
                )
                for s_i in range(gpu_count)
            ]
        else:
            assert server_model_id is not None
            srv = self._server_catalog[server_model_id]
            model_id = server_model_id
            srv_name = srv["name"]
            gpu_product_id = None
            gpu_count = 0
            idle_kw = float(srv["idle_kw"])
            min_act_kw = float(srv["min_active_kw"])
            typ_kw = float(srv["typical_kw"])
            max_kw = float(srv["max_kw"])
            eta = float(srv["eta"])
            sup_c = float(srv["supply_temp_c"])
            slots = []

        server_id = f"{site_id}-srv-{idx:02d}"
        inst = ServerInstance(
            server_id=server_id,
            site_id=site_id,
            server_model_id=model_id,
            server_name=srv_name,
            gpu_model_id=gpu_product_id,
            gpu_count=gpu_count,
            idle_kw=idle_kw,
            min_active_kw=min_act_kw,
            typical_kw=typ_kw,
            max_kw=max_kw,
            eta=eta,
            supply_temp_c=sup_c,
            gpu_slots=slots,
        )
        self._servers[server_id] = inst
        self._site_servers[site_id].append(server_id)

    def _init_sites_and_inventory(self) -> None:
        inventory_plan: dict[str, list[tuple[str | None, str | None]]] = {
            "sole_uno": [
                (None, "b200_sxm_180gb"),
                (None, "b200_sxm_180gb"),
                (None, "h100_nvl_94gb"),
                ("ocp_winterfell_3kw", None),
            ],
            "ara_basel_biogas": [
                (None, "b200_sxm_180gb"),
                (None, "b200_sxm_180gb"),
                (None, "h100_sxm_80gb"),
                (None, "a100_sxm_80gb"),
            ],
            "basel_st_jakob": [
                (None, "b200_sxm_180gb"),
                (None, "h100_nvl_94gb"),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
            ],
            "stgallen_hospital": [
                (None, "a100_sxm_80gb"),
                ("ocp_winterfell_3kw", None),
                ("ocp_winterfell_3kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
            ],
            "thurgau_greenhouse": [
                (None, "b200_sxm_180gb"),
                (None, "h100_sxm_80gb"),
            ],
            "basel_klingental": [
                (None, "rtx_pro_4500_blackwell"),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
            ],
            "basel_erdsonde": [
                (None, "rtx_pro_4500_blackwell"),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("edge_micro_250w", None),
            ],
            "riehen_villa": [
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("edge_micro_250w", None),
                ("edge_micro_250w", None),
            ],
            "zurich_oerlikon": [
                (None, "rtx_pro_4500_blackwell"),
                (None, "rtx_pro_4500_blackwell"),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
            ],
            "bern_matte": [
                ("hydronode_1kw", None),
                (None, "rtx_pro_4500_blackwell"),
                ("edge_micro_250w", None),
            ],
            "geneva_carouge": [
                ("hydronode_1kw", None),
                (None, "rtx_pro_4500_blackwell"),
                ("edge_micro_250w", None),
            ],
            "lugano_lakeside": [
                ("hydronode_1kw", None),
                (None, "rtx_pro_4500_blackwell"),
                ("edge_micro_250w", None),
            ],
            "emmental_dairy": [
                ("ocp_winterfell_3kw", None),
                (None, "a100_sxm_80gb"),
                ("hydronode_1kw", None),
            ],
            "lucerne_bakery": [
                ("ocp_winterfell_3kw", None),
                (None, "a100_sxm_80gb"),
                ("hydronode_1kw", None),
            ],
            "valais_dryer": [
                ("ocp_winterfell_3kw", None),
                (None, "a100_sxm_80gb"),
                ("hydronode_1kw", None),
            ],
            "hotel_laundry_dhw": [
                ("ocp_winterfell_3kw", None),
                (None, "a100_sxm_80gb"),
                ("hydronode_1kw", None),
            ],
            "brewery_dairy_cip": [
                ("ocp_winterfell_3kw", None),
                (None, "a100_sxm_80gb"),
                ("hydronode_1kw", None),
            ],
            "davos_alpine": [
                (None, "h100_nvl_94gb"),
                ("ocp_winterfell_3kw", None),
                ("ocp_winterfell_3kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
                ("hydronode_1kw", None),
            ],
        }

        for sid, spec in CANONICAL_SITES.items():
            t_out, space_dhw_kw, proc_kw, _ = _site_ambient_and_demand(
                spec, self._sim_dt_utc, 21.0
            )
            initial_indoor = 20.4 if spec.category == "residential" else 20.8
            self._sites[sid] = SiteThermalState(
                site_id=sid,
                indoor_temp_c=initial_indoor,
                target_temp_c=21.0,
                comfort_band_max_c=22.5,
                outdoor_temp_c=t_out,
                heat_demand_kw=round(space_dhw_kw + proc_kw, 3),
                tank_soc_kwh=round(0.25 * spec.tank_capacity_kwh, 2),
                tank_capacity_kwh=spec.tank_capacity_kwh,
                target_supply_c=float(spec.target_supply_c),
                heater_type=spec.heater_type,
                building_ua_kw_per_k=max(0.15, spec.base_heating_ua_kw_per_k),
                building_cap_kwh_per_k=max(2.5, spec.base_heating_ua_kw_per_k * 12.0),
                dump_allowed=False,
            )
            for idx, (srv_id, gpu_id) in enumerate(inventory_plan[sid], start=1):
                self._add_server_to_site_locked(
                    sid, idx, server_model_id=srv_id, gpu_product_id=gpu_id
                )

    def _seed_realistic_workloads(self) -> None:
        seeds = [
            ("on_demand", "gpu", "b200_sxm_180gb", 2, 4.0, "sole_uno", "seed-llm-b200-sole-uno"),
            ("on_demand", "gpu", "h100_sxm_80gb", 2, 6.0, "ara_basel_biogas", "seed-biogas-h100"),
            ("spot", "gpu", "rtx_pro_4500_blackwell", 2, 3.0, "basel_klingental", "seed-basel-ocr"),
            ("spot", "server", "hydronode_1kw", 2, 4.0, "stgallen_hospital", "seed-hospital-dhw"),
        ]
        for tier, mode, sku, qty, dur, site_id, jname in seeds:
            try:
                q = self.create_quote(
                    tier=tier,  # type: ignore[arg-type]
                    unit_mode=mode,  # type: ignore[arg-type]
                    gpu_or_sku=sku,
                    quantity=qty,
                    duration_hours=dur,
                    preferred_site_id=site_id,
                    job_name=jname,
                )
                self.book_quote(
                    quote_id=q["quote_id"],
                    idempotency_key=f"boot-{jname}",
                    job_name=jname,
                )
            except FleetSchedulerError:
                continue

    def _site_is_offline_locked(self, site: SiteThermalState) -> bool:
        return (
            site.offline_until_hour is not None
            and self._sim_hours_elapsed < site.offline_until_hour
        )

    def _site_committed_heat_kw_locked(self, site_id: str, hour_offset: int = 0) -> float:
        total = 0.0
        target_h = self._sim_hours_elapsed + hour_offset
        for job in self._jobs.values():
            if job.site_id != site_id:
                continue
            if job.status == "running" and hour_offset == 0:
                total += job.kw_th
            elif job.status in ("running", "queued"):
                end_h = job.start_hour + job.duration_hours
                if job.start_hour <= target_h < end_h:
                    total += job.kw_th
        return total

    def _instant_headroom_kw_locked(self, site_id: str) -> float:
        site = self._sites[site_id]
        if self._site_is_offline_locked(site):
            return 0.0
        spec = CANONICAL_SITES[site_id]
        _, space_dhw_kw, proc_kw, _ = _site_ambient_and_demand(
            spec, self._sim_dt_utc, site.target_temp_c
        )
        effective_demand = site.heat_demand_kw
        if site.indoor_temp_c >= site.comfort_band_max_c and proc_kw <= 0.0:
            # Building already warm: space heating headroom drops to zero; DHW only remains
            effective_demand = min(site.heat_demand_kw, spec.dhw_daily_kwh / 24.0)
            if space_dhw_kw > 0.0 and proc_kw <= 0.0:
                effective_demand = min(effective_demand, spec.dhw_daily_kwh / 24.0)
        tank_free = max(0.0, site.tank_capacity_kwh - site.tank_soc_kwh)
        committed_now = self._site_committed_heat_kw_locked(site_id, hour_offset=0)
        return max(0.0, effective_demand + tank_free - committed_now)

    def _forecast_headroom_kwh_locked(
        self,
        site_id: str,
        start_offset_h: int,
        duration_hours: float,
    ) -> float:
        site = self._sites[site_id]
        if self._site_is_offline_locked(site):
            return 0.0
        spec = CANONICAL_SITES[site_id]
        n_steps = max(1, int(math.ceil(duration_hours)))
        tank_free = max(0.0, site.tank_capacity_kwh - site.tank_soc_kwh)
        total_budget = tank_free
        for step in range(n_steps):
            h_off = start_offset_h + step
            dt_h = self._sim_dt_utc + timedelta(hours=h_off)
            _, space_dhw_kw, proc_kw, _ = _site_ambient_and_demand(
                spec, dt_h, site.target_temp_c
            )
            dem_h = space_dhw_kw + proc_kw
            if h_off == 0:
                dem_h = site.heat_demand_kw
                if site.indoor_temp_c >= site.comfort_band_max_c and proc_kw <= 0.0:
                    dem_h = spec.dhw_daily_kwh / 24.0
            trust = 1.0 if h_off == 0 else FORECAST_TRUST
            frac = min(1.0, duration_hours - step) if step == n_steps - 1 else 1.0
            committed = self._site_committed_heat_kw_locked(site_id, hour_offset=h_off)
            total_budget += max(0.0, dem_h * trust - committed) * frac
        return max(0.0, total_budget)

    def _available_slots_on_server_locked(
        self,
        srv: ServerInstance,
        unit_mode: Literal["gpu", "server"],
        gpu_or_sku: str,
        start_offset_h: int,
        duration_hours: float,
    ) -> list[int]:
        if srv.status == "offline":
            return []
        n_steps = max(1, int(math.ceil(duration_hours)))
        hours_range = range(start_offset_h, start_offset_h + n_steps)

        if unit_mode == "gpu":
            if srv.gpu_model_id != gpu_or_sku or not srv.gpu_slots:
                return []
            free_indices: list[int] = []
            for slot in srv.gpu_slots:
                if slot.status == "offline":
                    continue
                is_free = True
                for h_off in hours_range:
                    busy_set = srv.schedule_bitmap.get(h_off)
                    if busy_set and (-1 in busy_set or slot.slot_index in busy_set):
                        is_free = False
                        break
                if is_free:
                    free_indices.append(slot.slot_index)
            return free_indices

        # unit_mode == "server"
        if srv.server_model_id != gpu_or_sku:
            return []
        for h_off in hours_range:
            if srv.schedule_bitmap.get(h_off):
                return []
        if srv.gpu_slots:
            return [slot.slot_index for slot in srv.gpu_slots]
        return [-1]

    def _find_candidate_placement_locked(
        self,
        *,
        tier: Literal["on_demand", "spot"],
        unit_mode: Literal["gpu", "server"],
        gpu_or_sku: str,
        quantity: int,
        duration_hours: float,
        preferred_site_id: str | None,
    ) -> dict[str, Any] | None:
        if preferred_site_id is not None:
            candidate_site_ids = [resolve_site_id(preferred_site_id)]
        else:
            candidate_site_ids = list(CANONICAL_SITES.keys())

        if unit_mode == "gpu":
            gpu_spec = self._gpu_catalog[gpu_or_sku]
            kw_e = round(float(gpu_spec["typical_kw_per_gpu"]) * quantity, 4)
            eta = float(gpu_spec["eta"])
            supply_c = float(gpu_spec["supply_temp_c"])
        else:
            srv_spec = self._server_catalog[gpu_or_sku]
            kw_e = round(float(srv_spec["typical_kw"]) * quantity, 4)
            eta = float(srv_spec["eta"])
            supply_c = float(srv_spec["supply_temp_c"])

        kw_th = round(kw_e * eta, 4)
        total_heat_kwh = round(kw_th * duration_hours, 4)

        max_wait_h = 0 if tier == "on_demand" else 24
        best: dict[str, Any] | None = None

        for wait_h in range(max_wait_h + 1):
            for sid in candidate_site_ids:
                site = self._sites[sid]
                spec = CANONICAL_SITES[sid]
                if self._site_is_offline_locked(site):
                    continue

                # Thermal admission check
                if tier == "on_demand":
                    instant_room = self._instant_headroom_kw_locked(sid)
                    horizon_room = self._forecast_headroom_kwh_locked(sid, 0, duration_hours)
                    if not site.dump_allowed:
                        if kw_th > instant_room + 1e-6 or total_heat_kwh > horizon_room + 1e-6:
                            continue
                else:
                    horizon_room = self._forecast_headroom_kwh_locked(sid, wait_h, duration_hours)
                    margin = ADMISSION_MARGIN if wait_h == 0 else 1.15
                    if not site.dump_allowed and total_heat_kwh * margin > horizon_room + 1e-6:
                        continue

                # Slot matching across site's servers
                chosen_server_ids: list[str] = []
                chosen_slots: list[tuple[str, int]] = []
                remaining_qty = quantity

                for srv_id in self._site_servers[sid]:
                    if remaining_qty <= 0:
                        break
                    srv = self._servers[srv_id]
                    free = self._available_slots_on_server_locked(
                        srv, unit_mode, gpu_or_sku, wait_h, duration_hours
                    )
                    if not free:
                        continue
                    if unit_mode == "gpu":
                        take = free[:remaining_qty]
                        chosen_server_ids.append(srv_id)
                        for s_idx in take:
                            chosen_slots.append((srv_id, s_idx))
                        remaining_qty -= len(take)
                    else:
                        chosen_server_ids.append(srv_id)
                        for s_idx in free:
                            chosen_slots.append((srv_id, s_idx))
                        remaining_qty -= 1

                if remaining_qty > 0:
                    continue

                # Score placement (Vaibhav saving_kwh + Vilas headroom + sink preference)
                displaced_kwh = min(total_heat_kwh, site.heat_demand_kw * duration_hours)
                saving_kwh = displaced_kwh / HEAT_PUMP_REF_COP
                norm_saving = min(1.0, saving_kwh / max(total_heat_kwh, 0.1))
                spare_ratio = min(
                    1.0,
                    max(0.0, horizon_room - total_heat_kwh) / max(horizon_room, 1.0),
                )
                norm_tariff = spec.tariff_chf_per_kwh / 0.35

                _, _, _, sink_active_now = _site_ambient_and_demand(
                    spec, self._sim_dt_utc + timedelta(hours=wait_h), site.target_temp_c
                )
                sink_bonus = (
                    0.25
                    if (spec.is_year_round_sink or (spec.is_summer_sink and sink_active_now))
                    else 0.0
                )
                cold_bonus = 0.15 if site.indoor_temp_c < site.target_temp_c else 0.0

                if tier == "on_demand":
                    score = (
                        1.0 * (1.0 if spec.is_year_round_sink else 0.3)
                        + 0.4 * sink_bonus
                        + 0.3 * spare_ratio
                        - 0.1 * norm_tariff
                    )
                else:
                    score = (
                        0.60 * norm_saving
                        + 0.30 * spare_ratio
                        + cold_bonus
                        + sink_bonus
                        - 0.10 * norm_tariff
                        - 0.05 * wait_h
                    )

                if best is None or score > best["score"]:
                    best = {
                        "score": score,
                        "site_id": sid,
                        "site_name": spec.name,
                        "sink_type": spec.sink_type,
                        "target_supply_c": spec.target_supply_c,
                        "server_supply_c": supply_c,
                        "server_ids": chosen_server_ids,
                        "gpu_slots": chosen_slots,
                        "start_offset_h": wait_h,
                        "kw_e": kw_e,
                        "kw_th": kw_th,
                        "total_heat_kwh": total_heat_kwh,
                        "sink_active_now": sink_active_now,
                    }
            if best is not None and tier == "spot" and wait_h == 0:
                break
        return best

    def _lookup_rates_locked(
        self,
        unit_mode: Literal["gpu", "server"],
        gpu_or_sku: str,
        tier: Literal["on_demand", "spot"],
    ) -> dict[str, Any]:
        if self._pricing_service is not None:
            return self._pricing_service.get_unit_rates(unit_mode, gpu_or_sku, tier)

        # Fallback directly from catalog if no CloudPricingService was attached
        gpu_rates = {
            "b200_sxm_180gb": 3.95,
            "h100_sxm_80gb": 2.65,
            "h100_nvl_94gb": 2.25,
            "a100_sxm_80gb": 1.45,
            "rtx_pro_4500_blackwell": 0.48,
        }
        srv_rates = {
            "hydronode_1kw": 0.56,
            "ocp_winterfell_3kw": 1.35,
            "b200_ai_10kw": 31.60,
            "edge_micro_250w": 0.18,
        }
        base_chf = gpu_rates[gpu_or_sku] if unit_mode == "gpu" else srv_rates[gpu_or_sku]
        rate_chf = base_chf if tier == "on_demand" else round(base_chf * SPOT_PRICE_RATIO, 4)
        rate_usd = round(rate_chf / self._fx_chf_per_usd, 4)
        return {
            "unit_rate_chf_per_hr": rate_chf,
            "unit_rate_usd_per_hr": rate_usd,
            "fx_chf_per_usd": self._fx_chf_per_usd,
            "cloud_savings_pct": None,
            "cloud_benchmark_usd_per_hr": None,
            "cloud_benchmark_provider": None,
        }

    def create_quote(
        self,
        *,
        tier: Literal["on_demand", "spot"] = "on_demand",
        unit_mode: Literal["gpu", "server"] = "gpu",
        gpu_or_sku: str,
        quantity: int = 1,
        duration_hours: float = 2.0,
        preferred_site_id: str | None = None,
        workload_type: str = "llm_inference",
        job_name: str | None = None,
    ) -> dict[str, Any]:
        """Quote GPU or server capacity with seasonal/thermal headroom enforcement."""
        if unit_mode == "gpu" and gpu_or_sku not in self._gpu_catalog:
            raise ValidationInputError(f"Unknown gpu_or_sku '{gpu_or_sku}' for unit_mode='gpu'")
        if unit_mode == "server" and gpu_or_sku not in self._server_catalog:
            raise ValidationInputError(
                f"Unknown gpu_or_sku '{gpu_or_sku}' for unit_mode='server'"
            )
        if quantity < 1 or quantity > 64:
            raise ValidationInputError("quantity must be in [1, 64]")
        if not math.isfinite(duration_hours) or duration_hours <= 0.0 or duration_hours > 168.0:
            raise ValidationInputError("duration_hours must be finite in (0, 168]")

        resolved_pref: str | None = None
        if preferred_site_id is not None:
            try:
                resolved_pref = resolve_site_id(preferred_site_id)
            except ValueError as exc:
                raise ValidationInputError(str(exc)) from exc

        with self._lock:
            placement = self._find_candidate_placement_locked(
                tier=tier,
                unit_mode=unit_mode,
                gpu_or_sku=gpu_or_sku,
                quantity=quantity,
                duration_hours=duration_hours,
                preferred_site_id=resolved_pref,
            )

            if placement is None:
                if tier == "on_demand":
                    spot_alt = self._find_candidate_placement_locked(
                        tier="spot",
                        unit_mode=unit_mode,
                        gpu_or_sku=gpu_or_sku,
                        quantity=quantity,
                        duration_hours=duration_hours,
                        preferred_site_id=resolved_pref,
                    )
                    hint = (
                        f"Spot fallback available at {spot_alt['site_id']} in "
                        f"{spot_alt['start_offset_h'] * 60} min."
                        if spot_alt is not None
                        else "Reduce quantity or choose Spot tier."
                    )
                    log_event(
                        "customer_booking",
                        "compute_quote_rejected_thermal",
                        f"Rejected On-Demand {quantity}x {gpu_or_sku}: thermal headroom saturated",
                        level="warning",
                        source_module="fleet_scheduler",
                        site_id=resolved_pref,
                        metrics={"tier": tier, "gpu_or_sku": gpu_or_sku, "quantity": quantity},
                        store=self._log_store,
                    )
                    raise ThermalCapacityConflictError(
                        f"thermal_headroom_saturated: zero-dump On-Demand cannot absorb "
                        f"{quantity}x {gpu_or_sku} immediately. {hint}"
                    )
                raise BookingConflictError(
                    f"No feasible Spot window within 24h for {quantity}x {gpu_or_sku}"
                )

            rates = self._lookup_rates_locked(unit_mode, gpu_or_sku, tier)
            unit_chf = float(rates["unit_rate_chf_per_hr"])
            unit_usd = float(rates["unit_rate_usd_per_hr"])
            total_cost_chf = round(unit_chf * quantity * duration_hours, 2)
            total_cost_usd = round(unit_usd * quantity * duration_hours, 2)
            bench_usd = rates.get("cloud_benchmark_usd_per_hr")
            bench_total_usd = (
                round(float(bench_usd) * quantity * duration_hours, 2)
                if bench_usd is not None
                else None
            )

            warnings: list[str] = []
            preheat_only = False
            if placement["server_supply_c"] < placement["target_supply_c"]:
                preheat_only = True
                cap_pct = round(
                    100.0
                    * max(0.0, (placement["server_supply_c"] - 10.0))
                    / max(1.0, (placement["target_supply_c"] - 10.0)),
                    1,
                )
                warnings.append(
                    f"Supply {placement['server_supply_c']:.0f} C < target "
                    f"{placement['target_supply_c']} C: preheat-only (~{cap_pct}% share)."
                )
            if placement["site_id"] == "basel_st_jakob" and not placement["sink_active_now"]:
                warnings.append(
                    "Gartenbad St. Jakob outdoor pool is off-season (Oct-Apr); "
                    "absorbs indoor changing-room base only."
                )
            zurich_month = self._sim_dt_utc.astimezone(ZURICH_TZ).month
            if resolved_pref == "davos_alpine" and zurich_month in (5, 6, 7, 8, 9):
                warnings.append(
                    "Davos summer window limited to 4.8-6.8 kW indoor spa baseload "
                    "(snowmaking is a cooling load)."
                )

            now_mono = time.monotonic()
            now_utc = _utc_now_iso()
            expires_utc = (
                (datetime.now(timezone.utc) + timedelta(seconds=QUOTE_TTL_SECONDS))
                .isoformat()
                .replace("+00:00", "Z")
            )
            qid = uuid.uuid4().hex
            effective_name = (job_name or f"{tier}-{gpu_or_sku}-{qid[:6]}").strip()[:80]
            wait_min = int(placement["start_offset_h"] * 60)

            quote_obj = ComputeQuote(
                quote_id=qid,
                created_at_mono=now_mono,
                expires_at_mono=now_mono + QUOTE_TTL_SECONDS,
                created_at_utc=now_utc,
                expires_at_utc=expires_utc,
                state_version=self._state_version,
                tier=tier,
                unit_mode=unit_mode,
                gpu_or_sku=gpu_or_sku,
                quantity=quantity,
                duration_hours=round(float(duration_hours), 2),
                workload_type=workload_type,
                job_name=effective_name,
                assigned_site_id=placement["site_id"],
                assigned_site_name=placement["site_name"],
                assigned_server_ids=list(placement["server_ids"]),
                assigned_gpu_slots=list(placement["gpu_slots"]),
                start_offset_h=placement["start_offset_h"],
                wait_time_min=wait_min,
                wait_time_unknown=False,
                kw_e=placement["kw_e"],
                kw_th=placement["kw_th"],
                total_heat_kwh=placement["total_heat_kwh"],
                unit_rate_chf_per_hr=unit_chf,
                unit_rate_usd_per_hr=unit_usd,
                total_cost_chf=total_cost_chf,
                total_cost_usd=total_cost_usd,
                fx_chf_per_usd=float(rates["fx_chf_per_usd"]),
                cloud_savings_pct=rates.get("cloud_savings_pct"),
                cloud_benchmark_provider=rates.get("cloud_benchmark_provider"),
                cloud_benchmark_cost_usd=bench_total_usd,
                sink_type=placement["sink_type"],
                preheat_only=preheat_only,
                warnings=warnings,
            )

            # Evict expired or oldest quotes if over cap
            if len(self._quotes) >= MAX_STORED_QUOTES:
                expired_keys = [
                    k for k, v in self._quotes.items() if now_mono > v.expires_at_mono
                ]
                for k in expired_keys:
                    self._quotes.pop(k, None)
                while len(self._quotes) >= MAX_STORED_QUOTES:
                    oldest_k = next(iter(self._quotes))
                    self._quotes.pop(oldest_k, None)

            self._quotes[qid] = quote_obj

            log_event(
                "customer_booking",
                "compute_quote_created",
                (
                    f"Quoted {tier} {quantity}x {gpu_or_sku} ({duration_hours}h) -> "
                    f"{placement['site_id']} (CHF {total_cost_chf})"
                ),
                source_module="fleet_scheduler",
                site_id=placement["site_id"],
                metrics={
                    "quote_id": qid,
                    "tier": tier,
                    "unit_mode": unit_mode,
                    "gpu_or_sku": gpu_or_sku,
                    "quantity": quantity,
                    "duration_hours": duration_hours,
                    "wait_time_min": wait_min,
                    "total_cost_chf": total_cost_chf,
                },
                store=self._log_store,
            )
            return self._quote_to_dict(quote_obj)

    def _quote_to_dict(self, q: ComputeQuote) -> dict[str, Any]:
        return {
            "quote_id": q.quote_id,
            "created_at_utc": q.created_at_utc,
            "expires_at": q.expires_at_utc,
            "expires_at_utc": q.expires_at_utc,
            "state_version": q.state_version,
            "tier": q.tier,
            "unit_mode": q.unit_mode,
            "gpu_or_sku": q.gpu_or_sku,
            "quantity": q.quantity,
            "duration_hours": q.duration_hours,
            "workload_type": q.workload_type,
            "job_name": q.job_name,
            "assigned_site_id": q.assigned_site_id,
            "assigned_site_name": q.assigned_site_name,
            "assigned_server_ids": list(q.assigned_server_ids),
            "assigned_gpu_slots": [
                {"server_id": sid, "slot_index": idx} for sid, idx in q.assigned_gpu_slots
            ],
            "wait_time_min": q.wait_time_min,
            "wait_time_unknown": q.wait_time_unknown,
            "kw_e": q.kw_e,
            "kw_th": q.kw_th,
            "thermal_output_kw": q.kw_th,
            "total_heat_kwh": q.total_heat_kwh,
            "unit_rate_chf_per_hr": q.unit_rate_chf_per_hr,
            "unit_rate_usd_per_hr": q.unit_rate_usd_per_hr,
            "total_cost_chf": q.total_cost_chf,
            "total_cost_usd": q.total_cost_usd,
            "fx_chf_per_usd": q.fx_chf_per_usd,
            "cloud_savings_pct": q.cloud_savings_pct,
            "cloud_benchmark_provider": q.cloud_benchmark_provider,
            "cloud_benchmark_cost_usd": q.cloud_benchmark_cost_usd,
            "sink_type": q.sink_type,
            "preheat_only": q.preheat_only,
            "warnings": list(q.warnings),
        }

    def _reserve_job_slots_locked(self, job: ScheduledJob) -> None:
        start_off = max(0, int(round(job.start_hour - self._sim_hours_elapsed)))
        n_steps = max(1, int(math.ceil(job.remaining_hours)))
        for h_off in range(start_off, start_off + n_steps):
            for srv_id, s_idx in job.gpu_slot_refs:
                srv = self._servers[srv_id]
                srv.schedule_bitmap.setdefault(h_off, set()).add(s_idx)

        if job.status == "running":
            for srv_id, s_idx in job.gpu_slot_refs:
                srv = self._servers[srv_id]
                if job.job_id not in srv.active_job_ids:
                    srv.active_job_ids.append(job.job_id)
                srv.status = "running"
                if s_idx >= 0 and s_idx < len(srv.gpu_slots):
                    slot = srv.gpu_slots[s_idx]
                    slot.status = "busy"
                    slot.utilization_pct = 92.0 if job.tier == "on_demand" else 84.0
                    slot.kw_e = round(slot.typical_kw, 4)
                    slot.kw_th = round(slot.typical_kw * slot.eta, 4)
                    slot.job_id = job.job_id
                    slot.job_name = job.job_name

    def _release_job_slots_locked(self, job: ScheduledJob) -> None:
        for srv_id, s_idx in job.gpu_slot_refs:
            srv = self._servers.get(srv_id)
            if srv is None:
                continue
            for busy_set in srv.schedule_bitmap.values():
                busy_set.discard(s_idx)
            if job.job_id in srv.active_job_ids:
                srv.active_job_ids.remove(job.job_id)
            if s_idx >= 0 and s_idx < len(srv.gpu_slots):
                slot = srv.gpu_slots[s_idx]
                if slot.job_id == job.job_id:
                    slot.status = "idle"
                    slot.utilization_pct = 0.0
                    slot.kw_e = round(slot.idle_kw, 4)
                    slot.kw_th = round(slot.idle_kw * slot.eta, 4)
                    slot.job_id = None
                    slot.job_name = None
            if not srv.active_job_ids and srv.status != "offline":
                srv.status = "standby"

    def book_quote(
        self,
        *,
        quote_id: str,
        idempotency_key: str,
        job_name: str | None = None,
    ) -> dict[str, Any]:
        """Atomically book a valid quote with Idempotency-Key deduplication."""
        clean_key = (idempotency_key or "").strip()
        if not clean_key:
            raise ValidationInputError("Idempotency-Key is required and must be non-empty")

        with self._lock:
            cached = self._idempotency_cache.get(clean_key)
            if cached is not None:
                if cached["quote_id"] != quote_id:
                    raise IdempotencyConflictError(
                        f"Idempotency-Key '{clean_key}' already bound to quote_id "
                        f"'{cached['quote_id']}'"
                    )
                replay = copy.deepcopy(cached)
                replay["idempotent_replay"] = True
                return replay

            quote = self._quotes.get(quote_id)
            if quote is None:
                raise BookingNotFoundError(f"Unknown quote_id '{quote_id}'")

            now_mono = time.monotonic()
            if now_mono > quote.expires_at_mono:
                raise QuoteExpiredError(f"Quote '{quote_id}' expired at {quote.expires_at_utc}")

            # Re-validate live slots & thermal headroom under lock (even if state_version moved)
            sid = quote.assigned_site_id
            site = self._sites[sid]
            if self._site_is_offline_locked(site):
                raise BookingConflictError(f"Assigned site '{sid}' is currently offline")

            n_steps = max(1, int(math.ceil(quote.duration_hours)))
            for srv_id, s_idx in quote.assigned_gpu_slots:
                srv = self._servers[srv_id]
                if srv.status == "offline":
                    raise BookingConflictError(f"Assigned server '{srv_id}' went offline")
                for h_off in range(quote.start_offset_h, quote.start_offset_h + n_steps):
                    busy = srv.schedule_bitmap.get(h_off, set())
                    if -1 in busy or s_idx in busy:
                        log_event(
                            "customer_booking",
                            "booking_conflict_preempted",
                            f"Quote {quote_id} preempted on {srv_id} slot {s_idx}",
                            level="warning",
                            source_module="fleet_scheduler",
                            site_id=sid,
                            server_id=srv_id,
                            metrics={"quote_id": quote_id, "state_version": self._state_version},
                            store=self._log_store,
                        )
                        raise BookingConflictError(
                            f"Reserved slot ({srv_id}, {s_idx}) is no longer free"
                        )

            if not site.dump_allowed:
                if quote.tier == "on_demand":
                    if (
                        quote.kw_th > self._instant_headroom_kw_locked(sid) + 1e-6
                        or quote.total_heat_kwh
                        > self._forecast_headroom_kwh_locked(sid, 0, quote.duration_hours) + 1e-6
                    ):
                        raise BookingConflictError(
                            f"Thermal headroom at '{sid}' saturated since quote creation"
                        )
                else:
                    horizon_room = self._forecast_headroom_kwh_locked(
                        sid, quote.start_offset_h, quote.duration_hours
                    )
                    if quote.total_heat_kwh > horizon_room + 1e-6:
                        raise BookingConflictError(
                            f"Forecast thermal headroom at '{sid}' saturated since quote creation"
                        )

            job_id = f"job-{uuid.uuid4().hex[:10]}"
            booking_id = f"bkg-{uuid.uuid4().hex[:10]}"
            effective_name = (job_name or quote.job_name).strip()[:80]
            status_val: Literal["running", "queued"] = (
                "running" if quote.wait_time_min == 0 else "queued"
            )
            job = ScheduledJob(
                job_id=job_id,
                job_name=effective_name,
                tier=quote.tier,
                unit_mode=quote.unit_mode,
                gpu_or_sku=quote.gpu_or_sku,
                quantity=quote.quantity,
                workload_type=quote.workload_type,
                site_id=sid,
                server_ids=list(quote.assigned_server_ids),
                gpu_slot_refs=list(quote.assigned_gpu_slots),
                arrival_hour=self._sim_hours_elapsed,
                start_hour=self._sim_hours_elapsed + quote.start_offset_h,
                duration_hours=quote.duration_hours,
                remaining_hours=quote.duration_hours,
                deadline_hour=(
                    self._sim_hours_elapsed + quote.start_offset_h + quote.duration_hours + 6.0
                ),
                interruptible=(quote.tier == "spot"),
                kw_e=quote.kw_e,
                kw_th=quote.kw_th,
                status=status_val,
                source="customer_booking",
            )
            self._jobs[job_id] = job
            self._reserve_job_slots_locked(job)
            self._state_version += 1

            log_event(
                "customer_booking",
                "booking_confirmed",
                (
                    f"Booked {quote.tier} job '{effective_name}' ({quote.quantity}x "
                    f"{quote.gpu_or_sku}) at {sid}"
                ),
                source_module="fleet_scheduler",
                site_id=sid,
                server_id=quote.assigned_server_ids[0] if quote.assigned_server_ids else None,
                job_id=job_id,
                metrics={
                    "booking_id": booking_id,
                    "quote_id": quote_id,
                    "idempotency_key": clean_key,
                    "total_cost_chf": quote.total_cost_chf,
                    "state_version": self._state_version,
                },
                store=self._log_store,
            )
            log_event(
                "scheduler_dispatch",
                "job_dispatched_to_slots",
                (
                    f"Dispatched {job_id} ({status_val}) to "
                    f"{len(quote.assigned_gpu_slots)} slots on {sid}"
                ),
                source_module="fleet_scheduler",
                site_id=sid,
                server_id=quote.assigned_server_ids[0] if quote.assigned_server_ids else None,
                job_id=job_id,
                metrics={
                    "tier": quote.tier,
                    "kw_e": quote.kw_e,
                    "kw_th": quote.kw_th,
                    "wait_time_min": quote.wait_time_min,
                },
                store=self._log_store,
            )

            tel = self.get_fleet_telemetry()
            receipt = {
                "booking_id": booking_id,
                "quote_id": quote_id,
                "job_id": job_id,
                "idempotency_key": clean_key,
                "idempotent_replay": False,
                "status": status_val,
                "state_version": self._state_version,
                "tier": quote.tier,
                "unit_mode": quote.unit_mode,
                "gpu_or_sku": quote.gpu_or_sku,
                "quantity": quote.quantity,
                "duration_hours": quote.duration_hours,
                "assigned_site_id": sid,
                "assigned_site_name": quote.assigned_site_name,
                "assigned_server_ids": list(quote.assigned_server_ids),
                "assigned_gpu_slots": [
                    {"server_id": s, "slot_index": i} for s, i in quote.assigned_gpu_slots
                ],
                "wait_time_min": quote.wait_time_min,
                "thermal_output_kw": quote.kw_th,
                "total_heat_kwh": quote.total_heat_kwh,
                "total_cost_chf": quote.total_cost_chf,
                "total_cost_usd": quote.total_cost_usd,
                "quote": self._quote_to_dict(quote),
                "fleet_summary": copy.deepcopy(tel["summary"]),
                "telemetry_snapshot": tel,
            }

            if len(self._idempotency_cache) >= MAX_STORED_BOOKINGS:
                oldest_k = next(iter(self._idempotency_cache))
                self._idempotency_cache.pop(oldest_k, None)
            self._idempotency_cache[clean_key] = copy.deepcopy(receipt)
            return receipt

    def _proactive_migrate_locked(self) -> list[dict[str, Any]]:
        """Relocate interruptible jobs when thermal headroom shrinks."""
        migrations: list[dict[str, Any]] = []
        # Sort candidate interruptible running/queued jobs by Least Laxity First
        candidates = [
            j
            for j in self._jobs.values()
            if j.status in ("running", "queued") and j.interruptible
        ]
        candidates.sort(
            key=lambda j: (
                j.deadline_hour - self._sim_hours_elapsed - j.remaining_hours,
                j.deadline_hour,
                j.job_id,
            )
        )

        for job in candidates:
            site = self._sites[job.site_id]
            offline = self._site_is_offline_locked(site)
            soc_ratio = site.tank_soc_kwh / max(site.tank_capacity_kwh, 1.0)
            overheated = site.indoor_temp_c >= site.comfort_band_max_c
            committed = self._site_committed_heat_kw_locked(job.site_id, 0)
            horizon_room = self._forecast_headroom_kwh_locked(
                job.site_id, 0, min(2.0, job.remaining_hours)
            )
            needs_migration = offline or overheated or soc_ratio > 0.92 or (
                not site.dump_allowed and committed > max(horizon_room, 0.1) * 0.95
            )
            if not needs_migration:
                continue

            # Try to find a receiving thermal sink
            old_site = job.site_id
            self._release_job_slots_locked(job)
            placement = self._find_candidate_placement_locked(
                tier="spot",
                unit_mode=job.unit_mode,
                gpu_or_sku=job.gpu_or_sku,
                quantity=job.quantity,
                duration_hours=max(0.5, job.remaining_hours),
                preferred_site_id=None,
            )
            if placement is not None and placement["site_id"] != old_site:
                job.site_id = placement["site_id"]
                job.server_ids = list(placement["server_ids"])
                job.gpu_slot_refs = list(placement["gpu_slots"])
                job.hold_until_hour = self._sim_hours_elapsed + 1.0
                job.start_hour = self._sim_hours_elapsed + placement["start_offset_h"]
                job.status = "running" if placement["start_offset_h"] == 0 else "queued"
                self._reserve_job_slots_locked(job)
                migrations.append(
                    {
                        "job_id": job.job_id,
                        "from_site": old_site,
                        "to_site": job.site_id,
                        "reason": "failover" if offline else "proactive_thermal_migration",
                    }
                )
                log_event(
                    "thermal_rebalance",
                    "site_failover_migration" if offline else "proactive_thermal_migration",
                    f"Migrated {job.job_id} from {old_site} -> {job.site_id}",
                    source_module="fleet_scheduler",
                    site_id=job.site_id,
                    job_id=job.job_id,
                    metrics={
                        "from_site": old_site,
                        "to_site": job.site_id,
                        "kw_th": job.kw_th,
                        "hold_until_hour": job.hold_until_hour,
                    },
                    store=self._log_store,
                )
            else:
                # Pause/checkpoint in queue until thermal headroom recovers
                job.status = "queued"
                job.hold_until_hour = self._sim_hours_elapsed + 1.0
                job.start_hour = self._sim_hours_elapsed + 1.0
                self._reserve_job_slots_locked(job)
                migrations.append(
                    {
                        "job_id": job.job_id,
                        "from_site": old_site,
                        "to_site": old_site,
                        "reason": "checkpointed_standby",
                    }
                )
        return migrations

    def _run_backfill_locked(self) -> int:
        """Admit synthetic Vilas BACKFILL work on remaining spare thermal headroom."""
        # Clear completed/stale backfill entries first
        active_backfill_sites = {
            j.site_id
            for j in self._jobs.values()
            if j.tier == "backfill" and j.status == "running"
        }
        added = 0
        for sid, site in self._sites.items():
            if sid in active_backfill_sites or self._site_is_offline_locked(site):
                continue
            room_kw = self._instant_headroom_kw_locked(sid)
            if room_kw <= 0.30:
                continue
            # Prefer idle hydronode / edge / rtx slots for low-power backfill
            for srv_id in self._site_servers[sid]:
                srv = self._servers[srv_id]
                if srv.status == "offline":
                    continue
                if srv.gpu_slots:
                    free_slots = self._available_slots_on_server_locked(
                        srv, "gpu", srv.gpu_model_id or "", 0, 1.0
                    )
                    if not free_slots:
                        continue
                    slot0 = srv.gpu_slots[free_slots[0]]
                    q_th = round(slot0.min_active_kw * slot0.eta, 4)
                    if q_th > room_kw:
                        continue
                    jid = f"bf-{sid}-{uuid.uuid4().hex[:6]}"
                    bf_job = ScheduledJob(
                        job_id=jid,
                        job_name="backfill-archive-ocr",
                        tier="backfill",
                        unit_mode="gpu",
                        gpu_or_sku=slot0.gpu_model_id,
                        quantity=1,
                        workload_type="archive_ocr",
                        site_id=sid,
                        server_ids=[srv_id],
                        gpu_slot_refs=[(srv_id, slot0.slot_index)],
                        arrival_hour=self._sim_hours_elapsed,
                        start_hour=self._sim_hours_elapsed,
                        duration_hours=1.0,
                        remaining_hours=1.0,
                        deadline_hour=self._sim_hours_elapsed + 24.0,
                        interruptible=True,
                        kw_e=round(slot0.min_active_kw, 4),
                        kw_th=q_th,
                        status="running",
                        source="backfill_pool",
                    )
                    self._jobs[jid] = bf_job
                    self._reserve_job_slots_locked(bf_job)
                    added += 1
                    break
                else:
                    free_srv = self._available_slots_on_server_locked(
                        srv, "server", srv.server_model_id, 0, 1.0
                    )
                    if not free_srv:
                        continue
                    q_th = round(srv.min_active_kw * srv.eta, 4)
                    if q_th > room_kw:
                        continue
                    jid = f"bf-{sid}-{uuid.uuid4().hex[:6]}"
                    bf_job = ScheduledJob(
                        job_id=jid,
                        job_name="backfill-iiif-embed",
                        tier="backfill",
                        unit_mode="server",
                        gpu_or_sku=srv.server_model_id,
                        quantity=1,
                        workload_type="iiif_embeddings",
                        site_id=sid,
                        server_ids=[srv_id],
                        gpu_slot_refs=[(srv_id, -1)],
                        arrival_hour=self._sim_hours_elapsed,
                        start_hour=self._sim_hours_elapsed,
                        duration_hours=1.0,
                        remaining_hours=1.0,
                        deadline_hour=self._sim_hours_elapsed + 24.0,
                        interruptible=True,
                        kw_e=round(srv.min_active_kw, 4),
                        kw_th=q_th,
                        status="running",
                        source="backfill_pool",
                    )
                    self._jobs[jid] = bf_job
                    self._reserve_job_slots_locked(bf_job)
                    added += 1
                    break

        if added > 0:
            log_event(
                "scheduler_dispatch",
                "backfill_dispatched",
                f"Admitted {added} BACKFILL spot jobs into spare thermal headroom (0.50x rate)",
                source_module="fleet_scheduler",
                metrics={"backfill_jobs_added": added, "spot_price_ratio": SPOT_PRICE_RATIO},
                store=self._log_store,
            )
        return added

    def dispatch_tick(self, hours: float = 1.0) -> dict[str, Any]:
        """Advance simulation clock, update thermal/RC states, migrate and backfill."""
        if not math.isfinite(hours) or hours <= 0.0 or hours > 72.0:
            raise ValidationInputError("hours must be finite in (0, 72]")

        with self._lock:
            self._sim_hours_elapsed += hours
            self._sim_dt_utc += timedelta(hours=hours)
            self._refresh_all_site_demands_locked()

            # 1. Advance running jobs and update building RC thermal states
            completed_ids: list[str] = []
            for job in list(self._jobs.values()):
                if job.status == "running":
                    job.remaining_hours = max(0.0, job.remaining_hours - hours)
                    if job.remaining_hours <= 1e-6:
                        job.status = "completed"
                        self._release_job_slots_locked(job)
                        completed_ids.append(job.job_id)
                elif job.status == "queued" and self._sim_hours_elapsed >= job.start_hour:
                    job.status = "running"
                    self._reserve_job_slots_locked(job)

            for sid, site in self._sites.items():
                if (
                    site.offline_until_hour is not None
                    and self._sim_hours_elapsed >= site.offline_until_hour
                ):
                    site.offline_until_hour = None
                    for srv_id in self._site_servers[sid]:
                        srv = self._servers[srv_id]
                        if srv.status == "offline":
                            srv.status = "standby"
                q_gen = sum(
                    j.kw_th
                    for j in self._jobs.values()
                    if j.site_id == sid and j.status == "running"
                )
                net_heat_kwh = (q_gen - site.heat_demand_kw) * hours
                new_soc = site.tank_soc_kwh + net_heat_kwh
                site.tank_soc_kwh = round(max(0.0, min(site.tank_capacity_kwh, new_soc)), 3)
                # 1-node Vaibhav RC thermal drift on residential/alpine buildings
                q_space_delivery = min(site.heat_demand_kw, q_gen + site.tank_soc_kwh * 0.15)
                dt_in = (
                    q_space_delivery
                    - site.building_ua_kw_per_k * (site.indoor_temp_c - site.outdoor_temp_c)
                ) * hours / max(site.building_cap_kwh_per_k, 1.0)
                site.indoor_temp_c = round(
                    max(15.0, min(26.0, site.indoor_temp_c + dt_in * 0.25)), 2
                )

            # 2. Proactive thermal migration & backfill
            migrations = self._proactive_migrate_locked()
            backfill_added = self._run_backfill_locked()
            self._state_version += 1

            tel = self.get_fleet_telemetry()
            return {
                "simulation_clock_utc": self._sim_dt_utc.isoformat().replace("+00:00", "Z"),
                "state_version": self._state_version,
                "hours_advanced": hours,
                "completed_jobs_count": len(completed_ids),
                "migrations": migrations,
                "backfill_jobs_added": backfill_added,
                "telemetry": tel,
            }

    def update_site_demand(
        self,
        site_id: str,
        *,
        target_temp_c: float | None = None,
        indoor_temp_c: float | None = None,
        heat_demand_kw: float | None = None,
        tank_soc_kwh: float | None = None,
    ) -> dict[str, Any]:
        """Update live site thermal demand/setpoint and trigger proactive migration if needed."""
        try:
            sid = resolve_site_id(site_id)
        except ValueError as exc:
            raise ValidationInputError(str(exc)) from exc

        with self._lock:
            site = self._sites[sid]
            if target_temp_c is not None:
                site.target_temp_c = round(float(target_temp_c), 2)
                spec = CANONICAL_SITES[sid]
                _, space_dhw_kw, proc_kw, _ = _site_ambient_and_demand(
                    spec, self._sim_dt_utc, site.target_temp_c
                )
                if heat_demand_kw is None:
                    site.heat_demand_kw = round(space_dhw_kw + proc_kw, 3)
            if indoor_temp_c is not None:
                site.indoor_temp_c = round(float(indoor_temp_c), 2)
            if heat_demand_kw is not None:
                site.heat_demand_kw = round(max(0.0, float(heat_demand_kw)), 3)
            if tank_soc_kwh is not None:
                site.tank_soc_kwh = round(
                    max(0.0, min(site.tank_capacity_kwh, float(tank_soc_kwh))), 3
                )

            migrations = self._proactive_migrate_locked()
            self._state_version += 1
            log_event(
                "thermal_rebalance",
                "site_thermal_state_updated",
                (
                    f"Updated {sid}: target={site.target_temp_c} C, "
                    f"indoor={site.indoor_temp_c} C, demand={site.heat_demand_kw} kW"
                ),
                source_module="fleet_scheduler",
                site_id=sid,
                metrics={
                    "target_temp_c": site.target_temp_c,
                    "indoor_temp_c": site.indoor_temp_c,
                    "heat_demand_kw": site.heat_demand_kw,
                    "tank_soc_kwh": site.tank_soc_kwh,
                    "migrations_triggered": len(migrations),
                },
                store=self._log_store,
            )
            return {"site_id": sid, "migrations": migrations, "state_version": self._state_version}

    def failover_site(self, site_id: str, offline_hours: float = 4.0) -> dict[str, Any]:
        """Take a site offline and run Vilas failover migration for interruptible jobs."""
        try:
            sid = resolve_site_id(site_id)
        except ValueError as exc:
            raise ValidationInputError(str(exc)) from exc

        with self._lock:
            site = self._sites[sid]
            site.offline_until_hour = self._sim_hours_elapsed + max(0.5, offline_hours)
            for srv_id in self._site_servers[sid]:
                self._servers[srv_id].status = "offline"
            migrations = self._proactive_migrate_locked()
            self._state_version += 1
            return {"site_id": sid, "migrations": migrations, "state_version": self._state_version}

    def get_fleet_telemetry(
        self,
        *,
        site_id: str | None = None,
        worker_status: Any | None = None,
        day_of_year: int | None = None,
        hour: int | None = None,
    ) -> dict[str, Any]:
        """Return copy.deepcopy live fleet, server, and GPU slot telemetry."""
        resolved_site: str | None = None
        if site_id is not None:
            try:
                resolved_site = resolve_site_id(site_id)
            except ValueError as exc:
                raise ValidationInputError(str(exc)) from exc

        preview_dt: datetime | None = None
        if day_of_year is not None or hour is not None:
            base_dt = self._sim_dt_utc
            doy_val = int(day_of_year) if day_of_year is not None else base_dt.timetuple().tm_yday
            hour_val = int(hour) if hour is not None else base_dt.hour
            preview_dt = datetime(base_dt.year, 1, 1, tzinfo=timezone.utc) + timedelta(
                days=max(1, min(365, doy_val)) - 1,
                hours=max(0, min(23, hour_val)),
            )

        with self._lock:
            target_sids = (
                [resolved_site] if resolved_site is not None else list(CANONICAL_SITES.keys())
            )
            sites_out: list[dict[str, Any]] = []
            tot_servers = 0
            tot_gpus = 0
            active_gpus = 0
            tot_kw_e = 0.0
            tot_kw_th = 0.0
            tot_headroom_kw = 0.0
            backfill_kw_e = 0.0

            worker_pid = None
            worker_running = False
            worker_kw_e = 0.0
            worker_kw_th = 0.0
            worker_job_type = None
            if worker_status is not None and getattr(worker_status, "state", None) in (
                "running",
                "paused",
            ):
                worker_pid = getattr(worker_status, "pid", None)
                if worker_pid is not None:
                    worker_running = getattr(worker_status, "state", None) == "running"
                    worker_kw_e = round(
                        float(getattr(worker_status, "estimated_cpu_power_w", 0.0)) / 1000.0, 4
                    )
                    scaled = getattr(worker_status, "scaled_1kw", None)
                    worker_kw_th = (
                        round(float(getattr(scaled, "heat_recovered_kw", 0.0)), 4)
                        if scaled is not None
                        else round(worker_kw_e * 0.80, 4)
                    )
                    worker_job_type = getattr(worker_status, "job_type", None)

            for sid in target_sids:
                spec = CANONICAL_SITES[sid]
                site = self._sites[sid]
                eval_dt = preview_dt or self._sim_dt_utc
                t_out, space_dhw_kw, proc_kw, sink_active_now = _site_ambient_and_demand(
                    spec, eval_dt, site.target_temp_c
                )
                demand_kw = (
                    round(space_dhw_kw + proc_kw, 3)
                    if preview_dt is not None
                    else site.heat_demand_kw
                )
                outdoor_c = t_out if preview_dt is not None else site.outdoor_temp_c
                headroom_kw = round(self._instant_headroom_kw_locked(sid), 3)

                servers_out: list[dict[str, Any]] = []
                site_kw_e = 0.0
                site_kw_th = 0.0
                site_gpus = 0
                site_busy_gpus = 0

                for srv_idx, srv_id in enumerate(self._site_servers[sid]):
                    srv = self._servers[srv_id]
                    tot_servers += 1
                    srv_jobs = [
                        self._jobs[jid]
                        for jid in srv.active_job_ids
                        if jid in self._jobs and self._jobs[jid].status == "running"
                    ]

                    slots_out: list[dict[str, Any]] = []
                    srv_kw_e = 0.0
                    srv_kw_th = 0.0

                    if srv.gpu_slots:
                        for slot in srv.gpu_slots:
                            tot_gpus += 1
                            site_gpus += 1
                            slot_dict = {
                                "slot_index": slot.slot_index,
                                "gpu_id": slot.gpu_id,
                                "gpu_model_id": slot.gpu_model_id,
                                "gpu_model_name": slot.gpu_model_name,
                                "status": slot.status,
                                "utilization_pct": slot.utilization_pct,
                                "kw_e": slot.kw_e,
                                "kw_th": slot.kw_th,
                                "job_id": slot.job_id,
                                "job_name": slot.job_name,
                                "source": slot.source,
                            }
                            if (
                                sid == "basel_klingental"
                                and srv_idx == 0
                                and slot.slot_index == 0
                                and worker_pid is not None
                            ):
                                slot_dict["source"] = "live_pid"
                                slot_dict["pid"] = worker_pid
                                slot_dict["status"] = "busy" if worker_running else "idle"
                                slot_dict["utilization_pct"] = (
                                    round(float(getattr(worker_status, "cpu_percent", 0.0)), 1)
                                    if worker_running
                                    else 0.0
                                )
                                slot_dict["kw_e"] = worker_kw_e
                                slot_dict["kw_th"] = worker_kw_th
                                slot_dict["job_id"] = f"pid-{worker_pid}"
                                slot_dict["job_name"] = f"live-cpu-{worker_job_type or 'worker'}"

                            if slot_dict["status"] == "busy":
                                active_gpus += 1
                                site_busy_gpus += 1
                            srv_kw_e += float(slot_dict["kw_e"])
                            srv_kw_th += float(slot_dict["kw_th"])
                            slots_out.append(slot_dict)

                        srv_util_pct = round(
                            100.0
                            * sum(float(s["utilization_pct"]) for s in slots_out)
                            / max(len(slots_out), 1),
                            1,
                        )
                    else:
                        if srv_jobs:
                            srv_kw_e = sum(j.kw_e / max(len(j.server_ids), 1) for j in srv_jobs)
                            srv_kw_th = sum(j.kw_th / max(len(j.server_ids), 1) for j in srv_jobs)
                            srv_util_pct = round(
                                min(100.0, 100.0 * srv_kw_e / max(srv.max_kw, 0.1)), 1
                            )
                        elif srv.status == "offline":
                            srv_kw_e = 0.0
                            srv_kw_th = 0.0
                            srv_util_pct = 0.0
                        else:
                            srv_kw_e = srv.idle_kw
                            srv_kw_th = srv.idle_kw * srv.eta
                            srv_util_pct = 0.0

                    site_kw_e += srv_kw_e
                    site_kw_th += srv_kw_th
                    servers_out.append(
                        {
                            "server_id": srv.server_id,
                            "server_model_id": srv.server_model_id,
                            "server_name": srv.server_name,
                            "gpu_model_id": srv.gpu_model_id,
                            "gpu_count": srv.gpu_count,
                            "status": srv.status,
                            "utilization_pct": srv_util_pct,
                            "kw_e": round(srv_kw_e, 4),
                            "kw_th": round(srv_kw_th, 4),
                            "supply_temp_c": srv.supply_temp_c,
                            "active_jobs": [j.job_id for j in srv_jobs],
                            "gpu_slots": slots_out,
                        }
                    )

                tot_kw_e += site_kw_e
                tot_kw_th += site_kw_th
                tot_headroom_kw += headroom_kw
                site_util_pct = (
                    round(
                        sum(float(s["utilization_pct"]) for s in servers_out)
                        / max(len(servers_out), 1),
                        1,
                    )
                )
                sites_out.append(
                    {
                        "site_id": sid,
                        "name": spec.name,
                        "city": spec.city,
                        "canton": spec.canton,
                        "region": spec.region,
                        "category": spec.category,
                        "sink_type": spec.sink_type,
                        "is_summer_sink": spec.is_summer_sink,
                        "is_year_round_sink": spec.is_year_round_sink,
                        "sink_active_now": sink_active_now,
                        "indoor_temp_c": site.indoor_temp_c,
                        "target_temp_c": site.target_temp_c,
                        "comfort_band_max_c": site.comfort_band_max_c,
                        "outdoor_temp_c": outdoor_c,
                        "heat_demand_kw": demand_kw,
                        "thermal_headroom_kw": headroom_kw,
                        "tank_soc_kwh": site.tank_soc_kwh,
                        "tank_soc_pct": round(
                            100.0 * site.tank_soc_kwh / max(site.tank_capacity_kwh, 1.0), 1
                        ),
                        "tank_capacity_kwh": site.tank_capacity_kwh,
                        "target_supply_c": site.target_supply_c,
                        "heater_type": site.heater_type,
                        "gpus_total": site_gpus,
                        "gpus_busy": site_busy_gpus,
                        "utilization_pct": site_util_pct,
                        "kw_e": round(site_kw_e, 4),
                        "kw_th": round(site_kw_th, 4),
                        "offline": self._site_is_offline_locked(site),
                        "servers": servers_out,
                    }
                )

            active_jobs_out: list[dict[str, Any]] = []
            running_count = 0
            queued_count = 0
            for job in self._jobs.values():
                if resolved_site is not None and job.site_id != resolved_site:
                    continue
                if job.status not in ("running", "queued"):
                    continue
                if job.status == "running":
                    running_count += 1
                    if job.tier == "backfill":
                        backfill_kw_e += job.kw_e
                else:
                    queued_count += 1
                active_jobs_out.append(
                    {
                        "job_id": job.job_id,
                        "job_name": job.job_name,
                        "tier": job.tier,
                        "unit_mode": job.unit_mode,
                        "gpu_or_sku": job.gpu_or_sku,
                        "quantity": job.quantity,
                        "workload_type": job.workload_type,
                        "site_id": job.site_id,
                        "server_id": job.server_ids[0] if job.server_ids else None,
                        "server_ids": list(job.server_ids),
                        "gpu_slots": [
                            {"server_id": s, "slot_index": i} for s, i in job.gpu_slot_refs
                        ],
                        "kw_e": job.kw_e,
                        "kw_th": job.kw_th,
                        "remaining_hours": round(job.remaining_hours, 2),
                        "status": job.status,
                        "source": job.source,
                    }
                )

            fleet_util_pct = round(
                100.0 * active_gpus / max(tot_gpus, 1),
                1,
            )
            clock_iso = (preview_dt or self._sim_dt_utc).isoformat().replace("+00:00", "Z")
            summary = {
                "total_sites": len(sites_out),
                "total_servers": tot_servers,
                "total_gpus": tot_gpus,
                "active_gpus": active_gpus,
                "fleet_utilization_pct": fleet_util_pct,
                "total_kw_e": round(tot_kw_e, 3),
                "total_kw_th": round(tot_kw_th, 3),
                "total_thermal_headroom_kw": round(tot_headroom_kw, 3),
                "active_jobs_count": running_count,
                "queued_jobs_count": queued_count,
                "backfill_kw_e": round(backfill_kw_e, 3),
            }
            return copy.deepcopy(
                {
                    "simulation_clock_utc": clock_iso,
                    "state_version": self._state_version,
                    "summary": summary,
                    "fleet_totals": summary,
                    "sites": sites_out,
                    "active_jobs": active_jobs_out,
                }
            )
