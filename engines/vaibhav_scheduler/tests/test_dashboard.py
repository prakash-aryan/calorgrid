"""The live dashboard server: its snapshot, the tier results, the money and its HTTP endpoints."""

import json
import sys
import threading
import urllib.request
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))

import dashboard_server  # noqa: E402
import scheduler_viz  # noqa: E402


@pytest.fixture(scope="module")
def live():
    world = scheduler_viz.World(load=0.85, seed=1)
    return dashboard_server.LiveFleet(world, date(2025, 4, 14), days=2, seconds_per_hour=3600)


def test_snapshot_adds_up(live):
    for _ in range(20):
        live.step()
    snap = live.snapshot()
    fleet = snap["fleet"]
    assert snap["t"] == 21 and snap["hours"] == 48 and not snap["finished"]
    assert fleet["arrived"] == fleet["done"] + fleet["running"] + fleet["booked"] + fleet["waiting"]
    assert len(snap["sites"]) == 6
    for site in snap["sites"]:
        held = sum(job["g"] for server in site["servers"] for job in server)
        assert held <= site["gpus"] + 1e-9
        assert all(sum(job["g"] for job in server) <= 8 + 1e-9 for server in site["servers"])
        assert site["gpu"] <= held + 1e-6              # serverless and dedicated hold more than works
        assert all(len(values) == 21 for values in site["history"].values())
        assert all(job["tier"] in dashboard_server.TIERS for server in site["servers"] for job in server)
    assert {tier["key"] for tier in snap["tiers"]} == set(dashboard_server.TIERS)
    assert sum(tier["share"] for tier in snap["tiers"]) == pytest.approx(100)
    assert sum(tier["arrived"] for tier in snap["tiers"]) == fleet["arrived"]
    json.dumps(snap, allow_nan=False)                  # jobs without a deadline must not leak inf


def test_money_adds_up_and_break_even_price_leaves_no_profit(live):
    money, tiers = live.snapshot()["money"], live.snapshot()["tiers"]
    assert money["cost"] == pytest.approx(
        money["hardware"] + money["maintenance"] + money["electricity"], abs=0.02)
    assert money["net_cost"] == pytest.approx(money["cost"] - money["heat_saving"], abs=0.02)
    assert money["sold"] == pytest.approx(sum(tier["sold"] for tier in tiers), abs=0.1)
    assert money["sold"] > money["gpu_hours"]          # dedicated hosts pay for whole servers
    assert money["income"] == pytest.approx(sum(tier["income"] for tier in tiers), abs=0.1)
    assert money["profit"] == pytest.approx(money["income"] - money["net_cost"], abs=0.05)
    assert money["net_per_gpu_hour"] == pytest.approx(money["net_cost"] / money["sold"], rel=1e-3)
    spot = next(tier for tier in tiers if tier["key"] == "spot")
    assert spot["factor"] == 1 and spot["price"] == pytest.approx(money["base"])
    inputs = live.snapshot()["inputs"]
    assert money["base"] == inputs["spot"]             # P opens at the market's lowest tier
    on_demand = next(tier for tier in tiers if tier["key"] == "on_demand")
    assert on_demand["price"] == pytest.approx(inputs["on_demand"], rel=0.01)
    assert inputs["hardware_low_chf"] < inputs["hardware_chf"] < inputs["hardware_high_chf"]
    for key in ("net_per_gpu_hour", "break_even_base", "target_base"):   # the hardware range
        assert money[key + "_low"] < money[key] < money[key + "_high"]
    assert money["hardware_low"] < money["hardware"] + money["maintenance"] < money["hardware_high"]
    assert spot["break_even_price"] == pytest.approx(money["break_even_base"], abs=1e-4)
    before = live.snapshot()["t"]
    live.control("prices", {"base": money["break_even_base"], "factors": {"on_demand": 4}})
    after = live.snapshot()
    assert after["t"] == before and after["run"] == 1  # prices do not restart or advance the run
    assert next(t for t in after["tiers"] if t["key"] == "on_demand")["factor"] == 4
    live.control("prices", {"base": after["money"]["break_even_base"], "margin": 25})
    assert live.snapshot()["money"]["profit"] == pytest.approx(0.0, abs=0.05)
    assert live.snapshot()["money"]["target_margin"] == 0.25
    for bad in ({"base": -1}, {"margin": 100}, {"factors": {"gold": 2}}):
        with pytest.raises(ValueError):
            live.control("prices", bad)


def test_costs_entered_as_percentages_raise_the_cost_without_restarting(live):
    before = live.snapshot()
    assert before["money"]["other"] == {}                  # only maintenance counts to start with
    assert before["cost_inputs"]["maintenance"]["percent"] == 5
    live.control("costs", {"staff": 10, "building": 2, "network": 1, "installation": 8,
                           "idle_power": 15, "maintenance": 7})
    after = live.snapshot()
    assert after["t"] == before["t"] and after["run"] == before["run"]
    money, was = after["money"], before["money"]
    assert set(money["other"]) == {"staff", "building", "network", "installation", "idle power"}
    assert money["other"]["staff"] == pytest.approx(10 * money["other"]["network"], rel=1e-3)
    assert money["maintenance"] == pytest.approx(was["maintenance"] * 7 / 5, rel=1e-3)
    assert money["cost"] == pytest.approx(money["hardware"] + money["maintenance"]
                                          + money["electricity"] + sum(money["other"].values()),
                                          abs=0.05)
    assert money["net_per_gpu_hour"] > was["net_per_gpu_hour"]
    assert money["break_even_base"] > was["break_even_base"]
    for bad in ({"rent": 5}, {"staff": -1}, {"staff": "a lot"}):
        with pytest.raises(ValueError):
            live.control("costs", bad)
    live.control("costs", {name: c["default"] for name, c in after["cost_inputs"].items()})
    assert live.snapshot()["money"]["other"] == {}



def test_backup_heating_is_gas_by_default_and_can_be_switched_without_restarting(live):
    before = live.snapshot()
    heating, inputs = before["heating"], before["inputs"]
    assert heating["backup"] == "gas"
    gas, pump = heating["options"]["gas"], heating["options"]["heat_pump"]
    assert gas["chf_per_heat_kwh"] == pytest.approx(inputs["gas"] / inputs["boiler_efficiency"],
                                                    abs=1e-4)
    assert pump["chf_per_heat_kwh"] == pytest.approx(inputs["tariff"] / inputs["cop"], abs=1e-4)
    live.control("costs", {"backup": "heat_pump"})
    after = live.snapshot()
    assert after["t"] == before["t"] and after["run"] == before["run"]
    assert after["heating"]["backup"] == "heat_pump"
    ratio = pump["chf_per_heat_kwh"] / gas["chf_per_heat_kwh"]
    assert after["money"]["heat_saving"] == pytest.approx(before["money"]["heat_saving"] * ratio,
                                                          rel=1e-3)   # options are rounded
    assert after["money"]["cost"] == before["money"]["cost"]
    for bad in ({"backup": "oil"}, {"backup": ["gas"]}):
        with pytest.raises(ValueError):
            live.control("costs", bad)
    live.control("costs", {"backup": "gas"})
    assert live.snapshot()["heating"]["backup"] == "gas"

def test_history_is_sent_only_from_where_the_page_left_off(live):
    snap = live.snapshot()
    newer = live.snapshot(since=snap["t"] - 3, run_id=snap["run"])
    assert newer["from"] == snap["t"] - 3
    assert len(newer["sites"][0]["history"]["dumped"]) == 3
    assert live.snapshot(since=5, run_id=snap["run"] + 99)["from"] == 0     # another run: send all


def test_controls(live):
    live.control("pause")
    assert live.snapshot()["paused"]
    live.control("play")
    live.control("speed", 5)
    assert live.snapshot()["speed"] == 5
    before = live.snapshot()["t"]
    live.control("step")
    assert live.snapshot()["t"] == before + 1
    with pytest.raises(ValueError):
        live.control("speed", 3)                     # not one of the offered speeds
    live.control("restart")
    snap = live.snapshot()
    assert snap["t"] == 1 and snap["run"] == 2
    live.control("finish")
    assert live.snapshot()["finished"] and live.snapshot()["t"] == 48
    assert not live.step()                           # nothing left to run


def test_configure_starts_a_new_run_with_the_new_mix(live):
    run = live.snapshot()["run"]
    live.control("configure", {"mix": {"spot": 100}, "load": 60, "start": "2025-07-14", "days": 2})
    live.control("finish")
    snap = live.snapshot()
    assert snap["run"] == run + 1 and snap["start"] == "2025-07-14" and snap["hours"] == 48
    assert snap["config"]["mix"]["spot"] == 100 and snap["config"]["load"] == 60
    by_tier = {tier["key"]: tier for tier in snap["tiers"]}
    assert by_tier["spot"]["arrived"] == snap["fleet"]["arrived"] > 0
    assert by_tier["on_demand"]["arrived"] == 0
    assert sum(sum(site["history"]["dumped"]) for site in snap["sites"]) == pytest.approx(0, abs=0.1)
    assert snap["fleet"]["waiting"] > 0              # a warm July: spot waits for a house
    assert all(job["tier"] == "spot" for job in snap["queue"])
    for bad in ({"mix": {"spot": 50}, "load": 85, "start": "2025-01-01", "days": 2},
                {"mix": {"gold": 100}, "load": 85, "start": "2025-01-01", "days": 2},
                {"mix": {"spot": 100}, "load": 0, "start": "2025-01-01", "days": 2},
                {"mix": {"spot": 100}, "load": 85, "start": "2025-12-25", "days": 14},
                {"mix": {"spot": 100}, "load": 85}):
        with pytest.raises(ValueError):
            live.control("configure", bad)
    assert live.snapshot()["run"] == run + 1         # a refused request leaves the run alone


def test_http_endpoints(live):
    try:
        server = dashboard_server.make_server(live, 0)
    except OSError:
        pytest.skip("cannot open a local port here")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/", timeout=10).read().decode()
        assert "Fleet dashboard" in page
        state = json.load(urllib.request.urlopen(base + "/state", timeout=10))
        assert {"t", "sites", "queue", "fleet", "tiers", "money", "config"} <= set(state)
        request = urllib.request.Request(base + "/control", json.dumps({"action": "pause"}).encode(),
                                         {"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(request, timeout=10)) == {"ok": True}
        assert json.load(urllib.request.urlopen(base + "/state", timeout=10))["paused"]
        refused = urllib.request.Request(
            base + "/control", json.dumps({"action": "configure", "value": {"mix": {}}}).encode(),
            {"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(refused, timeout=10)
        assert error.value.code == 400 and "error" in json.load(error.value)
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/missing", timeout=10)
    finally:
        server.shutdown()
        server.server_close()
