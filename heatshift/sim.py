"""8,760-hour fleet simulation (reference house + DigitalHeat fleet)."""
from __future__ import annotations

import numpy as np

from .config import val
from .house import demand
from .jobs import JOB_TYPE, Job, generate_jobs
from .scheduler import BACKFILL, ETA, POLICIES, SiteState, admit, failover, proactive_migrate
from .sites import REFERENCE, SITES


def forecast_temps(actual: np.ndarray, seed: int) -> np.ndarray:
    """Day-ahead forecast = actual + AR(1) error (sd from assumptions, ~12 h correlation)."""
    sd = val("scheduler.forecast_error_sd_c")
    rng = np.random.default_rng(seed)
    phi = np.exp(-1 / 12)
    e = np.zeros(len(actual))
    for i in range(1, len(e)):
        e[i] = phi * e[i - 1] + np.sqrt(1 - phi ** 2) * sd * rng.standard_normal()
    return actual + e


def site_prices() -> dict[str, float]:
    """CHF/kWh per site from the ElCom fetch; falls back to the Basel price."""
    import json
    from .config import DERIVED
    base = val("energy_prices.electricity_chf_per_kwh")
    p = DERIVED / "elcom_sites_2026.json"
    if not p.exists():
        return {}
    return {k: v.get("rp_per_kwh", base * 100) / 100 for k, v in json.loads(p.read_text())["sites"].items()}


def outage_mask(n_hours: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m = np.zeros(n_hours, bool)
    dur = int(val("reliability.outage_hours"))
    for _ in range(int(val("reliability.outages_per_site_year"))):
        a = int(rng.integers(0, n_hours - dur))
        m[a:a + dur] = True
    return m


def build_sites(ref_servers: int, dump_allowed: bool | None = None, seed: int = 1,
                fleet: bool = True, outages: bool = True, ref_absences=None) -> list[SiteState]:
    dump = val("scheduler.dump_allowed") if dump_allowed is None else dump_allowed
    prices = site_prices()
    out = []
    for i, s in enumerate(SITES):
        if not fleet and s.key != REFERENCE:
            continue
        absn = ref_absences if s.key == REFERENCE else None
        d = demand(s.key, absences=absn)
        f = demand(s.key, temps=forecast_temps(d.temp_c.to_numpy(), seed + i), absences=absn)
        n = ref_servers if s.key == REFERENCE else s.servers
        out.append(SiteState(s.key, n, s.tank_kwh, d.demand_kwh.to_numpy(), f.demand_kwh.to_numpy(),
                             dump_allowed=dump, price=prices.get(s.key, val("energy_prices.electricity_chf_per_kwh")),
                             down=outage_mask(len(d), 1000 + i) if outages else None))
    return out


def heat_matched_server_hours(sites: list[SiteState]) -> float:
    return float(sum(np.minimum(s.servers, s.demand / ETA).sum() for s in sites))


def simulate(policy: str = "heatshift", ref_servers: int = 8, demand_factor: float | None = None,
             seed: int = 7, hours: int = 8760, fleet: bool = True, dump_allowed: bool | None = None,
             jobs: list[Job] | None = None, backfill: bool = True, outages: bool = True,
             ref_absences=None) -> dict:
    pol = POLICIES[policy]
    pol.backfill = backfill
    # baselines need a dry cooler (they dump); HeatShift runs zero-dump unless told otherwise
    sites = build_sites(ref_servers, dump_allowed=(True if policy == "always_on" else dump_allowed), fleet=fleet,
                        outages=outages, ref_absences=ref_absences)
    by_key = {s.key: s for s in sites}
    dfac = val("workload.demand_factor") if demand_factor is None else demand_factor
    sh_per_day = dfac * heat_matched_server_hours(sites) / 365
    if jobs is None:
        jobs = generate_jobs(sh_per_day, hours=hours, seed=seed)
    arrivals: dict[int, list[Job]] = {}
    for j in jobs:
        arrivals.setdefault(j.arrival, []).append(j)
    rng = np.random.default_rng(seed + 100)
    weights = np.array([s.servers for s in sites], float)
    weights /= weights.sum()
    T = min(hours, len(sites[0].demand))
    ref = by_key.get(REFERENCE)
    ref_kind = {k: np.zeros(T) for k in JOB_TYPE}
    rejected = migrations = failovers = 0

    for t in range(T):
        for s in sites:
            s._t = t
            s.refresh_index()
        for j in arrivals.get(t, []):
            if pol.admission:
                s = admit(sites, t, j)
                if s is None:
                    j.rejected = True
                    rejected += 1
                    continue
            else:
                s = sites[rng.choice(len(sites), p=weights)]   # static placement (may be offline)
            j.site = s.key
            s.add(j)
        if pol.migrates:
            failovers += failover(sites, t)
            migrations += proactive_migrate(sites, t)
        else:   # without a scheduler, atomic jobs on a failed site lose their progress
            for s in sites:
                if s.is_down(t):
                    for j in s.queue:
                        if not j.interruptible and j.started:
                            j.remaining, j.started = j.work, False
        for s in sites:
            s.log["budget"][t] = pol.budget(s, t)
            alloc = pol.allocate(s, t)
            total = 0.0
            by_id = {j.id: j for j in s.queue}
            bf = alloc.pop(BACKFILL, 0.0)
            s.log["backfill"][t] = bf
            for jid, a in alloc.items():
                j = by_id[jid]
                j.remaining -= a
                j.started = True
                total += a
                if s is ref:
                    ref_kind[j.kind][t] += a
                if j.remaining <= 1e-9:
                    j.done_at = t + 1
            s.log["sla"][t] = total
            total += bf
            s.queue = [j for j in s.queue if j.done_at is None]
            s.log["queue_work"][t] = sum(j.remaining for j in s.queue)
            s.apply_heat(t, total)

    return {"policy": policy, "ref_servers": ref_servers, "demand_factor": dfac,
            "server_hours_per_day": sh_per_day, "sites": sites, "jobs": jobs,
            "ref_kind": ref_kind, "rejected": rejected, "migrations": migrations, "failovers": failovers, "hours": T}
