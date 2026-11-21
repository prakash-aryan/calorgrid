export function initHud(sceneBridge) {
  const state = {
    strategy: "smart",
    servers: 4,
    site: "basel",
    date: "2025-01-15",
    hourIndex: 12,
    xray: false,
    provenanceActive: false,
    sensitivityOpen: false,
    verbundSceneId: "basel_erdsonde",
    isVerbundMode: false,
    activePreset: "scorecard",
    presetsById: {},
    dayData: null,
    annualData: null,
    provenanceData: null,
    verbundData: null,
    sensitivityData: null,
    workerStatus: null,
  };

  const el = {
    healthBadge: document.querySelector("[data-testid='api-health-badge']"),
    strategySelect: document.querySelector("[data-testid='strategy-select']"),
    serversSelect: document.querySelector("[data-testid='servers-select']"),
    siteSelect: document.querySelector("[data-testid='site-select']"),
    toggleXray: document.querySelector("[data-testid='toggle-xray']"),
    toggleProvenance: document.querySelector("[data-testid='toggle-provenance']"),
    toggleSensitivity: document.querySelector("[data-testid='toggle-sensitivity']"),
    togglePanels: document.querySelector("[data-testid='toggle-panels']"),
    togglePanelsMobile: document.querySelector("[data-testid='toggle-panels-mobile']"),
    toggleMobileControls: document.querySelector("[data-testid='toggle-mobile-controls']"),
    panelCollapseBtns: Array.from(document.querySelectorAll("[data-panel-toggle]")),
    mobileDockBtns: Array.from(document.querySelectorAll("[data-mobile-panel]")),
    presetButtons: Array.from(document.querySelectorAll("[data-preset]")),
    scorecardMeta: document.getElementById("scorecard-meta"),
    kpiServers: document.querySelector("[data-testid='kpi-servers-per-house']"),
    kpiHeatCovered: document.querySelector("[data-testid='kpi-heat-covered']"),
    kpiHeatDumped: document.querySelector("[data-testid='kpi-heat-dumped']"),
    kpiUtil: document.querySelector("[data-testid='kpi-server-utilisation']"),
    kpiMissed: document.querySelector("[data-testid='kpi-missed-deadlines']"),
    kpiElecCost: document.querySelector("[data-testid='kpi-electricity-cost']"),
    kpiIncome: document.querySelector("[data-testid='kpi-computing-income']"),
    kpiBreakEven: document.querySelector("[data-testid='kpi-break-even']"),
    kpiHeatingDaysSub: document.getElementById("kpi-heating-days-sub"),
    kpiCoveredSub: document.getElementById("kpi-heat-covered-sub"),
    kpiDumpedSub: document.getElementById("kpi-heat-dumped-sub"),
    kpiHoursSub: document.getElementById("kpi-server-hours-sub"),
    kpiJobsSub: document.getElementById("kpi-jobs-sub"),
    kpiHeatValSub: document.getElementById("kpi-heat-value-sub"),
    kpiResidualSub: document.getElementById("kpi-residual-sub"),
    negBanner: document.querySelector("[data-testid='negative-payoff-banner']"),
    dayPicker: document.querySelector("[data-testid='day-picker']"),
    hourSlider: document.querySelector("[data-testid='hour-slider']"),
    hourLabel: document.getElementById("hour-label"),
    telemetryTs: document.getElementById("telemetry-timestamp"),
    dstNote: document.getElementById("dst-note"),
    readTankSoc: document.querySelector("[data-testid='readout-tank-soc']"),
    readWaterTemps: document.querySelector("[data-testid='readout-water-temps']"),
    readOutdoorTemp: document.querySelector("[data-testid='readout-outdoor-temp']"),
    readBasementAir: document.querySelector("[data-testid='readout-basement-air-temp']"),
    readHeatDump: document.querySelector("[data-testid='readout-heat-dump']"),
    workerJobSelect: document.querySelector("[data-testid='worker-job-select']"),
    workerStartBtn: document.querySelector("[data-testid='worker-start-btn']"),
    workerPauseBtn: document.querySelector("[data-testid='worker-pause-btn']"),
    workerResumeBtn: document.querySelector("[data-testid='worker-resume-btn']"),
    workerStopBtn: document.querySelector("[data-testid='worker-stop-btn']"),
    workerSignalBtn: document.querySelector("[data-testid='worker-heat-signal-btn']"),
    workerState: document.querySelector("[data-testid='worker-state']"),
    workerPid: document.querySelector("[data-testid='worker-pid']"),
    workerScaled: document.querySelector("[data-testid='worker-scaled-throughput']"),
    workerEnergy: document.getElementById("worker-energy-readout"),
    workerCaption: document.getElementById("worker-arrival-caption"),
    verbundSelect: document.querySelector("[data-testid='verbund-scene-select']"),
    verbundAshrae: document.getElementById("verbund-ashrae-badge"),
    verbundDumpElim: document.querySelector("[data-testid='verbund-dump-elimination']"),
    verbundValveSegPrimary: document.getElementById("valve-seg-primary"),
    verbundValveSegSec: document.getElementById("valve-seg-secondary"),
    verbundValveSegDump: document.getElementById("valve-seg-dump"),
    verbundValveText: document.getElementById("valve-split-text"),
    verbundTbody: document.getElementById("verbund-comparison-tbody"),
    verbundCitation: document.getElementById("verbund-citation"),
    provenanceLegend: document.querySelector("[data-testid='provenance-legend']"),
    sensPanel: document.querySelector("[data-testid='sensitivity-panel']"),
    closeSensBtn: document.getElementById("close-sensitivity-btn"),
    sensHeatwise: document.querySelector("[data-testid='sens-heatwise']"),
    sensMlperf: document.querySelector("[data-testid='sens-mlperf']"),
    sensAshrae: document.querySelector("[data-testid='sens-ashrae']"),
    sensCapex: document.querySelector("[data-testid='sens-capex']"),
    sensTariff: document.querySelector("[data-testid='sens-tariff']"),
    footer: document.querySelector("[data-testid='attribution-footer']"),
  };

  async function fetchJson(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status} for ${url}`);
    }
    return resp.json();
  }

  function renderHealth(health) {
    if (!el.healthBadge) return;
    const ok = health?.status === "ok";
    el.healthBadge.textContent = ok ? "API OK" : "API DEGRADED";
    el.healthBadge.classList.toggle("is-degraded", !ok);
  }

  function renderAnnual(annual) {
    state.annualData = annual;
    const k = annual?.kpis;
    if (!k) return;

    const siteTitle = annual.site === "davos" ? "Davos 2025" : "Basel 2025";
    if (el.scorecardMeta) {
      el.scorecardMeta.textContent = `${siteTitle} | ${annual.strategy}`;
    }

    el.kpiServers.textContent = String(k.servers_per_house);
    el.kpiHeatCovered.textContent = `${(k.heat_demand_covered_share * 100).toFixed(1)}%`;
    // Single-number purity required by E2E float parsers
    el.kpiHeatDumped.textContent = `${k.heat_dumped_kwh.toFixed(1)} kWh`;
    el.kpiUtil.textContent = `${(k.server_utilisation * 100).toFixed(1)}%`;
    el.kpiMissed.textContent = `${k.missed_deadlines_pct.toFixed(1)}%`;
    el.kpiElecCost.textContent = `CHF ${k.electricity_cost_chf.toFixed(2)}`;
    el.kpiIncome.textContent = `CHF ${k.computing_income_chf.toFixed(2)}`;
    el.kpiBreakEven.textContent = k.break_even_defined
      ? `CHF ${k.break_even_computing_price_chf_per_server_hour.toFixed(4)}/srv-h`
      : "N/A (0 srv-h)";

    if (el.kpiHeatingDaysSub) {
      el.kpiHeatingDaysSub.textContent = `${annual.heating_days_count} heating days`;
    }
    if (el.kpiCoveredSub) {
      el.kpiCoveredSub.textContent = `${k.heat_used_kwh.toFixed(0)} / ${k.heat_demand_total_kwh.toFixed(0)} kWh`;
    }
    if (el.kpiDumpedSub) {
      const sharePct = (k.heat_dumped_share * 100).toFixed(1);
      el.kpiDumpedSub.textContent = `${sharePct}% of recovered heat`;
    }
    if (el.kpiHoursSub) {
      el.kpiHoursSub.textContent = `${k.server_hours_sold.toFixed(0)} srv-h`;
    }
    if (el.kpiJobsSub) {
      el.kpiJobsSub.textContent = `${k.total_jobs_completed}/${k.total_jobs_arrived} jobs`;
    }
    if (el.kpiHeatValSub) {
      el.kpiHeatValSub.textContent = `Heat credit: CHF ${k.heat_value_used_chf.toFixed(2)}`;
    }
    if (el.kpiResidualSub) {
      el.kpiResidualSub.textContent = `Residual ${annual.conservation_residual_kwh.toExponential(1)} kWh`;
    }

    el.negBanner.textContent = annual.negative_payoff_statement || "";
  }

  function renderHour(index) {
    const hours = state.dayData?.hours;
    if (!Array.isArray(hours) || hours.length === 0) return;

    const maxIdx = hours.length - 1;
    el.hourSlider.max = String(maxIdx);
    const idx = Math.max(0, Math.min(index, maxIdx));
    state.hourIndex = idx;
    el.hourSlider.value = String(idx);
    if (el.hourLabel) el.hourLabel.textContent = String(idx);

    const row = hours[idx];
    if (el.telemetryTs) {
      el.telemetryTs.textContent = row.timestamp_zurich || `${state.date} ${idx}:00`;
    }

    el.readTankSoc.textContent = `${row.tank_soc_kwh.toFixed(1)} / 35.0 kWh (${row.tank_soc_pct.toFixed(0)}%, ${row.tank_temp_c.toFixed(1)} C)`;
    el.readWaterTemps.textContent = `Supply ${row.supply_temp_c.toFixed(1)} C / Return ${row.return_temp_c.toFixed(1)} C (${row.loop_flow_l_per_min.toFixed(1)} L/min)`;
    el.readOutdoorTemp.textContent = `${row.outdoor_temp_c.toFixed(1)} C`;
    el.readBasementAir.textContent = `${row.basement_air_temp_c.toFixed(2)} C`;
    // Single-number purity required by E2E readout-heat-dump assertion
    el.readHeatDump.textContent = `${row.heat_dumped_kw.toFixed(2)} kW`;

    if (el.dstNote) {
      if (state.dayData.is_dst_transition && state.dayData.dst_note) {
        el.dstNote.textContent = state.dayData.dst_note;
        el.dstNote.hidden = false;
      } else {
        el.dstNote.hidden = true;
      }
    }

    sceneBridge.updateThermalAndSolar(row, state.dayData, {
      verbundScene: state.verbundData,
      isVerbundMode: state.isVerbundMode,
    });
  }

  function renderWorkerStatus(status) {
    if (!status) return;
    state.workerStatus = status;
    const st = String(status.state || "idle").toLowerCase();
    el.workerState.textContent = st.toUpperCase();
    el.workerState.className = `state-pill state-${st}`;

    el.workerPid.textContent = status.pid !== null && status.pid !== undefined ? String(status.pid) : "-";

    const scaledRate = status.scaled_1kw?.scaled_units_per_server_hour;
    if (scaledRate !== null && scaledRate !== undefined) {
      el.workerScaled.textContent = `${Number(scaledRate).toFixed(1)} units/1kW-h`;
    } else if (st === "idle") {
      el.workerScaled.textContent = "idle";
    } else {
      el.workerScaled.textContent = "measuring...";
    }

    if (el.workerEnergy) {
      el.workerEnergy.textContent = `${Number(status.measured_energy_j || 0).toFixed(1)} J | ${status.units_completed ?? 0} units`;
    }
    if (el.workerCaption && status.arrival_rate_source) {
      el.workerCaption.textContent = `1 kW scaling (${status.energy_meter_method || "psutil"}); ${status.arrival_rate_source}`;
    }
  }

  function renderVerbundScene(scene) {
    if (!scene) return;
    state.verbundData = scene;

    if (el.verbundAshrae) {
      el.verbundAshrae.textContent = `${scene.ashrae_class} | ${scene.primary_flow_l_per_min} L/min`;
    }

    const elim = scene.continuous_mode?.dump_elimination_vs_basel_house_pct ?? 0;
    const dumpKwh = scene.continuous_mode?.heat_dumped_kwh ?? 0;
    el.verbundDumpElim.textContent = `${Number(elim).toFixed(1)}% dump eliminated (${Number(dumpKwh).toFixed(0)} kWh/yr dumped)`;

    const valve = scene.three_way_valve || {};
    const pPct = (Number(valve.primary_fraction ?? 1) * 100).toFixed(1);
    const sPct = (Number(valve.secondary_fraction ?? 0) * 100).toFixed(1);
    const dPct = (Number(valve.dump_fraction ?? 0) * 100).toFixed(1);

    el.verbundValveSegPrimary.style.width = `${pPct}%`;
    el.verbundValveSegSec.style.width = `${sPct}%`;
    el.verbundValveSegDump.style.width = `${dPct}%`;

    const secFlow =
      scene.secondary_flow_l_per_min !== null && scene.secondary_flow_l_per_min !== undefined
        ? ` | Sec ${scene.secondary_flow_l_per_min} L/min`
        : "";
    el.verbundValveText.textContent = `Primary ${pPct}% | Sink ${sPct}% | Dump ${dPct}% | Prim ${scene.primary_flow_l_per_min} L/min${secFlow}`;

    const cmp = scene.separate_vs_paired_comparison || {};
    const rows = [
      {
        label: "Basel Separate House",
        util: cmp.basel_single_house_heat_led_util_pct,
        dump: cmp.basel_single_house_continuous_dump_kwh,
        cost: "-",
      },
    ];
    if (cmp.standalone_seasonal_pool_dump_kwh !== null && cmp.standalone_seasonal_pool_dump_kwh !== undefined) {
      rows.push({
        label: "Standalone Pool",
        util: cmp.standalone_seasonal_pool_heat_led_util_pct,
        dump: cmp.standalone_seasonal_pool_dump_kwh,
        cost: "-",
      });
    }
    rows.push({
      label: scene.scene_id,
      util: cmp.this_scene_heat_led_util_pct,
      dump: cmp.this_scene_continuous_dump_kwh,
      cost: scene.heat_led_mode?.total_break_even_chf_per_srv_h_central,
    });

    el.verbundTbody.innerHTML = rows
      .map(
        (r) => `<tr>
          <td>${r.label}</td>
          <td class="mono">${r.util !== null && r.util !== undefined ? Number(r.util).toFixed(1) + "%" : "-"}</td>
          <td class="mono">${r.dump !== null && r.dump !== undefined ? Number(r.dump).toFixed(0) : "-"}</td>
          <td class="mono">${typeof r.cost === "number" ? r.cost.toFixed(4) : r.cost}</td>
        </tr>`
      )
      .join("");

    if (el.verbundCitation) {
      el.verbundCitation.textContent = scene.source_citation || "";
    }
  }

  function renderSensitivity(sens) {
    if (!sens) return;
    state.sensitivityData = sens;

    const hw = sens.heatwise;
    const hwRows = (hw.sigstop_idle_penalty_sweep || [])
      .map(
        (r) => `<tr>
          <td>${r.band}</td>
          <td class="mono">${r.idle_to_full_ratio.toFixed(3)}</td>
          <td class="mono">${r.adjusted_electricity_cost_chf.toFixed(0)}</td>
          <td class="mono">${r.adjusted_break_even_chf_per_srv_h.toFixed(4)}</td>
        </tr>`
      )
      .join("");
    const recRows = (hw.recovery_fraction_sweep || [])
      .map(
        (r) => `<tr>
          <td class="mono">${(r.recovery_fraction * 100).toFixed(0)}%</td>
          <td>${r.note}</td>
          <td class="mono">${r.opex_break_even_floor_chf_per_srv_h.toFixed(4)}</td>
        </tr>`
      )
      .join("");
    el.sensHeatwise.innerHTML = `
      <h3>HEATWISE Idle &amp; Capture Sweep (Zenodo ${hw.zenodo_record})</h3>
      <p>Idle-to-full ratio: <strong>${hw.idle_to_full_ratio_range.min.toFixed(3)} to ${hw.idle_to_full_ratio_range.max.toFixed(3)}</strong> | Air vs hybrid slowdown: <strong>${hw.throttling_comparison.air_frequency_slowdown_pct}%</strong></p>
      <table class="compact-table">
        <thead><tr><th>Band</th><th>Idle Ratio</th><th>Elec CHF</th><th>Break-Even</th></tr></thead>
        <tbody>${hwRows}</tbody>
      </table>
      <table class="compact-table" style="margin-top:6px">
        <thead><tr><th>Capture</th><th>Cooling Path</th><th>OPEX Floor</th></tr></thead>
        <tbody>${recRows}</tbody>
      </table>
      <p>${hw.caveat}</p>
    `;

    const w45 = sens.ashrae_w45;
    el.sensAshrae.innerHTML = `
      <h3>ASHRAE W45 Temperature Limit</h3>
      <p>Supply cap: <strong>${w45.ashrae_w45_supply_limit_c} C</strong> (tank ${w45.tank_temp_min_c} to ${w45.tank_temp_max_c} C)</p>
      <table class="compact-table">
        <thead><tr><th>Tank Model</th><th>Capacity</th><th>Util %</th><th>Covered %</th></tr></thead>
        <tbody>
          <tr><td>Stratified</td><td class="mono">${w45.stratified_effective_capacity_kwh} kWh</td><td class="mono">${w45.stratified_server_utilisation_pct}%</td><td class="mono">${w45.stratified_heat_covered_pct}%</td></tr>
          <tr><td>Mixed W45</td><td class="mono">${w45.mixed_w45_effective_capacity_kwh} kWh</td><td class="mono">${w45.mixed_w45_server_utilisation_pct}%</td><td class="mono">${w45.mixed_w45_heat_covered_pct}%</td></tr>
        </tbody>
      </table>
      <p>${w45.legionella_note}</p>
    `;

    const cliff = sens.capex_utilisation_cliff;
    const cliffRows = (cliff.cliff_rows || [])
      .map(
        (r) => `<tr>
          <td class="mono">${r.servers_per_house}</td>
          <td class="mono">${r.server_utilisation_pct.toFixed(1)}%</td>
          <td class="mono">${r.heat_demand_covered_pct.toFixed(1)}%</td>
          <td class="mono">${r.break_even_opex_chf_per_srv_h.toFixed(4)}</td>
          <td class="mono">${r.total_break_even_chf_per_srv_h.central.toFixed(4)}</td>
        </tr>`
      )
      .join("");
    el.sensCapex.innerHTML = `
      <h3>CAPEX vs Utilisation Scaling</h3>
      <table class="compact-table">
        <thead><tr><th>N</th><th>Util %</th><th>Heat %</th><th>OPEX BE</th><th>Total BE</th></tr></thead>
        <tbody>${cliffRows}</tbody>
      </table>
      <p>${cliff.conclusion}</p>
    `;

    const tg = sens.tariff_gap;
    const tariffRows = (tg.tariffs || [])
      .map(
        (r) => `<tr>
          <td><strong>${r.category}</strong></td>
          <td class="mono">${r.total_excl_vat_chf_per_kwh.toFixed(4)}</td>
          <td class="mono">${r.total_incl_vat_chf_per_kwh.toFixed(4)}</td>
          <td class="mono">${r.h4_penalty_excl_vat_chf_per_kwh.toFixed(4)}</td>
        </tr>`
      )
      .join("");
    el.sensTariff.innerHTML = `
      <h3>Retail H4 vs Industrial C2-C6 Tariff Gap</h3>
      <p>${tg.vat_basis} | Max residential heat credit: <strong>${tg.max_residential_heat_credit_chf_per_kwh_e.toFixed(4)} CHF/kWh</strong></p>
      <table class="compact-table">
        <thead><tr><th>Cat</th><th>Excl VAT</th><th>Incl VAT</th><th>H4 Gap</th></tr></thead>
        <tbody>${tariffRows}</tbody>
      </table>
    `;

    const ml = sens.mlperf_v51;
    const mlRows = (ml.rows || [])
      .map(
        (r) => `<tr>
          <td>${r.model}</td>
          <td>${r.system}</td>
          <td class="mono">${r.joules_per_sample.toFixed(2)}</td>
          <td class="mono">${r.joules_per_unit.toFixed(2)}</td>
          <td class="mono">${r.chf_per_1m_units_at_full_reuse.toFixed(4)}</td>
          <td class="mono">${r.chf_per_1m_units_at_zero_reuse.toFixed(4)}</td>
        </tr>`
      )
      .join("");
    el.sensMlperf.innerHTML = `
      <h3>MLPerf Inference v5.1 Offline (11 Submissions | Energy-Only Marginal Cost)</h3>
      <p>Reuse floor ${ml.break_even_low_chf_per_kwh.toFixed(4)} CHF/kWh vs zero-reuse ${ml.break_even_high_chf_per_kwh.toFixed(4)} CHF/kWh</p>
      <table class="compact-table">
        <thead><tr><th>Model</th><th>System</th><th>J/sample</th><th>J/unit</th><th>CHF/1M (Full Reuse)</th><th>CHF/1M (Zero Reuse)</th></tr></thead>
        <tbody>${mlRows}</tbody>
      </table>
      <p>${ml.caveat}</p>
    `;
  }

  async function refreshAnnual() {
    try {
      const annual = await fetchJson(
        `/api/v1/simulation/annual?strategy=${encodeURIComponent(state.strategy)}&servers=${state.servers}&site=${encodeURIComponent(state.site)}`
      );
      renderAnnual(annual);
    } catch (err) {
      if (state.strategy === "adapter" && el.negBanner) {
        el.negBanner.textContent =
          "No external adapter schedule registered yet for this server count and site.";
      }
    }
  }

  async function refreshDay(targetHour = null) {
    const day = await fetchJson(
      `/api/v1/simulation/day/${encodeURIComponent(state.date)}?strategy=${encodeURIComponent(state.strategy)}&servers=${state.servers}&site=${encodeURIComponent(state.site)}`
    );
    state.dayData = day;
    renderHour(targetHour !== null ? targetHour : state.hourIndex);
  }

  let sensitivityInFlight = null;

  async function loadSensitivity(servers) {
    if (state.sensitivityData && state.sensitivityData.servers_per_house === servers) {
      return state.sensitivityData;
    }
    if (sensitivityInFlight && sensitivityInFlight.servers === servers) {
      return sensitivityInFlight.promise;
    }
    const promise = fetchJson(`/api/v1/sensitivity/pilot-reality?servers=${servers}`).then((sens) => {
      renderSensitivity(sens);
      return sens;
    });
    sensitivityInFlight = { servers, promise };
    try {
      return await promise;
    } finally {
      if (sensitivityInFlight && sensitivityInFlight.promise === promise) {
        sensitivityInFlight = null;
      }
    }
  }

  async function refreshSensitivityIfOpen() {
    if (!state.sensitivityOpen) return;
    await loadSensitivity(state.servers);
  }

  async function switchVerbundScene(sceneId, moveCamera = true) {
    state.verbundSceneId = sceneId;
    state.isVerbundMode = true;
    const scenePayload = await fetchJson(
      `/api/v1/verbund/scenes/${encodeURIComponent(sceneId)}?servers=${state.servers}`
    );
    renderVerbundScene(scenePayload);
    if (moveCamera) {
      await sceneBridge.setVerbundScene(scenePayload);
    }
    if (state.provenanceActive) {
      sceneBridge.setProvenanceMode(true, state.provenanceData);
    }
  }

  function syncXrayUi(enabled) {
    state.xray = Boolean(enabled);
    el.toggleXray.setAttribute("aria-pressed", String(state.xray));
    el.toggleXray.setAttribute("data-active", String(state.xray));
    sceneBridge.setXray(state.xray);
  }

  function syncProvenanceUi(enabled) {
    state.provenanceActive = Boolean(enabled);
    el.toggleProvenance.setAttribute("aria-pressed", String(state.provenanceActive));
    el.toggleProvenance.setAttribute("data-active", String(state.provenanceActive));
    sceneBridge.setProvenanceMode(state.provenanceActive, state.provenanceData);
  }

  let panelsVisible = true;
  let mobilePanel = "none";
  let lastMobilePanel = "ledger";
  let mobileControlsOpen = false;
  const MOBILE_PANEL_IDS = ["ledger", "thermal", "worker", "verbund"];

  function isMobileLayout() {
    return window.innerWidth <= 1080;
  }

  function syncDesktopPanels(visible) {
    panelsVisible = Boolean(visible);
    document.body.classList.toggle("panels-hidden", !panelsVisible);
    if (el.togglePanels) {
      el.togglePanels.setAttribute("aria-pressed", String(panelsVisible));
    }
  }

  function setMobilePanel(panelId) {
    const next = MOBILE_PANEL_IDS.includes(panelId) ? panelId : "none";
    mobilePanel = next;
    if (next !== "none") lastMobilePanel = next;
    for (const id of MOBILE_PANEL_IDS) {
      document.body.classList.toggle(`mobile-panel-${id}`, id === next);
    }
    for (const btn of el.mobileDockBtns) {
      btn.classList.toggle("is-active", btn.dataset.mobilePanel === next);
    }
    if (el.togglePanelsMobile) {
      el.togglePanelsMobile.setAttribute("aria-pressed", String(next !== "none"));
    }
    if (next !== "none" && mobileControlsOpen) {
      setMobileControls(false);
    }
  }

  function setMobileControls(open) {
    mobileControlsOpen = Boolean(open);
    document.body.classList.toggle("mobile-controls-open", mobileControlsOpen);
    if (el.toggleMobileControls) {
      el.toggleMobileControls.setAttribute("aria-expanded", String(mobileControlsOpen));
    }
  }

  async function applyPreset(presetId) {
    state.activePreset = presetId;
    for (const btn of el.presetButtons) {
      btn.classList.toggle("is-active", btn.dataset.preset === presetId);
    }
    if (presetId === "swiss_network") {
      await sceneBridge.openSwissNetwork();
      return;
    }
    sceneBridge.closeSwissNetwork(false);

    const preset = state.presetsById[presetId];
    if (!preset) return;

    syncXrayUi(Boolean(preset.xray));
    sceneBridge.setFastWorkerPolling(presetId === "live_rack");

    let needsAnnual = false;
    if (preset.strategy && preset.strategy !== state.strategy) {
      state.strategy = preset.strategy;
      el.strategySelect.value = preset.strategy;
      needsAnnual = true;
    }

    if (preset.verbund_scene) {
      el.verbundSelect.value = preset.verbund_scene;
      await switchVerbundScene(preset.verbund_scene, true);
    } else if (state.isVerbundMode) {
      state.isVerbundMode = false;
      await sceneBridge.resetToBaselHouse();
    }

    sceneBridge.setCameraPreset(preset);

    const targetDate = preset.date || state.date;
    const targetHour = preset.hour !== undefined && preset.hour !== null ? preset.hour : state.hourIndex;
    state.date = targetDate;
    el.dayPicker.value = targetDate;

    const tasks = [refreshDay(targetHour)];
    if (needsAnnual) tasks.push(refreshAnnual());
    await Promise.all(tasks);
  }

  async function postWorker(endpoint, body = null) {
    const opts = { method: "POST" };
    if (body !== null) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    const status = await fetchJson(endpoint, opts);
    renderWorkerStatus(status);
    sceneBridge.updateWorkerState(status);
  }

  function bindEvents() {
    for (const btn of el.presetButtons) {
      btn.addEventListener("click", () => {
        applyPreset(btn.dataset.preset);
      });
    }

    el.strategySelect.addEventListener("change", () => {
      state.strategy = el.strategySelect.value;
      Promise.all([refreshAnnual(), refreshDay()]);
    });

    el.serversSelect.addEventListener("change", () => {
      state.servers = Number(el.serversSelect.value);
      Promise.all([
        refreshAnnual(),
        refreshDay(),
        switchVerbundScene(state.verbundSceneId, false),
        refreshSensitivityIfOpen(),
      ]).then(() => {
        if (!state.sensitivityOpen) {
          loadSensitivity(state.servers).catch(() => {});
        }
      });
    });

    if (el.siteSelect) {
      el.siteSelect.addEventListener("change", () => {
        state.site = el.siteSelect.value;
        Promise.all([refreshAnnual(), refreshDay()]);
      });
    }

    el.toggleXray.addEventListener("click", () => {
      syncXrayUi(!state.xray);
    });

    el.toggleProvenance.addEventListener("click", () => {
      syncProvenanceUi(!state.provenanceActive);
    });

    if (el.togglePanels) {
      el.togglePanels.addEventListener("click", () => {
        syncDesktopPanels(!panelsVisible);
      });
    }

    if (el.togglePanelsMobile) {
      el.togglePanelsMobile.addEventListener("click", () => {
        setMobilePanel(mobilePanel === "none" ? lastMobilePanel : "none");
      });
    }

    if (el.toggleMobileControls) {
      el.toggleMobileControls.addEventListener("click", () => {
        setMobileControls(!mobileControlsOpen);
      });
    }

    for (const btn of el.panelCollapseBtns) {
      btn.addEventListener("click", () => {
        if (isMobileLayout()) {
          setMobilePanel("none");
          return;
        }
        const card = btn.closest(".panel-card");
        if (!card) return;
        const collapsed = card.classList.toggle("is-collapsed");
        btn.setAttribute("aria-expanded", String(!collapsed));
        btn.textContent = collapsed ? "+" : "−";
      });
    }

    for (const btn of el.mobileDockBtns) {
      btn.addEventListener("click", () => {
        const target = btn.dataset.mobilePanel || "none";
        if (target !== "none" && mobilePanel === target) {
          setMobilePanel("none");
        } else {
          setMobilePanel(target);
        }
      });
    }

    document.addEventListener("keydown", (e) => {
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (e.key === "h" || e.key === "H") {
        if (isMobileLayout()) {
          setMobilePanel(mobilePanel === "none" ? lastMobilePanel : "none");
        } else {
          syncDesktopPanels(!panelsVisible);
        }
      }
    });

    async function toggleSensitivityDrawer(forceState = null) {
      const next = forceState !== null ? forceState : !state.sensitivityOpen;
      state.sensitivityOpen = next;
      el.toggleSensitivity.setAttribute("aria-expanded", String(next));
      sceneBridge.setSensitivityHighlight(next);
      if (next) {
        await loadSensitivity(state.servers);
        el.sensPanel.hidden = false;
      } else {
        el.sensPanel.hidden = true;
      }
    }

    el.toggleSensitivity.addEventListener("click", () => toggleSensitivityDrawer());
    if (el.closeSensBtn) {
      el.closeSensBtn.addEventListener("click", () => toggleSensitivityDrawer(false));
    }

    el.dayPicker.addEventListener("change", () => {
      if (!el.dayPicker.value) return;
      state.date = el.dayPicker.value;
      refreshDay();
    });

    el.hourSlider.addEventListener("input", () => {
      renderHour(Number(el.hourSlider.value));
    });
    el.hourSlider.addEventListener("change", () => {
      renderHour(Number(el.hourSlider.value));
    });

    el.verbundSelect.addEventListener("change", () => {
      switchVerbundScene(el.verbundSelect.value, true);
    });

    el.workerStartBtn.addEventListener("click", () => {
      postWorker("/api/v1/worker/start", { job_type: el.workerJobSelect.value });
    });
    el.workerPauseBtn.addEventListener("click", () => postWorker("/api/v1/worker/pause"));
    el.workerResumeBtn.addEventListener("click", () => postWorker("/api/v1/worker/resume"));
    el.workerStopBtn.addEventListener("click", () => postWorker("/api/v1/worker/stop"));
    el.workerSignalBtn.addEventListener("click", () => {
      const row = state.dayData?.hours?.[state.hourIndex];
      const payload = row
        ? {
            tank_soc_kwh: row.tank_soc_kwh,
            heat_demand_kw: row.heat_demand_kw,
            outdoor_temp_c: row.outdoor_temp_c,
          }
        : { tank_soc_kwh: 34.8, heat_demand_kw: 0.5, outdoor_temp_c: 22.0 };
      postWorker("/api/v1/worker/heat-signal", payload);
    });
  }

  async function start() {
    bindEvents();

    const [health, provenance, presetsResp, annual, day, verbund, worker] = await Promise.all([
      fetchJson("/api/v1/health"),
      fetchJson("/api/v1/provenance"),
      fetchJson("/api/v1/cockpit/presets"),
      fetchJson(`/api/v1/simulation/annual?strategy=${state.strategy}&servers=${state.servers}&site=${state.site}`),
      fetchJson(`/api/v1/simulation/day/${state.date}?strategy=${state.strategy}&servers=${state.servers}&site=${state.site}`),
      fetchJson(`/api/v1/verbund/scenes/${state.verbundSceneId}?servers=${state.servers}`),
      fetchJson("/api/v1/worker/status"),
    ]);

    renderHealth(health);
    state.provenanceData = provenance;
    if (el.footer && Array.isArray(provenance.mandatory_notices)) {
      el.footer.textContent = provenance.mandatory_notices.join(" | ");
    }

    for (const p of presetsResp?.presets || []) {
      state.presetsById[p.id] = p;
    }

    renderAnnual(annual);
    state.dayData = day;
    renderHour(state.hourIndex);
    renderVerbundScene(verbund);
    renderWorkerStatus(worker);

    await sceneBridge.init(provenance, renderWorkerStatus);
    renderHour(state.hourIndex);
    sceneBridge.updateWorkerState(worker);

    loadSensitivity(state.servers).catch(() => {});
  }

  return { start, state, renderWorkerStatus, applyPreset };
}
