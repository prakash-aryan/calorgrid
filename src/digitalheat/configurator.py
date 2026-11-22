"""End-user server configurator, separate Host/Operator ledgers, and conserved HeatLedger."""

from __future__ import annotations

import copy
import json
import math
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from digitalheat.config import DATA_DIR, HEAT_RECOVERY_FRACTION
from digitalheat.data_loader import DatasetIntegrityError, load_weather_zurich
from digitalheat.telemetry_log import log_event

ZURICH_TZ = ZoneInfo("Europe/Zurich")

HeaterType = Literal["gas_boiler", "heat_pump", "electric_backup"]

# Pinned Swiss heating-bill reference baselines
GAS_TARIFF_CHF_PER_KWH_HHV: float = 0.1355  # MESSPUNKT_004 2025 (4237.37 / 31275)
GAS_BOILER_EFFICIENCY: float = 0.90
HEAT_PUMP_SEASONAL_COP: float = 2.5
ELECTRIC_BACKUP_EFFICIENCY: float = 1.00
DHW_MAINS_COLD_TEMP_C: float = 10.0
DHW_LEGIONELLA_TARGET_TEMP_C: float = 60.0
SPACE_RETURN_FLOOR_C: float = 30.0

# Normalized 24h DHW weights (raw sum = 4*1.8 + 20*0.75 = 22.2 -> normalized to 24.0)
_RAW_DHW_WEIGHTS_24H: tuple[float, ...] = tuple(
    1.8 if h in (7, 8, 19, 20) else 0.75 for h in range(24)
)
_DHW_NORM_FACTOR: float = 24.0 / sum(_RAW_DHW_WEIGHTS_24H)
DHW_HOURLY_WEIGHTS_24H: tuple[float, ...] = tuple(
    w * _DHW_NORM_FACTOR for w in _RAW_DHW_WEIGHTS_24H
)

_FORECAST_CACHE: dict[str, tuple[float, dict]] = {}
_FORECAST_TTL_S = 900.0  # 15 minutes


@dataclass(frozen=True)
class DeploymentArchetype:
    id: str
    label: str
    category: str
    city: str
    canton: str
    lat: float
    lon: float
    elevation_m: int
    preview_node_id: str
    annual_heat_demand_kwh: float
    target_supply_c: float
    tariff_chf_per_kwh: float
    heat_credit_chf_per_kwh: float
    summer_utilisation_factor: float
    default_model_id: str
    default_count: int
    max_recommended_kw: float
    summary: str
    boiler_retail_chf: float = 4200.0
    boiler_subsidized_chf: float = 2400.0
    tie_in_base_chf: float = 350.0
    tie_in_per_unit_chf: float = 50.0
    tank_capacity_kwh: float = 35.0
    dhw_annual_share: float = 0.14
    secondary_sink_summer_kw: float = 0.0
    seasonal_months: tuple[int, ...] | None = None
    default_heater_type: HeaterType = "gas_boiler"
    climate_site: Literal["basel", "davos"] = "basel"
    temp_offset_c: float = 0.0


@dataclass(frozen=True)
class ServerModelSpec:
    id: str
    name: str
    vendor_ref: str
    cooling_type: str
    electrical_kw: float
    idle_kw: float
    recovery_fraction: float
    supply_temp_c: float
    unit_capex_chf: float
    annual_amort_chf: float
    compute_rate_chf_per_kwh: float
    visual_style: str
    workload_profile: str
    dataset_source: str
    max_units: int
    min_active_kw: float = 0.25
    typical_kw: float = 1.0
    annual_maintenance_chf: float = 250.0
    gpu_model: str | None = None
    gpu_count: int = 0
    vram_gb_total: int = 0


class ConservationBalance(BaseModel):
    s_start_kwh: float
    s_end_kwh: float
    electricity_active_kwh: float
    electricity_standby_kwh: float
    electricity_total_kwh: float
    heat_recovered_kwh: float
    heat_used_host_kwh: float
    heat_used_secondary_kwh: float
    heat_dumped_kwh: float
    residual_kwh: float
    max_hourly_residual_kwh: float
    tank_capacity_kwh: float
    hours_simulated: int


class HostHorizonLedger(BaseModel):
    heater_type: HeaterType
    heater_label: str
    heater_efficiency_or_cop: float
    fuel_tariff_chf_per_kwh: float
    effective_heat_rate_chf_per_kwh_th: float
    dhw_preheat_cap_pct: float
    space_heat_cap_pct: float
    building_heat_demand_kwh: float
    calorgrid_heat_kwh: float
    default_heater_heat_kwh: float
    calorgrid_heat_share_pct: float | None
    default_heater_share_pct: float | None
    overheat_boost_kwh: float
    baseline_heating_bill_chf: float
    backup_heater_cost_chf: float
    heating_bill_savings_chf: float
    host_server_hardware_chf: float = 0.0
    host_server_electricity_chf: float = 0.0


class HostLedger(HostHorizonLedger):
    savings_basis_note: str
    electricity_reimbursement_mechanism: str
    tomorrow: HostHorizonLedger


class OperatorHorizonLedger(BaseModel):
    active_electricity_kwh: float
    standby_electricity_kwh: float
    total_electricity_kwh: float
    tariff_chf_per_kwh: float
    compute_rate_chf_per_kwh: float
    calorgrid_compute_revenue_chf: float
    calorgrid_heat_fee_chf: float = 0.0
    calorgrid_electricity_cost_chf: float
    calorgrid_maintenance_chf: float
    calorgrid_amortized_capex_chf: float
    calorgrid_operating_cost_chf: float
    calorgrid_total_cost_chf: float
    calorgrid_opex_margin_chf: float
    calorgrid_net_margin_chf: float


class OperatorLedger(OperatorHorizonLedger):
    tomorrow: OperatorHorizonLedger


class GpuSlotProcess(BaseModel):
    slot_index: int
    gpu_id: str
    gpu_model: str
    status: Literal["RUNNING", "SIGSTOP_STANDBY", "FIBER_OFFLOAD"]
    utilization_pct: float
    power_kw: float
    heat_kw: float
    job_id: str | None = None
    job_name: str | None = None


class ProcessUnitStatus(BaseModel):
    unit: int
    pid: int
    process: str
    workload: str
    category: str
    status: Literal["RUNNING", "SIGSTOP_STANDBY", "FIBER_OFFLOAD"]
    source: Literal["simulated", "live_pid"]
    cpu_pct: float
    elec_kw: float
    heat_kw: float
    gpu_slots: list[GpuSlotProcess] = Field(default_factory=list)


DEPLOYMENT_ARCHETYPES: tuple[DeploymentArchetype, ...] = (
    DeploymentArchetype(
        id="house_basel",
        label="Single / Multi-Family House (Basel)",
        category="Residential",
        city="Basel",
        canton="BS",
        lat=47.5618,
        lon=7.5905,
        elevation_m=260,
        preview_node_id="basel_klingental",
        annual_heat_demand_kwh=31231.0,
        target_supply_c=55.0,
        tariff_chf_per_kwh=0.3325,
        heat_credit_chf_per_kwh=0.133,
        summer_utilisation_factor=0.646,
        default_model_id="hydronode_1kw",
        default_count=4,
        max_recommended_kw=6.0,
        summary=(
            "Urban Swiss multi-family home with basement hydronic manifold and 1,000 L buffer tank."
        ),
        tank_capacity_kwh=35.0,
        dhw_annual_share=0.140245,
        secondary_sink_summer_kw=0.0,
        default_heater_type="gas_boiler",
        climate_site="basel",
        temp_offset_c=0.0,
    ),
    DeploymentArchetype(
        id="house_borehole",
        label="Residential Villa + Geothermal Borehole (Riehen)",
        category="Residential + Borehole",
        city="Riehen",
        canton="BS",
        lat=47.5850,
        lon=7.6480,
        elevation_m=278,
        preview_node_id="basel_erdsonde",
        annual_heat_demand_kwh=38500.0,
        target_supply_c=45.0,
        tariff_chf_per_kwh=0.2913,
        heat_credit_chf_per_kwh=0.133,
        summer_utilisation_factor=0.962,
        default_model_id="hydronode_1kw",
        default_count=4,
        max_recommended_kw=8.0,
        summary=(
            "Heats the residence in winter and recharges the SIA 384/6 ground borehole in summer."
        ),
        tank_capacity_kwh=55.0,
        dhw_annual_share=0.14,
        secondary_sink_summer_kw=7.5,
        default_heater_type="heat_pump",
        climate_site="basel",
        temp_offset_c=-0.2,
    ),
    DeploymentArchetype(
        id="alpine_chalet",
        label="Alpine Residence / Spa Chalet (Davos)",
        category="Alpine",
        city="Davos",
        canton="GR",
        lat=46.8005,
        lon=9.8307,
        elevation_m=1560,
        preview_node_id="davos_alpine",
        annual_heat_demand_kwh=52100.0,
        target_supply_c=55.0,
        tariff_chf_per_kwh=0.2601,
        heat_credit_chf_per_kwh=0.145,
        summer_utilisation_factor=0.895,
        default_model_id="ocp_winterfell_3kw",
        default_count=2,
        max_recommended_kw=12.0,
        summary=(
            "291 heating days per year at 1,560 m elevation with continuous hot-water baseload."
        ),
        boiler_retail_chf=6800.0,
        boiler_subsidized_chf=3900.0,
        tie_in_base_chf=550.0,
        tie_in_per_unit_chf=75.0,
        tank_capacity_kwh=125.0,
        dhw_annual_share=0.22,
        secondary_sink_summer_kw=0.0,
        default_heater_type="gas_boiler",
        climate_site="davos",
        temp_offset_c=0.0,
    ),
    DeploymentArchetype(
        id="dairy_farm",
        label="Working Dairy Farm & Cheese Wash (Emmental)",
        category="Agriculture",
        city="Emmental",
        canton="BE",
        lat=46.9735,
        lon=7.7810,
        elevation_m=715,
        preview_node_id="emmental_dairy",
        annual_heat_demand_kwh=74000.0,
        target_supply_c=60.0,
        tariff_chf_per_kwh=0.2527,
        heat_credit_chf_per_kwh=0.140,
        summer_utilisation_factor=0.955,
        default_model_id="ocp_winterfell_3kw",
        default_count=3,
        max_recommended_kw=14.0,
        summary=(
            "Daily milk-line sanitation and cheese-vat preheating absorb server heat year-round."
        ),
        boiler_retail_chf=6800.0,
        boiler_subsidized_chf=3900.0,
        tie_in_base_chf=550.0,
        tie_in_per_unit_chf=75.0,
        tank_capacity_kwh=95.0,
        dhw_annual_share=0.55,
        secondary_sink_summer_kw=0.0,
        default_heater_type="gas_boiler",
        climate_site="basel",
        temp_offset_c=-2.8,
    ),
    DeploymentArchetype(
        id="glasshouse_farm",
        label="Hydroponic Tomato Glasshouse (Thurgau)",
        category="Agriculture",
        city="Thurgau",
        canton="TG",
        lat=47.5580,
        lon=9.1020,
        elevation_m=415,
        preview_node_id="thurgau_greenhouse",
        annual_heat_demand_kwh=128000.0,
        target_supply_c=38.0,
        tariff_chf_per_kwh=0.2527,
        heat_credit_chf_per_kwh=0.133,
        summer_utilisation_factor=0.920,
        default_model_id="b200_ai_10kw",
        default_count=2,
        max_recommended_kw=24.0,
        summary=(
            "Low-temperature 38 C root-zone heating loop ideal for high-density liquid AI racks."
        ),
        boiler_retail_chf=11500.0,
        boiler_subsidized_chf=6500.0,
        tie_in_base_chf=850.0,
        tie_in_per_unit_chf=100.0,
        tank_capacity_kwh=180.0,
        dhw_annual_share=0.25,
        secondary_sink_summer_kw=0.0,
        default_heater_type="gas_boiler",
        climate_site="basel",
        temp_offset_c=-1.0,
    ),
    DeploymentArchetype(
        id="municipal_pool",
        label="Municipal Swimming Pool (Basel St. Jakob)",
        category="Aquatic Facility",
        city="Basel",
        canton="BS",
        lat=47.5384,
        lon=7.6208,
        elevation_m=267,
        preview_node_id="basel_st_jakob",
        annual_heat_demand_kwh=165000.0,
        target_supply_c=32.0,
        tariff_chf_per_kwh=0.2527,
        heat_credit_chf_per_kwh=0.133,
        summer_utilisation_factor=0.962,
        default_model_id="b200_ai_10kw",
        default_count=2,
        max_recommended_kw=28.0,
        summary="28-32 C aquatic basin heat exchanger matching ASHRAE W45 coolant specifications.",
        boiler_retail_chf=11500.0,
        boiler_subsidized_chf=6500.0,
        tie_in_base_chf=850.0,
        tie_in_per_unit_chf=100.0,
        tank_capacity_kwh=160.0,
        dhw_annual_share=0.20,
        secondary_sink_summer_kw=0.0,
        seasonal_months=(5, 6, 7, 8, 9),
        default_heater_type="gas_boiler",
        climate_site="basel",
        temp_offset_c=0.0,
    ),
    DeploymentArchetype(
        id="commercial_bakery",
        label="Commercial Bakery & Laundry (Lucerne)",
        category="Commercial",
        city="Lucerne",
        canton="LU",
        lat=47.0340,
        lon=8.2820,
        elevation_m=490,
        preview_node_id="lucerne_bakery",
        annual_heat_demand_kwh=112000.0,
        target_supply_c=58.0,
        tariff_chf_per_kwh=0.2601,
        heat_credit_chf_per_kwh=0.138,
        summer_utilisation_factor=0.970,
        default_model_id="ocp_winterfell_3kw",
        default_count=4,
        max_recommended_kw=18.0,
        summary="Continuous wash-water preheating from 12 C mains to 55 C six days per week.",
        boiler_retail_chf=6800.0,
        boiler_subsidized_chf=3900.0,
        tie_in_base_chf=550.0,
        tie_in_per_unit_chf=75.0,
        tank_capacity_kwh=135.0,
        dhw_annual_share=0.75,
        secondary_sink_summer_kw=0.0,
        default_heater_type="gas_boiler",
        climate_site="basel",
        temp_offset_c=-1.5,
    ),
    DeploymentArchetype(
        id="hospital_wing",
        label="Hospital DHW & Sterilisation Wing (St. Gallen)",
        category="Healthcare",
        city="St. Gallen",
        canton="SG",
        lat=47.4290,
        lon=9.3860,
        elevation_m=675,
        preview_node_id="stgallen_hospital",
        annual_heat_demand_kwh=215000.0,
        target_supply_c=58.0,
        tariff_chf_per_kwh=0.2527,
        heat_credit_chf_per_kwh=0.135,
        summer_utilisation_factor=0.990,
        default_model_id="b200_ai_10kw",
        default_count=2,
        max_recommended_kw=32.0,
        summary=(
            "24/7 domestic hot-water baseload enabling near-100% utilisation on commercial C6."
        ),
        boiler_retail_chf=11500.0,
        boiler_subsidized_chf=6500.0,
        tie_in_base_chf=850.0,
        tie_in_per_unit_chf=100.0,
        tank_capacity_kwh=210.0,
        dhw_annual_share=0.75,
        secondary_sink_summer_kw=0.0,
        default_heater_type="gas_boiler",
        climate_site="basel",
        temp_offset_c=-2.4,
    ),
)

SERVER_MODELS: tuple[ServerModelSpec, ...] = (
    ServerModelSpec(
        id="hydronode_1kw",
        name="CalorGrid HydroNode 1 kW",
        vendor_ref="Vertiv 16OCP ORV3 Liquid Module",
        cooling_type="Dielectric Coolant -> Exchanger (55/45 C)",
        electrical_kw=1.0,
        idle_kw=0.05,
        recovery_fraction=HEAT_RECOVERY_FRACTION,
        supply_temp_c=55.0,
        unit_capex_chf=4200.0,
        annual_amort_chf=1400.0,
        compute_rate_chf_per_kwh=0.34,
        visual_style="orv3_compact",
        workload_profile="Archive OCR & Embeddings",
        dataset_source="ETH Aquasar / Basel Reference Model",
        max_units=8,
        min_active_kw=0.25,
        typical_kw=1.0,
        annual_maintenance_chf=250.0,
        gpu_model=None,
        gpu_count=0,
        vram_gb_total=0,
    ),
    ServerModelSpec(
        id="ocp_winterfell_3kw",
        name="OCP Winterfell Hybrid Rack (2.9 kW)",
        vendor_ref="Vertiv VR3307 / 9x OCP Xeon Nodes",
        cooling_type="Dielectric Cold-Plate -> Exchanger (52 C)",
        electrical_kw=2.93,
        idle_kw=0.67,
        recovery_fraction=0.78,
        supply_temp_c=52.0,
        unit_capex_chf=11800.0,
        annual_amort_chf=3600.0,
        compute_rate_chf_per_kwh=0.36,
        visual_style="vr3307_enterprise",
        workload_profile="Batch CPU Compute & Simulation",
        dataset_source="HEATWISE Zenodo 23237930",
        max_units=5,
        min_active_kw=1.85,
        typical_kw=2.29,
        annual_maintenance_chf=450.0,
        gpu_model=None,
        gpu_count=0,
        vram_gb_total=0,
    ),
    ServerModelSpec(
        id="b200_ai_10kw",
        name="NVIDIA B200 Liquid AI Rack (10.4 kW)",
        vendor_ref="Lenovo ThinkSystem 8x B200-SXM",
        cooling_type="Dielectric Direct-to-Chip -> Exchanger (45 C)",
        electrical_kw=10.4,
        idle_kw=1.2,
        recovery_fraction=0.80,
        supply_temp_c=45.0,
        unit_capex_chf=48000.0,
        annual_amort_chf=14500.0,
        compute_rate_chf_per_kwh=0.52,
        visual_style="b200_ai_rack",
        workload_profile="LLM Inference (102k tok/s) & SDXL",
        dataset_source="MLPerf Inference v5.1 Offline",
        max_units=3,
        min_active_kw=7.47,
        typical_kw=9.44,
        annual_maintenance_chf=1440.0,
        gpu_model="NVIDIA B200-SXM-180GB",
        gpu_count=8,
        vram_gb_total=1440,
    ),
    ServerModelSpec(
        id="edge_micro_250w",
        name="CalorGrid Edge Micro-Cluster (0.25 kW)",
        vendor_ref="Compact 8x Edge Compute Enclosure",
        cooling_type="Sealed Dielectric Micro-Loop (45/35 C)",
        electrical_kw=0.25,
        idle_kw=0.03,
        recovery_fraction=0.75,
        supply_temp_c=45.0,
        unit_capex_chf=1250.0,
        annual_amort_chf=420.0,
        compute_rate_chf_per_kwh=0.32,
        visual_style="edge_micro",
        workload_profile="Edge Vision & Audio Indexing",
        dataset_source="MLPerf v5.1 Edge Power Reference",
        max_units=8,
        min_active_kw=0.07,
        typical_kw=0.25,
        annual_maintenance_chf=90.0,
        gpu_model=None,
        gpu_count=0,
        vram_gb_total=0,
    ),
)

DEPLOY_BY_ID: dict[str, DeploymentArchetype] = {d.id: d for d in DEPLOYMENT_ARCHETYPES}
MODEL_BY_ID: dict[str, ServerModelSpec] = {m.id: m for m in SERVER_MODELS}

DEPLOYMENT_ALIASES: dict[str, str] = {
    d.preview_node_id: d.id for d in DEPLOYMENT_ARCHETYPES
}
DEPLOYMENT_ALIASES.update(
    {
        "basel_pool_st_jakob": "municipal_pool",
        "basel_ref": "house_basel",
        "davos_hotel": "alpine_chalet",
    }
)

WMO_LABELS: dict[int, str] = {
    0: "Clear Sky",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Alpine Fog",
    48: "Rime Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Dense Drizzle",
    61: "Light Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    71: "Light Snow",
    73: "Snowfall",
    75: "Heavy Snow",
    80: "Rain Showers",
    85: "Snow Showers",
    95: "Thunderstorm",
}


def resolve_deployment_id(deployment_id: str) -> DeploymentArchetype:
    key = deployment_id.strip()
    if key in DEPLOY_BY_ID:
        return DEPLOY_BY_ID[key]
    if key in DEPLOYMENT_ALIASES:
        return DEPLOY_BY_ID[DEPLOYMENT_ALIASES[key]]
    raise ValueError(f"Unknown deployment_id '{deployment_id}'")


def resolve_server_model(server_model_id: str) -> ServerModelSpec:
    key = server_model_id.strip()
    if key not in MODEL_BY_ID:
        raise ValueError(f"Unknown server_model_id '{server_model_id}'")
    return MODEL_BY_ID[key]


def _resolve_heater_spec(
    heater_type: HeaterType, dep: DeploymentArchetype
) -> tuple[str, float, float, float]:
    if heater_type == "gas_boiler":
        eff = GAS_BOILER_EFFICIENCY
        tariff = GAS_TARIFF_CHF_PER_KWH_HHV
        return "Gas Boiler (90% eff.)", eff, tariff, tariff / eff
    if heater_type == "heat_pump":
        cop = HEAT_PUMP_SEASONAL_COP
        tariff = dep.tariff_chf_per_kwh
        return "Air/Ground Heat Pump (COP 2.5)", cop, tariff, tariff / cop
    if heater_type == "electric_backup":
        eff = ELECTRIC_BACKUP_EFFICIENCY
        tariff = dep.tariff_chf_per_kwh
        return "Direct Electric Resistance Heater", eff, tariff, tariff / eff
    raise ValueError(f"Unknown heater_type '{heater_type}'")


def max_units_for_deployment(dep: DeploymentArchetype, mdl: ServerModelSpec) -> int:
    """Return physical max units of `mdl` that fit inside `dep`'s thermal & electrical plant room.

    Small residential basements (`house_basel`, 6 kW recommended) stay at the baseline
    `mdl.max_units` ceiling, while larger commercial/civic/aquatic/hospital plant rooms
    scale proportionally with their thermal & electrical service envelope (`max_recommended_kw`).
    """
    if dep.max_recommended_kw <= 6.5:
        return mdl.max_units
    if mdl.id == "b200_ai_10kw":
        # ~10.4 kW (8x B200) per rack: scale from 3 racks (small sites) up to 8 racks (64 GPUs) in hospitals
        scaled = max(mdl.max_units, int(round(dep.max_recommended_kw / 4.0)))
        return min(8, scaled)
    if mdl.id == "ocp_winterfell_3kw":
        scaled = max(mdl.max_units, int(round(dep.max_recommended_kw * 0.58)))
        return min(20, scaled)
    if mdl.id == "hydronode_1kw":
        scaled = max(mdl.max_units, int(round(dep.max_recommended_kw * 1.25)))
        return min(40, scaled)
    # edge_micro_250w
    scaled = max(mdl.max_units, int(round(dep.max_recommended_kw * 0.75)))
    return min(24, scaled)


def get_configurator_catalog() -> dict:
    deployments_out = []
    for d in DEPLOYMENT_ARCHETYPES:
        dd = asdict(d)
        dd["max_units_by_model"] = {m.id: max_units_for_deployment(d, m) for m in SERVER_MODELS}
        dd["max_gpus_by_model"] = {
            m.id: max_units_for_deployment(d, m) * m.gpu_count for m in SERVER_MODELS
        }
        deployments_out.append(dd)
    return {
        "deployments": deployments_out,
        "server_models": [asdict(m) for m in SERVER_MODELS],
    }


def _fallback_tomorrow_forecast(dep: DeploymentArchetype) -> dict:
    """Deterministic Swiss seasonal fallback if upstream Open-Meteo is unreachable offline."""
    tomorrow_dt = datetime.now(ZURICH_TZ).date() + timedelta(days=1)
    doy = tomorrow_dt.timetuple().tm_yday
    target_mmdd = f"-{tomorrow_dt.month:02d}-{tomorrow_dt.day:02d}"

    hourly_t: list[float] = []
    hourly_hours: list[int] = []
    try:
        weather = load_weather_zurich(site=dep.climate_site, data_dir=DATA_DIR, verify=False)
        for hw in weather.hours:
            if hw.local_date.endswith(target_mmdd):
                hourly_t.append(round(hw.outdoor_temp_c + dep.temp_offset_c, 1))
                hourly_hours.append(hw.local_hour)
    except (DatasetIntegrityError, OSError, ValueError):
        hourly_t = []
        hourly_hours = []

    if not (23 <= len(hourly_t) <= 25):
        base = (
            10.5
            - 9.0 * math.cos(2.0 * math.pi * (doy - 15) / 365.0)
            - (dep.elevation_m - 260.0) * 0.0055
        )
        hourly_t = [
            round(base - 2.4 * math.cos(2.0 * math.pi * (h - 14) / 24.0), 1)
            for h in range(24)
        ]
        hourly_hours = list(range(24))

    mean_t = round(sum(hourly_t) / len(hourly_t), 1)
    return {
        "source": "Pinned Swiss Meteorological Fallback (Offline)",
        "live_api": False,
        "city": dep.city,
        "canton": dep.canton,
        "elevation_m": dep.elevation_m,
        "date": tomorrow_dt.isoformat(),
        "condition": "Partly Cloudy" if mean_t > 0.5 else "Alpine Snow Flurries",
        "weather_code": 2 if mean_t > 0.5 else 71,
        "temp_min_c": min(hourly_t),
        "temp_max_c": max(hourly_t),
        "temp_mean_c": mean_t,
        "hourly_temps_c": hourly_t,
        "hourly_local_hours": hourly_hours,
        "hours_count": len(hourly_t),
        "is_dst_transition": len(hourly_t) != 24,
    }


def _parse_open_meteo_tomorrow(raw: dict, dep: DeploymentArchetype) -> dict | None:
    daily = raw.get("daily", {})
    hourly = raw.get("hourly", {})
    t_dates = daily.get("time", [])
    tomorrow_date = (
        str(t_dates[1])
        if len(t_dates) > 1
        else (datetime.now(ZURICH_TZ).date() + timedelta(days=1)).isoformat()
    )
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    records: list[tuple[int, float]] = []
    for ts, temp in zip(times, temps):
        if isinstance(ts, str) and ts[:10] == tomorrow_date and temp is not None:
            records.append((int(ts[11:13]), round(float(temp), 1)))

    if not (23 <= len(records) <= 25):
        return None

    hourly_local_hours = [r[0] for r in records]
    hourly_t = [r[1] for r in records]
    wcodes = daily.get("weather_code") or [2, 2]
    wcode = int(wcodes[min(1, len(wcodes) - 1)])
    tmins = daily.get("temperature_2m_min") or [min(hourly_t)]
    tmaxs = daily.get("temperature_2m_max") or [max(hourly_t)]
    t_idx = min(1, max(0, len(t_dates) - 1))
    tmin = round(float(tmins[min(t_idx, len(tmins) - 1)]), 1)
    tmax = round(float(tmaxs[min(t_idx, len(tmaxs) - 1)]), 1)
    return {
        "source": "Open-Meteo Live Forecast API (Keyless)",
        "live_api": True,
        "city": dep.city,
        "canton": dep.canton,
        "elevation_m": dep.elevation_m,
        "date": tomorrow_date,
        "condition": WMO_LABELS.get(wcode, "Swiss Alpine Conditions"),
        "weather_code": wcode,
        "temp_min_c": min(tmin, min(hourly_t)),
        "temp_max_c": max(tmax, max(hourly_t)),
        "temp_mean_c": round(sum(hourly_t) / len(hourly_t), 1),
        "hourly_temps_c": hourly_t,
        "hourly_local_hours": hourly_local_hours,
        "hours_count": len(records),
        "is_dst_transition": len(records) != 24,
    }


def fetch_live_swiss_forecast(deployment_id: str = "house_basel") -> dict:
    dep = resolve_deployment_id(deployment_id)
    cache_key = dep.id
    now = time.monotonic()
    cached = _FORECAST_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _FORECAST_TTL_S:
        return copy.deepcopy(cached[1])

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={dep.lat:.4f}&longitude={dep.lon:.4f}"
        f"&hourly=temperature_2m,weather_code"
        f"&daily=temperature_2m_max,temperature_2m_min,weather_code"
        f"&timezone=Europe%2FZurich&forecast_days=2"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CalorGrid/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        parsed = _parse_open_meteo_tomorrow(raw, dep)
        if parsed is None:
            fb = _fallback_tomorrow_forecast(dep)
            _FORECAST_CACHE[cache_key] = (now, fb)
            log_event(
                "analysis_task",
                "open_meteo_forecast_fetched",
                f"Forecast fallback for {dep.id} ({fb['city']}, {fb['temp_mean_c']} C)",
                source_module="configurator",
                site_id=dep.preview_node_id,
                metrics={
                    "deployment_id": dep.id,
                    "city": dep.city,
                    "live_api": False,
                    "temp_mean_c": fb["temp_mean_c"],
                    "date": fb["date"],
                },
            )
            return copy.deepcopy(fb)

        _FORECAST_CACHE[cache_key] = (now, parsed)
        log_event(
            "analysis_task",
            "open_meteo_forecast_fetched",
            f"Live Open-Meteo forecast for {dep.id} ({parsed['city']}, {parsed['temp_mean_c']} C)",
            source_module="configurator",
            site_id=dep.preview_node_id,
            metrics={
                "deployment_id": dep.id,
                "city": dep.city,
                "live_api": True,
                "temp_mean_c": parsed["temp_mean_c"],
                "date": parsed["date"],
            },
        )
        return copy.deepcopy(parsed)
    except Exception:
        fb = _fallback_tomorrow_forecast(dep)
        _FORECAST_CACHE[cache_key] = (now, fb)
        log_event(
            "analysis_task",
            "open_meteo_forecast_fetched",
            f"Offline forecast fallback for {dep.id} ({fb['city']}, {fb['temp_mean_c']} C)",
            source_module="configurator",
            site_id=dep.preview_node_id,
            metrics={
                "deployment_id": dep.id,
                "city": dep.city,
                "live_api": False,
                "temp_mean_c": fb["temp_mean_c"],
                "date": fb["date"],
            },
        )
        return copy.deepcopy(fb)


def _load_annual_climate_hours(climate_site: Literal["basel", "davos"]):
    try:
        return load_weather_zurich(site=climate_site, data_dir=DATA_DIR, verify=False)
    except (DatasetIntegrityError, OSError, ValueError):
        return None


def _build_annual_hourly_demands(
    dep: DeploymentArchetype,
    indoor_target_c: float,
) -> tuple[list[float], list[float], list[float], float]:
    """Construct calibrated 8,760h (space_kw, dhw_kw, sec_kw, ua_space) for dep."""
    if dep.annual_heat_demand_kwh <= 0.0:
        zeros = [0.0] * 8760
        return zeros, zeros, zeros, 0.0

    weather = _load_annual_climate_hours(dep.climate_site)
    dhw_yr = dep.annual_heat_demand_kwh * dep.dhw_annual_share
    space_yr = max(0.0, dep.annual_heat_demand_kwh - dhw_yr)

    if weather is not None:
        n_h = len(weather.hours)
        if dep.seasonal_months is not None:
            active_flags = [int(h.local_date[5:7]) in dep.seasonal_months for h in weather.hours]
            g_ref = sum(
                max(0.5, 28.0 - (h.outdoor_temp_c + dep.temp_offset_c))
                for h, act in zip(weather.hours, active_flags)
                if act
            )
            ua_space = space_yr / max(g_ref, 1e-6)
            indoor_scale = 1.0 + 0.02 * (indoor_target_c - 21.0)
            space_list = [
                (
                    ua_space
                    * max(0.5, 28.0 - (h.outdoor_temp_c + dep.temp_offset_c))
                    * indoor_scale
                    if act
                    else 0.0
                )
                for h, act in zip(weather.hours, active_flags)
            ]
        else:
            active_flags = [h.local_date in weather.heating_days for h in weather.hours]
            g_ref = sum(
                max(0.0, 18.0 - (h.outdoor_temp_c + dep.temp_offset_c))
                for h, act in zip(weather.hours, active_flags)
                if act
            )
            ua_space = space_yr / max(g_ref, 1e-6)
            space_list = [
                (
                    ua_space
                    * max(0.0, (indoor_target_c - 3.0) - (h.outdoor_temp_c + dep.temp_offset_c))
                    if act
                    else 0.0
                )
                for h, act in zip(weather.hours, active_flags)
            ]

        dhw_list = [
            (dhw_yr / float(n_h)) * DHW_HOURLY_WEIGHTS_24H[h.local_hour % 24]
            for h in weather.hours
        ]
        sec_list = [
            dep.secondary_sink_summer_kw if 5 <= int(h.local_date[5:7]) <= 9 else 0.0
            for h in weather.hours
        ]
        return space_list, dhw_list, sec_list, ua_space

    # Synthetic 8,760h fallback if CSV unavailable in isolated test
    n_h = 8760
    mean_t = 2.0 if dep.climate_site == "davos" else 10.5
    temps = [
        mean_t - 9.0 * math.cos(2.0 * math.pi * ((i // 24) - 15) / 365.0) + dep.temp_offset_c
        for i in range(n_h)
    ]
    g_ref = sum(max(0.0, 18.0 - t) for t in temps)
    ua_space = space_yr / max(g_ref, 1e-6)
    space_list = [ua_space * max(0.0, (indoor_target_c - 3.0) - t) for t in temps]
    dhw_list = [(dhw_yr / n_h) * DHW_HOURLY_WEIGHTS_24H[i % 24] for i in range(n_h)]
    sec_list = [
        dep.secondary_sink_summer_kw if 121 <= (i // 24) + 1 <= 273 else 0.0
        for i in range(n_h)
    ]
    return space_list, dhw_list, sec_list, ua_space


def _simulate_conserved_horizon(
    *,
    dep: DeploymentArchetype,
    mdl: ServerModelSpec,
    count: int,
    space_kw_list: list[float],
    dhw_kw_list: list[float],
    sec_kw_list: list[float],
    target_frac: float,
    heater_type: HeaterType,
) -> dict[str, Any]:
    """Run strictly conserved hourly energy and thermal dispatch loop."""
    s_t = 0.0
    s_max = dep.tank_capacity_kwh
    q_unit = mdl.electrical_kw * mdl.recovery_fraction

    f_dhw = max(
        0.0,
        min(
            1.0,
            (mdl.supply_temp_c - DHW_MAINS_COLD_TEMP_C)
            / (DHW_LEGIONELLA_TARGET_TEMP_C - DHW_MAINS_COLD_TEMP_C),
        ),
    )
    if dep.target_supply_c <= mdl.supply_temp_c:
        f_space = 1.0
    else:
        f_space = max(
            0.35,
            min(
                1.0,
                (mdl.supply_temp_c - SPACE_RETURN_FLOOR_C)
                / max(dep.target_supply_c - SPACE_RETURN_FLOOR_C, 1.0),
            ),
        )

    tot_demand = 0.0
    tot_cg_bill = 0.0
    tot_host_delivered = 0.0
    tot_sec_used = 0.0
    tot_dumped = 0.0
    tot_elec_act = 0.0
    tot_elec_stdby = 0.0
    tot_rec = 0.0
    max_hourly_res = 0.0
    hourly_records: list[dict[str, float | int]] = []

    for h_idx, (d_sp, d_dhw, d_sec) in enumerate(zip(space_kw_list, dhw_kw_list, sec_kw_list)):
        d_host = max(0.0, d_sp + d_dhw)
        d_host_cap = max(0.0, f_space * d_sp + f_dhw * d_dhw)
        d_dispatch = target_frac * d_host

        headroom = max(0.0, s_max - s_t) + d_dispatch + d_sec
        n_act = min(count, int((headroom + 1e-9) // q_unit)) if q_unit > 0.0 else 0
        act_ratio = n_act / max(count, 1)
        n_standby = (count - n_act) if (n_act < count and act_ratio >= 0.45) else 0

        e_act = n_act * mdl.electrical_kw
        e_stdby = n_standby * mdl.idle_kw
        e_tot = e_act + e_stdby
        q_rec = mdl.recovery_fraction * e_tot

        avail = s_t + q_rec
        d_absorb = max(d_host_cap, target_frac * d_host_cap)
        q_host = min(avail, d_absorb)
        q_bill = min(q_host, d_host_cap)

        rem_1 = avail - q_host
        q_sec = min(max(0.0, rem_1 - s_max), d_sec)
        rem_2 = rem_1 - q_sec
        s_next = min(s_max, max(0.0, rem_2))
        q_dump = max(0.0, rem_2 - s_max)

        res_h = abs((s_t + q_rec) - (q_host + q_sec + q_dump + s_next))
        if res_h > max_hourly_res:
            max_hourly_res = res_h

        s_t = s_next
        tot_demand += d_host
        tot_cg_bill += q_bill
        tot_host_delivered += q_host
        tot_sec_used += q_sec
        tot_dumped += q_dump
        tot_elec_act += e_act
        tot_elec_stdby += e_stdby
        tot_rec += q_rec

        hourly_records.append(
            {
                "hour_idx": h_idx,
                "demand_kw": round(d_host, 3),
                "dispatch_target_kw": round(d_dispatch, 3),
                "server_heat_kw": round(q_rec, 3),
                "calorgrid_heat_kw": round(q_bill, 3),
                "default_heater_kw": round(max(0.0, d_host - q_bill), 3),
                "tank_soc_kwh": round(s_next, 3),
                "heat_dumped_kw": round(q_dump, 3),
                "active_servers": n_act,
            }
        )

    n_hours = len(space_kw_list)
    total_res = abs((0.0 + tot_rec) - (tot_host_delivered + tot_sec_used + tot_dumped + s_t))
    e_total = tot_elec_act + tot_elec_stdby

    heater_label, eff_or_cop, fuel_tariff, rate_th = _resolve_heater_spec(heater_type, dep)

    q_cg_clamped = min(tot_cg_bill, tot_demand)
    def_heat = max(0.0, tot_demand - q_cg_clamped)
    if tot_demand > 1e-9:
        cg_share = round(100.0 * q_cg_clamped / tot_demand, 1)
        def_share = round(100.0 - cg_share, 1)
    else:
        cg_share = None
        def_share = None

    baseline_bill = round(tot_demand * rate_th, 2)
    backup_cost = round(def_heat * rate_th, 2)
    bill_savings = round(baseline_bill - backup_cost, 2)
    overheat_boost = round(max(0.0, tot_host_delivered - q_cg_clamped), 2)

    host_ledger = HostHorizonLedger(
        heater_type=heater_type,
        heater_label=heater_label,
        heater_efficiency_or_cop=eff_or_cop,
        fuel_tariff_chf_per_kwh=round(fuel_tariff, 5),
        effective_heat_rate_chf_per_kwh_th=round(rate_th, 5),
        dhw_preheat_cap_pct=round(100.0 * f_dhw, 1),
        space_heat_cap_pct=round(100.0 * f_space, 1),
        building_heat_demand_kwh=round(tot_demand, 2),
        calorgrid_heat_kwh=round(q_cg_clamped, 2),
        default_heater_heat_kwh=round(def_heat, 2),
        calorgrid_heat_share_pct=cg_share,
        default_heater_share_pct=def_share,
        overheat_boost_kwh=overheat_boost,
        baseline_heating_bill_chf=baseline_bill,
        backup_heater_cost_chf=backup_cost,
        heating_bill_savings_chf=bill_savings,
        host_server_hardware_chf=0.0,
        host_server_electricity_chf=0.0,
    )

    comp_rev = round(tot_elec_act * mdl.compute_rate_chf_per_kwh, 2)
    elec_cost = round(e_total * dep.tariff_chf_per_kwh, 2)
    maint_cost = round(count * mdl.annual_maintenance_chf * (n_hours / 8760.0), 2)
    amort_cost = round(count * mdl.annual_amort_chf * (n_hours / 8760.0), 2)
    op_cost = round(elec_cost + maint_cost, 2)
    tot_cost = round(op_cost + amort_cost, 2)
    opex_margin = round(comp_rev - op_cost, 2)
    net_margin = round(comp_rev - tot_cost, 2)

    operator_ledger = OperatorHorizonLedger(
        active_electricity_kwh=round(tot_elec_act, 2),
        standby_electricity_kwh=round(tot_elec_stdby, 2),
        total_electricity_kwh=round(e_total, 2),
        tariff_chf_per_kwh=round(dep.tariff_chf_per_kwh, 5),
        compute_rate_chf_per_kwh=round(mdl.compute_rate_chf_per_kwh, 4),
        calorgrid_compute_revenue_chf=comp_rev,
        calorgrid_heat_fee_chf=0.0,
        calorgrid_electricity_cost_chf=elec_cost,
        calorgrid_maintenance_chf=maint_cost,
        calorgrid_amortized_capex_chf=amort_cost,
        calorgrid_operating_cost_chf=op_cost,
        calorgrid_total_cost_chf=tot_cost,
        calorgrid_opex_margin_chf=opex_margin,
        calorgrid_net_margin_chf=net_margin,
    )

    conservation = ConservationBalance(
        s_start_kwh=0.0,
        s_end_kwh=round(s_t, 6),
        electricity_active_kwh=round(tot_elec_act, 4),
        electricity_standby_kwh=round(tot_elec_stdby, 4),
        electricity_total_kwh=round(e_total, 4),
        heat_recovered_kwh=round(tot_rec, 4),
        heat_used_host_kwh=round(tot_host_delivered, 4),
        heat_used_secondary_kwh=round(tot_sec_used, 4),
        heat_dumped_kwh=round(tot_dumped, 4),
        residual_kwh=total_res,
        max_hourly_residual_kwh=max_hourly_res,
        tank_capacity_kwh=s_max,
        hours_simulated=n_hours,
    )

    return {
        "conservation": conservation,
        "host_horizon": host_ledger,
        "operator_horizon": operator_ledger,
        "hourly_records": hourly_records,
        "s_end_kwh": s_t,
    }


@lru_cache(maxsize=128)
def _simulate_annual_cached(
    deployment_id: str,
    server_model_id: str,
    count: int,
    indoor_target_rounded: float,
    heater_type: HeaterType,
) -> dict[str, Any]:
    dep = DEPLOY_BY_ID[deployment_id]
    mdl = MODEL_BY_ID[server_model_id]
    space_list, dhw_list, sec_list, ua_space = _build_annual_hourly_demands(
        dep, indoor_target_rounded
    )
    sim = _simulate_conserved_horizon(
        dep=dep,
        mdl=mdl,
        count=count,
        space_kw_list=space_list,
        dhw_kw_list=dhw_list,
        sec_kw_list=sec_list,
        target_frac=1.0,
        heater_type=heater_type,
    )
    sim["ua_space"] = ua_space
    return sim


def compute_configuration_quote(
    deployment_id: str = "house_basel",
    server_model_id: str = "hydronode_1kw",
    server_count: int = 4,
    heat_target_pct: int = 100,
    outdoor_temp_c: float | None = None,
    indoor_target_c: float = 21.0,
    has_existing_boiler: bool = False,
    heater_type: HeaterType | None = None,
    fleet_site_snapshot: dict[str, Any] | None = None,
    worker_status: Any | None = None,
) -> dict:
    dep = resolve_deployment_id(deployment_id)
    mdl = resolve_server_model(server_model_id)

    site_max_units = max_units_for_deployment(dep, mdl)
    if server_count < 1 or server_count > site_max_units:
        raise ValueError(
            f"server_count ({server_count}) out of range [1, {site_max_units}] for {mdl.id} at {dep.id}"
        )
    count = int(server_count)

    chosen_heater: HeaterType = heater_type if heater_type is not None else dep.default_heater_type
    if chosen_heater not in ("gas_boiler", "heat_pump", "electric_backup"):
        raise ValueError(f"Unknown heater_type '{chosen_heater}'")

    if heat_target_pct < 20 or heat_target_pct > 140:
        raise ValueError(f"heat_target_pct ({heat_target_pct}) outside [20, 140]")
    if indoor_target_c < 18.0 or indoor_target_c > 24.0:
        raise ValueError(f"indoor_target_c ({indoor_target_c}) outside [18.0, 24.0]")

    target_frac = float(heat_target_pct) / 100.0
    indoor_rounded = round(float(indoor_target_c), 1)

    forecast = fetch_live_swiss_forecast(dep.id)
    hourly_temps_raw = list(forecast["hourly_temps_c"])
    hourly_hours = list(forecast.get("hourly_local_hours", list(range(len(hourly_temps_raw)))))
    if outdoor_temp_c is not None:
        shift = float(outdoor_temp_c) - float(forecast["temp_mean_c"])
        hourly_temps = [round(t + shift, 1) for t in hourly_temps_raw]
    else:
        hourly_temps = hourly_temps_raw
    ext_temp = round(sum(hourly_temps) / max(len(hourly_temps), 1), 1)

    # Annual conserved simulation (invariant to heat_target_pct)
    annual_sim = copy.deepcopy(
        _simulate_annual_cached(dep.id, mdl.id, count, indoor_rounded, chosen_heater)
    )
    annual_cons: ConservationBalance = annual_sim["conservation"]
    annual_host: HostHorizonLedger = annual_sim["host_horizon"]
    annual_op: OperatorHorizonLedger = annual_sim["operator_horizon"]
    ua_space: float = annual_sim["ua_space"]

    # Peak hardware electrical & thermal output
    peak_elec_kw = count * mdl.electrical_kw
    peak_water_kw = peak_elec_kw * mdl.recovery_fraction
    peak_room_kw = peak_elec_kw * (1.0 - mdl.recovery_fraction)
    peak_flow_l_min = peak_water_kw * 60.0 / (4.186 * 10.0)

    heat_covered_pct = (
        annual_host.calorgrid_heat_share_pct
        if annual_host.calorgrid_heat_share_pct is not None
        else 0.0
    )

    # Operator-owned compute CAPEX (CalorGrid supplies servers at CHF 0 to host)
    operator_server_capex_chf = count * mdl.unit_capex_chf
    server_host_cost_chf = 0.0
    boiler_retail_chf = dep.boiler_retail_chf
    boiler_subsidized_chf = dep.boiler_subsidized_chf
    boiler_discount_chf = max(0.0, boiler_retail_chf - boiler_subsidized_chf)
    boiler_cost_chf = 0.0 if has_existing_boiler else boiler_subsidized_chf
    tie_in_chf = dep.tie_in_base_chf + count * dep.tie_in_per_unit_chf
    host_upfront_chf = boiler_cost_chf + tie_in_chf
    monthly_plan_chf = host_upfront_chf / 36.0

    host_payback_years = (
        round(host_upfront_chf / annual_host.heating_bill_savings_chf, 1)
        if annual_host.heating_bill_savings_chf > 50.0
        else None
    )
    co2_avoided_kg_yr = round(annual_host.calorgrid_heat_kwh * 0.228, 0)

    # Tomorrow hourly demand with normalized DHW weights across 23/24/25h
    base_dhw_kwh = dep.annual_heat_demand_kwh * dep.dhw_annual_share / 365.0
    raw_w = [1.8 if (hr % 24) in (7, 8, 19, 20) else 0.75 for hr in hourly_hours]
    w_sum = max(sum(raw_w), 1e-6)
    dhw_tom = [base_dhw_kwh * (w / w_sum) for w in raw_w]

    tom_month = date.fromisoformat(str(forecast["date"])[:10]).month
    if dep.seasonal_months is not None:
        in_season = tom_month in dep.seasonal_months
        space_tom = [
            (ua_space * max(0.5, 28.0 - t_h) * (1.0 + 0.02 * (indoor_rounded - 21.0)))
            if in_season
            else 0.0
            for t_h in hourly_temps
        ]
    elif dep.annual_heat_demand_kwh <= 0.0:
        space_tom = [0.0 for _ in hourly_temps]
    else:
        is_heating = ext_temp < 15.0
        space_tom = [
            ua_space * max(0.0, (indoor_rounded - 3.0) - t_h)
            if (is_heating or t_h < indoor_rounded - 3.0)
            else 0.0
            for t_h in hourly_temps
        ]

    is_summer = 5 <= tom_month <= 9
    sec_tom = [dep.secondary_sink_summer_kw if is_summer else 0.0 for _ in hourly_temps]

    tomorrow_sim = _simulate_conserved_horizon(
        dep=dep,
        mdl=mdl,
        count=count,
        space_kw_list=space_tom,
        dhw_kw_list=dhw_tom,
        sec_kw_list=sec_tom,
        target_frac=target_frac,
        heater_type=chosen_heater,
    )
    tom_cons: ConservationBalance = tomorrow_sim["conservation"]
    tom_host: HostHorizonLedger = tomorrow_sim["host_horizon"]
    tom_op: OperatorHorizonLedger = tomorrow_sim["operator_horizon"]

    recommended_tomorrow_kwh = round(tom_host.building_heat_demand_kwh, 1)
    user_target_tomorrow_kwh = round(recommended_tomorrow_kwh * target_frac, 1)
    delivered_tomorrow_kwh = round(tom_cons.heat_used_host_kwh, 1)

    max_daily_water_kwh = max(peak_water_kw * max(len(hourly_temps), 1), 0.1)
    active_load_ratio = min(1.0, delivered_tomorrow_kwh / max_daily_water_kwh)
    active_servers_now = max(0, min(count, int(round(count * active_load_ratio))))
    if active_load_ratio > 0.08 and active_servers_now == 0:
        active_servers_now = 1
    standby_servers = (count - active_servers_now) if (active_load_ratio >= 0.45) else 0
    live_elec_kw = round(
        active_servers_now * mdl.electrical_kw + standby_servers * mdl.idle_kw,
        2,
    )
    live_water_kw = round(active_servers_now * mdl.electrical_kw * mdl.recovery_fraction, 2)
    live_flow_l_min = round(live_water_kw * 60.0 / (4.186 * 10.0), 2)
    tank_soc_pct = round(
        min(98.0, max(12.0, 100.0 * tom_cons.s_end_kwh / max(dep.tank_capacity_kwh, 1.0))),
        0,
    )
    supply_temp_c = round(
        min(mdl.supply_temp_c, 36.0 + active_load_ratio * (mdl.supply_temp_c - 34.0)), 1
    )

    hourly_schedule = []
    for hr_local, t_h, rec in zip(hourly_hours, hourly_temps, tomorrow_sim["hourly_records"]):
        hourly_schedule.append(
            {
                "hour": hr_local,
                "temp_c": t_h,
                "demand_kw": round(rec["dispatch_target_kw"], 2),
                "baseline_demand_kw": round(rec["demand_kw"], 2),
                "server_heat_kw": round(rec["server_heat_kw"], 2),
                "calorgrid_heat_kw": round(rec["calorgrid_heat_kw"], 2),
                "default_heater_kw": round(rec["default_heater_kw"], 2),
                "tank_soc_kwh": rec["tank_soc_kwh"],
            }
        )

    process_templates = [
        ("tesseract-ocr", "Swiss Heritage Archive OCR", "Batch CPU", 4821),
        ("iiif-clip-embed", "Basel Museum IIIF Embeddings", "Vector Batch", 4824),
        ("llama3-batch", "Swiss Research LLM Summarisation", "Tensor Batch", 4829),
        ("whisper-ch-asr", "Alpine Oral History Transcription", "Audio Batch", 4835),
        ("fhnw-grid-sim", "FHNW Thermal Grid Simulation", "Scientific", 4842),
        ("iiif-pyramid", "High-Res Archival Tile Generation", "Imaging", 4849),
        ("rag-indexer", "Cantonal Document Vector Indexing", "Batch CPU", 4856),
        ("climate-regrid", "Alpine Weather Grid Downscaling", "Scientific", 4863),
    ]

    worker_pid = None
    worker_running = False
    worker_job_type = None
    if worker_status is not None and getattr(worker_status, "state", None) in ("running", "paused"):
        worker_pid = getattr(worker_status, "pid", None)
        worker_running = getattr(worker_status, "state", None) == "running"
        worker_job_type = getattr(worker_status, "job_type", None)

    fleet_servers = (
        fleet_site_snapshot.get("servers", [])
        if isinstance(fleet_site_snapshot, dict)
        else []
    )

    processes: list[dict[str, Any]] = []
    for idx in range(count):
        proc, desc, kind, pid = process_templates[idx % len(process_templates)]
        source_mode: Literal["simulated", "live_pid"] = "simulated"
        if idx < active_servers_now:
            status: Literal["RUNNING", "SIGSTOP_STANDBY", "FIBER_OFFLOAD"] = "RUNNING"
            cpu_pct = round(84.0 + (idx % 3) * 4.5, 1)
            elec_kw = round(mdl.electrical_kw, 3)
            heat_kw = round(mdl.electrical_kw * mdl.recovery_fraction, 2)
        elif active_load_ratio < 0.45:
            status = "FIBER_OFFLOAD"
            cpu_pct = 0.0
            elec_kw = 0.0
            heat_kw = 0.0
        else:
            status = "SIGSTOP_STANDBY"
            cpu_pct = 0.0
            elec_kw = round(mdl.idle_kw, 3)
            heat_kw = round(mdl.idle_kw * mdl.recovery_fraction, 3)

        if idx == 0 and worker_pid is not None:
            source_mode = "live_pid"
            pid = int(worker_pid)
            proc = f"live-{worker_job_type or 'worker'}"
            if worker_running:
                status = "RUNNING"
                cpu_pct = round(float(getattr(worker_status, "cpu_percent", 88.0)), 1)
                if elec_kw <= 0.0:
                    scaled = getattr(worker_status, "scaled_1kw", None)
                    live_ref_kw = float(
                        getattr(scaled, "reference_server_power_kw", 0.0) or 0.0
                    )
                    live_rec_kw = float(
                        getattr(scaled, "heat_recovered_kw", 0.0) or 0.0
                    )
                    est_w = float(
                        getattr(worker_status, "estimated_cpu_power_w", 0.0) or 0.0
                    )
                    if live_ref_kw > 0.0:
                        elec_kw = round(live_ref_kw, 3)
                        heat_kw = round(
                            live_rec_kw
                            if live_rec_kw > 0.0
                            else live_ref_kw * mdl.recovery_fraction,
                            3,
                        )
                    elif live_rec_kw > 0.0:
                        heat_kw = round(live_rec_kw, 3)
                        elec_kw = round(
                            live_rec_kw / max(mdl.recovery_fraction, 1e-6), 3
                        )
                    elif est_w > 0.0:
                        est_kw = est_w / 1000.0
                        elec_kw = round(est_kw, 4)
                        heat_kw = round(est_kw * mdl.recovery_fraction, 4)
                    else:
                        elec_kw = round(mdl.electrical_kw, 3)
                        heat_kw = round(mdl.electrical_kw * mdl.recovery_fraction, 3)
            else:
                status = "SIGSTOP_STANDBY"
                cpu_pct = 0.0
                elec_kw = round(mdl.idle_kw, 3)
                heat_kw = round(mdl.idle_kw * mdl.recovery_fraction, 3)

        gpu_slots_out: list[dict[str, Any]] = []
        if mdl.gpu_count > 0:
            fleet_srv = fleet_servers[idx] if idx < len(fleet_servers) else None
            fleet_slots = fleet_srv.get("gpu_slots", []) if isinstance(fleet_srv, dict) else []
            per_gpu_p = round(elec_kw / mdl.gpu_count, 3)
            per_gpu_h = round(heat_kw / mdl.gpu_count, 3)
            for s_i in range(mdl.gpu_count):
                fs = fleet_slots[s_i] if s_i < len(fleet_slots) else None
                gpu_slots_out.append(
                    GpuSlotProcess(
                        slot_index=s_i,
                        gpu_id=(
                            str(fs["gpu_id"])
                            if isinstance(fs, dict) and fs.get("gpu_id")
                            else f"{dep.preview_node_id}-u{idx + 1}-gpu-{s_i}"
                        ),
                        gpu_model=mdl.gpu_model or "GPU",
                        status=status,
                        utilization_pct=cpu_pct,
                        power_kw=per_gpu_p,
                        heat_kw=per_gpu_h,
                        job_id=fs.get("job_id") if isinstance(fs, dict) else None,
                        job_name=fs.get("job_name") if isinstance(fs, dict) else desc,
                    ).model_dump()
                )

        processes.append(
            ProcessUnitStatus(
                unit=idx + 1,
                pid=pid + idx if source_mode == "simulated" else pid,
                process=proc,
                workload=desc,
                category=kind,
                status=status,
                source=source_mode,
                cpu_pct=cpu_pct,
                elec_kw=elec_kw,
                heat_kw=heat_kw,
                gpu_slots=[GpuSlotProcess(**gs) for gs in gpu_slots_out],
            ).model_dump()
        )

    # Smart sizing fit guidance
    if peak_elec_kw <= dep.max_recommended_kw * 0.35:
        fit_status = "under_sized"
        fit_label = "Light Baseload Fit"
        fit_note = (
            f"Covers {heat_covered_pct:.0f}% of annual demand. "
            "Adding another unit increases thermal savings."
        )
    elif peak_elec_kw <= dep.max_recommended_kw:
        fit_status = "optimal"
        fit_label = "Optimal Thermal Fit"
        fit_note = (
            f"Balanced for {dep.city}: covers {heat_covered_pct:.0f}% "
            "of annual heat with zero summer waste."
        )
    else:
        fit_status = "fiber_paired"
        fit_label = "High Output · Fiber Offload Active"
        fit_note = (
            "Exceeds single-site summer demand; CalorGrid routes surplus compute over Swiss fiber."
        )

    if chosen_heater == "gas_boiler":
        savings_note = (
            "Building Hosts save on their variable heating bill by displacing gas boiler output "
            "(capped at min(CalorGrid heat, building demand); Basel MESSPUNKT_004 2025 variable "
            "tariff CHF 0.1355/kWh_gas / 90% eff. = CHF 0.1506/kWh_th, excluding the "
            "non-avoidable CHF 39.29/mo fixed meter fee). Server hardware is CHF 0 and server "
            "electricity is paid directly by CalorGrid."
        )
    else:
        savings_note = (
            "Building Hosts save specifically on their variable heating bill by displacing "
            "default heater output (capped at min(CalorGrid heat, building demand)). Server "
            "hardware is CHF 0 and server electricity is paid directly by CalorGrid."
        )

    full_host_ledger = HostLedger(
        **annual_host.model_dump(),
        savings_basis_note=savings_note,
        electricity_reimbursement_mechanism=(
            "Dedicated sub-meter / direct CalorGrid utility billing "
            "(CHF 0 on host electricity bill)."
        ),
        tomorrow=tom_host,
    )
    full_operator_ledger = OperatorLedger(
        **annual_op.model_dump(),
        tomorrow=tom_op,
    )

    log_event(
        "analysis_task",
        "host_heatledger_quote",
        (
            f"HeatLedger quote {dep.id}/{mdl.id} x{count} "
            f"(target={heat_target_pct}%, indoor={indoor_rounded} C)"
        ),
        source_module="configurator",
        site_id=dep.preview_node_id,
        metrics={
            "deployment_id": dep.id,
            "server_model_id": mdl.id,
            "server_count": count,
            "heat_target_pct": int(heat_target_pct),
            "indoor_target_c": indoor_rounded,
            "delivered_tomorrow_kwh": delivered_tomorrow_kwh,
            "active_servers_now": active_servers_now,
            "fit_status": fit_status,
            "annual_conservation_residual_kwh": annual_cons.residual_kwh,
        },
    )
    if fit_status == "fiber_paired":
        log_event(
            "thermal_rebalance",
            "configurator_fiber_offload_advised",
            f"{dep.id} with {count}x {mdl.id} triggers summer Swiss fiber offload",
            source_module="configurator",
            site_id=dep.preview_node_id,
            metrics={"peak_elec_kw": peak_elec_kw, "max_recommended_kw": dep.max_recommended_kw},
        )

    return {
        "deployment": asdict(dep),
        "server_model": asdict(mdl),
        "server_count": count,
        "sizing": {
            "site_max_units": site_max_units,
            "installed_gpus": count * mdl.gpu_count,
            "site_max_gpus": site_max_units * mdl.gpu_count,
            "installed_vram_gb": count * mdl.vram_gb_total,
            "building_recommended_kw": dep.max_recommended_kw,
            "peak_elec_kw": round(peak_elec_kw, 2),
            "peak_water_kw": round(peak_water_kw, 2),
            "peak_room_kw": round(peak_room_kw, 2),
            "peak_flow_l_min": round(peak_flow_l_min, 2),
            "annual_heat_covered_pct": round(heat_covered_pct, 1),
            "annual_useful_heat_mwh": round(annual_host.calorgrid_heat_kwh / 1000.0, 1),
            "annual_dumped_kwh": round(annual_cons.heat_dumped_kwh, 2),
            "co2_avoided_kg_yr": int(co2_avoided_kg_yr),
            "fit_status": fit_status,
            "fit_label": fit_label,
            "fit_note": fit_note,
        },
        "conservation": annual_cons.model_dump(),
        "tomorrow_conservation": tom_cons.model_dump(),
        "host_ledger": full_host_ledger.model_dump(),
        "operator_ledger": full_operator_ledger.model_dump(),
        "financials": {
            "has_existing_boiler": bool(has_existing_boiler),
            "server_host_cost_chf": round(server_host_cost_chf, 0),
            "host_server_electricity_chf": 0.0,
            "operator_server_capex_chf": round(operator_server_capex_chf, 0),
            "hardware_capex_chf": round(operator_server_capex_chf, 0),
            "boiler_retail_chf": round(boiler_retail_chf, 0),
            "boiler_subsidized_chf": round(boiler_subsidized_chf, 0),
            "boiler_discount_chf": round(boiler_discount_chf, 0),
            "boiler_cost_chf": round(boiler_cost_chf, 0),
            "tie_in_chf": round(tie_in_chf, 0),
            "install_chf": round(tie_in_chf, 0),
            "host_upfront_chf": round(host_upfront_chf, 0),
            "total_upfront_chf": round(host_upfront_chf, 0),
            "monthly_plan_chf": round(monthly_plan_chf, 0),
            "annual_elec_cost_chf": round(annual_op.calorgrid_electricity_cost_chf, 2),
            "annual_heat_savings_chf": round(annual_host.heating_bill_savings_chf, 2),
            "host_annual_heat_savings_chf": round(annual_host.heating_bill_savings_chf, 2),
            "annual_compute_revenue_chf": round(annual_op.calorgrid_compute_revenue_chf, 2),
            "net_annual_value_chf": round(annual_op.calorgrid_net_margin_chf, 2),
            "host_payback_years": host_payback_years,
            "payback_years": host_payback_years,
        },
        "forecast": forecast,
        "heatledger_tomorrow": {
            "indoor_target_c": indoor_rounded,
            "heat_target_pct": int(heat_target_pct),
            "recommended_kwh": recommended_tomorrow_kwh,
            "target_kwh": user_target_tomorrow_kwh,
            "delivered_kwh": delivered_tomorrow_kwh,
            "calorgrid_heat_kwh": tom_host.calorgrid_heat_kwh,
            "default_heater_heat_kwh": tom_host.default_heater_heat_kwh,
            "active_servers": active_servers_now,
            "installed_servers": count,
            "live_elec_kw": live_elec_kw,
            "live_water_kw": live_water_kw,
            "live_flow_l_min": live_flow_l_min,
            "supply_temp_c": supply_temp_c,
            "tank_soc_pct": int(tank_soc_pct),
            "tomorrow_elec_chf": tom_op.calorgrid_electricity_cost_chf,
            "tomorrow_heat_credit_chf": tom_host.heating_bill_savings_chf,
            "tomorrow_compute_rev_chf": tom_op.calorgrid_compute_revenue_chf,
            "tomorrow_net_chf": tom_op.calorgrid_net_margin_chf,
            "hourly": hourly_schedule,
            "processes": processes,
        },
    }
