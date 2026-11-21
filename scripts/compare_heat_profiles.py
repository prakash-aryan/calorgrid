"""Compare the supplied HSR hourly profiles with the DigitalHeat reference demand.

This is a sensitivity check, not a replacement for the case-calibrated Basel model.
The source CSV labels its values as MWh, while its accompanying analysis interprets
them as kWh per hourly step. Both scales are shown so the unresolved unit question
stays visible.

Run with: python scripts/compare_heat_profiles.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/heat_demand/residential_heating_hour_profile_HSR.csv"
OUTPUT = ROOT / "docs/HEAT_PROFILE_CROSSCHECK.md"
JSON_OUTPUT = ROOT / "data/derived/heat_profile_crosscheck.json"
REFERENCE_SPACE_HEAT_KWH = 27_000.0
SOURCE_HOURS = 8_640  # 360 days, as documented with the supplied profile
YEAR_HOURS = 8_760


def main() -> None:
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = [name for name in reader.fieldnames or () if name != "Hour"]
        values = {name: [] for name in columns}
        for row in reader:
            for name in columns:
                value = float(row[name])
                if value < 0:
                    raise ValueError(f"negative heat demand in {name}: {value}")
                values[name].append(value)

    if not columns or any(len(series) != SOURCE_HOURS for series in values.values()):
        raise ValueError(f"expected {SOURCE_HOURS} hourly rows for each building profile")

    annualizer = YEAR_HOURS / SOURCE_HOURS
    profiles = []
    for name in columns:
        series = values[name]
        total = sum(series)
        normalized = total * annualizer
        profiles.append({
            "name": name.replace(" (MWh)", ""),
            "source_label": name,
            "source_total_kwh_interpretation": round(total, 2),
            "annualized_kwh": round(normalized, 2),
            "peak_hourly_kw": round(max(series), 2),
            "share_of_case_reference": normalized / REFERENCE_SPACE_HEAT_KWH,
        })
    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(json.dumps({
        "reference_space_heating_kwh": REFERENCE_SPACE_HEAT_KWH,
        "source_hours": SOURCE_HOURS,
        "annual_hours": YEAR_HOURS,
        "interpretation": "CSV values treated as kWh per hour; source header says MWh and needs confirmation.",
        "profiles": profiles,
    }, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Residential heat profile cross-check",
        "",
        "This check compares the case-calibrated Basel reference space-heating total with the hourly residential profiles supplied alongside the project. It is a sensitivity check; the reference scenario and its headline results remain unchanged.",
        "",
        "## Results under the documented kWh-per-hour interpretation",
        "",
        "The source header says `MWh`, but its companion analysis says the values fit one building when read as kWh per hour. The table follows that interpretation and scales the 8,640-hour (360-day) series to 8,760 hours. The scale label is unresolved, so use these as profile-shape and sensitivity evidence rather than validated annual forecasts.",
        "",
        "| Profile | Source total (kWh / 360-day series) | 365-day equivalent (kWh) | Peak hourly demand (kW) | vs. 27,000 kWh case space heat |",
        "|---|---:|---:|---:|---:|",
    ]
    for profile in profiles:
        lines.append(
            f"| {profile['source_label']} | {profile['source_total_kwh_interpretation']:,.0f} | {profile['annualized_kwh']:,.0f} | {profile['peak_hourly_kw']:,.2f} | {profile['share_of_case_reference']:.1%} |"
        )

    old = next((name for name in columns if "SFH_OLD" in name), None)
    if old:
        normalized = next(p["annualized_kwh"] for p in profiles if p["source_label"] == old)
        lines += [
            "",
            "## What this changes",
            "",
            f"Under the kWh interpretation, the `SFH_OLD` profile annualizes to about {normalized:,.0f} kWh, around {normalized / REFERENCE_SPACE_HEAT_KWH:.0%} of the case's 27,000 kWh space-heating assumption. That gap is large enough to affect server sizing, heat coverage, and break-even economics. It supports showing a profile sensitivity alongside the reference result, rather than presenting one house demand curve as universal.",
            "",
            "The profile values may instead be MWh as the CSV header states. That interpretation is 1,000 times larger and is not plausible for one house at the reported hourly peaks. The project should confirm the source unit before using this data to replace or recalibrate the reference house.",
        ]
    lines += [
        "",
        "## Provenance and limits",
        "",
        f"- Input: [`{SOURCE.relative_to(ROOT)}`](../{SOURCE.relative_to(ROOT)}), provided with the `digitalheat-backend` comparison project.",
        "- Source analysis: [heat_model_server_vs_house_req.md](../friend-digitalheat-backend/context/heat_model_server_vs_house_req.md).",
        "- The source series contains 8,640 hourly values (360 days); the 365-day values above are a proportional normalization, not a weather-year simulation.",
        "- These profiles are not Swiss-wide representative samples and should not be mixed into the existing fleet results without confirming units, building definitions, and hot-water treatment.",
        "- Rebuild this report with `python scripts/compare_heat_profiles.py`.",
        "",
    ]
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
