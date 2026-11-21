"""Download hourly Basel weather for 2025 from the Open-Meteo historical archive.

    python scripts/fetch_weather.py                 # writes data/open_meteo_basel_2025.csv

No API key is needed. The coordinates are our choice for 'Basel'; if the case
organisers hand out a ready-made profile CSV, use that for reported results.
"""

from __future__ import annotations

import argparse
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://archive-api.open-meteo.com/v1/archive"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--latitude", type=float, default=47.5584)
    parser.add_argument("--longitude", type=float, default=7.5733)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    query = urllib.parse.urlencode({
        "latitude": args.latitude,
        "longitude": args.longitude,
        "start_date": f"{args.year}-01-01",
        "end_date": f"{args.year}-12-31",
        "hourly": "temperature_2m,shortwave_radiation",
        "timezone": "Europe/Zurich",
        "format": "csv",
    })
    out = args.out or Path("data") / f"open_meteo_basel_{args.year}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{API}?{query}", timeout=60) as response:
        out.write_bytes(response.read())
    rows = sum(1 for _ in out.open(encoding="utf-8"))
    print(f"wrote {out} ({rows} lines)")


if __name__ == "__main__":
    main()
