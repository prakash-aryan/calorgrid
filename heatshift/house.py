"""Hourly heat demand per site (space heating + hot water).

Space heating uses the degree-hour method:  Q_sh(t) = UA * max(0, T_base - T(t)) on heating
days only (daily mean < 12 degC). UA of the reference house is *calibrated* so that the Basel
2025 space-heating total equals the case's 27,000 kWh. Other sites scale UA.
Hot water: 12 kWh/day with a fixed intra-day shape (morning and evening peaks).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from .config import DERIVED, WEATHER, val
from .sites import REFERENCE, SITE_BY_KEY

# share of daily hot water per hour (sums to 1): morning shower + evening peak
HW_SHAPE = np.array([0.5, 0.3, 0.3, 0.3, 0.5, 1.5, 6.0, 9.0, 8.0, 5.0, 3.5, 3.5,
                     4.0, 3.5, 3.0, 3.0, 3.5, 5.0, 7.5, 8.5, 7.5, 5.5, 3.0, 1.5])
HW_SHAPE = HW_SHAPE / HW_SHAPE.sum()
# pools / hotels: flatter, long opening hours
HW_SHAPE_FLAT = np.array([2, 2, 2, 2, 2, 3, 5, 6, 6, 5, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 5, 4, 3, 2], float)
HW_SHAPE_FLAT /= HW_SHAPE_FLAT.sum()


def load_weather(site_key: str) -> pd.DataFrame:
    df = pd.read_csv(WEATHER / f"{site_key}.csv", parse_dates=["time"])
    return df.iloc[:8760].reset_index(drop=True)


def _heating_mask(df: pd.DataFrame) -> np.ndarray:
    thr = val("reference_house.heating_day_threshold_c")
    daily = df.groupby(df.time.dt.date)["temp_c"].transform("mean")
    return (daily < thr).to_numpy()


@lru_cache(maxsize=1)
def reference_ua() -> float:
    """kW/K so that Basel 2025 heating-day degree-hours * UA = 27,000 kWh."""
    df = load_weather(REFERENCE)
    tb = val("reference_house.indoor_base_temp_c")
    dh = np.where(_heating_mask(df), np.clip(tb - df.temp_c.to_numpy(), 0, None), 0).sum()
    return val("reference_house.space_heating_kwh_year") / dh


def absence_mask(times: pd.Series, absences) -> np.ndarray:
    md = times.dt.strftime("%m-%d")
    m = np.zeros(len(times), bool)
    for a, b in absences or ():
        m |= ((md >= a) & (md <= b)).to_numpy()
    return m


def demand(site_key: str, temps: np.ndarray | None = None, absences=None) -> pd.DataFrame:
    """Hourly demand for one site. `temps` can override (e.g. a noisy forecast).
    `absences` overrides the site's planned holidays: during an absence the heating runs in
    setback mode (16 degC instead of 20 degC) and hot water drops to 10 % (legionella cycles only)."""
    s = SITE_BY_KEY[site_key]
    df = load_weather(site_key)
    if temps is not None:
        df = df.assign(temp_c=temps)
    tb = val("reference_house.indoor_base_temp_c")
    away = absence_mask(df.time, s.absences if absences is None else absences)
    base = np.where(away, val("reference_house.setback_temp_c"), tb)
    heat_day = _heating_mask(df)
    sh = np.where(heat_day, reference_ua() * s.ua_factor * np.clip(base - df.temp_c.to_numpy(), 0, None), 0.0)
    shape = HW_SHAPE_FLAT if s.kind in ("pool", "hotel") else HW_SHAPE
    hw = np.tile(shape, len(df) // 24 + 1)[: len(df)] * s.hw_kwh_day
    hw = np.where(away, hw * val("reference_house.away_hot_water_share"), hw)
    return pd.DataFrame({"time": df.time, "temp_c": df.temp_c, "heating_day": heat_day, "away": away,
                         "space_heat_kwh": sh, "hot_water_kwh": hw, "demand_kwh": sh + hw})


def export_reference_profile() -> dict:
    d = demand(REFERENCE)
    DERIVED.mkdir(parents=True, exist_ok=True)
    d.to_csv(DERIVED / "reference_house_profile_2025.csv", index=False)
    days = d.groupby(d.time.dt.date).agg(t=("temp_c", "mean"), q=("demand_kwh", "sum"))
    summary = {
        "ua_kw_per_k": reference_ua(),
        "heating_days": int(d.groupby(d.time.dt.date)["heating_day"].first().sum()),
        "space_heat_kwh": float(d.space_heat_kwh.sum()),
        "hot_water_kwh": float(d.hot_water_kwh.sum()),
        "total_kwh": float(d.demand_kwh.sum()),
        "peak_hour_kw": float(d.demand_kwh.max()),
        "coldest_day": str(days.t.idxmin()), "coldest_day_mean_c": float(days.t.min()),
        "coldest_day_avg_kw": float(days.q.max() / 24),
    }
    return summary
