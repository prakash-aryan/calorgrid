"""Collect the figures that describe the fleet machine, each from a public source.

    python scripts/fetch_prices.py        # writes data/fleet_machine.json

The fleet is one machine: a server with eight NVIDIA H100 GPUs, at today's
prices. Every figure a page needs about it is in this one file, so the power
draw, the purchase price and the market price describe the same GPU.

    machine       the GPU and the MLPerf power profile of the measured server
    hardware      purchase price of an 8-GPU H100 server: low, typical and high.
                  No list price is published; this is a market estimate (fetched)
    GPU-hour      what an H100 hour rents for, per GPU:
                    large cloud       AWS on-demand price list and spot price feed (fetched)
                    specialist clouds the providers' own pricing pages (recorded by hand:
                                      their pages load prices by script)
    electricity   ElCom tariff of IWB Basel, household category H4, total price (fetched)
    exchange rate ECB reference rates for the year so far, through the Frankfurter API
    speed         how much faster an H100 is than each GPU of the job trace, from
                  NVIDIA's published peak mixed-precision throughput (recorded by hand)

No API key is needed. Figures recorded by hand carry the page they were read
from and the date; update them here when they change.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import statistics
import urllib.request
from datetime import date
from pathlib import Path

YEAR = 2026
GPU = "NVIDIA H100"
GPUS_PER_SERVER = 8
POWER_PROFILE = "5.0-0023/llama2-70b-99/Offline"     # id in data/mlperf_power.json

# --- large cloud: AWS, fetched ---
AWS_INSTANCE = "p5.48xlarge"                 # eight H100
AWS_REGION_NAME = "US East (N. Virginia)"
AWS_ON_DEMAND = ("https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/"
                 "ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json")
AWS_ON_DEMAND_PAGE = "https://aws.amazon.com/ec2/pricing/on-demand/"
AWS_SPOT_FEED = "https://website.spot.ec2.aws.a2z.com/spot.js"
AWS_SPOT_PAGE = "https://aws.amazon.com/ec2/spot/pricing/"
AWS_SPOT_REGIONS = {"us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)",
                    "us-west-2": "US West (Oregon)"}

# --- specialist GPU clouds, recorded by hand from each provider's pricing page ---
# name, page, date read, what the on-demand price is for, USD per GPU-hour,
# what the lowest tier is (spot, preemptible or community), USD per GPU-hour or None
SPECIALISTS = [
    ("RunPod", "https://www.runpod.io/pricing", "2026-11-21",
     "H100 SXM 80 GB, Secure Cloud", 4.39, "H100 SXM 80 GB, Community Cloud, from", 2.69),
    ("Lambda", "https://lambda.com/pricing", "2026-11-21",
     "HGX H100 80 GB instance", 4.59, None, None),
    ("CoreWeave", "https://www.coreweave.com/pricing", "2026-11-21",
     "HGX H100 node of 8 GPUs, North America, on-demand 49.24 USD per node", 49.24 / 8,
     "the same node on spot, 19.71 USD per node", 19.71 / 8),
    ("Together AI", "https://www.together.ai/pricing", "2026-11-21",
     "GPU clusters, HGX H100, on-demand", 3.99, "GPU clusters, HGX H100, preemptible", 1.99),
]

# --- hardware: no list price is published; a market estimate, fetched ---
HARDWARE_PAGE = "https://www.mercatus-ai.com/blog/h100-server-price"
HARDWARE_PATTERN = (r"8-GPU HGX H100 server costs \$([0-9,]+) to \$([0-9,]+) in 2026, "
                    r"with roughly \$([0-9,]+) as the typical")

# --- speed: peak mixed-precision (FP16) throughput in TFLOPS, recorded by hand ---
H100_TFLOPS = 1979 / 2          # NVIDIA lists 1,979 with sparsity; half of it without
H100_SPEC_PAGE = "https://www.nvidia.com/en-us/data-center/h100/"
TRACE_GPU_PAGE = "https://www.nvidia.com/en-au/data-center/tesla-t4/"   # one table, three GPUs
TRACE_GPU_TFLOPS = {"T4": 65.0, "V100": 112.0, "P100": 21.0}
UNNAMED_GPU = ("MISC", "T4")    # the trace calls some GPUs only MISC; treated as a T4
SPEC_DATE = "2026-11-21"

ELCOM_SITE = "https://www.strompreis.elcom.admin.ch/"
ELCOM_API = ELCOM_SITE + "api/graphql"
BASEL = "2701"                               # municipality number of Basel
FX_API = "https://api.frankfurter.dev/v1/{year}-01-01..{today}?base=USD&symbols=CHF"


def fetch(url: str, body: dict | None = None) -> str:
    data = json.dumps(body).encode() if body else None
    headers = {"User-Agent": "digitalheat-price-fetch", "Content-Type": "application/json"}
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=120) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def page_text(source: str) -> str:
    source = re.sub(r"(?is)<(script|style).*?</\1>", " ", source)
    return re.sub(r"[|\s]*\|[|\s]*", " | ", html.unescape(re.sub(r"(?s)<[^>]+>", "|", source)))


def aws_on_demand() -> tuple[float, str]:
    """USD per hour of the eight-GPU instance, and the date AWS published the list."""
    data = json.loads(fetch(AWS_ON_DEMAND))
    row = next(r for r in data["regions"][AWS_REGION_NAME].values()
               if r.get("Instance Type") == AWS_INSTANCE)
    return float(row["price"]), data["manifest"]["hawkFilePublicationDate"][:10]


def aws_spot() -> dict[str, float]:
    """USD per hour of the eight-GPU instance on spot, by region."""
    feed = json.loads(re.search(r"callback\((.*)\)\s*;?\s*$", fetch(AWS_SPOT_FEED), re.S).group(1))
    prices = {}
    for region in feed["config"]["regions"]:
        if region["region"] not in AWS_SPOT_REGIONS:
            continue
        for family in region["instanceTypes"]:
            for size in family["sizes"]:
                if size["size"] == AWS_INSTANCE:
                    linux = next(v for v in size["valueColumns"] if v["name"] == "linux")
                    prices[region["region"]] = float(linux["prices"]["USD"])
    if not prices:
        raise KeyError(f"{AWS_INSTANCE} is not in the spot feed")
    return prices


def large_cloud(today: str) -> dict:
    on_demand, published = aws_on_demand()
    spot = aws_spot()
    cheapest = min(spot, key=spot.get)
    others = "; ".join(f"{AWS_SPOT_REGIONS[r]} {p:.2f}" for r, p in spot.items() if r != cheapest)
    return {
        "id": "h100-aws", "gpu": GPU, "year": YEAR, "market": "large cloud",
        "instance": f"{AWS_INSTANCE}, {GPUS_PER_SERVER} GPUs, priced per GPU",
        "provider": f"AWS, {AWS_REGION_NAME}, Linux",
        "on_demand_usd": round(on_demand / GPUS_PER_SERVER, 4),
        "on_demand_source": f"AWS on-demand price list published {published}: "
                            f"{on_demand:.2f} USD per instance-hour",
        "on_demand_url": AWS_ON_DEMAND_PAGE,
        "spot_usd": round(spot[cheapest] / GPUS_PER_SERVER, 4),
        "spot_source": f"AWS spot price feed on {today}, {AWS_SPOT_REGIONS[cheapest]}, the "
                       f"cheapest of three US regions: {spot[cheapest]:.2f} USD per "
                       f"instance-hour ({others})",
        "spot_url": AWS_SPOT_PAGE,
    }


def specialist_clouds() -> dict:
    providers = [{
        "name": name, "url": url, "read_on": read_on,
        "on_demand_usd": round(on_demand, 4), "on_demand_for": on_demand_for,
        "low_usd": None if low is None else round(low, 4), "low_for": low_for,
    } for name, url, read_on, on_demand_for, on_demand, low_for, low in SPECIALISTS]
    with_low = [p for p in providers if p["low_usd"] is not None]
    read_on = max(p["read_on"] for p in providers)
    return {
        "id": "h100-specialist", "gpu": GPU, "year": YEAR, "market": "specialist GPU clouds",
        "instance": "one H100, or an 8-GPU H100 node priced per GPU",
        "provider": "median of " + ", ".join(p["name"] for p in providers),
        "on_demand_usd": round(statistics.median(p["on_demand_usd"] for p in providers), 4),
        "on_demand_source": f"median on-demand price of {len(providers)} providers, their "
                            f"pricing pages read on {read_on}",
        "on_demand_url": providers[0]["url"],
        "spot_usd": round(statistics.median(p["low_usd"] for p in with_low), 4),
        "spot_source": f"median of the lowest tier (spot, preemptible or community) of "
                       f"{len(with_low)} providers, their pricing pages read on {read_on}",
        "spot_url": with_low[0]["url"],
        "method": "recorded by hand", "providers": providers,
    }


def hardware() -> dict:
    text = page_text(fetch(HARDWARE_PAGE))
    low, high, typical = (int(v.replace(",", "")) for v in re.search(HARDWARE_PATTERN, text).groups())
    verified = re.search(r"Last verified: ([0-9-]+)", text)
    return {
        "system": f"server with {GPUS_PER_SERVER} x {GPU} (HGX H100, built by a server maker)",
        "usd": typical, "low_usd": low, "high_usd": high, "year": YEAR, "method": "estimate",
        "source": "Mercatus Compute, 'NVIDIA H100 Server Price 2026'"
                  + (f", last verified {verified.group(1)}" if verified else "")
                  + ": price range and typical price from the large server makers. "
                    "No list price is published; the source is a compute marketplace",
        "url": HARDWARE_PAGE,
    }


def speed() -> dict:
    factors = {gpu: {"tflops": tflops, "factor": round(H100_TFLOPS / tflops, 1),
                     "source": f"NVIDIA comparison table on the T4 page, read on {SPEC_DATE}",
                     "url": TRACE_GPU_PAGE}
               for gpu, tflops in TRACE_GPU_TFLOPS.items()}
    label, stand_in = UNNAMED_GPU
    factors[label] = {"factor": factors[stand_in]["factor"], "assumed": True,
                      "source": f"the trace names these GPUs only {label}; treated as a "
                                f"{stand_in}, the most common named GPU in the examples"}
    return {
        "fleet_gpu": GPU, "fleet_tflops": H100_TFLOPS,
        "measure": "peak mixed-precision (FP16) throughput in TFLOPS, from NVIDIA's "
                   "published specifications",
        "fleet_source": f"NVIDIA H100 page, read on {SPEC_DATE}: FP16 Tensor Core 1,979 TFLOPS "
                        "with sparsity, half of that without",
        "fleet_url": H100_SPEC_PAGE,
        "note": "A job's speed-up is at most this ratio: time spent loading data or on the "
                "CPU does not shrink with a faster GPU.",
        "factors": factors,
    }


def elcom(year: int) -> dict:
    query = ('query { observations(locale: "de", filters: { period: ["%d"], category: ["H4"], '
             'municipality: ["%s"] }) { ... on OperatorObservation { municipality operator '
             'period category operatorLabel value(priceComponent: total) } } }' % (year, BASEL))
    row = json.loads(fetch(ELCOM_API, {"query": query}))["data"]["observations"][0]
    return {"year": year, "chf_per_kwh": round(row["value"] / 100, 5),
            "source": f"ElCom, {row['operatorLabel']}, household category H4, total price {year}",
            "url": ELCOM_SITE}


def exchange(today: str) -> dict:
    rates = json.loads(fetch(FX_API.format(year=YEAR, today=today)))["rates"]
    values = [day["CHF"] for day in rates.values()]
    return {"chf_per_usd": round(sum(values) / len(values), 4),
            "source": f"ECB reference rates, mean of {len(values)} trading days in {YEAR} up to "
                      f"{max(rates)} (Frankfurter API)",
            "url": "https://frankfurter.dev/"}


def build() -> dict:
    today = date.today().isoformat()
    return {
        "retrieved": today,
        "machine": {"gpu": GPU, "gpus_per_server": GPUS_PER_SERVER, "year": YEAR,
                    "power_profile": POWER_PROFILE,
                    "note": "The measured server carries the H100 NVL variant (94 GB). Prices "
                            "are quoted for the H100 in general."},
        "hardware": hardware(),
        "gpu_hour": [specialist_clouds(), large_cloud(today)],
        "electricity": [elcom(YEAR)],
        "exchange": exchange(today),
        "speed": speed(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data/fleet_machine.json"))
    args = parser.parse_args()
    data = build()
    args.out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    hw = data["hardware"]
    print(f"hardware: {hw['low_usd']:,} to {hw['high_usd']:,} USD, typical {hw['usd']:,}")
    for row in data["gpu_hour"]:
        print(f"{row['market']}: on-demand {row['on_demand_usd']} USD, lowest tier "
              f"{row['spot_usd']} USD per GPU-hour")
    print(f"electricity: {data['electricity'][0]['chf_per_kwh']} CHF/kWh; "
          f"exchange: {data['exchange']['chf_per_usd']} CHF per USD")
    print("speed against the trace's GPUs: "
          + ", ".join(f"{gpu} x{row['factor']}" for gpu, row in data["speed"]["factors"].items()))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
