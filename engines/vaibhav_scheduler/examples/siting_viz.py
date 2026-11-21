"""Write the interactive siting page: one house, 100 weather profiles, a second site.

    python scripts/fetch_locations.py                    # once: data/ch_weather_2025.json
    python examples/siting_viz.py --open                 # both sites in Switzerland: out/siting.html

    python scripts/fetch_locations.py --country NZ       # once: data/nz_weather_2025.json
    python examples/siting_viz.py --second data/nz_weather_2025.json --open
                                                         # site 2 in New Zealand: out/siting_nz.html

    python examples/siting_viz.py --rank                 # also print the second-site ranking

The page recomputes everything in the browser when a control moves. The same
model lives in `digitalheat.siting`; this script runs it for the default
settings and embeds the results, and the page shows whether its own numbers
agree with them.
"""

from __future__ import annotations

import argparse
import base64
import json
import struct
import webbrowser
from dataclasses import asdict
from pathlib import Path

from digitalheat import reference_assumptions
from digitalheat.siting import (SitingParams, heat_requirement, rank_second_site, run_sites,
                                window_hours)

SITE1, COMPUTE_KW = "Basel", 6.0
HOURS = 8760                     # the shared clock: one year of Swiss local hours


def load(path: Path) -> dict:
    """One weather file as a region: its towns, outline and aligned heat requirement."""
    data = json.loads(path.read_text(encoding="utf-8"))
    offset = data.get("axis_offset", 0)
    outlines = data.get("outlines") or ([data["outline"]] if data.get("outline") else [])
    for loc in data["locations"]:
        # 24-hour blocks of temps_c are the town's own calendar days; the slice
        # puts the result on the shared clock
        loc["need"] = heat_requirement(loc["temps_c"])[offset:offset + HOURS]
        if len(loc["need"]) != HOURS:
            raise ValueError(f"{loc['name']}: weather does not cover the shared year")
    lats = [loc["lat"] for loc in data["locations"]]
    return {
        "code": data.get("country", "CH"), "name": data.get("country_name", "Switzerland"),
        "year": data["year"], "source": data["source"], "selection": data["selection"],
        "day_starts": data.get("day_starts", ""), "offset": offset, "outlines": outlines,
        "south": sum(lats) / len(lats) < 0, "locations": data["locations"],
    }


def correlation(xs: list[float], ys: list[float]) -> float:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx, syy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weather", type=Path, default=Path("data/ch_weather_2025.json"),
                        help="towns that site 1 is chosen from")
    parser.add_argument("--second", type=Path, default=None,
                        help="towns that site 2 is chosen from (default: the same as site 1)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    parser.add_argument("--rank", action="store_true", help="print the second-site ranking")
    args = parser.parse_args()

    home = load(args.weather)
    away = load(args.second) if args.second else home
    cross = away is not home
    if cross and (away["code"] == home["code"] or away["year"] != home["year"]):
        raise SystemExit("--second must be another country for the same year")
    regions = [home, away] if cross else [home]
    out = args.out or Path("out") / ("siting.html" if not cross else f"siting_{away['code'].lower()}.html")
    # Two sites in one country are compared over six months. Two hemispheres hand
    # the compute back and forth once a year, so that page opens on the full year.
    defaults = {"site1": SITE1, "compute_kw": COMPUTE_KW, "window_start": 1,
                "window_months": 12 if cross else 6}

    params = SitingParams()
    need = {loc["name"]: loc["need"] for region in regions for loc in region["locations"]}
    if len(need) != sum(len(region["locations"]) for region in regions):
        raise SystemExit("two towns share a name")
    first, compute_kw = defaults["site1"], defaults["compute_kw"]
    hours = window_hours(defaults["window_start"], defaults["window_months"])
    year = run_sites([need[first]], compute_kw, params)
    alone = run_sites([need[first]], compute_kw, params, hours)
    candidates = {first: need[first], **{loc["name"]: loc["need"] for loc in away["locations"]}}
    ranking = rank_second_site(candidates, first, compute_kw, params, hours)
    best = ranking[0]

    # The scatter plot uses whichever of elevation and latitude explains the gain better.
    meta = {loc["name"]: loc for loc in away["locations"]}
    gains = [score.gain_chf for score in ranking]
    by_elevation = correlation([meta[score.name]["elevation_m"] for score in ranking], gains)
    by_latitude = correlation([abs(meta[score.name]["lat"]) for score in ranking], gains)
    scatter = "lat" if abs(by_latitude) > abs(by_elevation) else "elev"

    if args.rank:
        window = "full year" if defaults["window_months"] == 12 else \
            f"{defaults['window_months']} months from month {defaults['window_start']}"
        print(f"{first}, {compute_kw:g} kW of compute, recovery {params.recovery:.0%}, "
              f"tank {params.tank_kwh:g} kWh per house; site 2 in {away['name']}")
        print(f"  full year, one house: {year.covered_share:.1%} of heat covered, "
              f"{year.savings_chf:,.0f} CHF saved, {year.bill_recovered:.1%} of the bill recovered")
        print(f"  {window}, {first} alone: {alone.savings_chf:,.0f} CHF saved, "
              f"{alone.dumped_kwh:,.0f} kWh dumped")
        print(f"  gain against elevation r = {by_elevation:+.2f}, against latitude r = {by_latitude:+.2f}\n")
        print(f"  {'rank':>4}  {'second site':<22}{'elev m':>7}{'lat':>7}{'saved CHF':>11}{'gain CHF':>10}"
              f"{'bill back':>10}{'dumped kWh':>12}")
        shown = list(enumerate(ranking, 1))
        for rank, score in shown[:10] + shown[-3:]:
            print(f"  {rank:>4}  {score.name:<22}{meta[score.name]['elevation_m']:>7}"
                  f"{meta[score.name]['lat']:>7.1f}{score.savings_chf:>11,.0f}{score.gain_chf:>10,.0f}"
                  f"{score.bill_recovered:>10.1%}{score.dumped_kwh:>12,.0f}")

    a = reference_assumptions()
    payload = {
        "year": home["year"], "hours": HOURS,
        "pools": {"site1": home["code"], "site2": away["code"]},
        "regions": {region["code"]: {key: region[key] for key in (
            "name", "source", "selection", "day_starts", "outlines", "south")} for region in regions},
        "locations": [{
            "name": loc["name"], "region": region["code"], "off": region["offset"],
            "canton": loc.get("canton", "").replace("Kanton ", "").replace(" Region", ""),
            "lat": loc["lat"], "lon": loc["lon"], "elev": loc["elevation_m"],
            # temperatures in tenths of a degree, 16-bit little-endian, base64
            "t": base64.b64encode(struct.pack(f"<{len(loc['temps_c'])}h",
                                              *(round(t * 10) for t in loc["temps_c"]))).decode(),
        } for region in regions for loc in region["locations"]],
        "house": {key: a.value(key) for key in (
            "design_heat_load_kw", "indoor_setpoint_c", "design_delta_k",
            "heating_day_threshold_c", "hot_water_kwh_per_day")},
        "params": asdict(params),
        "defaults": defaults,
        "scatter": scatter,
        "check": {"year_savings_chf": year.savings_chf, "year_covered_share": year.covered_share,
                  "alone_savings_chf": alone.savings_chf, "best_name": best.name,
                  "best_savings_chf": best.savings_chf},
    }
    template = (Path(__file__).parent / "siting_template.html").read_text(encoding="utf-8")
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.replace("/*SITING_JSON*/null", blob), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(payload['locations'])} locations)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
