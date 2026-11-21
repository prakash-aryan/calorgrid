# CalorGrid

Basel 2025 thermal-compute 3D atlas, hourly conservation engine, heat-led scheduler and live CPU batch worker for the sprintd Basel GreenTech Edition case.

## Quickstart (CPU-only fresh clone)

Tested on Ubuntu Linux (POSIX process groups for `SIGSTOP`/`SIGCONT`). System prerequisites outside `uv`: `tesseract-ocr` (for the live `archive_ocr` worker) and Chromium or Google Chrome (for Playwright WebGL E2E tests).

```bash
uv sync
env -u PYTHONPATH uv run digitalheat verify
env -u PYTHONPATH uv run digitalheat serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in Chromium to launch the **CalorGrid** 3D Thermal-Compute Atlas. Fresh git clone size is `8.5 MB` `.git` / `8.2 MB` working tree excluding `.venv` (`5.7 MB` of 18 Draco+WebP GLBs with 2048px `swisstopo` geodata, SpeedTree vegetation and SimReady hardware plus `1.0 MB` of offline Three.js).

Run tests and linting on CPU:

```bash
env -u PYTHONPATH uv run ruff check src/ tests/
env -u PYTHONPATH uv run pytest tests/unit/ -v
env -u PYTHONPATH PW_CHANNEL=chrome uv run pytest tests/e2e/ -v
```

If your shell sources ROS Jazzy (`PYTHONPATH` containing `/opt/ros/jazzy`), prefix commands with `env -u PYTHONPATH` (or `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`) so pytest skips external ROS `launch_testing` entrypoints.

## Reference house results (Basel 2025, N = 4 x 1 kW servers)

Workload trace assumption: `12.0` offered server-hours per server per day (`50%` 1-hour `archive_ocr` jobs + `50%` 2-hour `iiif_embeddings` jobs, 24 h diurnal arrival weights from Alibaba GPU cluster trace v2020; computing price `0.25 CHF/srv-h`).

| Metric (Basel 2025, 31,231.02 kWh demand) | Smart (Heat-Led) | Baseline (Run-on-Arrival) |
|---|---:|---:|
| Servers per house | 4 x 1 kW | 4 x 1 kW |
| Heat demand covered | 34.1% (10,643.2 kWh) | 32.7% (10,225.1 kWh) |
| Heat dumped (80% water loop) | 0.0 kWh (0.0%) | 3,790.1 kWh (27.0% of recovered) |
| Server utilisation (`N * 8760`) | 38.0% (13,304 srv-h) | 50.0% (17,519 srv-h) |
| Missed SLA deadlines (unserved jobs) | 26.3% | 0.0% |
| Electricity cost (IWB H4 `0.3325 CHF/kWh` excl. VAT) | CHF 4,423.58 | CHF 5,825.07 |
| Computing income (at `0.25 CHF/srv-h`) | CHF 3,326.00 | CHF 4,379.75 |
| OPEX break-even price | 0.2261 CHF/srv-h | 0.2549 CHF/srv-h |
| Hourly conservation residual | `3.97e-10 kWh` | `4.89e-10 kWh` |

**Where the model loses money:**
- At `0.25 CHF/srv-h`, Smart covers OPEX after heat credit (`0.2261 CHF/srv-h`) but loses money once hardware CAPEX (`1,400 CHF/server-yr` central) enters the ledger. This 4-server house at `38.0%` utilisation needs **`~0.647 CHF/srv-h`** (`0.473 CHF/srv-h` under an unlimited interruptible queue at `64.6%` utilisation; `0.386 to 0.43 CHF/srv-h` in year-round commercial or paired `Erdsonde` Verbund scenes depending on secondary-sink heat valuation).
- Smart beats Run-on-Arrival below **`~0.346 CHF/srv-h`** including heat credit (before SLA penalties). Above that price, Run-on-Arrival earns more despite dumping heat.
- Under measured HEATWISE idle power ratios (`21.0% to 26.8%`), pausing servers via OS `SIGSTOP` without deep sleep pushes Smart's break-even to `0.340 to 0.372 CHF/srv-h`, reversing its OPEX edge over Baseline (`0.325 to 0.344 CHF/srv-h`).
- Retail residential `H4` (`0.3325 CHF/kWh` excl. VAT / `0.3594` incl. 8.1% VAT) exceeds industrial `C6` (`0.2527 CHF/kWh` excl. VAT) by `0.0798 CHF/kWh`, erasing `75.0%` of the maximum `0.1064 CHF/kWh` residential heat credit. Raising the displaced heat-pump COP from `2.5` to `3.5` lifts the OPEX break-even floor from `0.2261` to `0.2565 CHF/srv-h`.
- Physical limits: the benchmark tank is lossless (`35 kWh`, 1,000 L / 30 K spread). ASHRAE W45 caps IT facility-water supply at `45 C`, so 55-60 C hygienic DHW requires preheating or auxiliary lift. The local worker reads OS PID and CPU time via `psutil` and estimates workload joules (not wall-plug metering).

## Architecture

- `src/digitalheat/config.py`: Physical, thermal, economic and versioned API schema definitions (1 kW reference server, `0.80` water-loop heat recovery, `35 kWh` lossless tank, no heat pump in server loop, district heating grid feed-in out of scope).
- `src/digitalheat/data_loader.py`: SHA-256 manifest verification and UTC to `Europe/Zurich` DST localization across all 8,760 local hours (23 h spring transition on `2025-03-30`, 25 h autumn transition on `2025-10-26`).
- `src/digitalheat/physics.py`: Hourly energy conservation engine enforcing `0.8 * E_server + S_start = Q_used + Q_dumped + S_end` (`0 <= S <= 35 kWh`, residual `<= 1e-6 kWh`), all 7 KPIs with zero-division guard, 5-line lumped 1-node basement air model for the 20% unrecovered heat, 7 Swiss Verbund thermal scenes (residuals `<= 7.3e-9 kWh`) and 5 Pilot Reality sensitivity modules.
- `src/digitalheat/scheduler.py`: `ReferenceScheduler` (`decide_live_step` runs both the 8,760 h annual simulation and live process control), `RunOnArrivalBaseline`, `ExternalScheduleAdapter` hook and Digital Humanities batch workloads (`archive_ocr`, `iiif_embeddings`, `audio_transcription` MFCC feature workload).
- `src/digitalheat/worker.py`: Local CPU worker running `tesseract` OCR, 2D FFT IIIF image embeddings and MFCC audio workloads with live `SIGSTOP`/`SIGCONT` process-group control from the scheduler heat signal, OS PID and CPU-time telemetry, estimated workload energy and 1 kW reference scaling.
- `src/digitalheat/api.py` & `src/digitalheat/cli.py`: Versioned FastAPI backend (`/api/v1/*`) serving simulation, Verbund, sensitivity, provenance, asset manifest and live worker endpoints with zero KPI math in the browser, plus `POST /api/v1/scheduler/adapter` for external scheduler integration.
- `web/`: Offline Three.js (`three@0.187.0`, MIT) light Swiss editorial 3D atlas with 7-KPI ledger, X-Ray house cutaway, animated hydronic loop, live process-ID rack throttle view, camera presets (`Basel Overview`, `Jan Peak`, `Jul Dump`, `Live Rack`, `Summer Verbund`), 4-category `Provenance` overlay and slide-over `Pilot Reality` panel.

## Datasets, 3D Assets and Attributions

All data inputs are pinned with SHA-256 checksums in `data/manifest.json`; 3D assets are indexed in `assets/manifest.json` and `assets/attributions.json`:

- **Basel and Davos 2025 hourly reanalysis weather**: Open-Meteo historical archive (`2024-12-31` to `2025-12-31` UTC localized to `Europe/Zurich`, CC BY 4.0). For Basel, UTC localization preserves exact elapsed hours (`8,760 h`, `206` heating days, `-5.6 C` min, `26,851.02 kWh/yr` space heating + `4,380.0 kWh/yr` DHW = `31,231.02 kWh/yr`, differing in space heating by `0.00175%` from the naive local-timezone endpoint). Davos totals `52,090.5 kWh/yr` (`291` heating days).
- **Electricity tariffs**: ElCom / LINDAS 2026 IWB Basel (`H4` residential `0.3325 CHF/kWh` excl. VAT / `0.3594 CHF/kWh` incl. 8.1% VAT; `C2` to `C6` commercial/industrial tariffs via `lindas.admin.ch`).
- **Hardware thermal telemetry**: HEATWISE OCP dataset (RISE Sweden, Zenodo `10.5281/zenodo.23237930`, CC BY 4.0).
- **AI inference benchmarks**: MLPerf Inference v5.1 Offline (`summary_results.json`, Apache-2.0); derived J/sample and CHF/1M token figures are not verified by MLCommons.
- **Swiss 3D geodata**: Source: Federal Office of Topography swisstopo (`swissBUILDINGS3D 3.0`, `swissALTI3D` 2 m DEM, `swissimage-dop10`, Open Government Data terms of use).
- **Map & site context**: OpenStreetMap via Overpass API (`(c) OpenStreetMap contributors, ODbL`, projected into Swiss LV95 via `pyproj`).
- **Interior architectural & HVAC blueprint**: buildingSMART IFC 4.3 Simple-Scene (`Building-Architecture.ifc`, `Building-Structural.ifc`, `Building-Hvac.ifc`; `(C) buildingSMART International Ltd.`, CC BY 4.0). Open reference IFC 4.3 house blueprint, not a surveyed private Basel residence.
- **Basement & datacentre 3D hardware stand-ins**: Converted to Draco+WebP `.glb` from SimReady repositories by NVIDIA (`nvidia/simready-assets`), Vertiv (`Vertiv/simready-assets`: ORV3 rack, XDU1350 CDU, NVD RPP panel) and ForgeCAD (`ForgeCAD/simready-assets`: gate valve), credited as visual CAD stand-ins (CC BY 4.0 per upstream package manifests).
- **Software libraries**: Three.js (MIT), Tesseract OCR (Apache-2.0), FastAPI (MIT). Project code license: MIT.
- **Literature context**: ETH Zurich Aquasar (Zimmermann et al., Energy 2012) and Data Furnace (Liu et al., USENIX HotCloud 2011).

## Live Web Platform & Interactive Tools

- **Explore (14-Site Swiss Network):** 3D swisstopo Switzerland relief map with a year-round 12-month timeline scrubber, Smart vs. Uncoordinated scheduling comparison, latency-aware fiber routing across 10 cantons, and outside/inside 3D building inspection.
- **Configure (Building Hardware Sizer):** Interactive 3D plant room comparing four real server classes (`0.25 kW` Edge Micro-Cluster, `1 kW` HydroNode, `2.9 kW` OCP Winterfell Hybrid, `10.4 kW` NVIDIA B200 Liquid AI) with isolated primary dielectric coolant loop, plate heat exchanger (CDU), 3-way diverter valve, and 1,000 L secondary heating water buffer tank.
- **HeatLedger (Operator Telemetry):** Live Swiss weather integration (keyless Open-Meteo API), interactive thermal demand throttle, real-time OS worker dispatch (`SIGSTOP`/`SIGCONT`), and annual energy/economics verification.
