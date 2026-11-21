# Residential heat profile cross-check

This check compares the case-calibrated Basel reference space-heating total with the hourly residential profiles supplied alongside the project. It is a sensitivity check; the reference scenario and its headline results remain unchanged.

## Results under the documented kWh-per-hour interpretation

The source header says `MWh`, but its companion analysis says the values fit one building when read as kWh per hour. The table follows that interpretation and scales the 8,640-hour (360-day) series to 8,760 hours. The scale label is unresolved, so use these as profile-shape and sensitivity evidence rather than validated annual forecasts.

| Profile | Source total (kWh / 360-day series) | 365-day equivalent (kWh) | Peak hourly demand (kW) | vs. 27,000 kWh case space heat |
|---|---:|---:|---:|---:|
| SFH_OLD (MWh) | 16,221 | 16,446 | 7.37 | 60.9% |
| SFH_NEW (MWh) | 7,894 | 8,004 | 4.37 | 29.6% |
| MFH_OLD (MWh) | 100,172 | 101,563 | 38.10 | 376.2% |
| MFH_NEW (MWh) | 32,827 | 33,283 | 16.50 | 123.3% |

## What this changes

Under the kWh interpretation, the `SFH_OLD` profile annualizes to about 16,446 kWh, around 61% of the case's 27,000 kWh space-heating assumption. That gap is large enough to affect server sizing, heat coverage, and break-even economics. It supports showing a profile sensitivity alongside the reference result, rather than presenting one house demand curve as universal.

The profile values may instead be MWh as the CSV header states. That interpretation is 1,000 times larger and is not plausible for one house at the reported hourly peaks. The project should confirm the source unit before using this data to replace or recalibrate the reference house.

## Provenance and limits

- Input: [`data/raw/heat_demand/residential_heating_hour_profile_HSR.csv`](../data/raw/heat_demand/residential_heating_hour_profile_HSR.csv), provided with the `digitalheat-backend` comparison project.
- Source analysis: [heat_model_server_vs_house_req.md](../friend-digitalheat-backend/context/heat_model_server_vs_house_req.md).
- The source series contains 8,640 hourly values (360 days); the 365-day values above are a proportional normalization, not a weather-year simulation.
- These profiles are not Swiss-wide representative samples and should not be mixed into the existing fleet results without confirming units, building definitions, and hot-water treatment.
- Rebuild this report with `python scripts/compare_heat_profiles.py`.
