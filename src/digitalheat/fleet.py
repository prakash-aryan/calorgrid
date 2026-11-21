"""Switzerland-wide 14-node distributed thermal-compute fleet and workload migration engine.

Models 14 distinct building digital twins across 10 Swiss cantons (BS, ZH, BE, TG, GE, VS, LU,
SG, GR, TI) using the pinned Open-Meteo 2025 hourly weather series (Basel and Davos) with
elevation lapse calibration, ElCom-grounded cantonal electricity tariffs, and strict hourly
thermal conservation. Demonstrates how flexible batch compute (archive OCR, IIIF embeddings,
local LLM batch inference) pauses via SIGSTOP in warm residential basements when thermal tanks
fill and migrates across Swiss fiber to active thermal sinks (municipal pools, dairy farms,
hydroponic greenhouses, fruit dryers, commercial laundries, hospitals and alpine spas).
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from digitalheat.config import (
    DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H,
    HEAT_PUMP_REF_COP,
    HEAT_RECOVERY_FRACTION,
    SERVER_ELECTRICAL_POWER_KW,
    TANK_TEMP_MIN_C,
    TANK_TEMP_SPREAD_K,
)
from digitalheat.data_loader import load_weather_zurich
from digitalheat.telemetry_log import log_event


@dataclass(frozen=True)
class SwissTwinNode:
    """Static definition of a Swiss building digital twin in the CalorGrid network."""

    node_id: str
    name: str
    city: str
    canton: str
    region: str
    category: str  # residential | farm | pool | commercial | alpine
    archetype: str
    lat: float
    lon: float
    elevation_m: int
    servers_installed: int
    tank_capacity_kwh: float
    target_supply_c: int
    tariff_chf_per_kwh: float
    climate_base: str  # basel | davos
    temp_offset_c: float
    base_heating_ua_kw_per_k: float
    dhw_daily_kwh: float
    process_sink_type: str
    process_base_kw: float
    process_summer_kw: float
    workload_primary: str
    glb_scene_key: str | None
    twin_style: str
    accent_hex: str
    summary: str


SWISS_FLEET_NODES: tuple[SwissTwinNode, ...] = (
    SwissTwinNode(
        node_id="basel_klingental",
        name="Basel Klingental MFH",
        city="Basel",
        canton="BS",
        region="Nordwestschweiz",
        category="residential",
        archetype="Historic 6-Unit Multi-Family Home",
        lat=47.561,
        lon=7.590,
        elevation_m=255,
        servers_installed=4,
        tank_capacity_kwh=35.0,
        target_supply_c=55,
        tariff_chf_per_kwh=0.3325,
        climate_base="basel",
        temp_offset_c=0.0,
        base_heating_ua_kw_per_k=0.39,
        dhw_daily_kwh=12.0,
        process_sink_type="residential_space_dhw",
        process_base_kw=0.0,
        process_summer_kw=0.0,
        workload_primary="archive_ocr",
        glb_scene_key="context_neighborhood",
        twin_style="basel_mfh",
        accent_hex="#f97316",
        summary="Reference Basel 6-unit house with 4 x 1 kW liquid-cooled rack and 1,000 L tank.",
    ),
    SwissTwinNode(
        node_id="basel_st_jakob",
        name="Gartenbad St. Jakob",
        city="Basel",
        canton="BS",
        region="Nordwestschweiz",
        category="pool",
        archetype="Municipal Outdoor Swimming Pool",
        lat=47.539,
        lon=7.621,
        elevation_m=267,
        servers_installed=16,
        tank_capacity_kwh=160.0,
        target_supply_c=28,
        tariff_chf_per_kwh=0.278,
        climate_base="basel",
        temp_offset_c=0.0,
        base_heating_ua_kw_per_k=0.12,
        dhw_daily_kwh=28.0,
        process_sink_type="summer_pool",
        process_base_kw=3.2,
        process_summer_kw=18.5,
        workload_primary="iiif_embeddings",
        glb_scene_key="verbund_st_jakob",
        twin_style="st_jakob_pool",
        accent_hex="#0ea5e9",
        summary=(
            "Olympic outdoor Freibad basin absorbing migrated residential compute heat "
            "from May to September."
        ),
    ),
    SwissTwinNode(
        node_id="basel_erdsonde",
        name="Dreispitz Minergie Erdsonde",
        city="Muttenz / Basel",
        canton="BS",
        region="Nordwestschweiz",
        category="residential",
        archetype="Minergie Townhouse + Borehole Field",
        lat=47.531,
        lon=7.638,
        elevation_m=278,
        servers_installed=6,
        tank_capacity_kwh=55.0,
        target_supply_c=38,
        tariff_chf_per_kwh=0.312,
        climate_base="basel",
        temp_offset_c=-0.2,
        base_heating_ua_kw_per_k=0.34,
        dhw_daily_kwh=16.0,
        process_sink_type="geothermal_regen",
        process_base_kw=0.4,
        process_summer_kw=5.6,
        workload_primary="llm_batch",
        glb_scene_key=None,
        twin_style="minergie_erdsonde",
        accent_hex="#06b6d4",
        summary="Low-temperature radiant home paired with summer geothermal borehole regeneration.",
    ),
    SwissTwinNode(
        node_id="riehen_villa",
        name="Riehen Bruderholz Villa",
        city="Riehen",
        canton="BS",
        region="Nordwestschweiz",
        category="residential",
        archetype="Suburban Single-Family Home",
        lat=47.585,
        lon=7.652,
        elevation_m=315,
        servers_installed=2,
        tank_capacity_kwh=22.0,
        target_supply_c=42,
        tariff_chf_per_kwh=0.3325,
        climate_base="basel",
        temp_offset_c=-0.4,
        base_heating_ua_kw_per_k=0.22,
        dhw_daily_kwh=7.5,
        process_sink_type="residential_space_dhw",
        process_base_kw=0.0,
        process_summer_kw=0.0,
        workload_primary="archive_ocr",
        glb_scene_key=None,
        twin_style="swiss_villa",
        accent_hex="#fb923c",
        summary="Compact 2 kW basement micro-rack feeding low-temperature underfloor loops.",
    ),
    SwissTwinNode(
        node_id="zurich_oerlikon",
        name="Zurich Oerlikon Co-op Block",
        city="Zurich",
        canton="ZH",
        region="Zurich",
        category="residential",
        archetype="18-Unit Urban Cooperative Block",
        lat=47.412,
        lon=8.544,
        elevation_m=438,
        servers_installed=12,
        tank_capacity_kwh=110.0,
        target_supply_c=60,
        tariff_chf_per_kwh=0.294,
        climate_base="basel",
        temp_offset_c=-1.1,
        base_heating_ua_kw_per_k=1.18,
        dhw_daily_kwh=48.0,
        process_sink_type="urban_mfh_dhw",
        process_base_kw=1.2,
        process_summer_kw=0.8,
        workload_primary="llm_batch",
        glb_scene_key=None,
        twin_style="zurich_coop",
        accent_hex="#f59e0b",
        summary=(
            "Dense Zurich cooperative housing with shared central hot-water riser and 12 kW rack."
        ),
    ),
    SwissTwinNode(
        node_id="bern_matte",
        name="Bern Matte Aare Rowhouse",
        city="Bern",
        canton="BE",
        region="Espace Mittelland",
        category="residential",
        archetype="Historic Sandstone Rowhouse",
        lat=46.948,
        lon=7.456,
        elevation_m=512,
        servers_installed=4,
        tank_capacity_kwh=38.0,
        target_supply_c=58,
        tariff_chf_per_kwh=0.318,
        climate_base="basel",
        temp_offset_c=-1.6,
        base_heating_ua_kw_per_k=0.44,
        dhw_daily_kwh=13.0,
        process_sink_type="residential_space_dhw",
        process_base_kw=0.0,
        process_summer_kw=0.0,
        workload_primary="archive_ocr",
        glb_scene_key=None,
        twin_style="bern_rowhouse",
        accent_hex="#ea580c",
        summary="Heritage sandstone rowhouse along the Aare running winter heat-led OCR jobs.",
    ),
    SwissTwinNode(
        node_id="emmental_dairy",
        name="Emmental Schaukaserei Dairy",
        city="Affoltern i.E.",
        canton="BE",
        region="Espace Mittelland",
        category="farm",
        archetype="Alpine Cheese Dairy & Farmstead",
        lat=47.058,
        lon=7.734,
        elevation_m=742,
        servers_installed=10,
        tank_capacity_kwh=95.0,
        target_supply_c=62,
        tariff_chf_per_kwh=0.282,
        climate_base="basel",
        temp_offset_c=-2.8,
        base_heating_ua_kw_per_k=0.28,
        dhw_daily_kwh=22.0,
        process_sink_type="dairy_pasteurisation",
        process_base_kw=7.8,
        process_summer_kw=9.4,
        workload_primary="iiif_embeddings",
        glb_scene_key=None,
        twin_style="emmental_dairy",
        accent_hex="#16a34a",
        summary=(
            "Working Emmental cheese dairy preheating milk pasteurisation and washdown "
            "water 365 days/year."
        ),
    ),
    SwissTwinNode(
        node_id="thurgau_greenhouse",
        name="Thurgau Bodensee Glasshouse",
        city="Kreuzlingen",
        canton="TG",
        region="Ostschweiz",
        category="farm",
        archetype="Hydroponic Tomato Glasshouse Farm",
        lat=47.645,
        lon=9.175,
        elevation_m=415,
        servers_installed=18,
        tank_capacity_kwh=180.0,
        target_supply_c=38,
        tariff_chf_per_kwh=0.268,
        climate_base="basel",
        temp_offset_c=-1.0,
        base_heating_ua_kw_per_k=0.95,
        dhw_daily_kwh=10.0,
        process_sink_type="greenhouse_rootzone",
        process_base_kw=9.2,
        process_summer_kw=12.4,
        workload_primary="llm_batch",
        glb_scene_key=None,
        twin_style="thurgau_greenhouse",
        accent_hex="#22c55e",
        summary=(
            "3-bay hydroponic greenhouse using 38 C server loop water for root-zone and "
            "night heating."
        ),
    ),
    SwissTwinNode(
        node_id="geneva_carouge",
        name="Geneva Carouge Residence",
        city="Carouge",
        canton="GE",
        region="Region Lemanique",
        category="residential",
        archetype="Romandie Mixed-Use Residence",
        lat=46.183,
        lon=6.140,
        elevation_m=385,
        servers_installed=6,
        tank_capacity_kwh=52.0,
        target_supply_c=52,
        tariff_chf_per_kwh=0.326,
        climate_base="basel",
        temp_offset_c=0.8,
        base_heating_ua_kw_per_k=0.52,
        dhw_daily_kwh=20.0,
        process_sink_type="residential_space_dhw",
        process_base_kw=0.0,
        process_summer_kw=0.0,
        workload_primary="archive_ocr",
        glb_scene_key=None,
        twin_style="geneva_block",
        accent_hex="#f97316",
        summary=(
            "Mild Lake Geneva residential block that offloads summer workloads east to "
            "Valais and Bern."
        ),
    ),
    SwissTwinNode(
        node_id="valais_dryer",
        name="Valais Sion Agro-Dryer & Winery",
        city="Sion",
        canton="VS",
        region="Region Lemanique",
        category="farm",
        archetype="Fruit Dehydration & Winery Farm",
        lat=46.233,
        lon=7.360,
        elevation_m=512,
        servers_installed=8,
        tank_capacity_kwh=75.0,
        target_supply_c=55,
        tariff_chf_per_kwh=0.258,
        climate_base="basel",
        temp_offset_c=0.4,
        base_heating_ua_kw_per_k=0.20,
        dhw_daily_kwh=14.0,
        process_sink_type="agricultural_drying",
        process_base_kw=4.2,
        process_summer_kw=8.8,
        workload_primary="iiif_embeddings",
        glb_scene_key=None,
        twin_style="valais_winery",
        accent_hex="#84cc16",
        summary=(
            "Apricot/herb drying kilns and barrel sanitation absorbing peak summer compute heat."
        ),
    ),
    SwissTwinNode(
        node_id="lucerne_bakery",
        name="Lucerne Kriens Bakery & Laundry",
        city="Kriens",
        canton="LU",
        region="Zentralschweiz",
        category="commercial",
        archetype="Commercial Process Hot-Water Hall",
        lat=47.035,
        lon=8.278,
        elevation_m=490,
        servers_installed=14,
        tank_capacity_kwh=135.0,
        target_supply_c=65,
        tariff_chf_per_kwh=0.274,
        climate_base="basel",
        temp_offset_c=-1.5,
        base_heating_ua_kw_per_k=0.36,
        dhw_daily_kwh=30.0,
        process_sink_type="commercial_laundry",
        process_base_kw=9.6,
        process_summer_kw=10.2,
        workload_primary="llm_batch",
        glb_scene_key=None,
        twin_style="lucerne_industrial",
        accent_hex="#a855f7",
        summary="Daily 04:00 to 18:00 laundry wash preheat and bakery steam-boiler feedwater loop.",
    ),
    SwissTwinNode(
        node_id="stgallen_hospital",
        name="St. Gallen Hospital DHW Wing",
        city="St. Gallen",
        canton="SG",
        region="Ostschweiz",
        category="commercial",
        archetype="Healthcare Sterilisation & DHW Wing",
        lat=47.430,
        lon=9.388,
        elevation_m=675,
        servers_installed=20,
        tank_capacity_kwh=210.0,
        target_supply_c=58,
        tariff_chf_per_kwh=0.264,
        climate_base="basel",
        temp_offset_c=-2.4,
        base_heating_ua_kw_per_k=0.72,
        dhw_daily_kwh=180.0,
        process_sink_type="hospital_24_7",
        process_base_kw=13.5,
        process_summer_kw=14.0,
        workload_primary="llm_batch",
        glb_scene_key=None,
        twin_style="stgallen_hospital",
        accent_hex="#ec4899",
        summary=(
            "Continuous 24/7 clinical hot-water preheat delivering >94% annual server utilisation."
        ),
    ),
    SwissTwinNode(
        node_id="davos_alpine",
        name="Davos Platz Alpine Spa Hotel",
        city="Davos",
        canton="GR",
        region="Ostschweiz / Alpen",
        category="alpine",
        archetype="High-Altitude Alpine Resort & Spa",
        lat=46.802,
        lon=9.836,
        elevation_m=1560,
        servers_installed=12,
        tank_capacity_kwh=125.0,
        target_supply_c=48,
        tariff_chf_per_kwh=0.289,
        climate_base="davos",
        temp_offset_c=0.0,
        base_heating_ua_kw_per_k=0.78,
        dhw_daily_kwh=62.0,
        process_sink_type="alpine_spa_pool",
        process_base_kw=4.8,
        process_summer_kw=6.8,
        workload_primary="iiif_embeddings",
        glb_scene_key="verbund_davos",
        twin_style="davos_spa",
        accent_hex="#38bdf8",
        summary=(
            "1,560 m Alpine climate (291 heating days) plus year-round indoor thermal spa basin."
        ),
    ),
    SwissTwinNode(
        node_id="lugano_lakeside",
        name="Lugano Castagnola Residence",
        city="Lugano",
        canton="TI",
        region="Ticino",
        category="residential",
        archetype="Southern Ticino Lakeside Apartment",
        lat=46.003,
        lon=8.976,
        elevation_m=330,
        servers_installed=4,
        tank_capacity_kwh=34.0,
        target_supply_c=50,
        tariff_chf_per_kwh=0.315,
        climate_base="basel",
        temp_offset_c=2.9,
        base_heating_ua_kw_per_k=0.34,
        dhw_daily_kwh=12.0,
        process_sink_type="residential_space_dhw",
        process_base_kw=0.0,
        process_summer_kw=0.0,
        workload_primary="archive_ocr",
        glb_scene_key=None,
        twin_style="lugano_villa",
        accent_hex="#fbbf24",
        summary=(
            "Warm Ticino lakeside residence exporting summer compute load north across "
            "Gotthard fiber."
        ),
    ),
)

NODE_BY_ID: dict[str, SwissTwinNode] = {n.node_id: n for n in SWISS_FLEET_NODES}


@dataclass(frozen=True)
class CanonicalSiteSpec:
    """Unified site metadata across 14 Swiss Twin nodes and 4 Verbund/industrial sinks."""

    site_id: str
    name: str
    city: str
    canton: str
    region: str
    category: str
    lat: float
    lon: float
    elevation_m: int
    climate_base: Literal["basel", "davos"]
    temp_offset_c: float
    base_heating_ua_kw_per_k: float
    dhw_daily_kwh: float
    tank_capacity_kwh: float
    target_supply_c: int
    tariff_chf_per_kwh: float
    sink_type: str
    process_base_kw: float
    process_summer_kw: float
    active_months: tuple[int, ...]
    is_summer_sink: bool
    is_year_round_sink: bool
    heater_type: Literal["heat_pump", "gas_boiler", "electric_backup"]
    verbund_scene_id: str | None = None
    workload_primary: str = "llm_batch"
    summary: str = ""


_ALL_MONTHS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
_WINTER_MONTHS: tuple[int, ...] = (1, 2, 3, 4, 10, 11, 12)
_SUMMER_MONTHS: tuple[int, ...] = (5, 6, 7, 8, 9)


def _node_to_canonical(node: SwissTwinNode) -> CanonicalSiteSpec:
    if node.node_id == "basel_st_jakob":
        return CanonicalSiteSpec(
            site_id=node.node_id,
            name=node.name,
            city=node.city,
            canton=node.canton,
            region=node.region,
            category=node.category,
            lat=node.lat,
            lon=node.lon,
            elevation_m=node.elevation_m,
            climate_base="basel",
            temp_offset_c=node.temp_offset_c,
            base_heating_ua_kw_per_k=node.base_heating_ua_kw_per_k,
            dhw_daily_kwh=node.dhw_daily_kwh,
            tank_capacity_kwh=node.tank_capacity_kwh,
            target_supply_c=node.target_supply_c,
            tariff_chf_per_kwh=0.25267,
            sink_type=node.process_sink_type,
            process_base_kw=node.process_base_kw,
            process_summer_kw=node.process_summer_kw,
            active_months=_SUMMER_MONTHS,
            is_summer_sink=True,
            is_year_round_sink=False,
            heater_type="gas_boiler",
            verbund_scene_id="basel_pool_st_jakob",
            workload_primary=node.workload_primary,
            summary=node.summary,
        )
    if node.node_id == "basel_erdsonde":
        return CanonicalSiteSpec(
            site_id=node.node_id,
            name=node.name,
            city=node.city,
            canton=node.canton,
            region=node.region,
            category=node.category,
            lat=node.lat,
            lon=node.lon,
            elevation_m=node.elevation_m,
            climate_base="basel",
            temp_offset_c=node.temp_offset_c,
            base_heating_ua_kw_per_k=node.base_heating_ua_kw_per_k,
            dhw_daily_kwh=node.dhw_daily_kwh,
            tank_capacity_kwh=node.tank_capacity_kwh,
            target_supply_c=node.target_supply_c,
            tariff_chf_per_kwh=0.29128,
            sink_type=node.process_sink_type,
            process_base_kw=node.process_base_kw,
            process_summer_kw=7.5,
            active_months=_ALL_MONTHS,
            is_summer_sink=True,
            is_year_round_sink=True,
            heater_type="heat_pump",
            verbund_scene_id="basel_erdsonde",
            workload_primary=node.workload_primary,
            summary=node.summary,
        )
    if node.node_id == "davos_alpine":
        return CanonicalSiteSpec(
            site_id=node.node_id,
            name=node.name,
            city=node.city,
            canton=node.canton,
            region=node.region,
            category=node.category,
            lat=node.lat,
            lon=node.lon,
            elevation_m=node.elevation_m,
            climate_base="davos",
            temp_offset_c=node.temp_offset_c,
            base_heating_ua_kw_per_k=node.base_heating_ua_kw_per_k,
            dhw_daily_kwh=node.dhw_daily_kwh,
            tank_capacity_kwh=node.tank_capacity_kwh,
            target_supply_c=node.target_supply_c,
            tariff_chf_per_kwh=node.tariff_chf_per_kwh,
            sink_type=node.process_sink_type,
            process_base_kw=node.process_base_kw,
            process_summer_kw=node.process_summer_kw,
            active_months=_WINTER_MONTHS,
            is_summer_sink=False,
            is_year_round_sink=False,
            heater_type="gas_boiler",
            verbund_scene_id="davos_alpine",
            workload_primary=node.workload_primary,
            summary=node.summary,
        )

    is_year_round_proc = node.process_sink_type in (
        "dairy_pasteurisation",
        "greenhouse_rootzone",
        "commercial_laundry",
        "hospital_24_7",
    )
    is_summer_proc = is_year_round_proc or node.process_sink_type == "agricultural_drying"
    return CanonicalSiteSpec(
        site_id=node.node_id,
        name=node.name,
        city=node.city,
        canton=node.canton,
        region=node.region,
        category=node.category,
        lat=node.lat,
        lon=node.lon,
        elevation_m=node.elevation_m,
        climate_base="davos" if node.climate_base == "davos" else "basel",
        temp_offset_c=node.temp_offset_c,
        base_heating_ua_kw_per_k=node.base_heating_ua_kw_per_k,
        dhw_daily_kwh=node.dhw_daily_kwh,
        tank_capacity_kwh=node.tank_capacity_kwh,
        target_supply_c=node.target_supply_c,
        tariff_chf_per_kwh=node.tariff_chf_per_kwh,
        sink_type=node.process_sink_type,
        process_base_kw=node.process_base_kw,
        process_summer_kw=node.process_summer_kw,
        active_months=_ALL_MONTHS if is_summer_proc else _WINTER_MONTHS,
        is_summer_sink=is_summer_proc,
        is_year_round_sink=is_year_round_proc,
        heater_type="gas_boiler",
        verbund_scene_id=None,
        workload_primary=node.workload_primary,
        summary=node.summary,
    )


_EXTRA_VERBUND_SITES: tuple[CanonicalSiteSpec, ...] = (
    CanonicalSiteSpec(
        site_id="sole_uno",
        name="Sole Uno Wellness Thermal Bath",
        city="Rheinfelden",
        canton="AG",
        region="Nordwestschweiz",
        category="spa",
        lat=47.5540,
        lon=7.7980,
        elevation_m=280,
        climate_base="basel",
        temp_offset_c=0.0,
        base_heating_ua_kw_per_k=0.35,
        dhw_daily_kwh=48.0,
        tank_capacity_kwh=220.0,
        target_supply_c=34,
        tariff_chf_per_kwh=0.25267,
        sink_type="indoor_thermal_spa",
        process_base_kw=22.0,
        process_summer_kw=24.0,
        active_months=_ALL_MONTHS,
        is_summer_sink=True,
        is_year_round_sink=True,
        heater_type="gas_boiler",
        verbund_scene_id="sole_uno",
        workload_primary="llm_batch",
        summary=(
            "Year-round indoor thermal saltwater bath absorbing 34-45 C server heat 365 days/yr."
        ),
    ),
    CanonicalSiteSpec(
        site_id="ara_basel_biogas",
        name="ARA Basel ProRheno Biogas Digester",
        city="Basel",
        canton="BS",
        region="Nordwestschweiz",
        category="biogas",
        lat=47.5845,
        lon=7.5880,
        elevation_m=248,
        climate_base="basel",
        temp_offset_c=0.2,
        base_heating_ua_kw_per_k=0.10,
        dhw_daily_kwh=12.0,
        tank_capacity_kwh=260.0,
        target_supply_c=42,
        tariff_chf_per_kwh=0.25267,
        sink_type="biogas_digester",
        process_base_kw=28.0,
        process_summer_kw=28.0,
        active_months=_ALL_MONTHS,
        is_summer_sink=True,
        is_year_round_sink=True,
        heater_type="gas_boiler",
        verbund_scene_id="ara_basel_biogas",
        workload_primary="llm_batch",
        summary="Constant 24/7 mesophilic wastewater digester heating at 37-42 C on C6 tariff.",
    ),
    CanonicalSiteSpec(
        site_id="hotel_laundry_dhw",
        name="Basel Commercial Laundry & Hotel DHW",
        city="Basel",
        canton="BS",
        region="Nordwestschweiz",
        category="commercial",
        lat=47.5495,
        lon=7.5890,
        elevation_m=256,
        climate_base="basel",
        temp_offset_c=0.0,
        base_heating_ua_kw_per_k=0.30,
        dhw_daily_kwh=60.0,
        tank_capacity_kwh=140.0,
        target_supply_c=60,
        tariff_chf_per_kwh=0.26014,
        sink_type="commercial_laundry",
        process_base_kw=11.0,
        process_summer_kw=12.0,
        active_months=_ALL_MONTHS,
        is_summer_sink=True,
        is_year_round_sink=True,
        heater_type="gas_boiler",
        verbund_scene_id="hotel_laundry_dhw",
        workload_primary="archive_ocr",
        summary="Commercial hotel hot-water and laundry preheat loop (45-60 C).",
    ),
    CanonicalSiteSpec(
        site_id="brewery_dairy_cip",
        name="Baselbiet Brewery & Dairy CIP",
        city="Liestal",
        canton="BL",
        region="Nordwestschweiz",
        category="industrial",
        lat=47.4840,
        lon=7.7340,
        elevation_m=327,
        climate_base="basel",
        temp_offset_c=-0.3,
        base_heating_ua_kw_per_k=0.25,
        dhw_daily_kwh=32.0,
        tank_capacity_kwh=120.0,
        target_supply_c=62,
        tariff_chf_per_kwh=0.26014,
        sink_type="cip_preheat",
        process_base_kw=9.0,
        process_summer_kw=11.0,
        active_months=_ALL_MONTHS,
        is_summer_sink=True,
        is_year_round_sink=False,
        heater_type="gas_boiler",
        verbund_scene_id="brewery_dairy_cip",
        workload_primary="iiif_embeddings",
        summary="Mon-Sat 06:00-18:00 brewery and dairy CIP preheat sink.",
    ),
)

CANONICAL_SITES: dict[str, CanonicalSiteSpec] = {
    **{n.node_id: _node_to_canonical(n) for n in SWISS_FLEET_NODES},
    **{s.site_id: s for s in _EXTRA_VERBUND_SITES},
}

SITE_ALIASES: dict[str, str] = {
    "basel_pool_st_jakob": "basel_st_jakob",
    "municipal_pool": "basel_st_jakob",
    "house_basel": "basel_klingental",
    "house_borehole": "basel_erdsonde",
    "alpine_chalet": "davos_alpine",
    "dairy_farm": "emmental_dairy",
    "glasshouse_farm": "thurgau_greenhouse",
    "commercial_bakery": "lucerne_bakery",
    "hospital_wing": "stgallen_hospital",
    "basel_ref": "basel_klingental",
    "basel_mfh": "basel_klingental",
    "riehen_sfh": "riehen_villa",
    "liestal_mfh": "brewery_dairy_cip",
    "aarau_pool": "sole_uno",
    "zurich_mfh": "zurich_oerlikon",
    "davos_hotel": "davos_alpine",
    "stmoritz_sfh": "davos_alpine",
    "zermatt_hotel": "davos_alpine",
    "lugano_mfh": "lugano_lakeside",
}


def resolve_site_id(raw_id: str) -> str:
    """Resolve a canonical site ID or known alias; raise ValueError on unknown IDs."""
    key = raw_id.strip()
    if key in CANONICAL_SITES:
        return key
    if key in SITE_ALIASES:
        return SITE_ALIASES[key]
    raise ValueError(f"Unknown site_id '{raw_id}'")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r_km * math.asin(min(1.0, math.sqrt(a)))


def _diurnal_dhw_weight(hour: int) -> float:
    """Normalized hourly domestic hot-water profile (sums to 1.0 over 24 hours)."""
    weights = (
        0.015,
        0.012,
        0.010,
        0.012,
        0.018,
        0.036,
        0.068,
        0.088,
        0.072,
        0.048,
        0.038,
        0.044,
        0.052,
        0.042,
        0.034,
        0.036,
        0.046,
        0.064,
        0.082,
        0.076,
        0.056,
        0.042,
        0.032,
        0.025,
    )
    return weights[hour % 24] / sum(weights)


def _hourly_thermal_demand_kw(
    node: SwissTwinNode,
    outdoor_temp_c: float,
    day_of_year: int,
    hour: int,
) -> float:
    """Compute hourly thermal demand (kW_th) for a Swiss building archetype."""
    t_eff = outdoor_temp_c + node.temp_offset_c
    # Space heating below 15 C balance point
    space_kw = max(0.0, (15.0 - t_eff) * node.base_heating_ua_kw_per_k)
    dhw_kw = node.dhw_daily_kwh * _diurnal_dhw_weight(hour)

    # Seasonal envelope (0 in Jan, 1 in mid-Jul)
    summer_wave = 0.5 - 0.5 * math.cos(2.0 * math.pi * (day_of_year - 15) / 365.0)
    is_summer_pool = 121 <= day_of_year <= 265

    proc_kw = node.process_base_kw
    if node.process_sink_type == "summer_pool":
        if is_summer_pool:
            # Higher makeup heat at night/morning when ambient air cools the pool basin
            night_boost = 1.18 if (hour < 9 or hour >= 20) else 0.88
            proc_kw = node.process_summer_kw * night_boost
        else:
            proc_kw = 1.8  # indoor changing rooms / winter facility base
    elif node.process_sink_type == "geothermal_regen":
        proc_kw = node.process_base_kw + node.process_summer_kw * (summer_wave**1.4)
    elif node.process_sink_type == "dairy_pasteurisation":
        # Peak morning milking (05-11) and afternoon cheesemaking (14-18)
        shift = 1.28 if (5 <= hour <= 11 or 14 <= hour <= 18) else 0.78
        proc_kw = (
            node.process_base_kw + (node.process_summer_kw - node.process_base_kw) * summer_wave
        ) * shift
    elif node.process_sink_type == "greenhouse_rootzone":
        # Night + shoulder-season heating peak, steady daytime root-zone nutrient loop
        night_factor = 1.32 if (hour < 8 or hour >= 19) else 0.72
        shoulder = 1.0 - 0.35 * max(0.0, summer_wave - 0.65)
        proc_kw = (node.process_base_kw + 3.2 * shoulder) * night_factor
    elif node.process_sink_type == "agricultural_drying":
        # Peak harvest drying Jun-Oct (days 150-300)
        harvest = 1.0 if 145 <= day_of_year <= 295 else 0.38
        proc_kw = node.process_base_kw * (1.0 - harvest) + node.process_summer_kw * harvest
    elif node.process_sink_type == "commercial_laundry":
        shift = 1.35 if 4 <= hour <= 18 else 0.52
        proc_kw = (node.process_base_kw + 0.6 * summer_wave) * shift
    elif node.process_sink_type == "hospital_24_7":
        shift = 1.12 if 6 <= hour <= 21 else 0.88
        proc_kw = node.process_base_kw * shift
    elif node.process_sink_type == "alpine_spa_pool":
        proc_kw = (
            node.process_base_kw + (node.process_summer_kw - node.process_base_kw) * summer_wave
        )

    return round(space_kw + dhw_kw + proc_kw, 3)


def _offered_fleet_load_factor(hour: int) -> float:
    """Offered batch compute demand (50% average load factor matching benchmark trace)."""
    # Slight daytime work queue + steady overnight batch backlog
    return 0.56 if 7 <= hour <= 21 else 0.42


@lru_cache(maxsize=2)
def _compute_annual_fleet_cache(strategy: str) -> dict[str, Any]:
    """Run deterministic 8,760-hour fleet simulation for all 14 Swiss nodes (cached)."""
    w_basel = load_weather_zurich("basel").hours
    w_davos = load_weather_zurich("davos").hours
    n_hours = min(len(w_basel), len(w_davos))

    # Per-node state & accumulators
    soc = {n.node_id: 0.35 * n.tank_capacity_kwh for n in SWISS_FLEET_NODES}
    acc = {
        n.node_id: {
            "demand_kwh": 0.0,
            "useful_heat_kwh": 0.0,
            "dumped_kwh": 0.0,
            "srv_h": 0.0,
            "migrated_in_srv_h": 0.0,
            "migrated_out_srv_h": 0.0,
            "elec_chf": 0.0,
            "compute_chf": 0.0,
            "heat_val_chf": 0.0,
        }
        for n in SWISS_FLEET_NODES
    }
    hourly_records: list[dict[str, Any]] = []

    for h_idx in range(n_hours):
        wb = w_basel[h_idx]
        wd = w_davos[h_idx]
        doy = min(365, (h_idx // 24) + 1)
        hod = wb.local_hour
        load_f = _offered_fleet_load_factor(hod)

        demands: dict[str, float] = {}
        temps: dict[str, float] = {}
        headroom_srv: dict[str, float] = {}
        offered_srv: dict[str, float] = {}
        local_run: dict[str, float] = {}
        surplus_offered: dict[str, float] = {}

        for n in SWISS_FLEET_NODES:
            base_t = wd.outdoor_temp_c if n.climate_base == "davos" else wb.outdoor_temp_c
            t_out = round(base_t + n.temp_offset_c, 1)
            temps[n.node_id] = t_out
            dem = _hourly_thermal_demand_kw(n, base_t, doy, hod)
            demands[n.node_id] = dem
            off = n.servers_installed * load_f
            offered_srv[n.node_id] = off

            # Max thermal recovery absorbable without dumping (1 kW server -> 0.8 kW_th water loop)
            max_absorb_kwh = dem + max(0.0, n.tank_capacity_kwh - soc[n.node_id])
            max_srv_no_dump = min(
                float(n.servers_installed),
                max_absorb_kwh / (SERVER_ELECTRICAL_POWER_KW * HEAT_RECOVERY_FRACTION),
            )
            headroom_srv[n.node_id] = max_srv_no_dump

            if strategy == "baseline":
                local_run[n.node_id] = off
                surplus_offered[n.node_id] = 0.0
            else:
                can_run = min(off, max_srv_no_dump)
                local_run[n.node_id] = can_run
                surplus_offered[n.node_id] = max(0.0, off - can_run)

        # Inter-canton fiber migration under Smart Fleet scheduler
        migrated_in = {n.node_id: 0.0 for n in SWISS_FLEET_NODES}
        migrated_out = {n.node_id: 0.0 for n in SWISS_FLEET_NODES}
        flows_hour: list[dict[str, Any]] = []

        if strategy != "baseline":
            # Eligible receivers with remaining thermal headroom
            receivers = []
            for n in SWISS_FLEET_NODES:
                spare = max(0.0, headroom_srv[n.node_id] - local_run[n.node_id])
                if spare > 0.15:
                    receivers.append([n, spare])

            donors = [n for n in SWISS_FLEET_NODES if surplus_offered[n.node_id] > 0.15]
            donors.sort(key=lambda n: -surplus_offered[n.node_id])

            for donor in donors:
                rem = surplus_offered[donor.node_id]
                # Latency-aware + tariff-aware regional thermal routing
                ranked = sorted(
                    receivers,
                    key=lambda item: (
                        _haversine_km(donor.lat, donor.lon, item[0].lat, item[0].lon) / 140.0
                        + item[0].tariff_chf_per_kwh * 1.6
                    ),
                )
                for rec_entry in ranked:
                    if rem <= 0.12:
                        break
                    target, spare = rec_entry
                    if spare <= 0.12 or target.node_id == donor.node_id:
                        continue
                    moved = min(rem, spare)
                    rem -= moved
                    rec_entry[1] -= moved
                    migrated_out[donor.node_id] += moved
                    migrated_in[target.node_id] += moved
                    dist_km = round(_haversine_km(donor.lat, donor.lon, target.lat, target.lon), 1)
                    flows_hour.append(
                        {
                            "from_id": donor.node_id,
                            "to_id": target.node_id,
                            "from_name": donor.name,
                            "to_name": target.name,
                            "servers_moved": round(moved, 2),
                            "kw_shifted": round(
                                moved * SERVER_ELECTRICAL_POWER_KW * HEAT_RECOVERY_FRACTION, 2
                            ),
                            "workload": donor.workload_primary,
                            "distance_km": dist_km,
                            "latency_ms": round(1.8 + dist_km * 0.015, 1),
                        }
                    )

        # Thermal conservation update per node
        node_snaps: dict[str, dict[str, Any]] = {}
        for n in SWISS_FLEET_NODES:
            nid = n.node_id
            active = local_run[nid] + migrated_in[nid]
            heat_gen = active * SERVER_ELECTRICAL_POWER_KW * HEAT_RECOVERY_FRACTION
            dem = demands[nid]

            # Tank + demand balance
            direct_used = min(heat_gen, dem)
            excess_to_tank = max(0.0, heat_gen - direct_used)
            rem_dem = max(0.0, dem - direct_used)

            tank_draw = min(soc[nid], rem_dem)
            soc_after_draw = soc[nid] - tank_draw
            tank_charge = min(n.tank_capacity_kwh - soc_after_draw, excess_to_tank)
            dumped = max(0.0, excess_to_tank - tank_charge)
            soc[nid] = soc_after_draw + tank_charge
            useful = direct_used + tank_draw

            elec_chf = active * SERVER_ELECTRICAL_POWER_KW * n.tariff_chf_per_kwh
            comp_chf = active * DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H
            heat_val = useful * (n.tariff_chf_per_kwh / HEAT_PUMP_REF_COP)

            a = acc[nid]
            a["demand_kwh"] += dem
            a["useful_heat_kwh"] += useful
            a["dumped_kwh"] += dumped
            a["srv_h"] += active
            a["migrated_in_srv_h"] += migrated_in[nid]
            a["migrated_out_srv_h"] += migrated_out[nid]
            a["elec_chf"] += elec_chf
            a["compute_chf"] += comp_chf
            a["heat_val_chf"] += heat_val

            if strategy == "baseline" and dumped > 0.05:
                st_label = "DUMPING_HEAT"
            elif migrated_out[nid] > 0.15 and local_run[nid] < 0.25:
                st_label = "SIGSTOP_OFFLOADING"
            elif migrated_out[nid] > 0.15:
                st_label = "THROTTLED_OFFLOADING"
            elif migrated_in[nid] > 0.15:
                st_label = "RECEIVING_MIGRATED"
            elif active > 0.1:
                st_label = "RUNNING_LOCAL"
            else:
                st_label = "TANK_FULL_STANDBY"

            soc_pct = (soc[nid] / n.tank_capacity_kwh) * 100.0
            node_snaps[nid] = {
                "outdoor_temp_c": temps[nid],
                "heat_demand_kw": round(dem, 2),
                "heat_generated_kw": round(heat_gen, 2),
                "heat_dumped_kw": round(dumped, 2),
                "tank_soc_kwh": round(soc[nid], 2),
                "tank_soc_pct": round(soc_pct, 1),
                "tank_temp_c": round(TANK_TEMP_MIN_C + (soc_pct / 100.0) * TANK_TEMP_SPREAD_K, 1),
                "servers_active": round(active, 2),
                "servers_local": round(local_run[nid], 2),
                "servers_migrated_in": round(migrated_in[nid], 2),
                "servers_migrated_out": round(migrated_out[nid], 2),
                "scheduler_state": st_label,
                "hourly_elec_chf": round(elec_chf, 3),
                "hourly_compute_chf": round(comp_chf, 3),
            }

        # Store every hour (lightweight dicts for instant scrubbing across all 365 days!)
        hourly_records.append({"doy": doy, "hour": hod, "nodes": node_snaps, "flows": flows_hour})

    # Annual summary per node & fleet total
    capex_per_srv_yr = 1400.0
    annual_by_node: dict[str, dict[str, Any]] = {}
    tot_dem = tot_use = tot_dump = tot_srv_h = tot_elec = tot_comp = tot_hval = 0.0
    tot_mig_in = tot_mig_out = 0.0
    tot_installed = sum(n.servers_installed for n in SWISS_FLEET_NODES)

    for n in SWISS_FLEET_NODES:
        a = acc[n.node_id]
        util_pct = (a["srv_h"] / (n.servers_installed * n_hours)) * 100.0
        cov_pct = (a["useful_heat_kwh"] / max(a["demand_kwh"], 1e-6)) * 100.0
        opex_be = max(0.0, (a["elec_chf"] - a["heat_val_chf"]) / max(a["srv_h"], 1.0))
        capex_be = max(
            0.0,
            (a["elec_chf"] + n.servers_installed * capex_per_srv_yr - a["heat_val_chf"])
            / max(a["srv_h"], 1.0),
        )
        annual_by_node[n.node_id] = {
            "annual_demand_kwh": round(a["demand_kwh"], 1),
            "annual_useful_heat_kwh": round(a["useful_heat_kwh"], 1),
            "annual_dumped_kwh": round(a["dumped_kwh"], 1),
            "annual_heat_covered_pct": round(cov_pct, 1),
            "annual_utilisation_pct": round(util_pct, 1),
            "annual_srv_h": round(a["srv_h"], 1),
            "annual_migrated_in_srv_h": round(a["migrated_in_srv_h"], 1),
            "annual_migrated_out_srv_h": round(a["migrated_out_srv_h"], 1),
            "annual_elec_chf": round(a["elec_chf"], 2),
            "annual_compute_chf": round(a["compute_chf"], 2),
            "annual_heat_value_chf": round(a["heat_val_chf"], 2),
            "opex_breakeven_chf": round(opex_be, 4),
            "capex_breakeven_chf": round(capex_be, 4),
        }
        tot_dem += a["demand_kwh"]
        tot_use += a["useful_heat_kwh"]
        tot_dump += a["dumped_kwh"]
        tot_srv_h += a["srv_h"]
        tot_elec += a["elec_chf"]
        tot_comp += a["compute_chf"]
        tot_hval += a["heat_val_chf"]
        tot_mig_in += a["migrated_in_srv_h"]
        tot_mig_out += a["migrated_out_srv_h"]

    fleet_annual = {
        "total_nodes": len(SWISS_FLEET_NODES),
        "total_cantons": len({n.canton for n in SWISS_FLEET_NODES}),
        "total_servers": tot_installed,
        "annual_demand_mwh": round(tot_dem / 1000.0, 2),
        "annual_useful_heat_mwh": round(tot_use / 1000.0, 2),
        "annual_dumped_kwh": round(tot_dump, 1),
        "annual_heat_covered_pct": round((tot_use / max(tot_dem, 1e-6)) * 100.0, 1),
        "annual_utilisation_pct": round((tot_srv_h / (tot_installed * n_hours)) * 100.0, 1),
        "annual_migrated_srv_h": round(tot_mig_in, 1),
        "annual_elec_chf": round(tot_elec, 2),
        "annual_compute_chf": round(tot_comp, 2),
        "annual_heat_value_chf": round(tot_hval, 2),
        "opex_breakeven_chf": round(max(0.0, (tot_elec - tot_hval) / max(tot_srv_h, 1.0)), 4),
        "capex_breakeven_chf": round(
            max(
                0.0, (tot_elec + tot_installed * capex_per_srv_yr - tot_hval) / max(tot_srv_h, 1.0)
            ),
            4,
        ),
    }

    log_event(
        "simulation_run",
        "swiss_fleet_8760h_simulated",
        f"Simulated 8,760h Swiss 14-node fleet ({strategy}, {tot_installed} servers)",
        source_module="fleet",
        metrics={
            "strategy": strategy,
            "total_nodes": fleet_annual["total_nodes"],
            "total_servers": tot_installed,
            "annual_dumped_kwh": fleet_annual["annual_dumped_kwh"],
            "annual_migrated_srv_h": fleet_annual["annual_migrated_srv_h"],
            "annual_utilisation_pct": fleet_annual["annual_utilisation_pct"],
        },
    )

    return {
        "strategy": strategy,
        "n_hours": n_hours,
        "fleet_annual": fleet_annual,
        "annual_by_node": annual_by_node,
        "hourly": hourly_records,
    }


def get_swiss_fleet_snapshot(
    day_of_year: int = 196,
    hour: int = 14,
    strategy: str = "smart",
) -> dict[str, Any]:
    """Return full Switzerland 14-node fleet telemetry, annual KPIs and active migration arcs."""
    strat = "baseline" if strategy == "baseline" else "smart"
    cache = _compute_annual_fleet_cache(strat)
    doy_clamped = max(1, min(365, int(day_of_year)))
    hod_clamped = max(0, min(23, int(hour)))
    h_idx = min(cache["n_hours"] - 1, (doy_clamped - 1) * 24 + hod_clamped)
    snap = cache["hourly"][h_idx]

    nodes_out = []
    live_gen = live_dem = live_dump = live_active = live_elec = live_mig_kw = 0.0
    for n in SWISS_FLEET_NODES:
        live = copy.deepcopy(snap["nodes"][n.node_id])
        ann = copy.deepcopy(cache["annual_by_node"][n.node_id])
        live_gen += live["heat_generated_kw"]
        live_dem += live["heat_demand_kw"]
        live_dump += live["heat_dumped_kw"]
        live_active += live["servers_active"]
        live_elec += live["hourly_elec_chf"]
        live_mig_kw += (
            live["servers_migrated_in"] * SERVER_ELECTRICAL_POWER_KW * HEAT_RECOVERY_FRACTION
        )

        nodes_out.append(
            {
                "node_id": n.node_id,
                "name": n.name,
                "city": n.city,
                "canton": n.canton,
                "region": n.region,
                "category": n.category,
                "archetype": n.archetype,
                "lat": n.lat,
                "lon": n.lon,
                "elevation_m": n.elevation_m,
                "servers_installed": n.servers_installed,
                "tank_capacity_kwh": n.tank_capacity_kwh,
                "target_supply_c": n.target_supply_c,
                "tariff_chf_per_kwh": n.tariff_chf_per_kwh,
                "workload_primary": n.workload_primary,
                "glb_scene_key": n.glb_scene_key,
                "twin_style": n.twin_style,
                "accent_hex": n.accent_hex,
                "summary": n.summary,
                "live": live,
                "annual": ann,
            }
        )

    baseline_cache = _compute_annual_fleet_cache("baseline")
    return {
        "strategy": strat,
        "day_of_year": doy_clamped,
        "hour": hod_clamped,
        "provenance_note": (
            "Hourly ambient profiles derived from pinned Open-Meteo 2025 Basel & Davos series "
            "with Swiss cantonal elevation lapse calibration and ElCom 2026 tariffs."
        ),
        "live_summary": {
            "servers_active": round(live_active, 1),
            "servers_installed": cache["fleet_annual"]["total_servers"],
            "heat_generated_kw": round(live_gen, 1),
            "heat_demand_kw": round(live_dem, 1),
            "heat_dumped_kw": round(live_dump, 1),
            "migrated_heat_kw": round(live_mig_kw, 1),
            "active_flows_count": len(snap["flows"]),
            "hourly_elec_chf": round(live_elec, 2),
        },
        "fleet_annual": copy.deepcopy(cache["fleet_annual"]),
        "baseline_dumped_kwh": baseline_cache["fleet_annual"]["annual_dumped_kwh"],
        "nodes": nodes_out,
        "flows": copy.deepcopy(snap["flows"]),
    }
