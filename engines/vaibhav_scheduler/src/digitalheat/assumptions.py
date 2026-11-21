"""AssumptionRegistry: every constant the model uses, with its unit and source.

Nothing else in the package hard-codes a physical or economic number. A
Scenario reads its values from a registry, so one table lists everything a
result depends on, and an override leaves a trace of what was changed and why.

Print the table with:  python -m digitalheat
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Iterator

BRIEF = "DigitalHeat case brief (sprintd Basel GreenTech)"
TEAM = "Team assumption"


@dataclass(frozen=True)
class Assumption:
    key: str
    value: float | str
    unit: str
    source: str
    note: str = ""


class AssumptionRegistry:
    def __init__(self, items: Iterable[Assumption] = ()) -> None:
        self._items: dict[str, Assumption] = {}
        for item in items:
            self.add(item)

    def add(self, item: Assumption) -> None:
        if item.key in self._items:
            raise KeyError(f"assumption {item.key!r} already defined; use override()")
        self._items[item.key] = item

    def override(self, key: str, value: float | str, source: str, note: str = "") -> None:
        """Change a value and record where the new one comes from."""
        old = self.get(key)
        trail = f"overrides {old.value} {old.unit} ({old.source})"
        self._items[key] = replace(
            old, value=value, source=source, note=f"{note}; {trail}" if note else trail
        )

    def get(self, key: str) -> Assumption:
        try:
            return self._items[key]
        except KeyError:
            raise KeyError(f"unknown assumption {key!r}") from None

    def value(self, key: str) -> float:
        value = self.get(key).value
        if isinstance(value, str):
            raise TypeError(f"assumption {key!r} is not numeric: {value!r}")
        return float(value)

    def __iter__(self) -> Iterator[Assumption]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def to_markdown(self) -> str:
        lines = ["| Key | Value | Unit | Source | Note |", "|---|---|---|---|---|"]
        for a in self:
            lines.append(f"| `{a.key}` | {a.value} | {a.unit} | {a.source} | {a.note} |")
        return "\n".join(lines)


def reference_assumptions() -> AssumptionRegistry:
    """Starting assumptions for the reference house.

    Values marked BRIEF are given by DigitalHeat. Values marked TEAM are our
    own choices and should be challenged.
    """
    return AssumptionRegistry(
        [
            # --- server ---
            Assumption("server_power_kw", 1.0, "kW", BRIEF, "electricity per rack server"),
            Assumption("heat_recovery_fraction", 0.8, "-", BRIEF,
                       "share of server electricity recovered into water heating"),
            Assumption("server_idle_power_kw", 0.0, "kW", TEAM,
                       "servers are switched off when no job is assigned"),
            Assumption("fixed_cost_chf_per_server_year", 0.0, "CHF/server/year", TEAM,
                       "hardware, installation and maintenance are not priced yet"),
            # --- reference house heat demand ---
            Assumption("design_heat_load_kw", 10.0, "kW", BRIEF, "house heat load on a cold day"),
            Assumption("indoor_setpoint_c", 20.0, "degC", BRIEF,
                       "fixed-setpoint formula: demand = load x (setpoint - T) / design_delta; "
                       "also the indoor temperature the building model starts at"),
            Assumption("indoor_min_c", 19.0, "degC", TEAM,
                       "lower edge of the comfort band the indoor temperature may drift in"),
            Assumption("indoor_max_c", 22.0, "degC", TEAM,
                       "upper edge of the comfort band"),
            Assumption("building_thermal_capacity_kwh_per_k", 20.0, "kWh/K", TEAM,
                       "heat stored per degree of indoor temperature; single-family houses "
                       "span roughly 10 (timber) to 40 (masonry)"),
            Assumption("design_delta_k", 25.6, "K", BRIEF, "temperature difference at full load"),
            Assumption("heating_day_threshold_c", 12.0, "degC", BRIEF,
                       "heating only on days whose mean hourly temperature is below this"),
            Assumption("hot_water_kwh_per_day", 12.0, "kWh/day", BRIEF, "year-round"),
            Assumption("hot_water_hourly_shape", "flat", "-", TEAM,
                       "fixed-setpoint profile: hot water demand is spread evenly over 24 hours"),
            Assumption("space_heating_max_output_kw", 10.0, "kW", TEAM,
                       "most heat the heating system can deliver to the rooms; "
                       "sized to the brief's design load"),
            # --- hot water tank (heat range model) ---
            Assumption("hot_water_tank_litres", 300.0, "L", TEAM,
                       "domestic hot water tank of one household"),
            Assumption("hot_water_tank_temp_c", 55.0, "degC", TEAM,
                       "temperature the tank is heated to"),
            Assumption("mains_water_temp_c", 10.0, "degC", TEAM,
                       "cold water that replaces every draw"),
            Assumption("hot_water_reserve_fraction", 0.25, "-", TEAM,
                       "share of the tank that must stay hot so taps never run cold"),
            Assumption("hot_water_draw_shape", "morning_evening", "-", TEAM,
                       "hourly split of the daily draw: illustrative morning and evening "
                       "peaks, to be replaced by a measured tapping profile"),
            # --- storage ---
            Assumption("tank_capacity_kwh", 35.0, "kWh", BRIEF, "one tank, usable capacity"),
            Assumption("tank_loss_fraction_per_h", 0.0, "1/h", BRIEF, "no losses"),
            Assumption("tank_initial_soc_kwh", 0.0, "kWh", TEAM, "tank starts the year empty"),
            # --- economics ---
            Assumption("electricity_price_chf_per_kwh", 0.3325, "CHF/kWh",
                       "ElCom 2026, IWB Basel, category H4, total price (33.25 Rp./kWh)",
                       "confirm on strompreis.elcom.admin.ch, including VAT treatment"),
            Assumption("heat_pump_cop", 2.5, "-", BRIEF, "heat value = electricity price / COP"),
            Assumption("billing_basis", "running server-hour", "-", TEAM,
                       "compute income accrues for every hour a server runs a job"),
            # --- model ---
            Assumption("time_step_h", 1.0, "h", TEAM, "matches the hourly weather data"),
        ]
    )

