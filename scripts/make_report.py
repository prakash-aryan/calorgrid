"""Write results/REPORT.md from results/results.json (numbers are never typed by hand)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from heatshift.config import DERIVED, RESULTS  # noqa: E402


def f(x, d=0):
    return f"{x:,.{d}f}".replace(",", "'")


def main():
    R = json.loads((RESULTS / "results.json").read_text())
    h, P, fl = R["house"], R["reference_profile"], R["fleet"]
    meas = json.loads((DERIVED / "measured_jobs.json").read_text()) if (DERIVED / "measured_jobs.json").exists() else None
    L = []
    L.append(f"# HeatShift results (generated {R['meta']['generated']}, seed {R['meta']['seed']})\n")
    L.append(f"## Balance per house and year: reference house Basel 2025, {h['servers']} × 1 kW servers, HeatShift\n")
    L.append("| Metric | Value |\n|---|---|")
    rows = [
        ("Servers per house", h["servers"]),
        ("Heat demand (space + hot water)", f"{f(h['heat_demand_kwh'])} kWh ({f(P['space_heat_kwh'])} + {f(P['hot_water_kwh'])})"),
        ("Share of heat demand covered by servers", f"{100*h['coverage']:.1f} %"),
        ("Heat dumped", f"{f(h['dumped_heat_kwh'])} kWh"),
        ("Backup heat (heat pump)", f"{f(h['backup_heat_kwh'])} kWh"),
        ("Utilisation", f"{100*h['utilisation']:.1f} % ({f(h['sla_server_h'])} SLA h + {f(h['backfill_server_h'])} spot h)"),
        ("Deadline jobs per day (house)", f"{h['jobs_per_day']:.1f}"),
        ("Missed deadlines", f"{h['missed_pct']:.2f} %"),
        ("Electricity", f"{f(h['electricity_kwh'])} kWh → CHF {f(h['electricity_cost_chf'])}"),
        ("Heat value (electricity price ÷ 2.5)", f"CHF {f(h['heat_value_chf'])}"),
        ("Capex + install annuity, opex", f"CHF {f(h['capex_annuity_chf'] + h['install_annuity_chf'])} + {f(h['opex_chf'])}"),
        ("Computing income at mid-market", f"CHF {f(h['compute_income_at_market_mid_chf'])}"),
        ("**Break-even computing price**", f"**CHF {h['breakeven_chf_per_server_h']:.2f} per server-hour** (market {h['market_low']:.2f}–{h['market_high']:.2f}) → {h['pays_off_verdict']}"),
        ("Break-even, electricity only", f"CHF {h['marginal_breakeven_chf_per_server_h']:.2f} per server-hour"),
    ]
    L += [f"| {a} | {b} |" for a, b in rows]
    L.append("\n## Scheduler comparison (whole fleet, same jobs and weather)\n")
    L.append("| Policy | Missed deadlines | Rejected | Heat dumped | Fleet utilisation | House coverage | House break-even |\n|---|---|---|---|---|---|---|")
    for p in R["policies"]:
        L.append(f"| {p['label']} | {p['fleet']['missed_pct']:.2f} % | {p['fleet']['rejected_pct']:.1f} % | "
                 f"{p['fleet']['dumped_heat_mwh']:.1f} MWh | {100*p['fleet']['utilisation']:.0f} % | "
                 f"{100*p['house']['coverage']:.1f} % | CHF {p['house']['breakeven_chf_per_server_h']:.2f} |")
    L.append("\n## Servers per house (HeatShift)\n")
    L.append("| Servers | Coverage | Utilisation | Missed | Break-even CHF/h | Profit @ mid-market CHF/yr |\n|---|---|---|---|---|---|")
    for s in R["sweep_servers"]:
        L.append(f"| {s['n']} | {100*s['coverage']:.0f} % | {100*s['utilisation']:.0f} % | {s['missed_pct']:.2f} % | "
                 f"{s['breakeven_chf_per_server_h']:.2f} | {f(s['profit_at_market_mid_chf'])} |")
    L.append("\n## Demand sensitivity (fleet)\n")
    L.append("| SLA demand (× heat-matched capacity) | Policy | Rejected | Missed | Utilisation |\n|---|---|---|---|---|")
    for s in R["sweep_demand"]:
        L.append(f"| {s['demand']} | {s['policy']} | {s['rejected_pct']:.1f} % | {s['missed_pct']:.2f} % | {100*s['utilisation']:.0f} % |")
    L.append(f"\n## Fleet\n\n{fl['sites']} sites, {fl['servers']} servers, {f(fl['jobs_per_day'])} deadline jobs/day, "
             f"{fl['migrated_jobs_pct']:.1f} % migrated, {fl['rejected_pct']:.1f} % rejected, {fl['missed_pct']:.2f} % missed, "
             f"{f(fl['useful_heat_mwh'])} MWh useful heat, {fl['dumped_heat_mwh']:.1f} MWh dumped.\n")
    L.append("## Useful computing delivered by the reference house\n\n| Job type | Server-h | Output |\n|---|---|---|")
    for o in R["useful_output"]:
        L.append(f"| {o['label']} | {f(o['server_h'])} | {f(o['output'], 1)} {o['unit']} |")
    if meas:
        L.append(f"\n## Measured on this machine ({meas['machine']}, idle {meas['idle_w']:.1f} W)\n")
        L.append("| Job | Items/s | W above idle | J/item |\n|---|---|---|---|")
        for k, lab in [("image_tag", "Image tagging, ResNet-50, Imagenette"), ("embed", "Embeddings, MiniLM-L6, SQuAD passages")]:
            m = meas[k]
            L.append(f"| {lab} | {m['items_per_s']:.1f} | {m['incremental_w']:.1f} | {m['j_per_item_incremental']:.3f} |")
    (RESULTS / "REPORT.md").write_text("\n".join(L) + "\n")
    print(f"wrote {RESULTS/'REPORT.md'}")


if __name__ == "__main__":
    main()
