"""Live/cached cloud GPU & server pricing comparison and summer sink cost model."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from digitalheat.config import DATA_DIR as CONFIG_DATA_DIR
from digitalheat.config import ELCOM_BASEL_H4_CHF_PER_KWH, HEAT_PUMP_REF_COP
from digitalheat.data_loader import DatasetIntegrityError, load_hardware_catalog

DEFAULT_PRICING_CACHE_PATH: Path = (
    Path.home() / ".cache" / "calorgrid" / "cloud_pricing_cache.json"
)

AZURE_RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
AZURE_API_VERSION = "2023-01-01-preview"
HTTP_TIMEOUT_S = 2.5
MAX_RESPONSE_BYTES = 1_048_576
CACHE_TTL_S = 900.0
MAX_CACHED_LIVE_AGE_S = 86_400.0
FAILURE_THRESHOLD = 3
COOLDOWN_S = 300.0

SPOT_PRICE_RATIO = 0.50
DISCOUNT_RATE = 0.04
HARDWARE_LIFE_YEARS = 5
INSTALL_LIFE_YEARS = 15
DRY_COOLER_CAPEX_CHF = 4500.0
DRY_COOLER_FAN_KWH_PER_KWH = 0.03

ProviderId = Literal["aws", "gcp", "azure", "coreweave", "lambda"]
PriceSource = Literal["fresh_live", "cached_live", "pinned_fallback", "unavailable"]
ComputeTier = Literal[
    "on_demand", "spot", "reserved", "block", "dedicated", "serverless"
]

# Unified 6-tier cloud compute taxonomy (reconciled with Vaibhav's fleet.py TIERS & TierPricing)
DEMAND_TIER_SPECS: dict[str, dict[str, Any]] = {
    "on_demand": {
        "key": "on_demand",
        "label": "On-Demand",
        "short_label": "On-Demand",
        "price_multiplier": 1.00,
        "firm": True,
        "whole_server": False,
        "busy_share": 1.0,
        "lead_h": 0,
        "evicts_spot": True,
        "summary": "Start and stop anytime, 0 min wait, routed to year-round thermal sinks.",
    },
    "spot": {
        "key": "spot",
        "label": "Spot (-50%)",
        "short_label": "Spot",
        "price_multiplier": SPOT_PRICE_RATIO,
        "firm": False,
        "whole_server": False,
        "busy_share": 1.0,
        "lead_h": 0,
        "evicts_spot": False,
        "summary": "Preemptible capacity routed to heat-demanding Swiss buildings with zero overflow.",
    },
    "reserved": {
        "key": "reserved",
        "label": "Reserved (-40%)",
        "short_label": "Reserved",
        "price_multiplier": 0.60,
        "firm": True,
        "whole_server": False,
        "busy_share": 1.0,
        "lead_h": 0,
        "evicts_spot": True,
        "summary": "1-3 year commitment at 40% below On-Demand with highest firm scheduling priority.",
    },
    "block": {
        "key": "block",
        "label": "Capacity Block (-10%)",
        "short_label": "Block (24h)",
        "price_multiplier": 0.90,
        "firm": True,
        "whole_server": False,
        "busy_share": 1.0,
        "lead_h": 24,
        "evicts_spot": False,
        "summary": "Scheduled GPU cluster window booked 24h in advance for guaranteed thermal capacity.",
    },
    "dedicated": {
        "key": "dedicated",
        "label": "Dedicated Host (+10%)",
        "short_label": "Dedicated",
        "price_multiplier": 1.10,
        "firm": True,
        "whole_server": True,
        "busy_share": 1.0,
        "lead_h": 0,
        "evicts_spot": True,
        "summary": "Whole physical liquid-cooled server exclusively isolated for one tenant.",
    },
    "serverless": {
        "key": "serverless",
        "label": "Serverless (per-req)",
        "short_label": "Serverless",
        "price_multiplier": 1.50,
        "firm": True,
        "whole_server": False,
        "busy_share": 0.50,
        "lead_h": 0,
        "evicts_spot": True,
        "summary": "Pay-per-request inference (1.5x active rate, 50% duty factor, CHF 0 while idle).",
    },
}

_VAIBHAV_DATA_DIR = Path(__file__).resolve().parents[2] / "engines" / "vaibhav_scheduler" / "data"


def load_specialist_cloud_benchmarks() -> dict[str, Any]:
    """Load Vaibhav's 2026 verified specialist GPU cloud & hardware benchmark JSON."""
    path = _VAIBHAV_DATA_DIR / "fleet_machine.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {
                "retrieved": raw.get("retrieved", "2026-11-21"),
                "hardware": raw.get("hardware", {}),
                "gpu_hour": raw.get("gpu_hour", []),
                "exchange": raw.get("exchange", {}),
                "speed": raw.get("speed", {}),
                "source_file": "engines/vaibhav_scheduler/data/fleet_machine.json",
            }
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "retrieved": "2026-11-21",
        "hardware": {"system": "8x NVIDIA H100 (HGX H100)", "usd": 285000, "low_usd": 250000, "high_usd": 320000},
        "gpu_hour": [
            {
                "id": "h100-specialist",
                "gpu": "NVIDIA H100",
                "market": "specialist GPU clouds",
                "provider": "median of RunPod, Lambda, CoreWeave, Together AI",
                "on_demand_usd": 4.49,
                "spot_usd": 2.4638,
            },
            {
                "id": "h100-aws",
                "gpu": "NVIDIA H100",
                "market": "large cloud",
                "provider": "AWS p5.48xlarge (US East)",
                "on_demand_usd": 6.88,
                "spot_usd": 3.9123,
            },
        ],
        "source_file": "inline_fallback",
    }


def load_swiss_gas_history() -> dict[str, Any]:
    """Load Vaibhav's 2021-2025 real Basel gas meter (MESSPUNKT_004) variable tariff data."""
    path = _VAIBHAV_DATA_DIR / "gas_price.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["boiler_efficiency"] = 0.90
            raw["effective_2025_chf_per_kwh_th"] = round(
                float(raw["years"]["2025"]["chf_per_kwh"]) / 0.90, 4
            )
            return raw
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    return {
        "meter": "MESSPUNKT_004",
        "unit": "CHF per kWh of gas",
        "boiler_efficiency": 0.90,
        "effective_2025_chf_per_kwh_th": 0.1506,
        "years": {
            "2021": {"chf_per_kwh": 0.0848, "fixed_chf_per_month": 39.29, "kwh": 66317, "months": 12},
            "2022": {"chf_per_kwh": 0.1144, "fixed_chf_per_month": 39.29, "kwh": 45268, "months": 12},
            "2023": {"chf_per_kwh": 0.1587, "fixed_chf_per_month": 39.29, "kwh": 26191, "months": 12},
            "2024": {"chf_per_kwh": 0.1501, "fixed_chf_per_month": 39.29, "kwh": 44464, "months": 12},
            "2025": {"chf_per_kwh": 0.1355, "fixed_chf_per_month": 39.29, "kwh": 31275, "months": 9},
        },
        "note": "Fixed CHF 39.29/mo capacity charge is not avoided while the gas connection remains.",
    }

_PINNED_FALLBACK_AS_OF = "2026-11-20T00:00:00Z"

# Verified Azure SKU queries against prices.azure.com (Linux VMs, 1 Hour, USD)
_AZURE_SKU_QUERIES: dict[str, dict[str, Any]] = {
    "b200_sxm_180gb": {
        "arm_sku": "Standard_ND128isr_NDR_GB200_v6",
        "arm_region": "germanywestcentral",
        "gpus_per_instance": 4,
        "offer_status": "proxy_gb200",
        "notes": "Azure GB200 v6 4-GPU proxy (B200 SXM offer absent on Azure Retail API).",
    },
    "h100_sxm_80gb": {
        "arm_sku": "Standard_ND96isr_H100_v5",
        "arm_region": "westeurope",
        "gpus_per_instance": 8,
        "offer_status": "available",
        "notes": "8x NVIDIA H100 SXM 80GB VM in westeurope.",
    },
    "h100_nvl_94gb": {
        "arm_sku": "Standard_NC40ads_H100_v5",
        "arm_region": "westeurope",
        "gpus_per_instance": 1,
        "offer_status": "available",
        "notes": "1x NVIDIA H100 NVL 94GB VM in westeurope.",
    },
    "a100_sxm_80gb": {
        "arm_sku": "Standard_NC24ads_A100_v4",
        "arm_region": "switzerlandnorth",
        "gpus_per_instance": 1,
        "offer_status": "available",
        "notes": "1x NVIDIA A100 80GB VM in switzerlandnorth.",
    },
}

_PROVIDER_LABELS: dict[str, str] = {
    "aws": "AWS EC2",
    "gcp": "Google Cloud",
    "azure": "Microsoft Azure",
    "coreweave": "CoreWeave",
    "lambda": "Lambda Cloud",
}

# Pinned multi-provider fallback matrix (USD/GPU-hr, reference date 2026-11-20)
_PINNED_GPU_OFFERS: dict[str, dict[str, dict[str, Any]]] = {
    "b200_sxm_180gb": {
        "aws": {
            "region": "us-east-1",
            "instance_type": "p6-b200.48xlarge",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 14.24,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
        "gcp": {
            "region": "us-central1",
            "instance_type": "a4-highgpu-8g",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 11.00,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
        "azure": {
            "region": "germanywestcentral",
            "instance_type": "Standard_ND128isr_NDR_GB200_v6",
            "min_purchasable_gpus": 4,
            "on_demand_usd": 35.15,
            "spot_usd": None,
            "offer_status": "proxy_gb200",
            "includes_host_overhead": True,
            "notes": "GB200 4-GPU instance proxy ($140.60/hr VM).",
        },
        "coreweave": {
            "region": "us-east",
            "instance_type": "hgx-b200-8x",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 8.60,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
        "lambda": {
            "region": "us-west",
            "instance_type": "gpu_8x_b200_sxm",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 4.99,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
    },
    "h100_sxm_80gb": {
        "aws": {
            "region": "us-east-1",
            "instance_type": "p5.48xlarge",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 6.88,
            "spot_usd": 2.75,
            "offer_status": "available",
            "includes_host_overhead": True,
        },
        "gcp": {
            "region": "europe-west4",
            "instance_type": "a3-highgpu-8g",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 11.06,
            "spot_usd": 3.32,
            "offer_status": "available",
            "includes_host_overhead": True,
        },
        "azure": {
            "region": "westeurope",
            "instance_type": "Standard_ND96isr_H100_v5",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 15.977,
            "spot_usd": 2.9525,
            "offer_status": "available",
            "includes_host_overhead": True,
        },
        "coreweave": {
            "region": "eu-west",
            "instance_type": "gd-8xh100ib",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 6.15,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
        "lambda": {
            "region": "us-east",
            "instance_type": "gpu_8x_h100_sxm5",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 2.99,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
    },
    "h100_nvl_94gb": {
        "aws": {
            "region": None,
            "instance_type": None,
            "min_purchasable_gpus": None,
            "on_demand_usd": None,
            "spot_usd": None,
            "offer_status": "not_offered",
            "includes_host_overhead": True,
            "notes": "AWS EC2 does not offer H100 NVL 94GB PCIe.",
        },
        "gcp": {
            "region": None,
            "instance_type": None,
            "min_purchasable_gpus": None,
            "on_demand_usd": None,
            "spot_usd": None,
            "offer_status": "not_offered",
            "includes_host_overhead": True,
            "notes": "GCP does not offer H100 NVL 94GB PCIe.",
        },
        "azure": {
            "region": "westeurope",
            "instance_type": "Standard_NC40ads_H100_v5",
            "min_purchasable_gpus": 1,
            "on_demand_usd": 9.08,
            "spot_usd": 3.6810,
            "offer_status": "available",
            "includes_host_overhead": True,
        },
        "coreweave": {
            "region": "us-east",
            "instance_type": "1x-h100-pcie",
            "min_purchasable_gpus": 1,
            "on_demand_usd": 4.76,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
        "lambda": {
            "region": "us-west",
            "instance_type": "gpu_1x_h100_pcie",
            "min_purchasable_gpus": 1,
            "on_demand_usd": 2.49,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
    },
    "a100_sxm_80gb": {
        "aws": {
            "region": "eu-central-1",
            "instance_type": "p4de.24xlarge",
            "min_purchasable_gpus": 8,
            "on_demand_usd": 4.10,
            "spot_usd": 1.43,
            "offer_status": "available",
            "includes_host_overhead": True,
        },
        "gcp": {
            "region": "europe-west4",
            "instance_type": "a2-ultragpu-1g",
            "min_purchasable_gpus": 1,
            "on_demand_usd": 5.07,
            "spot_usd": 1.52,
            "offer_status": "available",
            "includes_host_overhead": True,
        },
        "azure": {
            "region": "switzerlandnorth",
            "instance_type": "Standard_NC24ads_A100_v4",
            "min_purchasable_gpus": 1,
            "on_demand_usd": 5.252,
            "spot_usd": 0.9706,
            "offer_status": "available",
            "includes_host_overhead": True,
        },
        "coreweave": {
            "region": "eu-west",
            "instance_type": "1x-a100-80gb",
            "min_purchasable_gpus": 1,
            "on_demand_usd": 2.21,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
        "lambda": {
            "region": "us-east",
            "instance_type": "gpu_1x_a100_sxm4",
            "min_purchasable_gpus": 1,
            "on_demand_usd": 1.79,
            "spot_usd": None,
            "offer_status": "on_demand_only",
            "includes_host_overhead": True,
        },
    },
    "rtx_pro_4500_blackwell": {
        "aws": {
            "region": None,
            "instance_type": None,
            "min_purchasable_gpus": None,
            "on_demand_usd": None,
            "spot_usd": None,
            "offer_status": "not_offered",
            "includes_host_overhead": True,
            "notes": "No public hyperscaler cloud equivalent for RTX PRO 4500 Blackwell.",
        },
        "gcp": {
            "region": None,
            "instance_type": None,
            "min_purchasable_gpus": None,
            "on_demand_usd": None,
            "spot_usd": None,
            "offer_status": "not_offered",
            "includes_host_overhead": True,
        },
        "azure": {
            "region": None,
            "instance_type": None,
            "min_purchasable_gpus": None,
            "on_demand_usd": None,
            "spot_usd": None,
            "offer_status": "not_offered",
            "includes_host_overhead": True,
        },
        "coreweave": {
            "region": None,
            "instance_type": None,
            "min_purchasable_gpus": None,
            "on_demand_usd": None,
            "spot_usd": None,
            "offer_status": "not_offered",
            "includes_host_overhead": True,
        },
        "lambda": {
            "region": None,
            "instance_type": None,
            "min_purchasable_gpus": None,
            "on_demand_usd": None,
            "spot_usd": None,
            "offer_status": "not_offered",
            "includes_host_overhead": True,
        },
    },
}

_PUBLISHED_GPU_RATES_CHF: dict[str, float] = {
    "b200_sxm_180gb": 3.95,
    "h100_sxm_80gb": 2.65,
    "h100_nvl_94gb": 2.25,
    "a100_sxm_80gb": 1.45,
    "rtx_pro_4500_blackwell": 0.48,
}

_PUBLISHED_SERVER_RATES_CHF: dict[str, float] = {
    "hydronode_1kw": 0.56,
    "ocp_winterfell_3kw": 1.35,
    "b200_ai_10kw": 31.60,
    "edge_micro_250w": 0.18,
}

_CPU_SERVER_CLOUD_BASELINES: dict[str, dict[str, Any]] = {
    "hydronode_1kw": {
        "cloud_equivalent_type": "cpu_compute_baseline",
        "aws_instance": "c7i.8xlarge (32 vCPU)",
        "aws_on_demand_usd_per_hr": 1.428,
        "aws_spot_usd_per_hr": 0.571,
        "azure_instance": "Standard_F32s_v2 (32 vCPU)",
        "azure_on_demand_usd_per_hr": 1.356,
        "azure_spot_usd_per_hr": 0.407,
    },
    "ocp_winterfell_3kw": {
        "cloud_equivalent_type": "cpu_compute_baseline",
        "aws_instance": "c7i.24xlarge (96 vCPU)",
        "aws_on_demand_usd_per_hr": 4.284,
        "aws_spot_usd_per_hr": 1.714,
        "azure_instance": "Standard_F96s_v2 (96 vCPU)",
        "azure_on_demand_usd_per_hr": 4.068,
        "azure_spot_usd_per_hr": 1.220,
    },
    "edge_micro_250w": {
        "cloud_equivalent_type": "cpu_compute_baseline",
        "aws_instance": "c7i.2xlarge (8 vCPU)",
        "aws_on_demand_usd_per_hr": 0.357,
        "aws_spot_usd_per_hr": 0.143,
        "azure_instance": "Standard_F8s_v2 (8 vCPU)",
        "azure_on_demand_usd_per_hr": 0.339,
        "azure_spot_usd_per_hr": 0.102,
    },
}


def annuity(capex: float, years: int, rate: float) -> float:
    """Capital recovery annuity from Vilas heatshift/economics.py."""
    if years <= 0:
        return 0.0
    if rate <= 0.0:
        return capex / years
    return capex * rate / (1.0 - (1.0 + rate) ** -years)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_utc(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_summer_sink_cost_model(
    fx_chf_per_usd: float = 0.83922,
) -> list[dict[str, Any]]:
    """Model distributed summer and year-round sink economics across 5 Swiss archetypes."""
    # Evaluate a 1x 8-GPU B200-SXM-180GB node (~10.4 kW peak, 9.44 kW typical, 1.20 kW idle)
    node_capex = 377_650.0
    capex_ann = annuity(node_capex, HARDWARE_LIFE_YEARS, DISCOUNT_RATE)
    opex_yr = 0.0208 * node_capex
    gpu_count = 8
    typical_kw = 9.44
    idle_kw = 1.20
    eta = 0.80

    archetypes = [
        {
            "site_id": "sole_uno",
            "name": "Sole Uno Wellness Thermal Bath (Rheinfelden)",
            "sink_type": "indoor_thermal_spa",
            "seasonality": "year_round",
            "active_months": list(range(1, 13)),
            "supply_temp_c": 45.0,
            "w45_compliant": True,
            "tariff_code": "C6",
            "tariff_chf_per_kwh": 0.25267,
            "annual_utilisation_pct": 94.0,
            "summer_utilisation_pct": 95.0,
            "dumped_kwh": 0.0,
            "dry_cooler_required": False,
            "heat_credit_accounting": "direct_displaced_thermal_demand",
            "notes": (
                "24/7 indoor brine spa basin absorbs 45 C server return year-round "
                "with zero dry cooler."
            ),
        },
        {
            "site_id": "ara_basel_biogas",
            "name": "ARA Basel ProRheno Biogas Digester",
            "sink_type": "biogas_digester",
            "seasonality": "year_round",
            "active_months": list(range(1, 13)),
            "supply_temp_c": 42.0,
            "w45_compliant": True,
            "tariff_code": "C6",
            "tariff_chf_per_kwh": 0.25267,
            "annual_utilisation_pct": 96.0,
            "summer_utilisation_pct": 96.0,
            "dumped_kwh": 0.0,
            "dry_cooler_required": False,
            "heat_credit_accounting": "direct_displaced_thermal_demand",
            "notes": (
                "Continuous 37-42 C mesophilic digester heating delivers 96% "
                "server utilisation without heat dumping."
            ),
        },
        {
            "site_id": "basel_erdsonde",
            "name": "Dreispitz Minergie + SIA 384/6 Borehole Regen",
            "sink_type": "geothermal_regen",
            "seasonality": "dual_sink_summer_regen",
            "active_months": list(range(1, 13)),
            "supply_temp_c": 40.0,
            "w45_compliant": True,
            "tariff_code": "C4",
            "tariff_chf_per_kwh": 0.29128,
            "annual_utilisation_pct": 92.0,
            "summer_utilisation_pct": 90.0,
            "dumped_kwh": 0.0,
            "dry_cooler_required": False,
            "heat_credit_accounting": "winter_direct_plus_summer_deferred_cop",
            "notes": (
                "Winter space/DHW displacement plus May-Sep ground borehole regeneration "
                "(tagged as deferred COP preservation rather than immediate cash displacement)."
            ),
        },
        {
            "site_id": "basel_pool_st_jakob",
            "name": "Gartenbad St. Jakob Outdoor Pool (Basel)",
            "sink_type": "summer_pool",
            "seasonality": "seasonal_may_sep",
            "active_months": [5, 6, 7, 8, 9],
            "supply_temp_c": 32.0,
            "w45_compliant": True,
            "tariff_code": "C6",
            "tariff_chf_per_kwh": 0.25267,
            "annual_utilisation_pct": 39.6,
            "summer_utilisation_pct": 95.0,
            "paired_annual_utilisation_pct": 91.0,
            "dumped_kwh": 0.0,
            "dry_cooler_required": False,
            "heat_credit_accounting": "seasonal_may_sep_pool_displacement",
            "notes": (
                "95% utilisation in May-Sep; standalone annual is 39.6% unless paired with "
                "a winter residential/district sink (91.0% combined)."
            ),
        },
        {
            "site_id": "davos_alpine",
            "name": "Davos Alpine Resort & Indoor Spa",
            "sink_type": "alpine_spa_pool",
            "seasonality": "winter_dominant",
            "active_months": [1, 2, 3, 4, 10, 11, 12],
            "heating_days": 291,
            "supply_temp_c": 48.0,
            "w45_compliant": False,
            "tariff_code": "GR_Alpine",
            "tariff_chf_per_kwh": 0.289,
            "annual_utilisation_pct": 68.0,
            "summer_utilisation_pct": 38.0,
            "dumped_kwh": 0.0,
            "dry_cooler_required": False,
            "heat_credit_accounting": "winter_space_plus_indoor_spa_baseload",
            "notes": (
                "Snowmaking is a cooling load, so ski resorts are NOT summer bulk heat sinks; "
                "only the 4.8-6.8 kW indoor alpine spa wing absorbs summer heat without dumping."
            ),
        },
    ]

    results: list[dict[str, Any]] = []
    for arch in archetypes:
        util_frac = arch["annual_utilisation_pct"] / 100.0
        sold_h = 8760.0 * util_frac
        idle_h = max(0.0, 8760.0 - sold_h)
        elec_kwh = sold_h * typical_kw + idle_h * idle_kw
        elec_cost = elec_kwh * arch["tariff_chf_per_kwh"]
        dumped_kwh = float(arch["dumped_kwh"])
        dry_cooler_ann = (
            annuity(DRY_COOLER_CAPEX_CHF, INSTALL_LIFE_YEARS, DISCOUNT_RATE)
            if dumped_kwh > 1.0
            else 0.0
        )
        fan_cost = dumped_kwh * DRY_COOLER_FAN_KWH_PER_KWH * arch["tariff_chf_per_kwh"]

        recovered_kwh = sold_h * typical_kw * eta
        # For dual-sink Erdsonde, ~55% is direct winter heat displacement, 45% is deferred COP regen
        direct_credit_frac = (
            0.55
            if arch["site_id"] == "basel_erdsonde"
            else 1.0
        )
        useful_displaced_kwh = max(0.0, recovered_kwh - dumped_kwh) * direct_credit_frac
        heat_value_chf = useful_displaced_kwh * (arch["tariff_chf_per_kwh"] / HEAT_PUMP_REF_COP)
        net_cost_chf = capex_ann + opex_yr + elec_cost + dry_cooler_ann + fan_cost - heat_value_chf

        gpu_hours = sold_h * gpu_count
        break_even_chf_per_gpu_hr = net_cost_chf / max(gpu_hours, 1.0)
        # Vilas two-tier split: 65% On-Demand + 35% Spot (at 0.50x)
        eff_hours = (0.65 * gpu_hours) + SPOT_PRICE_RATIO * (0.35 * gpu_hours)
        two_tier_od_chf_per_gpu_hr = net_cost_chf / max(eff_hours, 1.0)

        results.append(
            {
                **arch,
                "reference_node": "8x NVIDIA B200-SXM-180GB (10.4 kW peak / 9.44 kW typical)",
                "fx_chf_per_usd": fx_chf_per_usd,
                "annual_capex_annuity_chf": round(capex_ann, 2),
                "annual_opex_chf": round(opex_yr, 2),
                "annual_elec_cost_chf": round(elec_cost, 2),
                "annual_heat_value_chf": round(heat_value_chf, 2),
                "break_even_chf_per_gpu_hr": round(break_even_chf_per_gpu_hr, 4),
                "break_even_usd_per_gpu_hr": round(break_even_chf_per_gpu_hr / fx_chf_per_usd, 4),
                "two_tier_on_demand_breakeven_chf_per_gpu_hr": round(
                    two_tier_od_chf_per_gpu_hr, 4
                ),
            }
        )
    return results


class CloudPricingService:
    """Thread-safe non-blocking cloud pricing comparator with ~/.cache fallback."""

    def __init__(
        self,
        data_dir: Path | None = None,
        cache_path: Path | None = None,
        ttl_s: float = CACHE_TTL_S,
        enable_live_fetch: bool = True,
    ) -> None:
        self._data_dir = (data_dir if data_dir is not None else CONFIG_DATA_DIR).resolve()
        env_cache = os.environ.get("CALORGRID_PRICING_CACHE_PATH")
        resolved_cache = (
            Path(cache_path)
            if cache_path is not None
            else (Path(env_cache) if env_cache else DEFAULT_PRICING_CACHE_PATH)
        )
        resolved_cache = resolved_cache.expanduser().resolve()
        config_data_dir = CONFIG_DATA_DIR.resolve()
        if resolved_cache.is_relative_to(self._data_dir) or resolved_cache.is_relative_to(
            config_data_dir
        ):
            raise ValueError(
                f"Cloud pricing cache_path ({resolved_cache}) must NEVER reside inside "
                f"DATA_DIR ({self._data_dir} / {config_data_dir})"
            )

        self._cache_path = resolved_cache
        self._ttl_s = float(ttl_s)
        self._enable_live_fetch = bool(enable_live_fetch)

        self._lock = threading.RLock()
        self._refresh_in_flight = False
        self._last_refresh_mono: float | None = None
        self._last_refresh_utc: str | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._circuit_state: Literal["closed", "open", "half_open"] = "closed"
        self._open_until_mono = 0.0

        try:
            self._catalog = load_hardware_catalog(data_dir=self._data_dir, verify=False)
        except DatasetIntegrityError:
            self._catalog = load_hardware_catalog(data_dir=CONFIG_DATA_DIR, verify=False)
        fx_ref = self._catalog.get("fx_reference", {})
        self._fx_chf_per_usd = float(fx_ref.get("chf_per_usd", 0.83922))
        self._fx_as_of = str(fx_ref.get("date", "2026-11-20"))
        self._fx_source = str(
            fx_ref.get("source", "ECB reference exchange rate via Frankfurter API")
        )

        # In-memory live Azure overrides: gpu_id -> {on_demand_usd, spot_usd, as_of, ...}
        self._live_azure: dict[str, dict[str, Any]] = {}
        self._live_loaded_from_disk = False
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        if not self._cache_path.is_file():
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("version") != "1.0.0":
            return
        azure_items = payload.get("azure_live")
        if not isinstance(azure_items, dict):
            return
        now_dt = datetime.now(timezone.utc)
        loaded: dict[str, dict[str, Any]] = {}
        for gpu_id, row in azure_items.items():
            if not isinstance(row, dict):
                continue
            as_of_dt = _parse_iso_utc(row.get("as_of"))
            if as_of_dt is None:
                continue
            age_s = (now_dt - as_of_dt).total_seconds()
            if 0.0 <= age_s <= MAX_CACHED_LIVE_AGE_S:
                loaded[gpu_id] = dict(row)
        if loaded:
            self._live_azure = loaded
            self._live_loaded_from_disk = True
            self._last_refresh_utc = payload.get("updated_utc")

    def _save_disk_cache_locked(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": "1.0.0",
                "updated_utc": self._last_refresh_utc or _utc_now_iso(),
                "azure_live": self._live_azure,
            }
            fd, tmp_name = tempfile.mkstemp(
                prefix=".cloud_pricing_", suffix=".tmp", dir=str(self._cache_path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp_name, self._cache_path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError:
            pass

    def _fetch_azure_gpu_price(self, gpu_id: str, spec: dict[str, Any]) -> dict[str, Any] | None:
        arm_sku = spec["arm_sku"]
        arm_region = spec["arm_region"]
        gpus_per_inst = int(spec["gpus_per_instance"])
        odata_filter = (
            f"serviceName eq 'Virtual Machines' "
            f"and armSkuName eq '{arm_sku}' "
            f"and armRegionName eq '{arm_region}'"
        )
        query = urllib.parse.urlencode(
            {"api-version": AZURE_API_VERSION, "$filter": odata_filter}
        )
        url = f"{AZURE_RETAIL_PRICES_URL}?{query}"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "prices.azure.com":
            raise ValueError(f"Disallowed pricing URL host: {parsed.netloc}")

        req = urllib.request.Request(url, headers={"User-Agent": "CalorGrid/1.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_bytes) > MAX_RESPONSE_BYTES:
                raise ValueError("Azure Retail Prices response exceeded 1 MB limit")
            data = json.loads(raw_bytes.decode("utf-8"))

        items = data.get("Items", [])
        if not isinstance(items, list):
            return None

        od_prices: list[float] = []
        spot_prices: list[float] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if (
                item.get("type") != "Consumption"
                or item.get("unitOfMeasure") != "1 Hour"
                or item.get("currencyCode") != "USD"
            ):
                continue
            prod_name = str(item.get("productName", ""))
            meter_name = str(item.get("meterName", ""))
            sku_name = str(item.get("skuName", ""))
            if "Windows" in prod_name:
                continue
            if "Low Priority" in meter_name or "Low Priority" in sku_name:
                continue
            try:
                price = float(item.get("retailPrice") or item.get("unitPrice") or 0.0)
            except (TypeError, ValueError):
                continue
            if price <= 0.0:
                continue
            if "Spot" in meter_name or "Spot" in sku_name:
                spot_prices.append(price)
            else:
                od_prices.append(price)

        if not od_prices and not spot_prices:
            return None

        inst_od = min(od_prices) if od_prices else None
        inst_spot = min(spot_prices) if spot_prices else None
        per_gpu_od = round(inst_od / gpus_per_inst, 4) if inst_od is not None else None
        per_gpu_spot = round(inst_spot / gpus_per_inst, 4) if inst_spot is not None else None
        return {
            "region": arm_region,
            "instance_type": arm_sku,
            "min_purchasable_gpus": gpus_per_inst,
            "native_instance_on_demand_usd_per_hr": round(inst_od, 4)
            if inst_od is not None
            else None,
            "native_instance_spot_usd_per_hr": round(inst_spot, 4)
            if inst_spot is not None
            else None,
            "on_demand_usd_per_gpu_hr": per_gpu_od,
            "spot_usd_per_gpu_hr": per_gpu_spot,
            "offer_status": spec["offer_status"],
            "as_of": _utc_now_iso(),
            "notes": spec.get("notes"),
        }

    def refresh_now(self, force: bool = False) -> dict[str, Any]:
        """Synchronously fetch Azure Retail Prices; respects circuit breaker unless force=True."""
        now_mono = time.monotonic()
        with self._lock:
            if not force and self._circuit_state == "open":
                if now_mono < self._open_until_mono:
                    return self.get_pricing_snapshot(trigger_refresh=False)
                self._circuit_state = "half_open"

        fetched: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for gpu_id, spec in _AZURE_SKU_QUERIES.items():
            try:
                row = self._fetch_azure_gpu_price(gpu_id, spec)
                if row is not None:
                    fetched[gpu_id] = row
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                errors.append(f"{gpu_id}: {exc}")

        with self._lock:
            finish_mono = time.monotonic()
            if fetched:
                self._live_azure.update(fetched)
                self._live_loaded_from_disk = False
                self._last_refresh_mono = finish_mono
                self._last_refresh_utc = _utc_now_iso()
                self._consecutive_failures = 0
                self._circuit_state = "closed"
                self._last_error = "; ".join(errors) if errors else None
                self._save_disk_cache_locked()
            else:
                self._consecutive_failures += 1
                self._last_error = "; ".join(errors) if errors else "No Azure rows returned"
                if (
                    self._circuit_state == "half_open"
                    or self._consecutive_failures >= FAILURE_THRESHOLD
                ):
                    self._circuit_state = "open"
                    self._open_until_mono = finish_mono + COOLDOWN_S

        return self.get_pricing_snapshot(trigger_refresh=False)

    def _background_refresh_worker(self) -> None:
        try:
            self.refresh_now(force=False)
        finally:
            with self._lock:
                self._refresh_in_flight = False

    def _maybe_trigger_background_refresh_locked(self) -> None:
        if not self._enable_live_fetch or self._refresh_in_flight:
            return
        now_mono = time.monotonic()
        if self._circuit_state == "open" and now_mono < self._open_until_mono:
            return
        due = (
            self._last_refresh_mono is None
            or (now_mono - self._last_refresh_mono) >= self._ttl_s
        )
        if not due:
            return
        self._refresh_in_flight = True
        t = threading.Thread(
            target=self._background_refresh_worker,
            name="calorgrid-cloud-pricing-refresh",
            daemon=True,
        )
        t.start()

    def _build_provider_offer(
        self,
        gpu_id: str,
        provider: ProviderId,
        now_mono: float,
        now_dt: datetime,
    ) -> dict[str, Any]:
        pinned = _PINNED_GPU_OFFERS[gpu_id][provider]
        live_row = self._live_azure.get(gpu_id) if provider == "azure" else None

        source: PriceSource
        as_of = _PINNED_FALLBACK_AS_OF
        od_usd = pinned["on_demand_usd"]
        spot_usd = pinned["spot_usd"]
        min_gpus = pinned["min_purchasable_gpus"]
        region = pinned["region"]
        inst_type = pinned["instance_type"]
        offer_status = pinned["offer_status"]
        notes = pinned.get("notes")
        native_od = round(od_usd * min_gpus, 4) if od_usd is not None and min_gpus else None
        native_spot = round(spot_usd * min_gpus, 4) if spot_usd is not None and min_gpus else None

        if live_row is not None:
            as_of_dt = _parse_iso_utc(live_row.get("as_of"))
            age_s = (now_dt - as_of_dt).total_seconds() if as_of_dt else MAX_CACHED_LIVE_AGE_S + 1
            if 0.0 <= age_s <= MAX_CACHED_LIVE_AGE_S:
                if (
                    not self._live_loaded_from_disk
                    and self._last_refresh_mono is not None
                    and (now_mono - self._last_refresh_mono) <= self._ttl_s
                ):
                    source = "fresh_live"
                else:
                    source = "cached_live"
                as_of = str(live_row["as_of"])
                od_usd = live_row.get("on_demand_usd_per_gpu_hr")
                spot_usd = live_row.get("spot_usd_per_gpu_hr")
                min_gpus = live_row.get("min_purchasable_gpus", min_gpus)
                region = live_row.get("region", region)
                inst_type = live_row.get("instance_type", inst_type)
                offer_status = live_row.get("offer_status", offer_status)
                native_od = live_row.get("native_instance_on_demand_usd_per_hr", native_od)
                native_spot = live_row.get("native_instance_spot_usd_per_hr", native_spot)
                notes = live_row.get("notes", notes)
            else:
                source = "pinned_fallback"
        elif offer_status == "not_offered":
            source = "unavailable"
        else:
            source = "pinned_fallback"

        fx = self._fx_chf_per_usd
        od_chf = round(od_usd * fx, 4) if od_usd is not None else None
        spot_chf = round(spot_usd * fx, 4) if spot_usd is not None else None

        return {
            "provider": provider,
            "provider_label": _PROVIDER_LABELS[provider],
            "region": region,
            "instance_type": inst_type,
            "min_purchasable_gpus": min_gpus,
            "on_demand_usd_per_gpu_hr": od_usd,
            "spot_usd_per_gpu_hr": spot_usd,
            "on_demand_chf_per_gpu_hr": od_chf,
            "spot_chf_per_gpu_hr": spot_chf,
            "native_instance_on_demand_usd_per_hr": native_od,
            "native_instance_spot_usd_per_hr": native_spot,
            "offer_status": offer_status,
            "source": source,
            "as_of": as_of,
            "fx_chf_per_usd": fx,
            "fx_as_of": self._fx_as_of,
            "includes_host_overhead": bool(pinned.get("includes_host_overhead", True)),
            "notes": notes,
        }

    def _compute_gpu_calorgrid_rates(self, gpu_spec: dict[str, Any]) -> dict[str, Any]:
        gpu_id = gpu_spec["id"]
        od_chf = _PUBLISHED_GPU_RATES_CHF[gpu_id]
        spot_chf = round(od_chf * SPOT_PRICE_RATIO, 4)
        fx = self._fx_chf_per_usd

        # Vaibhav break-even at year-round summer sink (92% util, C6 = 0.25267)
        capex_gpu = float(gpu_spec["capex_chf_per_gpu"])
        life = int(gpu_spec.get("lifetime_years", HARDWARE_LIFE_YEARS))
        capex_ann = annuity(capex_gpu, life, DISCOUNT_RATE)
        opex_yr = 0.0208 * capex_gpu
        typical_kw = float(gpu_spec["typical_kw_per_gpu"])
        idle_kw = float(gpu_spec["idle_kw_per_gpu"])
        eta = float(gpu_spec["eta"])

        sold_yr_sink = 8760.0 * 0.92
        idle_yr_sink = 8760.0 - sold_yr_sink
        elec_yr_sink = sold_yr_sink * typical_kw + idle_yr_sink * idle_kw
        c6_tariff = 0.25267
        heat_val_sink = sold_yr_sink * typical_kw * eta * (c6_tariff / HEAT_PUMP_REF_COP)
        net_cost_sink = capex_ann + opex_yr + elec_yr_sink * c6_tariff - heat_val_sink
        be_sink_chf = max(0.0, net_cost_sink / sold_yr_sink)

        # Residential contrast (~45% util, H4 = 0.3325)
        sold_yr_res = 8760.0 * 0.45
        idle_yr_res = 8760.0 - sold_yr_res
        elec_yr_res = sold_yr_res * typical_kw + idle_yr_res * idle_kw
        heat_val_res = (
            sold_yr_res
            * typical_kw
            * eta
            * (ELCOM_BASEL_H4_CHF_PER_KWH / HEAT_PUMP_REF_COP)
        )
        net_cost_res = capex_ann + opex_yr + elec_yr_res * ELCOM_BASEL_H4_CHF_PER_KWH - heat_val_res
        be_res_chf = max(0.0, net_cost_res / sold_yr_res)

        return {
            "on_demand_chf_per_gpu_hr": od_chf,
            "spot_chf_per_gpu_hr": spot_chf,
            "on_demand_usd_per_gpu_hr": round(od_chf / fx, 4),
            "spot_usd_per_gpu_hr": round(spot_chf / fx, 4),
            "summer_sink_breakeven_chf_per_gpu_hr": round(be_sink_chf, 4),
            "residential_breakeven_chf_per_gpu_hr": round(be_res_chf, 4),
            "source": "calorgrid_catalog_model",
            "as_of": _utc_now_iso(),
            "fx_chf_per_usd": fx,
        }

    def get_pricing_snapshot(self, trigger_refresh: bool = True) -> dict[str, Any]:
        """Return non-blocking GPU & server cloud vs CalorGrid pricing snapshot."""
        with self._lock:
            if trigger_refresh:
                self._maybe_trigger_background_refresh_locked()

            now_mono = time.monotonic()
            now_dt = datetime.now(timezone.utc)
            fx = self._fx_chf_per_usd

            gpu_comparisons: list[dict[str, Any]] = []
            gpu_rate_lookup: dict[str, dict[str, Any]] = {}

            for gpu_spec in self._catalog.get("gpu_products", []):
                gid = gpu_spec["id"]
                calorgrid_rates = self._compute_gpu_calorgrid_rates(gpu_spec)
                providers: dict[str, dict[str, Any]] = {}
                od_cloud_usd: list[tuple[str, float]] = []
                spot_cloud_usd: list[tuple[str, float]] = []

                for prov in ("aws", "gcp", "azure", "coreweave", "lambda"):
                    offer = self._build_provider_offer(gid, prov, now_mono, now_dt)
                    providers[prov] = offer
                    if offer["on_demand_usd_per_gpu_hr"] is not None:
                        od_cloud_usd.append((prov, float(offer["on_demand_usd_per_gpu_hr"])))
                    if offer["spot_usd_per_gpu_hr"] is not None:
                        spot_cloud_usd.append((prov, float(offer["spot_usd_per_gpu_hr"])))

                cheapest_od = min(od_cloud_usd, key=lambda item: item[1]) if od_cloud_usd else None
                hyperscaler_od = [
                    item[1] for item in od_cloud_usd if item[0] in ("aws", "gcp", "azure")
                ]
                ref_od_usd = (
                    sum(hyperscaler_od) / len(hyperscaler_od)
                    if hyperscaler_od
                    else (cheapest_od[1] if cheapest_od else None)
                )
                ref_prov = (
                    "hyperscaler_mean"
                    if hyperscaler_od
                    else (cheapest_od[0] if cheapest_od else None)
                )

                cg_od_usd = calorgrid_rates["on_demand_usd_per_gpu_hr"]
                cg_spot_usd = calorgrid_rates["spot_usd_per_gpu_hr"]
                od_savings_pct = (
                    round(100.0 * (1.0 - cg_od_usd / ref_od_usd), 1)
                    if ref_od_usd and ref_od_usd > 0
                    else None
                )
                spot_savings_pct = (
                    round(100.0 * (1.0 - cg_spot_usd / ref_od_usd), 1)
                    if ref_od_usd and ref_od_usd > 0
                    else None
                )

                row_out = {
                    "gpu_id": gid,
                    "name": gpu_spec["name"],
                    "architecture": gpu_spec["architecture"],
                    "vram_gb": gpu_spec["vram_gb"],
                    "node_gpu_count": gpu_spec["node_gpu_count"],
                    "typical_kw_per_gpu": gpu_spec["typical_kw_per_gpu"],
                    "supply_temp_c": gpu_spec["supply_temp_c"],
                    "calorgrid": calorgrid_rates,
                    "providers": providers,
                    "cheapest_cloud_on_demand": (
                        {"provider": cheapest_od[0], "usd_per_gpu_hr": cheapest_od[1]}
                        if cheapest_od
                        else None
                    ),
                    "cheapest_cloud_spot": (
                        {
                            "provider": min(spot_cloud_usd, key=lambda x: x[1])[0],
                            "usd_per_gpu_hr": min(spot_cloud_usd, key=lambda x: x[1])[1],
                        }
                        if spot_cloud_usd
                        else None
                    ),
                    "reference_cloud_on_demand_usd_per_gpu_hr": (
                        round(ref_od_usd, 4) if ref_od_usd is not None else None
                    ),
                    "on_demand_savings_vs_cloud_pct": od_savings_pct,
                    "spot_savings_vs_cloud_pct": spot_savings_pct,
                }
                gpu_comparisons.append(row_out)
                gpu_rate_lookup[gid] = {
                    "on_demand_chf": calorgrid_rates["on_demand_chf_per_gpu_hr"],
                    "spot_chf": calorgrid_rates["spot_chf_per_gpu_hr"],
                    "on_demand_usd": cg_od_usd,
                    "spot_usd": cg_spot_usd,
                    "benchmark_provider": ref_prov,
                    "benchmark_usd": round(ref_od_usd, 4) if ref_od_usd is not None else None,
                    "on_demand_savings_pct": od_savings_pct,
                    "spot_savings_pct": spot_savings_pct,
                }

            b200_gpu_cmp = next(
                (g for g in gpu_comparisons if g["gpu_id"] == "b200_sxm_180gb"), None
            )

            server_comparisons: list[dict[str, Any]] = []
            for srv in self._catalog.get("server_models", []):
                sid = srv["id"]
                od_chf = _PUBLISHED_SERVER_RATES_CHF[sid]
                spot_chf = round(od_chf * SPOT_PRICE_RATIO, 4)
                od_usd = round(od_chf / fx, 4)
                spot_usd = round(spot_chf / fx, 4)

                if sid == "b200_ai_10kw" and b200_gpu_cmp is not None:
                    azure_b200_od_gpu = b200_gpu_cmp["providers"]["azure"][
                        "on_demand_usd_per_gpu_hr"
                    ]
                    cloud_equiv = {
                        "cloud_equivalent_type": "gpu_8x_node",
                        "aws_instance": "p6-b200.48xlarge (8x B200)",
                        "aws_on_demand_usd_per_hr": b200_gpu_cmp["providers"]["aws"][
                            "native_instance_on_demand_usd_per_hr"
                        ],
                        "azure_instance": "ND128isr_NDR_GB200_v6 (4x GB200 proxy scaled 2x)",
                        "azure_on_demand_usd_per_hr": (
                            round(azure_b200_od_gpu * 8.0, 2)
                            if azure_b200_od_gpu is not None
                            else None
                        ),
                    }
                    ref_srv_usd = cloud_equiv["aws_on_demand_usd_per_hr"]
                else:
                    cloud_equiv = dict(_CPU_SERVER_CLOUD_BASELINES[sid])
                    ref_srv_usd = float(cloud_equiv["aws_on_demand_usd_per_hr"])

                od_sav = (
                    round(100.0 * (1.0 - od_usd / ref_srv_usd), 1)
                    if ref_srv_usd and ref_srv_usd > 0
                    else None
                )
                spot_sav = (
                    round(100.0 * (1.0 - spot_usd / ref_srv_usd), 1)
                    if ref_srv_usd and ref_srv_usd > 0
                    else None
                )

                server_comparisons.append(
                    {
                        "server_model_id": sid,
                        "name": srv["name"],
                        "electrical_kw": srv["electrical_kw"],
                        "typical_kw": srv["typical_kw"],
                        "idle_kw": srv["idle_kw"],
                        "eta": srv["eta"],
                        "supply_temp_c": srv["supply_temp_c"],
                        "gpu_model": srv["gpu_model"],
                        "gpu_count": srv["gpu_count"],
                        "capex_chf": srv["capex_chf"],
                        "commercial_street_capex_chf": srv["commercial_street_capex_chf"],
                        "calorgrid": {
                            "on_demand_chf_per_server_hr": od_chf,
                            "spot_chf_per_server_hr": spot_chf,
                            "on_demand_usd_per_server_hr": od_usd,
                            "spot_usd_per_server_hr": spot_usd,
                            "source": "calorgrid_catalog_model",
                            "as_of": _utc_now_iso(),
                            "fx_chf_per_usd": fx,
                        },
                        "cloud_baseline": {
                            **cloud_equiv,
                            "source": "pinned_fallback",
                            "as_of": _PINNED_FALLBACK_AS_OF,
                            "fx_chf_per_usd": fx,
                        },
                        "on_demand_savings_vs_cloud_pct": od_sav,
                        "spot_savings_vs_cloud_pct": spot_sav,
                    }
                )

            cache_status = {
                "cache_path": str(self._cache_path),
                "azure_live_gpus_cached": len(self._live_azure),
                "loaded_from_disk": self._live_loaded_from_disk,
                "last_refresh_utc": self._last_refresh_utc,
                "circuit_state": self._circuit_state,
                "consecutive_failures": self._consecutive_failures,
                "refresh_in_flight": self._refresh_in_flight,
                "last_error": self._last_error,
            }

            return {
                "schema_version": "1.1.0",
                "fx_chf_per_usd": fx,
                "fx_as_of": self._fx_as_of,
                "fx_source": self._fx_source,
                "spot_price_ratio": SPOT_PRICE_RATIO,
                "demand_tiers": copy.deepcopy(DEMAND_TIER_SPECS),
                "specialist_cloud_benchmarks": load_specialist_cloud_benchmarks(),
                "swiss_gas_history": load_swiss_gas_history(),
                "cache_status": cache_status,
                "gpu_comparisons": gpu_comparisons,
                "server_sku_comparisons": server_comparisons,
                "summer_sinks": compute_summer_sink_cost_model(fx_chf_per_usd=fx),
                "_rate_lookup": gpu_rate_lookup,
            }

    def get_unit_rates(
        self,
        unit_mode: Literal["gpu", "server"],
        gpu_or_sku: str,
        tier: ComputeTier,
    ) -> dict[str, Any]:
        """Return unit CHF/USD rate and cloud savings % across all 6 compute demand tiers."""
        if tier not in DEMAND_TIER_SPECS:
            raise ValueError(f"Unknown compute tier '{tier}'")
        tier_spec = DEMAND_TIER_SPECS[tier]
        mult = float(tier_spec["price_multiplier"])
        busy_share = float(tier_spec["busy_share"])
        snap = self.get_pricing_snapshot(trigger_refresh=False)
        fx = snap["fx_chf_per_usd"]
        if unit_mode == "gpu":
            row = next(
                (g for g in snap["gpu_comparisons"] if g["gpu_id"] == gpu_or_sku), None
            )
            if row is None:
                raise ValueError(f"Unknown gpu product '{gpu_or_sku}'")
            cg = row["calorgrid"]
            od_chf = float(cg["on_demand_chf_per_gpu_hr"])
            if tier == "on_demand":
                rate_chf = od_chf
            elif tier == "spot":
                rate_chf = float(cg["spot_chf_per_gpu_hr"])
            else:
                rate_chf = round(od_chf * mult, 4)
            rate_usd = round(rate_chf / fx, 4)
            bench_usd = row["reference_cloud_on_demand_usd_per_gpu_hr"]
            if tier == "on_demand":
                sav_pct = row["on_demand_savings_vs_cloud_pct"]
            elif tier == "spot":
                sav_pct = row["spot_savings_vs_cloud_pct"]
            elif bench_usd and bench_usd > 0:
                effective_usd = rate_usd * busy_share
                sav_pct = round(100.0 * (1.0 - effective_usd / float(bench_usd)), 1)
            else:
                sav_pct = None
            return {
                "unit_rate_chf_per_hr": rate_chf,
                "unit_rate_usd_per_hr": rate_usd,
                "fx_chf_per_usd": fx,
                "cloud_savings_pct": sav_pct,
                "cloud_benchmark_usd_per_hr": bench_usd,
                "cloud_benchmark_provider": "AWS/GCP/Azure mean" if bench_usd else None,
                "tier_spec": dict(tier_spec),
            }

        row_srv = next(
            (s for s in snap["server_sku_comparisons"] if s["server_model_id"] == gpu_or_sku),
            None,
        )
        if row_srv is None:
            raise ValueError(f"Unknown server SKU '{gpu_or_sku}'")
        cg = row_srv["calorgrid"]
        od_chf = float(cg["on_demand_chf_per_server_hr"])
        if tier == "on_demand":
            rate_chf = od_chf
        elif tier == "spot":
            rate_chf = float(cg["spot_chf_per_server_hr"])
        else:
            rate_chf = round(od_chf * mult, 4)
        rate_usd = round(rate_chf / fx, 4)
        bench_usd = row_srv["cloud_baseline"].get("aws_on_demand_usd_per_hr")
        if tier == "on_demand":
            sav_pct = row_srv["on_demand_savings_vs_cloud_pct"]
        elif tier == "spot":
            sav_pct = row_srv["spot_savings_vs_cloud_pct"]
        elif bench_usd and bench_usd > 0:
            effective_usd = rate_usd * busy_share
            sav_pct = round(100.0 * (1.0 - effective_usd / float(bench_usd)), 1)
        else:
            sav_pct = None
        return {
            "unit_rate_chf_per_hr": rate_chf,
            "unit_rate_usd_per_hr": rate_usd,
            "fx_chf_per_usd": fx,
            "cloud_savings_pct": sav_pct,
            "cloud_benchmark_usd_per_hr": bench_usd,
            "cloud_benchmark_provider": "AWS EC2 baseline" if bench_usd else None,
            "tier_spec": dict(tier_spec),
        }

    def get_public_pricing_payload(self, trigger_refresh: bool = True) -> dict[str, Any]:
        """Return API-ready pricing payload without internal helper keys."""
        snap = copy.deepcopy(self.get_pricing_snapshot(trigger_refresh=trigger_refresh))
        snap.pop("_rate_lookup", None)
        return snap
