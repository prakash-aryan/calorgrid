"""Compute side: the servers and the registry that knows their state.

Entities
    HardwareSpec    optional description of a machine
    PowerProfile    a system's measured power at full load on one benchmark
    FleetMachine    the one server the fleet is built from: power, price, market, speed
    GasPrice        what the real house pays for its heating gas in one year
    Server          one heat-producing server in a house
    ServerRegistry  every server, with lookups by house, region and state

Results are normalised to a 1 kW reference server, so the model only needs a
server's power, its recoverable heat fraction and what it is running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


class ServerState(str, Enum):
    OFF = "off"          # no job, drawing idle power (0 by default)
    RUNNING = "running"
    FAILED = "failed"    # out of service; the scheduler skips it


@dataclass(frozen=True)
class HardwareSpec:
    """Descriptive only; the scheduler does not use it."""

    cpu_cores: int | None = None
    gpu_model: str | None = None
    ram_gb: float | None = None
    vram_gb: float | None = None


@dataclass(frozen=True)
class PowerProfile:
    """A system's average wall power while it runs one benchmark at full load,
    as published in the MLPerf Inference results with power measurement."""

    id: str
    system: str
    accelerator: str
    gpus: int                       # accelerators in the system
    benchmark: str
    scenario: str
    full_load_kw: float
    source: str                     # round and submission the number comes from
    url: str = ""

    def __post_init__(self) -> None:
        if self.gpus <= 0 or self.full_load_kw <= 0:
            raise ValueError(f"{self.id}: accelerators and power must be positive")

    @property
    def kw_per_gpu(self) -> float:
        """The whole system's power shared out over its accelerators."""
        return self.full_load_kw / self.gpus

    def power_kw(self, gpus_busy: float) -> float:
        """Draw with `gpus_busy` accelerators working.

        Proportional to the load and zero when nothing runs: the results
        measure full load only, so the idle draw of a real machine is left out.
        """
        return self.kw_per_gpu * min(max(gpus_busy, 0.0), self.gpus)


def load_power_profiles(path: str | Path) -> list[PowerProfile]:
    """Read the file written by scripts/fetch_workloads.py (data/mlperf_power.json)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        PowerProfile(
            id=row["id"], system=row["system"], accelerator=row["accelerator"],
            gpus=row["accelerators"] * row["nodes"], benchmark=row["benchmark"],
            scenario=row["scenario"], full_load_kw=row["power_w"] / 1000,
            source=f"MLPerf Inference {row['round']}, submission {row['submission']} "
                   f"({row['submitter']})",
            url=row.get("details_url") or data["source_url"],
        )
        for row in data["systems"]
    ]


@dataclass(frozen=True)
class MarketPrice:
    """What an hour of the fleet's GPU rents for on one market, in CHF per GPU-hour."""

    id: str
    market: str                     # e.g. "specialist GPU clouds"
    provider: str
    on_demand_chf: float
    spot_chf: float                 # the lowest tier: spot, preemptible or community
    on_demand_source: str
    spot_source: str


@dataclass(frozen=True)
class FleetMachine:
    """The one server every figure describes: its measured power, what it costs to buy,
    what its GPU-hours rent for, and how fast it is next to the GPUs of the job trace.

    Pages take all of these from here, so the cost of one machine is never
    compared with the rental price of another.
    """

    gpu: str
    year: int
    profile: PowerProfile
    hardware_chf: float             # typical purchase price of one server
    hardware_low_chf: float
    hardware_high_chf: float
    hardware_source: str
    tariff_chf_per_kwh: float
    tariff_source: str
    chf_per_usd: float
    markets: Mapping[str, MarketPrice]      # by id
    speed_up: Mapping[str, float]           # trace GPU type -> times faster the fleet GPU is
    assumed_speed_up: tuple[str, ...] = ()  # GPU types whose factor is an assumption

    def __post_init__(self) -> None:
        if not self.hardware_low_chf <= self.hardware_chf <= self.hardware_high_chf:
            raise ValueError("the hardware price must lie between its low and high case")
        if any(factor <= 0 for factor in self.speed_up.values()):
            raise ValueError("speed factors must be positive")

    @property
    def hardware_range_chf(self) -> tuple[float, float, float]:
        return self.hardware_low_chf, self.hardware_chf, self.hardware_high_chf


@dataclass(frozen=True)
class GasPrice:
    year: int
    chf_per_kwh: float              # variable price: energy, network and levies
    fixed_chf_per_month: float      # capacity charge, paid whether or not gas is burnt
    months: int                     # months of bills behind the price
    source: str


def load_gas_price(path: str | Path, year: int) -> GasPrice:
    """Read the file written by scripts/gas_price.py (data/gas_price.json)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    row = data["years"][str(year)]
    return GasPrice(year=year, chf_per_kwh=row["chf_per_kwh"],
                    fixed_chf_per_month=row["fixed_chf_per_month"], months=row["months"],
                    source=f"{data['source']}; {year}, {row['months']} months")


def load_fleet_machine(path: str | Path, power_path: str | Path) -> FleetMachine:
    """Read the file written by scripts/fetch_prices.py (data/fleet_machine.json) and
    the power profile it names in data/mlperf_power.json."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    machine, hardware, fx = data["machine"], data["hardware"], data["exchange"]["chf_per_usd"]
    profile = next(p for p in load_power_profiles(power_path) if p.id == machine["power_profile"])
    tariff = next(e for e in data["electricity"] if e["year"] == machine["year"])
    factors = data["speed"]["factors"]
    return FleetMachine(
        gpu=machine["gpu"], year=machine["year"], profile=profile,
        hardware_chf=round(hardware["usd"] * fx), hardware_low_chf=round(hardware["low_usd"] * fx),
        hardware_high_chf=round(hardware["high_usd"] * fx),
        hardware_source=f"{hardware['system']}: {hardware['low_usd']:,} to "
                        f"{hardware['high_usd']:,} USD, typical {hardware['usd']:,} USD, "
                        f"{hardware['year']}, an estimate",
        tariff_chf_per_kwh=tariff["chf_per_kwh"], tariff_source=tariff["source"], chf_per_usd=fx,
        markets={row["id"]: MarketPrice(
            id=row["id"], market=row["market"], provider=row["provider"],
            on_demand_chf=round(row["on_demand_usd"] * fx, 3),
            spot_chf=round(row["spot_usd"] * fx, 3),
            on_demand_source=row["on_demand_source"], spot_source=row["spot_source"])
            for row in data["gpu_hour"]},
        speed_up={gpu: row["factor"] for gpu, row in factors.items()},
        assumed_speed_up=tuple(gpu for gpu, row in factors.items() if row.get("assumed")),
    )


@dataclass
class Server:
    id: str
    house_id: str
    region: str
    power_kw: float                 # electricity at full load
    heat_recovery_fraction: float   # share of electricity recovered as useful heat
    idle_power_kw: float = 0.0
    hardware: HardwareSpec | None = None
    address: str | None = None      # host:port of the machine's executor agent
    # --- state ---
    state: ServerState = ServerState.OFF
    task_id: str | None = None
    throttle: float = 1.0           # 0 < throttle <= 1; scales power and progress
    # --- meters ---
    running_hours: float = 0.0
    electricity_kwh: float = 0.0
    heat_kwh: float = 0.0           # recoverable heat produced

    def __post_init__(self) -> None:
        if self.power_kw <= 0 or not 0 <= self.heat_recovery_fraction <= 1:
            raise ValueError(f"{self.id}: invalid power or heat recovery fraction")

    @property
    def is_free(self) -> bool:
        return self.state is ServerState.OFF

    def draw_kw(self, task_power_kw: float) -> float:
        """Electricity drawn while running a task of the given power."""
        return min(task_power_kw, self.power_kw) * self.throttle

    def heat_kw(self, task_power_kw: float) -> float:
        """Recoverable heat produced while running that task."""
        return self.draw_kw(task_power_kw) * self.heat_recovery_fraction

    def set_throttle(self, throttle: float) -> None:
        if not 0 < throttle <= 1:
            raise ValueError("throttle must be in (0, 1]")
        self.throttle = throttle

    def assign(self, task_id: str) -> None:
        if not self.is_free:
            raise RuntimeError(f"server {self.id} is {self.state.value}, cannot take {task_id}")
        self.state, self.task_id = ServerState.RUNNING, task_id

    def release(self) -> None:
        if self.state is ServerState.RUNNING:
            self.state = ServerState.OFF
        self.task_id = None

    def fail(self) -> str | None:
        """Take the server out of service; returns the task it was running."""
        task_id, self.task_id = self.task_id, None
        self.state = ServerState.FAILED
        return task_id

    def repair(self) -> None:
        if self.state is ServerState.FAILED:
            self.state = ServerState.OFF

    def run(self, hours: float, task_power_kw: float) -> tuple[float, float]:
        """Meter `hours` of running; returns (electricity kWh, recoverable heat kWh)."""
        electricity = self.draw_kw(task_power_kw) * hours
        heat = electricity * self.heat_recovery_fraction
        self.running_hours += hours
        self.electricity_kwh += electricity
        self.heat_kwh += heat
        return electricity, heat

    def idle(self, hours: float) -> tuple[float, float]:
        """Meter `hours` without a job."""
        if self.state is ServerState.FAILED:
            return 0.0, 0.0
        electricity = self.idle_power_kw * hours
        heat = electricity * self.heat_recovery_fraction
        self.electricity_kwh += electricity
        self.heat_kwh += heat
        return electricity, heat


class ServerRegistry:
    """Every server and its live state (the 'server list' a load balancer holds)."""

    def __init__(self) -> None:
        self._servers: dict[str, Server] = {}
        self._by_house: dict[str, list[Server]] = {}

    def add(self, server: Server) -> Server:
        if server.id in self._servers:
            raise KeyError(f"server {server.id!r} already registered")
        self._servers[server.id] = server
        self._by_house.setdefault(server.house_id, []).append(server)
        return server

    def get(self, server_id: str) -> Server:
        return self._servers[server_id]

    def all(self) -> list[Server]:
        return list(self._servers.values())

    def by_house(self, house_id: str) -> list[Server]:
        return list(self._by_house.get(house_id, ()))

    def free(self, house_id: str | None = None, region: str | None = None) -> list[Server]:
        servers = self.by_house(house_id) if house_id else self.all()
        return [s for s in servers if s.is_free and (region is None or s.region == region)]

    def running(self, house_id: str | None = None) -> list[Server]:
        servers = self.by_house(house_id) if house_id else self.all()
        return [s for s in servers if s.state is ServerState.RUNNING]

    def snapshot(self) -> list[dict]:
        """Plain-data view for a dashboard or an API response."""
        return [
            {
                "id": s.id, "house_id": s.house_id, "region": s.region,
                "state": s.state.value, "task_id": s.task_id, "throttle": s.throttle,
                "power_kw": s.power_kw, "running_hours": round(s.running_hours, 3),
                "electricity_kwh": round(s.electricity_kwh, 3), "heat_kwh": round(s.heat_kwh, 3),
            }
            for s in self._servers.values()
        ]
