"""Siting: which houses should host servers, judged by weather alone.

This is a screening model, coarser than the task-level simulation in
`simulation.py`. Compute is a continuous load in kW with no jobs, deadlines or
servers of a fixed size, so a hundred locations can be compared in seconds.

    heat_requirement   hourly heat a reference house needs at a location
    window_hours       hour indices of a run of months
    run_sites          share a compute load between sites hour by hour
    rank_second_site   6-month savings for every candidate second site

Rules of `run_sites`, per hour:
  1. Compute goes to houses that need heat now, in the order the sites are
     given: the first site is served first, the next takes what it cannot use.
  2. What is left goes to sites with free room in their tank, in the same order.
  3. What is still left has no use for its heat. With `follow_heat` the job is
     paused; otherwise it runs anyway and the heat is dumped.
  4. Each house then uses server heat directly, stores the surplus, and covers
     any remaining requirement from its tank and then from its backup heater.

Savings = heat the houses actually used x electricity price / heat-pump COP,
which is the heat-pump electricity that server heat replaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from .assumptions import AssumptionRegistry, reference_assumptions
from .heat import HeatDemandProfile, WeatherSeries

DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)   # not a leap year
EPS = 1e-12


@dataclass(frozen=True)
class SitingParams:
    recovery: float = 0.85                 # share of server electricity that reaches the house
    tank_kwh: float = 35.0                 # per house
    electricity_chf_per_kwh: float = 0.3325
    heat_pump_cop: float = 2.5
    follow_heat: bool = False              # True: pause compute whose heat nobody can use

    @property
    def heat_value_chf_per_kwh(self) -> float:
        return self.electricity_chf_per_kwh / self.heat_pump_cop


@dataclass
class SiteTotals:
    """Energy totals of one site over the run, in kWh."""

    requirement: float = 0.0     # heat the house needed
    compute: float = 0.0         # electricity the servers drew
    direct: float = 0.0          # server heat used straight away
    from_tank: float = 0.0
    dumped: float = 0.0          # server heat nobody could use
    backup: float = 0.0          # requirement left for the backup heater

    @property
    def covered(self) -> float:
        return self.direct + self.from_tank


@dataclass
class SitingResult:
    sites: list[SiteTotals]
    hours: int
    params: SitingParams
    unplaced_compute: float = 0.0          # kWh that did not fit the server capacity
    paused_compute: float = 0.0            # kWh not run because its heat had no use

    def _sum(self, name: str) -> float:
        return sum(getattr(site, name) for site in self.sites)

    @property
    def requirement_kwh(self) -> float:
        return self._sum("requirement")

    @property
    def covered_kwh(self) -> float:
        return self._sum("covered")

    @property
    def compute_kwh(self) -> float:
        return self._sum("compute")

    @property
    def dumped_kwh(self) -> float:
        return self._sum("dumped")

    @property
    def savings_chf(self) -> float:
        return self.covered_kwh * self.params.heat_value_chf_per_kwh

    @property
    def electricity_cost_chf(self) -> float:
        return self.compute_kwh * self.params.electricity_chf_per_kwh

    @property
    def bill_recovered(self) -> float:
        """Share of the servers' electricity bill paid back by the heat."""
        cost = self.electricity_cost_chf
        return self.savings_chf / cost if cost else 0.0

    @property
    def covered_share(self) -> float:
        need = self.requirement_kwh
        return self.covered_kwh / need if need else 0.0


def heat_requirement(temps_c: Sequence[float], start: datetime = datetime(2025, 1, 1),
                     assumptions: AssumptionRegistry | None = None) -> list[float]:
    """Hourly heat requirement (kWh) of the reference house in this weather.

    Uses the same formula and constants as the yearly simulation: the house is
    identical everywhere, only the weather changes.
    """
    a = assumptions or reference_assumptions()
    profile = HeatDemandProfile.reference_house(
        WeatherSeries.from_temperatures(temps_c, start),
        design_load_kw=a.value("design_heat_load_kw"),
        indoor_setpoint_c=a.value("indoor_setpoint_c"),
        design_delta_k=a.value("design_delta_k"),
        heating_day_threshold_c=a.value("heating_day_threshold_c"),
        hot_water_kwh_per_day=a.value("hot_water_kwh_per_day"),
    )
    return [profile.demand_kwh(t) for t in range(len(profile))]


def window_hours(start_month: int, months: int = 6) -> list[int]:
    """Hour indices of `months` calendar months from `start_month` (1-12).

    A window that passes December continues in January of the same data year.
    """
    if not 1 <= start_month <= 12 or not 1 <= months <= 12:
        raise ValueError("start_month and months must be between 1 and 12")
    first_day = [sum(DAYS_IN_MONTH[:m]) for m in range(12)]
    hours: list[int] = []
    for offset in range(months):
        month = (start_month - 1 + offset) % 12
        begin = first_day[month] * 24
        hours.extend(range(begin, begin + DAYS_IN_MONTH[month] * 24))
    return hours


def run_sites(requirements: Sequence[Sequence[float]], compute_kw: float,
              params: SitingParams = SitingParams(), hours: Sequence[int] | None = None,
              capacity_kw: float | None = None) -> SitingResult:
    """Share a constant compute load between sites, one hour at a time.

    `capacity_kw` is the server capacity at each site; by default every site
    can host the whole load.
    """
    n = len(requirements)
    cap = compute_kw if capacity_kw is None else capacity_kw
    eta, tank = params.recovery, params.tank_kwh
    soc = [0.0] * n
    result = SitingResult([SiteTotals() for _ in range(n)], 0, params)
    for h in (range(len(requirements[0])) if hours is None else hours):
        need = [requirements[i][h] for i in range(n)]
        order = range(n)
        x = [0.0] * n
        left = compute_kw
        for i in order:                                    # 1. heat needed now
            take = min(left, cap - x[i], need[i] / eta)
            x[i] += take
            left -= take
        for i in order:                                    # 2. room in the tank
            take = min(left, cap - x[i], (tank - soc[i]) / eta)
            x[i] += take
            left -= take
        if params.follow_heat:                             # 3. no use for the heat
            result.paused_compute += left
        else:
            for i in order:
                take = min(left, cap - x[i])
                x[i] += take
                left -= take
            result.unplaced_compute += left
        for i in range(n):                                 # 4. settle each house
            site, heat = result.sites[i], eta * x[i]
            direct = min(heat, need[i])
            charge = min(heat - direct, tank - soc[i])
            discharge = min(need[i] - direct, soc[i])
            soc[i] += charge - discharge
            site.requirement += need[i]
            site.compute += x[i]
            site.direct += direct
            site.from_tank += discharge
            site.dumped += heat - direct - charge
            site.backup += need[i] - direct - discharge
        result.hours += 1
    return result


@dataclass(frozen=True)
class SecondSiteScore:
    name: str
    savings_chf: float            # both sites together, over the window
    gain_chf: float               # compared with the first site alone
    bill_recovered: float
    dumped_kwh: float
    requirement_kwh: float        # of the candidate site alone
    result: SitingResult = field(repr=False, compare=False, default=None)


def rank_second_site(requirements: Mapping[str, Sequence[float]], first: str, compute_kw: float,
                     params: SitingParams = SitingParams(), hours: Sequence[int] | None = None,
                     capacity_kw: float | None = None) -> list[SecondSiteScore]:
    """Savings of `first` paired with every other location, best first."""
    alone = run_sites([requirements[first]], compute_kw, params, hours, capacity_kw)
    scores = []
    for name, requirement in requirements.items():
        if name == first:
            continue
        pair = run_sites([requirements[first], requirement], compute_kw, params, hours, capacity_kw)
        scores.append(SecondSiteScore(
            name=name, savings_chf=pair.savings_chf, gain_chf=pair.savings_chf - alone.savings_chf,
            bill_recovered=pair.bill_recovered, dumped_kwh=pair.dumped_kwh,
            requirement_kwh=pair.sites[1].requirement, result=pair))
    return sorted(scores, key=lambda score: (-score.savings_chf, score.name))
