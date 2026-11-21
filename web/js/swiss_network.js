import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { Basement3DStudio } from "./configurator.js?v=20261121m";

// Exact LV95 -> 3D relief surface coordinates (Y = terrain surface on switzerland_3d_relief.glb)
// Minor local offset on the 4 tightly clustered Basel sites keeps each 3D beacon distinct and clickable.
const NODE_3D_COORDS = {
  basel_klingental: [-5.18, 0.61, -8.46],
  riehen_villa: [-4.02, 0.64, -8.82],
  basel_st_jakob: [-4.72, 0.63, -7.76],
  basel_erdsonde: [-3.88, 0.66, -7.68],
  zurich_oerlikon: [2.34, 0.77, -6.68],
  bern_matte: [-5.87, 0.84, -1.47],
  emmental_dairy: [-3.76, 1.01, -2.69],
  thurgau_greenhouse: [7.05, 0.75, -9.36],
  geneva_carouge: [-16.03, 0.73, 6.96],
  valais_dryer: [-6.61, 0.92, 6.48],
  lucerne_bakery: [0.38, 0.84, -2.47],
  stgallen_hospital: [8.71, 0.96, -7.01],
  davos_alpine: [12.30, 1.84, -0.12],
  lugano_lakeside: [5.91, 0.75, 8.92],
};

const SHORT_CITY = {
  basel_klingental: "Basel MFH",
  basel_st_jakob: "St. Jakob Pool",
  basel_erdsonde: "Dreispitz",
  riehen_villa: "Riehen",
  zurich_oerlikon: "Zurich",
  bern_matte: "Bern",
  emmental_dairy: "Emmental Dairy",
  thurgau_greenhouse: "Thurgau Glass",
  geneva_carouge: "Geneva",
  valais_dryer: "Valais Winery",
  lucerne_bakery: "Lucerne",
  stgallen_hospital: "St. Gallen Hosp.",
  davos_alpine: "Davos Spa",
  lugano_lakeside: "Lugano",
};

const BACKBONE_LINKS = [
  ["basel_klingental", "basel_st_jakob"],
  ["basel_st_jakob", "basel_erdsonde"],
  ["basel_klingental", "riehen_villa"],
  ["basel_klingental", "zurich_oerlikon"],
  ["basel_klingental", "bern_matte"],
  ["bern_matte", "emmental_dairy"],
  ["bern_matte", "geneva_carouge"],
  ["geneva_carouge", "valais_dryer"],
  ["valais_dryer", "emmental_dairy"],
  ["emmental_dairy", "lucerne_bakery"],
  ["zurich_oerlikon", "lucerne_bakery"],
  ["zurich_oerlikon", "thurgau_greenhouse"],
  ["thurgau_greenhouse", "stgallen_hospital"],
  ["stgallen_hospital", "davos_alpine"],
  ["lucerne_bakery", "lugano_lakeside"],
  ["lugano_lakeside", "davos_alpine"],
];

function workloadShort(w) {
  if (w === "archive_ocr") return "OCR";
  if (w === "iiif_embeddings") return "EMB";
  return "LLM";
}

function stateBadgeInfo(state) {
  switch (state) {
    case "RECEIVING_MIGRATED":
      return { text: "Receiving Jobs", color: "#0369a1", hex: 0x0284c7, bg: "#e0f2fe" };
    case "SIGSTOP_OFFLOADING":
    case "THROTTLED_OFFLOADING":
      return { text: "Sharing Load", color: "#1d4ed8", hex: 0x2563eb, bg: "#dbeafe" };
    case "DUMPING_HEAT":
      return { text: "Wasting Heat", color: "#b91c1c", hex: 0xdc2626, bg: "#fee2e2" };
    case "TANK_FULL_STANDBY":
      return { text: "Standby", color: "#475569", hex: 0x64748b, bg: "#f1f5f9" };
    default:
      return { text: "Heating Now", color: "#c2410c", hex: 0xea580c, bg: "#ffedd5" };
  }
}

function hardwareSummaryForNode(node) {
  const n = Number(node.servers_installed || 4);
  if (n >= 16) {
    return `${n} Liquid-Cooled AI Server Units (Vertiv 16OCP / B200 Class) · Water heated to ${node.target_supply_c} C`;
  }
  if (n >= 8) {
    return `${n} Hybrid Liquid Server Racks (OCP Winterfell Class) · Water heated to ${node.target_supply_c} C`;
  }
  return `${n} CalorGrid HydroNode 1 kW Units · Compact Heat Exchanger + 1,000 L Tank (${node.target_supply_c} C)`;
}

function nodeToBasementConfig(node) {
  const n = Number(node.servers_installed || 4);
  const activeRatio = Math.max(0, Math.min(1, Number(node.live?.servers_active || 0) / Math.max(1, n)));
  let modelId = "hydronode_1kw";
  let visualUnits = Math.max(1, Math.min(8, n));
  if (n >= 16) {
    modelId = "b200_ai_10kw";
    visualUnits = Math.max(2, Math.min(3, Math.round(n / 8)));
  } else if (n >= 8) {
    modelId = "ocp_winterfell_3kw";
    visualUnits = Math.max(3, Math.min(5, Math.round(n / 2.5)));
  } else if (node.node_id === "riehen_villa" || node.node_id === "lugano_lakeside") {
    modelId = "edge_micro_250w";
    visualUnits = Math.max(2, Math.min(6, n));
  }
  const activeVisual = Math.max(
    activeRatio > 0.05 ? 1 : 0,
    Math.min(visualUnits, Math.round(visualUnits * activeRatio))
  );
  let roomStyle = "basement";
  if (node.node_id === "thurgau_greenhouse") roomStyle = "greenhouse";
  else if (node.category === "farm") roomStyle = "farm_barn";
  else if (node.category === "pool" || node.category === "alpine") roomStyle = "pool_plant";
  else if (node.category === "commercial") roomStyle = "commercial";
  return {
    modelId,
    count: visualUnits,
    activeCount: activeVisual,
    tankSocPct: Math.max(20, Math.min(95, Number(node.live?.tank_soc_pct || 60))),
    loadRatio: activeRatio,
    roomStyle,
  };
}

export function createSwissNetworkController({
  sceneCtrl,
  onExitToBasel,
  onConfigureNode,
  onOpenHeatLedger,
  onBookComputeAtSite,
}) {
  const overlay = document.querySelector("[data-testid='swiss-network-overlay']");
  const mapHost = document.getElementById("swiss-map-svg-host");
  const rosterEl = document.getElementById("swiss-node-roster");
  const detailEl = document.getElementById("swiss-node-detail");
  const kpiHost = document.getElementById("swiss-fleet-kpis");
  const flowsHost = document.getElementById("swiss-active-flows");
  const twinBar = document.querySelector("[data-testid='swiss-twin-bar']");
  const twinBarInfo = document.getElementById("swiss-twin-bar-info");
  const rtxStage = document.querySelector("[data-testid='swiss-rtx-stage']");
  const rtxVideo = document.getElementById("swiss-rtx-video");
  const rtxBadge = document.getElementById("swiss-rtx-badge");
  const siteBasementHost = document.getElementById("sn-site-basement-host");
  const viewExtBtn = document.getElementById("sn-view-exterior");
  const viewBasBtn = document.getElementById("sn-view-basement");
  const rtxBackBtn = document.getElementById("sn-rtx-back-btn");
  const webglToggleBtn = document.getElementById("sn-tb-webgl");
  const siteBasementStudio = siteBasementHost ? new Basement3DStudio(siteBasementHost) : null;
  let siteStageMode = "exterior"; // "exterior" | "basement"

  if (!overlay || !mapHost) {
    return {
      open: async () => {},
      close: () => {},
      isOpen: () => false,
      inspectTwinById: async () => {},
      setSeason: async () => {},
    };
  }

  let isMapOpen = false;
  let isTwinInspecting = false;
  let currentDoy = 196;
  let currentHour = 14;
  let currentStrategy = "smart";
  let currentFilter = "all";
  let selectedNodeId = "basel_st_jakob";
  let latestData = null;

  // Interactive 3D Swisstopo Relief Map state
  let map3dReady = false;
  let reliefLoaded = false;
  let mapScene = null;
  let mapCamera = null;
  let mapRenderer = null;
  let mapControls = null;
  let labelLayer = null;
  let nodesGroup = null;
  let flowsGroup = null;
  let animHandle = null;
  let resizeObs = null;
  let animatedPackets = [];
  let beaconHitTargets = [];
  let labelEntries = [];
  let flowMidEntries = [];
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const projVec = new THREE.Vector3();

  function setDefaultCamera() {
    if (!mapCamera || !mapControls) return;
    const w = Math.max(320, mapHost?.clientWidth || 960);
    const h = Math.max(260, mapHost?.clientHeight || 540);
    const aspect = w / h;
    if (aspect < 1.35) {
      mapCamera.position.set(-0.4, 26.5, 34.5);
      mapControls.target.set(-0.2, 0.7, -0.1);
    } else {
      mapCamera.position.set(-0.6, 17.8, 22.8);
      mapControls.target.set(-0.2, 0.85, -0.35);
    }
    mapControls.update();
  }

  function resize3D() {
    if (!mapRenderer || !mapCamera || !mapHost) return;
    const w = Math.max(320, mapHost.clientWidth);
    const h = Math.max(260, mapHost.clientHeight);
    mapCamera.aspect = w / h;
    mapCamera.updateProjectionMatrix();
    mapRenderer.setSize(w, h, false);
  }

  function ensure3DMap() {
    if (map3dReady) {
      resize3D();
      return;
    }
    map3dReady = true;
    mapHost.innerHTML = "";

    const canvas = document.createElement("canvas");
    canvas.className = "sn-3d-canvas";
    canvas.setAttribute("aria-label", "Interactive 3D Swisstopo Relief Map of Switzerland");
    mapHost.appendChild(canvas);

    labelLayer = document.createElement("div");
    labelLayer.className = "sn-3d-labels";
    mapHost.appendChild(labelLayer);

    const hintPill = document.createElement("div");
    hintPill.className = "sn-3d-corner-hud";
    hintPill.innerHTML = `<button type="button" class="sn-3d-reset-btn" id="sn-reset-3d-cam">Reset view</button>`;
    mapHost.appendChild(hintPill);
    hintPill.querySelector("#sn-reset-3d-cam")?.addEventListener("click", () => setDefaultCamera());

    mapScene = new THREE.Scene();

    const w = Math.max(320, mapHost.clientWidth || 960);
    const h = Math.max(260, mapHost.clientHeight || 540);
    mapCamera = new THREE.PerspectiveCamera(36, w / h, 0.2, 160);

    mapRenderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true,
    });
    mapRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    mapRenderer.outputColorSpace = THREE.SRGBColorSpace;
    mapRenderer.toneMapping = THREE.ACESFilmicToneMapping;
    mapRenderer.toneMappingExposure = 1.06;
    mapRenderer.setSize(w, h, false);

    mapControls = new OrbitControls(mapCamera, canvas);
    mapControls.enableDamping = true;
    mapControls.dampingFactor = 0.08;
    mapControls.minDistance = 12.5;
    mapControls.maxDistance = 48.0;
    mapControls.minPolarAngle = 0.24;
    mapControls.maxPolarAngle = 1.24;
    setDefaultCamera();

    // Clean sunlit Alpine architectural lighting
    const hemi = new THREE.HemisphereLight(0xf8fbff, 0x9fb0c4, 1.15);
    mapScene.add(hemi);

    const sun = new THREE.DirectionalLight(0xfff9f0, 1.55);
    sun.position.set(-22, 34, 18);
    mapScene.add(sun);

    const rim = new THREE.DirectionalLight(0xdbeafe, 0.55);
    rim.position.set(24, 18, -22);
    mapScene.add(rim);

    // Clean architectural ground plane resting right below Switzerland's minimum terrain elevation (y=0.576)
    const tablePlane = new THREE.Mesh(
      new THREE.PlaneGeometry(92, 64),
      new THREE.MeshStandardMaterial({
        color: 0xeef2f6,
        roughness: 0.96,
        metalness: 0.0,
        transparent: true,
        opacity: 0.85,
      })
    );
    tablePlane.rotation.x = -Math.PI / 2;
    tablePlane.position.y = 0.54;
    mapScene.add(tablePlane);

    const grid = new THREE.GridHelper(60, 30, 0xd4dde8, 0xe2e8f0);
    grid.position.y = 0.55;
    grid.material.transparent = true;
    grid.material.opacity = 0.30;
    mapScene.add(grid);

    nodesGroup = new THREE.Group();
    flowsGroup = new THREE.Group();
    mapScene.add(flowsGroup);
    mapScene.add(nodesGroup);

    // Subtle national backbone splines resting lightly over the terrain
    const backboneMat = new THREE.LineBasicMaterial({
      color: 0x475569,
      transparent: true,
      opacity: 0.34,
    });
    for (const [a, b] of BACKBONE_LINKS) {
      const ca = NODE_3D_COORDS[a];
      const cb = NODE_3D_COORDS[b];
      if (!ca || !cb) continue;
      const p1 = new THREE.Vector3(ca[0], ca[1] + 0.14, ca[2]);
      const p2 = new THREE.Vector3(cb[0], cb[1] + 0.14, cb[2]);
      const mid = p1.clone().lerp(p2, 0.5);
      mid.y = Math.max(p1.y, p2.y) + 0.55;
      const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2);
      const geo = new THREE.BufferGeometry().setFromPoints(curve.getPoints(18));
      mapScene.add(new THREE.Line(geo, backboneMat));
    }

    // Load sculpted 3D Swisstopo relief GLB (100% offline bundled asset)
    const loader = new GLTFLoader();
    loader.load(
      "/assets/glb/switzerland_3d_relief.glb",
      (gltf) => {
        reliefLoaded = true;
        gltf.scene.traverse((obj) => {
          if (obj.isMesh && obj.material) {
            // Hide the extruded white/gray border plinth wedges so only the clean sculpted 3D relief renders
            if (obj.material.name === "ArchitecturalPlinthMat") {
              obj.visible = false;
              return;
            }
            if (obj.material.map) {
              obj.material.map.anisotropy = Math.min(8, mapRenderer.capabilities.getMaxAnisotropy());
            }
          }
        });
        mapScene.add(gltf.scene);
      },
      undefined,
      (err) => console.error("Failed to load switzerland_3d_relief.glb:", err)
    );

    // Single-click on a 3D deployment beacon opens its 3D cinematic view + installed server stats
    canvas.addEventListener("click", (ev) => {
      if (!beaconHitTargets.length) return;
      const rect = canvas.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, mapCamera);
      const hits = raycaster.intersectObjects(beaconHitTargets, false);
      if (hits.length > 0 && hits[0].object.userData?.nodeId) {
        selectedNodeId = hits[0].object.userData.nodeId;
        inspectTwinById(selectedNodeId);
      }
    });

    resizeObs = new ResizeObserver(() => resize3D());
    resizeObs.observe(mapHost);
  }

  function clearGroup(grp) {
    if (!grp) return;
    while (grp.children.length > 0) {
      const ch = grp.children.pop();
      ch.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
          else o.material.dispose();
        }
      });
    }
  }

  function updateProjectedLabels() {
    if (!mapCamera || !mapHost || !labelEntries.length) return;
    const w = mapHost.clientWidth || 960;
    const h = mapHost.clientHeight || 540;

    // 1. Project 3D anchor points to screen pixels
    const placed = [];
    for (const item of labelEntries) {
      projVec.copy(item.anchor).project(mapCamera);
      if (projVec.z > 1.0) {
        item.el.style.opacity = "0";
        continue;
      }
      const sx = (projVec.x * 0.5 + 0.5) * w;
      const sy = (-projVec.y * 0.5 + 0.5) * h;
      placed.push({
        item,
        x: Math.max(76, Math.min(w - 76, sx)),
        y: Math.max(46, Math.min(h - 24, sy - 14)),
        w: item.el.offsetWidth || 126,
        h: item.el.offsetHeight || 22,
      });
    }

    // 2. Gentle iterative repulsion so closely-spaced Basel / Bern / Lucerne pills never collide
    const isNarrow = w < 520;
    const iters = isNarrow ? 16 : 10;
    const padX = isNarrow ? 8 : 6;
    const padY = isNarrow ? 6 : 4;
    for (let iter = 0; iter < iters; iter++) {
      for (let i = 0; i < placed.length; i++) {
        for (let j = i + 1; j < placed.length; j++) {
          const a = placed[i];
          const b = placed[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const minDx = (a.w + b.w) * 0.5 + padX;
          const minDy = (a.h + b.h) * 0.5 + padY;
          if (Math.abs(dx) < minDx && Math.abs(dy) < minDy) {
            const pushY = (minDy - Math.abs(dy)) * 0.56;
            if (dy >= 0) {
              a.y -= pushY;
              b.y += pushY;
            } else {
              a.y += pushY;
              b.y -= pushY;
            }
            const pushX = (minDx - Math.abs(dx)) * 0.18;
            if (dx >= 0) {
              a.x -= pushX;
              b.x += pushX;
            } else {
              a.x += pushX;
              b.x -= pushX;
            }
          }
        }
      }
    }

    for (const p of placed) {
      const cx = Math.max(p.w * 0.5 + 6, Math.min(w - p.w * 0.5 - 6, p.x));
      const minY = isNarrow ? 58 : 44;
      const cy = Math.max(minY, Math.min(h - 16, p.y));
      p.item.el.style.opacity = "1";
      p.item.el.style.transform = `translate(${cx.toFixed(1)}px, ${cy.toFixed(1)}px) translate(-50%, -50%)`;
    }

    // Position active fiber-arc midpoint pills on desktop; keep mobile Alps clean and uncluttered
    for (const fm of flowMidEntries) {
      if (isNarrow) {
        fm.el.style.opacity = "0";
        continue;
      }
      projVec.copy(fm.anchor).project(mapCamera);
      if (projVec.z > 1.0) {
        fm.el.style.opacity = "0";
        continue;
      }
      const sx = (projVec.x * 0.5 + 0.5) * w;
      const sy = (-projVec.y * 0.5 + 0.5) * h;
      fm.el.style.opacity = "1";
      fm.el.style.transform = `translate(${sx.toFixed(1)}px, ${sy.toFixed(1)}px) translate(-50%, -50%)`;
    }
  }

  function startLoop() {
    if (animHandle !== null) return;
    const clock = new THREE.Clock();
    const tick = () => {
      if (!isMapOpen) {
        animHandle = null;
        return;
      }
      animHandle = requestAnimationFrame(tick);
      const t = clock.getElapsedTime();
      if (mapControls) mapControls.update();

      // Advance animated fiber workload packets smoothly along 3D parabolic arcs
      for (const pkt of animatedPackets) {
        const u = (t * pkt.speed + pkt.phase) % 1.0;
        pkt.curve.getPoint(u, pkt.mesh.position);
      }

      if (mapRenderer && mapScene && mapCamera) {
        mapRenderer.render(mapScene, mapCamera);
        updateProjectedLabels();
      }
    };
    animHandle = requestAnimationFrame(tick);
  }

  function stopLoop() {
    if (animHandle !== null) {
      cancelAnimationFrame(animHandle);
      animHandle = null;
    }
  }

  function renderMap() {
    if (!latestData) return;
    ensure3DMap();
    clearGroup(nodesGroup);
    clearGroup(flowsGroup);
    animatedPackets = [];
    beaconHitTargets = [];
    labelEntries = [];
    flowMidEntries = [];
    labelLayer.innerHTML = "";

    // 1. Build 14 sculpted 3D building server-fleet beacons + projected Swiss callouts
    for (const n of latestData.nodes) {
      const c = NODE_3D_COORDS[n.node_id];
      if (!c) continue;
      const isSelected = n.node_id === selectedNodeId;
      const badge = stateBadgeInfo(n.live.scheduler_state);
      const pillarH = 0.62 + Math.min(2.1, n.servers_installed * 0.095);

      // Flush base ring on the sculpted terrain
      const ringGeo = new THREE.RingGeometry(0.24, isSelected ? 0.58 : 0.40, 28);
      const ringMat = new THREE.MeshBasicMaterial({
        color: isSelected ? 0xea580c : badge.hex,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: isSelected ? 0.92 : 0.65,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(c[0], c[1] + 0.04, c[2]);
      nodesGroup.add(ring);

      // Vertical server-capacity beacon column
      const colMesh = new THREE.Mesh(
        new THREE.CylinderGeometry(0.11, 0.17, pillarH, 16),
        new THREE.MeshStandardMaterial({
          color: badge.hex,
          roughness: 0.30,
          metalness: 0.25,
          emissive: badge.hex,
          emissiveIntensity: isSelected ? 0.42 : 0.16,
        })
      );
      colMesh.position.set(c[0], c[1] + pillarH * 0.5, c[2]);
      colMesh.userData = { nodeId: n.node_id };
      nodesGroup.add(colMesh);
      beaconHitTargets.push(colMesh);

      // Crown sphere on top of column
      const capMesh = new THREE.Mesh(
        new THREE.SphereGeometry(isSelected ? 0.27 : 0.20, 18, 14),
        new THREE.MeshStandardMaterial({
          color: isSelected ? 0xfef08a : 0xffffff,
          roughness: 0.20,
          metalness: 0.10,
          emissive: badge.hex,
          emissiveIntensity: isSelected ? 0.55 : 0.28,
        })
      );
      capMesh.position.set(c[0], c[1] + pillarH + 0.12, c[2]);
      capMesh.userData = { nodeId: n.node_id };
      nodesGroup.add(capMesh);
      beaconHitTargets.push(capMesh);

      // Projected crisp HTML callout pill (retains .sn-node selector for E2E & video tests)
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = `sn-3d-pin sn-node${isSelected ? " is-selected" : ""}`;
      pill.dataset.nodeId = n.node_id;
      pill.innerHTML = `<i style="background:${badge.color}"></i><b>${SHORT_CITY[n.node_id] || n.city}</b><span>${n.live.heat_generated_kw.toFixed(1)} kW</span>`;
      pill.addEventListener("click", () => {
        selectedNodeId = n.node_id;
        inspectTwinById(n.node_id);
      });
      labelLayer.appendChild(pill);
      labelEntries.push({
        el: pill,
        anchor: new THREE.Vector3(c[0], c[1] + pillarH + 0.58, c[2]),
      });
    }

    // 2. Build 3D elevated parabolic fiber migration arcs arching over the Swiss Alps
    const pktGeo = new THREE.SphereGeometry(0.19, 14, 12);
    latestData.flows.forEach((f, idx) => {
      const ca = NODE_3D_COORDS[f.from_id];
      const cb = NODE_3D_COORDS[f.to_id];
      if (!ca || !cb) return;
      const p1 = new THREE.Vector3(ca[0], ca[1] + 0.85, ca[2]);
      const p2 = new THREE.Vector3(cb[0], cb[1] + 0.85, cb[2]);
      const dist = p1.distanceTo(p2);
      const mid = p1.clone().lerp(p2, 0.5);
      mid.y = Math.max(p1.y, p2.y) + 2.1 + dist * 0.14;
      const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2);

      const tubeRadius = Math.max(0.065, Math.min(0.13, 0.055 + f.kw_shifted * 0.018));
      const tube = new THREE.Mesh(
        new THREE.TubeGeometry(curve, 36, tubeRadius, 8, false),
        new THREE.MeshStandardMaterial({
          color: 0x0284c7,
          roughness: 0.25,
          metalness: 0.1,
          emissive: 0x0ea5e9,
          emissiveIntensity: 0.45,
          transparent: true,
          opacity: 0.88,
        })
      );
      flowsGroup.add(tube);

      // Two glowing workload packets traveling smoothly in 3D along the fiber route
      for (let k = 0; k < 2; k++) {
        const pm = new THREE.Mesh(
          pktGeo,
          new THREE.MeshBasicMaterial({ color: 0xe0f2fe })
        );
        flowsGroup.add(pm);
        animatedPackets.push({
          mesh: pm,
          curve,
          speed: 0.34 + (idx % 3) * 0.05,
          phase: (k * 0.5 + idx * 0.17) % 1.0,
        });
      }

      // Crisp arc apex badge for inter-regional routes
      if (dist >= 3.2) {
        const apex = curve.getPoint(0.5);
        const tagEl = document.createElement("div");
        tagEl.className = "sn-3d-flow-tag";
        tagEl.textContent = `${f.kw_shifted.toFixed(1)} kW ${workloadShort(f.workload)}`;
        labelLayer.appendChild(tagEl);
        flowMidEntries.push({
          el: tagEl,
          anchor: new THREE.Vector3(apex.x, apex.y + 0.45, apex.z),
        });
      }
    });

    if (mapRenderer && mapScene && mapCamera) {
      mapRenderer.render(mapScene, mapCamera);
      updateProjectedLabels();
    }
    startLoop();
  }

  async function fetchFleet() {
    const resp = await fetch(
      `/api/v1/fleet?day_of_year=${currentDoy}&hour=${currentHour}&strategy=${currentStrategy}`
    );
    if (!resp.ok) throw new Error(`Fleet API HTTP ${resp.status}`);
    latestData = await resp.json();
    renderAll();
    if (isTwinInspecting) {
      const node = latestData.nodes.find((n) => n.node_id === selectedNodeId);
      if (node) updateTwinBar(node);
    }
    return latestData;
  }

  function renderKpis() {
    if (!latestData || !kpiHost) return;
    const ls = latestData.live_summary;
    const fa = latestData.fleet_annual;
    const dumpedCol = ls.heat_dumped_kw > 0.05 ? "#c13515" : "#008a05";
    kpiHost.innerHTML = `
      <div class="sn-kpi">
        <span class="sn-kpi-label">Active Sites</span>
        <strong class="sn-kpi-val">${fa.total_nodes} Sites · ${fa.total_cantons} Cantons</strong>
        <span class="sn-kpi-sub">${fa.total_servers} servers (${Math.round(ls.servers_active)} heating now)</span>
      </div>
      <div class="sn-kpi">
        <span class="sn-kpi-label">Live Heat Output</span>
        <strong class="sn-kpi-val" style="color:#ea580c">${ls.heat_generated_kw.toFixed(1)} kW</strong>
        <span class="sn-kpi-sub">${fa.annual_useful_heat_mwh} MWh/yr useful building heat</span>
      </div>
      <div class="sn-kpi sn-kpi-desktop">
        <span class="sn-kpi-label">Shared Over Fiber</span>
        <strong class="sn-kpi-val" style="color:#0369a1">${ls.migrated_heat_kw.toFixed(1)} kW</strong>
        <span class="sn-kpi-sub">${ls.active_flows_count} live routes across Switzerland</span>
      </div>
      <div class="sn-kpi">
        <span class="sn-kpi-label">Wasted Heat</span>
        <strong class="sn-kpi-val" style="color:${dumpedCol}">${ls.heat_dumped_kw.toFixed(1)} kW</strong>
        <span class="sn-kpi-sub">100% reused (0 vs ${latestData.baseline_dumped_kwh.toLocaleString()} kWh)</span>
      </div>
      <div class="sn-kpi sn-kpi-desktop">
        <span class="sn-kpi-label">Average Energy Cost</span>
        <strong class="sn-kpi-val" style="color:#111827">CHF ${fa.opex_breakeven_chf.toFixed(2)}/hr</strong>
        <span class="sn-kpi-sub">Per server-hour · Now CHF ${ls.hourly_elec_chf.toFixed(2)}/h</span>
      </div>
    `;
  }

  function renderSelectedDetail() {
    if (!latestData || !detailEl) return;
    const n = latestData.nodes.find((x) => x.node_id === selectedNodeId) || latestData.nodes[0];
    if (!n) return;
    const badge = stateBadgeInfo(n.live.scheduler_state);
    const outFlows = latestData.flows.filter((f) => f.from_id === n.node_id);
    const inFlows = latestData.flows.filter((f) => f.to_id === n.node_id);

    let flowNote = "Balanced on local thermal buffer.";
    if (outFlows.length) {
      flowNote = `Offloading to ${outFlows.map((f) => `${f.to_name} (${f.kw_shifted.toFixed(1)} kW, ${f.latency_ms} ms)`).join(", ")}`;
    } else if (inFlows.length) {
      flowNote = `Absorbing from ${inFlows.map((f) => `${f.from_name} (${f.kw_shifted.toFixed(1)} kW)`).join(", ")}`;
    }

    const rtxUrl = `/media/twins/${n.node_id}.mp4`;
    const hwText = hardwareSummaryForNode(n);
    detailEl.innerHTML = `
      <div class="sn-detail-head">
        <div>
          <span class="sn-detail-region">${n.region} · Canton ${n.canton} · ${n.elevation_m} m</span>
          <h3>${n.name}</h3>
          <div class="sn-detail-arch">${n.archetype}</div>
        </div>
        <span class="sn-state-pill" style="color:${badge.color};background:${badge.bg};border-color:${badge.color}44">${badge.text}</span>
      </div>
      <div class="sn-detail-thumb">
        <video src="${rtxUrl}" autoplay muted loop playsinline preload="auto"></video>
        <span>3D Cinematic RTX View · swisstopo 3D + SimReady USD</span>
      </div>
      <div class="sn-detail-hw">
        <span>Installed Hardware</span>
        <strong>${hwText}</strong>
      </div>
      <p class="sn-detail-summary">${n.summary}</p>
      <div class="sn-detail-grid">
        <div><span>Servers Active</span><strong>${n.live.servers_active.toFixed(1)} / ${n.servers_installed} x 1 kW</strong></div>
        <div><span>Heat Generated</span><strong style="color:#c2410c">${n.live.heat_generated_kw.toFixed(1)} kW_th (${n.target_supply_c} C)</strong></div>
        <div><span>Building Demand</span><strong>${n.live.heat_demand_kw.toFixed(1)} kW_th (${n.live.outdoor_temp_c} C out)</strong></div>
        <div><span>Tank SoC / Temp</span><strong>${n.live.tank_soc_pct.toFixed(0)}% (${n.live.tank_temp_c} C)</strong></div>
        <div><span>Electricity / Break-Even</span><strong>${n.tariff_chf_per_kwh.toFixed(3)} · ${n.annual.opex_breakeven_chf.toFixed(3)} CHF/h</strong></div>
        <div><span>Annual Heat / Waste</span><strong>${n.annual.annual_heat_covered_pct}% cov · ${n.annual.annual_dumped_kwh} kWh waste</strong></div>
      </div>
      <div class="sn-detail-flow">${flowNote}</div>
      <div class="sn-detail-actions">
        <button type="button" class="sn-btn-primary" id="sn-inspect-3d-btn" data-testid="swiss-inspect-twin-btn">Open 3D Cinema</button>
        <button type="button" class="sn-btn-ghost" id="sn-detail-configure-btn">Configure Similar</button>
      </div>
    `;

    detailEl.querySelector("#sn-inspect-3d-btn")?.addEventListener("click", () => inspectTwinById(n.node_id));
    detailEl.querySelector("#sn-detail-configure-btn")?.addEventListener("click", () => {
      if (onConfigureNode) onConfigureNode(n.node_id);
    });
  }

  function renderRoster() {
    if (!latestData || !rosterEl) return;
    const filtered = latestData.nodes.filter((n) => {
      if (currentFilter === "all") return true;
      if (currentFilter === "homes") return n.category === "residential";
      if (currentFilter === "farms") return n.category === "farm";
      return n.category === "pool" || n.category === "commercial" || n.category === "alpine";
    });
    rosterEl.innerHTML = filtered
      .map((n) => {
        const badge = stateBadgeInfo(n.live.scheduler_state);
        const sel = n.node_id === selectedNodeId ? "is-selected" : "";
        return `
          <button type="button" class="sn-roster-row ${sel}" data-roster-id="${n.node_id}">
            <div class="sn-roster-main">
              <strong>${n.name}</strong>
              <span>${n.city} (${n.canton}) · ${n.servers_installed} servers · ${n.live.heat_generated_kw.toFixed(1)} kW heat</span>
            </div>
            <div class="sn-roster-right">
              <span class="sn-roster-pill" style="color:${badge.color};background:${badge.bg}">${badge.text}</span>
              <span class="sn-roster-arrow">View 3D -></span>
            </div>
          </button>
        `;
      })
      .join("");
    rosterEl.querySelectorAll("[data-roster-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedNodeId = btn.dataset.rosterId;
        inspectTwinById(selectedNodeId);
      });
    });

    if (flowsHost) {
      if (!latestData.flows.length) {
        flowsHost.innerHTML = `<div class="sn-flow-empty">Winter peak: all 14 Swiss buildings absorb 100% of local server heat.</div>`;
      } else {
        flowsHost.innerHTML = latestData.flows
          .map(
            (f) =>
              `<div class="sn-flow-chip"><strong>${f.from_name}</strong> -&gt; <strong>${f.to_name}</strong> <span>${f.kw_shifted.toFixed(1)} kW (${workloadShort(f.workload)}, ${f.latency_ms} ms)</span></div>`
          )
          .join("");
      }
    }
  }

  function renderAll() {
    renderKpis();
    renderMap();
    renderSelectedDetail();
    renderRoster();
  }

  function updateTwinBar(node) {
    if (!twinBar || !twinBarInfo) return;
    const badge = stateBadgeInfo(node.live.scheduler_state);
    const hwText = hardwareSummaryForNode(node);
    const outFlows = (latestData?.flows || []).filter((f) => f.from_id === node.node_id);
    const inFlows = (latestData?.flows || []).filter((f) => f.to_id === node.node_id);
    let flowSummary = "Heating this building directly with zero thermal waste.";
    if (outFlows.length) {
      flowSummary = `Sharing excess compute over Swiss fiber with ${outFlows.map((f) => `${f.to_name} (${f.kw_shifted.toFixed(1)} kW)`).join(", ")}.`;
    } else if (inFlows.length) {
      flowSummary = `Receiving extra compute workloads from ${inFlows.map((f) => `${f.from_name} (${f.kw_shifted.toFixed(1)} kW)`).join(", ")}.`;
    }
    const activeRounded = Math.round(node.live.servers_active);
    twinBarInfo.innerHTML = `
      <div class="sn-tb-header">
        <div class="sn-tb-title">
          <strong>${node.name}</strong>
          <span>${node.archetype} · ${node.city}, ${node.canton}</span>
        </div>
        <span class="sn-state-pill" style="color:${badge.color};background:${badge.bg}">${badge.text}</span>
      </div>
      <div class="sn-tb-hardware">
        <strong>${hwText}</strong>
      </div>
      <div class="sn-tb-stats-grid">
        <div class="sn-tb-stat"><span>Active servers</span><b>${activeRounded} / ${node.servers_installed}</b></div>
        <div class="sn-tb-stat"><span>Heat output</span><b class="accent-heat">${node.live.heat_generated_kw.toFixed(1)} kW (${node.target_supply_c} C)</b></div>
        <div class="sn-tb-stat"><span>Annual heat covered</span><b>${node.annual.annual_heat_covered_pct}%</b></div>
        <div class="sn-tb-stat"><span>Heat wasted</span><b>${node.annual.annual_dumped_kwh} kWh</b></div>
      </div>
      <p class="sn-tb-flow">${flowSummary}</p>
    `;
  }

  function setSiteStageMode(mode, node = null) {
    const activeNode = node || latestData?.nodes?.find((n) => n.node_id === selectedNodeId);
    siteStageMode = mode === "basement" ? "basement" : "exterior";
    const isBas = siteStageMode === "basement";
    if (rtxVideo) rtxVideo.hidden = isBas;
    if (siteBasementHost) siteBasementHost.hidden = !isBas;
    viewExtBtn?.classList.toggle("is-active", !isBas);
    viewBasBtn?.classList.toggle("is-active", isBas);

    if (isBas) {
      if (rtxVideo) rtxVideo.pause();
      if (siteBasementStudio && activeNode) {
        siteBasementStudio.start();
        siteBasementStudio.syncConfig(nodeToBasementConfig(activeNode));
      }
      if (rtxBadge && activeNode) {
        rtxBadge.textContent = `Inside ${activeNode.name} · Drag to inspect installed servers in 3D`;
      }
    } else {
      siteBasementStudio?.stop();
      if (rtxVideo) rtxVideo.play().catch(() => {});
      if (rtxBadge && activeNode) {
        rtxBadge.textContent = `Outside 3D View · ${activeNode.name} (${activeNode.canton})`;
      }
    }
  }

  async function inspectTwinById(nodeId, preferWebgl = false) {
    if (!latestData) await fetchFleet();
    const node = latestData.nodes.find((n) => n.node_id === nodeId);
    if (!node) return;
    selectedNodeId = node.node_id;
    isMapOpen = false;
    stopLoop();
    isTwinInspecting = true;
    overlay.hidden = true;
    if (twinBar) twinBar.hidden = false;
    document.body.classList.add("panels-hidden", "site-cinema-open");
    updateTwinBar(node);

    const hasRealGlb = Boolean(node.glb_scene_key);
    if (webglToggleBtn) {
      webglToggleBtn.hidden = !hasRealGlb;
      webglToggleBtn.textContent = preferWebgl ? "RTX Cinema View" : "Interactive 3D";
      webglToggleBtn.dataset.mode = preferWebgl ? "webgl" : "rtx";
    }

    if (preferWebgl && hasRealGlb) {
      siteBasementStudio?.stop();
      if (rtxStage) rtxStage.hidden = true;
      if (rtxVideo) rtxVideo.pause();
      await sceneCtrl.showSwissTwin(node);
      sceneCtrl.smoothOrbit(16, 5200);
      return;
    }

    if (rtxStage && rtxVideo) {
      rtxStage.hidden = false;
      const src = `/media/twins/${node.node_id}.mp4`;
      if (rtxVideo.getAttribute("src") !== src) {
        rtxVideo.setAttribute("src", src);
        rtxVideo.load();
      }
      setSiteStageMode("exterior", node);
    }
  }

  async function open() {
    isMapOpen = true;
    isTwinInspecting = false;
    document.body.classList.remove("site-cinema-open");
    siteBasementStudio?.stop();
    if (twinBar) twinBar.hidden = true;
    if (rtxStage) rtxStage.hidden = true;
    if (rtxVideo) rtxVideo.pause();
    overlay.hidden = false;
    await fetchFleet();
    resize3D();
    setDefaultCamera();
    startLoop();
  }

  function close(restoreBasel = true) {
    isMapOpen = false;
    isTwinInspecting = false;
    stopLoop();
    siteBasementStudio?.stop();
    overlay.hidden = true;
    if (twinBar) twinBar.hidden = true;
    if (rtxStage) rtxStage.hidden = true;
    if (rtxVideo) rtxVideo.pause();
    document.body.classList.remove("panels-hidden", "site-cinema-open");
    if (restoreBasel) {
      sceneCtrl.clearSwissTwin();
      if (onExitToBasel) onExitToBasel();
    }
  }

  const MONTH_PRESETS = [
    { m: 1, name: "Jan", doy: 15, hour: 8, temp: "-4 C" },
    { m: 2, name: "Feb", doy: 46, hour: 9, temp: "1 C" },
    { m: 3, name: "Mar", doy: 75, hour: 12, temp: "7 C" },
    { m: 4, name: "Apr", doy: 105, hour: 14, temp: "12 C" },
    { m: 5, name: "May", doy: 135, hour: 14, temp: "18 C" },
    { m: 6, name: "Jun", doy: 166, hour: 14, temp: "24 C" },
    { m: 7, name: "Jul", doy: 196, hour: 14, temp: "29 C" },
    { m: 8, name: "Aug", doy: 227, hour: 14, temp: "27 C" },
    { m: 9, name: "Sep", doy: 258, hour: 14, temp: "19 C" },
    { m: 10, name: "Oct", doy: 288, hour: 14, temp: "12 C" },
    { m: 11, name: "Nov", doy: 319, hour: 10, temp: "5 C" },
    { m: 12, name: "Dec", doy: 349, hour: 8, temp: "-1 C" },
  ];

  function syncMonthUi(doy) {
    let best = MONTH_PRESETS[6];
    let bestDiff = 999;
    for (const mp of MONTH_PRESETS) {
      const d = Math.abs(mp.doy - doy);
      if (d < bestDiff) {
        bestDiff = d;
        best = mp;
      }
    }
    const sl = document.getElementById("sn-month-slider");
    const lbl = document.getElementById("sn-month-label");
    if (sl && Number(sl.value) !== best.m) sl.value = String(best.m);
    if (lbl) lbl.textContent = `${best.name} (${best.temp})`;
  }

  async function setSeason(doy, hour, strategy = currentStrategy) {
    currentDoy = doy;
    currentHour = hour;
    currentStrategy = strategy;
    syncMonthUi(doy);
    overlay.querySelectorAll("[data-sn-season]").forEach((btn) => {
      const active = Number(btn.dataset.doy) === doy;
      btn.classList.toggle("is-active", active);
    });
    overlay.querySelectorAll("[data-sn-strategy]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.snStrategy === strategy);
    });
    await fetchFleet();
  }

  let monthDebounce = null;
  document.getElementById("sn-month-slider")?.addEventListener("input", (e) => {
    const mIdx = Math.max(1, Math.min(12, Number(e.target.value))) - 1;
    const mp = MONTH_PRESETS[mIdx];
    const lbl = document.getElementById("sn-month-label");
    if (lbl) lbl.textContent = `${mp.name} (${mp.temp})`;
    if (monthDebounce) clearTimeout(monthDebounce);
    monthDebounce = setTimeout(() => setSeason(mp.doy, mp.hour, currentStrategy), 90);
  });

  overlay.querySelector("#close-swiss-network-btn")?.addEventListener("click", () => close(true));
  overlay.querySelector("#sn-order-server-btn")?.addEventListener("click", () => {
    if (onConfigureNode) onConfigureNode(selectedNodeId);
  });
  overlay.querySelector("#sn-book-compute-btn")?.addEventListener("click", () => {
    if (onBookComputeAtSite) onBookComputeAtSite("");
  });
  rtxBackBtn?.addEventListener("click", () => open());
  viewExtBtn?.addEventListener("click", () => setSiteStageMode("exterior"));
  viewBasBtn?.addEventListener("click", () => setSiteStageMode("basement"));
  overlay.querySelectorAll("[data-sn-season]").forEach((btn) => {
    btn.addEventListener("click", () => setSeason(Number(btn.dataset.doy), Number(btn.dataset.hour), currentStrategy));
  });
  overlay.querySelectorAll("[data-sn-strategy]").forEach((btn) => {
    btn.addEventListener("click", () => setSeason(currentDoy, currentHour, btn.dataset.snStrategy));
  });
  overlay.querySelectorAll("[data-sn-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentFilter = btn.dataset.snFilter;
      overlay.querySelectorAll("[data-sn-filter]").forEach((b) => b.classList.toggle("is-active", b === btn));
      renderRoster();
    });
  });

  if (twinBar) {
    twinBar.querySelector("#sn-tb-back-map")?.addEventListener("click", () => open());
    twinBar.querySelector("#sn-tb-exit")?.addEventListener("click", () => close(true));
    twinBar.querySelector("#sn-tb-configure")?.addEventListener("click", () => {
      if (onConfigureNode) onConfigureNode(selectedNodeId);
    });
    twinBar.querySelector("#sn-tb-heatledger")?.addEventListener("click", () => {
      if (onOpenHeatLedger) onOpenHeatLedger(selectedNodeId);
    });
    twinBar.querySelector("#sn-tb-book-compute")?.addEventListener("click", () => {
      if (onBookComputeAtSite) onBookComputeAtSite(selectedNodeId);
    });
    webglToggleBtn?.addEventListener("click", () => {
      const nextWebgl = webglToggleBtn.dataset.mode !== "webgl";
      inspectTwinById(selectedNodeId, nextWebgl);
    });
    twinBar.querySelector("#sn-tb-prev")?.addEventListener("click", () => {
      if (!latestData) return;
      const idx = latestData.nodes.findIndex((x) => x.node_id === selectedNodeId);
      const prev = latestData.nodes[(idx - 1 + latestData.nodes.length) % latestData.nodes.length];
      inspectTwinById(prev.node_id);
    });
    twinBar.querySelector("#sn-tb-next")?.addEventListener("click", () => {
      if (!latestData) return;
      const idx = latestData.nodes.findIndex((x) => x.node_id === selectedNodeId);
      const next = latestData.nodes[(idx + 1) % latestData.nodes.length];
      inspectTwinById(next.node_id);
    });
  }

  return {
    open,
    close,
    isOpen: () => isMapOpen || isTwinInspecting,
    isReliefLoaded: () => reliefLoaded,
    inspectTwinById,
    setSeason,
    resetCamera: setDefaultCamera,
    getLatest: () => latestData,
  };
}
