/**
 * Shared safe DOM builders, telemetry formatters, GPU slot renderers,
 * and Application & Simulation Log panel renderer for CalorGrid.
 */

export function domEl(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.id) node.id = opts.id;
  if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
  if (opts.title) node.title = String(opts.title);
  if (opts.type) node.type = String(opts.type);
  if (opts.value !== undefined) node.value = String(opts.value);
  if (opts.hidden !== undefined) node.hidden = Boolean(opts.hidden);
  if (opts.disabled !== undefined) node.disabled = Boolean(opts.disabled);
  if (opts.dataset) {
    for (const [k, v] of Object.entries(opts.dataset)) {
      if (v !== undefined && v !== null) node.dataset[k] = String(v);
    }
  }
  if (opts.attrs) {
    for (const [k, v] of Object.entries(opts.attrs)) {
      if (v !== undefined && v !== null) node.setAttribute(k, String(v));
    }
  }
  if (opts.style) {
    for (const [k, v] of Object.entries(opts.style)) {
      if (v !== undefined && v !== null) node.style[k] = String(v);
    }
  }
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    if (typeof child === "string" || typeof child === "number") {
      node.appendChild(document.createTextNode(String(child)));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

export function fmtChf(val, digits = 2) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "--";
  return `CHF ${Number(val).toLocaleString("en-CH", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function fmtUsd(val, digits = 2) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "--";
  return `$${Number(val).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function fmtNum(val, digits = 1) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "--";
  return Number(val).toFixed(digits);
}

export const LOG_CATEGORY_LABELS = {
  "": "All",
  simulation_run: "Simulations",
  analysis_task: "Analysis",
  scheduler_dispatch: "Scheduler",
  customer_booking: "Bookings",
  worker_lifecycle: "Workers",
  thermal_rebalance: "Rebalance",
};

const LOG_CATEGORY_BADGE = {
  simulation_run: "Simulation",
  analysis_task: "Analysis",
  scheduler_dispatch: "Scheduler",
  customer_booking: "Booking",
  worker_lifecycle: "Worker",
  thermal_rebalance: "Thermal",
};

function formatShortUtc(ts) {
  if (!ts || typeof ts !== "string") return "--:--:--";
  const tIdx = ts.indexOf("T");
  if (tIdx === -1) return ts.slice(0, 19);
  return ts.slice(tIdx + 1, tIdx + 9);
}

function summarizeMetrics(metrics) {
  if (!metrics || typeof metrics !== "object") return "";
  const pairs = [];
  for (const [k, v] of Object.entries(metrics)) {
    if (v === null || v === undefined || typeof v === "object") continue;
    pairs.push(`${k}=${v}`);
    if (pairs.length >= 4) break;
  }
  return pairs.join(" · ");
}

/**
 * Render structured application & simulation logs into `hostEl` using textContent only.
 */
export function renderLogFeed(hostEl, payload, activeCategory, onCategoryChange, onRefresh) {
  if (!hostEl) return;
  const entries = Array.isArray(payload?.entries) ? payload.entries : [];
  const counts = payload?.category_counts || {};
  const totalLogged = payload?.total_logged ?? entries.length;

  const filterBar = domEl("div", { className: "cg-log-filters" });
  const cats = [
    "",
    "simulation_run",
    "analysis_task",
    "scheduler_dispatch",
    "customer_booking",
    "worker_lifecycle",
    "thermal_rebalance",
  ];

  for (const cat of cats) {
    const label = LOG_CATEGORY_LABELS[cat] || cat;
    const countNum = cat === "" ? payload?.total_buffered ?? entries.length : counts[cat] ?? 0;
    const btn = domEl("button", {
      type: "button",
      className: `cg-log-tab${activeCategory === cat ? " is-active" : ""}`,
      dataset: { logCat: cat },
    }, [
      domEl("span", { text: label }),
      domEl("small", { text: String(countNum) }),
    ]);
    btn.addEventListener("click", () => {
      if (onCategoryChange) onCategoryChange(cat);
    });
    filterBar.appendChild(btn);
  }

  const refreshBtn = domEl("button", {
    type: "button",
    className: "cg-log-refresh-link",
    text: "Refresh",
  });
  refreshBtn.addEventListener("click", () => {
    if (onRefresh) onRefresh();
  });

  const metaBar = domEl("div", { className: "cg-log-meta" }, [
    domEl("span", {
      text: `${entries.length} recent events (${totalLogged} recorded)`,
    }),
    refreshBtn,
  ]);

  const listEl = domEl("div", { className: "cg-log-list" });
  if (!entries.length) {
    listEl.appendChild(
      domEl("div", {
        className: "cg-log-empty",
        text: "No log entries match this filter yet.",
      })
    );
  } else {
    for (const item of entries) {
      const cat = item.category || "analysis_task";
      const badgeTxt = LOG_CATEGORY_BADGE[cat] || cat;
      const metricStr = summarizeMetrics(item.metrics);
      const row = domEl("div", { className: `cg-log-row level-${item.level || "info"}` }, [
        domEl("span", {
          className: "cg-log-ts",
          text: formatShortUtc(item.timestamp_utc),
        }),
        domEl("span", { className: `cg-log-cat cat-${cat}`, text: badgeTxt }),
        domEl("div", { className: "cg-log-body" }, [
          domEl("div", { className: "cg-log-summary", text: item.summary || item.event_type || "" }),
          metricStr ? domEl("div", { className: "cg-log-metrics", text: metricStr }) : null,
        ]),
      ]);
      listEl.appendChild(row);
    }
  }

  hostEl.replaceChildren(filterBar, metaBar, listEl);
}

/**
 * Render sleek inline GPU slot utilization rows (no nested box cards).
 */
export function buildGpuSlotsElement(gpuSlots) {
  if (!Array.isArray(gpuSlots) || !gpuSlots.length) return null;
  const list = domEl("div", { className: "hl-gpu-slots" });
  for (const gs of gpuSlots) {
    const rawStatus = String(gs.status || "idle").toLowerCase();
    const isBusy = rawStatus === "running" || rawStatus === "busy";
    const isOff = rawStatus === "fiber_offload" || rawStatus === "offline";
    const cls = isBusy ? "is-busy" : isOff ? "is-offload" : "is-idle";
    const utilNum = Math.max(0, Math.min(100, Number(gs.utilization_pct ?? 0)));
    const util = utilNum.toFixed(0);
    const hKw = Number(gs.kw_th ?? gs.heat_kw ?? 0).toFixed(2);
    const jobLbl = gs.job_name || (isBusy ? "Active workload" : "Idle");
    const slotNum = gs.slot_index !== undefined && gs.slot_index !== null ? gs.slot_index : 0;
    const shortId = `GPU ${slotNum}`;

    const row = domEl("div", { className: `hl-gpu-chip ${cls}` }, [
      domEl("div", { className: "hl-gpu-chip-top" }, [
        domEl("strong", { text: shortId }),
        domEl("span", { className: "hl-gpu-job", text: jobLbl }),
        domEl("span", { className: "hl-gpu-util", text: `${util}% · ${hKw} kW` }),
      ]),
      domEl("div", { className: "hl-gpu-bar" }, [
        domEl("div", { className: "hl-gpu-bar-fill", style: { width: `${utilNum}%` } }),
      ]),
    ]);
    list.appendChild(row);
  }
  return list;
}
