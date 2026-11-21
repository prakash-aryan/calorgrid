"""Heat side: weather, demand, storage, backup heating and the house.

Entities
    WeatherRecord / WeatherSeries   hourly temperature and irradiance
    HeatDemandProfile               hourly space heating and hot water demand
    Building                        indoor temperature drifting inside a comfort band
    HotWaterTank                    the domestic hot water store and its draws
    HeatStorage                     the buffer tank
    BackupHeater                    whatever covers demand the servers cannot
    HeatSignal                      what a house tells the scheduler each step
    HeatFlows                       where one step's heat went
    House                           ties the above together and settles heat
    HeatRange / HeatDelivery        what a house needs and can absorb in a step, and where heat went
    HouseHeatModel                  hourly heat range from the Building and the HotWaterTank

Energy is in kWh per step, power in kW.
"""

from __future__ import annotations

import copy
import csv
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

EPS = 1e-9
WATER_KWH_PER_L_K = 1.163e-3        # heat capacity of water

# Share of the day's hot water drawn in each hour of the day (weights sum to 1).
HOT_WATER_SHAPES: dict[str, tuple[float, ...]] = {
    "flat": tuple([1 / 24] * 24),
    # Illustrative household pattern with a morning and an evening peak.
    "morning_evening": tuple(weight / 100 for weight in (
        0.5, 0.5, 0.5, 0.5, 1, 3, 9, 12, 9, 5, 4, 4,
        5, 4, 3, 3, 3, 5, 8, 7.5, 6, 3.5, 2, 1)),
}


# --------------------------------------------------------------------------- Weather


@dataclass(frozen=True)
class WeatherRecord:
    t: int
    time: datetime              # local time
    temp_c: float
    ghi_w_m2: float | None = None   # global horizontal irradiance (for the solar stretch goal)


class WeatherSeries:
    def __init__(self, records: Sequence[WeatherRecord], source: str = "") -> None:
        self.records = list(records)
        self.source = source

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, t: int) -> WeatherRecord:
        return self.records[t]

    @property
    def start(self) -> datetime:
        return self.records[0].time

    @classmethod
    def from_temperatures(cls, temps_c: Sequence[float], start: datetime,
                          source: str = "synthetic") -> "WeatherSeries":
        return cls(
            [WeatherRecord(t, start + timedelta(hours=t), float(temp))
             for t, temp in enumerate(temps_c)],
            source,
        )

    @classmethod
    def from_open_meteo_csv(cls, path: str | Path) -> "WeatherSeries":
        """Read an Open-Meteo archive CSV (hourly temperature_2m, optional shortwave_radiation).

        The file starts with a short metadata block; data begins at the row
        whose first column is `time`.
        """
        records: list[WeatherRecord] = []
        with open(path, newline="", encoding="utf-8") as handle:
            rows = csv.reader(handle)
            header: list[str] | None = None
            for row in rows:
                if not row:
                    continue
                if header is None:
                    if row[0].strip().lower() == "time":
                        header = [col.strip().lower() for col in row]
                    continue
                temp_col = next(i for i, col in enumerate(header) if col.startswith("temperature_2m"))
                ghi_col = next((i for i, col in enumerate(header)
                                if col.startswith("shortwave_radiation")), None)
                ghi = float(row[ghi_col]) if ghi_col is not None and row[ghi_col] else None
                records.append(WeatherRecord(len(records), datetime.fromisoformat(row[0]),
                                             float(row[temp_col]), ghi))
        if not records:
            raise ValueError(f"no hourly rows found in {path}")
        return cls(records, f"Open-Meteo archive CSV: {Path(path).name}")

    def daily_mean_temps(self) -> dict:
        """Mean hourly temperature per local calendar day."""
        sums: dict = {}
        for record in self.records:
            total, count = sums.get(record.time.date(), (0.0, 0))
            sums[record.time.date()] = (total + record.temp_c, count + 1)
        return {day: total / count for day, (total, count) in sums.items()}


# ----------------------------------------------------------------- HeatDemandProfile


@dataclass(frozen=True)
class HeatDemandProfile:
    space_heating_kwh: tuple[float, ...]   # per step
    hot_water_kwh: tuple[float, ...]       # per step
    dt_h: float = 1.0
    source: str = ""

    def __post_init__(self) -> None:
        if len(self.space_heating_kwh) != len(self.hot_water_kwh):
            raise ValueError("space heating and hot water series differ in length")

    def __len__(self) -> int:
        return len(self.space_heating_kwh)

    def demand_kwh(self, t: int) -> float:
        return self.space_heating_kwh[t] + self.hot_water_kwh[t]

    def forecast_kwh(self, t: int, horizon: int) -> tuple[float, ...]:
        """Demand for steps t .. t+horizon-1 (a perfect forecast from the profile)."""
        return tuple(self.demand_kwh(i) for i in range(t, min(t + horizon, len(self))))

    @property
    def total_space_heating_kwh(self) -> float:
        return sum(self.space_heating_kwh)

    @property
    def total_hot_water_kwh(self) -> float:
        return sum(self.hot_water_kwh)

    @property
    def total_kwh(self) -> float:
        return self.total_space_heating_kwh + self.total_hot_water_kwh

    @classmethod
    def reference_house(cls, weather: WeatherSeries, *, design_load_kw: float,
                        indoor_setpoint_c: float, design_delta_k: float,
                        heating_day_threshold_c: float, hot_water_kwh_per_day: float,
                        hot_water_shape: Sequence[float] | None = None,
                        dt_h: float = 1.0) -> "HeatDemandProfile":
        """The brief's formula.

        Space heating = max(0, load x (setpoint - T) / delta) on heating days
        only; a heating day is a local calendar day whose mean hourly
        temperature is below the threshold. Hot water is the same every day.
        """
        if hot_water_shape is not None and (
            len(hot_water_shape) != 24 or abs(sum(hot_water_shape) - 1) > 1e-6
        ):
            raise ValueError("hot_water_shape needs 24 weights summing to 1")
        daily_mean = weather.daily_mean_temps()
        space, water = [], []
        for record in weather.records:
            heating_day = daily_mean[record.time.date()] < heating_day_threshold_c
            load_kw = max(0.0, design_load_kw * (indoor_setpoint_c - record.temp_c) / design_delta_k)
            space.append(load_kw * dt_h if heating_day else 0.0)
            weight = hot_water_shape[record.time.hour] if hot_water_shape else 1 / 24
            water.append(hot_water_kwh_per_day * weight * dt_h)
        return cls(tuple(space), tuple(water), dt_h,
                   f"reference-house formula on {weather.source}")

    @classmethod
    def from_shape(cls, shape: Sequence[float], *, annual_space_heating_kwh: float,
                   hot_water_kwh_per_day: float, dt_h: float = 1.0,
                   source: str = "") -> "HeatDemandProfile":
        """Scale any hourly shape (e.g. a column of a load-profile CSV) to an annual total."""
        total = sum(shape)
        if total <= 0:
            raise ValueError("shape must contain positive values")
        space = tuple(value / total * annual_space_heating_kwh for value in shape)
        water = tuple(hot_water_kwh_per_day / 24 * dt_h for _ in shape)
        return cls(space, water, dt_h, source)

    @classmethod
    def from_csv(cls, path: str | Path, *, space_heating_col: str,
                 hot_water_col: str | None = None, hot_water_kwh_per_day: float = 0.0,
                 scale: float = 1.0, dt_h: float = 1.0) -> "HeatDemandProfile":
        """Read a ready-made profile whose columns are already in kWh per step
        (use `scale` to convert units)."""
        space, water = [], []
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                space.append(float(row[space_heating_col]) * scale)
                if hot_water_col:
                    water.append(float(row[hot_water_col]) * scale)
                else:
                    water.append(hot_water_kwh_per_day / 24 * dt_h)
        return cls(tuple(space), tuple(water), dt_h, f"CSV profile: {Path(path).name}")


def read_csv_column(path: str | Path, column: str) -> list[float]:
    """One numeric column of a CSV, e.g. `SFH_OLD (MWh)` from the HSR profile."""
    with open(path, newline="", encoding="utf-8") as handle:
        return [float(row[column]) for row in csv.DictReader(handle)]


# -------------------------------------------------------------------------- Building


@dataclass
class Building:
    """The house as a thermal mass whose indoor temperature is a state, not a constant.

        C x dT_in/dt = heat delivered - H x (T_in - T_out)

    H is the heat loss per degree (the brief's design load / design delta) and
    C the heat stored per degree. The indoor temperature may drift between
    `min_temp_c` and `max_temp_c`; `HeatDemandProfile.reference_house` is the
    special case where it is held at one setpoint.
    """

    loss_kw_per_k: float            # H
    capacity_kwh_per_k: float       # C
    min_temp_c: float               # lower edge of the comfort band
    max_temp_c: float               # upper edge
    temp_c: float                   # indoor temperature now

    def __post_init__(self) -> None:
        if self.loss_kw_per_k <= 0 or self.capacity_kwh_per_k <= 0:
            raise ValueError("heat loss and thermal capacity must be positive")
        if not self.min_temp_c <= self.temp_c <= self.max_temp_c:
            raise ValueError("indoor temperature must start inside the comfort band")

    @property
    def time_constant_h(self) -> float:
        """C / H: how slowly the house follows the outdoor temperature."""
        return self.capacity_kwh_per_k / self.loss_kw_per_k

    @property
    def stored_kwh(self) -> float:
        """Heat held above the lower edge of the band."""
        return self.capacity_kwh_per_k * (self.temp_c - self.min_temp_c)

    def loss_kwh(self, outdoor_c: float, dt_h: float = 1.0) -> float:
        """Heat leaving through the envelope this step (negative when it is warmer outside)."""
        return self.loss_kw_per_k * (self.temp_c - outdoor_c) * dt_h

    def heat_to_reach_kwh(self, target_c: float, outdoor_c: float, dt_h: float = 1.0) -> float:
        """Heat that brings the indoor temperature to `target_c` by the end of the step."""
        self._check_step(dt_h)
        return max(0.0, self.loss_kwh(outdoor_c, dt_h)
                   + self.capacity_kwh_per_k * (target_c - self.temp_c))

    def min_heat_kwh(self, outdoor_c: float, dt_h: float = 1.0) -> float:
        """Least heat that keeps the house at or above the band this step."""
        return self.heat_to_reach_kwh(self.min_temp_c, outdoor_c, dt_h)

    def max_heat_kwh(self, outdoor_c: float, dt_h: float = 1.0) -> float:
        """Most heat the house can take this step without leaving the band."""
        return self.heat_to_reach_kwh(self.max_temp_c, outdoor_c, dt_h)

    def apply(self, heat_kwh: float, outdoor_c: float, dt_h: float = 1.0) -> float:
        """Advance one step with `heat_kwh` delivered; returns the new indoor temperature.

        Pure physics: it does not clamp to the band, so too little heat shows
        up as a house below `min_temp_c`.
        """
        self._check_step(dt_h)
        self.temp_c += (heat_kwh - self.loss_kwh(outdoor_c, dt_h)) / self.capacity_kwh_per_k
        return self.temp_c

    def _check_step(self, dt_h: float) -> None:
        if dt_h <= 0 or dt_h > self.time_constant_h:
            raise ValueError("time step must be positive and no longer than C / H")


# ---------------------------------------------------------------------- HotWaterTank


@dataclass
class HotWaterTank:
    """The domestic hot water store.

    The tank stays full of water; what empties is its heat, because every draw
    replaces hot water with cold mains water. The level is that heat in kWh.
    """

    capacity_kwh: float
    level_kwh: float
    reserve_kwh: float = 0.0            # level the tank must not fall below
    volume_l: float | None = None

    def __post_init__(self) -> None:
        if self.capacity_kwh <= 0 or not 0 <= self.reserve_kwh <= self.capacity_kwh:
            raise ValueError("invalid hot water tank capacity or reserve")
        if not 0 <= self.level_kwh <= self.capacity_kwh + EPS:
            raise ValueError("hot water level must be between empty and full")

    @classmethod
    def from_volume(cls, volume_l: float, hot_c: float, mains_c: float, *,
                    reserve_fraction: float = 0.0,
                    fill_fraction: float = 1.0) -> "HotWaterTank":
        """A tank of `volume_l` litres heated from mains temperature to `hot_c`."""
        capacity = volume_l * WATER_KWH_PER_L_K * (hot_c - mains_c)
        return cls(capacity, capacity * fill_fraction, capacity * reserve_fraction, volume_l)

    @property
    def headroom_kwh(self) -> float:
        return max(0.0, self.capacity_kwh - self.level_kwh)

    @property
    def fill_fraction(self) -> float:
        return self.level_kwh / self.capacity_kwh

    def min_heat_kwh(self, draw_kwh: float) -> float:
        """Least heat that keeps the tank at its reserve after this step's draw."""
        return max(0.0, self.reserve_kwh - (self.level_kwh - draw_kwh))

    def max_heat_kwh(self, draw_kwh: float) -> float:
        """Heat that leaves the tank full after this step's draw."""
        return max(0.0, self.capacity_kwh - (self.level_kwh - draw_kwh))

    def apply(self, heat_kwh: float, draw_kwh: float) -> float:
        """Advance one step; returns the part of the draw the tank could not serve."""
        if heat_kwh > self.max_heat_kwh(draw_kwh) + EPS:
            raise ValueError("more heat than the hot water tank can take")
        level = self.level_kwh + heat_kwh - draw_kwh
        self.level_kwh = min(self.capacity_kwh, max(0.0, level))
        return max(0.0, -level)


# ----------------------------------------------------------------------- HeatStorage


@dataclass
class HeatStorage:
    capacity_kwh: float
    soc_kwh: float = 0.0                 # state of charge
    loss_fraction_per_h: float = 0.0

    def __post_init__(self) -> None:
        if self.capacity_kwh < 0 or not 0 <= self.soc_kwh <= self.capacity_kwh + EPS:
            raise ValueError("invalid tank capacity or state of charge")

    @property
    def headroom_kwh(self) -> float:
        return max(0.0, self.capacity_kwh - self.soc_kwh)

    def charge(self, kwh: float) -> float:
        """Store up to `kwh`; returns what was accepted."""
        accepted = min(max(kwh, 0.0), self.headroom_kwh)
        self.soc_kwh += accepted
        return accepted

    def discharge(self, kwh: float) -> float:
        """Draw up to `kwh`; returns what was delivered."""
        delivered = min(max(kwh, 0.0), self.soc_kwh)
        self.soc_kwh -= delivered
        return delivered

    def apply_losses(self, hours: float) -> float:
        lost = self.soc_kwh * (1 - (1 - self.loss_fraction_per_h) ** hours)
        self.soc_kwh -= lost
        return lost


# ---------------------------------------------------------------------- BackupHeater


@dataclass
class BackupHeater:
    """Covers heat demand that servers and tank cannot (a heat pump in the brief)."""

    cop: float
    heat_kwh: float = 0.0

    def supply(self, kwh: float) -> float:
        self.heat_kwh += max(kwh, 0.0)
        return max(kwh, 0.0)

    @property
    def electricity_kwh(self) -> float:
        return self.heat_kwh / self.cop


# ------------------------------------------------------------- HeatSignal, HeatFlows


@dataclass(frozen=True)
class HeatSignal:
    """The one input that steers scheduling: how much heat a house can use now."""

    house_id: str
    t: int
    demand_kwh: float                  # space heating + hot water this step
    tank_soc_kwh: float
    tank_headroom_kwh: float
    forecast_kwh: tuple[float, ...] = ()   # demand for this and the following steps

    @property
    def absorbable_kwh(self) -> float:
        """Server heat that can be used or stored this step without dumping."""
        return self.demand_kwh + self.tank_headroom_kwh

    def servers_wanted(self, heat_per_server_kwh: float) -> int:
        return int(math.floor(self.absorbable_kwh / heat_per_server_kwh + EPS))


@dataclass(frozen=True)
class HeatFlows:
    """Where one step's heat went.

    server_heat = direct + to_tank + dumped
    demand      = direct + from_tank + backup
    """

    demand_kwh: float
    server_heat_kwh: float
    direct_kwh: float       # server heat used immediately
    to_tank_kwh: float
    from_tank_kwh: float
    dumped_kwh: float       # server heat nobody could use
    backup_kwh: float       # demand left for the backup heater
    tank_loss_kwh: float
    tank_soc_kwh: float     # after the step

    @property
    def covered_kwh(self) -> float:
        """Demand met by server heat, directly or via the tank."""
        return self.direct_kwh + self.from_tank_kwh


# ----------------------------------------------------------------------------- House


@dataclass
class House:
    id: str
    name: str
    region: str                # e.g. "CH-NW"; matched against task region constraints
    latitude: float
    longitude: float
    profile: HeatDemandProfile
    storage: HeatStorage
    backup: BackupHeater
    server_ids: list[str] = field(default_factory=list)

    def heat_signal(self, t: int, horizon: int = 24) -> HeatSignal:
        return HeatSignal(
            house_id=self.id,
            t=t,
            demand_kwh=self.profile.demand_kwh(t),
            tank_soc_kwh=self.storage.soc_kwh,
            tank_headroom_kwh=self.storage.headroom_kwh,
            forecast_kwh=self.profile.forecast_kwh(t, horizon),
        )

    def settle(self, t: int, server_heat_kwh: float) -> HeatFlows:
        """Account for one step: server heat first meets demand, the surplus
        charges the tank, the tank covers what is still missing, and the backup
        heater takes the rest."""
        demand = self.profile.demand_kwh(t)
        direct = min(server_heat_kwh, demand)
        surplus = server_heat_kwh - direct
        to_tank = self.storage.charge(surplus)
        dumped = surplus - to_tank
        if dumped < EPS:                 # float noise, not real heat
            dumped = 0.0
        residual = demand - direct
        from_tank = self.storage.discharge(residual)
        backup = self.backup.supply(residual - from_tank)
        loss = self.storage.apply_losses(self.profile.dt_h)
        return HeatFlows(demand, server_heat_kwh, direct, to_tank, from_tank, dumped,
                         backup, loss, self.storage.soc_kwh)


# ---------------------------------------------- HeatRange, HeatDelivery, HouseHeatModel


@dataclass(frozen=True)
class HeatRange:
    """What a house needs and can absorb in one step, split by use.

    The minimum is heat that must arrive (from servers or the backup); the
    maximum is the most the house can take without wasting any.
    """

    space_min_kwh: float
    space_max_kwh: float
    water_min_kwh: float
    water_max_kwh: float

    @property
    def must_run_kwh(self) -> float:
        return self.space_min_kwh + self.water_min_kwh

    @property
    def absorbable_kwh(self) -> float:
        return self.space_max_kwh + self.water_max_kwh


@dataclass(frozen=True)
class HeatDelivery:
    """Where one step's heat went.

    supplied = to_space + to_water + dumped
    """

    range: HeatRange
    supplied_kwh: float
    to_space_kwh: float         # server heat into the building
    to_water_kwh: float         # server heat into the hot water tank
    backup_space_kwh: float     # must-run heat the servers did not cover
    backup_water_kwh: float
    dumped_kwh: float           # server heat beyond what the house could absorb
    water_draw_kwh: float
    indoor_c: float             # after the step
    tank_level_kwh: float       # after the step

    @property
    def used_kwh(self) -> float:
        return self.to_space_kwh + self.to_water_kwh

    @property
    def backup_kwh(self) -> float:
        return self.backup_space_kwh + self.backup_water_kwh


@dataclass
class HouseHeatModel:
    """Hourly heat a house needs and can absorb, from its two uses.

    Space heating follows the Building: its range is the heat that keeps the
    indoor temperature inside the comfort band, limited by what the heating
    system can deliver. Hot water follows the HotWaterTank: the household
    draws from it by the hour, and the range is the heat that keeps it between
    its reserve and full.
    """

    building: Building
    tank: HotWaterTank
    hot_water_kwh_per_day: float
    draw_shape: Sequence[float]         # 24 hourly weights summing to 1
    max_space_heat_kw: float            # most the heating system can deliver
    dt_h: float = 1.0

    def __post_init__(self) -> None:
        if len(self.draw_shape) != 24 or abs(sum(self.draw_shape) - 1) > 1e-6:
            raise ValueError("draw_shape needs 24 weights summing to 1")
        if self.max_space_heat_kw <= 0:
            raise ValueError("the heating system needs a positive output")

    def backup_ahead_kwh(self, heat_kwh: Sequence[float], outdoor_c: Sequence[float],
                         start_hour: int, heating: Sequence[bool]) -> float:
        """Backup heat over the coming steps if the servers supplied `heat_kwh`, without
        changing the model. The same arithmetic as deliver(), step after step, kept in
        plain numbers because a scheduler asks this question many times."""
        b, dt = self.building, self.dt_h
        loss_per_k, capacity, low, high = (b.loss_kw_per_k, b.capacity_kwh_per_k,
                                           b.min_temp_c, b.max_temp_c)
        full, reserve = self.tank.capacity_kwh, self.tank.reserve_kwh
        temp, level, total = b.temp_c, self.tank.level_kwh, 0.0
        for i, supplied in enumerate(heat_kwh):
            loss = loss_per_k * (temp - outdoor_c[i]) * dt
            limit = self.max_space_heat_kw * dt if heating[i] else 0.0
            space_min = min(max(0.0, loss + capacity * (low - temp)), limit)
            space_max = min(max(0.0, loss + capacity * (high - temp)), limit)
            draw = self.hot_water_kwh_per_day * self.draw_shape[(start_hour + i) % 24] * dt
            water_min = max(0.0, reserve - (level - draw))
            water_max = max(0.0, full - (level - draw))
            left = max(supplied, 0.0)
            water = min(left, water_min)
            left -= water
            space = min(left, space_min)
            left -= space
            backup_water, backup_space = water_min - water, space_min - space
            more = min(left, water_max - water_min)
            water += more
            space += min(left - more, space_max - space_min)
            total += backup_water + backup_space
            level = min(full, max(0.0, level + water + backup_water - draw))
            temp += (space + backup_space - loss) / capacity
        return total

    def copy(self) -> "HouseHeatModel":
        """An independent model in the same state, for trying out what a change would do."""
        twin = copy.copy(self)           # plain copies: the state may be outside the
        twin.building = copy.copy(self.building)   # start-up checks, e.g. a warm summer house
        twin.tank = copy.copy(self.tank)
        return twin

    def scaled(self, factor: float) -> "HouseHeatModel":
        """The same building `factor` times as large: heat loss, thermal mass, heating
        system, hot water use and tank all grow together; temperatures stay the same."""
        if factor <= 0:
            raise ValueError("size factor must be positive")
        b, t = self.building, self.tank
        return replace(
            self,
            building=replace(b, loss_kw_per_k=b.loss_kw_per_k * factor,
                             capacity_kwh_per_k=b.capacity_kwh_per_k * factor),
            tank=replace(t, capacity_kwh=t.capacity_kwh * factor, level_kwh=t.level_kwh * factor,
                         reserve_kwh=t.reserve_kwh * factor,
                         volume_l=t.volume_l * factor if t.volume_l else t.volume_l),
            hot_water_kwh_per_day=self.hot_water_kwh_per_day * factor,
            max_space_heat_kw=self.max_space_heat_kw * factor,
        )

    def draw_kwh(self, hour_of_day: int) -> float:
        """Hot water the household uses in the step that starts at this hour."""
        return self.hot_water_kwh_per_day * self.draw_shape[hour_of_day % 24] * self.dt_h

    def heat_range(self, outdoor_c: float, hour_of_day: int,
                   heating_day: bool = True) -> HeatRange:
        """The heat range for the coming step, from the current state.

        On a non-heating day the space heating range is zero.
        """
        limit = self.max_space_heat_kw * self.dt_h if heating_day else 0.0
        draw = self.draw_kwh(hour_of_day)
        return HeatRange(
            space_min_kwh=min(self.building.min_heat_kwh(outdoor_c, self.dt_h), limit),
            space_max_kwh=min(self.building.max_heat_kwh(outdoor_c, self.dt_h), limit),
            water_min_kwh=self.tank.min_heat_kwh(draw),
            water_max_kwh=self.tank.max_heat_kwh(draw),
        )

    def deliver(self, supplied_kwh: float, outdoor_c: float, hour_of_day: int,
                heating_day: bool = True) -> HeatDelivery:
        """Advance one step with `supplied_kwh` of server heat.

        Server heat covers the must-run amounts first (hot water, then space
        heating), then fills the hot water tank, then warms the building; the
        rest is dumped. The backup covers any must-run heat the servers missed.
        """
        needs = self.heat_range(outdoor_c, hour_of_day, heating_day)
        left = max(supplied_kwh, 0.0)

        def take(amount: float) -> float:
            nonlocal left
            used = min(left, amount)
            left -= used
            return used

        water = take(needs.water_min_kwh)
        space = take(needs.space_min_kwh)
        backup_water = needs.water_min_kwh - water
        backup_space = needs.space_min_kwh - space
        water += take(needs.water_max_kwh - needs.water_min_kwh)
        space += take(needs.space_max_kwh - needs.space_min_kwh)
        dumped = left if left > EPS else 0.0

        draw = self.draw_kwh(hour_of_day)
        self.tank.apply(water + backup_water, draw)
        indoor = self.building.apply(space + backup_space, outdoor_c, self.dt_h)
        return HeatDelivery(needs, max(supplied_kwh, 0.0), space, water, backup_space,
                            backup_water, dumped, draw, indoor, self.tank.level_kwh)
