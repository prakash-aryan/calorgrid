import {
  buildGpuSlotsElement,
  domEl,
  fmtChf,
  fmtNum,
  fmtUsd,
  renderLogFeed,
} from "./telemetry_logs.js?v=20261121v";

const WORKLOAD_LABELS = {
  llm_inference: "LLM inference",
  training_fine_tune: "Fine-tuning batch",
  archive_ocr: "Heritage archive OCR",
  iiif_embeddings: "Vector embeddings",
  scientific_sim: "Alpine climate / CFD",
};

const HW_IMG_MAP = {
  b200_sxm_180gb: "/img/hardware/gpu_b200_board.jpg",
  h100_sxm_80gb: "/img/hardware/gpu_h100_sxm.jpg",
  h100_nvl_94gb: "/img/hardware/gpu_h100_nvl.jpg",
  a100_sxm_80gb: "/img/hardware/gpu_a100.jpg",
  rtx_pro_4500_blackwell: "/img/hardware/gpu_rtx_pro_4500.jpg",
  hydronode_1kw: "/img/hardware/server_ocp_liquid.jpg",
  ocp_winterfell_3kw: "/img/hardware/server_ocp_liquid.jpg",
  b200_ai_10kw: "/img/hardware/server_b200_rack.jpg",
  edge_micro_250w: "/img/hardware/server_ocp_liquid.jpg",
};

const SITE_VIDEO_MAP = {
  basel_st_jakob: "/media/twins/basel_st_jakob.mp4",
  basel_klingental: "/media/twins/basel_klingental.mp4",
  riehen_villa: "/media/twins/riehen_villa.mp4",
  basel_erdsonde: "/media/twins/basel_erdsonde.mp4",
  zurich_oerlikon: "/media/twins/zurich_oerlikon.mp4",
  bern_matte: "/media/twins/bern_matte.mp4",
  emmental_dairy: "/media/twins/emmental_dairy.mp4",
  thurgau_greenhouse: "/media/twins/thurgau_greenhouse.mp4",
  geneva_carouge: "/media/twins/geneva_carouge.mp4",
  valais_dryer: "/media/twins/valais_dryer.mp4",
  lucerne_bakery: "/media/twins/lucerne_bakery.mp4",
  stgallen_hospital: "/media/twins/stgallen_hospital.mp4",
  davos_alpine: "/media/twins/davos_alpine.mp4",
  lugano_lakeside: "/media/twins/lugano_lakeside.mp4",
  ara_basel_biogas: "/media/twins/basel_st_jakob.mp4",
  sole_uno_spa: "/media/twins/basel_st_jakob.mp4",
  zermatt_ski_resort: "/media/twins/davos_alpine.mp4",
  st_moritz_alpine: "/media/twins/davos_alpine.mp4",
};

function hwImgFor(id) {
  return HW_IMG_MAP[id] || "/img/hardware/gpu_h100_sxm.jpg";
}

function siteVideoFor(siteId, sinkType = "") {
  if (siteId && SITE_VIDEO_MAP[siteId]) return SITE_VIDEO_MAP[siteId];
  if (String(sinkType).includes("alpine") || String(sinkType).includes("ski")) {
    return "/media/twins/davos_alpine.mp4";
  }
  if (String(sinkType).includes("pool") || String(sinkType).includes("spa") || String(sinkType).includes("biogas")) {
    return "/media/twins/basel_st_jakob.mp4";
  }
  return "/media/twins/basel_klingental.mp4";
}

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
    subtabBtns: Array.from(overlay.querySelectorAll("[data-cc-subtab]")),
    subpanes: Array.from(overlay.querySelectorAll("[data-cc-pane]")),
    catalogBtns: Array.from(overlay.querySelectorAll("[data-cc-catalog]")),
    pricingHeading: document.getElementById("cc-pricing-heading"),
    visHwImg: document.getElementById("cc-vis-hw-img"),
    visHwName: document.getElementById("cc-vis-hw-name"),
    visHwSpec: document.getElementById("cc-vis-hw-spec"),
    visHeatTag: document.getElementById("cc-vis-heat-tag"),
    visSiteVideo: document.getElementById("cc-vis-site-video"),
    visSiteName: document.getElementById("cc-vis-site-name"),
    visSiteWait: document.getElementById("cc-vis-site-wait"),
  };

  let activeSubtab = "book";
  let activeCatalog = "gpu";

  function setSubtab(tab) {
    activeSubtab = tab;
    els.subtabBtns.forEach((b) => {
      const act = b.dataset.ccSubtab === tab;
      b.classList.toggle("is-active", act);
      b.setAttribute("aria-selected", act ? "true" : "false");
    });
    els.subpanes.forEach((p) => {
      p.hidden = p.dataset.ccPane !== tab;
    });
    if (tab === "fleet") fetchTelemetry().catch(() => {});
    if (tab === "logs") fetchLogs().catch(() => {});
  }

  function setCatalogView(mode) {
    activeCatalog = mode === "server" ? "server" : "gpu";
    els.catalogBtns.forEach((b) => {
      b.classList.toggle("is-active", b.dataset.ccCatalog === activeCatalog);
    });
    if (els.gpuTableHost) els.gpuTableHost.hidden = activeCatalog !== "gpu";
    if (els.skuTableHost) els.skuTableHost.hidden = activeCatalog !== "server";
    if (els.pricingHeading) {
      els.pricingHeading.textContent =
        activeCatalog === "gpu"
          ? "GPU pricing vs. cloud providers"
          : "Full server nodes vs. cloud equivalents";
    }
  }

  function updateVisualStage(quote = null) {
    const hwId = state.gpuOrSku;
    const imgSrc = hwImgFor(hwId);
    if (els.visHwImg && els.visHwImg.getAttribute("src") !== imgSrc) {
      els.visHwImg.setAttribute("src", imgSrc);
    }
    if (pricingData) {
      if (state.unitMode === "gpu") {
        const g = (pricingData.gpu_comparisons || []).find((x) => x.gpu_id === hwId);
        if (g) {
          if (els.visHwName) els.visHwName.textContent = g.name.replace(/^NVIDIA\s+/, "");
          if (els.visHwSpec) {
            els.visHwSpec.textContent = `${g.vram_gb} GB · ${(Number(g.typical_kw_per_gpu) * 1000).toFixed(0)} W · ${g.supply_temp_c}°C`;
          }
        }
      } else {
        const s = (pricingData.server_sku_comparisons || []).find((x) => x.server_model_id === hwId);
        if (s) {
          if (els.visHwName) els.visHwName.textContent = s.name;
          if (els.visHwSpec) {
            els.visHwSpec.textContent = `${s.electrical_kw} kW · ${s.supply_temp_c}°C water`;
          }
        }
      }
    }
    if (quote) {
      if (els.visHeatTag) {
        els.visHeatTag.textContent = `${fmtNum(quote.thermal_output_kw ?? quote.kw_th, 2)} kW heat`;
      }
      if (els.visSiteName) {
        els.visSiteName.textContent = quote.assigned_site_name || "Swiss Heat Sink";
      }
      if (els.visSiteWait) {
        if (quote.tier === "block") {
          els.visSiteWait.textContent = `Scheduled +${Math.round(quote.wait_time_min / 60)}h block`;
        } else if (quote.wait_time_min === 0) {
          els.visSiteWait.textContent =
            quote.tier === "dedicated"
              ? "Dedicated host"
              : quote.tier === "serverless"
                ? "Per-request active"
                : "Instant allocation";
        } else {
          els.visSiteWait.textContent = `${quote.wait_time_min} min Spot window`;
        }
      }
      const vSrc = siteVideoFor(quote.assigned_site_id, quote.sink_type);
      if (els.visSiteVideo && els.visSiteVideo.getAttribute("src") !== vSrc) {
        els.visSiteVideo.setAttribute("src", vSrc);
        els.visSiteVideo.load();
      }
      if (els.visSiteVideo && isOpened && activeSubtab === "book") {
        els.visSiteVideo.play().catch(() => {});
      }
    }
  }

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

  function tierMultiplier(tier) {
    const spec = pricingData?.demand_tiers?.[tier];
    if (spec && typeof spec.price_multiplier === "number") return spec.price_multiplier;
    return (
      {
        on_demand: 1.0,
        spot: 0.5,
        reserved: 0.6,
        block: 0.9,
        dedicated: 1.1,
        serverless: 1.5,
      }[tier] ?? 1.0
    );
  }

  function populateSkuOptions() {
    if (!els.skuSelect || !pricingData) return;
    const opts = [];
    const mult = tierMultiplier(state.tier);
    if (state.unitMode === "gpu") {
      for (const g of pricingData.gpu_comparisons || []) {
        const od = Number(g.calorgrid?.on_demand_chf_per_gpu_hr || 0);
        const rate = state.tier === "spot" ? g.calorgrid?.spot_chf_per_gpu_hr : od * mult;
        opts.push({
          value: g.gpu_id,
          label: `${g.name.replace(/^NVIDIA\s+/, "")} (${g.vram_gb} GB) - ${fmtChf(rate, 2)}/h`,
        });
      }
    } else {
      for (const s of pricingData.server_sku_comparisons || []) {
        const od = Number(s.calorgrid?.on_demand_chf_per_server_hr || 0);
        const rate = state.tier === "spot" ? s.calorgrid?.spot_chf_per_server_hr : od * mult;
        opts.push({
          value: s.server_model_id,
          label: `${s.name} (${s.electrical_kw} kW) - ${fmtChf(rate, 2)}/h`,
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
            state.tier === "spot"
              ? "Auto-route (cheapest thermal window)"
              : state.tier === "block"
                ? "Auto-route (24h scheduled thermal block)"
                : "Auto-route (best year-round sink)",
        })
      );
      for (const s of telemetryData.sites) {
        const sinkTag = s.is_year_round_sink
          ? "Year-round"
          : s.is_summer_sink
            ? "Summer"
            : "Building";
        frag.appendChild(
          domEl("option", {
            value: s.site_id,
            text: `${s.name} (${s.canton}, ${sinkTag})`,
          })
        );
      }
      els.siteSelect.replaceChildren(frag);
      els.siteSelect.value = prev || "";
    }
    if (els.siteFilterSelect) {
      const prevF = telemetrySiteFilter;
      const frag2 = document.createDocumentFragment();
      frag2.appendChild(domEl("option", { value: "", text: `All ${telemetryData.sites.length} fleet sites` }));
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
        ? { text: `Azure Live API (${azureCount} SKUs)`, cls: "is-live" }
        : azureCount > 0
          ? { text: `Cached Live (${azureCount} SKUs)`, cls: "is-cached" }
          : { text: "Pinned cloud matrix", cls: "is-pinned" };

    const specBench = (pricingData.specialist_cloud_benchmarks?.gpu_hour || []).find(
      (x) => x.id === "h100-specialist"
    );
    const gas2025 = pricingData.swiss_gas_history?.years?.["2025"];
    els.pricingMeta.replaceChildren(
      domEl("span", { className: `cc-source-badge ${badgeInfo.cls}`, text: badgeInfo.text }),
      specBench
        ? domEl("span", {
            className: "cc-meta-item",
            text: `Specialist H100 median: $${Number(specBench.on_demand_usd).toFixed(2)} OD / $${Number(specBench.spot_usd).toFixed(2)} Spot`,
          })
        : null,
      gas2025
        ? domEl("span", {
            className: "cc-meta-item",
            text: `Basel gas 2025: CHF ${Number(gas2025.chf_per_kwh).toFixed(4)}/kWh`,
          })
        : null,
      domEl("span", {
        className: "cc-meta-item",
        text: `1 USD = ${Number(pricingData.fx_chf_per_usd).toFixed(3)} CHF`,
      }),
      domEl("span", {
        className: "cc-meta-item",
        text: `Spot is ${Math.round((1 - (pricingData.spot_price_ratio ?? 0.5)) * 100)}% off On-Demand`,
      }),
      cs.last_refresh_utc
        ? domEl("span", {
            className: "cc-meta-item",
            text: `Updated ${formatAsOfShort(cs.last_refresh_utc)}`,
          })
        : null
    );
  }

  function renderComparisonTables() {
    if (!pricingData) return;

    // 1. Clean 6-column GPU Comparison Table (responsive stacked cards on mobile via data-label!)
    if (els.gpuTableHost) {
      const table = domEl("table", { className: "cc-table cc-table-responsive" });
      table.appendChild(
        domEl("thead", {}, [
          domEl("tr", {}, [
            domEl("th", { text: "GPU model" }),
            domEl("th", { className: "is-num", text: "On-Demand" }),
            domEl("th", { className: "is-num", text: "Spot (-50%)" }),
            domEl("th", { className: "is-num", text: "AWS EC2" }),
            domEl("th", { className: "is-num", text: "Lambda / Azure" }),
            domEl("th", { className: "is-num", text: "Savings" }),
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

        let altPrimary = "-";
        let altSub = "";
        if (lam.on_demand_usd_per_gpu_hr != null) {
          altPrimary = `Lambda ${fmtUsd(lam.on_demand_usd_per_gpu_hr)}`;
          if (az.on_demand_usd_per_gpu_hr != null && az.offer_status !== "proxy_gb200") {
            altSub = `Azure ${fmtUsd(az.on_demand_usd_per_gpu_hr)}`;
          } else if (gcp.on_demand_usd_per_gpu_hr != null) {
            altSub = `GCP ${fmtUsd(gcp.on_demand_usd_per_gpu_hr)}`;
          }
        } else if (az.on_demand_usd_per_gpu_hr != null) {
          altPrimary = `Azure ${fmtUsd(az.on_demand_usd_per_gpu_hr)}`;
          if (az.spot_usd_per_gpu_hr != null) altSub = `Spot ${fmtUsd(az.spot_usd_per_gpu_hr)}`;
        }

        const isSel = state.unitMode === "gpu" && state.gpuOrSku === g.gpu_id;
        const shortGpuName = g.name.replace(/^NVIDIA\s+/, "");

        const tr = domEl(
          "tr",
          {
            className: `cc-clickable-row${isSel ? " is-selected" : ""}`,
            dataset: { gpuId: g.gpu_id },
          },
          [
            domEl("td", { className: "cc-td-model", attrs: { "data-label": "GPU" } }, [
              domEl("div", { className: "cc-hw-cell" }, [
                domEl("img", {
                  className: "cc-hw-thumb",
                  attrs: { src: hwImgFor(g.gpu_id), alt: shortGpuName, loading: "lazy" },
                }),
                domEl("div", { className: "cc-hw-meta" }, [
                  domEl("strong", { text: shortGpuName }),
                  domEl("div", {
                    className: "cc-cell-sub",
                    text: `${g.vram_gb} GB, ${(Number(g.typical_kw_per_gpu) * 1000).toFixed(0)} W, ${g.supply_temp_c}°C`,
                  }),
                ]),
              ]),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "On-Demand" } }, [
              domEl("strong", { className: "cc-price-main", text: `${fmtChf(cg.on_demand_chf_per_gpu_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.on_demand_usd_per_gpu_hr)}/h` }),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "Spot (-50%)" } }, [
              domEl("strong", { className: "cc-price-spot", text: `${fmtChf(cg.spot_chf_per_gpu_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.spot_usd_per_gpu_hr)}/h` }),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "AWS EC2" } }, [
              domEl("span", {
                text: aws.on_demand_usd_per_gpu_hr != null ? `${fmtUsd(aws.on_demand_usd_per_gpu_hr)}/h` : "-",
              }),
              aws.spot_usd_per_gpu_hr != null
                ? domEl("div", { className: "cc-cell-sub", text: `Spot ${fmtUsd(aws.spot_usd_per_gpu_hr)}` })
                : null,
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "Lambda / Azure" } }, [
              domEl("span", { text: altPrimary }),
              altSub ? domEl("div", { className: "cc-cell-sub", text: altSub }) : null,
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "Savings" } }, [
              g.on_demand_savings_vs_cloud_pct != null
                ? domEl("span", {
                    className: "cc-savings-pill",
                    text: `-${Math.round(g.on_demand_savings_vs_cloud_pct)}%`,
                    title: `-${g.on_demand_savings_vs_cloud_pct}% On-Demand / -${g.spot_savings_vs_cloud_pct}% Spot vs cloud`,
                  })
                : domEl("span", { className: "cc-cell-sub", text: "Exclusive" }),
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

    // 2. Clean 6-column Full Server SKU Comparison Table
    if (els.skuTableHost) {
      const table = domEl("table", { className: "cc-table cc-table-responsive" });
      table.appendChild(
        domEl("thead", {}, [
          domEl("tr", {}, [
            domEl("th", { text: "Server node" }),
            domEl("th", { className: "is-num", text: "Heat out" }),
            domEl("th", { className: "is-num", text: "On-Demand" }),
            domEl("th", { className: "is-num", text: "Spot (-50%)" }),
            domEl("th", { className: "is-num", text: "Cloud equiv." }),
            domEl("th", { className: "is-num", text: "Savings" }),
          ]),
        ])
      );
      const tbody = domEl("tbody");
      for (const s of pricingData.server_sku_comparisons || []) {
        const cg = s.calorgrid || {};
        const cb = s.cloud_baseline || {};
        const isSel = state.unitMode === "server" && state.gpuOrSku === s.server_model_id;
        const thKw = (Number(s.typical_kw) * Number(s.eta)).toFixed(1);

        const tr = domEl(
          "tr",
          {
            className: `cc-clickable-row${isSel ? " is-selected" : ""}`,
            dataset: { serverModelId: s.server_model_id },
          },
          [
            domEl("td", { className: "cc-td-model", attrs: { "data-label": "Server" } }, [
              domEl("div", { className: "cc-hw-cell" }, [
                domEl("img", {
                  className: "cc-hw-thumb",
                  attrs: { src: hwImgFor(s.server_model_id), alt: s.name, loading: "lazy" },
                }),
                domEl("div", { className: "cc-hw-meta" }, [
                  domEl("strong", { text: s.name }),
                  domEl("div", {
                    className: "cc-cell-sub",
                    text: s.gpu_count > 0 ? `${s.gpu_count}× ${s.gpu_model}` : "CPU / edge node",
                  }),
                ]),
              ]),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "Heat" } }, [
              domEl("span", { text: `${thKw} kW` }),
              domEl("div", { className: "cc-cell-sub", text: `${s.supply_temp_c}°C water` }),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "On-Demand" } }, [
              domEl("strong", { className: "cc-price-main", text: `${fmtChf(cg.on_demand_chf_per_server_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.on_demand_usd_per_server_hr)}/h` }),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "Spot (-50%)" } }, [
              domEl("strong", { className: "cc-price-spot", text: `${fmtChf(cg.spot_chf_per_server_hr)}/h` }),
              domEl("div", { className: "cc-cell-sub", text: `${fmtUsd(cg.spot_usd_per_server_hr)}/h` }),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "Cloud equiv." } }, [
              domEl("span", {
                text: cb.aws_on_demand_usd_per_hr != null ? `AWS ${fmtUsd(cb.aws_on_demand_usd_per_hr)}/h` : "-",
              }),
              domEl("div", {
                className: "cc-cell-sub",
                text: cb.aws_instance ? cb.aws_instance.split(" ")[0] : "",
              }),
            ]),
            domEl("td", { className: "is-num", attrs: { "data-label": "Savings" } }, [
              s.on_demand_savings_vs_cloud_pct != null
                ? domEl("span", {
                    className: "cc-savings-pill",
                    text: `-${Math.round(s.on_demand_savings_vs_cloud_pct)}%`,
                  })
                : domEl("span", { className: "cc-cell-sub", text: "-" }),
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
      const vSrc = siteVideoFor(sk.site_id, sk.sink_type || sk.name);
      const vid = domEl("video", {
        className: "cc-sink-media",
        attrs: {
          src: vSrc,
          autoplay: "",
          muted: "",
          loop: "",
          playsinline: "",
          preload: "metadata",
        },
      });
      vid.muted = true;
      const card = domEl("article", { className: "cc-sink-card" }, [
        domEl("div", { className: "cc-sink-thumb" }, [vid]),
        domEl("div", { className: "cc-sink-body" }, [
          domEl("div", { className: "cc-sink-head" }, [
            domEl("strong", { text: sk.name }),
            domEl("span", {
              className: `cc-sink-spec ${sk.w45_compliant ? "is-w45" : ""}`,
              text: `${sk.supply_temp_c}°C, ${sk.w45_compliant ? "W45" : "Alpine"}`,
            }),
          ]),
          domEl("div", { className: "cc-sink-ledger" }, [
            domEl("div", { className: "cc-sink-row" }, [
              domEl("span", { text: "Annual / summer util." }),
              domEl("strong", { text: `${sk.annual_utilisation_pct}% / ${sk.summer_utilisation_pct}%` }),
            ]),
            domEl("div", { className: "cc-sink-row" }, [
              domEl("span", { text: "Heat wasted" }),
              domEl("strong", { text: `${sk.dumped_kwh} kWh` }),
            ]),
            domEl("div", { className: "cc-sink-row" }, [
              domEl("span", { text: "Break-even (8x B200)" }),
              domEl("strong", {
                text: `${fmtChf(sk.break_even_chf_per_gpu_hr, 2)}/GPU-h`,
              }),
            ]),
          ]),
          domEl("p", { className: "cc-sink-note", text: sk.notes || "" }),
          domEl("div", { className: "cc-sink-cta", text: "Route quote to this sink" }),
        ]),
      ]);
      card.addEventListener("click", () => {
        state.preferredSiteId = sk.site_id;
        syncFormState();
        setSubtab("book");
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
          domEl("strong", { text: "Capacity or thermal headroom limit" }),
          domEl("p", { text: errorDetail }),
        ])
      );
      if (els.bookBtn) els.bookBtn.disabled = true;
      return;
    }
    if (!quote) {
      els.quoteResult.replaceChildren(
        domEl("div", { className: "cc-quote-empty", text: "Select hardware above to compute a live quote." })
      );
      if (els.bookBtn) els.bookBtn.disabled = true;
      return;
    }

    updateVisualStage(quote);
    if (els.bookBtn) els.bookBtn.disabled = false;
    const waitTxt =
      quote.wait_time_min === 0
        ? "0 min (instant)"
        : quote.wait_time_min >= 60
          ? `${Math.floor(quote.wait_time_min / 60)}h ${String(quote.wait_time_min % 60).padStart(2, "0")}m (next heat window)`
          : `~${quote.wait_time_min} min (thermal batch window)`;
    const saveBadge =
      quote.cloud_savings_pct !== null && quote.cloud_savings_pct !== undefined
        ? domEl("span", {
            className: "cc-savings-pill",
            text: `Save ${Math.round(quote.cloud_savings_pct)}% vs cloud`,
          })
        : null;

    const children = [
      domEl("div", { className: "cc-quote-ledger" }, [
        domEl("div", { className: "cc-quote-total-row" }, [
          domEl("div", {}, [
            domEl("strong", { className: "cc-quote-price", text: fmtChf(quote.total_cost_chf) }),
            domEl("span", {
              className: "cc-quote-usd",
              text: `${fmtUsd(quote.total_cost_usd)} (${fmtChf(quote.unit_rate_chf_per_hr)}/h)`,
            }),
          ]),
          saveBadge,
        ]),
        domEl("div", { className: "cc-quote-rows" }, [
          domEl("div", { className: "cc-quote-line" }, [
            domEl("span", { text: "Assigned Swiss site" }),
            domEl("strong", { text: quote.assigned_site_name }),
          ]),
          domEl("div", { className: "cc-quote-line" }, [
            domEl("span", { text: "Allocation time" }),
            domEl("strong", { text: waitTxt }),
          ]),
          domEl("div", { className: "cc-quote-line" }, [
            domEl("span", { text: "Useful heat delivered" }),
            domEl("strong", {
              className: "accent-heat",
              text: `${fmtNum(quote.thermal_output_kw ?? quote.kw_th, 2)} kW (${fmtNum(quote.total_heat_kwh, 1)} kWh)`,
            }),
          ]),
        ]),
      ]),
    ];

    if (Array.isArray(quote.warnings) && quote.warnings.length > 0) {
      const warnBox = domEl("div", { className: "cc-quote-warnings" });
      for (const w of quote.warnings) {
        warnBox.appendChild(domEl("div", { text: w }));
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
    const viewFleetBtn = domEl("button", {
      type: "button",
      className: "cc-receipt-link",
      text: "View live job in fleet ->",
    });
    viewFleetBtn.addEventListener("click", () => {
      telemetrySiteFilter = receipt.assigned_site_id || "";
      if (els.siteFilterSelect) els.siteFilterSelect.value = telemetrySiteFilter;
      setSubtab("fleet");
    });
    els.bookingReceipt.replaceChildren(
      domEl("strong", {
        text: `Dispatched ${receipt.job_id} (${receipt.status}) at ${receipt.assigned_site_name}`,
      }),
      domEl("span", {
        text: `${receipt.quantity}x ${receipt.gpu_or_sku} (${receipt.duration_hours}h) · ${fmtNum(receipt.thermal_output_kw, 2)} kW heat · ${fmtChf(receipt.total_cost_chf)}`,
      }),
      viewFleetBtn
    );
  }

  function renderFleetTelemetry() {
    if (!telemetryData) return;
    const sum = telemetryData.summary || {};

    if (els.fleetSummaryHost) {
      els.fleetSummaryHost.replaceChildren(
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Active GPUs" }),
          domEl("strong", { text: `${sum.active_gpus ?? 0} / ${sum.total_gpus ?? 0}` }),
          domEl("small", { text: `${fmtNum(sum.fleet_utilization_pct, 1)}% fleet utilization` }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Live heat output" }),
          domEl("strong", { className: "accent-heat", text: `${fmtNum(sum.total_kw_th, 1)} kW` }),
          domEl("small", { text: `${fmtNum(sum.total_kw_e, 1)} kW electrical across ${sum.total_servers} servers` }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Scheduler jobs" }),
          domEl("strong", { text: `${sum.active_jobs_count ?? 0} running` }),
          domEl("small", {
            text: `${sum.queued_jobs_count ?? 0} queued, ${fmtNum(sum.backfill_kw_e, 1)} kW backfill`,
          }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Thermal headroom" }),
          domEl("strong", { text: `${fmtNum(sum.total_thermal_headroom_kw, 1)} kW` }),
          domEl("small", {
            text: `${sum.total_dumped_kwh ?? 0} kWh wasted heat`,
          }),
        ])
      );
    }

    const siteNameById = new Map((telemetryData.sites || []).map((s) => [s.site_id, s.name]));
    const tierLabelMap = {
      on_demand: "On-Demand",
      spot: "Spot",
      reserved: "Reserved",
      block: "Block",
      dedicated: "Dedicated",
      serverless: "Serverless",
      backfill: "Backfill",
    };

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
          const siteLabel = siteNameById.get(j.site_id) || j.site_id.replace(/_/g, " ");
          const skuShort = String(j.gpu_or_sku || "").replace(/_/g, " ").toUpperCase();
          list.appendChild(
            domEl("div", { className: "cc-job-row" }, [
              domEl("div", { className: "cc-job-main" }, [
                domEl("strong", { text: j.job_name || j.job_id }),
                domEl("span", {
                  text: `${siteLabel} (${j.quantity}x ${skuShort}, ${j.remaining_hours}h left)`,
                }),
              ]),
              domEl("div", { className: "cc-job-right" }, [
                domEl("span", {
                  className: `cc-job-tier tier-${j.tier}`,
                  text: `${tierLabelMap[j.tier] || j.tier}, ${j.status}`,
                }),
                domEl("span", {
                  className: "cc-job-kw",
                  text: `${fmtNum(j.kw_th, 2)} kW heat`,
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
      filteredSites.forEach((site, idx) => {
        const gpuPct = site.gpus_total > 0 ? Math.round((site.gpus_busy / site.gpus_total) * 100) : 0;
        const details = domEl("details", { className: "cc-site-tel-card" });
        if (idx < 3 || filteredSites.length === 1) details.open = true;

        details.appendChild(
          domEl("summary", { className: "cc-site-tel-head" }, [
            domEl("div", { className: "cc-site-tel-title" }, [
              domEl("strong", { text: site.name }),
              domEl("span", {
                className: "cc-cell-sub",
                text: `${site.city} (${site.canton}), indoor ${site.indoor_temp_c}°C, tank ${site.tank_soc_pct}%`,
              }),
            ]),
            domEl("div", { className: "cc-site-tel-right" }, [
              domEl("span", {
                className: "cc-site-tel-stat",
                text: `${fmtNum(site.kw_th, 1)} kW heat`,
              }),
              domEl("span", {
                className: "cc-cell-sub",
                text: `${site.gpus_busy}/${site.gpus_total} GPUs (${gpuPct}%), ${fmtNum(site.thermal_headroom_kw, 1)} kW room`,
              }),
            ]),
          ])
        );

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
              domEl("strong", { text: srv.server_name || srv.server_id }),
              domEl("span", {
                className: `hl-proc-pill ${srv.status === "running" ? "is-run" : "is-stop"}`,
                text: `${fmtNum(srvUtil, 0)}% load, ${fmtNum(srv.kw_th, 2)} kW heat`,
              }),
            ]),
          ]);
          const slotsEl = buildGpuSlotsElement(srv.gpu_slots);
          if (slotsEl) srvBlock.appendChild(slotsEl);
          srvList.appendChild(srvBlock);
        }
        details.appendChild(srvList);
        grid.appendChild(details);
      });
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
    setCatalogView(state.unitMode);
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
    updateVisualStage(latestQuote);
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

  els.subtabBtns.forEach((btn) => {
    btn.addEventListener("click", () => setSubtab(btn.dataset.ccSubtab));
  });

  els.catalogBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      state.unitMode = btn.dataset.ccCatalog;
      syncFormState();
      requestQuote().catch(() => {});
    });
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
