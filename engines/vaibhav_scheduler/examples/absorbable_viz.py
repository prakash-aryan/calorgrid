"""Show, hour by hour, how much heat the reference house needs and can absorb.

    python examples/absorbable_viz.py                    # writes out/absorbable.html
    python examples/absorbable_viz.py --date 2025-12-31  # another day of the Basel year
    python examples/absorbable_viz.py --open             # ...and opens it in the browser

One day of the heat range model (HouseHeatModel): space heating from the
indoor temperature inside the comfort band, hot water from the tank as the
household draws from it. The day starts with the house at the bottom of the
band and the hot water tank full. The page replays the same day for every
number of servers running around the clock, so the effect of supplied heat on
the indoor temperature and the tank can be compared. To choose the servers
hour by hour, use planner_viz.py.

Outdoor temperatures are the measured Basel hours of the chosen date. The
band, thermal capacity, tank and draw pattern are team assumptions listed in
the registry.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import date, datetime
from pathlib import Path

from digitalheat import Scenario

ROOT = Path(__file__).resolve().parent.parent
WEATHER = ROOT / "data" / "ch_weather_2025.json"
PLACE = "Basel"
DEFAULT_DATE = "2025-01-14"      # a cold winter day with a clear day-night swing
START_INDOOR_C = 19.0            # bottom of the comfort band
MAX_SERVERS = 16


def r(value: float) -> float:
    return round(value, 3)


def day_temperatures(day: date) -> tuple[list[float], str]:
    data = json.loads(WEATHER.read_text(encoding="utf-8"))
    place = next(loc for loc in data["locations"] if loc["name"] == PLACE)
    first = datetime.fromisoformat(place["first_hour"])
    start = int((datetime(day.year, day.month, day.day) - first).total_seconds() // 3600)
    if start < 0 or start + 24 > len(place["temps_c"]):
        raise SystemExit(f"{day} is outside the weather data ({first.year})")
    return place["temps_c"][start:start + 24], data["source"]


def run_day(scenario: Scenario, temps: list[float], heating_day: bool, servers: int) -> dict:
    a = scenario.assumptions
    model = scenario.heat_model(indoor_c=START_INDOOR_C, tank_fill_fraction=1.0)
    supplied = servers * a.value("server_power_kw") * a.value("heat_recovery_fraction") \
        * a.value("time_step_h")
    steps = []
    for hour, outdoor in enumerate(temps):
        d = model.deliver(supplied, outdoor, hour, heating_day)
        steps.append({
            "smin": r(d.range.space_min_kwh), "smax": r(d.range.space_max_kwh),
            "wmin": r(d.range.water_min_kwh), "wmax": r(d.range.water_max_kwh),
            "ts": r(d.to_space_kwh), "tw": r(d.to_water_kwh),
            "bs": r(d.backup_space_kwh), "bw": r(d.backup_water_kwh),
            "dump": r(d.dumped_kwh), "tin": r(d.indoor_c), "lvl": r(d.tank_level_kwh),
        })
    return {"servers": servers, "supplied": r(supplied), "steps": steps}


def build(day: date) -> dict:
    scenario = Scenario("absorbable heat", 0, [], [])
    a = scenario.assumptions
    temps, source = day_temperatures(day)
    mean = sum(temps) / len(temps)
    heating_day = mean < a.value("heating_day_threshold_c")
    start = scenario.heat_model(indoor_c=START_INDOOR_C, tank_fill_fraction=1.0)
    return {
        "meta": {"place": PLACE, "date": day.isoformat(), "label": f"{day.day} {day:%B %Y}",
                 "source": source, "mean_c": r(mean), "min_c": min(temps), "max_c": max(temps),
                 "heating_day": heating_day},
        "params": {
            "loss_kw_per_k": r(start.building.loss_kw_per_k),
            "capacity_kwh_per_k": start.building.capacity_kwh_per_k,
            "min_c": start.building.min_temp_c, "max_c": start.building.max_temp_c,
            "start_c": START_INDOOR_C,
            "max_space_kw": start.max_space_heat_kw,
            "tank_l": start.tank.volume_l, "tank_kwh": r(start.tank.capacity_kwh),
            "reserve_kwh": r(start.tank.reserve_kwh),
            "tank_hot_c": a.value("hot_water_tank_temp_c"),
            "mains_c": a.value("mains_water_temp_c"),
            "daily_water_kwh": a.value("hot_water_kwh_per_day"),
            "server_heat_kwh": r(a.value("server_power_kw") * a.value("heat_recovery_fraction")),
        },
        "hours": [{"t_out": temp, "draw": r(start.draw_kwh(hour))}
                  for hour, temp in enumerate(temps)],
        "runs": [run_day(scenario, temps, heating_day, n) for n in range(MAX_SERVERS + 1)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", default=DEFAULT_DATE, help="day of the Basel year, YYYY-MM-DD")
    parser.add_argument("--out", type=Path, default=Path("out/absorbable.html"))
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()

    data = build(date.fromisoformat(args.date))
    template = (Path(__file__).parent / "absorbable_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(template.replace("/*DAY_JSON*/null", payload), encoding="utf-8")
    base = data["runs"][0]["steps"]
    print(f"wrote {args.out}: {PLACE} {args.date}, mean {data['meta']['mean_c']:.1f} degC; "
          f"with no servers the house can absorb "
          f"{sum(s['smax'] + s['wmax'] for s in base):.0f} kWh over the day "
          f"and must get {sum(s['smin'] + s['wmin'] for s in base):.0f} kWh")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
