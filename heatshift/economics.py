"""Per-house yearly balance and break-even computing price.

Business model assumed (all numbers in config/assumptions.json):
  * DigitalHeat owns the servers, pays the electricity, and sells
      - computing:  SLA jobs at price p   [CHF/server-h],  backfill (spot) at spot_ratio * p
      - heat:       to the homeowner at the heat-pump-equivalent value (electricity / COP 2.5)
  * break-even p* = (electricity + capex annuity + install annuity + opex - heat revenue)
                    / server-hours of useful computing sold            (case definition)
  * two-tier variant: SLA price needed if backfill only fetches spot_ratio x p
"""
from __future__ import annotations

import numpy as np

from .config import val
from .scheduler import ETA


def annuity(capex: float, years: float, rate: float) -> float:
    if rate <= 0:
        return capex / years
    return capex * rate / (1 - (1 + rate) ** -years)


def impact(compute_kwh: float, heat_used_kwh: float) -> dict:
    """Energy a conventional setup would need for the same computing + heat:
    data centre (compute x PUE) + heat pump (heat / COP).  DigitalHeat needs only compute_kwh."""
    pue, cop = val("impact.datacenter_pue"), val("energy_prices.heat_pump_cop")
    saved = compute_kwh * (pue - 1) + heat_used_kwh / cop
    return {"energy_saved_kwh": saved,
            "co2_saved_t": saved * val("impact.grid_co2_g_per_kwh") / 1e6,
            "conventional_kwh": compute_kwh * pue + heat_used_kwh / cop}


def house_balance(site, jobs_at_site: list, hours: int = 8760) -> dict:
    L = site.log
    n = site.servers
    elec_kwh = float(L["compute"].sum())                  # 1 kW server -> kWh = server-h
    sla_h = float(L["sla"].sum())
    bf_h = float(L["backfill"].sum())
    heat_used = float(L["heat_used"].sum())
    dump = float(L["dump"].sum())
    demand = float(site.demand[:hours].sum())
    p_el = getattr(site, "price", None) or val("energy_prices.electricity_chf_per_kwh")
    p_heat = p_el / val("energy_prices.heat_pump_cop")
    r = val("server.discount_rate")
    capex = annuity(n * val("server.capex_chf"), val("server.lifetime_years"), r)
    install = annuity(val("server.install_chf_per_house"), val("server.install_lifetime_years"), r) if n > 0 else 0.0
    opex = n * val("server.opex_chf_per_server_year")
    # a policy that dumps heat needs a dry cooler in the basement (capex + fan electricity)
    if dump > 1.0:
        install += annuity(val("server.dry_cooler_chf"), val("server.install_lifetime_years"), r)
        opex += dump * val("server.dry_cooler_fan_kwh_per_kwh") * p_el
    elec_cost = elec_kwh * p_el
    heat_rev = heat_used * p_heat
    spot = val("workload.spot_price_ratio")
    sold = sla_h + bf_h
    sold_eq = sla_h + spot * bf_h
    net_cost = elec_cost + capex + install + opex - heat_rev
    # case definition: one price per server-hour of useful computing
    breakeven = net_cost / sold if sold > 0 else float("inf")
    # two-tier variant: SLA price needed if backfill hours only fetch spot_ratio x that price
    breakeven_two_tier = net_cost / sold_eq if sold_eq > 0 else float("inf")
    marginal = (elec_cost - heat_rev) / sold if sold > 0 else float("inf")

    acc = [j for j in jobs_at_site if not j.rejected and j.deadline < hours]
    missed = sum(1 for j in acc if j.done_at is None or j.done_at > j.deadline)
    lo, hi = val("market.gpu_rental_low_chf_per_server_h"), val("market.gpu_rental_high_chf_per_server_h")
    mid = (lo + hi) / 2
    imp = impact(elec_kwh, heat_used)
    return {
        **imp,
        "electricity_price_chf_per_kwh": p_el,
        "servers": n,
        "heat_demand_kwh": demand,
        "server_heat_kwh": float(ETA * elec_kwh),
        "useful_heat_kwh": heat_used,
        "coverage": 1 - float(L["backup"].sum()) / demand if demand else 0.0,
        "dumped_heat_kwh": dump,
        "dumped_share": dump / (ETA * elec_kwh) if elec_kwh else 0.0,
        "backup_heat_kwh": float(L["backup"].sum()),
        "utilisation": elec_kwh / (n * hours) if n else 0.0,
        "sla_server_h": sla_h,
        "backfill_server_h": bf_h,
        "jobs_done": len(acc),
        "jobs_per_day": len(acc) / (hours / 24),
        "missed_deadlines": missed,
        "missed_pct": 100 * missed / len(acc) if acc else 0.0,
        "electricity_kwh": elec_kwh,
        "electricity_cost_chf": elec_cost,
        "heat_value_chf": heat_rev,
        "capex_annuity_chf": capex,
        "install_annuity_chf": install,
        "opex_chf": opex,
        "net_cost_before_compute_chf": net_cost,
        "breakeven_chf_per_server_h": breakeven,
        "breakeven_two_tier_sla_chf_per_server_h": breakeven_two_tier,
        "marginal_breakeven_chf_per_server_h": marginal,
        "market_low": lo, "market_high": hi,
        "compute_income_at_market_mid_chf": sold * mid,
        "profit_at_market_mid_chf": sold * mid - net_cost,
        "pays_off": bool(breakeven <= mid),
        "pays_off_verdict": ("pays off at low market prices" if breakeven <= lo else
                             "pays off at mid market prices" if breakeven <= mid else
                             "pays off only at premium prices" if breakeven <= hi else
                             "does NOT pay off"),
    }
