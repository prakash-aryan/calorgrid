"""The live dashboard server: its snapshot and its HTTP endpoints."""

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
    assert fleet["arrived"] == fleet["done"] + fleet["running"] + fleet["waiting"] + fleet["missed"]
    assert len(snap["sites"]) == 6
    for site in snap["sites"]:
        booked = sum(job["g"] for server in site["servers"] for job in server)
        assert booked <= site["gpus"] + 1e-9
        assert all(sum(job["g"] for job in server) <= 8 + 1e-9 for server in site["servers"])
        assert site["gpu"] <= booked + 1e-6            # a job's last hour may be partial
        assert all(len(values) == 21 for values in site["history"].values())
    priorities = [job["priority"] for job in snap["queue"]]
    assert priorities == sorted(priorities, reverse=True)


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
    for _ in range(100):
        live.step()
    assert live.snapshot()["finished"] and live.snapshot()["t"] == 48


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
        assert {"t", "sites", "queue", "fleet"} <= set(state)
        request = urllib.request.Request(base + "/control", json.dumps({"action": "pause"}).encode(),
                                         {"Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(request, timeout=10)) == {"ok": True}
        assert json.load(urllib.request.urlopen(base + "/state", timeout=10))["paused"]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/missing", timeout=10)
    finally:
        server.shutdown()
        server.server_close()
