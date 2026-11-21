"""Invariants that must always hold. Run: make test"""
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heatshift.config import val  # noqa: E402
from heatshift.house import demand, export_reference_profile  # noqa: E402
from heatshift.sim import simulate  # noqa: E402
from heatshift.scheduler import ETA  # noqa: E402


def test_reference_house_matches_case():
    p = export_reference_profile()
    assert p["heating_days"] == 206
    assert abs(p["space_heat_kwh"] - 27000) < 1
    assert abs(p["hot_water_kwh"] - 12 * 365) < 1
    assert 8.5 < p["coldest_day_avg_kw"] < 11.5           # "about 10 kW on a cold day"


@pytest.fixture(scope="module")
def year():
    return simulate("heatshift", ref_servers=8, hours=24 * 60)


def test_energy_balance_every_site(year):
    """server heat + backup = demand + tank change + dump (per site, whole period)."""
    T = year["hours"]
    for s in year["sites"]:
        L = s.log
        lhs = ETA * L["compute"][:T].sum() + L["backup"][:T].sum()
        soc0 = s.tank_max * 0.5
        rhs = s.demand[:T].sum() + (L["soc"][T - 1] - soc0) + L["dump"][:T].sum()
        assert abs(lhs - rhs) < 1e-6 * max(1, lhs), s.key


def test_zero_dump_policy(year):
    assert sum(s.log["dump"].sum() for s in year["sites"]) < 1e-6


def test_never_exceeds_installed_servers(year):
    for s in year["sites"]:
        assert s.log["compute"].max() <= s.servers + 1e-9


def test_tank_within_limits(year):
    for s in year["sites"]:
        assert s.log["soc"].min() >= -1e-9 and s.log["soc"].max() <= s.tank_max + 1e-9


def test_worker_checkpoint_resume(tmp_path):
    """SIGTERM -> checkpoint -> restart on another 'server' continues without repeating work."""
    st = tmp_path / "job"
    cmd = [sys.executable, str(ROOT / "workers" / "embed_worker.py"), "--state", str(st), "--limit", "600"]
    p = subprocess.Popen(cmd + ["--server", "house-A"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in p.stdout:
        if json.loads(line)["event"] == "progress" and json.loads(line)["at"] >= 96:
            break
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=60)
    ck = json.loads((st / "checkpoint.json").read_text())
    assert 0 < ck["next"] < 600
    first = ck["next"]
    p2 = subprocess.run(cmd + ["--server", "house-B"], capture_output=True, text=True, timeout=300)
    events = [json.loads(l) for l in p2.stdout.splitlines()]
    assert events[1]["resumed"] and events[1]["at"] == first
    ck = json.loads((st / "checkpoint.json").read_text())
    assert ck["next"] == 600
    shards = sorted(st.glob("out_*.npy"))
    assert sum(np.load(f).shape[0] for f in shards) == 600          # no item processed twice
    assert [h["server"] for h in ck["history"]][:2] == ["house-A", "house-A"]
    assert ck["history"][-1]["server"] == "house-B"


def test_nothing_runs_during_outage(year):
    for s in year["sites"]:
        if s.down is not None:
            T = year["hours"]
            assert s.log["compute"][:T][s.down[:T]].sum() == 0, s.key


def test_holidays_reduce_demand():
    from heatshift.sites import HOLIDAYS
    base = demand("basel_ref")
    away = demand("basel_ref", absences=HOLIDAYS)
    assert away.demand_kwh.sum() < base.demand_kwh.sum()
    july = away.time.dt.strftime("%m-%d").between("07-05", "07-26")
    assert away.hot_water_kwh[july].sum() < 0.11 * base.hot_water_kwh[july].sum()


def test_regional_prices_loaded():
    from heatshift.sim import site_prices
    p = site_prices()
    assert abs(p["basel_ref"] - 0.3325) < 1e-6
    assert min(p.values()) < 0.25          # Zermatt / Davos are cheaper than Basel
