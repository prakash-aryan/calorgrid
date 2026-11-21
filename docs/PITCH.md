# 5-minute pitch + live demo script

Setup before going on stage: `make demo`, open http://localhost:8050, laptop plugged in,
browser full screen. Select **Image tagging** and **1 h = 2 s**. Tip: press **Heat demand × 0** during the January run to force a visible PAUSE.

---

**0:00 – Hook (20 s)**
> "A server is a 1 kW heater that happens to compute. Basel homes burn 31,000 kWh a year on heat.
> DigitalHeat puts servers in basements. The hard part is not the hardware. It is the **schedule**:
> customers want computing every day of the year, but houses only want heat in winter."

**0:20 – What we built (30 s)**, top of the dashboard
> "HeatShift is a scheduler. It runs a full year, hour by hour, on real 2025 Basel weather,
> the real IWB tariff of 33.25 Rp, MLPerf throughput and real job sizes from Alibaba's GPU cluster trace.
> For the reference house with 8 servers: **92.4 % of heat covered, zero heat dumped,
> 0.40 % missed deadlines, break-even at CHF 0.89 per server-hour**, below the market price."

**0:50 – Live demo 1: cold January day (60 s)**, Live, *Cold January day*, ▶ Run day
> "Same scheduler code as the simulation. The pink bar is a **real ResNet-50 job on this laptop**.
> −3 °C outside, the house needs almost 10 kW, so every server runs and the tank stays near empty.
> Look at the log: the job is running, and it is tagging real photos."

**1:50 – Live demo 2: July day + interrupted job (70 s)**, *Hot July day*, ▶ Run day
> "In July the house only needs hot water, about 12 kWh a day. The heat budget collapses once the tank is full.
> Watch the job card: **PAUSED**. The process is frozen with SIGSTOP and uses zero CPU.
> At 18:00 people shower, heat is needed again, and the job continues: **RESUMED**."
Then press **Migrate → Hotel Davos**:
> "Or we move it. SIGTERM, the job writes a checkpoint, and it restarts on the Davos server
> at exactly the same item. No work is repeated. In summer, our fleet sends deadline jobs to indoor pools and alpine hotels."

**3:00 – Why scheduling matters (40 s)**, Scheduler section
> "Same jobs, same houses, four policies. A data-centre scheduler meets deadlines but dumps
> 473 MWh of heat a year. A thermostat dumps nothing but misses 38 % of deadlines.
> HeatShift gets both: under 1 % missed, zero dumped. It uses the tank, a 36-hour forecast,
> admission control and early migration."

**3:40 – Break-even and when it does NOT pay (50 s)**, Break-even section
> "Electricity is never the problem: the net running cost is only CHF 0.23 per server-hour.
> **Capex is**, so utilisation decides everything. 1 server per house gives the lowest
> break-even. 8 servers gives the highest profit. Beyond about 10 servers, summer idle time kills it.
> If DigitalHeat can only sell spot capacity, it does not pay."

**4:05 – Swiss map (25 s)**, Map section, press **▶ Play the year**
> "In winter, computing glows in the lowlands. In summer it moves to the Aarau indoor pool and the alpine hotels.
> Real ElCom prices show Basel has the most expensive power in our fleet. The best DigitalHeat sites are year-round heat sinks
> with cheap power: Zermatt and Aarau break even at CHF 0.54, against 0.89 for the Basel house.
> The blue dots show where the next 1,000 servers should go."

**4:30 – Close (30 s)**
> "Everything is reproducible: one config file with sources, `make all`, tests for energy balance
> and checkpoint/resume. Next step with DigitalHeat: plug in the real customer job queue and live Open-Meteo forecasts."

---

## Q&A cheat sheet

| Question | Answer |
|---|---|
| How many jobs per day, and where from? | Job sizes and arrival curve come from the Alibaba PAI GPU trace 2020 (618k GPU tasks, 1.46 tasks/GPU/day, median 17 min). 12 trace tasks are bundled into one customer batch. Demand is 60 % of the fleet's yearly heat-matched capacity, flat over the year. The reference house gets about 10 deadline jobs/day, and the demand sweep shows sensitivity. |
| Why is the energy per job credible? | MLPerf v6.1 results for an HPE DL345 with 4× RTX PRO 4500 (about 1 kW): Whisper 3,686 samples/s, Llama-3.1-8B 9,558 tok/s, YOLO 146 img/s per GPU. Our laptop jobs are measured with the Mac battery controller (whole-system watts, no estimate). |
| Forecast perfect? | No. Actual temperature plus AR(1) error, sd 1.5 °C. The scheduler plans on only 70 % of forecast heat sink. That value was tuned: trusting the forecast less gave fewer misses *and* fewer rejections. |
| What if the tank is full now? | Press "Tank full now" in the demo. The budget drops, and the lowest-laxity-priority job pauses within the same hour. |
| Non-interruptible jobs? | 5 % of jobs (model evaluation runs) are atomic. They only start if the forecast guarantees heat for their whole runtime. |
| Who pays? | DigitalHeat pays electricity, sells heat to the homeowner at heat-pump parity (electricity price ÷ 2.5), sells SLA computing at price p and spot backfill at 0.5 p. |
| What if a server dies? | Simulated: 2 outages × 18 h per site per year. 184 automatic failovers from the last checkpoint; no measurable impact on missed deadlines (0.38 % with outages vs 0.43 % without, within run-to-run noise). |
| Holidays? | The family enters them in the app. Heating sets back to 16 °C, hot water drops to 10 %. Scenario result: covered 93.1 %, missed 0.47 %. |
| Regional prices? | Real ElCom 2026 tariff per municipality, from Basel 33.25 to Zermatt 22.32 Rp/kWh. Break-even per site shown on the map. |
| CO₂? | Fleet saves 1.41 GWh/yr against a data centre (PUE 1.56) plus heat pump, about 181 t CO₂/yr at 128 g/kWh. |
| District heating? | Out of scope. The outlook: an anchor site with a district-heating connection would be the perfect summer sink. |
