/* HeatShift dashboard. Reads /api/results (year simulation) and polls /api/live/state (live controller). */
const css = n => getComputedStyle(document.body).getPropertyValue(n).trim();
const C = {
  sla: css('--series-2'), backfill: css('--series-4'), real: css('--series-5'), backup: css('--neutral'),
  tank: css('--series-3'), blue: css('--series-1'), text: css('--text-primary'), text2: css('--text-secondary'),
  muted: css('--text-muted'), grid: '#2a2a28', accent: css('--accent'), surface: css('--surface-1'),
};
Chart.defaults.color = C.text2;
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif';
Chart.defaults.font.size = 12;
Chart.defaults.borderColor = C.grid;
Chart.defaults.plugins.legend.labels.boxWidth = 10;
Chart.defaults.plugins.legend.labels.boxHeight = 10;
Chart.defaults.plugins.legend.labels.useBorderRadius = true;
Chart.defaults.plugins.legend.labels.borderRadius = 2;
Chart.defaults.plugins.tooltip.backgroundColor = '#0b0b0b';
Chart.defaults.plugins.tooltip.borderColor = '#44433f';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.interaction = { mode: 'index', intersect: false };
Chart.defaults.maintainAspectRatio = false;
Chart.defaults.animation.duration = 300;

const $ = s => document.querySelector(s);
const fmt = (x, d = 0) => x == null || !isFinite(x) ? '-' : Number(x).toLocaleString('de-CH', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (x, d = 1) => fmt(100 * x, d) + ' %';
const human = (v, unit) => {   // 300'482 'M tokens' -> '300 billion tokens'
  const mult = unit.startsWith('M ') ? 1e6 : unit.startsWith('k ') ? 1e3 : 1, base = unit.replace(/^(M|k) /, '');
  const x = v * mult;
  return x >= 1e9 ? `${fmt(x / 1e9, 1)} billion ${base}` : x >= 1e6 ? `${fmt(x / 1e6, 1)} million ${base}` : `${fmt(x)} ${base}`;
};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const bar = (label, data, color, extra = {}) => ({ label, data, backgroundColor: color, borderColor: C.surface, borderWidth: { top: 0, left: 0, right: 0, bottom: 2 }, borderRadius: 4, borderSkipped: 'bottom', maxBarThickness: 34, ...extra });
const line = (label, data, color, extra = {}) => ({ type: 'line', label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.25, ...extra });
const yAxis = (title, extra = {}) => ({ beginAtZero: true, grid: { color: C.grid }, border: { display: false }, title: { display: !!title, text: title, color: C.muted }, ...extra });
const xAxis = (extra = {}) => ({ grid: { display: false }, border: { color: '#44433f' }, ...extra });

let R = null;

async function loadResults() {
  const r = await fetch('/api/results');
  if (!r.ok) { document.body.insertAdjacentHTML('afterbegin', '<div style="padding:20px;background:#511">results.json missing - run <code>make year</code></div>'); return; }
  R = await r.json();
  $('#gen').textContent = `results generated ${R.meta.generated} · seed ${R.meta.seed} · ${R.meta.cases} simulation runs in ${R.meta.runtime_s}s`;
  // each section renders independently: one failure must not break the rest of the page
  for (const fn of [renderTiles, renderYear, renderProfileCheck, renderPolicies, renderEcon, renderMap, renderFleet, renderEdge, renderWork, renderAssumptions, setupLive]) {
    try { await fn(); } catch (e) { console.error(fn.name, e); }
  }
}

/* ---------------- Residential demand profile cross-check ---------------- */
async function renderProfileCheck() {
  const response = await fetch('/api/profile-check');
  if (!response.ok) throw new Error('profile-check.json missing - run `make profile-check`');
  const data = await response.json();
  const old = data.profiles.find(p => p.name === 'SFH_OLD');
  const labels = ['Case · Basel', ...data.profiles.map(p => p.name.replaceAll('_', ' '))];
  const values = [data.reference_space_heating_kwh, ...data.profiles.map(p => p.annualized_kwh)];
  const colors = [C.accent, C.sla, C.real, C.blue, C.tank];
  new Chart($('#c-profiles'), { type: 'bar', data: { labels, datasets: [{
    data: values, backgroundColor: colors, borderRadius: 5, maxBarThickness: 34,
  }] }, options: { indexAxis: 'y', plugins: {
    legend: { display: false },
    tooltip: { callbacks: { label: c => `${fmt(c.raw)} kWh/year` } },
  }, scales: {
    x: yAxis('kWh/year', { max: Math.ceil(Math.max(...values) / 20000) * 20000 }),
    y: xAxis({ ticks: { color: C.text2 } }),
  } } });
  $('#profile-table').innerHTML = `<tr><th>Building profile</th><th class="num">Annual demand</th><th class="num">Peak hour</th><th class="num">vs case</th></tr>` +
    data.profiles.map(p => `<tr><td>${p.name.replaceAll('_', ' ')}</td><td class="num">${fmt(p.annualized_kwh)} kWh</td><td class="num">${fmt(p.peak_hourly_kw, 2)} kW</td><td class="num">${fmt(p.share_of_case_reference * 100, 0)} %</td></tr>`).join('');
  $('#profile-insight').innerHTML = `<div class="profile-tag">DEMAND PROFILE SENSITIVITY</div>
    <h3>Same technology. Different heat sink.</h3>
    <div class="profile-stat">${fmt(old.annualized_kwh / 1000, 1)} <small>MWh</small></div>
    <p>The older single-family profile annualizes to <b>${fmt(old.share_of_case_reference * 100, 0)}%</b> of the case's 27 MWh space-heat reference. The older multi-family profile is <b>${fmt(data.profiles.find(p => p.name === 'MFH_OLD').share_of_case_reference, 1)}×</b> the reference.</p>
    <p>This is a useful sizing insight: the building's heat demand can change the server capacity that has a useful heat sink. The existing yearly simulation still uses the case reference.</p>
    <div class="profile-caveat"><b>Unit caveat</b><br>The source CSV labels values MWh; its companion analysis interprets them as kWh per hour. The chart follows that interpretation and scales 360 days to 365. Confirm the units before treating these as forecasts.</div>`;
}

/* ---------------- KPI tiles ---------------- */
function renderTiles() {
  const h = R.house, n = R.meta.default_servers;
  $('#k-servers').textContent = n;
  const tiles = [
    ['Heat demand covered', fmt(100 * h.coverage, 1), '%', `${fmt(h.useful_heat_kwh)} of ${fmt(h.heat_demand_kwh)} kWh`, true],
    ['Heat dumped', fmt(h.dumped_heat_kwh), 'kWh', 'zero-dump policy, no dry cooler'],
    ['Missed deadlines', fmt(h.missed_pct, 2), '%', `${fmt(h.jobs_per_day, 1)} jobs/day in the house`],
    ['Server utilisation', fmt(100 * h.utilisation, 0), '%', `${fmt(h.sla_server_h)} SLA + ${fmt(h.backfill_server_h)} spot h`],
    ['Break-even price', fmt(h.breakeven_chf_per_server_h, 2), 'CHF/h', `market ${fmt(h.market_low, 2)}–${fmt(h.market_high, 2)} per server-hour`, true],
    ['Profit at mid-market', fmt(h.profit_at_market_mid_chf), 'CHF/yr', h.pays_off_verdict],
    ['Energy saved', fmt(h.energy_saved_kwh / 1000, 1), 'MWh/yr', `vs data centre (PUE ${R.assumptions.find(a => a.key === 'datacenter_pue').value}) + heat pump`],
    ['CO₂ avoided', fmt(h.co2_saved_t, 1), 't/yr', `fleet: ${fmt(R.fleet.co2_saved_t)} t/yr · ${fmt(R.fleet.energy_saved_kwh / 1e6, 2)} GWh saved`],
  ];
  $('#tiles').innerHTML = tiles.map(([l, v, u, note, hero]) =>
    `<div class="tile ${hero ? 'hero' : ''}"><div class="label">${l}</div><div class="value">${v} <small>${u}</small></div><div class="note">${note}</div></div>`).join('');
}

/* ---------------- Year ---------------- */
function renderYear() {
  const P = R.reference_profile, h = R.house;
  $('#year-sub').textContent = `Basel 2025: ${P.heating_days} heating days (daily mean below 12 °C). Space heating ${fmt(P.space_heat_kwh)} kWh plus hot water ${fmt(P.hot_water_kwh)} kWh. Heat-loss coefficient UA ${fmt(P.ua_kw_per_k * 1000)} W/K, calibrated to the case's 27,000 kWh. Coldest day ${P.coldest_day}: ${fmt(P.coldest_day_mean_c, 1)} °C mean, ${fmt(P.coldest_day_avg_kw, 1)} kW average demand.`;
  const M = R.monthly;
  new Chart($('#c-month-heat'), { type: 'bar', data: { labels: MONTHS, datasets: [
    bar('Covered by server heat', M.map(m => m.space_heat_kwh + m.hot_water_kwh - m.backup_kwh), C.sla, { stack: 'h' }),
    bar('Backup heat (heat pump)', M.map(m => m.backup_kwh), C.backup, { stack: 'h' }),
  ] }, options: { scales: { x: xAxis({ stacked: true }), y: yAxis('kWh', { stacked: true }) },
    plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmt(c.raw)} kWh` } } } } });
  new Chart($('#c-month-compute'), { type: 'bar', data: { labels: MONTHS, datasets: [
    bar('Deadline (SLA) jobs', M.map(m => m.sla_server_h), C.sla, { stack: 'c' }),
    bar('Spot backfill', M.map(m => m.backfill_server_h), C.backfill, { stack: 'c' }),
  ] }, options: { scales: { x: xAxis({ stacked: true }), y: yAxis('server-h', { stacked: true }) } } });
  // daily series
  const H = R.hourly, days = [], dem = [], used = [], bk = [];
  for (let i = 0; i < H.demand.length; i += 24) {
    days.push(H.time[i].slice(5, 10));
    let a = 0, b = 0, c = 0;
    for (let k = i; k < i + 24; k++) { a += H.demand[k]; b += H.heat_used[k]; c += H.backup[k]; }
    dem.push(a); used.push(Math.min(b, a)); bk.push(c);
  }
  new Chart($('#c-daily'), { type: 'line', data: { labels: days, datasets: [
    line('Heat demand', dem, C.text, { borderWidth: 1.5 }),
    line('Server heat used', used, C.sla, { fill: 'origin', backgroundColor: 'rgba(217,89,38,.35)', borderWidth: 1.5 }),
  ] }, options: { scales: { x: xAxis({ ticks: { maxTicksLimit: 12 } }), y: yAxis('kWh/day') },
    plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmt(c.raw)} kWh` } } } } });
  $('#balance-card').innerHTML = `<div class="chart-title">Balance per house and year (reference house, ${h.servers} servers)</div><div class="balance">
    ${[['Servers per house', h.servers], ['Heat demand (space + hot water)', fmt(h.heat_demand_kwh) + ' kWh'], ['Share covered by servers', pct(h.coverage)], ['Heat dumped', fmt(h.dumped_heat_kwh) + ' kWh'],
       ['Utilisation', pct(h.utilisation)], ['Missed deadlines', fmt(h.missed_pct, 2) + ' %'], ['Electricity', `${fmt(h.electricity_kwh)} kWh · CHF ${fmt(h.electricity_cost_chf)}`], ['Heat value (elec. / 2.5)', 'CHF ' + fmt(h.heat_value_chf)],
       ['Capex + install annuity', 'CHF ' + fmt(h.capex_annuity_chf + h.install_annuity_chf)], ['Opex', 'CHF ' + fmt(h.opex_chf)], ['Computing income @ mid-market', 'CHF ' + fmt(h.compute_income_at_market_mid_chf)], ['Break-even price', `CHF ${fmt(h.breakeven_chf_per_server_h, 2)} / server-h`]]
      .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('')}</div>`;
}

/* ---------------- Policies ---------------- */
function renderPolicies() {
  const P = R.policies, labels = P.map(p => p.label.replace(' (data-centre style)', '').replace(' (heat-now only)', '').replace('HeatShift + deadline rescue', 'HS + rescue'));
  const colors = P.map(p => p.policy.startsWith('heatshift') ? C.sla : C.backup);
  const metrics = [
    ['Missed deadlines', p => p.fleet.missed_pct, '%', 2],
    ['Heat dumped (fleet)', p => p.fleet.dumped_heat_mwh, 'MWh', 0],
    ['Jobs rejected at admission', p => p.fleet.rejected_pct, '%', 1],
    ['Reference house heat covered', p => 100 * p.house.coverage, '%', 1],
  ];
  $('#policy-charts').innerHTML = metrics.map((m, i) => `<div class="card"><div class="chart-title">${m[0]} <span class="unit">(${m[2]})</span></div><div class="chart-box tiny"><canvas id="pc${i}"></canvas></div></div>`).join('');
  metrics.forEach((m, i) => new Chart($('#pc' + i), { type: 'bar', data: { labels, datasets: [{ data: P.map(m[1]), backgroundColor: colors, borderRadius: 4, maxBarThickness: 22 }] },
    options: { indexAxis: 'y', plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.raw, m[3])} ${m[2]}` } } },
      scales: { x: yAxis('', { grace: '22%' }), y: xAxis({ ticks: { color: C.text2, font: { size: 11 } } }) } },
    plugins: [{ id: 'vals', afterDatasetsDraw(ch) { const ctx = ch.ctx; ctx.save(); ctx.fillStyle = C.text2; ctx.font = '11px sans-serif';
      ch.getDatasetMeta(0).data.forEach((b, k) => ctx.fillText(fmt(ch.data.datasets[0].data[k], m[3]), b.x + 5, b.y + 4)); ctx.restore(); } }] }));
  $('#policy-note').innerHTML = `<b>Why not just run always-on?</b> Its lower cost per hour assumes buyers for 100 % of all server-hours, all year. Alibaba's production GPU cluster averaged only ${fmt(100 * R.alibaba.gpu_hours_per_gpu_day / 24)} % utilisation. It also needs a dry cooler in every basement and throws away ${fmt(R.policies.find(p => p.policy === 'always_on').fleet.dumped_heat_mwh)} MWh of heat a year. That's a data centre in a cellar, not a heating system.`;
  $('#policy-table').innerHTML = `<tr><th>Policy</th><th>How it decides</th><th class="num">Missed</th><th class="num">Rejected</th><th class="num">Dumped</th><th class="num">Fleet util.</th><th class="num">Migrated jobs</th></tr>` +
    P.map(p => `<tr class="${p.policy === 'heatshift' ? 'ours' : ''}"><td>${p.label}</td><td>${{
      always_on: 'Runs every job immediately at full power, like a data centre. Needs a dry cooler.',
      thermostat: 'Runs servers only while the house needs heat right now. FIFO, no tank, no forecast.',
      heatshift: 'Heat budget includes free tank space. Least-laxity first. Admission against a 36 h forecast. Moves jobs before they miss their deadline.',
      heatshift_rescue: 'Like HeatShift, plus a job about to miss its deadline may exceed the heat budget.' }[p.policy]}</td>
      <td class="num">${fmt(p.fleet.missed_pct, 2)} %</td><td class="num">${fmt(p.fleet.rejected_pct, 1)} %</td><td class="num">${fmt(p.fleet.dumped_heat_mwh, 1)} MWh</td><td class="num">${pct(p.fleet.utilisation, 0)}</td><td class="num">${fmt(p.fleet.migrated_jobs_pct, 1)} %</td></tr>`).join('');
}

/* ---------------- Economics ---------------- */
function renderEcon() {
  const S = R.sweep_servers, N = S.map(s => s.n), h = R.house;
  const lo = h.market_low, hi = h.market_high, mid = (lo + hi) / 2;
  const best = S.reduce((a, b) => b.profit_at_market_mid_chf > a.profit_at_market_mid_chf ? b : a);
  const cheapest = S.reduce((a, b) => b.breakeven_chf_per_server_h < a.breakeven_chf_per_server_h ? b : a);
  $('#econ-sub').textContent = `HeatShift run for 1–16 servers in the reference house, everything else unchanged. Lowest break-even: ${cheapest.n} servers at CHF ${fmt(cheapest.breakeven_chf_per_server_h, 2)}/h, covering ${pct(cheapest.coverage, 0)} of the heat. Highest profit: ${best.n} servers, covering ${pct(best.coverage, 0)}.`;
  new Chart($('#c-cover'), { type: 'line', data: { labels: N, datasets: [line('Heat covered', S.map(s => 100 * s.coverage), C.sla, { pointRadius: 3 }), line('Utilisation', S.map(s => 100 * s.utilisation), C.blue, { pointRadius: 3 })] },
    options: { scales: { x: xAxis({ title: { display: true, text: 'servers per house', color: C.muted } }), y: yAxis('%', { max: 105 }) } } });
  new Chart($('#c-breakeven'), { type: 'line', data: { labels: N, datasets: [
    line('Market high', N.map(() => hi), C.muted, { borderDash: [4, 4], borderWidth: 1 }),
    line('Market low', N.map(() => lo), C.muted, { borderDash: [4, 4], borderWidth: 1, fill: '-1', backgroundColor: 'rgba(240,239,236,.06)' }),
    line('Break-even (all costs)', S.map(s => s.breakeven_chf_per_server_h), C.sla, { pointRadius: 3 }),
    line('Break-even (electricity only)', S.map(s => s.marginal_breakeven_chf_per_server_h), C.backfill, { pointRadius: 3 }),
  ] }, options: { scales: { x: xAxis({ title: { display: true, text: 'servers per house', color: C.muted } }), y: yAxis('CHF / server-h') },
    plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: CHF ${fmt(c.raw, 2)}` } } } } });
  new Chart($('#c-profit'), { type: 'bar', data: { labels: N, datasets: [bar('Profit @ mid-market', S.map(s => s.profit_at_market_mid_chf), S.map(s => s.n === best.n ? C.sla : C.backup))] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `CHF ${fmt(c.raw)} / year` } } }, scales: { x: xAxis({ title: { display: true, text: 'servers per house', color: C.muted } }), y: yAxis('CHF/yr', { beginAtZero: false }) } } });
  const nb = R.no_backfill;
  const loss = S.filter(s => s.breakeven_chf_per_server_h > lo).map(s => s.n);
  const sens = [];
  $('#payoff-card').innerHTML = `<div class="verdict"><h3>Verdict for the reference house</h3>
    <p>With ${h.servers} servers, computing pays off if DigitalHeat sells a server-hour for at least <b>CHF ${fmt(h.breakeven_chf_per_server_h, 2)}</b>. The market for four Blackwell-class GPUs is CHF ${fmt(lo, 2)}–${fmt(hi, 2)}. <span class="pill ${h.breakeven_chf_per_server_h <= lo ? 'ok' : h.breakeven_chf_per_server_h <= mid ? 'mid' : 'no'}">${h.pays_off_verdict}</span></p>
    <h3>When it does not pay off</h3><ul>
      <li><b>Too many servers.</b> Above ${Math.min(...loss.filter(n => n > cheapest.n))} servers, break-even rises above the low market price, because the extra servers sit idle all summer.</li>
      <li><b>Low summer demand at the house.</b> July heat demand is only 12 kWh/day (hot water), enough for about 15 server-hours a day. Without fleet migration, ${h.servers} servers would be ${pct(1 - 15 / (24 * h.servers), 0)} idle in summer.</li>
      <li><b>Selling only spot capacity.</b> If all hours sold at the spot discount (×${fmt(R.assumptions.find(a => a.key === 'spot_price_ratio').value, 1)}), break-even would be CHF ${fmt(h.breakeven_chf_per_server_h / R.assumptions.find(a => a.key === 'spot_price_ratio').value, 2)}.</li>
      <li><b>Electricity alone is never the problem.</b> Running cost minus heat value is CHF ${fmt(h.marginal_breakeven_chf_per_server_h, 2)}/h. <b>Capex is ${pct((h.capex_annuity_chf + h.install_annuity_chf) / (h.electricity_cost_chf + h.capex_annuity_chf + h.install_annuity_chf + h.opex_chf), 0)} of total cost</b>, so utilisation decides profitability.</li>
    </ul></div>`;
}

/* ---------------- Fleet ---------------- */
function renderFleet() {
  const S = R.sites, seasonal = s => s.kind === 'sfh' || s.kind === 'mfh';
  const alpine = s => ['davos_hotel', 'zermatt_hotel', 'stmoritz_sfh'].includes(s.key);
  const groups = [
    ['Houses & apartment blocks (lowland, seasonal)', s => seasonal(s) && !alpine(s), C.sla],
    ['Indoor pool (year-round hot water)', s => s.kind === 'pool', C.blue],
    ['Alpine hotels & houses (cool summers)', s => alpine(s), C.tank],
  ];
  new Chart($('#c-fleet'), { type: 'bar', data: { labels: MONTHS, datasets: groups.map(([l, f, c]) =>
    bar(l, MONTHS.map((_, m) => S.filter(f).reduce((a, s) => a + (s.monthly_compute[m] || 0), 0)), c, { stack: 'f' })) },
    options: { scales: { x: xAxis({ stacked: true }), y: yAxis('server-h', { stacked: true }) }, plugins: { legend: { position: 'bottom' } } } });
  $('#site-table').innerHTML = `<tr><th>Site</th><th>Region</th><th class="num">Servers</th><th class="num">Heat MWh/yr</th><th class="num">Rp/kWh</th><th class="num">Covered</th><th class="num">Util.</th><th class="num">Break-even</th></tr>` +
    S.map(s => `<tr class="${s.key === 'basel_ref' ? 'ours' : ''}"><td>${s.name}</td><td>${s.region}</td><td class="num">${s.servers}</td><td class="num">${fmt(s.demand_mwh, 0)}</td><td class="num">${fmt(s.price_rp, 1)}</td><td class="num">${pct(s.coverage, 0)}</td><td class="num">${pct(s.utilisation, 0)}</td><td class="num">${fmt(s.breakeven, 2)}</td></tr>`).join('');
  const f = R.fleet;
  $('#fleet-kpis').innerHTML = `<div class="balance">${[['Fleet', `${f.sites} sites · ${f.servers} servers`], ['Deadline jobs', `${fmt(f.jobs_per_day)} / day`], ['Jobs migrated before missing a deadline', pct(f.migrated_jobs_pct / 100, 1)], ['Rejected (sent to partner cloud)', fmt(f.rejected_pct, 1) + ' %'],
    ['Missed deadlines', fmt(f.missed_pct, 2) + ' %'], ['Useful heat delivered', fmt(f.useful_heat_mwh) + ' MWh'], ['Heat dumped', fmt(f.dumped_heat_mwh, 1) + ' MWh'], ['Fleet heat covered', pct(f.coverage)]].map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('')}</div>`;
}

/* ---------------- Workloads ---------------- */
function renderEdge() {
  const h = R.house, H = R.holidays, f = R.fleet, n = R.no_outages, nb = R.no_backfill, resc = R.policies.find(p => p.policy === 'heatshift_rescue');
  const rows = [
    ['Server or internet outage', `${R.assumptions.find(a => a.key === 'outages_per_site_year').value} outages × ${R.assumptions.find(a => a.key === 'outage_hours').value} h per site (${fmt(f.outage_hours)} site-hours). Running jobs fail over to other sites from their last checkpoint.`,
      `${fmt(f.failovers)} automatic failovers · missed ${fmt(f.missed_pct, 2)} % with outages vs ${fmt(n.missed_pct, 2)} % without (no measurable impact, within run-to-run noise)`],
    ['Family on holiday (reference house)', 'Ski holidays in February and 3 weeks in July: heating set back to 16 °C, hot water at 10 %. The owner enters the dates in the app.',
      `demand ${fmt(h.heat_demand_kwh)} → ${fmt(H.heat_demand_kwh)} kWh · covered ${pct(H.coverage)} · missed ${fmt(H.missed_pct, 2)} %`],
    ['Tank full, no heat needed', 'The heat budget falls to the current demand. Jobs slow down (duty cycle), pause, or move. Shown live with the "Tank full now" button.', '0 kWh dumped all year'],
    ['Deadline about to be missed', 'Rescue mode: a job may exceed the heat budget, which needs a small dry cooler.', `missed ${fmt(resc.fleet.missed_pct, 2)} % for ${fmt(resc.fleet.dumped_heat_mwh, 1)} MWh dumped (fleet)`],
    ['Forecast wrong', 'Forecast = real temperature + AR(1) error, sd 1.5 °C. The planner trusts only 70 % of the forecast heat sink.', `missed ${fmt(f.missed_pct, 2)} %`],
    ['Non-checkpointable job', '5 % of jobs (evaluation runs) only start if the forecast guarantees heat for their whole runtime. After an outage they restart from zero.', 'included above'],
    ['No spot market for backfill', 'Only deadline jobs are sold.', `utilisation ${pct(h.utilisation, 0)} → ${pct(nb.utilisation, 0)} · break-even CHF ${fmt(nb.breakeven_chf_per_server_h, 2)}/h`],
    ['Cold snap beyond server capacity', `Peak ${fmt(R.reference_profile.peak_hour_kw, 1)} kW vs ${fmt(h.servers * 0.8, 1)} kW of server heat: the tank discharges first, then the backup heat pump.`, `backup ${fmt(h.backup_heat_kwh)} kWh/yr`],
    ['Alpine summer / Ticino winter', 'Every site uses its own local weather. St. Moritz still needs heat on 80 of 92 summer days; Lugano has only 158 heating days a year.', 'see map'],
  ];
  $('#edge-table').innerHTML = `<tr><th>Edge case</th><th>How HeatShift handles it</th><th>Result (2025 simulation)</th></tr>` + rows.map(r => `<tr><td><b>${r[0]}</b></td><td>${r[1]}</td><td class="num" style="text-align:left">${r[2]}</td></tr>`).join('');
}

async function renderWork() {
  const A = R.alibaba;
  $('#job-table').innerHTML = `<tr><th>Job type</th><th class="num">Share</th><th class="num">Slack</th><th>Pause?</th><th class="num">Output / kWh</th><th>Source</th></tr>` +
    R.job_types.map(j => `<tr><td>${j.label}</td><td class="num">${pct(j.share, 0)}</td><td class="num">${j.slack_h} h</td><td>${j.interruptible ? 'checkpoint' : 'no (atomic)'}</td><td class="num">${j.per_kwh ? human(j.per_kwh, j.unit) : '-'}</td><td class="src">${j.source.replace('mlperf:', 'MLPerf v6.1 · ').replace('measured:', 'measured here · ')}</td></tr>`).join('') +
    `<tr><td colspan="6" class="src">Job sizes and the daily arrival curve are resampled from the Alibaba PAI GPU trace 2020: ${fmt(A.tasks)} GPU tasks over ${A.days} days, ${fmt(A.tasks_per_gpu_day, 2)} tasks per GPU per day, median ${fmt(A.duration_h.p50 * 60)} min. Each job bundles 12 trace tasks into one customer batch. Demand = ${fmt(R.assumptions.find(a => a.key === 'demand_factor').value * 100)} % of the fleet's yearly heat-matched capacity, flat over the year. That gives the reference house ${fmt(R.house.jobs_per_day, 1)} deadline jobs per day.</td></tr>`;
  $('#output-table').innerHTML = `<tr><th>Job type</th><th class="num">Server-h</th><th class="num">Useful output</th></tr>` +
    R.useful_output.map(o => `<tr><td>${o.label}</td><td class="num">${fmt(o.server_h)}</td><td class="num">${human(o.output, o.unit)}</td></tr>`).join('') +
    `<tr><td>Spot backfill (evaluation sweeps, archive embedding, rendering)</td><td class="num">${fmt(R.house.backfill_server_h)}</td><td class="num">best effort</td></tr>`;
  const m = await (await fetch('/api/measured')).json().catch(() => null);
  if (m && m.image_tag) {
    $('#measured-table').innerHTML = `<tr><th>Job (${m.machine})</th><th class="num">Items/s</th><th class="num">W above idle</th><th class="num">J / item</th></tr>` +
      [['Image tagging · ResNet-50 · Imagenette', m.image_tag], ['Embeddings · MiniLM-L6 · SQuAD passages', m.embed]].map(([l, x]) =>
        `<tr><td>${l}</td><td class="num">${fmt(x.items_per_s, 1)}</td><td class="num">${fmt(x.incremental_w, 1)}</td><td class="num">${fmt(x.j_per_item_incremental, 2)}</td></tr>`).join('') +
      `<tr><td colspan="4" class="src">Idle ${fmt(m.idle_w, 1)} W. Whole-system power from the Mac battery controller (${m.method}). Measured ${m.measured_at}.</td></tr>`;
  } else $('#measured-table').innerHTML = '<tr><td class="src">run <code>make bench</code> to measure</td></tr>';
}

function renderAssumptions() {
  $('#assume-table').innerHTML = `<tr><th>Section</th><th>Parameter</th><th class="num">Value</th><th>Unit</th><th>Source / reasoning</th></tr>` +
    R.assumptions.map(a => `<tr><td>${a.section}</td><td>${a.key}</td><td class="num">${typeof a.value === 'number' ? (a.key.endsWith('year') ? a.value : fmt(a.value, a.value < 10 && a.value % 1 ? 3 : 0)) : a.value}</td><td>${a.unit || ''}</td><td class="src">${a.source}</td></tr>`).join('');
}

/* ---------------- Live ---------------- */
let liveChart, tankChart, pollTimer, selectedDay;
function setupLive() {
  const D = R.days;
  const days = [['Cold January day', D.january], ['Shoulder day (April)', D.april], ['Hot July day', D.july]];
  selectedDay = D.january;
  $('#day-buttons').innerHTML = days.map(([l, d], i) => `<button data-day="${d}" class="${i === 0 ? 'on' : ''}">${l}<br><small>${d}</small></button>`).join('');
  document.querySelectorAll('#day-buttons button').forEach(b => b.onclick = () => {
    document.querySelectorAll('#day-buttons button').forEach(x => x.classList.remove('on')); b.classList.add('on'); selectedDay = b.dataset.day; previewDay(); });
  // rack
  $('#rack').innerHTML = Array.from({ length: 8 }, (_, i) => `<rect class="srv" id="srv${i}" x="${70 + (i % 4) * 33}" y="${252 + Math.floor(i / 4) * 18}" width="28" height="13" rx="2"/>`).join('') +
    `<text x="135" y="290" class="svg-label" text-anchor="middle">8 × 1 kW SERVERS</text>`;
  $('#windows').innerHTML = [[85, 140], [155, 140], [225, 140], [85, 185], [155, 185], [225, 185]].map(([x, y]) => `<rect class="win" x="${x}" y="${y}" width="46" height="30" rx="2"/>`).join('');
  const labels = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}:00`);
  liveChart = new Chart($('#c-live'), { type: 'bar', data: { labels, datasets: [
    bar('Deadline jobs', [], C.sla, { stack: 's' }), bar('Real job (this laptop)', [], C.real, { stack: 's' }), bar('Spot backfill', [], C.backfill, { stack: 's' }),
    line('Heat demand ÷ 0.8 (kW of servers)', [], C.text, { stepped: 'middle', tension: 0, stack: 'l1' }),
    line('Heat budget (incl. free tank space)', [], C.muted, { borderDash: [4, 3], borderWidth: 1.5, stepped: 'middle', tension: 0, stack: 'l2' }),
  ] }, options: { animation: { duration: 250 }, scales: { x: xAxis({ stacked: true, ticks: { maxTicksLimit: 12 } }), y: yAxis('kW', { stacked: true, suggestedMax: 9 }) },
    plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmt(c.raw, 2)} kW` } } } } });
  tankChart = new Chart($('#c-live-tank'), { type: 'line', data: { labels, datasets: [line('Tank', [], C.tank, { fill: 'origin', backgroundColor: 'rgba(25,158,112,.25)', tension: 0.2 })] },
    options: { plugins: { legend: { display: false } }, scales: { x: xAxis({ ticks: { maxTicksLimit: 12 } }), y: yAxis('kWh', { max: 35 }) } } });
  $('#btn-start').onclick = async () => { await post('/api/live/start', { day: selectedDay, job: $('#job').value, sec_per_hour: +$('#speed').value }); startPolling(); };
  $('#btn-stop').onclick = () => post('/api/live/stop', {});
  $('#btn-tankfull').onclick = () => post('/api/live/override', { tank_full: true });
  document.querySelectorAll('[data-hf]').forEach(b => b.onclick = () => post('/api/live/override', { heat_factor: +b.dataset.hf }));
  $('#btn-migrate').onclick = () => post('/api/live/migrate', { target: 'davos_hotel' });
  previewDay();
  startPolling();
}
const post = (u, b) => fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });

function previewDay() {   // show what the year simulation did that day, before running live
  const i0 = R.hourly.time.findIndex(t => t.startsWith(selectedDay));
  if (i0 < 0) return;
  const sl = k => R.hourly[k].slice(i0, i0 + 24);
  liveChart.data.datasets[0].data = []; liveChart.data.datasets[1].data = []; liveChart.data.datasets[2].data = [];
  liveChart.data.datasets[3].data = sl('demand').map(x => x / 0.8);
  liveChart.data.datasets[4].data = [];
  liveChart.update();
  tankChart.data.datasets[0].data = []; tankChart.update();
  const t = R.hourly.temp_c.slice(i0, i0 + 24);
  $('#svg-temp').textContent = `${fmt(Math.min(...t), 0)} … ${fmt(Math.max(...t), 0)} °C outside`;
  $('#svg-hour').textContent = selectedDay;
}

function startPolling() { clearInterval(pollTimer); pollTimer = setInterval(poll, 500); poll(); }
async function poll() {
  const s = await (await fetch('/api/live/state')).json();
  const H = s.history || [];
  if (H.length) {
    const pad = a => a.concat(Array(24 - a.length).fill(null));
    liveChart.data.datasets[0].data = pad(H.map(r => r.sla));
    liveChart.data.datasets[1].data = pad(H.map(r => r.real));
    liveChart.data.datasets[2].data = pad(H.map(r => r.backfill));
    liveChart.data.datasets[4].data = pad(H.map(r => r.budget));
    const i0 = R.hourly.time.findIndex(t => t.startsWith(s.day));
    liveChart.data.datasets[3].data = H.map(r => r.demand / 0.8).concat(R.hourly.demand.slice(i0 + H.length, i0 + 24).map(x => x / 0.8));
    liveChart.update('none');
    tankChart.data.datasets[0].data = pad(H.map(r => r.soc)); tankChart.update('none');
    const r = s.row;
    if (r) {
      $('#svg-temp').textContent = `${fmt(r.temp_c, 1)} °C outside`;
      $('#svg-hour').textContent = `${s.day} ${r.label}`;
      let a = r.sla, b = r.real, c = r.backfill;
      for (let i = 0; i < 8; i++) {
        const el = $('#srv' + i); el.setAttribute('class', 'srv');
        if (a >= 0.5) { el.classList.add('sla'); a -= 1; } else if (b >= 0.5) { el.classList.add('real'); b -= 1; } else if (c >= 0.5) { el.classList.add('bf'); c -= 1; }
      }
      $('#tank-fill').setAttribute('height', 38 * r.soc / 35); $('#tank-fill').setAttribute('y', 286 - 38 * r.soc / 35);
      document.querySelectorAll('.win').forEach((w, i) => w.classList.toggle('warm', r.heat > i * 1.2));
      $('#pipe').classList.toggle('hot', r.compute > 0.05);
      $('#house-stats').innerHTML = [['Heat demand', fmt(r.demand, 1) + ' kWh/h'], ['Heat budget', fmt(r.budget, 1) + ' kW servers'], ['Server heat', fmt(r.heat, 1) + ' kW'], ['Backup heat', fmt(r.backup, 1) + ' kW'],
        ['Tank', `${fmt(r.soc, 1)} / 35 kWh`], ['Heat dumped', fmt(r.dump, 1) + ' kWh'], ['Queue', r.queue + ' jobs'], ['Laptop power', s.watts ? fmt(s.watts, 1) + ' W' : '-']]
        .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('');
    }
  }
  const j = s.real;
  if (j) {
    const st = $('#job-status'); st.className = 'status ' + j.status; st.textContent = j.status.toUpperCase() + (j.server ? ' · ' + j.server : '');
    $('#job-bar').style.width = (100 * j.at / (j.total || 1)) + '%';
    $('#job-meta').textContent = `${fmt(j.at)} / ${fmt(j.total)} items · ${fmt(j.items_per_s, 1)} items/s · pid ${j.pid ?? '-'}`;
    $('#job-sample').textContent = j.sample || '';
  }
  $('#log').innerHTML = (s.events || []).map(e => `<li class="${/PAUSED|RESUMED|CHECKPOINT|MIGRATE|STARTED|OVERRIDE/.test(e.msg) ? 'hl' : ''}"><b>${e.sim || e.t}</b>${e.msg}</li>`).join('');
}

loadResults();

/* ---------------- Swiss map ---------------- */
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const RAMP = ['#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#5598e7', '#6da7ec', '#86b6ef', '#9ec5f4', '#b7d3f6', '#cde2fb'];
const ALPINE = ['davos_hotel', 'zermatt_hotel', 'stmoritz_sfh'];
const proj = (lon, lat) => [((lon - 5.9) * Math.cos(46.8 * Math.PI / 180)) * 277 + 6, (47.86 - lat) * 277 + 6];
let mapMonth = 0, mapLayer = 'summer', mapTimer = null;

async function renderMap() {
  const geo = await (await fetch('geo/cantons.json')).json();
  const svg = $('#ch-map');
  const path = rings => rings.map(r => 'M' + r.map(([x, y]) => proj(x, y).map(v => v.toFixed(1)).join(',')).join('L') + 'Z').join('');
  let html = '<g id="cantons">' + geo.features.map(f => {
    const polys = f.geometry.type === 'MultiPolygon' ? f.geometry.coordinates : [f.geometry.coordinates];
    return `<path class="canton" d="${polys.map(path).join('')}"><title>${f.properties.name}</title></path>`;
  }).join('') + '</g><g id="grid"></g><g id="glows"></g><g id="sites"></g><g id="labels"></g>';
  svg.innerHTML = html;
  // sites
  const S = R.sites, maxS = Math.max(...S.map(s => s.servers));
  const drawOrder = [...S.filter(s => s.key !== 'basel_ref'), S.find(s => s.key === 'basel_ref')];   // reference house on top
  $('#sites').innerHTML = drawOrder.map(s => { const [x, y] = proj(s.lon, s.lat); const r = 4 + 9 * Math.sqrt(s.servers / maxS);
    return `<circle class="site-dot ${s.key === 'basel_ref' ? 'ref' : ''}" data-key="${s.key}" cx="${x}" cy="${y}" r="${r}"/>`; }).join('');
  $('#glows').innerHTML = S.map(s => { const [x, y] = proj(s.lon, s.lat); return `<circle class="site-glow" id="glow-${s.key}" cx="${x}" cy="${y}" r="0" opacity=".35"/>`; }).join('');
  const off = { basel_mfh: [-12, 22, 'end'], riehen_sfh: [12, -10], basel_ref: [-16, -10, 'end'], liestal_mfh: [8, 24], aarau_pool: [0, 30, 'middle'], zurich_mfh: [16, 4], zermatt_hotel: [14, 4], stmoritz_sfh: [12, 14] };
  $('#labels').innerHTML = S.map(s => { const [x, y] = proj(s.lon, s.lat); const o = off[s.key] || [12, 4];
    const short = s.name.replace(' (1,560 m)', '').replace(' (1,822 m)', '').replace(' (1,608 m)', '').replace('Apartment block', 'Block').replace(' (8 flats)', '');
    return `<text class="site-label ${s.key === 'basel_ref' ? 'ref' : ''}" x="${x + o[0]}" y="${y + o[1]}" text-anchor="${o[2] || 'start'}">${short}</text>`; }).join('');
  // tooltips
  const tip = $('#map-tip');
  document.querySelectorAll('.site-dot').forEach(el => {
    el.onmousemove = ev => { const s = S.find(x => x.key === el.dataset.key); const box = svg.closest('.map-card').getBoundingClientRect();
      tip.hidden = false; tip.style.left = (ev.clientX - box.left + 14) + 'px'; tip.style.top = (ev.clientY - box.top + 14) + 'px';
      tip.innerHTML = `<b>${s.name}</b><br>${s.region} · ${s.servers} servers · tank ${fmt(s.tank_kwh)} kWh<br>
        Electricity <b>${fmt(s.price_rp, 2)} Rp/kWh</b> (ElCom 2026)<br>Heat demand ${fmt(s.demand_mwh)} MWh/yr · covered <b>${pct(s.coverage, 0)}</b><br>
        Utilisation ${pct(s.utilisation, 0)} · break-even <b>CHF ${fmt(s.breakeven, 2)}/h</b><br>
        ${MONTH_NAMES[mapMonth]}: ${fmt(s.monthly_compute[mapMonth])} server-h of computing<br>Outages simulated: ${s.outage_hours} h ${s.holidays ? '<br>Family on holiday Feb + Jul' : ''}`; };
    el.onmouseleave = () => tip.hidden = true;
  });
  // controls
  $('#map-month').oninput = e => setMonth(+e.target.value);
  $('#map-play').onclick = () => {
    if (mapTimer) { clearInterval(mapTimer); mapTimer = null; $('#map-play').textContent = '▶ Play the year'; return; }
    $('#map-play').textContent = '❚❚ Pause';
    mapTimer = setInterval(() => setMonth((mapMonth + 1) % 12), 900);
  };
  document.querySelectorAll('#map-layer button').forEach(b => b.onclick = () => {
    document.querySelectorAll('#map-layer button').forEach(x => x.classList.remove('on')); b.classList.add('on'); mapLayer = b.dataset.layer; drawGrid(); });
  // price chart (one series, reference house emphasised)
  const P = [...S].sort((a, b) => b.price_rp - a.price_rp);
  new Chart($('#c-prices'), { type: 'bar', data: { labels: P.map(s => s.name.replace(/ \(.*\)/, '').replace('Apartment block', 'Block')),
    datasets: [{ data: P.map(s => s.price_rp), backgroundColor: P.map(s => s.key === 'basel_ref' ? C.sla : C.backup), borderRadius: 4, maxBarThickness: 16 }] },
    options: { indexAxis: 'y', plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${fmt(c.raw, 2)} Rp/kWh · break-even CHF ${fmt(P[c.dataIndex].breakeven, 2)}/h` } } },
      scales: { x: yAxis('', { grace: '10%' }), y: xAxis({ ticks: { font: { size: 11 } } }) } } });
  const best = [...S].sort((a, b) => a.breakeven - b.breakeven), ref = S.find(s => s.key === 'basel_ref');
  const g = R.heat_grid, cold = g.filter(p => p.summer_heating_days >= 30);
  $('#map-insights').innerHTML = `<h3 style="margin-top:14px">What the map tells DigitalHeat</h3><ul>
    <li><b>Basel has the most expensive power in the fleet</b> (${fmt(ref.price_rp, 2)} Rp vs ${fmt(Math.min(...S.map(s => s.price_rp)), 2)} Rp in ${S.reduce((a, b) => a.price_rp < b.price_rp ? a : b).name.replace(/ \(.*\)/, '')}).</li>
    <li><b>Best sites:</b> ${best.slice(0, 3).map(s => `${s.name.replace(/ \(.*\)/, '')} (CHF ${fmt(s.breakeven, 2)}/h)`).join(', ')}. They need heat all year, and power is cheap. The reference house breaks even at CHF ${fmt(ref.breakeven, 2)}/h.</li>
    <li><b>${cold.length} of ${g.length}</b> scored locations still need heating on 30+ summer days, mostly in Graubünden and Valais. That's room for summer computing beyond today's fleet.</li></ul>`;
  drawGrid(); setMonth(0);
}

function drawGrid() {
  const g = R.heat_grid, key = mapLayer === 'summer' ? 'summer_heating_days' : 'heating_days', max = mapLayer === 'summer' ? 92 : 365;
  $('#grid').innerHTML = g.map(p => { const [x, y] = proj(p.lon, p.lat); const v = p[key] / max;
    const c = RAMP[Math.min(RAMP.length - 1, Math.round(v * (RAMP.length - 1)))];
    return `<circle class="gridpt" cx="${x}" cy="${y}" r="${3 + 6 * v}" fill="${c}" opacity="${0.25 + 0.6 * v}"><title>${p.canton}, ${fmt(p.elev)} m: ${p.heating_days} heating days, ${p.summer_heating_days} in summer, mean ${fmt(p.mean_c, 1)} °C</title></circle>`; }).join('');
  $('#map-legend').innerHTML = `<span><span class="ramp">${RAMP.filter((_, i) => i % 2 === 0).map(c => `<i style="background:${c}"></i>`).join('')}</span>
    ${mapLayer === 'summer' ? 'summer days (Jun–Aug) that still need heating: 0 → 92' : 'heating days per year: 0 → 365'}</span>
    <span><svg width="14" height="14"><circle cx="7" cy="7" r="6" fill="${C.sla}"/></svg> DigitalHeat site (size = servers, glow = computing this month)</span>
    <span><svg width="14" height="14"><circle cx="7" cy="7" r="6" fill="${C.accent}"/></svg> reference house</span>`;
}

function setMonth(m) {
  mapMonth = m; $('#map-month').value = m; $('#map-month-label').textContent = MONTH_NAMES[m];
  const S = R.sites, maxC = Math.max(...S.flatMap(s => s.monthly_compute));
  S.forEach(s => { const el = $('#glow-' + s.key); const v = s.monthly_compute[m] / maxC;
    el.setAttribute('r', 6 + 34 * Math.sqrt(v)); el.setAttribute('opacity', (0.12 + 0.5 * v).toFixed(2)); });
  const tot = S.reduce((a, s) => a + s.monthly_compute[m], 0);
  const alp = S.filter(s => ALPINE.includes(s.key) || s.kind === 'pool').reduce((a, s) => a + s.monthly_compute[m], 0);
  const ref = S.find(s => s.key === 'basel_ref');
  $('#map-month-stats').innerHTML = [['Fleet computing', fmt(tot) + ' server-h'], ['…at pools & alpine sites', pct(alp / tot, 0)],
    ['Reference house computing', fmt(ref.monthly_compute[m]) + ' server-h'], ['Reference house heat demand', fmt(ref.monthly_demand[m]) + ' kWh']]
    .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('');
}
