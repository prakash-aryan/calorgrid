# HeatShift results (generated 2026-11-21 03:07, seed 7)

## Balance per house and year: reference house Basel 2025, 8 × 1 kW servers, HeatShift

| Metric | Value |
|---|---|
| Servers per house | 8 |
| Heat demand (space + hot water) | 31'380 kWh (27'000 + 4'380) |
| Share of heat demand covered by servers | 92.4 % |
| Heat dumped | 0 kWh |
| Backup heat (heat pump) | 2'384 kWh |
| Utilisation | 51.7 % (30'521 SLA h + 5'702 spot h) |
| Deadline jobs per day (house) | 10.3 |
| Missed deadlines | 0.40 % |
| Electricity | 36'223 kWh → CHF 12'044 |
| Heat value (electricity price ÷ 2.5) | CHF 3'854 |
| Capex + install annuity, opex | CHF 22'104 + 2'000 |
| Computing income at mid-market | CHF 59'767 |
| **Break-even computing price** | **CHF 0.89 per server-hour** (market 0.90–2.40) → pays off at low market prices |
| Break-even, electricity only | CHF 0.23 per server-hour |

## Scheduler comparison (whole fleet, same jobs and weather)

| Policy | Missed deadlines | Rejected | Heat dumped | Fleet utilisation | House coverage | House break-even |
|---|---|---|---|---|---|---|
| Always-on (data-centre style) | 0.09 % | 0.0 % | 473.3 MWh | 100 % | 92.4 % | CHF 0.63 |
| Thermostat (heat-now only) | 37.95 % | 0.0 % | 0.0 MWh | 71 % | 90.3 % | CHF 0.91 |
| HeatShift (ours) | 0.38 % | 1.3 % | 0.0 MWh | 73 % | 92.4 % | CHF 0.89 |
| HeatShift + deadline rescue | 0.01 % | 1.2 % | 1.5 MWh | 73 % | 92.4 % | CHF 0.90 |

## Servers per house (HeatShift)

| Servers | Coverage | Utilisation | Missed | Break-even CHF/h | Profit @ mid-market CHF/yr |
|---|---|---|---|---|---|
| 1 | 19 % | 87 % | 7.76 % | 0.68 | 7'355 |
| 2 | 33 % | 73 % | 2.46 % | 0.73 | 11'727 |
| 3 | 45 % | 67 % | 0.91 % | 0.75 | 15'866 |
| 4 | 58 % | 64 % | 0.70 % | 0.77 | 19'855 |
| 5 | 69 % | 62 % | 0.18 % | 0.79 | 23'476 |
| 6 | 79 % | 59 % | 0.86 % | 0.81 | 25'932 |
| 7 | 87 % | 55 % | 0.67 % | 0.85 | 27'182 |
| 8 | 92 % | 52 % | 0.20 % | 0.89 | 27'474 |
| 9 | 97 % | 48 % | 0.12 % | 0.94 | 26'844 |
| 10 | 99 % | 44 % | 0.71 % | 1.00 | 25'239 |
| 11 | 100 % | 41 % | 0.25 % | 1.07 | 22'730 |
| 12 | 100 % | 37 % | 1.18 % | 1.14 | 19'828 |
| 13 | 100 % | 34 % | 0.57 % | 1.22 | 16'888 |
| 14 | 100 % | 32 % | 0.45 % | 1.29 | 13'942 |
| 15 | 100 % | 30 % | 0.65 % | 1.37 | 10'997 |
| 16 | 100 % | 28 % | 0.83 % | 1.44 | 8'052 |

## Demand sensitivity (fleet)

| SLA demand (× heat-matched capacity) | Policy | Rejected | Missed | Utilisation |
|---|---|---|---|---|
| 0.3 | heatshift | 0.0 % | 0.04 % | 73 % |
| 0.3 | thermostat | 0.0 % | 24.29 % | 71 % |
| 0.45 | heatshift | 0.0 % | 0.16 % | 73 % |
| 0.45 | thermostat | 0.0 % | 30.82 % | 71 % |
| 0.6 | heatshift | 1.3 % | 0.48 % | 73 % |
| 0.6 | thermostat | 0.0 % | 37.95 % | 71 % |
| 0.8 | heatshift | 5.5 % | 0.96 % | 73 % |
| 0.8 | thermostat | 0.0 % | 44.76 % | 71 % |
| 1.0 | heatshift | 9.6 % | 2.43 % | 73 % |
| 1.2 | heatshift | 13.5 % | 7.40 % | 73 % |

## Fleet

10 sites, 252 servers, 243 deadline jobs/day, 0.4 % migrated, 1.3 % rejected, 0.38 % missed, 1'285 MWh useful heat, 0.0 MWh dumped.

## Useful computing delivered by the reference house

| Job type | Server-h | Output |
|---|---|---|
| LLM batch inference (Llama-3.1-8B: summaries, extraction) | 8'887 | 305'772.9 M tokens |
| Speech transcription (Whisper) | 9'717 | 128'927'357.8 k utterances |
| Image tagging / detection (YOLO) | 6'460 | 13'601'444.2 k images |
| Document embeddings for RAG search (MiniLM) | 5'356 | 157'090'307.6 k passages |
| Model evaluation run (not checkpointable) | 101 | 363.0 runs |

## Measured on this machine (Apple M5 (arm64), CPU only, 4 threads, idle 4.4 W)

| Job | Items/s | W above idle | J/item |
|---|---|---|---|
| Image tagging, ResNet-50, Imagenette | 11.6 | 18.8 | 1.625 |
| Embeddings, MiniLM-L6, SQuAD passages | 139.3 | 17.1 | 0.123 |
