# HeatShift architecture

```
                 config/assumptions.json  (single source of truth, every number + source)
                               │
  scripts/fetch_data.py ──► data/weather/*.csv          Open-Meteo 2025 hourly, 10 Swiss sites
                        ──► data/derived/elcom_*.json   ElCom API: IWB Basel H4 2026 = 33.25 Rp/kWh
                        ──► data/derived/mlperf_*.json  MLPerf Inference v6.1 summary.xlsx
                        ──► data/derived/alibaba_*.json Alibaba PAI GPU trace 2020 (618k GPU tasks)
  scripts/benchmark_jobs.py ──► data/derived/measured_jobs.json  (real runtime + watts on this laptop)
                               │
          heatshift/house.py      hourly demand: degree-hours (UA calibrated to 27,000 kWh) + hot water
          heatshift/jobs.py       job types, job stream (trace shapes, flat yearly demand)
          heatshift/scheduler.py  SiteState (heat budget, tank, forecast capacity) + policies
          heatshift/sim.py        8,760 h fleet simulation
          heatshift/economics.py  balance per house, break-even CHF/server-h
                               │
  scripts/run_year.py ──► results/results.json (+ CSVs)   4 policies, servers sweep, demand sweep
                               │
  web/server.py  ── /api/results ──► web/static (dashboard)
                 ── /api/live/*  ──► live/controller.py ── same HeatShift.allocate() ──► real process
                                                         SIGSTOP / SIGCONT / SIGTERM+checkpoint
                                                         workers/image_worker.py (ResNet-50)
                                                         workers/embed_worker.py (MiniLM)
```

## The scheduler in one paragraph

Each hour, each site gets a **heat budget**: how many kW of servers can run without producing
heat that nobody can use, `(demand_now + free tank space) / 0.8`, capped at installed servers.
Jobs fill the budget **least-laxity-first** (time to deadline minus time still needed). Paused
interruptible jobs keep their checkpoint. A new job is only **admitted** by a site whose
**forecast** heat sink before the job's deadline can take it on top of the work already
committed, and the test covers every later deadline too (EDF feasibility). Every hour, an EDF feasibility
test against the 36 h forecast finds jobs that will not make it. Those jobs are **checkpointed and moved** to the site
with the largest free fraction of its heat sink *before* they are late. Any budget left over
is filled with **spot backfill** (no deadline), so winter heat is never wasted.

## Why the numbers are trustworthy

* Energy balance holds on every site, every hour (`tests/test_core.py`).
* Zero dumped heat is a hard constraint for HeatShift, not a lucky outcome.
* The forecast is not perfect: it uses actual temperature plus an AR(1) error (sd 1.5 °C, 12 h correlation).
  Near the 12 °C threshold, whole heating days flip in the forecast, as they do in reality.
* Every external number has a fetch script or a cited source. Nothing is typed into the code.
