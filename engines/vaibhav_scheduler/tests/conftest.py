"""Shared fixtures. Weather here is synthetic and only exercises the model;
reported results must use the Open-Meteo Basel 2025 data."""

from datetime import datetime

import pytest

from digitalheat import ArrivalMode, ArrivalProcess, JobType, Scenario, WeatherSeries

START = datetime(2025, 1, 1)


def make_run(temps_c, servers, job_types, arrivals, **build_kwargs):
    scenario = Scenario("test", servers, job_types, arrivals)
    overrides = build_kwargs.pop("overrides", {})
    for key, value in overrides.items():
        scenario.assumptions.override(key, value, "test")
    weather = WeatherSeries.from_temperatures(temps_c, START)
    return scenario.build([scenario.reference_house(weather)], START, **build_kwargs)


@pytest.fixture
def filler():
    """Deadline-free, interruptible job that is always available."""
    job = JobType(id="filler", name="Batch inference", runtime_h=4, power_kw=1.0,
                  interruptible=True, price_chf_per_server_hour=0.10, source="test")
    arrival = ArrivalProcess("filler", ArrivalMode.UNLIMITED, source="test", buffer=16)
    return job, arrival
