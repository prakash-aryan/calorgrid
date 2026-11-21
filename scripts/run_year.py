"""Run the full-year experiments and write results/results.json (+ CSVs) for the dashboard.

  * policy comparison (always-on / thermostat / HeatShift / HeatShift+rescue), 8 servers in the reference house
  * servers-per-house sweep 1..16 for the reference house (HeatShift)
  * demand sweep (how much deadline work can the fleet sell?)
  * hourly series of the reference house for the dashboard (Jan / Jul days)

Usage: python scripts/run_year.py [--quick]
"""
from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from heatshift.config import DERIVED, RESULTS, flat_assumptions, val  # noqa: E402
from heatshift.economics import house_balance, impact  # noqa: E402
from heatshift.house import demand as house_demand, export_reference_profile  # noqa: E402
from heatshift.jobs import JOB_TYPES, alibaba_stats, output_per_kwh  # noqa: E402
from heatshift.scheduler import ETA, POLICIES  # noqa: E402
from heatshift.sim import simulate  # noqa: E402
from heatshift.sites import HOLIDAYS, REFERENCE, SITE_BY_KEY  # noqa: E402

DEFAULT_N = 8
SEED = 7


def fleet_kpis(r: dict) -> dict:
    sites, jobs, T = r["sites"], r["jobs"], r["hours"]
    js = [j for j in jobs if j.deadline < T]
    acc = [j for j in js if not j.rejected]
    missed = sum(1 for j in acc if j.done_at is None or j.done_at > j.deadline)
    comp = sum(s.log["compute"].sum() for s in sites)
    dump = sum(s.log["dump"].sum() for s in sites)
    used = sum(s.log["heat_used"].sum() for s in sites)
    dem = sum(s.demand[:T].sum() for s in sites)
    servers = sum(s.servers for s in sites)
    cost = sum((s.log["compute"].sum()) * s.price for s in sites)
    imp = impact(float(comp), float(used))
    return {
        **imp,
        "electricity_cost_chf": float(cost), "avg_price_chf_per_kwh": float(cost / comp) if comp else 0,
        "failovers": r.get("failovers", 0),
        "outage_hours": int(sum(s.down.sum() for s in sites if s.down is not None)),
        "sites": len(sites), "servers": servers,
        "jobs": len(js), "jobs_per_day": len(js) / (T / 24),
        "rejected_pct": 100 * r["rejected"] / len(js) if js else 0,
        "missed_pct": 100 * missed / len(acc) if acc else 0,
        "migrations": r["migrations"],
        "migrated_jobs_pct": 100 * sum(1 for j in acc if j.migrations) / len(acc) if acc else 0,
        "sla_server_h": float(sum(s.log["sla"].sum() for s in sites)),
        "backfill_server_h": float(sum(s.log["backfill"].sum() for s in sites)),
        "utilisation": float(comp / (servers * T)),
        "useful_heat_mwh": float(used / 1000), "dumped_heat_mwh": float(dump / 1000),
        "dumped_share": float(dump / (ETA * comp)) if comp else 0,
        "coverage": float(1 - sum(s.log["backup"].sum() for s in sites) / dem),
        "server_hours_per_day_demand": r["server_hours_per_day"],
    }


def run_case(args: dict) -> dict:
    t0 = time.time()
    if "price_weight" in args:
        import heatshift.scheduler as sch
        sch.PRICE_WEIGHT = args["price_weight"]
    r = simulate(policy=args["policy"], ref_servers=args["n"], demand_factor=args.get("demand"),
                 seed=SEED, backfill=args.get("backfill", True), outages=args.get("outages", True),
                 ref_absences=HOLIDAYS if args.get("holidays") else None)
    ref = next(s for s in r["sites"] if s.key == REFERENCE)
    ref_jobs = [j for j in r["jobs"] if j.site == REFERENCE]
    out = {"args": args, "fleet": fleet_kpis(r), "house": house_balance(ref, ref_jobs),
           "runtime_s": time.time() - t0}
    if args.get("hourly"):
        L = ref.log
        out["hourly"] = {k: np.round(v, 3).tolist() for k, v in L.items()}
        out["hourly"]["demand"] = np.round(ref.demand, 3).tolist()
        out["hourly"]["forecast"] = np.round(ref.forecast, 3).tolist()
        out["hourly_kind"] = {k: np.round(v, 3).tolist() for k, v in r["ref_kind"].items()}
        out["sites"] = []
        for s in r["sites"]:
            meta = SITE_BY_KEY[s.key]
            eco = house_balance(s, [j for j in r["jobs"] if j.site == s.key])
            out["sites"].append({
                "breakeven": eco["breakeven_chf_per_server_h"], "profit_mid": eco["profit_at_market_mid_chf"],
                "co2_saved_t": eco["co2_saved_t"], "electricity_cost_chf": eco["electricity_cost_chf"],
                "key": s.key, "name": meta.name, "region": meta.region, "kind": meta.kind,
                "lat": meta.lat, "lon": meta.lon, "servers": s.servers, "price_rp": 100 * s.price,
                "outage_hours": int(s.down.sum()) if s.down is not None else 0,
                "holidays": bool(meta.absences), "tank_kwh": s.tank_max,
                "monthly_demand": pd.Series(s.demand).groupby(np.arange(len(s.demand)) // 730).sum().round(0).tolist(),
                "demand_mwh": float(s.demand.sum() / 1000),
                "coverage": float(1 - s.log["backup"].sum() / s.demand.sum()),
                "utilisation": float(s.log["compute"].sum() / (s.servers * len(s.demand))),
                "sla_server_h": float(s.log["sla"].sum()), "backfill_server_h": float(s.log["backfill"].sum()),
                "dump_kwh": float(s.log["dump"].sum()),
                "monthly_compute": pd.Series(s.log["compute"]).groupby(np.arange(len(s.demand)) // 730).sum().round(0).tolist(),
            })
        # useful computing delivered by the reference house, per job type
        opk = output_per_kwh()
        out["useful_output"] = []
        for jt in JOB_TYPES:
            kwh = float(np.sum(r["ref_kind"][jt.key]))
            out["useful_output"].append({"key": jt.key, "label": jt.label, "server_h": kwh,
                                         "unit": jt.unit, "output": kwh * opk[jt.key] if jt.key != "eval" else
                                         sum(1 for j in ref_jobs if j.kind == "eval" and j.done_at)})
    return out


def main(quick: bool = False):
    RESULTS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    profile = export_reference_profile()
    cases = []
    for p in POLICIES:
        cases.append({"group": "policy", "policy": p, "n": DEFAULT_N, "hourly": p == "heatshift"})
    cases.append({"group": "no_backfill", "policy": "heatshift", "n": DEFAULT_N, "backfill": False})
    cases.append({"group": "holidays", "policy": "heatshift", "n": DEFAULT_N, "holidays": True})
    cases.append({"group": "no_outages", "policy": "heatshift", "n": DEFAULT_N, "outages": False})
    for n in ([2, 4, 6, 8, 10, 12, 14] if quick else range(1, 17)):
        cases.append({"group": "servers", "policy": "heatshift", "n": n})
    for d in ([0.4, 0.8, 1.2] if quick else [0.3, 0.45, 0.6, 0.8, 1.0, 1.2]):
        cases.append({"group": "demand", "policy": "heatshift", "n": DEFAULT_N, "demand": d})
        if d <= 0.8:   # overloaded thermostat runs build huge queues (slow); the trend is clear by 0.8
            cases.append({"group": "demand_thermo", "policy": "thermostat", "n": DEFAULT_N, "demand": d})
    with Pool() as pool:
        res = pool.map(run_case, cases)
    main_run = next(r for r in res if r["args"]["group"] == "policy" and r["args"]["policy"] == "heatshift")

    d = house_demand(REFERENCE)
    time_idx = d.time.dt.strftime("%Y-%m-%dT%H:%M").tolist()
    daily_t = d.groupby(d.time.dt.date).temp_c.mean()
    jan = daily_t[[x.month == 1 for x in daily_t.index]].idxmin()
    jul = daily_t[[x.month == 7 for x in daily_t.index]].idxmax()
    # shoulder-season day where the forecast + tank matter most
    apr = daily_t[[x.month == 4 for x in daily_t.index]]
    apr_day = (apr - 10).abs().idxmin()

    H = main_run["hourly"]
    month = d.time.dt.month.to_numpy()
    monthly = []
    for m in range(1, 13):
        k = month == m
        monthly.append({
            "month": m,
            "space_heat_kwh": float(d.space_heat_kwh[k].sum()), "hot_water_kwh": float(d.hot_water_kwh[k].sum()),
            "server_heat_used_kwh": float(np.array(H["heat_used"])[k].sum()),
            "backup_kwh": float(np.array(H["backup"])[k].sum()),
            "dump_kwh": float(np.array(H["dump"])[k].sum()),
            "sla_server_h": float(np.array(H["sla"])[k].sum()),
            "backfill_server_h": float(np.array(H["backfill"])[k].sum()),
            "temp_mean_c": float(d.temp_c[k].mean()),
        })

    out = {
        "meta": {"generated": time.strftime("%Y-%m-%d %H:%M"), "seed": SEED, "default_servers": DEFAULT_N,
                 "runtime_s": round(time.time() - t0, 1), "cases": len(cases)},
        "assumptions": flat_assumptions(),
        "reference_profile": profile,
        "alibaba": alibaba_stats(),
        "job_types": [{"key": j.key, "label": j.label, "share": j.share, "slack_h": j.slack_h,
                       "interruptible": j.interruptible, "unit": j.unit, "per_kwh": output_per_kwh()[j.key],
                       "source": j.per_kwh_key} for j in JOB_TYPES],
        "policies": [{"policy": r["args"]["policy"], "label": POLICIES[r["args"]["policy"]].label,
                      "fleet": r["fleet"], "house": r["house"]} for r in res if r["args"]["group"] == "policy"],
        "no_backfill": next(r for r in res if r["args"]["group"] == "no_backfill")["house"],
        "holidays": next(r for r in res if r["args"]["group"] == "holidays")["house"],
        "no_outages": next(r for r in res if r["args"]["group"] == "no_outages")["fleet"],
        "heat_grid": json.loads((DERIVED / "heat_grid_2025.json").read_text())["points"] if (DERIVED / "heat_grid_2025.json").exists() else [],
        "sweep_servers": [{"n": r["args"]["n"], **r["house"]} for r in res if r["args"]["group"] == "servers"],
        "sweep_demand": [{"demand": r["args"]["demand"], "policy": r["args"]["policy"], **r["fleet"]}
                         for r in res if r["args"]["group"] in ("demand", "demand_thermo")],
        "house": main_run["house"],
        "fleet": main_run["fleet"],
        "sites": main_run["sites"],
        "useful_output": main_run["useful_output"],
        "monthly": monthly,
        "days": {"january": str(jan), "july": str(jul), "april": str(apr_day)},
        "hourly": {"time": time_idx, "temp_c": np.round(d.temp_c, 2).tolist(), **H,
                   "kind": main_run["hourly_kind"]},
    }
    (RESULTS / "results.json").write_text(json.dumps(out, default=float))
    pd.DataFrame(out["sweep_servers"]).to_csv(RESULTS / "sweep_servers.csv", index=False)
    pd.DataFrame([{"policy": p["policy"], **{f"house_{k}": v for k, v in p["house"].items()},
                   **{f"fleet_{k}": v for k, v in p["fleet"].items()}} for p in out["policies"]]
                 ).to_csv(RESULTS / "policies.csv", index=False)
    pd.DataFrame({"time": time_idx, **{k: v for k, v in H.items()}}).to_csv(RESULTS / "reference_house_hourly.csv", index=False)

    h = out["house"]
    print(f"\n=== Reference house Basel, {DEFAULT_N} x 1 kW servers, HeatShift, 2025 ===")
    print(f"heat demand          {h['heat_demand_kwh']:9,.0f} kWh  (space {profile['space_heat_kwh']:,.0f} + hot water {profile['hot_water_kwh']:,.0f})")
    print(f"covered by servers   {100*h['coverage']:8.1f} %   dumped {h['dumped_heat_kwh']:,.0f} kWh   backup {h['backup_heat_kwh']:,.0f} kWh")
    print(f"utilisation          {100*h['utilisation']:8.1f} %   SLA {h['sla_server_h']:,.0f} h + backfill {h['backfill_server_h']:,.0f} h")
    print(f"jobs/day {h['jobs_per_day']:.1f}   missed deadlines {h['missed_pct']:.2f} %")
    print(f"electricity          {h['electricity_cost_chf']:9,.0f} CHF   heat value {h['heat_value_chf']:,.0f} CHF   capex+install+opex {h['capex_annuity_chf']+h['install_annuity_chf']+h['opex_chf']:,.0f} CHF")
    print(f"BREAK-EVEN           {h['breakeven_chf_per_server_h']:8.2f} CHF/server-h  (market {h['market_low']}-{h['market_high']}) -> {h['pays_off_verdict']}")
    print("\npolicy comparison (fleet):")
    for p in out["policies"]:
        f, hh = p["fleet"], p["house"]
        print(f"  {p['label']:32s} missed {f['missed_pct']:6.2f}%  rejected {f['rejected_pct']:5.1f}%  dumped {f['dumped_heat_mwh']:7.1f} MWh  "
              f"| house cover {100*hh['coverage']:5.1f}%  break-even {hh['breakeven_chf_per_server_h']:.2f}")
    print(f"\nwrote {RESULTS/'results.json'} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
