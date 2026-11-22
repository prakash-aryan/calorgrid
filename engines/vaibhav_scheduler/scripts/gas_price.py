"""The price of the gas the real house heats with, from its anonymised gas bills.

    python scripts/gas_price.py           # writes data/gas_price.json

Input: context/house_heat_details/Data_Gas_2021-2026_anonymisiert.csv, one row
per meter, month and charge. The heating meter is MESSPUNKT_004 (tens of MWh a
year); the other two meters use a few hundred kWh and do not heat the house.

    rows with unit KWH  the gas used and what it cost: energy, network, levies
    rows with unit KW   a fixed monthly capacity charge

The price per kWh is the variable part only: energy + network + levies over the
kWh used. The capacity charge is kept on its own: the house still pays it while
it keeps its gas connection, so heat from servers does not save it. The bills do
not say whether VAT is included.

The electricity files in the same folder (Data_Strom_*) hold the household's own
15-minute use and no prices, so they are not read here; server electricity is
priced at the ElCom tariff in data/fleet_machine.json.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BILLS = ROOT / "context" / "house_heat_details" / "Data_Gas_2021-2026_anonymisiert.csv"
OUT = ROOT / "data" / "gas_price.json"
HEATING_METER = "MESSPUNKT_004"

KWH = "Verbrauch Medium Einfachtarif (kWh/m3)"
VARIABLE = ("Betrag Energielieferung (CHF)", "Betrag Netznutzung (CHF)", "Betrag Abgaben (CHF)")
TOTAL = "Gesamtbetrag (CHF)"


def monthly(path: str | Path, meter: str = HEATING_METER) -> list[dict]:
    """Gas used, variable price and fixed charge of one meter, month by month."""
    months: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row["Messpunkt CH"] != meter:
                continue
            month = row["Zeitraum (KalJahr/Monat)"]
            m = months.setdefault(month, {"month": month, "kwh": 0.0, "variable_chf": 0.0,
                                          "fixed_chf": 0.0})
            if row["Ableseeinheit"] == "KWH":
                m["kwh"] += float(row[KWH])
                m["variable_chf"] += sum(float(row[col]) for col in VARIABLE)
            else:                                   # KW: the capacity charge
                m["fixed_chf"] += float(row[TOTAL])
    out = []
    for month in sorted(months):
        m = months[month]
        if m["kwh"] > 0:
            out.append({"month": f"{month[:4]}-{month[4:]}", "kwh": round(m["kwh"]),
                        "chf_per_kwh": round(m["variable_chf"] / m["kwh"], 4),
                        "fixed_chf": round(m["fixed_chf"], 2)})
    return out


def yearly(months: list[dict]) -> dict[str, dict]:
    """The kWh-weighted price and the mean monthly fixed charge of each year."""
    years: dict[str, list[dict]] = {}
    for m in months:
        years.setdefault(m["month"][:4], []).append(m)
    return {year: {
        "chf_per_kwh": round(sum(m["chf_per_kwh"] * m["kwh"] for m in rows)
                             / sum(m["kwh"] for m in rows), 4),
        "fixed_chf_per_month": round(sum(m["fixed_chf"] for m in rows) / len(rows), 2),
        "kwh": sum(m["kwh"] for m in rows),
        "months": len(rows),
    } for year, rows in years.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bills", default=BILLS, type=Path)
    parser.add_argument("--out", default=OUT, type=Path)
    args = parser.parse_args()
    months = monthly(args.bills)
    data = {
        "meter": HEATING_METER,
        "unit": "CHF per kWh of gas",
        "source": "the house's anonymised gas bills, heating meter "
                  f"({args.bills.name}): energy, network and levies over kWh used",
        "note": "The fixed capacity charge is not avoided while the house keeps its gas "
                "connection. The bills do not say whether VAT is included.",
        "years": yearly(months),
        "monthly": months,
    }
    args.out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    for year, y in data["years"].items():
        print(f"{year}: {y['chf_per_kwh']:.4f} CHF/kWh over {y['months']} months, "
              f"fixed {y['fixed_chf_per_month']:.2f} CHF/month")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
