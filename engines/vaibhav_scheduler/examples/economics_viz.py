"""Put prices on the servers, their jobs and the heat they give a house.

    python examples/economics_viz.py                    # writes out/economics.html
    python examples/economics_viz.py --date 2025-12-31  # another day of the Basel year
    python examples/economics_viz.py --open             # ...and opens it in the browser

Built on workload_viz.py: the same servers, jobs and house. On top of it the
page shows, for the day, each cost on its own (hardware, electricity,
maintenance), the heat savings, what a client pays at the on-demand and the
spot price, and the GPU-hour price that covers the costs with and without the
heat savings. The electricity tariff, both GPU-hour prices, the hardware
price, its life and the maintenance rate can be changed in the page.

    prices   data/fleet_machine.json, written by scripts/fetch_prices.py: the fleet machine,
             a server with eight H100 GPUs. Its purchase price, the market price of its
             GPU-hours and the tariff are this year's, each with its source, and the page
             opens with that same server, so cost and price describe one machine
    costs    ServerCosts in economics.py

Job lengths on this page are as recorded in the trace, not shortened for the
fleet's GPU as on the scheduler page and the dashboard.

The page recomputes everything in the browser and checks itself against a
configuration computed by the Python model.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import date
from pathlib import Path

import planner_viz
import workload_viz
from digitalheat import Scenario, ServerCosts

PRICES_FILE = workload_viz.DATA / "fleet_machine.json"
DEFAULT_MARKET = "h100-specialist"   # where the fleet's GPU-hours compete
HARDWARE_LIFE_YEARS = 10             # set by the team; can be changed in the page
MAINTENANCE_PER_YEAR = 0.05          # share of the hardware price; team assumption, no public source


def defaults(prices: dict) -> dict:
    a = Scenario("economics", 0, [], []).assumptions
    fx, year = prices["exchange"]["chf_per_usd"], prices["machine"]["year"]
    tariff = next(e for e in prices["electricity"] if e["year"] == year)
    return {
        "tariff": tariff["chf_per_kwh"], "gpu": DEFAULT_MARKET, "year": year,
        "hardware_chf": round(prices["hardware"]["usd"] * fx),
        "hardware_low_chf": round(prices["hardware"]["low_usd"] * fx),
        "hardware_high_chf": round(prices["hardware"]["high_usd"] * fx),
        "life_years": HARDWARE_LIFE_YEARS, "maintenance_pct": MAINTENANCE_PER_YEAR * 100,
        "cop": a.value("heat_pump_cop"), "cop_source": a.get("heat_pump_cop").source,
    }


def money(config: list[dict], day: date, d: dict) -> dict:
    """The day's costs and heat saving for one configuration, from the Python model."""
    run, empty = workload_viz.simulate(config, day), workload_viz.simulate([], day)

    def backup(result: dict) -> float:
        return sum(step["bs"] + step["bw"] for step in result["steps"])

    avoided = backup(empty) - backup(run)
    balance = ServerCosts(
        electricity_chf_per_kwh=d["tariff"], heat_pump_cop=d["cop"],
        hardware_chf_per_server=d["hardware_chf"], hardware_life_years=d["life_years"],
        maintenance_fraction_per_year=d["maintenance_pct"] / 100,
    ).balance(len(config), run["electricity_kwh"], run["gpu_hours"], avoided)
    return {
        "gpuh": run["gpu_hours"], "kwh": run["electricity_kwh"], "avoided": avoided,
        "hw": balance.hardware_chf, "el": balance.electricity_chf, "mt": balance.maintenance_chf,
        "heat": balance.heat_saving_chf,
        "be0": balance.break_even_chf_per_gpu_hour(with_heat_saving=False),
        "be1": balance.break_even_chf_per_gpu_hour(),
    }


def build(day: date) -> dict:
    prices = json.loads(PRICES_FILE.read_text(encoding="utf-8"))
    d = defaults(prices)
    data = workload_viz.build(day)
    # open with the fleet machine itself, so the costs and the prices are for one server
    server = next(p for p in data["profiles"] if p["id"] == prices["machine"]["power_profile"])
    opening = [{"p": server["id"], "jobs": data["default_sets"][server["default"]]["jobs"]}]
    return data | {"default": opening, "prices": prices, "money_defaults": d,
                   "check_money": money(workload_viz.CHECK_CONFIG, day, d)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", default=planner_viz.DEFAULT_DATE,
                        help="day of the Basel year, YYYY-MM-DD")
    parser.add_argument("--out", type=Path, default=Path("out/economics.html"))
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    args = parser.parse_args()

    day = date.fromisoformat(args.date)
    data = build(day)
    template = (Path(__file__).parent / "economics_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(template.replace("/*WORKLOAD_JSON*/null", payload), encoding="utf-8")
    m = money(data["default"], day, data["money_defaults"])
    print(f"wrote {args.out}: the opening configuration costs "
          f"{m['hw'] + m['el'] + m['mt']:.0f} CHF for the day and saves {m['heat']:.0f} CHF of "
          f"heating; a GPU-hour must earn {m['be0']:.2f} CHF, or {m['be1']:.2f} CHF with the "
          f"heat savings")
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__ == "__main__":
    main()
