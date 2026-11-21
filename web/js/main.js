import { createCockpitScene } from "./scene.js?v=20261121h";
import { createProvenanceController } from "./provenance.js?v=20261121h";
import { createWorkerRackOverlay } from "./worker_rack.js?v=20261121h";
import { createSwissNetworkController } from "./swiss_network.js?v=20261121h";
import { createConfiguratorAndLedgerController } from "./configurator.js?v=20261121h";
import { initHud } from "./hud.js?v=20261121h";

const canvasEl = document.getElementById("canvas-3d");
const legendEl = document.querySelector("[data-testid='provenance-legend']");

let sceneEngine = null;
let hudStarted = false;
let provenanceCtrl = null;
let workerRackCtrl = null;
let swissNetworkCtrl = null;
let configuratorCtrl = null;
let activeAppTab = "explore";
let latestHourRow = null;
let latestDayMeta = null;
let latestVerbundState = null;

function ensureSceneEngine() {
  if (!sceneEngine) {
    sceneEngine = createCockpitScene(canvasEl);
    window.__DIGITALHEAT_RAW_SCENE__ = sceneEngine;
  }
  return sceneEngine;
}

function syncAppTabUi(tabId) {
  activeAppTab = tabId;
  document.body.dataset.appTab = tabId;
  document.body.classList.toggle("app-mode-atlas", tabId === "atlas");
  if (canvasEl) {
    canvasEl.hidden = tabId !== "atlas";
  }
  document.querySelectorAll("[data-app-tab]").forEach((btn) => {
    if (btn.classList.contains("btn-order-compact")) return;
    btn.classList.toggle("is-active", btn.dataset.appTab === tabId);
  });
}

async function ensureAtlasStarted() {
  ensureSceneEngine();
  if (!hudStarted) {
    hudStarted = true;
    await hud.start();
  }
}

async function setAppTab(tabId, opts = {}) {
  const valid = ["explore", "configure", "heatledger", "atlas"];
  const next = valid.includes(tabId) ? tabId : "explore";
  syncAppTabUi(next);

  if (next === "explore") {
    configuratorCtrl?.closeAll();
    await swissNetworkCtrl?.open();
    return;
  }
  if (next === "configure") {
    swissNetworkCtrl?.close(false);
    await configuratorCtrl?.openConfigurator(opts.viewMode || "exterior");
    return;
  }
  if (next === "heatledger") {
    swissNetworkCtrl?.close(false);
    await configuratorCtrl?.openHeatLedger();
    return;
  }
  // Atlas (Detailed Basel 3D Engineering Workbench - lazy initialized on demand)
  configuratorCtrl?.closeAll();
  swissNetworkCtrl?.close(false);
  await ensureAtlasStarted();
  sceneEngine?.clearSwissTwin();
  if (!opts.skipPreset && hud?.applyPreset) {
    hud.applyPreset("scorecard");
  }
}

const sceneBridge = {
  async init(provenanceData, onWorkerStatus) {
    const eng = ensureSceneEngine();
    provenanceCtrl = createProvenanceController({
      scene: eng.scene,
      provenanceData,
      legendEl,
    });

    await eng.initAssets(provenanceData);

    workerRackCtrl = createWorkerRackOverlay({
      rackGroup: eng.hardwareGroups.rack,
      cduGroup: eng.hardwareGroups.cdu,
      onStatusUpdate: onWorkerStatus,
    });

    eng.attachControllers({
      provenance: provenanceCtrl,
      workerRackCtrl,
    });

    if (latestHourRow) {
      eng.updateThermalAndSolar(latestHourRow, latestDayMeta, latestVerbundState);
    }

    workerRackCtrl.startPolling();
  },
  setCameraPreset(preset) {
    ensureSceneEngine().setCameraPreset(preset);
  },
  flyTo(position, target, durationMs = 1100, fov = 45) {
    ensureSceneEngine().setCameraPreset({ position, target, fov_deg: fov }, durationMs);
  },
  smoothOrbit(degDelta = 35, durationMs = 2400) {
    ensureSceneEngine().smoothOrbit(degDelta, durationMs);
  },
  setXray(enabled) {
    ensureSceneEngine().setXray(enabled);
  },
  updateThermalAndSolar(hourRow, dayMeta, verbundState) {
    latestHourRow = hourRow;
    latestDayMeta = dayMeta;
    latestVerbundState = verbundState;
    ensureSceneEngine().updateThermalAndSolar(hourRow, dayMeta, verbundState);
  },
  updateWorkerState(status) {
    if (workerRackCtrl) {
      workerRackCtrl.applyStatus(status);
    }
  },
  setFastWorkerPolling(enabled) {
    if (workerRackCtrl) {
      workerRackCtrl.setFastMode(enabled);
    }
  },
  setSensitivityHighlight(enabled) {
    if (workerRackCtrl) {
      workerRackCtrl.setSensitivityHighlight(enabled);
    }
  },
  async setVerbundScene(payload) {
    await ensureSceneEngine().setVerbundScene(payload);
  },
  async resetToBaselHouse() {
    await ensureSceneEngine().resetToBaselHouse();
  },
  async openSwissNetwork() {
    await setAppTab("explore");
  },
  closeSwissNetwork(restoreBasel = false) {
    if (swissNetworkCtrl) swissNetworkCtrl.close(restoreBasel);
  },
  setProvenanceMode(enabled, provenanceData) {
    if (!provenanceCtrl) return;
    if (provenanceData) {
      provenanceCtrl.setProvenanceData(provenanceData);
    }
    provenanceCtrl.setActive(enabled);
  },
  getDebugState() {
    return {
      ...(sceneEngine ? sceneEngine.getDebugState() : {}),
      provenanceActive: provenanceCtrl ? provenanceCtrl.isActive() : false,
      activeAppTab,
    };
  },
};

window.DigitalHeatScene = sceneBridge;

const hud = initHud(sceneBridge);

configuratorCtrl = createConfiguratorAndLedgerController({
  onOpenTab: (tab) => setAppTab(tab),
});

swissNetworkCtrl = createSwissNetworkController({
  sceneCtrl: {
    showSwissTwin: async (node) => {
      if (canvasEl) canvasEl.hidden = false;
      await ensureAtlasStarted();
      await ensureSceneEngine().showSwissTwin(node);
    },
    smoothOrbit: (deg, ms) => sceneEngine?.smoothOrbit(deg, ms),
    clearSwissTwin: () => sceneEngine?.clearSwissTwin(),
  },
  onExitToBasel: () => setAppTab("atlas"),
  onConfigureNode: (nodeId) => configuratorCtrl.prefillFromNode(nodeId, "configure"),
  onOpenHeatLedger: (nodeId) => configuratorCtrl.prefillFromNode(nodeId, "heatledger"),
});

document.querySelectorAll("[data-app-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    setAppTab(btn.dataset.appTab);
  });
});

window.CalorGridFleet = swissNetworkCtrl;
window.CalorGridApp = {
  setTab: setAppTab,
  fleet: swissNetworkCtrl,
  configurator: configuratorCtrl,
};

// Lightweight health check on initial load so header dot turns Live immediately without downloading Basel GLBs
fetch("/api/v1/health")
  .then((r) => r.json())
  .then((h) => {
    const badge = document.querySelector("[data-testid='api-health-badge']");
    if (badge) {
      const ok = h?.status === "ok";
      badge.textContent = ok ? "Live" : "Offline";
      badge.classList.toggle("is-degraded", !ok);
    }
  })
  .catch(() => {});

const params = new URLSearchParams(window.location.search);
const initialTab = params.get("view") || "explore";
setAppTab(initialTab, { skipPreset: initialTab === "atlas" });
