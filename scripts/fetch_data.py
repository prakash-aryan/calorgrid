"""Fetch and derive every external input. Idempotent; cached files are reused.

  1. Open-Meteo 2025 hourly temperature for each fleet site     -> data/weather/<site>.csv
  2. ElCom 2026 tariff, IWB Basel, H4 (live API check)          -> data/derived/elcom_2026_iwb_h4.json
  3. MLPerf Inference v6.1 summary.xlsx -> long table + ref server -> data/derived/mlperf_*.{csv,json}
  4. Alibaba PAI GPU trace 2020 -> job size / arrival statistics   -> data/derived/alibaba_*.{csv,json}

Usage: python scripts/fetch_data.py [--force]
"""
from __future__ import annotations

import json
import subprocess
import time
import sys
import tarfile
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from heatshift.config import DATA, DERIVED, ROOT, WEATHER, val  # noqa: E402
from heatshift.sites import SITES  # noqa: E402

FORCE = "--force" in sys.argv
UA = {"User-Agent": "heatshift-hackathon/1.0"}


def get(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


# --------------------------------------------------------------------------- 1
def fetch_weather() -> None:
    WEATHER.mkdir(parents=True, exist_ok=True)
    year = val("reference_house.weather_year")
    for s in SITES:
        out = WEATHER / f"{s.key}.csv"
        if out.exists() and not FORCE:
            continue
        url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={s.lat}&longitude={s.lon}&start_date={year}-01-01&end_date={year}-12-31"
            "&hourly=temperature_2m&timezone=Europe%2FZurich"
        )
        d = json.loads(get(url))
        df = pd.DataFrame({"time": d["hourly"]["time"], "temp_c": d["hourly"]["temperature_2m"]})
        df["temp_c"] = df["temp_c"].interpolate().bfill().ffill()
        df.to_csv(out, index=False)
        print(f"  weather {s.key:14s} {len(df)} h  elev {d.get('elevation')} m  mean {df.temp_c.mean():.1f} C")


# --------------------------------------------------------------------------- 2
def fetch_elcom() -> None:
    out = DERIVED / "elcom_2026_iwb_h4.json"
    if out.exists() and not FORCE:
        return
    q = ('query{observations(locale:"de",filters:{period:["2026"],municipality:["2701"],category:["H4"]},'
         'observationKind:Municipality){... on OperatorObservation{period municipality operatorLabel category '
         'value(priceComponent:total)}}}')
    try:
        r = json.loads(get("https://www.strompreis.elcom.admin.ch/api/graphql",
                           json.dumps({"query": q}).encode(), {"content-type": "application/json"}))
        obs = r["data"]["observations"][0]
        rec = {**obs, "unit": "Rp./kWh", "source": "https://www.strompreis.elcom.admin.ch (GraphQL API)"}
    except Exception as e:  # offline: keep the documented value
        rec = {"value": val("energy_prices.electricity_chf_per_kwh") * 100, "unit": "Rp./kWh",
               "source": f"assumptions.json (API unreachable: {e})"}
    out.write_text(json.dumps(rec, indent=2))
    print(f"  ElCom 2026 IWB H4 total = {rec['value']} Rp/kWh")


def elcom_price(bfs: str, year: str = "2026") -> dict | None:
    q = ('query{observations(locale:"de",filters:{period:["%s"],municipality:["%s"],category:["H4"]},'
         'observationKind:Municipality){... on OperatorObservation{period municipality operator operatorLabel '
         'category value(priceComponent:total)}}}') % (year, bfs)
    r = json.loads(get("https://www.strompreis.elcom.admin.ch/api/graphql",
                       json.dumps({"query": q}).encode(), {"content-type": "application/json"}))
    obs = r["data"]["observations"]
    if not obs:
        return None
    # several operators/tariffs in one municipality -> take the mean (documented)
    return {"operator": obs[0]["operatorLabel"], "rp_per_kwh": sum(o["value"] for o in obs) / len(obs),
            "n_tariffs": len(obs)}


def fetch_site_prices() -> None:
    out = DERIVED / "elcom_sites_2026.json"
    if out.exists() and not FORCE:
        return
    res = {}
    for s in SITES:
        try:
            res[s.key] = {"bfs": s.bfs, **(elcom_price(s.bfs) or {})}
        except Exception as e:
            res[s.key] = {"bfs": s.bfs, "error": str(e)}
        print(f"  {s.key:14s} BFS {s.bfs:5s} {res[s.key].get('rp_per_kwh', float('nan')):6.2f} Rp/kWh  {res[s.key].get('operator', '')}")
    out.write_text(json.dumps({"source": "ElCom strompreis.elcom.admin.ch GraphQL, 2026, H4, total", "sites": res}, indent=2))


def _inside(lon, lat, rings) -> bool:
    ins = False
    for ring in rings:
        xs, ys = ring[:, 0], ring[:, 1]
        j = np.roll(np.arange(len(xs)), 1)
        cond = ((ys > lat) != (ys[j] > lat)) & (lon < (xs[j] - xs) * (lat - ys) / (ys[j] - ys + 1e-12) + xs)
        ins ^= bool(np.count_nonzero(cond) % 2)
    return ins


def fetch_heat_grid(step: float = 0.18) -> None:
    """Score a grid over Switzerland with real 2025 weather: where is there heat demand in summer?"""
    out = DERIVED / "heat_grid_2025.json"
    if out.exists() and not FORCE:
        return
    geo = json.loads((DATA / "geo" / "cantons.geojson").read_text())
    cantons = []
    for f in geo["features"]:
        polys = f["geometry"]["coordinates"] if f["geometry"]["type"] == "MultiPolygon" else [f["geometry"]["coordinates"]]
        cantons.append((f["properties"]["name"], [np.array(p[0]) for p in polys]))
    pts = []
    for lat in np.arange(45.85, 47.8, step):
        for lon in np.arange(5.98, 10.5, step * 1.45):
            for name, rings in cantons:
                if _inside(lon, lat, rings):
                    pts.append((round(lat, 3), round(lon, 3), name))
                    break
    print(f"  heat grid: {len(pts)} points inside Switzerland")
    thr = val("reference_house.heating_day_threshold_c")
    year = val("reference_house.weather_year")
    res = []
    for i in range(0, len(pts), 50):
        chunk = pts[i:i + 50]
        url = ("https://archive-api.open-meteo.com/v1/archive?latitude=" + ",".join(str(p[0]) for p in chunk)
               + "&longitude=" + ",".join(str(p[1]) for p in chunk)
               + f"&start_date={year}-01-01&end_date={year}-12-31&daily=temperature_2m_mean&timezone=Europe%2FZurich")
        d = json.loads(get(url))
        d = d if isinstance(d, list) else [d]
        for (lat, lon, canton), loc in zip(chunk, d):
            t = np.array(loc["daily"]["temperature_2m_mean"], float)
            months = np.array([int(x[5:7]) for x in loc["daily"]["time"]])
            heat = t < thr
            summer = (months >= 6) & (months <= 8)
            res.append({"lat": lat, "lon": lon, "canton": canton, "elev": loc.get("elevation"),
                        "mean_c": float(np.nanmean(t)), "heating_days": int(heat.sum()),
                        "summer_heating_days": int((heat & summer).sum()),
                        "hdd": float(np.clip(20 - t, 0, None)[heat].sum())})
        time.sleep(1.0)
    out.write_text(json.dumps({"source": f"Open-Meteo archive {year}, daily mean 2 m temperature; heating day < {thr} degC",
                               "points": res}))
    print(f"  heat grid scored: {len(res)} points")


# --------------------------------------------------------------------------- 3
def derive_mlperf() -> None:
    xlsx = ROOT / "summary.xlsx"
    if not xlsx.exists():
        print("  (summary.xlsx missing - skipping MLPerf)")
        return
    rows = []
    for sheet in pd.ExcelFile(xlsx).sheet_names:
        raw = pd.read_excel(xlsx, sheet_name=sheet, header=None)
        # header rows: 1 model, 2 scenario, 3 units, 4 column names
        lab_col = raw.iloc[1].tolist().index("Model")
        first = lab_col + 1
        model = raw.iloc[1, first:].ffill().tolist()
        scen = raw.iloc[2, first:].tolist()
        unit = raw.iloc[3, first:].tolist()
        names = raw.iloc[4, :first].tolist()
        meta = {}
        for i in range(5, len(raw)):
            r = raw.iloc[i]
            if not pd.isna(r.iloc[0]):   # continuation rows (2nd scenario/accuracy line) inherit the system
                meta = {str(names[j]): r.iloc[j] for j in range(first) if not pd.isna(names[j])}
            if not meta:
                continue
            for k, v in enumerate(r.iloc[first:].tolist()):
                if isinstance(v, (int, float)) and not pd.isna(v):
                    rows.append({"sheet": sheet, "id": meta.get("ID"), "system": meta.get("System"),
                                 "accelerator": meta.get("Accelerator"), "n_acc": meta.get("Total Accelerators"),
                                 "model": model[k], "scenario": scen[k], "unit": unit[k], "result": v})
    df = pd.DataFrame(rows)
    df.to_csv(DERIVED / "mlperf_v61_long.csv", index=False)

    def pick(rid, mdl, scn):
        m = df[(df.id == rid) & (df.model == mdl) & (df.scenario == scn)]
        return float(m.result.iloc[-1]) if len(m) else None

    ref = {
        "server": val("server.reference_hardware"),
        "assumed_power_kw": val("server.electric_kw"),
        "note": "MLPerf summary has no power column; system power assumed = 4x200 W GPU TDP + ~200 W host = 1 kW (DigitalHeat 1 kW server).",
        "benchmarks": {
            "whisper_samples_per_s": {"value": pick("6.1-0056", "whisper", "Offline"), "id": "6.1-0056",
                                       "system": "HPE DL345 Gen12, 4x RTX PRO 4500, TensorRT (edge/closed)"},
            "llama31_8b_tokens_per_s": {"value": pick("6.1-0114", "llama3.1-8b-edge", "Offline"), "id": "6.1-0114",
                                         "system": "HPE DL345 Gen12, 4x RTX PRO 4500, TensorRT (edge/open)"},
            "yolo_images_per_s_1gpu": {"value": pick("6.1-0054", "yolo-95", "Offline"), "id": "6.1-0054",
                                        "system": "HPE DL345 Gen12, 1x RTX PRO 4500, TensorRT (edge/closed)"},
        },
    }
    b = ref["benchmarks"]
    kwh = ref["assumed_power_kw"]
    if b["whisper_samples_per_s"]["value"]:
        b["whisper_samples_per_kwh"] = b["whisper_samples_per_s"]["value"] * 3600 / kwh
    if b["llama31_8b_tokens_per_s"]["value"]:
        b["llama31_8b_tokens_per_kwh"] = b["llama31_8b_tokens_per_s"]["value"] * 3600 / kwh
    if b["yolo_images_per_s_1gpu"]["value"]:
        # linear scaling 1 -> 4 GPUs (same chassis family)
        b["yolo_images_per_kwh"] = 4 * b["yolo_images_per_s_1gpu"]["value"] * 3600 / kwh
    (DERIVED / "mlperf_reference_server.json").write_text(json.dumps(ref, indent=2, default=float))
    print(f"  MLPerf: {len(df)} results, reference server -> {json.dumps({k: round(v['value'] if isinstance(v, dict) else v, 1) for k, v in b.items()})}")


# --------------------------------------------------------------------------- 4
def derive_alibaba() -> None:
    out = DERIVED / "alibaba_job_stats.json"
    if out.exists() and not FORCE:
        return
    raw = DATA / "raw" / "alibaba"
    csv = raw / "pai_task_table.csv"
    if not csv.exists():
        raw.mkdir(parents=True, exist_ok=True)
        tgz = raw / "pai_task_table.tar.gz"
        print("  downloading Alibaba pai_task_table (35 MB)...")
        tgz.write_bytes(get("https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2020GPUTraces/pai_task_table.tar.gz"))
        with tarfile.open(tgz) as t:
            t.extractall(raw)
    cols = ["job_name", "task_name", "inst_num", "status", "start_time", "end_time",
            "plan_cpu", "plan_mem", "plan_gpu", "gpu_type"]
    df = pd.read_csv(csv, header=None, names=cols)
    df = df[(df.plan_gpu > 0) & (df.status == "Terminated") & df.start_time.notna() & df.end_time.notna()]
    df = df[df.end_time > df.start_time]
    df["dur_h"] = (df.end_time - df.start_time) / 3600
    df["gpus"] = df.inst_num * df.plan_gpu / 100
    df["gpu_h"] = df.dur_h * df.gpus
    df = df[(df.dur_h > 1 / 60) & (df.dur_h < 72)]           # drop sub-minute noise and >3 day outliers
    # trace epoch is UTC+8 (Beijing) per the trace analysis notebook
    df["hour"] = ((df.start_time / 3600 + 8) % 24).astype(int)
    day = (df.start_time // 86400)
    n_days = int(day.max() - day.min() + 1)
    total_gpus = 6500
    hourly = df.groupby("hour").size()
    hourly = (hourly / hourly.sum()).reindex(range(24), fill_value=0)
    stats = {
        "source": "Alibaba PAI cluster-trace-gpu-v2020, pai_task_table (Terminated GPU tasks, 1 min < duration < 72 h)",
        "tasks": int(len(df)), "days": n_days, "cluster_gpus": total_gpus,
        "tasks_per_day": len(df) / n_days,
        "tasks_per_gpu_day": len(df) / n_days / total_gpus,
        "gpu_hours_per_gpu_day": float(df.gpu_h.sum() / n_days / total_gpus),
        "duration_h": {q: float(df.dur_h.quantile(p)) for q, p in
                       [("p10", .1), ("p50", .5), ("p90", .9), ("p99", .99)]} | {"mean": float(df.dur_h.mean())},
        "gpus_per_task": {"p50": float(df.gpus.quantile(.5)), "p90": float(df.gpus.quantile(.9)), "mean": float(df.gpus.mean())},
        "arrival_hour_share": [float(x) for x in hourly.values],
    }
    out.write_text(json.dumps(stats, indent=2))
    # resampling pool used by the job generator (fixed seed -> reproducible)
    smp = df.sample(n=min(50000, len(df)), random_state=42)[["dur_h", "gpus", "gpu_h", "hour"]]
    smp.to_csv(DERIVED / "alibaba_job_sample.csv", index=False)
    print(f"  Alibaba: {len(df):,} GPU tasks over {n_days} d -> {stats['tasks_per_gpu_day']:.2f} tasks/GPU/day, "
          f"{stats['gpu_hours_per_gpu_day']:.2f} GPU-h/GPU/day, median {stats['duration_h']['p50']*60:.0f} min")


if __name__ == "__main__":
    DERIVED.mkdir(parents=True, exist_ok=True)
    print("[1/4] weather");  fetch_weather()
    print("[2/4] ElCom");    fetch_elcom(); fetch_site_prices()
    print("[3/4] MLPerf");   derive_mlperf()
    print("[4/4] Alibaba");  derive_alibaba()
    print("[5/5] Swiss heat-sink grid"); fetch_heat_grid()
    print("done.")
