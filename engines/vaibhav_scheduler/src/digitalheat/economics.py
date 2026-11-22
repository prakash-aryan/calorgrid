"""Economics and reporting: prices, the hourly ledger and the yearly balance.

Entities
    Tariff         electricity price, heat value and fixed costs
    HourRecord     one house, one step: heat flows, electricity, income
    Ledger         every HourRecord of a run
    YearlyBalance  the figures the brief asks for, per house
    ServerCosts    prices behind the cost of running servers and the value of their heat
    Balance        those costs for one period, the heat saving and the break-even price
    TierPricing    GPU-hour prices by tier, from a base price and a factor per tier
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping

from .heat import HeatFlows


@dataclass(frozen=True)
class Tariff:
    electricity_chf_per_kwh: float
    heat_pump_cop: float                          # heat value = electricity price / COP
    fixed_cost_chf_per_server_year: float = 0.0   # hardware, installation, maintenance
    source: str = ""

    @property
    def heat_value_chf_per_kwh(self) -> float:
        """What a kWh of useful server heat is worth: the heat-pump electricity it replaces."""
        return self.electricity_chf_per_kwh / self.heat_pump_cop


@dataclass(frozen=True)
class HourRecord:
    t: int
    house_id: str
    flows: HeatFlows
    electricity_kwh: float        # drawn by the house's servers
    running_server_hours: float
    servers_running: int
    income_chf: float             # computing income earned in this step


@dataclass(frozen=True)
class YearlyBalance:
    house_id: str
    steps: int
    servers: int
    heat_demand_kwh: float
    heat_covered_kwh: float            # demand met by server heat (direct or via tank)
    covered_share: float               # heat_covered / heat_demand
    server_heat_kwh: float             # recoverable heat the servers produced
    heat_dumped_kwh: float
    backup_heat_kwh: float
    running_server_hours: float
    utilisation: float                 # running hours / (servers x hours in the run)
    missed_deadline_pct: float         # fleet-wide: tasks are not owned by a house
    electricity_kwh: float
    electricity_cost_chf: float
    heat_value_chf: float
    computing_income_chf: float
    fixed_cost_chf: float
    net_chf: float                     # income + heat value - electricity - fixed cost
    break_even_chf_per_server_hour: float | None   # None if no server ever ran

    @property
    def pays_off(self) -> bool:
        return self.net_chf >= 0

    def summary(self) -> str:
        break_even = ("n/a" if self.break_even_chf_per_server_hour is None
                      else f"{self.break_even_chf_per_server_hour:.3f} CHF")
        verdict = "pays off" if self.pays_off else "does NOT pay off"
        return "\n".join([
            f"House {self.house_id}: {self.servers} server(s), {self.steps} hours",
            f"  Heat demand            {self.heat_demand_kwh:10.0f} kWh",
            f"  Covered by servers     {self.heat_covered_kwh:10.0f} kWh ({self.covered_share:.1%})",
            f"  Heat dumped            {self.heat_dumped_kwh:10.0f} kWh",
            f"  Utilisation            {self.utilisation:10.1%}",
            f"  Missed deadlines       {self.missed_deadline_pct:10.1f} %",
            f"  Electricity cost       {self.electricity_cost_chf:10.0f} CHF",
            f"  Heat value             {self.heat_value_chf:10.0f} CHF",
            f"  Computing income       {self.computing_income_chf:10.0f} CHF",
            f"  Net                    {self.net_chf:10.0f} CHF -> {verdict}",
            f"  Break-even price       {break_even} per server-hour",
        ])


class Ledger:
    def __init__(self) -> None:
        self.records: list[HourRecord] = []

    def record(self, record: HourRecord) -> None:
        self.records.append(record)

    def for_house(self, house_id: str) -> list[HourRecord]:
        return [r for r in self.records if r.house_id == house_id]

    def window(self, house_id: str, first_t: int, steps: int) -> list[HourRecord]:
        """Records of one stretch, e.g. a January day for the demo."""
        return [r for r in self.for_house(house_id) if first_t <= r.t < first_t + steps]

    def balance(self, house_id: str, servers: int, tariff: Tariff, dt_h: float = 1.0,
                missed_deadline_pct: float = 0.0) -> YearlyBalance:
        records = self.for_house(house_id)
        steps = len(records)
        hours = steps * dt_h
        demand = sum(r.flows.demand_kwh for r in records)
        covered = sum(r.flows.covered_kwh for r in records)
        running = sum(r.running_server_hours for r in records)
        electricity = sum(r.electricity_kwh for r in records)
        income = sum(r.income_chf for r in records)
        cost = electricity * tariff.electricity_chf_per_kwh
        heat_value = covered * tariff.heat_value_chf_per_kwh
        fixed = tariff.fixed_cost_chf_per_server_year * servers * hours / 8760
        return YearlyBalance(
            house_id=house_id,
            steps=steps,
            servers=servers,
            heat_demand_kwh=demand,
            heat_covered_kwh=covered,
            covered_share=covered / demand if demand else 0.0,
            server_heat_kwh=sum(r.flows.server_heat_kwh for r in records),
            heat_dumped_kwh=sum(r.flows.dumped_kwh for r in records),
            backup_heat_kwh=sum(r.flows.backup_kwh for r in records),
            running_server_hours=running,
            utilisation=running / (servers * hours) if servers and hours else 0.0,
            missed_deadline_pct=missed_deadline_pct,
            electricity_kwh=electricity,
            electricity_cost_chf=cost,
            heat_value_chf=heat_value,
            computing_income_chf=income,
            fixed_cost_chf=fixed,
            net_chf=income + heat_value - cost - fixed,
            break_even_chf_per_server_hour=(cost + fixed - heat_value) / running if running else None,
        )

    def to_csv(self, path: str | Path) -> None:
        """Hourly records as CSV, so every yearly figure can be recomputed by hand."""
        flow_cols = [f.name for f in fields(HeatFlows)]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["t", "house_id", *flow_cols, "electricity_kwh",
                             "running_server_hours", "servers_running", "income_chf"])
            for r in self.records:
                writer.writerow([r.t, r.house_id, *(getattr(r.flows, c) for c in flow_cols),
                                 r.electricity_kwh, r.running_server_hours,
                                 r.servers_running, r.income_chf])


# ------------------------------------------------------------- ServerCosts, Balance

DAYS_PER_YEAR = 365
BACKUPS = ("heat_pump", "gas")      # how the house heats without servers


@dataclass(frozen=True)
class ServerCosts:
    """Prices behind the cost of running servers and the value of the heat they give.

    `backup` is how the house makes the heat the servers do not: a heat pump
    (electricity price / COP per kWh of heat) or a gas boiler (gas price /
    boiler efficiency). Heat from the servers saves what the backup would cost.
    """

    electricity_chf_per_kwh: float
    heat_pump_cop: float                    # heat-pump backup: electricity price / COP
    hardware_chf_per_server: float
    hardware_life_years: float              # the purchase is spread evenly over this
    maintenance_fraction_per_year: float    # of the hardware price
    # further yearly costs, each as a share of the hardware price: e.g. building, network, staff
    yearly_fractions: Mapping[str, float] = field(default_factory=dict)
    installation_fraction: float = 0.0      # one-off, of the hardware price; spread over its life
    idle_power_fraction: float = 0.0        # draw of an idle GPU as a share of its full-load draw
    backup: str = "heat_pump"               # one of BACKUPS
    gas_chf_per_kwh: float = 0.0            # gas backup: price of a kWh of gas
    boiler_efficiency: float = 0.90         # gas backup: kWh of heat per kWh of gas

    def __post_init__(self) -> None:
        if self.hardware_life_years <= 0 or self.heat_pump_cop <= 0:
            raise ValueError("hardware life and heat pump COP must be positive")
        if self.backup not in BACKUPS:
            raise ValueError(f"backup must be one of {BACKUPS}")
        if self.backup == "gas" and (self.gas_chf_per_kwh <= 0
                                     or not 0 < self.boiler_efficiency <= 1.2):
            raise ValueError("gas backup needs a positive gas price and an efficiency up to 1.2")
        shares = [self.maintenance_fraction_per_year, self.installation_fraction,
                  self.idle_power_fraction, *self.yearly_fractions.values()]
        if min(shares) < 0:
            raise ValueError("cost shares cannot be negative")

    @property
    def heat_value_chf_per_kwh(self) -> float:
        """What the backup pays for one kWh of heat: what a kWh of server heat saves."""
        if self.backup == "gas":
            return self.gas_chf_per_kwh / self.boiler_efficiency
        return self.electricity_chf_per_kwh / self.heat_pump_cop

    def balance(self, servers: int, electricity_kwh: float, gpu_hours: float,
                backup_avoided_kwh: float, days: float = 1.0,
                idle_full_load_kwh: float = 0.0) -> "Balance":
        """The money for `days` of running `servers` servers.

        `backup_avoided_kwh` is the heat the backup heater no longer supplies
        compared with the same days without any server. `idle_full_load_kwh`
        is what the GPUs that stood idle would have drawn at full load; they
        are charged `idle_power_fraction` of it. Their heat is not counted.
        """
        hardware = servers * self.hardware_chf_per_server
        per_day = hardware / DAYS_PER_YEAR * days
        other = {name: per_day * share for name, share in self.yearly_fractions.items()}
        other["installation"] = per_day * self.installation_fraction / self.hardware_life_years
        other["idle power"] = (idle_full_load_kwh * self.idle_power_fraction
                               * self.electricity_chf_per_kwh)
        return Balance(
            gpu_hours=gpu_hours,
            hardware_chf=per_day / self.hardware_life_years,
            electricity_chf=electricity_kwh * self.electricity_chf_per_kwh,
            maintenance_chf=per_day * self.maintenance_fraction_per_year,
            heat_saving_chf=backup_avoided_kwh * self.heat_value_chf_per_kwh,
            other_chf={name: value for name, value in other.items() if value},
        )


@dataclass(frozen=True)
class Balance:
    gpu_hours: float            # compute sold
    hardware_chf: float
    electricity_chf: float
    maintenance_chf: float
    heat_saving_chf: float      # heating cost the house no longer pays
    other_chf: Mapping[str, float] = field(default_factory=dict)   # the further costs, by name

    @property
    def cost_chf(self) -> float:
        return (self.hardware_chf + self.electricity_chf + self.maintenance_chf
                + sum(self.other_chf.values()))

    def break_even_chf_per_gpu_hour(self, with_heat_saving: bool = True) -> float | None:
        """GPU-hour price that covers the costs, optionally after the heat saving."""
        if self.gpu_hours <= 0:
            return None
        saving = self.heat_saving_chf if with_heat_saving else 0.0
        return (self.cost_chf - saving) / self.gpu_hours

    def income_chf(self, chf_per_gpu_hour: float) -> float:
        """What clients pay at this GPU-hour price."""
        return self.gpu_hours * chf_per_gpu_hour

    def profit_chf(self, chf_per_gpu_hour: float, with_heat_saving: bool = True) -> float:
        saving = self.heat_saving_chf if with_heat_saving else 0.0
        return self.income_chf(chf_per_gpu_hour) + saving - self.cost_chf


# ---------------------------------------------------------------------- TierPricing


@dataclass(frozen=True)
class TierPricing:
    """GPU-hour prices of the workload tiers: one base price times a factor per tier.

    The base price is the spot price, so spot has factor 1. `sold` in the
    methods is the GPU-hours billed in each tier.
    """

    base_chf_per_gpu_hour: float
    factors: Mapping[str, float]

    def __post_init__(self) -> None:
        if self.base_chf_per_gpu_hour < 0 or any(f < 0 for f in self.factors.values()):
            raise ValueError("the base price and the tier factors cannot be negative")

    def price(self, tier: str) -> float:
        return self.base_chf_per_gpu_hour * self.factors[tier]

    def weighted_gpu_hours(self, sold: Mapping[str, float]) -> float:
        """GPU-hours sold, each counted as many times as its tier's factor: income per
        franc of base price."""
        return sum(hours * self.factors[tier] for tier, hours in sold.items())

    def income_chf(self, sold: Mapping[str, float]) -> float:
        return self.base_chf_per_gpu_hour * self.weighted_gpu_hours(sold)

    def profit_chf(self, balance: Balance, sold: Mapping[str, float],
                   with_heat_saving: bool = True) -> float:
        saving = balance.heat_saving_chf if with_heat_saving else 0.0
        return self.income_chf(sold) + saving - balance.cost_chf

    def base_price_for(self, balance: Balance, sold: Mapping[str, float], margin: float = 0.0,
                       with_heat_saving: bool = True) -> float | None:
        """Base price at which profit is `margin` of the income; with the default of
        zero, the price that just covers the costs. None if nothing was sold."""
        if not 0 <= margin < 1:
            raise ValueError("margin must be at least 0 and below 1")
        weighted = self.weighted_gpu_hours(sold)
        if weighted <= 0:
            return None
        saving = balance.heat_saving_chf if with_heat_saving else 0.0
        return max(0.0, balance.cost_chf - saving) / ((1 - margin) * weighted)
