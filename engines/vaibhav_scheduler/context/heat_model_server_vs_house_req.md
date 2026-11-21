# Heat balance model: server heat vs. captured heat vs. house demand

Built 20 Nov 2026 from `residential_heating_hour_profile_HSR.csv`. The runnable version is `heat_balance_model.py` (Python, standard library only), shared in the chat where this doc was written.

## What it answers

For every hour of the year: how much heat the servers produce, how much the DigitalHeat system absorbs (85%), how much the building needs, and the gaps between the three.

## Inputs and assumptions

- **Demand** Q_dem(t): the HSR profile. 8,640 hourly steps (a 360-day year), four building types, no gaps, no negative values.
- **Units:** the CSV header says MWh, but the values only fit a single building when read as kWh per hour (old single-family house: 16,221 per year, peak 7.37 per hour). Everything below treats them as kWh. If the header is right, the profile describes roughly 1,000 buildings and every energy and power figure is ×1,000; the percentages do not change.
- **Capture** η = 0.85: share of server heat absorbed by the DigitalHeat system.
- **Server heat = electrical draw:** Q_srv(t) = P_el(t). All electricity ends up as heat.
- **Time step** 1 h, so kW and kWh per hour are the same number.
- **No storage** in the base model: absorbed heat that is not needed in the same hour counts as surplus.
- **Server sizes** (5, 10, 20 kW) are picked from the 5–20 kW per unit on the DigitalHeat slides. They are a parameter, not a given.

## Equations (per hour t)

```
Q_srv(t)  = P_el(t)                    heat produced by the servers
Q_abs(t)  = η · Q_srv(t)               heat absorbed by DigitalHeat     (η = 0.85)
Q_loss(t) = (1 − η) · Q_srv(t)         heat produced but not captured   (15%)
Q_dem(t)                               heat required by the house       (HSR profile)
```

The three differences:

```
produced − absorbed :  Q_srv − Q_abs = (1 − η) · Q_srv       = 0.15 · Q_srv
absorbed − required :  Δ(t) = η · Q_srv(t) − Q_dem(t)        = 0.85 · Q_srv − Q_dem
produced − required :  Q_srv − Q_dem = Δ(t) + (1 − η) · Q_srv
```

Δ(t) is the one that matters for the house. Split by sign:

```
S(t) = max(Δ(t), 0)                    surplus: store it or reject it
B(t) = max(−Δ(t), 0)                   deficit: backup heater
U(t) = min(Q_abs(t), Q_dem(t))         server heat the house actually uses
```

In every hour Q_abs = U + S and Q_dem = U + B.

## Server operating modes

```
constant :  P_el(t) = P_max                                  runs flat out all year
follow   :  P_el(t) = min(P_max, max(P_min, Q_dem(t) / η))   workload follows heat demand
```

## Annual totals (T hours)

```
U(P_max) = Σ min(η · P_max, Q_dem(t))       same in both modes
coverage = U / Σ Q_dem
backup   = Σ Q_dem − U                       same in both modes

constant :  produced = P_max · T    surplus = η · P_max · T − U    not captured = (1 − η) · P_max · T
follow   :  produced = U / η        surplus = 0                    not captured = (1 − η) · U / η     (P_min = 0)

full coverage needs  P_max ≥ max Q_dem / η
```

## Results on the HSR profile (kWh, kW)

### Demand

| Building | Annual demand | Peak hour | Mean | Hours with no demand |
|---|---|---|---|---|
| SFH_OLD | 16,221 | 7.37 | 1.88 | 1,689 |
| SFH_NEW | 7,894 | 4.37 | 0.91 | 2,162 |
| MFH_OLD | 100,172 | 38.10 | 11.59 | 2,358 |
| MFH_NEW | 32,827 | 16.50 | 3.80 | 3,683 |

### Server heat output needed to cover a share of annual demand (kW, no storage)

| Building | 50% | 80% | 90% | 95% | 100% |
|---|---|---|---|---|---|
| SFH_OLD | 1.82 | 3.64 | 4.60 | 5.32 | 8.67 |
| SFH_NEW | 1.14 | 2.20 | 2.76 | 3.20 | 5.14 |
| MFH_OLD | 10.12 | 18.46 | 22.91 | 26.56 | 44.82 |
| MFH_NEW | 4.16 | 7.56 | 9.48 | 11.25 | 19.41 |

### Annual balance, server at constant full load

| Building + server | Produced | Absorbed | Not captured | Required | Absorbed − required | Used by house | Surplus | Deficit | Demand covered | Server heat used |
|---|---|---|---|---|---|---|---|---|---|---|
| SFH_OLD + 5 kW | 43,200 | 36,720 | 6,480 | 16,221 | +20,499 | 15,100 | 21,620 | 1,121 | 93.1% | 35.0% |
| SFH_NEW + 5 kW | 43,200 | 36,720 | 6,480 | 7,894 | +28,826 | 7,893 | 28,827 | 1 | ~100% | 18.3% |
| MFH_OLD + 20 kW | 172,800 | 146,880 | 25,920 | 100,172 | +46,708 | 84,097 | 62,783 | 16,075 | 84.0% | 48.7% |
| MFH_NEW + 10 kW | 86,400 | 73,440 | 12,960 | 32,827 | +40,613 | 30,134 | 43,306 | 2,693 | 91.8% | 34.9% |

Hours in surplus / in deficit: SFH_OLD 7,346 / 1,280; SFH_NEW 8,624 / 16; MFH_OLD 5,804 / 2,805; MFH_NEW 7,417 / 1,218.

### Annual balance, server following heat demand

| Building + server | Produced | Absorbed = used | Not captured | Required | Deficit | Surplus | Full-load hours | Load factor |
|---|---|---|---|---|---|---|---|---|
| SFH_OLD + 5 kW | 17,765 | 15,100 | 2,665 | 16,221 | 1,121 | 0 | 3,553 | 41% |
| SFH_NEW + 5 kW | 9,286 | 7,893 | 1,393 | 7,894 | 1 | 0 | 1,857 | 21% |
| MFH_OLD + 20 kW | 98,937 | 84,097 | 14,841 | 100,172 | 16,075 | 0 | 4,947 | 57% |
| MFH_NEW + 10 kW | 35,452 | 30,134 | 5,318 | 32,827 | 2,693 | 0 | 3,545 | 41% |

### Month by month: SFH_OLD + 5 kW at constant load

30-day blocks, assuming hour 1 is 1 January. Produced is 3,600 and absorbed 3,060 every month.

| Month | Required | Used by house | Surplus | Deficit |
|---|---|---|---|---|
| Jan | 3,145 | 2,735 | 325 | 409 |
| Feb | 2,778 | 2,540 | 520 | 239 |
| Mar | 1,608 | 1,593 | 1,467 | 15 |
| Apr | 1,042 | 1,042 | 2,018 | 0 |
| May | 575 | 575 | 2,485 | 0 |
| Jun | 251 | 251 | 2,809 | 0 |
| Jul | 172 | 172 | 2,888 | 0 |
| Aug | 199 | 199 | 2,861 | 0 |
| Sep | 288 | 288 | 2,772 | 0 |
| Oct | 1,056 | 1,056 | 2,004 | 0 |
| Nov | 1,806 | 1,775 | 1,285 | 31 |
| Dec | 3,302 | 2,875 | 185 | 427 |

## Reading the results

- **Annual totals mislead.** At constant load the server captures more heat than the house needs in all four pairings, yet backup heat is still needed in winter because the surplus falls in other hours. The hourly Δ(t) is the quantity to simulate.
- **Flat-out operation wastes most of the heat.** Only 18–49% of server heat is used. Heat-following keeps the same coverage and the same backup, produces no surplus and uses 85% of server heat by construction. The price is a server that runs 21–57% of the year at this site; the rest of the compute has to move elsewhere.
- **The last 10% of demand doubles the server.** 90% coverage needs about half the server size of 100% (4.6 vs 8.7 kW for the old single-family house).

## Storage extension (in the script, off by default)

```
E(t) = (1 − λ) · E(t−1) + charge(t) − discharge(t),     0 ≤ E(t) ≤ E_max
charge(t)    = min(S(t), E_max − (1 − λ) · E(t−1))
discharge(t) = min(B(t), (1 − λ) · E(t−1))
rejected(t)  = S(t) − charge(t)
backup(t)    = B(t) − discharge(t)
```

Two runs at constant load, no standing loss:

- SFH_OLD + 5 kW with a 17.4 kWh tank (about 500 L of water over 30 K): backup falls from 1,121 to 789 kWh, coverage from 93.1% to 95.1%.
- MFH_OLD + 20 kW with a 70 kWh tank (about 2,000 L): backup falls from 16,075 to 14,162 kWh, coverage from 84.0% to 85.9%.

A buffer tank moves heat across hours, not across seasons. In follow mode as written the tank never charges, because the server never overproduces; a scheduler that pre-charges the tank ahead of cold hours is the next step.

## Running the script

```
python3 heat_balance_model.py residential_heating_hour_profile_HSR.csv
python3 heat_balance_model.py residential_heating_hour_profile_HSR.csv \
    --server-kw 5 10 20 --eta 0.85 --mode both \
    --storage-kwh 17.4 --storage-loss 0.005 --min-load-kw 0.5 \
    --summary-csv summary.csv --monthly-csv monthly.csv --hourly-csv hourly.csv
```

Every run checks energy conservation (produced = not captured + delivered + rejected + storage losses + change in tank level). The totals above were also cross-checked with an independent calculation.
