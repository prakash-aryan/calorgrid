"""Putting it together: the fleet, a scenario and the run loop.

Entities
    Fleet          every house and every server in the network
    Scenario       the knobs of one experiment, read from the assumption registry
    SimulationRun  the step loop shared by the yearly simulation and a live demo
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Sequence

from .assumptions import AssumptionRegistry, reference_assumptions
from .clock import Clock
from .compute import HardwareSpec, Server, ServerRegistry, ServerState
from .economics import HourRecord, Ledger, Tariff, YearlyBalance
from .execution import Action, Dispatcher, Executor, SimulatedExecutor
from .heat import (HOT_WATER_SHAPES, BackupHeater, Building, HeatDemandProfile, HeatSignal,
                   HeatStorage, HotWaterTank, House, HouseHeatModel, WeatherSeries)
from .scheduling import HeatFollowingScheduler, Scheduler
from .workload import ArrivalProcess, Demand, JobType

EPS = 1e-9


class Fleet:
    """The distributed network: houses and the servers placed in them."""

    def __init__(self) -> None:
        self.houses: dict[str, House] = {}
        self.registry = ServerRegistry()

    def add_house(self, house: House, servers: int, *, power_kw: float,
                  heat_recovery_fraction: float, idle_power_kw: float = 0.0,
                  hardware: HardwareSpec | None = None) -> list[Server]:
        if house.id in self.houses:
            raise KeyError(f"house {house.id!r} already in the fleet")
        self.houses[house.id] = house
        added = []
        for index in range(1, servers + 1):
            server = self.registry.add(Server(
                id=f"{house.id}-s{index:02d}", house_id=house.id, region=house.region,
                power_kw=power_kw, heat_recovery_fraction=heat_recovery_fraction,
                idle_power_kw=idle_power_kw, hardware=hardware,
            ))
            house.server_ids.append(server.id)
            added.append(server)
        return added

    def signals(self, t: int, horizon: int = 24) -> dict[str, HeatSignal]:
        return {house.id: house.heat_signal(t, horizon) for house in self.houses.values()}

    def steps_available(self) -> int:
        """Longest run every house has demand data for."""
        return min(len(house.profile) for house in self.houses.values())


@dataclass
class Scenario:
    """One experiment. Every physical and economic number comes from `assumptions`."""

    name: str
    servers_per_house: int
    job_types: Sequence[JobType]
    arrivals: Sequence[ArrivalProcess]
    assumptions: AssumptionRegistry = field(default_factory=reference_assumptions)
    region: str = "CH-NW"

    def tariff(self) -> Tariff:
        a = self.assumptions
        return Tariff(
            electricity_chf_per_kwh=a.value("electricity_price_chf_per_kwh"),
            heat_pump_cop=a.value("heat_pump_cop"),
            fixed_cost_chf_per_server_year=a.value("fixed_cost_chf_per_server_year"),
            source=a.get("electricity_price_chf_per_kwh").source,
        )

    def reference_house(self, weather: WeatherSeries, house_id: str = "ref") -> House:
        a = self.assumptions
        dt_h = a.value("time_step_h")
        profile = HeatDemandProfile.reference_house(
            weather,
            design_load_kw=a.value("design_heat_load_kw"),
            indoor_setpoint_c=a.value("indoor_setpoint_c"),
            design_delta_k=a.value("design_delta_k"),
            heating_day_threshold_c=a.value("heating_day_threshold_c"),
            hot_water_kwh_per_day=a.value("hot_water_kwh_per_day"),
            dt_h=dt_h,
        )
        return self.house(house_id, "Reference house, Basel", profile)

    def building(self) -> Building:
        """The reference house as a thermal mass, with the same heat loss as the
        brief's formula and the comfort band from the registry."""
        a = self.assumptions
        return Building(
            loss_kw_per_k=a.value("design_heat_load_kw") / a.value("design_delta_k"),
            capacity_kwh_per_k=a.value("building_thermal_capacity_kwh_per_k"),
            min_temp_c=a.value("indoor_min_c"),
            max_temp_c=a.value("indoor_max_c"),
            temp_c=a.value("indoor_setpoint_c"),
        )

    def heat_model(self, indoor_c: float | None = None,
                   tank_fill_fraction: float = 1.0) -> HouseHeatModel:
        """The reference house's hourly heat range model: the building plus a
        hot water tank, starting at `indoor_c` with the tank filled as given."""
        a = self.assumptions
        building = self.building()
        if indoor_c is not None:
            building = replace(building, temp_c=indoor_c)
        shape = a.get("hot_water_draw_shape").value
        return HouseHeatModel(
            building=building,
            tank=HotWaterTank.from_volume(
                a.value("hot_water_tank_litres"), a.value("hot_water_tank_temp_c"),
                a.value("mains_water_temp_c"),
                reserve_fraction=a.value("hot_water_reserve_fraction"),
                fill_fraction=tank_fill_fraction),
            hot_water_kwh_per_day=a.value("hot_water_kwh_per_day"),
            draw_shape=HOT_WATER_SHAPES[str(shape)],
            max_space_heat_kw=a.value("space_heating_max_output_kw"),
            dt_h=a.value("time_step_h"),
        )

    def house(self, house_id: str, name: str, profile: HeatDemandProfile,
              latitude: float = 47.56, longitude: float = 7.59) -> House:
        """A house with this scenario's tank and backup heater and any demand profile."""
        a = self.assumptions
        return House(
            id=house_id, name=name, region=self.region,
            latitude=latitude, longitude=longitude, profile=profile,
            storage=HeatStorage(a.value("tank_capacity_kwh"), a.value("tank_initial_soc_kwh"),
                                a.value("tank_loss_fraction_per_h")),
            backup=BackupHeater(cop=a.value("heat_pump_cop")),
        )

    def build(self, houses: Sequence[House], start: datetime, *,
              scheduler: Scheduler | None = None, executor: Executor | None = None,
              seconds_per_step: float | None = None) -> "SimulationRun":
        a = self.assumptions
        dt_h = a.value("time_step_h")
        fleet = Fleet()
        for house in houses:
            fleet.add_house(house, self.servers_per_house,
                            power_kw=a.value("server_power_kw"),
                            heat_recovery_fraction=a.value("heat_recovery_fraction"),
                            idle_power_kw=a.value("server_idle_power_kw"))
        demand = Demand(self.job_types, self.arrivals, dt_h)
        return SimulationRun(
            scenario=self,
            fleet=fleet,
            demand=demand,
            scheduler=scheduler or HeatFollowingScheduler(dt_h=dt_h),
            dispatcher=Dispatcher(demand, fleet.registry, executor or SimulatedExecutor()),
            clock=Clock(start=start, dt_h=dt_h, seconds_per_step=seconds_per_step),
            tariff=self.tariff(),
        )


@dataclass
class SimulationRun:
    """Runs the model one step at a time.

    Each step: jobs arrive, hopeless jobs are dropped, houses report their heat
    signal, the scheduler decides, the dispatcher applies the decisions, the
    servers run, and each house settles where the heat went.
    """

    scenario: Scenario
    fleet: Fleet
    demand: Demand
    scheduler: Scheduler
    dispatcher: Dispatcher
    clock: Clock
    tariff: Tariff
    ledger: Ledger = field(default_factory=Ledger)

    def step(self) -> list[Action]:
        t, now_h, dt_h = self.clock.t, self.clock.now_h, self.clock.dt_h
        executor, registry = self.dispatcher.executor, self.fleet.registry

        self.demand.step(now_h, self.clock.hour_of_day())
        for task in self.demand.overdue(now_h):
            self.dispatcher.drop(task, now_h)

        actions = self.scheduler.decide(now_h, self.fleet.signals(t), self.demand, registry)
        self.dispatcher.apply(actions, now_h)

        for house in self.fleet.houses.values():
            heat = electricity = running_hours = income = 0.0
            servers_running = 0
            for server in registry.by_house(house.id):
                if server.state is not ServerState.RUNNING:
                    idle_electricity, idle_heat = server.idle(dt_h)
                    electricity += idle_electricity
                    heat += idle_heat
                    continue
                task = self.demand.get(server.task_id)
                finished = executor.is_finished(task)
                worked = task.advance(dt_h * server.throttle)
                # Simulated jobs occupy the server only until their work is done;
                # real jobs occupy it for the whole step.
                hours = worked / server.throttle if finished is None else dt_h
                job_electricity, job_heat = server.run(hours, task.power_kw)
                electricity += job_electricity
                heat += job_heat
                running_hours += hours
                income += hours * task.price_chf_per_server_hour
                servers_running += 1
                done = task.remaining_h <= EPS if finished is None else finished
                if done:
                    self.dispatcher.finish(task, now_h + hours)
            flows = house.settle(t, heat)
            self.ledger.record(HourRecord(t, house.id, flows, electricity, running_hours,
                                          servers_running, income))
        self.clock.advance()
        return actions

    def run(self, steps: int | None = None) -> dict[str, YearlyBalance]:
        remaining = self.fleet.steps_available() - self.clock.t
        for _ in range(remaining if steps is None else min(steps, remaining)):
            self.step()
            self.clock.wait()
        return self.balances()

    def balances(self) -> dict[str, YearlyBalance]:
        missed = self.demand.missed_deadline_pct()
        return {
            house.id: self.ledger.balance(house.id, len(house.server_ids), self.tariff,
                                          self.clock.dt_h, missed)
            for house in self.fleet.houses.values()
        }
