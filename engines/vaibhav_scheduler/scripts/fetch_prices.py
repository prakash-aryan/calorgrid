"""Collect the 2020 prices used by the economics page, each from a public source.

    python scripts/fetch_prices.py        # writes data/prices_2020.json

    GPU-hour      AWS on-demand prices (instance pages) and spot prices (AWS's own spot
                  price feed), both as archived by the Wayback Machine in July 2020
    electricity   ElCom tariff of IWB Basel, household category H4, total price
    exchange rate ECB reference rates for 2020, through the Frankfurter API
    hardware      list price of an 8-GPU server announced in 2020 (NVIDIA press release)

2020 is the year of the Alibaba trace the jobs come from. No API key is needed.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import urllib.request
from datetime import date
from pathlib import Path

WAYBACK = "https://web.archive.org/web/{stamp}{flag}/{url}"
SPOT_FEED = ("20200707205233", "https://website.spot.ec2.aws.a2z.com/spot.js")
AWS_REGION = "us-east"                       # US East (N. Virginia) in the spot feed
GPUS = [   # id, GPU, instance with one GPU, snapshot and page listing its on-demand price
    ("v100-2020", "NVIDIA V100", "p3.2xlarge",
     "20200707225024", "https://aws.amazon.com/ec2/instance-types/p3/"),
    ("t4-2020", "NVIDIA T4", "g4dn.xlarge",
     "20200722071233", "https://aws.amazon.com/ec2/instance-types/g4/"),
]
DGX = ("20200602163205",
       "https://nvidianews.nvidia.com/news/nvidia-ships-worlds-most-advanced-ai-system-nvidia-"
       "dgx-a100-to-fight-covid-19-third-generation-dgx-packs-record-5-petaflops-of-ai-performance")
ELCOM_SITE = "https://www.strompreis.elcom.admin.ch/"
ELCOM_API = ELCOM_SITE + "api/graphql"
BASEL = "2701"                               # municipality number of Basel
FX_API = "https://api.frankfurter.dev/v1/2020-01-01..2020-12-31?base=USD&symbols=CHF"


def fetch(url: str, body: dict | None = None) -> str:
    data = json.dumps(body).encode() if body else None
    headers = {"User-Agent": "digitalheat-price-fetch", "Content-Type": "application/json"}
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=120) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def archived(stamp: str, url: str) -> str:
    """The page exactly as captured (the `id_` flag leaves out the archive's own banner)."""
    return fetch(WAYBACK.format(stamp=stamp, flag="id_", url=url))


def page_text(source: str) -> str:
    source = re.sub(r"(?is)<(script|style).*?</\1>", " ", source)
    return re.sub(r"[|\s]*\|[|\s]*", " | ", html.unescape(re.sub(r"(?s)<[^>]+>", "|", source)))


def on_demand_usd(text: str, instance: str) -> float:
    """First dollar amount in the instance's row of the product-details table."""
    row = text[text.index(f"| {instance} |"):]
    return float(re.search(r"\$([0-9.]+)", row).group(1))


def spot_usd(feed: dict, instance: str) -> float:
    region = next(r for r in feed["config"]["regions"] if r["region"] == AWS_REGION)
    for family in region["instanceTypes"]:
        for size in family["sizes"]:
            if size["size"] == instance:
                linux = next(v for v in size["valueColumns"] if v["name"] == "linux")
                return float(linux["prices"]["USD"])
    raise KeyError(instance)


def elcom(year: int) -> dict:
    query = ('query { observations(locale: "de", filters: { period: ["%d"], category: ["H4"], '
             'municipality: ["%s"] }) { ... on OperatorObservation { municipality operator '
             'period category operatorLabel value(priceComponent: total) } } }' % (year, BASEL))
    row = json.loads(fetch(ELCOM_API, {"query": query}))["data"]["observations"][0]
    return {"year": year, "chf_per_kwh": round(row["value"] / 100, 5),
            "source": f"ElCom, {row['operatorLabel']}, household category H4, total price {year}",
            "url": ELCOM_SITE}


def build() -> dict:
    feed_text = archived(*SPOT_FEED)
    feed = json.loads(re.search(r"callback\((.*)\)\s*;?\s*$", feed_text, re.S).group(1))
    gpu_hour = []
    for key, gpu, instance, stamp, url in GPUS:
        gpu_hour.append({
            "id": key, "gpu": gpu, "year": 2020, "instance": instance,
            "provider": "AWS, US East (N. Virginia), Linux",
            "on_demand_usd": on_demand_usd(page_text(archived(stamp, url)), instance),
            "on_demand_source": f"AWS instance page as archived on {stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}",
            "on_demand_url": WAYBACK.format(stamp=stamp, flag="", url=url),
            "spot_usd": spot_usd(feed, instance),
            "spot_source": "AWS spot price feed as archived on 2020-07-07",
            "spot_url": WAYBACK.format(stamp=SPOT_FEED[0], flag="", url=SPOT_FEED[1]),
        })

    rates = json.loads(fetch(FX_API))["rates"]
    in_2020 = [day["CHF"] for when, day in rates.items() if when.startswith("2020")]

    release = page_text(archived(*DGX))
    usd = int(re.search(r"DGX A100 systems start at \$([0-9,]+)", release).group(1).replace(",", ""))

    return {
        "retrieved": date.today().isoformat(),
        "gpu_hour": gpu_hour,
        "electricity": [elcom(2020), elcom(2026)],
        "exchange": {"chf_per_usd": round(sum(in_2020) / len(in_2020), 4),
                     "source": f"ECB reference rates, mean of {len(in_2020)} trading days in 2020 "
                               "(Frankfurter API)",
                     "url": "https://frankfurter.dev/"},
        "hardware": {"system": "NVIDIA DGX A100, 8 x A100 GPUs", "usd": usd, "year": 2020,
                     "source": "NVIDIA press release of 14 May 2020: systems start at this price",
                     "url": WAYBACK.format(stamp=DGX[0], flag="", url=DGX[1])},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data/prices_2020.json"))
    args = parser.parse_args()
    data = build()
    args.out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    for row in data["gpu_hour"]:
        print(f"{row['gpu']}: on-demand {row['on_demand_usd']} USD, spot {row['spot_usd']} USD "
              "per GPU-hour")
    print(f"electricity: {[(e['year'], e['chf_per_kwh']) for e in data['electricity']]} CHF/kWh")
    print(f"exchange: {data['exchange']['chf_per_usd']} CHF per USD; "
          f"hardware: {data['hardware']['usd']} USD")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
