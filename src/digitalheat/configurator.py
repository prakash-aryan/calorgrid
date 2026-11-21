"""End-user server configurator, HeatLedger simulation, and keyless Open-Meteo Swiss weather."""

from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import asdict, dataclass

from digitalheat.config import HEAT_RECOVERY_FRACTION

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
    ),
)

DEPLOY_BY_ID = {d.id: d for d in DEPLOYMENT_ARCHETYPES}
MODEL_BY_ID = {m.id: m for m in SERVER_MODELS}

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


def get_configurator_catalog() -> dict:
    return {
        "deployments": [asdict(d) for d in DEPLOYMENT_ARCHETYPES],
        "server_models": [asdict(m) for m in SERVER_MODELS],
    }


def _fallback_tomorrow_forecast(dep: DeploymentArchetype) -> dict:
    """Deterministic Swiss November fallback if upstream Open-Meteo is unreachable offline."""
    # Typical late-November Swiss diurnal profile scaled by elevation
    base = max(-5.0, 5.2 - (dep.elevation_m - 260.0) * 0.0055)
    hourly_t = [
        round(base - 2.4 * math.cos(2.0 * math.pi * (h - 14) / 24.0), 1)
        for h in range(24)
    ]
    return {
        "source": "Pinned Swiss Meteorological Fallback (Offline)",
        "live_api": False,
        "city": dep.city,
        "canton": dep.canton,
        "elevation_m": dep.elevation_m,
        "date": "Tomorrow",
        "condition": "Partly Cloudy" if base > 0.5 else "Alpine Snow Flurries",
        "weather_code": 2 if base > 0.5 else 71,
        "temp_min_c": min(hourly_t),
        "temp_max_c": max(hourly_t),
        "temp_mean_c": round(sum(hourly_t) / 24.0, 1),
        "hourly_temps_c": hourly_t,
    }


def fetch_live_swiss_forecast(deployment_id: str = "house_basel") -> dict:
    dep = DEPLOY_BY_ID.get(deployment_id, DEPLOYMENT_ARCHETYPES[0])
    cache_key = dep.id
    now = time.monotonic()
    cached = _FORECAST_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _FORECAST_TTL_S:
        return cached[1]

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
        # Index 1 in daily / hours 24..47 in hourly represent tomorrow (Europe/Zurich)
        daily = raw.get("daily", {})
        hourly = raw.get("hourly", {})
        t_dates = daily.get("time", [])
        tomorrow_date = t_dates[1] if len(t_dates) > 1 else (t_dates[0] if t_dates else "Tomorrow")
        temps_all = hourly.get("temperature_2m", [])
        hourly_t = [round(float(x), 1) for x in temps_all[24:48]] if len(temps_all) >= 48 else []
        if len(hourly_t) != 24:
            return _fallback_tomorrow_forecast(dep)
        wcodes = daily.get("weather_code") or [2, 2]
        wcode = int(wcodes[min(1, len(wcodes) - 1)])
        tmins = daily.get("temperature_2m_min") or [min(hourly_t)]
        tmaxs = daily.get("temperature_2m_max") or [max(hourly_t)]
        t_idx = min(1, max(0, len(t_dates) - 1))
        tmin = round(float(tmins[min(t_idx, len(tmins) - 1)]), 1)
        tmax = round(float(tmaxs[min(t_idx, len(tmaxs) - 1)]), 1)
        result = {
            "source": "Open-Meteo Live Forecast API (Keyless)",
            "live_api": True,
            "city": dep.city,
            "canton": dep.canton,
            "elevation_m": dep.elevation_m,
            "date": tomorrow_date,
            "condition": WMO_LABELS.get(wcode, "Swiss Alpine Conditions"),
            "weather_code": wcode,
            "temp_min_c": tmin,
            "temp_max_c": tmax,
            "temp_mean_c": round(sum(hourly_t) / 24.0, 1),
            "hourly_temps_c": hourly_t,
        }
        _FORECAST_CACHE[cache_key] = (now, result)
        return result
    except Exception:
        fb = _fallback_tomorrow_forecast(dep)
        _FORECAST_CACHE[cache_key] = (now, fb)
        return fb


def compute_configuration_quote(
    deployment_id: str = "house_basel",
    server_model_id: str = "hydronode_1kw",
    server_count: int = 4,
    heat_target_pct: int = 100,
    outdoor_temp_c: float | None = None,
    indoor_target_c: float = 21.0,
    has_existing_boiler: bool = False,
) -> dict:
    dep = DEPLOY_BY_ID.get(deployment_id, DEPLOYMENT_ARCHETYPES[0])
    mdl = MODEL_BY_ID.get(server_model_id, SERVER_MODELS[0])
    count = max(1, min(mdl.max_units, int(server_count)))
    target_frac = max(0.10, min(1.40, float(heat_target_pct) / 100.0))

    forecast = fetch_live_swiss_forecast(dep.id)
    ext_temp = forecast["temp_mean_c"] if outdoor_temp_c is None else float(outdoor_temp_c)

    # Peak hardware electrical & thermal output
    peak_elec_kw = count * mdl.electrical_kw
    peak_water_kw = peak_elec_kw * mdl.recovery_fraction
    peak_room_kw = peak_elec_kw * (1.0 - mdl.recovery_fraction)
    # m_dot [L/min] = Q [kW] * 60 / (4.186 * dT=10K)
    peak_flow_l_min = peak_water_kw * 60.0 / (4.186 * 10.0)

    # Annual economics (incorporating building demand ceiling & Swiss fiber migration)
    max_annual_water_kwh = peak_water_kw * 8760.0 * dep.summer_utilisation_factor
    annual_useful_heat_kwh = min(dep.annual_heat_demand_kwh * 0.96, max_annual_water_kwh)
    # Remaining capacity can serve Swiss fiber workloads at paired sinks (zero dumped heat!)
    annual_elec_kwh = peak_elec_kw * 8760.0 * min(0.96, max(dep.summer_utilisation_factor, 0.72))
    heat_covered_pct = min(100.0, 100.0 * annual_useful_heat_kwh / dep.annual_heat_demand_kwh)

    # Operator-owned compute CAPEX (CalorGrid supplies servers at CHF 0 to host)
    operator_server_capex_chf = count * mdl.unit_capex_chf
    server_host_cost_chf = 0.0
    boiler_retail_chf = dep.boiler_retail_chf
    boiler_subsidized_chf = dep.boiler_subsidized_chf
    boiler_discount_chf = max(0.0, boiler_retail_chf - boiler_subsidized_chf)
    boiler_cost_chf = 0.0 if has_existing_boiler else boiler_subsidized_chf
    tie_in_chf = dep.tie_in_base_chf + count * dep.tie_in_per_unit_chf
    host_upfront_chf = boiler_cost_chf + tie_in_chf
    monthly_plan_chf = host_upfront_chf / 36.0  # optional 3-year boiler installment

    annual_elec_cost_chf = annual_elec_kwh * dep.tariff_chf_per_kwh
    annual_heat_savings_chf = annual_useful_heat_kwh * dep.heat_credit_chf_per_kwh
    annual_compute_revenue_chf = annual_elec_kwh * mdl.compute_rate_chf_per_kwh
    net_annual_value_chf = (
        annual_compute_revenue_chf + annual_heat_savings_chf - annual_elec_cost_chf
    )
    host_payback_years = (
        round(host_upfront_chf / annual_heat_savings_chf, 1)
        if annual_heat_savings_chf > 50.0
        else None
    )
    payback_years = host_payback_years
    # Compared against gas boiler baseline (0.228 kg CO2 / kWh_th)
    co2_avoided_kg_yr = round(annual_useful_heat_kwh * 0.228, 0)

    # Tomorrow live interactive HeatLedger plan based on live weather + user slider!
    # Daily heating demand scales with heating degree days relative to indoor_target_c + base DHW
    base_dhw_kwh = max(12.0, dep.annual_heat_demand_kwh * 0.14 / 365.0)
    hdd = max(0.0, (indoor_target_c - 3.0) - ext_temp)
    nominal_space_kwh = (dep.annual_heat_demand_kwh * 0.86 / 365.0) * (hdd / 9.5)
    recommended_tomorrow_kwh = round((base_dhw_kwh + nominal_space_kwh), 1)
    user_target_tomorrow_kwh = round(recommended_tomorrow_kwh * target_frac, 1)

    max_daily_water_kwh = peak_water_kw * 24.0
    delivered_tomorrow_kwh = min(user_target_tomorrow_kwh, max_daily_water_kwh)
    active_load_ratio = min(1.0, delivered_tomorrow_kwh / max(max_daily_water_kwh, 0.1))
    active_servers_now = max(0, min(count, int(round(count * active_load_ratio))))
    if active_load_ratio > 0.08 and active_servers_now == 0:
        active_servers_now = 1
    live_elec_kw = round(
        active_servers_now * mdl.electrical_kw
        + (count - active_servers_now) * mdl.idle_kw * 0.15,
        2,
    )
    live_water_kw = round(active_servers_now * mdl.electrical_kw * mdl.recovery_fraction, 2)
    live_flow_l_min = round(live_water_kw * 60.0 / (4.186 * 10.0), 2)
    tank_soc_pct = round(min(98.0, max(22.0, 34.0 + (1.0 - active_load_ratio) * 58.0)), 0)
    supply_temp_c = round(
        min(mdl.supply_temp_c, 36.0 + active_load_ratio * (mdl.supply_temp_c - 34.0)), 1
    )

    tomorrow_elec_kwh = round(delivered_tomorrow_kwh / max(mdl.recovery_fraction, 0.5), 1)
    tomorrow_elec_chf = round(tomorrow_elec_kwh * dep.tariff_chf_per_kwh, 2)
    tomorrow_heat_credit_chf = round(delivered_tomorrow_kwh * dep.heat_credit_chf_per_kwh, 2)
    tomorrow_compute_rev_chf = round(tomorrow_elec_kwh * mdl.compute_rate_chf_per_kwh, 2)
    tomorrow_net_chf = round(
        tomorrow_compute_rev_chf + tomorrow_heat_credit_chf - tomorrow_elec_chf, 2
    )

    # Live 24-hour forecast schedule synced to tomorrow's hourly Open-Meteo temperatures
    hourly_schedule = []
    for h, t_h in enumerate(forecast["hourly_temps_c"]):
        dhw_h = (base_dhw_kwh / 24.0) * (1.8 if h in (7, 8, 19, 20) else 0.75)
        hdd_h = max(0.0, (indoor_target_c - 3.0) - t_h)
        sp_h = (dep.annual_heat_demand_kwh * 0.86 / 8760.0) * (hdd_h / 9.5)
        dem_h = round((dhw_h + sp_h) * target_frac, 2)
        srv_h = min(peak_water_kw, dem_h)
        hourly_schedule.append({
            "hour": h,
            "temp_c": t_h,
            "demand_kw": dem_h,
            "server_heat_kw": round(srv_h, 2),
        })

    # Live running processes roster dynamically reflecting active_servers_now vs count
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
    processes = []
    for idx in range(count):
        proc, desc, kind, pid = process_templates[idx % len(process_templates)]
        if idx < active_servers_now:
            status = "RUNNING"
            cpu_pct = round(84.0 + (idx % 3) * 4.5, 1)
            heat_kw = round(mdl.electrical_kw * mdl.recovery_fraction, 2)
        elif active_load_ratio < 0.45:
            status = "FIBER_OFFLOAD"
            cpu_pct = 0.0
            heat_kw = 0.0
        else:
            status = "SIGSTOP_STANDBY"
            cpu_pct = 0.0
            heat_kw = 0.0
        processes.append({
            "unit": idx + 1,
            "pid": pid + idx,
            "process": proc,
            "workload": desc,
            "category": kind,
            "status": status,
            "cpu_pct": cpu_pct,
            "heat_kw": heat_kw,
        })

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

    return {
        "deployment": asdict(dep),
        "server_model": asdict(mdl),
        "server_count": count,
        "sizing": {
            "peak_elec_kw": round(peak_elec_kw, 2),
            "peak_water_kw": round(peak_water_kw, 2),
            "peak_room_kw": round(peak_room_kw, 2),
            "peak_flow_l_min": round(peak_flow_l_min, 2),
            "annual_heat_covered_pct": round(heat_covered_pct, 1),
            "annual_useful_heat_mwh": round(annual_useful_heat_kwh / 1000.0, 1),
            "annual_dumped_kwh": 0.0,
            "co2_avoided_kg_yr": int(co2_avoided_kg_yr),
            "fit_status": fit_status,
            "fit_label": fit_label,
            "fit_note": fit_note,
        },
        "financials": {
            "has_existing_boiler": bool(has_existing_boiler),
            "server_host_cost_chf": round(server_host_cost_chf, 0),
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
            "annual_elec_cost_chf": round(annual_elec_cost_chf, 0),
            "annual_heat_savings_chf": round(annual_heat_savings_chf, 0),
            "host_annual_heat_savings_chf": round(annual_heat_savings_chf, 0),
            "annual_compute_revenue_chf": round(annual_compute_revenue_chf, 0),
            "net_annual_value_chf": round(net_annual_value_chf, 0),
            "host_payback_years": host_payback_years,
            "payback_years": payback_years,
        },
        "forecast": forecast,
        "heatledger_tomorrow": {
            "indoor_target_c": round(indoor_target_c, 1),
            "heat_target_pct": int(heat_target_pct),
            "recommended_kwh": recommended_tomorrow_kwh,
            "target_kwh": user_target_tomorrow_kwh,
            "delivered_kwh": round(delivered_tomorrow_kwh, 1),
            "active_servers": active_servers_now,
            "installed_servers": count,
            "live_elec_kw": live_elec_kw,
            "live_water_kw": live_water_kw,
            "live_flow_l_min": live_flow_l_min,
            "supply_temp_c": supply_temp_c,
            "tank_soc_pct": int(tank_soc_pct),
            "tomorrow_elec_chf": tomorrow_elec_chf,
            "tomorrow_heat_credit_chf": tomorrow_heat_credit_chf,
            "tomorrow_compute_rev_chf": tomorrow_compute_rev_chf,
            "tomorrow_net_chf": tomorrow_net_chf,
            "hourly": hourly_schedule,
            "processes": processes,
        },
    }
