# HeatShift: compute that follows the cold

**DigitalHeat challenge · sprintd Basel GreenTech: "Useful Load ⇒ Useful Heat"**

A server is a 1 kW heater that happens to compute. HeatShift schedules interruptible AI jobs
across basement servers, so that their heat arrives **where and when homes need it**, for a
full year. It reports useful heat, useful computing, missed deadlines and the break-even price.
All numbers are transparent and reproducible.

<!-- HEADLINE:START -->
## Headline (reference house Basel, 2025, 8 × 1 kW servers)

| Heat covered | Heat dumped | Missed deadlines | Utilisation | Break-even | CO₂ avoided |
|---|---|---|---|---|---|
| **92.4 %** | **0 kWh** | **0.40 %** | 52 % | **CHF 0.89 / server-h** (market 0.90–2.40) | 4.1 t/yr |

Same jobs, same weather, whole 10-site fleet (with simulated outages):
a data-centre scheduler dumps **473 MWh** of heat;
a thermostat misses **38 %** of deadlines;
**HeatShift misses 0.38 % and dumps 0**, with 184 automatic failovers.
Fleet: **181 t CO₂/yr** and 1.41 GWh saved against a data centre plus heat pump.

**Regional insight (real ElCom 2026 prices):** Basel has the most expensive electricity in the fleet (33.25 Rp/kWh vs 22.32 in Zermatt).
The best sites are year-round heat sinks with cheap power: Hotel Zermatt CHF 0.54/h, Indoor pool Aarau CHF 0.54/h, Hotel Davos CHF 0.55/h.
Full numbers: [results/REPORT.md](results/REPORT.md).
<!-- HEADLINE:END -->

## What is in the box

| Must-have from the case | Where |
|---|---|
| ≥ 2 job types with runtime, energy/job, interruptibility, deadlines | 5 job types (`heatshift/jobs.py`). Energy and throughput from **MLPerf v6.1** for a 1 kW 4× RTX PRO 4500 server |
| ≥ 1 job running on our own machine with measured runtime | **2 real jobs**: ResNet-50 image tagging (Imagenette) and MiniLM embeddings (SQuAD). Runtime **and watts measured** on the laptop (`scripts/benchmark_jobs.py`) |
| Jobs per day and where the figure comes from | Alibaba PAI GPU trace 2020 (618k GPU tasks): job sizes and daily arrival curve. Demand level is a stated assumption with a sensitivity sweep |
| Scheduler distributes / pauses / reschedules on heat demand, with storage | `heatshift/scheduler.py`: heat budget incl. 35 kWh tank, least-laxity-first, forecast admission, proactive migration across a 10-site fleet |
| Same scheduler code controls a real job (pause/resume, checkpoint) | `live/controller.py` calls the **same** `HeatShift.allocate()`. SIGSTOP/SIGCONT pause, duty-cycle throttling, SIGTERM → checkpoint → resume on another server |
| Balance per house and year | servers, coverage, dumped heat, utilisation, missed %, electricity cost, computing income, **break-even CHF/server-h**, and when it does **not** pay off (`results/REPORT.md`, dashboard) |
| All assumptions visible | `config/assumptions.json`: every number with its source. Rendered on the dashboard |
| Live demo: January day, July day, interrupted job | `make demo`, then http://localhost:8050 |
| **Extra: Swiss map** | 26 cantons, 10 fleet sites as hotspots, animated month by month; 106 locations scored with real 2025 weather for summer heat demand ("where to put the next 1,000 servers") |
| **Extra: regional economics** | Real ElCom 2026 tariff for every site's municipality, and break-even per site |
| **Extra: demand cross-check** | HSR residential profiles compared with the 27 MWh case reference; unit ambiguity and sizing sensitivity documented in `docs/HEAT_PROFILE_CROSSCHECK.md` |
| **Extra: edge cases** | Server/internet outages with automatic failover, family holidays, forecast error, atomic jobs, no spot market, cold snaps, alpine summers. Each one simulated, with results on the dashboard |
| **Extra: impact** | Energy and CO₂ saved against a data centre (PUE 1.56) plus a heat pump |

## Quick start

```bash
make setup     # Python 3.12 env (uv), torch, sentence-transformers, models
make data      # Open-Meteo 2025 (10 sites), ElCom API, MLPerf summary.xlsx, Alibaba trace, datasets
make bench     # measure the real jobs on this machine (~10 min, keep it idle)
make year      # 8,760 h × 4 policies + servers sweep + demand sweep -> results/ (~90 s)
make test      # energy balance, zero dump, tank limits, checkpoint/resume
make demo      # dashboard + live controller -> http://localhost:8050
make profile-check  # compare the reference house against supplied HSR profiles
```

### Auditable what-if scenarios

Use `heatshift.config.scenario()` to test an assumption without editing the
canonical JSON file. Overrides are isolated to the current run and retain their
source for reporting:

```python
from heatshift.config import scenario, assumption_audit, val

with scenario({"reference_house.tank_usable_kwh": 70}, source="double tank demo"):
    print(val("reference_house.tank_usable_kwh"))  # 70
    print(assumption_audit())
```

## Data sources

| Input | Source | Value used |
|---|---|---|
| Weather | Open-Meteo historical API, 2025 hourly, Basel + 9 fleet sites | 8,760 h per site |
| Heat demand | case: 27,000 kWh space heat on days with mean < 12 °C, 12 kWh/day hot water | UA = 393 W/K (calibrated); 206 heating days; coldest day 9.5 kW average |
| Electricity | ElCom 2026, H4 (live API query per municipality) | Basel 33.25 · Liestal 30.1 · Aarau 27.2 · St. Moritz 27.0 · Lugano 24.8 · Zurich 24.7 · Davos 23.5 · Zermatt 22.3 Rp/kWh |
| Swiss map | Canton boundaries (GeoJSON, codeforgermany/click_that_hood); Open-Meteo 2025 daily temperature at 106 grid points | heating days and summer heating days per location |
| Heat value | case: electricity ÷ 2.5 (heat-pump parity) | 13.3 Rp/kWh |
| Server throughput | MLPerf Inference v6.1 (summary.xlsx): HPE DL345 Gen12 4× RTX PRO 4500 | Whisper 3,686 samples/s · Llama-3.1-8B 9,558 tok/s · YOLO 146 img/s/GPU |
| Job shapes | Alibaba cluster-trace-gpu-v2020, pai_task_table | 1.46 tasks/GPU/day, median 17 min |
| Real jobs | Imagenette-160 (fast.ai), SQuAD v1.1 passages, ResNet-50, all-MiniLM-L6-v2 | measured on Apple M5 |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the scheduler works and [docs/PITCH.md](docs/PITCH.md) for the demo script.

## Honest limitations

* System power for the 1 kW server is the DigitalHeat assumption (MLPerf summary has no power column). Laptop power is measured.
* One weather year (2025). The forecast is synthetic (actual + AR(1) error), not archived forecasts.
* Hourly resolution. The tank is lossless and has no stratification (as the case specifies).
* Market prices for GPU-hours are ranges from public marketplaces, not quotes.
* District heating is out of scope (an outlook: a summer heat sink for the fleet).
