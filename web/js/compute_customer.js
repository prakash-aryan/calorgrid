import {
  buildGpuSlotsElement,
  domEl,
  fmtChf,
  fmtNum,
  fmtUsd,
  renderLogFeed,
} from "./telemetry_logs.js?v=20261121m";

const WORKLOAD_LABELS = {
  llm_inference: "LLM Inference (vLLM / TensorRT-LLM)",
  training_fine_tune: "LoRA / Fine-Tuning Batch",
  archive_ocr: "Swiss Heritage Archive OCR",
  iiif_embeddings: "Museum IIIF Vector Embeddings",
  scientific_sim: "Alpine Climate & CFD Batch",
};

function makeIdempotencyKey() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `cg-${crypto.randomUUID()}`;
  }
  return `cg-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function sourceBadgeMeta(src) {
  if (src === "fresh_live") return { text: "Live API", cls: "is-live" };
  if (src === "cached_live") return { text: "Cached Live", cls: "is-cached" };
  if (src === "unavailable") return { text: "No Cloud Offer", cls: "is-unavail" };
  return { text: "Pinned Ref", cls: "is-pinned" };
}

function formatAsOfShort(isoStr) {
  if (!isoStr || typeof isoStr !== "string") return "--";
  return isoStr.slice(0, 16).replace("T", " ") + "Z";
}

export function createComputeCustomerController({ onOpenTab } = {}) {
  const overlay = document.querySelector("[data-testid='compute-overlay']");
  if (!overlay) {
    return {
      open: async () => {},
      close: () => {},
      isOpen: () => false,
    };
  }

  const els = {
    pricingMeta: document.getElementById("cc-pricing-meta"),
    refreshPricingBtn: document.getElementById("cc-refresh-pricing-btn"),
    tierToggleBtns: Array.from(overlay.querySelectorAll("[data-cc-tier]")),
    unitModeBtns: Array.from(overlay.querySelectorAll("[data-cc-unit-mode]")),
    skuSelect: document.getElementById("cc-sku-select"),
    qtyInput: document.getElementById("cc-qty-input"),
    hoursInput: document.getElementById("cc-hours-input"),
    siteSelect: document.getElementById("cc-site-select"),
    workloadSelect: document.getElementById("cc-workload-select"),
    jobNameInput: document.getElementById("cc-job-name-input"),
    quoteBtn: document.getElementById("cc-quote-btn"),
    bookBtn: document.getElementById("cc-book-btn"),
    tickBtn: document.getElementById("cc-tick-btn"),
    quoteResult: document.getElementById("cc-quote-result"),
    bookingReceipt: document.getElementById("cc-booking-receipt"),
    gpuTableHost: document.getElementById("cc-gpu-table-host"),
    skuTableHost: document.getElementById("cc-sku-table-host"),
    summerSinksHost: document.getElementById("cc-summer-sinks"),
    fleetSummaryHost: document.getElementById("cc-fleet-summary"),
    activeJobsHost: document.getElementById("cc-active-jobs"),
    fleetSitesHost: document.getElementById("cc-fleet-sites"),
    siteFilterSelect: document.getElementById("cc-telemetry-site-filter"),
    logsHost: document.getElementById("cc-app-logs"),
    hostSwitchBtn: document.getElementById("cc-switch-to-host-btn"),
  };

  let isOpened = false;
  let pollTimer = null;
  let pricingData = null;
  let telemetryData = null;
  let logsData = null;
  let latestQuote = null;
  let latestBooking = null;
  let logCategoryFilter = "";
  let telemetrySiteFilter = "";
  let quoteDebounce = null;

  const state = {
    tier: "on_demand",
    unitMode: "gpu",
    gpuOrSku: "h100_sxm_80gb",
    quantity: 1,
    durationHours: 4,
    preferredSiteId: "",
    workloadType: "llm_inference",
    jobName: "",
  };

  let pricingAbort = null;
  let telemetryAbort = null;
  let logsAbort = null;
  let quoteAbort = null;

  function abortAndRenew(prev) {
    if (prev) prev.abort();
    return new AbortController();
  }

  async function fetchPricing(forceRefresh = false) {
    pricingAbort = abortAndRenew(pricingAbort);
    const url = forceRefresh
      ? "/api/v1/compute/pricing?refresh=true"
      : "/api/v1/compute/pricing";
    const resp = await fetch(url, { signal: pricingAbort.signal });
    if (!resp.ok) throw new Error(`Pricing HTTP ${resp.status}`);
    pricingData = await resp.json();
    populateSkuOptions();
    renderPricingMeta();
    renderComparisonTables();
    renderSummerSinks();
    return pricingData;
  }

  async function fetchTelemetry() {
    telemetryAbort = abortAndRenew(telemetryAbort);
    const resp = await fetch("/api/v1/fleet/telemetry", {
      signal: telemetryAbort.signal,
    });
    if (!resp.ok) throw new Error(`Telemetry HTTP ${resp.status}`);
    telemetryData = await resp.json();
    populateSiteSelects();
    renderFleetTelemetry();
    return telemetryData;
  }

  async function fetchLogs() {
    logsAbort = abortAndRenew(logsAbort);
    const qs = new URLSearchParams({ limit: "40", order: "desc" });
    if (logCategoryFilter) qs.set("category", logCategoryFilter);
    const resp = await fetch(`/api/v1/logs?${qs.toString()}`, {
      signal: logsAbort.signal,
    });
    if (!resp.ok) throw new Error(`Logs HTTP ${resp.status}`);
    logsData = await resp.json();
    renderLogFeed(
      els.logsHost,
      logsData,
      logCategoryFilter,
      (cat) => {
        logCategoryFilter = cat;
        fetchLogs().catch(() => {});
      },
      () => fetchLogs().catch(() => {})
    );
    return logsData;
  }

  function populateSkuOptions() {
    if (!els.skuSelect || !pricingData) return;
    const opts = [];
    if (state.unitMode === "gpu") {
      for (const g of pricingData.gpu_comparisons || []) {
        const rate =
          state.tier === "on_demand"
            ? g.calorgrid?.on_demand_chf_per_gpu_hr
            : g.calorgrid?.spot_chf_per_gpu_hr;
        opts.push({
          value: g.gpu_id,
          label: `${g.name} (${g.vram_gb} GB · CHF ${Number(rate ?? 0).toFixed(2)}/GPU-h)`,
        });
      }
    } else {
      for (const s of pricingData.server_sku_comparisons || []) {
        const rate =
          state.tier === "on_demand"
            ? s.calorgrid?.on_demand_chf_per_server_hr
            : s.calorgrid?.spot_chf_per_server_hr;
        opts.push({
          value: s.server_model_id,
          label: `${s.name} (${s.electrical_kw} kW_e · CHF ${Number(rate ?? 0).toFixed(2)}/srv-h)`,
        });
      }
    }
    const validVals = new Set(opts.map((o) => o.value));
    if (!validVals.has(state.gpuOrSku) && opts.length > 0) {
      state.gpuOrSku = opts[0].value;
    }

    const frag = document.createDocumentFragment();
    for (const o of opts) {
      frag.appendChild(domEl("option", { value: o.value, text: o.label }));
    }
    els.skuSelect.replaceChildren(frag);
    els.skuSelect.value = state.gpuOrSku;
  }

  function populateSiteSelects() {
    if (!telemetryData?.sites) return;
    if (els.siteSelect) {
      const prev = state.preferredSiteId;
      const frag = document.createDocumentFragment();
      frag.appendChild(
        domEl("option", {
          value: "",
          text:
            state.tier === "on_demand"
              ? "Auto-Route (Best Year-Round / Summer Heat Sink)"
              : "Auto-Route (Cheapest Thermal-Matched Building)",
        })
      );
      for (const s of telemetryData.sites) {
        const sinkTag = s.is_year_round_sink
          ? "Year-Round"
          : s.is_summer_sink
            ? "Summer"
            : "Building";
        frag.appendChild(
          domEl("option", {
            value: s.site_id,
            text: `${s.name} (${s.canton} · ${sinkTag} · ${Number(s.thermal_headroom_kw).toFixed(1)} kW room)`,
          })
        );
      }
      els.siteSelect.replaceChildren(frag);
      els.siteSelect.value = prev || "";
    }
    if (els.siteFilterSelect) {
      const prevF = telemetrySiteFilter;
      const frag2 = document.createDocumentFragment();
      frag2.appendChild(domEl("option", { value: "", text: `All ${telemetryData.sites.length} Fleet Sites` }));
      for (const s of telemetryData.sites) {
        frag2.appendChild(
          domEl("option", {
            value: s.site_id,
            text: `${s.name} (${s.gpus_busy}/${s.gpus_total} GPUs)`,
          })
        );
      }
      els.siteFilterSelect.replaceChildren(frag2);
      els.siteFilterSelect.value = prevF || "";
    }
  }

  function renderPricingMeta() {
    if (!els.pricingMeta || !pricingData) return;
    const cs = pricingData.cache_status || {};
    const azureCount = cs.azure_live_gpus_cached ?? 0;
    const badgeInfo =
      azureCount > 0 && !cs.loaded_from_disk
        ? { text: `Azure Live (${azureCount} SKUs)`, cls: "is-live" }
        : azureCount > 0
          ? { text: `Cached Live (${azureCount} SKUs)`, cls: "is-cached" }
          : { text: "Pinned Local Matrix", cls: "is-pinned" };

    els.pricingMeta.replaceChildren(
      domEl("span", { className: `cc-source-badge ${badgeInfo.cls}`, text: badgeInfo.text }),
      domEl("span", {
        className: "cc-meta-item",
        text: `FX 1 USD = ${Number(pricingData.fx_chf_per_usd).toFixed(4)} CHF (${pricingData.fx_as_of || "2026-11-20"})`,
      }),
      domEl("span", {
        className: "cc-meta-item",
        text: `Spot discount: ${Math.round((1 - (pricingData.spot_price_ratio ?? 0.5)) * 100)}% off On-Demand`,
      }),
      cs.last_refresh_utc
        ? domEl("span", {
            className: "cc-meta-item",
            text: `Refreshed ${formatAsOfShort(cs.last_refresh_utc)}`,
          })
        : null
    );
  }

  function renderComparisonTables() {
    if (!pricingData) return;

    // 1. GPU Comparison Table
    if (els.gpuTableHost) {
      const table = domEl("table", { className: "cc-table" });
      table.appendChild(
        domEl("thead", {}, [
          domEl("tr", {}, [
            domEl("th", { text: "GPU Model" }),
            domEl("th", { text: "CalorGrid On-Demand" }),
            domEl("th", { text: "CalorGrid Spot" }),
            domEl("th", { text: "AWS EC2" }),
            domEl("th", { text: "Azure" }),
            domEl("th", { text: "Lambda / GCP" }),
            domEl("th", { text: "Savings" }),
            domEl("th", { text: "Source / As Of" }),
          ]),
        ])
      );

      const tbody = domEl("tbody");
      for (const g of pricingData.gpu_comparisons || []) {
        const cg = g.calorgrid || {};
        const prov = g.providers || {};
        const aws = prov.aws || {};
        const az = prov.azure || {};
        const lam = prov.lambda || {};
        const gcp = prov.gcp || {};
        const lamOrGcp =
          lam.on_demand_usd_per_gpu_hr !== null && lam.on_demand_usd_per_gpu_hr !== undefined
            ? `Lambda ${fmtUsd(lam.on_demand_usd_per_gpu_hr)}/h`
            : gcp.on_demand_usd_per_gpu_hr !== null && gcp.on_demand_usd_per_gpu_hr !== undefined
              ? `GCP ${fmtUsd(gcp.on_demand_usd_per_gpu_hr)}/h`
              : "N/A";

        // Pick Azure source if live/cached, else AWS/fallback
        const repSrc = az.source && az.source !== "unavailable" ? az.source : aws.source || "pinned_fallback";
        const repAsOf = az.source && az.source !== "unavailable" ? az.as_of : aws.as_of;
        const badge = sourceBadgeMeta(repSrc);
        const isSel = state.unitMode === "gpu" && state.gpuOrSku === g.gpu_id;

        const tr = domEl(
          "tr",
          {
            className: `cc-clickable-row${isSel ? " is-selected" : ""}`,
            dataset: { gpuId: g.gpu_id },
          },
          [
            domEl("td", {}, [
              domEl("strong", { text: g.name }),
              domEl("div", {
                className: "cc-cell-sub",
                text: `${g.vram_gb} GB · ${(Number(g.typical_kw_per_gpu) * 1000).toFixed(0)} W typ · ${g.supply_temp_c} C`,
              }),
            ]),
            domEl("td", {}, [
              domEl("strong", { className: "cc-price-main", text: `${fmtChf(cg.on_demand_chf_per_gpu_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.on_demand_usd_per_gpu_hr)}/h` }),
            ]),
            domEl("td", {}, [
              domEl("strong", { className: "cc-price-spot", text: `${fmtChf(cg.spot_chf_per_gpu_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.spot_usd_per_gpu_hr)}/h` }),
            ]),
            domEl("td", {}, [
              domEl("span", {
                text: aws.on_demand_usd_per_gpu_hr !== null ? `${fmtUsd(aws.on_demand_usd_per_gpu_hr)}/h` : "N/A",
              }),
              aws.spot_usd_per_gpu_hr !== null
                ? domEl("div", { className: "cc-cell-sub", text: `Spot ${fmtUsd(aws.spot_usd_per_gpu_hr)}` })
                : null,
            ]),
            domEl("td", {}, [
              domEl("span", {
                text: az.on_demand_usd_per_gpu_hr !== null ? `${fmtUsd(az.on_demand_usd_per_gpu_hr)}/h` : "N/A",
              }),
              az.spot_usd_per_gpu_hr !== null
                ? domEl("div", { className: "cc-cell-sub", text: `Spot ${fmtUsd(az.spot_usd_per_gpu_hr)}` })
                : null,
            ]),
            domEl("td", { text: lamOrGcp }),
            domEl("td", {}, [
              g.on_demand_savings_vs_cloud_pct !== null
                ? domEl("span", {
                    className: "cc-savings-pill",
                    text: `-${g.on_demand_savings_vs_cloud_pct}% OD / -${g.spot_savings_vs_cloud_pct}% Spot`,
                  })
                : domEl("span", { className: "cc-cell-sub", text: "CalorGrid Exclusive" }),
            ]),
            domEl("td", {}, [
              domEl("span", { className: `cc-source-badge ${badge.cls}`, text: badge.text }),
              domEl("div", {
                className: "cc-cell-sub",
                text: `${formatAsOfShort(repAsOf)} · FX ${Number(pricingData.fx_chf_per_usd).toFixed(3)}`,
              }),
            ]),
          ]
        );
        tr.addEventListener("click", () => {
          state.unitMode = "gpu";
          state.gpuOrSku = g.gpu_id;
          syncFormState();
          requestQuote().catch(() => {});
        });
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      els.gpuTableHost.replaceChildren(table);
    }

    // 2. Full Server SKU Comparison Table (All 4 SKUs)
    if (els.skuTableHost) {
      const table = domEl("table", { className: "cc-table" });
      table.appendChild(
        domEl("thead", {}, [
          domEl("tr", {}, [
            domEl("th", { text: "Server SKU (Full Node)" }),
            domEl("th", { text: "Power & Heat" }),
            domEl("th", { text: "CalorGrid On-Demand" }),
            domEl("th", { text: "CalorGrid Spot" }),
            domEl("th", { text: "Cloud Equivalent (AWS / Azure)" }),
            domEl("th", { text: "Savings vs Cloud" }),
            domEl("th", { text: "Source" }),
          ]),
        ])
      );
      const tbody = domEl("tbody");
      for (const s of pricingData.server_sku_comparisons || []) {
        const cg = s.calorgrid || {};
        const cb = s.cloud_baseline || {};
        const badge = sourceBadgeMeta(cb.source || "pinned_fallback");
        const isSel = state.unitMode === "server" && state.gpuOrSku === s.server_model_id;
        const thKw = (Number(s.typical_kw) * Number(s.eta)).toFixed(2);

        const tr = domEl(
          "tr",
          {
            className: `cc-clickable-row${isSel ? " is-selected" : ""}`,
            dataset: { serverModelId: s.server_model_id },
          },
          [
            domEl("td", {}, [
              domEl("strong", { text: s.name }),
              domEl("div", {
                className: "cc-cell-sub",
                text: s.gpu_count > 0 ? `${s.gpu_count}x ${s.gpu_model}` : "CPU / Edge Compute Node",
              }),
            ]),
            domEl("td", {}, [
              domEl("span", { text: `${s.typical_kw} kW_e typ (${s.electrical_kw} max)` }),
              domEl("div", { className: "cc-cell-sub", text: `${thKw} kW_th @ ${s.supply_temp_c} C` }),
            ]),
            domEl("td", {}, [
              domEl("strong", { className: "cc-price-main", text: `${fmtChf(cg.on_demand_chf_per_server_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.on_demand_usd_per_server_hr)}/h` }),
            ]),
            domEl("td", {}, [
              domEl("strong", { className: "cc-price-spot", text: `${fmtChf(cg.spot_chf_per_server_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.spot_usd_per_server_hr)}/h` }),
            ]),
            domEl("td", {}, [
              domEl("span", {
                text: cb.aws_on_demand_usd_per_hr !== null ? `AWS ${fmtUsd(cb.aws_on_demand_usd_per_hr)}/h` : "N/A",
              }),
              domEl("div", {
                className: "cc-cell-sub",
                text: cb.aws_instance ? `${cb.aws_instance}` : "",
              }),
            ]),
            domEl("td", {}, [
              s.on_demand_savings_vs_cloud_pct !== null
                ? domEl("span", {
                    className: "cc-savings-pill",
                    text: `-${s.on_demand_savings_vs_cloud_pct}% OD / -${s.spot_savings_vs_cloud_pct}% Spot`,
                  })
                : domEl("span", { className: "cc-cell-sub", text: "--" }),
            ]),
            domEl("td", {}, [
              domEl("span", { className: `cc-source-badge ${badge.cls}`, text: badge.text }),
              domEl("div", { className: "cc-cell-sub", text: formatAsOfShort(cb.as_of) }),
            ]),
          ]
        );
        tr.addEventListener("click", () => {
          state.unitMode = "server";
          state.gpuOrSku = s.server_model_id;
          syncFormState();
          requestQuote().catch(() => {});
        });
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      els.skuTableHost.replaceChildren(table);
    }
  }

  function renderSummerSinks() {
    if (!els.summerSinksHost || !pricingData?.summer_sinks) return;
    const grid = domEl("div", { className: "cc-sink-grid" });
    for (const sk of pricingData.summer_sinks) {
      const card = domEl("article", { className: "cc-sink-card" }, [
        domEl("div", { className: "cc-sink-head" }, [
          domEl("strong", { text: sk.name }),
          domEl("span", {
            className: `cc-sink-tag ${sk.w45_compliant ? "is-w45" : "is-warn"}`,
            text: `${sk.supply_temp_c} C · ${sk.w45_compliant ? "W45 OK" : "Alpine"}`,
          }),
        ]),
        domEl("div", { className: "cc-sink-metrics" }, [
          domEl("div", {}, [
            domEl("span", { text: "Annual / Summer Util" }),
            domEl("strong", { text: `${sk.annual_utilisation_pct}% / ${sk.summer_utilisation_pct}%` }),
          ]),
          domEl("div", {}, [
            domEl("span", { text: "Heat Wasted" }),
            domEl("strong", { text: `${sk.dumped_kwh} kWh` }),
          ]),
          domEl("div", {}, [
            domEl("span", { text: "Break-Even (8x B200)" }),
            domEl("strong", {
              text: `${fmtChf(sk.break_even_chf_per_gpu_hr, 2)}/GPU-h (${fmtUsd(sk.break_even_usd_per_gpu_hr, 2)})`,
            }),
          ]),
          domEl("div", {}, [
            domEl("span", { text: "2-Tier On-Demand BE" }),
            domEl("strong", {
              text: `${fmtChf(sk.two_tier_on_demand_breakeven_chf_per_gpu_hr, 2)}/GPU-h (${sk.tariff_code})`,
            }),
          ]),
        ]),
        domEl("p", { className: "cc-sink-note", text: sk.notes || "" }),
      ]);
      card.addEventListener("click", () => {
        state.preferredSiteId = sk.site_id;
        syncFormState();
        requestQuote().catch(() => {});
      });
      grid.appendChild(card);
    }
    els.summerSinksHost.replaceChildren(grid);
  }

  function renderQuoteResult(quote, errorDetail = null) {
    if (!els.quoteResult) return;
    if (errorDetail) {
      els.quoteResult.replaceChildren(
        domEl("div", { className: "cc-quote-error" }, [
          domEl("strong", { text: "Thermal Headroom / Capacity Constraint" }),
          domEl("p", { text: errorDetail }),
        ])
      );
      if (els.bookBtn) els.bookBtn.disabled = true;
      return;
    }
    if (!quote) {
      els.quoteResult.replaceChildren(
        domEl("div", { className: "cc-quote-empty", text: "Configure GPU or server capacity above to generate a live scheduler quote." })
      );
      if (els.bookBtn) els.bookBtn.disabled = true;
      return;
    }

    if (els.bookBtn) els.bookBtn.disabled = false;
    const waitTxt =
      quote.wait_time_min === 0 ? "0 min (Instant)" : `${quote.wait_time_min} min (Thermal Window)`;
    const slotsTxt = Array.isArray(quote.assigned_gpu_slots)
      ? quote.assigned_gpu_slots
          .slice(0, 8)
          .map((s) => (s.slot_index >= 0 ? `${s.server_id}#gpu${s.slot_index}` : s.server_id))
          .join(", ") + (quote.assigned_gpu_slots.length > 8 ? ` (+${quote.assigned_gpu_slots.length - 8} more)` : "")
      : "--";

    const children = [
      domEl("div", { className: "cc-quote-kpis" }, [
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Assigned Swiss Site" }),
          domEl("strong", { text: quote.assigned_site_name }),
          domEl("small", { text: `Sink: ${quote.sink_type} · ${quote.assigned_server_ids?.join(", ") || ""}` }),
        ]),
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Estimated Wait" }),
          domEl("strong", { text: waitTxt }),
          domEl("small", {
            text: quote.tier === "on_demand" ? "Year-round thermal sink" : "Interruptible Spot window",
          }),
        ]),
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Thermal Heat Output" }),
          domEl("strong", {
            className: "accent-heat",
            text: `${fmtNum(quote.thermal_output_kw ?? quote.kw_th, 2)} kW_th`,
          }),
          domEl("small", {
            text: `${fmtNum(quote.total_heat_kwh, 1)} kWh total recovered (${fmtNum(quote.kw_e, 2)} kW_e)`,
          }),
        ]),
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Exact Quote Cost" }),
          domEl("strong", { text: `${fmtChf(quote.total_cost_chf)} (${fmtUsd(quote.total_cost_usd)})` }),
          domEl("small", {
            text:
              quote.cloud_savings_pct !== null && quote.cloud_savings_pct !== undefined
                ? `Save ${quote.cloud_savings_pct}% vs ${quote.cloud_benchmark_provider || "Cloud"}`
                : `Rate ${fmtChf(quote.unit_rate_chf_per_hr)}/hr`,
          }),
        ]),
      ]),
      domEl("div", {
        className: "cc-quote-slots",
        text: `Allocated slots: ${slotsTxt} · Quote expires ${formatAsOfShort(quote.expires_at)}`,
      }),
    ];

    if (Array.isArray(quote.warnings) && quote.warnings.length > 0) {
      const warnBox = domEl("div", { className: "cc-quote-warnings" });
      for (const w of quote.warnings) {
        warnBox.appendChild(domEl("div", { text: `Warning: ${w}` }));
      }
      children.push(warnBox);
    }

    els.quoteResult.replaceChildren(...children);
  }

  function renderBookingReceipt(receipt, errorMsg = null) {
    if (!els.bookingReceipt) return;
    if (errorMsg) {
      els.bookingReceipt.hidden = false;
      els.bookingReceipt.className = "cc-receipt-box is-error";
      els.bookingReceipt.replaceChildren(
        domEl("strong", { text: "Booking Could Not Complete" }),
        domEl("span", { text: errorMsg })
      );
      return;
    }
    if (!receipt) {
      els.bookingReceipt.hidden = true;
      return;
    }
    els.bookingReceipt.hidden = false;
    els.bookingReceipt.className = "cc-receipt-box is-ok";
    els.bookingReceipt.replaceChildren(
      domEl("strong", {
        text: `Dispatched ${receipt.job_id} (${receipt.status.toUpperCase()}) at ${receipt.assigned_site_name}`,
      }),
      domEl("span", {
        text: `Booking ${receipt.booking_id} · ${receipt.quantity}x ${receipt.gpu_or_sku} (${receipt.duration_hours}h) · ${fmtNum(receipt.thermal_output_kw, 2)} kW_th · ${fmtChf(receipt.total_cost_chf)} · Fleet state v${receipt.state_version}`,
      })
    );
  }

  function renderFleetTelemetry() {
    if (!telemetryData) return;
    const sum = telemetryData.summary || {};

    if (els.fleetSummaryHost) {
      els.fleetSummaryHost.replaceChildren(
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Active GPUs / Total" }),
          domEl("strong", { text: `${sum.active_gpus ?? 0} / ${sum.total_gpus ?? 0}` }),
          domEl("small", { text: `${fmtNum(sum.fleet_utilization_pct, 1)}% fleet utilization` }),
        ]),
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Live Power -> Heat" }),
          domEl("strong", { className: "accent-heat", text: `${fmtNum(sum.total_kw_th, 1)} kW_th` }),
          domEl("small", { text: `${fmtNum(sum.total_kw_e, 1)} kW_e across ${sum.total_servers} servers` }),
        ]),
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Scheduler Jobs" }),
          domEl("strong", { text: `${sum.active_jobs_count ?? 0} running` }),
          domEl("small", {
            text: `${sum.queued_jobs_count ?? 0} queued · ${fmtNum(sum.backfill_kw_e, 1)} kW backfill`,
          }),
        ]),
        domEl("div", { className: "cfg-kpi" }, [
          domEl("span", { text: "Thermal Headroom" }),
          domEl("strong", { text: `${fmtNum(sum.total_thermal_headroom_kw, 1)} kW` }),
          domEl("small", {
            text: `Clock ${formatAsOfShort(telemetryData.simulation_clock_utc)} · Dump ${sum.total_dumped_kwh ?? 0} kWh`,
          }),
        ])
      );
    }

    if (els.activeJobsHost) {
      const jobs = Array.isArray(telemetryData.active_jobs) ? telemetryData.active_jobs : [];
      const filteredJobs = telemetrySiteFilter
        ? jobs.filter((j) => j.site_id === telemetrySiteFilter)
        : jobs;
      if (!filteredJobs.length) {
        els.activeJobsHost.replaceChildren(
          domEl("div", { className: "cg-log-empty", text: "No active or queued jobs for this filter." })
        );
      } else {
        const list = domEl("div", { className: "cc-jobs-list" });
        for (const j of filteredJobs.slice(0, 12)) {
          list.appendChild(
            domEl("div", { className: "cc-job-row" }, [
              domEl("div", { className: "cc-job-main" }, [
                domEl("strong", { text: `${j.job_name} (${j.job_id})` }),
                domEl("span", {
                  text: `${j.site_id} · ${j.quantity}x ${j.gpu_or_sku} · ${j.remaining_hours}h left`,
                }),
              ]),
              domEl("div", { className: "cc-job-right" }, [
                domEl("span", {
                  className: `cc-job-tier tier-${j.tier}`,
                  text: `${j.tier.toUpperCase()} · ${j.status}`,
                }),
                domEl("span", {
                  className: "cc-job-kw",
                  text: `${fmtNum(j.kw_e, 2)} kW_e / ${fmtNum(j.kw_th, 2)} kW_th`,
                }),
              ]),
            ])
          );
        }
        els.activeJobsHost.replaceChildren(list);
      }
    }

    if (els.fleetSitesHost) {
      const sites = Array.isArray(telemetryData.sites) ? telemetryData.sites : [];
      const filteredSites = telemetrySiteFilter
        ? sites.filter((s) => s.site_id === telemetrySiteFilter)
        : sites;

      const grid = domEl("div", { className: "cc-telemetry-grid" });
      for (const site of filteredSites) {
        const siteCard = domEl("div", { className: "cc-site-tel-card" }, [
          domEl("div", { className: "cc-site-tel-head" }, [
            domEl("div", {}, [
              domEl("strong", { text: site.name }),
              domEl("span", {
                className: "cc-cell-sub",
                text: `${site.city} (${site.canton}) · Indoor ${site.indoor_temp_c} C / Target ${site.target_temp_c} C · Tank ${site.tank_soc_pct}%`,
              }),
            ]),
            domEl("span", {
              className: "cc-site-tel-stat",
              text: `${fmtNum(site.kw_th, 2)} kW_th · ${site.gpus_busy}/${site.gpus_total} GPUs · Room ${fmtNum(site.thermal_headroom_kw, 1)} kW`,
            }),
          ]),
        ]);

        const srvList = domEl("div", { className: "cc-srv-list" });
        for (const srv of site.servers || []) {
          const rawUtil = Number(srv.utilization_pct ?? 0);
          const srvUtil =
            Array.isArray(srv.gpu_slots) && srv.gpu_slots.length > 0
              ? srv.gpu_slots.reduce((acc, g) => acc + Number(g.utilization_pct ?? 0), 0) /
                srv.gpu_slots.length
              : rawUtil > 100
                ? rawUtil / 100
                : rawUtil;
          const srvBlock = domEl("div", { className: "cc-srv-item" }, [
            domEl("div", { className: "cc-srv-head" }, [
              domEl("strong", { text: `${srv.server_id} (${srv.server_name})` }),
              domEl("span", {
                className: `hl-proc-pill ${srv.status === "running" ? "is-run" : "is-stop"}`,
                text: `${srv.status} · ${fmtNum(srvUtil, 0)}% · ${fmtNum(srv.kw_e, 2)} kW_e -> ${fmtNum(srv.kw_th, 2)} kW_th`,
              }),
            ]),
          ]);
          const slotsEl = buildGpuSlotsElement(srv.gpu_slots);
          if (slotsEl) srvBlock.appendChild(slotsEl);
          srvList.appendChild(srvBlock);
        }
        siteCard.appendChild(srvList);
        grid.appendChild(siteCard);
      }
      els.fleetSitesHost.replaceChildren(grid);
    }
  }

  function syncFormState() {
    els.tierToggleBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.ccTier === state.tier);
    });
    els.unitModeBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.ccUnitMode === state.unitMode);
    });
    populateSkuOptions();
    populateSiteSelects();
    if (els.qtyInput && Number(els.qtyInput.value) !== state.quantity) {
      els.qtyInput.value = String(state.quantity);
    }
    if (els.hoursInput && Number(els.hoursInput.value) !== state.durationHours) {
      els.hoursInput.value = String(state.durationHours);
    }
    if (els.siteSelect && els.siteSelect.value !== (state.preferredSiteId || "")) {
      els.siteSelect.value = state.preferredSiteId || "";
    }
    if (els.workloadSelect) els.workloadSelect.value = state.workloadType;
    renderComparisonTables();
  }

  async function requestQuote() {
    quoteAbort = abortAndRenew(quoteAbort);
    const body = {
      tier: state.tier,
      unit_mode: state.unitMode,
      gpu_or_sku: state.gpuOrSku,
      quantity: Math.max(1, Math.min(64, parseInt(state.quantity, 10) || 1)),
      duration_hours: Math.max(0.5, Math.min(168, Number(state.durationHours) || 4)),
      preferred_site_id: state.preferredSiteId || null,
      workload_type: state.workloadType || "llm_inference",
      job_name: state.jobName?.trim() || null,
    };

    try {
      const resp = await fetch("/api/v1/compute/quote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: quoteAbort.signal,
      });
      const payload = await resp.json();
      if (!resp.ok) {
        latestQuote = null;
        renderQuoteResult(null, payload?.detail || `Quote HTTP ${resp.status}`);
        return null;
      }
      latestQuote = payload;
      renderQuoteResult(latestQuote, null);
      fetchLogs().catch(() => {});
      return latestQuote;
    } catch (err) {
      if (err?.name === "AbortError") return null;
      renderQuoteResult(null, err.message || "Quote request failed");
      return null;
    }
  }

  function scheduleQuote() {
    if (quoteDebounce) clearTimeout(quoteDebounce);
    quoteDebounce = setTimeout(() => {
      requestQuote().catch(() => {});
    }, 150);
  }

  async function dispatchBooking() {
    if (!latestQuote?.quote_id) {
      await requestQuote();
    }
    if (!latestQuote?.quote_id) return;
    if (els.bookBtn) els.bookBtn.disabled = true;
    try {
      const resp = await fetch("/api/v1/compute/book", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": makeIdempotencyKey(),
        },
        body: JSON.stringify({
          quote_id: latestQuote.quote_id,
          job_name: state.jobName?.trim() || null,
        }),
      });
      const payload = await resp.json();
      if (!resp.ok) {
        renderBookingReceipt(null, payload?.detail || `Booking HTTP ${resp.status}`);
        return;
      }
      latestBooking = payload;
      renderBookingReceipt(latestBooking, null);
      if (payload.telemetry_snapshot) {
        telemetryData = payload.telemetry_snapshot;
        renderFleetTelemetry();
      } else {
        await fetchTelemetry();
      }
      await fetchLogs();
      await requestQuote();
    } catch (err) {
      renderBookingReceipt(null, err.message || "Dispatch failed");
    } finally {
      if (els.bookBtn) els.bookBtn.disabled = false;
    }
  }

  async function advanceTick() {
    if (els.tickBtn) els.tickBtn.disabled = true;
    try {
      const resp = await fetch("/api/v1/fleet/telemetry/tick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hours: 1.0 }),
      });
      if (!resp.ok) throw new Error(`Tick HTTP ${resp.status}`);
      const payload = await resp.json();
      if (payload.telemetry) {
        telemetryData = payload.telemetry;
        populateSiteSelects();
        renderFleetTelemetry();
      }
      await fetchLogs();
      await requestQuote();
    } catch (err) {
      renderBookingReceipt(null, err.message || "Sim tick failed");
    } finally {
      if (els.tickBtn) els.tickBtn.disabled = false;
    }
  }

  // Wire controls
  els.tierToggleBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tier = btn.dataset.ccTier;
      syncFormState();
      requestQuote().catch(() => {});
    });
  });

  els.unitModeBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      state.unitMode = btn.dataset.ccUnitMode;
      syncFormState();
      requestQuote().catch(() => {});
    });
  });

  els.skuSelect?.addEventListener("change", (e) => {
    state.gpuOrSku = e.target.value;
    renderComparisonTables();
    requestQuote().catch(() => {});
  });

  els.qtyInput?.addEventListener("input", (e) => {
    state.quantity = Math.max(1, Math.min(64, parseInt(e.target.value, 10) || 1));
    scheduleQuote();
  });

  els.hoursInput?.addEventListener("input", (e) => {
    state.durationHours = Math.max(0.5, Math.min(168, Number(e.target.value) || 4));
    scheduleQuote();
  });

  els.siteSelect?.addEventListener("change", (e) => {
    state.preferredSiteId = e.target.value;
    requestQuote().catch(() => {});
  });

  els.workloadSelect?.addEventListener("change", (e) => {
    state.workloadType = e.target.value;
    requestQuote().catch(() => {});
  });

  els.jobNameInput?.addEventListener("input", (e) => {
    state.jobName = e.target.value;
  });

  els.quoteBtn?.addEventListener("click", () => {
    requestQuote().catch(() => {});
  });

  els.bookBtn?.addEventListener("click", () => {
    dispatchBooking().catch(() => {});
  });

  els.tickBtn?.addEventListener("click", () => {
    advanceTick().catch(() => {});
  });

  els.refreshPricingBtn?.addEventListener("click", () => {
    fetchPricing(true).catch(() => {});
  });

  els.siteFilterSelect?.addEventListener("change", (e) => {
    telemetrySiteFilter = e.target.value;
    renderFleetTelemetry();
  });

  els.hostSwitchBtn?.addEventListener("click", () => {
    if (onOpenTab) onOpenTab("configure");
  });

  if (els.workloadSelect) {
    const frag = document.createDocumentFragment();
    for (const [k, label] of Object.entries(WORKLOAD_LABELS)) {
      frag.appendChild(domEl("option", { value: k, text: label }));
    }
    els.workloadSelect.replaceChildren(frag);
    els.workloadSelect.value = state.workloadType;
  }

  async function open(opts = {}) {
    isOpened = true;
    overlay.hidden = false;
    if (opts.preferredSiteId) {
      state.preferredSiteId = opts.preferredSiteId;
    }
    if (opts.tier) {
      state.tier = opts.tier;
    }
    await Promise.all([
      fetchPricing(false).catch(() => {}),
      fetchTelemetry().catch(() => {}),
      fetchLogs().catch(() => {}),
    ]);
    syncFormState();
    await requestQuote();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      if (!isOpened) return;
      fetchTelemetry().catch(() => {});
      fetchLogs().catch(() => {});
    }, 5000);
  }

  function close() {
    isOpened = false;
    overlay.hidden = true;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    pricingAbort?.abort();
    telemetryAbort?.abort();
    logsAbort?.abort();
    quoteAbort?.abort();
  }

  return {
    open,
    close,
    isOpen: () => isOpened,
    refresh: () => open(),
  };
}
