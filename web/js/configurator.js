import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import {
  buildGpuSlotsElement,
  domEl,
  fmtChf,
  fmtNum,
  renderLogFeed,
} from "./telemetry_logs.js?v=20261121v";

function buildStudioEnvironment(renderer) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envScene = new THREE.Scene();
  // Balanced architectural studio dome with realistic ceiling troffer light strips
  // so dark anodized aluminum stays deep obsidian while copper, stainless & glazed tiles catch crisp highlights
  envScene.background = new THREE.Color(0x3f4a58);

  const floorBounce = new THREE.Mesh(
    new THREE.PlaneGeometry(36, 36),
    new THREE.MeshBasicMaterial({ color: 0x262d38, side: THREE.DoubleSide })
  );
  floorBounce.rotation.x = Math.PI / 2;
  floorBounce.position.y = -8;
  envScene.add(floorBounce);

  const ceilingSoft = new THREE.Mesh(
    new THREE.PlaneGeometry(24, 16),
    new THREE.MeshBasicMaterial({ color: 0xc8d4e3, side: THREE.DoubleSide })
  );
  ceilingSoft.rotation.x = Math.PI / 2;
  ceilingSoft.position.y = 11.2;
  envScene.add(ceilingSoft);

  // Three bright linear ceiling troffer reflection strips matching our architectural fixtures
  const trofferMat = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide });
  for (const tx of [-6.5, 0.0, 6.5]) {
    const tStrip = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 1.3), trofferMat);
    tStrip.rotation.x = Math.PI / 2;
    tStrip.position.set(tx, 10.6, -1.5);
    envScene.add(tStrip);
  }

  const warmKey = new THREE.Mesh(
    new THREE.PlaneGeometry(11, 8.5),
    new THREE.MeshBasicMaterial({ color: 0xfff3de, side: THREE.DoubleSide })
  );
  warmKey.position.set(11.5, 6.5, 10);
  warmKey.lookAt(0, 0, 0);
  envScene.add(warmKey);

  const coolFill = new THREE.Mesh(
    new THREE.PlaneGeometry(10, 7.5),
    new THREE.MeshBasicMaterial({ color: 0xd0e4fa, side: THREE.DoubleSide })
  );
  coolFill.position.set(-11.5, 5.2, 9.5);
  coolFill.lookAt(0, 0, 0);
  envScene.add(coolFill);

  // Narrow specular rim strips for gleaming metallic highlights along rack bevels & stainless tanks
  const specMat = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide });
  for (const [sx, sy, sz] of [[7.5, 4.5, 13], [-7.5, 5.0, 13], [0, 7.5, 13.5]]) {
    const strip = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 8.5), specMat);
    strip.position.set(sx, sy, sz);
    strip.lookAt(0, 1.0, 0);
    envScene.add(strip);
  }
  const tex = pmrem.fromScene(envScene, 0.032).texture;
  pmrem.dispose();
  return tex;
}

function enableShadows(root, cast = true, receive = true) {
  root.traverse((obj) => {
    if (!obj.isMesh) return;
    obj.castShadow = cast;
    obj.receiveShadow = receive;
  });
  return root;
}

function disposeSubtree(group) {
  while (group.children.length > 0) {
    const ch = group.children.pop();
    ch.traverse((o) => {
      if (!o.isMesh) return;
      if (!o.userData.sharedTemplate && !o.userData.sharedGeometry && o.geometry) {
        o.geometry.dispose();
      }
      if (o.material) {
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of mats) {
          if (m.userData?.sharedMaterial) continue;
          if (!o.userData.sharedTemplate || o.userData.clonedMaterial) {
            m.dispose();
          }
        }
      }
    });
  }
}

let cachedShadowTex = null;
function getContactShadowTexture() {
  if (cachedShadowTex) return cachedShadowTex;
  const c = document.createElement("canvas");
  c.width = 128;
  c.height = 128;
  const ctx = c.getContext("2d");
  const grad = ctx.createRadialGradient(64, 64, 8, 64, 64, 62);
  grad.addColorStop(0, "rgba(10, 15, 26, 0.54)");
  grad.addColorStop(0.55, "rgba(10, 15, 26, 0.24)");
  grad.addColorStop(1, "rgba(10, 15, 26, 0.0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 128, 128);
  cachedShadowTex = new THREE.CanvasTexture(c);
  cachedShadowTex.userData.cached = true;
  return cachedShadowTex;
}

function makeContactShadow(width, depth) {
  const m = new THREE.Mesh(
    new THREE.PlaneGeometry(width, depth),
    new THREE.MeshBasicMaterial({
      map: getContactShadowTexture(),
      transparent: true,
      depthWrite: false,
    })
  );
  m.rotation.x = -Math.PI / 2;
  m.position.y = 0.005;
  return m;
}

let cachedAoStripTex = null;
function getAoStripTexture() {
  if (cachedAoStripTex) return cachedAoStripTex;
  const c = document.createElement("canvas");
  c.width = 16;
  c.height = 128;
  const ctx = c.getContext("2d");
  const g = ctx.createLinearGradient(0, 0, 0, 128);
  // Top (v=1) = transparent, bottom (v=0) = architectural seam shadow
  g.addColorStop(0.0, "rgba(8, 12, 20, 0.0)");
  g.addColorStop(0.45, "rgba(8, 12, 20, 0.07)");
  g.addColorStop(0.78, "rgba(8, 12, 20, 0.22)");
  g.addColorStop(1.0, "rgba(8, 12, 20, 0.48)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 16, 128);
  cachedAoStripTex = new THREE.CanvasTexture(c);
  cachedAoStripTex.userData.cached = true;
  return cachedAoStripTex;
}

const _UTIL_TEX_CACHE = {};

function getDialGaugeTexture(kind = "temp") {
  const key = `dial_${kind}`;
  if (_UTIL_TEX_CACHE[key]) return _UTIL_TEX_CACHE[key];
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 256;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#f8fafc";
  ctx.beginPath();
  ctx.arc(128, 128, 122, 0, Math.PI * 2);
  ctx.fill();
  ctx.lineWidth = 10;
  ctx.strokeStyle = "#334155";
  ctx.stroke();

  // Colored operating arc
  ctx.lineWidth = 14;
  ctx.beginPath();
  ctx.strokeStyle = kind === "temp" ? "#ea580c" : "#059669";
  ctx.arc(128, 128, 92, Math.PI * 0.78, Math.PI * 1.92);
  ctx.stroke();
  ctx.beginPath();
  ctx.strokeStyle = "#dc2626";
  ctx.arc(128, 128, 92, Math.PI * 1.92, Math.PI * 2.22);
  ctx.stroke();

  // Tick marks
  ctx.strokeStyle = "#1e293b";
  for (let i = 0; i <= 12; i++) {
    const a = Math.PI * 0.75 + (i / 12) * Math.PI * 1.5;
    const r0 = i % 3 === 0 ? 74 : 84;
    ctx.lineWidth = i % 3 === 0 ? 4 : 2;
    ctx.beginPath();
    ctx.moveTo(128 + Math.cos(a) * r0, 128 + Math.sin(a) * r0);
    ctx.lineTo(128 + Math.cos(a) * 102, 128 + Math.sin(a) * 102);
    ctx.stroke();
  }
  ctx.fillStyle = "#0f172a";
  ctx.font = "bold 28px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(kind === "temp" ? "55 C" : "2.4 bar", 128, 194);
  ctx.font = "bold 18px sans-serif";
  ctx.fillStyle = "#64748b";
  ctx.fillText(kind === "temp" ? "THERM" : "BAR", 128, 86);

  // Needle
  const na = kind === "temp" ? Math.PI * 1.62 : Math.PI * 1.38;
  ctx.strokeStyle = "#b91c1c";
  ctx.lineWidth = 6;
  ctx.beginPath();
  ctx.moveTo(128 - Math.cos(na) * 18, 128 - Math.sin(na) * 18);
  ctx.lineTo(128 + Math.cos(na) * 84, 128 + Math.sin(na) * 84);
  ctx.stroke();
  ctx.fillStyle = "#0f172a";
  ctx.beginPath();
  ctx.arc(128, 128, 11, 0, Math.PI * 2);
  ctx.fill();

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.userData.cached = true;
  _UTIL_TEX_CACHE[key] = tex;
  return tex;
}

function getFloorDrainTexture() {
  if (_UTIL_TEX_CACHE.drain) return _UTIL_TEX_CACHE.drain;
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 128;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#94a3b8";
  ctx.fillRect(0, 0, 512, 128);
  ctx.fillStyle = "#111827";
  ctx.fillRect(10, 16, 492, 96);
  // Brushed stainless transverse slats
  for (let x = 18; x < 496; x += 16) {
    const g = ctx.createLinearGradient(x, 16, x + 8, 112);
    g.addColorStop(0, "#cbd5e1");
    g.addColorStop(0.5, "#94a3b8");
    g.addColorStop(1, "#e2e8f0");
    ctx.fillStyle = g;
    ctx.fillRect(x, 18, 8, 92);
  }
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 6;
  ctx.strokeRect(4, 4, 504, 120);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  tex.userData.cached = true;
  _UTIL_TEX_CACHE.drain = tex;
  return tex;
}

function getPoolCausticTexture() {
  if (_UTIL_TEX_CACHE.poolCaustic) return _UTIL_TEX_CACHE.poolCaustic;
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 512;
  const ctx = c.getContext("2d");
  const bg = ctx.createLinearGradient(0, 0, 0, 512);
  bg.addColorStop(0.0, "#7dd3fc");
  bg.addColorStop(0.28, "#22d3ee");
  bg.addColorStop(0.70, "#0891b2");
  bg.addColorStop(1.0, "#0e7490");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, 512, 512);

  // Subtle submerged Olympic swimming lane stripes
  ctx.fillStyle = "rgba(12, 74, 110, 0.28)";
  ctx.fillRect(0, 355, 512, 22);
  ctx.fillRect(0, 425, 512, 18);

  // Overlapping luminous underwater sunlight caustic bands
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < 42; i++) {
    const cx = ((i * 97 + 41) % 512);
    const cy = ((i * 67 + 23) % 440);
    const rx = 38 + ((i * 29) % 58);
    const ry = 14 + ((i * 19) % 24);
    const g = ctx.createRadialGradient(cx, cy, 2, cx, cy, rx);
    g.addColorStop(0, "rgba(224, 252, 255, 0.34)");
    g.addColorStop(0.55, "rgba(103, 232, 249, 0.14)");
    g.addColorStop(1, "rgba(34, 211, 238, 0.0)");
    ctx.fillStyle = g;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1.0, ry / rx);
    ctx.beginPath();
    ctx.arc(0, 0, rx, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  ctx.strokeStyle = "rgba(240, 253, 255, 0.36)";
  ctx.lineWidth = 2.5;
  for (let w = 0; w < 9; w++) {
    ctx.beginPath();
    const y0 = 36 + w * 48;
    for (let x = 0; x <= 512; x += 16) {
      const y = y0 + Math.sin((x / 512) * Math.PI * 4 + w * 1.1) * 14 + Math.cos((x / 512) * Math.PI * 6 - w) * 6;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  ctx.globalCompositeOperation = "source-over";
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.userData.cached = true;
  _UTIL_TEX_CACHE.poolCaustic = tex;
  return tex;
}

function getAlpineWindowTexture() {
  if (_UTIL_TEX_CACHE.alpineWin) return _UTIL_TEX_CACHE.alpineWin;
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 256;
  const ctx = c.getContext("2d");
  const sky = ctx.createLinearGradient(0, 0, 0, 256);
  sky.addColorStop(0.0, "#245ca0");
  sky.addColorStop(0.48, "#6aa8df");
  sky.addColorStop(1.0, "#d8ecfc");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, 512, 256);

  // Distant Graubunden alpine range in crisp alpine haze
  ctx.fillStyle = "#5c85b0";
  ctx.beginPath();
  ctx.moveTo(0, 256);
  ctx.lineTo(0, 148);
  ctx.lineTo(68, 86);
  ctx.lineTo(148, 134);
  ctx.lineTo(242, 62);
  ctx.lineTo(334, 126);
  ctx.lineTo(426, 72);
  ctx.lineTo(512, 122);
  ctx.lineTo(512, 256);
  ctx.closePath();
  ctx.fill();

  // Sunlit snow-bright alpine faces (right-facing sunlit slopes)
  ctx.fillStyle = "#f4f9ff";
  ctx.beginPath();
  ctx.moveTo(68, 86); ctx.lineTo(148, 134); ctx.lineTo(94, 152); ctx.closePath(); ctx.fill();
  ctx.beginPath();
  ctx.moveTo(242, 62); ctx.lineTo(334, 126); ctx.lineTo(255, 165); ctx.closePath(); ctx.fill();
  ctx.beginPath();
  ctx.moveTo(426, 72); ctx.lineTo(512, 122); ctx.lineTo(435, 160); ctx.closePath(); ctx.fill();

  // Foreground snow peaks & dark granite pine-valley ridges
  ctx.fillStyle = "#eaf4ff";
  ctx.beginPath();
  ctx.moveTo(0, 256);
  ctx.lineTo(0, 178);
  ctx.lineTo(104, 122);
  ctx.lineTo(192, 168);
  ctx.lineTo(308, 108);
  ctx.lineTo(405, 158);
  ctx.lineTo(512, 134);
  ctx.lineTo(512, 256);
  ctx.closePath();
  ctx.fill();

  ctx.fillStyle = "rgba(44, 72, 108, 0.56)";
  ctx.beginPath();
  ctx.moveTo(104, 122); ctx.lineTo(146, 256); ctx.lineTo(42, 256); ctx.closePath(); ctx.fill();
  ctx.beginPath();
  ctx.moveTo(308, 108); ctx.lineTo(348, 256); ctx.lineTo(245, 256); ctx.closePath(); ctx.fill();

  // Dark alpine spruce silhouette along window sill
  ctx.fillStyle = "#1e3a4c";
  for (let px = 14; px < 500; px += 28) {
    const h = 24 + ((px * 17) % 22);
    ctx.beginPath();
    ctx.moveTo(px, 256);
    ctx.lineTo(px + 9, 256 - h);
    ctx.lineTo(px + 18, 256);
    ctx.closePath();
    ctx.fill();
  }

  // Soft frosted glazing glow at outer pane edges
  const frost = ctx.createRadialGradient(256, 128, 90, 256, 128, 290);
  frost.addColorStop(0, "rgba(255,255,255,0.02)");
  frost.addColorStop(1, "rgba(235,246,255,0.34)");
  ctx.fillStyle = frost;
  ctx.fillRect(0, 0, 512, 256);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.userData.cached = true;
  _UTIL_TEX_CACHE.alpineWin = tex;
  return tex;
}

function getHospitalScreenTexture() {
  if (_UTIL_TEX_CACHE.hospScreen) return _UTIL_TEX_CACHE.hospScreen;
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 256;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#0b1726";
  ctx.fillRect(0, 0, 512, 256);
  ctx.fillStyle = "#0f2942";
  ctx.fillRect(0, 0, 512, 42);
  ctx.fillStyle = "#38bdf8";
  ctx.font = "bold 18px sans-serif";
  ctx.fillText("ST. GALLEN STERILE DHW WING  |  24/7 RECOVERY", 18, 27);
  ctx.fillStyle = "#22c55e";
  ctx.beginPath();
  ctx.arc(482, 21, 7, 0, Math.PI * 2);
  ctx.fill();

  // Telemetry cards
  const cards = [
    ["DHW SUPPLY", "60.2 C", "#f97316", 20],
    ["LEGIONELLA", "SAFE", "#22c55e", 184],
    ["HEAT COVER", "99.0%", "#38bdf8", 348],
  ];
  for (const [lbl, val, col, x] of cards) {
    ctx.fillStyle = "#132338";
    ctx.fillRect(x, 58, 146, 76);
    ctx.strokeStyle = "rgba(56,189,248,0.28)";
    ctx.lineWidth = 2;
    ctx.strokeRect(x, 58, 146, 76);
    ctx.fillStyle = "#94a3b8";
    ctx.font = "bold 13px sans-serif";
    ctx.fillText(lbl, x + 12, 80);
    ctx.fillStyle = col;
    ctx.font = "bold 28px monospace";
    ctx.fillText(val, x + 12, 118);
  }

  // Smooth thermal baseload chart
  ctx.fillStyle = "#101e30";
  ctx.fillRect(20, 150, 472, 88);
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 3;
  ctx.beginPath();
  for (let x = 0; x <= 450; x += 10) {
    const y = 198 - Math.sin(x * 0.025) * 14 - Math.cos(x * 0.06) * 6;
    if (x === 0) ctx.moveTo(30 + x, y);
    else ctx.lineTo(30 + x, y);
  }
  ctx.stroke();
  ctx.strokeStyle = "#f97316";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  for (let x = 0; x <= 450; x += 10) {
    const y = 212 - Math.sin(x * 0.025 + 0.4) * 10;
    if (x === 0) ctx.moveTo(30 + x, y);
    else ctx.lineTo(30 + x, y);
  }
  ctx.stroke();

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.userData.cached = true;
  _UTIL_TEX_CACHE.hospScreen = tex;
  return tex;
}

function getBoreholeControllerTexture() {
  if (_UTIL_TEX_CACHE.boreholeHmi) return _UTIL_TEX_CACHE.boreholeHmi;
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 256;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#0e1d2b";
  ctx.fillRect(0, 0, 256, 256);
  ctx.fillStyle = "#0f766e";
  ctx.fillRect(0, 0, 256, 38);
  ctx.fillStyle = "#f0fdfa";
  ctx.font = "bold 15px sans-serif";
  ctx.fillText("SIA 384/6 ERDSONDE", 14, 24);
  ctx.fillStyle = "#94a3b8";
  ctx.font = "bold 13px sans-serif";
  ctx.fillText("BOREHOLE DEPTH: 180 m", 14, 68);
  ctx.fillStyle = "#2dd4bf";
  ctx.font = "bold 30px monospace";
  ctx.fillText("+11.4 C", 14, 108);
  ctx.fillStyle = "#cbd5e1";
  ctx.font = "13px sans-serif";
  ctx.fillText("SUMMER RECHARGE: ACTIVE", 14, 138);
  ctx.fillStyle = "#f97316";
  ctx.fillRect(14, 158, 178, 14);
  ctx.fillStyle = "#1e3a4c";
  ctx.fillRect(192, 158, 50, 14);
  ctx.fillStyle = "#94a3b8";
  ctx.font = "12px monospace";
  ctx.fillText("SEASONAL STORE: 78%", 14, 194);
  ctx.fillStyle = "#22c55e";
  ctx.fillText("FLOW 18.4 L/min  |  OK", 14, 224);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.userData.cached = true;
  _UTIL_TEX_CACHE.boreholeHmi = tex;
  return tex;
}

function enhanceHardwareMaterials(root, key = "") {
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    obj.userData.sharedTemplate = true;
    obj.castShadow = true;
    obj.receiveShadow = true;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (key === "gb300") {
        // Rich dark anodized NVIDIA chassis + gleaming saturated rose-copper 18-tray compute stack + vivid green LEDs
        mat.color = new THREE.Color(0xe4e8ee);
        mat.envMapIntensity = 1.12;
        if (mat.emissive && (mat.emissive.r + mat.emissive.g + mat.emissive.b) > 0.05) {
          mat.color = new THREE.Color(0xffffff);
          mat.emissiveIntensity = 1.85;
        }
        mat.needsUpdate = true;
        continue;
      }
      if (key === "ocpPop" || key === "vr3307") {
        // Authentic Vertiv dark steel cabinet + brushed stainless OCP shelves + vivid green hot-swap handles
        mat.color = new THREE.Color(key === "ocpPop" ? 0xdbe1e9 : 0xcbd3dd);
        mat.envMapIntensity = 1.04;
        mat.needsUpdate = true;
        continue;
      }
      const c = mat.color;
      const isVeryDark = c && c.r < 0.04 && c.g < 0.04 && c.b < 0.04;
      if (mat.name === "vertiv_black" || isVeryDark) {
        // Sleek brushed anodized charcoal with readable chamfered louvers, intake grilles & crisp specular rim
        mat.color = new THREE.Color(0x2d3542);
        mat.metalness = 0.56;
        mat.roughness = 0.30;
        mat.emissive = new THREE.Color(0x000000);
        mat.emissiveIntensity = 0.0;
        mat.envMapIntensity = 1.38;
        mat.needsUpdate = true;
      } else if (key === "tank") {
        // Brushed stainless steel thermal vessel with crisp overhead reflection bands
        mat.color = new THREE.Color(0xa6b4c2);
        mat.metalness = 0.78;
        mat.roughness = 0.25;
        mat.envMapIntensity = 1.24;
        mat.needsUpdate = true;
      } else if (mat.metalness !== undefined && mat.metalness > 0.7) {
        mat.roughness = Math.max(0.18, Math.min(0.40, mat.roughness ?? 0.26));
        mat.envMapIntensity = 1.18;
        mat.needsUpdate = true;
      }
    }
  });
}

function normalizeHardwareTemplate(sceneRoot, rotY = 0, key = "") {
  const wrapper = new THREE.Group();
  const inner = sceneRoot;
  if (rotY) inner.rotation.y = rotY;
  inner.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(inner);
  const center = new THREE.Vector3();
  const size = new THREE.Vector3();
  box.getCenter(center);
  box.getSize(size);
  inner.position.x -= center.x;
  inner.position.z -= center.z;
  inner.position.y -= box.min.y;
  enhanceHardwareMaterials(inner, key);
  wrapper.add(inner);
  wrapper.userData.size = { x: size.x, y: size.y, z: size.z };
  return wrapper;
}

const MODEL_VISUALS = {
  hydronode_1kw: {
    templateKey: "vr3307",
    scale: [0.96, 0.70, 0.74],
    accentHex: 0x06b6d4,
    ledHex: 0x22d3ee,
    pipeRadius: 0.026,
    badge: "1 kW Liquid Module",
  },
  ocp_winterfell_3kw: {
    templateKey: "ocpPop",
    scale: [1.12, 0.98, 0.94],
    accentHex: 0xf59e0b,
    ledHex: 0xfbbf24,
    pipeRadius: 0.032,
    badge: "2.9 kW Hybrid Rack",
  },
  b200_ai_10kw: {
    templateKey: "gb300",
    scale: [1.10, 1.0, 0.90],
    accentHex: 0xea580c,
    ledHex: 0x76b900,
    pipeRadius: 0.038,
    badge: "10.4 kW Liquid AI Rack",
  },
  edge_micro_250w: {
    templateKey: "vr3307",
    scale: [0.76, 0.44, 0.56],
    accentHex: 0x10b981,
    ledHex: 0x4ade80,
    pipeRadius: 0.020,
    badge: "0.25 kW Micro-Cluster",
  },
};

function makePipeMesh(p1, p2, radius, mat) {
  const v1 = new THREE.Vector3(...p1);
  const v2 = new THREE.Vector3(...p2);
  const dir = v2.clone().sub(v1);
  const len = dir.length();
  if (len < 1e-4) return null;
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, len, 16), mat);
  mesh.position.copy(v1).addScaledVector(dir, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function makeFlangeCollar(point, towardPoint, radius, mat) {
  const v1 = new THREE.Vector3(...point);
  const v2 = new THREE.Vector3(...towardPoint);
  const dir = v2.sub(v1);
  if (dir.lengthSq() < 1e-6) return null;
  const collar = new THREE.Mesh(
    new THREE.CylinderGeometry(radius * 1.68, radius * 1.68, 0.048, 16),
    mat
  );
  collar.position.copy(v1);
  collar.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  collar.castShadow = true;
  collar.receiveShadow = true;
  return collar;
}

function buildOrthogonalPipe(parent, waypoints, radius, pipeMat, flangeMat = null, flangeEnds = [false, false]) {
  const curvePath = new THREE.CurvePath();
  if (!waypoints || waypoints.length < 2) return curvePath;
  const elbowGeo = new THREE.SphereGeometry(radius * 1.14, 12, 10);
  for (let i = 0; i < waypoints.length - 1; i++) {
    const a = waypoints[i];
    const b = waypoints[i + 1];
    const seg = makePipeMesh(a, b, radius, pipeMat);
    if (seg) parent.add(seg);
    curvePath.add(new THREE.LineCurve3(new THREE.Vector3(...a), new THREE.Vector3(...b)));
    if (i > 0) {
      const elbow = new THREE.Mesh(elbowGeo, pipeMat);
      elbow.position.set(a[0], a[1], a[2]);
      elbow.castShadow = true;
      elbow.receiveShadow = true;
      parent.add(elbow);
    }
  }
  if (flangeMat && flangeEnds[0]) {
    const f0 = makeFlangeCollar(waypoints[0], waypoints[1], radius, flangeMat);
    if (f0) parent.add(f0);
  }
  if (flangeMat && flangeEnds[1]) {
    const n = waypoints.length;
    const f1 = makeFlangeCollar(waypoints[n - 1], waypoints[n - 2], radius, flangeMat);
    if (f1) parent.add(f1);
  }
  return curvePath;
}

function makeAnalogGauge(kind, brassMat) {
  const grp = new THREE.Group();
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.07, 10), brassMat);
  stem.position.y = 0.035;
  grp.add(stem);
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.056, 0.056, 0.036, 18), brassMat);
  body.rotation.x = Math.PI / 2;
  body.position.y = 0.11;
  grp.add(body);
  const faceMat = new THREE.MeshStandardMaterial({
    map: getDialGaugeTexture(kind),
    roughness: 0.25,
    metalness: 0.05,
  });
  const face = new THREE.Mesh(new THREE.CircleGeometry(0.049, 18), faceMat);
  face.position.set(0, 0.11, 0.020);
  grp.add(face);
  return enableShadows(grp, true, true);
}

const ROOM_STYLES = {
  basel_basement: {
    bg: 0x2c333d,
    fogHex: 0x2c333d,
    fogDensity: 0.019,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xd0d8e2,
    beam: 0x525b68,
    accent: 0x334155,
    accentY: 0.07,
    accentH: 0.14,
    hemiTop: 0xe8f1fc,
    hemiBot: 0x455060,
    hemiInt: 0.92,
    keyHex: 0xfff5e6,
    keyInt: 1.65,
    fillHex: 0xc5ddf8,
    fillInt: 0.62,
    trofferHex: 0xfff6e8,
    trofferInt: 3.8,
    accentLight: { hex: 0xbfe2ff, pos: [-4.55, 2.32, -0.35], int: 3.4, dist: 6.8 },
    floorRough: 0.84,
    floorMetal: 0.12,
    floorEnv: 1.38,
    wallBump: 0.024,
    floorBump: 0.012,
    label: "Basel Residential Basement",
  },
  borehole_utility: {
    bg: 0xc8d3de,
    fogHex: 0xc8d3de,
    fogDensity: 0.012,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xe6edf4,
    beam: 0x94a3b8,
    accent: 0x0f766e,
    accentY: 1.18,
    accentH: 0.05,
    hemiTop: 0xf8fbff,
    hemiBot: 0x627386,
    hemiInt: 1.10,
    keyHex: 0xfffdf8,
    keyInt: 1.56,
    fillHex: 0xdbeafe,
    fillInt: 0.68,
    trofferHex: 0xf6faff,
    trofferInt: 3.6,
    accentLight: { hex: 0x5eead4, pos: [4.25, 1.55, -1.25], int: 1.8, dist: 5.5 },
    floorRough: 0.72,
    floorMetal: 0.10,
    floorEnv: 1.48,
    wallBump: 0.014,
    floorBump: 0.009,
    label: "Riehen Geothermal Borehole Utility",
  },
  alpine_chalet: {
    bg: 0x3b2d22,
    fogHex: 0x372a20,
    fogDensity: 0.020,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xc69c72,
    beam: 0x5c3c22,
    accent: 0x6f4d32,
    accentY: 0.94,
    accentH: 0.08,
    hemiTop: 0xffefd4,
    hemiBot: 0x573e2a,
    hemiInt: 0.96,
    keyHex: 0xffe4bc,
    keyInt: 1.65,
    fillHex: 0xc2dfff,
    fillInt: 0.65,
    trofferHex: 0xffe6bf,
    trofferInt: 4.1,
    accentLight: { hex: 0xc2e6ff, pos: [3.65, 2.08, -1.95], int: 4.8, dist: 7.2 },
    floorRough: 0.78,
    floorMetal: 0.10,
    floorEnv: 1.32,
    wallBump: 0.030,
    floorBump: 0.018,
    label: "Davos Alpine Chalet Plant Room",
  },
  emmental_dairy: {
    bg: 0x473626,
    fogHex: 0x423223,
    fogDensity: 0.018,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xc49a6c,
    beam: 0x5a3a20,
    accent: 0xcbd5e1,
    accentY: 1.34,
    accentH: 0.045,
    hemiTop: 0xfff3de,
    hemiBot: 0x614835,
    hemiInt: 1.04,
    keyHex: 0xffebca,
    keyInt: 1.62,
    fillHex: 0xfff3de,
    fillInt: 0.58,
    trofferHex: 0xffedd0,
    trofferInt: 3.9,
    accentLight: { hex: 0xffe4b5, pos: [4.25, 2.1, -0.85], int: 2.2, dist: 6.0 },
    floorRough: 0.76,
    floorMetal: 0.08,
    floorEnv: 1.30,
    wallBump: 0.025,
    floorBump: 0.016,
    label: "Emmental Creamery Plant Room",
  },
  thurgau_glasshouse: {
    bg: 0x9ac8b4,
    fogHex: 0xb2d8c6,
    fogDensity: 0.011,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xe6f7ee,
    beam: 0x165336,
    accent: 0x166534,
    accentY: 1.06,
    accentH: 0.06,
    hemiTop: 0xf7fff9,
    hemiBot: 0x567060,
    hemiInt: 1.24,
    keyHex: 0xfffbe6,
    keyInt: 1.72,
    fillHex: 0xdcfce7,
    fillInt: 0.72,
    trofferHex: 0xf7fff9,
    trofferInt: 3.0,
    accentLight: { hex: 0xfff0c4, pos: [4.25, 2.22, -0.68], int: 4.5, dist: 6.5 },
    floorRough: 0.86,
    floorMetal: 0.06,
    floorEnv: 1.15,
    wallBump: 0.018,
    floorBump: 0.014,
    label: "Thurgau Hydroponic Glasshouse Bay",
  },
  st_jakob_pool: {
    bg: 0x203c50,
    fogHex: 0x214054,
    fogDensity: 0.017,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xc5dde9,
    beam: 0x4b6a80,
    accent: 0x0284c7,
    accentY: 1.34,
    accentH: 0.08,
    hemiTop: 0xe0f5ff,
    hemiBot: 0x2d536b,
    hemiInt: 1.02,
    keyHex: 0xf0f9ff,
    keyInt: 1.50,
    fillHex: 0x7dd3fc,
    fillInt: 0.74,
    trofferHex: 0xeef8ff,
    trofferInt: 3.4,
    accentLight: { hex: 0x22d3ee, pos: [3.55, 1.85, -1.85], int: 6.0, dist: 7.8 },
    floorRough: 0.62,
    floorMetal: 0.14,
    floorEnv: 1.68,
    wallBump: 0.022,
    floorBump: 0.014,
    label: "St. Jakob Aquatic Plant Hall",
  },
  lucerne_bakery: {
    bg: 0x473a32,
    fogHex: 0x44372f,
    fogDensity: 0.017,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xe2d8cc,
    beam: 0x6b5c52,
    accent: 0x94a3b8,
    accentY: 1.14,
    accentH: 0.05,
    hemiTop: 0xfff8ee,
    hemiBot: 0x665243,
    hemiInt: 1.06,
    keyHex: 0xfff0d8,
    keyInt: 1.60,
    fillHex: 0xfeecd2,
    fillInt: 0.58,
    trofferHex: 0xfff1dc,
    trofferInt: 3.8,
    accentLight: { hex: 0xffd59e, pos: [-3.9, 1.85, -0.5], int: 2.6, dist: 6.0 },
    floorRough: 0.74,
    floorMetal: 0.09,
    floorEnv: 1.34,
    wallBump: 0.022,
    floorBump: 0.012,
    label: "Lucerne Bakery Washroom Bay",
  },
  stgallen_hospital: {
    bg: 0xc8d4e0,
    fogHex: 0xcdd9e4,
    fogDensity: 0.012,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xeef3f8,
    beam: 0x94a3b8,
    accent: 0x0ea5e9,
    accentY: 1.06,
    accentH: 0.045,
    hemiTop: 0xf8fbff,
    hemiBot: 0x64748b,
    hemiInt: 1.16,
    keyHex: 0xf8fbff,
    keyInt: 1.56,
    fillHex: 0xdbeafe,
    fillInt: 0.74,
    trofferHex: 0xf5faff,
    trofferInt: 3.8,
    accentLight: { hex: 0x38bdf8, pos: [3.8, 2.05, -1.85], int: 2.8, dist: 6.0 },
    floorRough: 0.58,
    floorMetal: 0.12,
    floorEnv: 1.65,
    wallBump: 0.014,
    floorBump: 0.008,
    label: "St. Gallen Sterile Mechanical Wing",
  },
};

const _ROOM_TEX_CACHE = {};

function makeCanvasTex(drawFn, repeatX, repeatY, size = 1024, isColor = true) {
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const ctx = c.getContext("2d");
  drawFn(ctx, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeatX, repeatY);
  if (isColor) tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  tex.userData.cached = true;
  return tex;
}

function makePbrSurface(drawAlbedo, drawBump, drawRough, rx, ry) {
  return {
    map: makeCanvasTex(drawAlbedo, rx, ry, 1024, true),
    bumpMap: makeCanvasTex(drawBump, rx, ry, 512, false),
    roughnessMap: makeCanvasTex(drawRough, rx, ry, 512, false),
  };
}

function makeSeededRand(seedInit = 1337) {
  let s = seedInit >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function drawMicroNoise(ctx, w, h, count, minV, spanV, alpha, sz = 3, seed = 4201) {
  const rand = makeSeededRand(seed);
  for (let i = 0; i < count; i++) {
    const v = minV + Math.floor(rand() * spanV);
    ctx.fillStyle = `rgba(${v},${v + 2},${v + 4},${alpha})`;
    ctx.fillRect(Math.floor(rand() * w), Math.floor(rand() * h), sz, sz);
  }
}

function drawTerrazzoFlecks(ctx, w, h, count, palette, seed = 7919) {
  const rand = makeSeededRand(seed);
  for (let i = 0; i < count; i++) {
    ctx.fillStyle = palette[Math.floor(rand() * palette.length)];
    const x = Math.floor(rand() * w);
    const y = Math.floor(rand() * h);
    const rw = 2 + Math.floor(rand() * 5);
    const rh = 2 + Math.floor(rand() * 4);
    ctx.fillRect(x, y, rw, rh);
  }
}

function getRoomTextures(styleKey) {
  const key = ROOM_STYLES[styleKey] ? styleKey : "basel_basement";
  if (_ROOM_TEX_CACHE[key]) return _ROOM_TEX_CACHE[key];

  let wall, floor, ceilTex;

  if (key === "basel_basement") {
    // Swiss Sichtbeton (board-formed architectural concrete) with crisp form-tie cones + satin epoxy aggregate floor
    wall = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#919aa6";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 7000, 118, 48, 0.22, 4);
        for (let y = 0; y < h; y += 128) {
          const bandShade = 138 + (((y / 128) * 11) % 16);
          ctx.fillStyle = `rgba(${bandShade},${bandShade + 4},${bandShade + 10},0.22)`;
          ctx.fillRect(0, y + 4, w, 120);
          ctx.fillStyle = "rgba(32,38,48,0.52)";
          ctx.fillRect(0, y, w, 4);
          ctx.fillStyle = "rgba(225,232,240,0.28)";
          ctx.fillRect(0, y + 4, w, 3);
          for (let x = 128; x < w; x += 256) {
            ctx.beginPath();
            ctx.arc(x, y + 64, 11, 0, Math.PI * 2);
            ctx.fillStyle = "rgba(30,36,44,0.62)";
            ctx.fill();
            ctx.beginPath();
            ctx.arc(x - 2, y + 62, 6, 0, Math.PI * 2);
            ctx.fillStyle = "rgba(74,84,96,0.65)";
            ctx.fill();
          }
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#b8b8b8";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 3600, 145, 55, 0.25, 3);
        for (let y = 0; y < h; y += 64) {
          ctx.fillStyle = "#222222";
          ctx.fillRect(0, y, w, 3);
          for (let x = 64; x < w; x += 128) {
            ctx.beginPath();
            ctx.arc(x, y + 32, 6, 0, Math.PI * 2);
            ctx.fillStyle = "#181818";
            ctx.fill();
          }
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#cfcfcf";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 2400, 180, 50, 0.25, 4);
        for (let y = 0; y < h; y += 64) {
          ctx.fillStyle = "#f2f2f2";
          ctx.fillRect(0, y, w, 3);
        }
      },
      3.4,
      1.25
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#4c5563";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            const s = 68 + ((r * 7 + c * 11) % 14);
            ctx.fillStyle = `rgb(${s},${s + 7},${s + 16})`;
            ctx.fillRect(c * 256 + 4, r * 256 + 4, 248, 248);
          }
        }
        drawMicroNoise(ctx, w, h, 6500, 54, 48, 0.22, 4);
        ctx.strokeStyle = "rgba(22,28,38,0.62)";
        ctx.lineWidth = 5;
        for (let k = 0; k <= w; k += 256) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#c8c8c8";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 2800, 165, 45, 0.2, 3);
        ctx.strokeStyle = "#262626";
        ctx.lineWidth = 4;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        // Satin sealed epoxy floor tiles with matte joints
        ctx.fillStyle = "#6e6e6e";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 2400, 92, 48, 0.28, 6);
        ctx.strokeStyle = "#e8e8e8";
        ctx.lineWidth = 5;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      4.4,
      3.1
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#7a8492";
      ctx.fillRect(0, 0, w, h);
      drawMicroNoise(ctx, w, h, 2200, 105, 36, 0.2, 4);
      ctx.strokeStyle = "rgba(42,50,60,0.35)";
      ctx.lineWidth = 3;
      for (let k = 0; k <= w; k += 128) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
      }
    }, 3.6, 2.4, 512, true);
  } else if (key === "borehole_utility") {
    // Bright Swiss Minergie utility: crisp mineral plaster upper wall + epoxy slate-teal lower wainscot + quartz terrazzo floor
    wall = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#e5ebf1";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 4200, 206, 28, 0.24, 3);
        // Epoxy lower protective wainscot
        ctx.fillStyle = "#b8c6d2";
        ctx.fillRect(0, h * 0.62, w, h * 0.38);
        drawMicroNoise(ctx, w, h, 2400, 162, 26, 0.16, 3);
        ctx.strokeStyle = "rgba(71, 85, 105, 0.18)";
        ctx.lineWidth = 3;
        for (let x = 0; x <= w; x += 256) {
          ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
        }
        ctx.fillStyle = "#0f766e";
        ctx.fillRect(0, h * 0.615, w, 10);
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#c0c0c0";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 3500, 165, 45, 0.22, 2);
        ctx.fillStyle = "#888888";
        ctx.fillRect(0, h * 0.615, w, 5);
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#d8d8d8";
        ctx.fillRect(0, 0, w, h * 0.62);
        ctx.fillStyle = "#7c7c7c";
        ctx.fillRect(0, h * 0.62, w, h * 0.38);
      },
      3.2,
      1.0
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#95a3b0";
        ctx.fillRect(0, 0, w, h);
        // Honed quartz terrazzo flecks (organic seeded distribution)
        drawTerrazzoFlecks(ctx, w, h, 5200, ["#e2e8f0", "#cbd5e1", "#64748b", "#475569", "#f8fafc"], 2111);
        ctx.strokeStyle = "rgba(51,65,85,0.34)";
        ctx.lineWidth = 4;
        for (let k = 0; k <= w; k += 256) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#c6c6c6";
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = "#3a3a3a";
        ctx.lineWidth = 3;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#5c5c5c";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 1800, 74, 42, 0.25, 5);
        ctx.strokeStyle = "#e0e0e0";
        ctx.lineWidth = 4;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      4.2,
      3.0
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#dce4ec";
      ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = "rgba(100,116,139,0.22)";
      ctx.lineWidth = 2;
      for (let k = 0; k <= w; k += 64) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
      }
    }, 3.6, 2.4, 512, true);
  } else if (key === "alpine_chalet") {
    // Engadin larch timber upper boards over chiseled alpine granite stone plinth + Vals quartzite slate floor
    wall = makePbrSurface(
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.70);
        const tones = ["#b8834d", "#a97340", "#c49059", "#9f6b3a", "#bc8852", "#ad7844"];
        const plankH = Math.round(splitY / 8);
        for (let r = 0; r < 8; r++) {
          const y0 = r * plankH;
          ctx.fillStyle = tones[r % tones.length];
          ctx.fillRect(0, y0, w, plankH);
          for (let g = 0; g < 18; g++) {
            ctx.fillStyle = g % 2 === 0 ? "rgba(62,34,12,0.13)" : "rgba(255,226,180,0.09)";
            ctx.fillRect(0, y0 + g * (plankH / 18), w, 2);
          }
          // Authentic wood knots
          const kx = ((r * 211 + 140) % (w - 80)) + 40;
          ctx.fillStyle = "rgba(74,42,18,0.45)";
          ctx.beginPath();
          ctx.ellipse(kx, y0 + plankH * 0.5, 16, 7, 0.1, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = "rgba(44,24,10,0.68)";
          ctx.fillRect(0, y0, w, 4);
        }
        // Chiseled Graubunden gneiss stone lower dado
        ctx.fillStyle = "#5c6268";
        ctx.fillRect(0, splitY, w, h - splitY);
        const rows = 4;
        const rh = (h - splitY) / rows;
        for (let r = 0; r < rows; r++) {
          const off = (r % 2) * 96;
          for (let x = -96; x < w + 96; x += 192) {
            const sh = 86 + ((r * 13 + Math.abs(x) * 7) % 34);
            ctx.fillStyle = `rgb(${sh},${sh + 4},${sh + 7})`;
            ctx.fillRect(x + off + 4, splitY + r * rh + 4, 184, rh - 8);
          }
        }
      },
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.70);
        ctx.fillStyle = "#c0c0c0";
        ctx.fillRect(0, 0, w, splitY);
        const plankH = Math.round(splitY / 8);
        for (let r = 0; r < 8; r++) {
          ctx.fillStyle = "#222222";
          ctx.fillRect(0, r * plankH, w, 3);
        }
        ctx.fillStyle = "#262626";
        ctx.fillRect(0, splitY, w, h - splitY);
        const rh = (h - splitY) / 4;
        for (let r = 0; r < 4; r++) {
          const off = (r % 2) * 48;
          for (let x = -48; x < w + 48; x += 96) {
            ctx.fillStyle = `rgb(${155 + ((r * 17 + Math.abs(x)) % 45)},${155 + ((r * 17 + Math.abs(x)) % 45)},${155 + ((r * 17 + Math.abs(x)) % 45)})`;
            ctx.fillRect(x + off + 3, splitY + r * rh + 3, 90, rh - 6);
          }
        }
      },
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.70);
        ctx.fillStyle = "#8e8e8e";
        ctx.fillRect(0, 0, w, splitY);
        ctx.fillStyle = "#d6d6d6";
        ctx.fillRect(0, splitY, w, h - splitY);
      },
      2.6,
      1.0
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        // Vals alpine quartzite slate floor slabs
        ctx.fillStyle = "#3f474e";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            const s = 62 + ((r * 11 + c * 17) % 24);
            ctx.fillStyle = `rgb(${s},${s + 6},${s + 10})`;
            ctx.fillRect(c * 256 + 4, r * 256 + 4, 248, 248);
            // Subtle mineral strata lines
            ctx.fillStyle = "rgba(180,200,210,0.09)";
            ctx.fillRect(c * 256 + 6, r * 256 + 42 + ((r * 23) % 90), 244, 14);
          }
        }
        drawMicroNoise(ctx, w, h, 4200, 48, 44, 0.22, 4);
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#c4c4c4";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 2600, 140, 60, 0.26, 4);
        ctx.strokeStyle = "#1c1c1c";
        ctx.lineWidth = 4;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#7a7a7a";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 2000, 96, 56, 0.28, 6);
        ctx.strokeStyle = "#ececec";
        ctx.lineWidth = 4;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      4.0,
      3.0
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      const tones = ["#a8794b", "#9d6e42", "#b38455", "#96683c"];
      for (let r = 0; r < 8; r++) {
        ctx.fillStyle = tones[r % tones.length];
        ctx.fillRect(0, r * 64, w, 64);
        ctx.fillStyle = "rgba(45,25,10,0.45)";
        ctx.fillRect(0, r * 64, w, 3);
      }
    }, 3.4, 2.2, 512, true);
  } else if (key === "emmental_dairy") {
    // Authentic Swiss creamery: warm honey Bernese spruce upper wall + hygienic glazed white ceramic lower dado + warm terracotta quarry tiles
    wall = makePbrSurface(
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.58);
        const tones = ["#b88450", "#ad7846", "#c28e5a", "#a36f3e", "#bc8854", "#aa7645"];
        const plankH = Math.round(splitY / 7);
        for (let r = 0; r < 7; r++) {
          const y0 = r * plankH;
          ctx.fillStyle = tones[r % tones.length];
          ctx.fillRect(0, y0, w, plankH);
          for (let g = 0; g < 16; g++) {
            ctx.fillStyle = g % 2 === 0 ? "rgba(68,38,14,0.12)" : "rgba(255,228,184,0.09)";
            ctx.fillRect(0, y0 + g * (plankH / 16), w, 2);
          }
          ctx.fillStyle = "rgba(52,30,12,0.58)";
          ctx.fillRect(0, y0, w, 4);
        }
        // Glazed hygienic creamery white ceramic dado tiles
        ctx.fillStyle = "#c8d0d6";
        ctx.fillRect(0, splitY, w, h - splitY);
        const tileS = 64;
        for (let y = splitY; y < h; y += tileS) {
          for (let x = 0; x < w; x += tileS) {
            ctx.fillStyle = ((x / tileS + Math.floor(y / tileS)) % 2 === 0) ? "#f4f7f8" : "#edf2f4";
            ctx.fillRect(x + 3, y + 3, tileS - 6, tileS - 6);
          }
        }
      },
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.58);
        ctx.fillStyle = "#bdbdbd";
        ctx.fillRect(0, 0, w, splitY);
        const plankH = Math.round(splitY / 7);
        for (let r = 0; r < 7; r++) {
          ctx.fillStyle = "#222222";
          ctx.fillRect(0, r * plankH, w, 3);
        }
        ctx.fillStyle = "#303030";
        ctx.fillRect(0, splitY, w, h - splitY);
        for (let y = splitY; y < h; y += 32) {
          for (let x = 0; x < w; x += 32) {
            ctx.fillStyle = "#dedede";
            ctx.fillRect(x + 2, y + 2, 28, 28);
          }
        }
      },
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.58);
        ctx.fillStyle = "#989898";
        ctx.fillRect(0, 0, w, splitY);
        // Glossy ceramic white dado tiles with matte grout!
        ctx.fillStyle = "#e8e8e8";
        ctx.fillRect(0, splitY, w, h - splitY);
        for (let y = splitY; y < h; y += 32) {
          for (let x = 0; x < w; x += 32) {
            ctx.fillStyle = "#484848";
            ctx.fillRect(x + 2, y + 2, 28, 28);
          }
        }
      },
      3.0,
      1.0
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        // Warm terracotta-sandstone dairy quarry tiles
        ctx.fillStyle = "#4d4238";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            const sh = 122 + ((r * 9 + c * 15) % 24);
            ctx.fillStyle = `rgb(${sh + 22},${sh - 2},${sh - 22})`;
            ctx.fillRect(c * 256 + 6, r * 256 + 6, 244, 244);
          }
        }
        drawMicroNoise(ctx, w, h, 3800, 96, 48, 0.16, 4);
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#1f1f1f";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            ctx.fillStyle = "#d0d0d0";
            ctx.fillRect(c * 128 + 4, r * 128 + 4, 120, 120);
          }
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#eaeaea";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            ctx.fillStyle = "#727272";
            ctx.fillRect(c * 128 + 4, r * 128 + 4, 120, 120);
          }
        }
      },
      4.2,
      3.0
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      const tones = ["#a87849", "#9e6e40", "#b28252", "#97693d"];
      for (let r = 0; r < 8; r++) {
        ctx.fillStyle = tones[r % tones.length];
        ctx.fillRect(0, r * 64, w, 64);
        ctx.fillStyle = "rgba(44,24,10,0.42)";
        ctx.fillRect(0, r * 64, w, 3);
      }
    }, 3.4, 2.2, 512, true);
  } else if (key === "thurgau_glasshouse") {
    // Luminous sunlit conservatory glazing with emerald structural mullions & warm sky/foliage light transmission
    wall = makePbrSurface(
      (ctx, w, h) => {
        const g = ctx.createLinearGradient(0, 0, 0, h);
        g.addColorStop(0.0, "#eefaf3");
        g.addColorStop(0.45, "#d8f2e2");
        g.addColorStop(0.75, "#b7dec7");
        g.addColorStop(1.0, "#85a892");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, w, h);
        // Warm diagonal sunlit conservatory light shafts
        ctx.fillStyle = "rgba(255, 252, 224, 0.28)";
        for (let sx = -120; sx < w; sx += 260) {
          ctx.beginPath();
          ctx.moveTo(sx, 0); ctx.lineTo(sx + 110, 0);
          ctx.lineTo(sx + 260, h); ctx.lineTo(sx + 150, h);
          ctx.closePath(); ctx.fill();
        }
        // Soft blurred exterior tomato rows visible behind lower frosted glass
        for (let i = 0; i < 16; i++) {
          ctx.fillStyle = i % 2 === 0 ? "rgba(34, 130, 68, 0.22)" : "rgba(22, 101, 52, 0.18)";
          ctx.beginPath();
          ctx.arc((i * 71) % w, h * 0.72 + ((i * 23) % 90), 54, 0, Math.PI * 2);
          ctx.fill();
        }
        // Concrete dwarf knee-wall at bottom (3D structural mullions cast crisp real shadows in front)
        ctx.fillStyle = "#8a998f";
        ctx.fillRect(0, h * 0.82, w, h * 0.18);
        ctx.fillStyle = "#14532d";
        ctx.fillRect(0, h * 0.81, w, 14);
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#b8b8b8";
        ctx.fillRect(0, 0, w, h);
        ctx.fillStyle = "#e8e8e8";
        ctx.fillRect(0, h * 0.81, w, 8);
      },
      (ctx, w, h) => {
        // Glossy sunlit glass panes, matte lower knee-wall
        ctx.fillStyle = "#484848";
        ctx.fillRect(0, 0, w, h * 0.82);
        ctx.fillStyle = "#d8d8d8";
        ctx.fillRect(0, h * 0.82, w, h * 0.18);
      },
      2.8,
      1.0
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#6c7a72";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            const s = (r + c) % 2 === 0 ? 128 : 116;
            ctx.fillStyle = `rgb(${s},${s + 8},${s + 2})`;
            ctx.fillRect(c * 256 + 5, r * 256 + 5, 246, 246);
          }
        }
        drawMicroNoise(ctx, w, h, 4200, 96, 46, 0.18, 4);
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#2a2a2a";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            ctx.fillStyle = "#cccccc";
            ctx.fillRect(c * 128 + 3, r * 128 + 3, 122, 122);
          }
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#ececec";
        ctx.fillRect(0, 0, w, h);
        for (let r = 0; r < 4; r++) {
          for (let c = 0; c < 4; c++) {
            ctx.fillStyle = "#989898";
            ctx.fillRect(c * 128 + 3, r * 128 + 3, 122, 122);
          }
        }
      },
      4.2,
      3.0
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#e0f6ea";
      ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = "#166534";
      ctx.lineWidth = 6;
      for (let k = 0; k <= w; k += 128) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
      }
    }, 3.2, 2.2, 512, true);
  } else if (key === "st_jakob_pool") {
    // Crisp St. Jakob aquatic mosaic tiles with racing-blue stripe + glistening wet-sheen petrol-slate deck tiles
    wall = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#89a8be";
        ctx.fillRect(0, 0, w, h);
        const cells = 16;
        const cs = w / cells;
        for (let r = 0; r < cells; r++) {
          for (let c = 0; c < cells; c++) {
            const isTopBand = r >= 1 && r <= 2;
            const isMidBand = r >= 9 && r <= 11;
            let col;
            if (isTopBand || isMidBand) {
              col = (r + c) % 2 === 0 ? "#0369a1" : "#0284c7";
            } else {
              col = (r + c) % 2 === 0 ? "#e2f2fb" : "#d4ebf8";
            }
            ctx.fillStyle = col;
            ctx.fillRect(c * cs + 3, r * cs + 3, cs - 6, cs - 6);
          }
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#202020";
        ctx.fillRect(0, 0, w, h);
        const cs = w / 16;
        for (let r = 0; r < 16; r++) {
          for (let c = 0; c < 16; c++) {
            ctx.fillStyle = "#dfdfdf";
            ctx.fillRect(c * cs + 2, r * cs + 2, cs - 4, cs - 4);
          }
        }
      },
      (ctx, w, h) => {
        // Glossy ceramic pool mosaic with matte recessed grout!
        ctx.fillStyle = "#efefef";
        ctx.fillRect(0, 0, w, h);
        const cs = w / 16;
        for (let r = 0; r < 16; r++) {
          for (let c = 0; c < 16; c++) {
            ctx.fillStyle = "#404040";
            ctx.fillRect(c * cs + 2, r * cs + 2, cs - 4, cs - 4);
          }
        }
      },
      3.4,
      1.2
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#283a47";
        ctx.fillRect(0, 0, w, h);
        const cs = w / 8;
        for (let r = 0; r < 8; r++) {
          for (let c = 0; c < 8; c++) {
            const sh = 56 + ((r * 7 + c * 11) % 14);
            ctx.fillStyle = `rgb(${sh},${sh + 22},${sh + 36})`;
            ctx.fillRect(c * cs + 4, r * cs + 4, cs - 8, cs - 8);
          }
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#1c1c1c";
        ctx.fillRect(0, 0, w, h);
        const cs = w / 8;
        for (let r = 0; r < 8; r++) {
          for (let c = 0; c < 8; c++) {
            ctx.fillStyle = "#d2d2d2";
            ctx.fillRect(c * cs + 2, r * cs + 2, cs - 4, cs - 4);
          }
        }
      },
      (ctx, w, h) => {
        // Wet-sheen pool-deck ceramic tiles
        ctx.fillStyle = "#e6e6e6";
        ctx.fillRect(0, 0, w, h);
        const cs = w / 8;
        for (let r = 0; r < 8; r++) {
          for (let c = 0; c < 8; c++) {
            const damp = ((r * 13 + c * 5) % 3 === 0) ? "#363636" : "#565656";
            ctx.fillStyle = damp;
            ctx.fillRect(c * cs + 2, r * cs + 2, cs - 4, cs - 4);
          }
        }
      },
      4.4,
      3.1
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#b8d2e2";
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "rgba(14,116,144,0.16)";
      for (let y = 0; y < h; y += 32) ctx.fillRect(0, y, w, 12);
    }, 3.6, 2.4, 512, true);
  } else if (key === "lucerne_bakery") {
    // Classic European cream glazed subway tiles + warm fired quarry-brick lower dado + speckled warm terrazzo floor
    wall = makePbrSurface(
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.64);
        ctx.fillStyle = "#c9bfb2";
        ctx.fillRect(0, 0, w, splitY);
        const rows = 14;
        const rh = splitY / rows;
        const bw = 128;
        for (let r = 0; r < rows; r++) {
          const off = (r % 2) * (bw / 2);
          for (let x = -bw; x < w + bw; x += bw) {
            ctx.fillStyle = (r + Math.round(x / bw)) % 2 === 0 ? "#f7f1e6" : "#f2eadc";
            ctx.fillRect(x + off + 3, r * rh + 3, bw - 6, rh - 6);
          }
        }
        // Fired terracotta-brown bakery dado brick
        ctx.fillStyle = "#5c4234";
        ctx.fillRect(0, splitY, w, h - splitY);
        const dRows = 6;
        const drh = (h - splitY) / dRows;
        for (let r = 0; r < dRows; r++) {
          const off = (r % 2) * 64;
          for (let x = -64; x < w + 64; x += 128) {
            const s = 142 + ((r * 13 + Math.abs(x) * 3) % 24);
            ctx.fillStyle = `rgb(${s + 14},${s - 22},${s - 40})`;
            ctx.fillRect(x + off + 4, splitY + r * drh + 4, 120, drh - 8);
          }
        }
      },
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.64);
        ctx.fillStyle = "#262626";
        ctx.fillRect(0, 0, w, h);
        const rh = splitY / 14;
        for (let r = 0; r < 14; r++) {
          const off = (r % 2) * 32;
          for (let x = -64; x < w + 64; x += 64) {
            ctx.fillStyle = "#dcdcdc";
            ctx.fillRect(x + off + 2, r * rh + 2, 60, rh - 4);
          }
        }
        const drh = (h - splitY) / 6;
        for (let r = 0; r < 6; r++) {
          const off = (r % 2) * 32;
          for (let x = -64; x < w + 64; x += 64) {
            ctx.fillStyle = "#c8c8c8";
            ctx.fillRect(x + off + 2, splitY + r * drh + 2, 60, drh - 4);
          }
        }
      },
      (ctx, w, h) => {
        const splitY = Math.round(h * 0.64);
        ctx.fillStyle = "#eaeaea";
        ctx.fillRect(0, 0, w, h);
        const rh = splitY / 14;
        for (let r = 0; r < 14; r++) {
          const off = (r % 2) * 32;
          for (let x = -64; x < w + 64; x += 64) {
            ctx.fillStyle = "#484848";
            ctx.fillRect(x + off + 2, r * rh + 2, 60, rh - 4);
          }
        }
        ctx.fillStyle = "#8a8a8a";
        ctx.fillRect(0, splitY, w, h - splitY);
      },
      2.8,
      1.0
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#7a6f64";
        ctx.fillRect(0, 0, w, h);
        drawTerrazzoFlecks(ctx, w, h, 5000, ["#d6cab8", "#a89886", "#574c42", "#e7dfd3", "#8a7866"], 5321);
        ctx.strokeStyle = "rgba(48,40,34,0.42)";
        ctx.lineWidth = 4;
        for (let k = 0; k <= w; k += 256) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#c8c8c8";
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = "#323232";
        ctx.lineWidth = 3;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#666666";
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = "#e4e4e4";
        ctx.lineWidth = 4;
        for (let k = 0; k <= w; k += 128) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
        }
      },
      4.2,
      3.0
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#dfd6ca";
      ctx.fillRect(0, 0, w, h);
      drawMicroNoise(ctx, w, h, 1800, 196, 26, 0.16, 4);
    }, 3.6, 2.4, 512, true);
  } else {
    // stgallen_hospital: Seamless antimicrobial cleanroom modular wall panels + reflective medical epoxy terrazzo floor
    wall = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#e7eef4";
        ctx.fillRect(0, 0, w, h);
        // Subtle lower stainless/antimicrobial kick zone
        ctx.fillStyle = "#cfdbe6";
        ctx.fillRect(0, h * 0.66, w, h * 0.34);
        // Anodized aluminum modular panel vertical seams
        for (let x = 0; x <= w; x += 256) {
          ctx.fillStyle = "#94a3b8";
          ctx.fillRect(x - 3, 0, 6, h);
          ctx.fillStyle = "#f8fafc";
          ctx.fillRect(x + 3, 0, 3, h);
        }
        // Hygienic clinical accent stripe
        ctx.fillStyle = "#0ea5e9";
        ctx.fillRect(0, h * 0.652, w, 8);
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#d0d0d0";
        ctx.fillRect(0, 0, w, h);
        for (let x = 0; x <= w; x += 128) {
          ctx.fillStyle = "#484848";
          ctx.fillRect(x - 2, 0, 4, h);
        }
      },
      (ctx, w, h) => {
        // Satin cleanroom panels, sleek reflective stainless lower zone
        ctx.fillStyle = "#767676";
        ctx.fillRect(0, 0, w, h * 0.66);
        ctx.fillStyle = "#525252";
        ctx.fillRect(0, h * 0.66, w, h * 0.34);
      },
      3.0,
      1.0
    );
    floor = makePbrSurface(
      (ctx, w, h) => {
        ctx.fillStyle = "#a6b5c4";
        ctx.fillRect(0, 0, w, h);
        drawTerrazzoFlecks(ctx, w, h, 4600, ["#e2eaf2", "#cbd6e2", "#7a8da0", "#f8fafc", "#94a3b8"], 8831);
        // Subtle welded seam lines of seamless hospital vinyl/epoxy flooring
        ctx.strokeStyle = "rgba(100,116,139,0.22)";
        ctx.lineWidth = 3;
        for (let k = 0; k <= w; k += 512) {
          ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
        }
      },
      (ctx, w, h) => {
        ctx.fillStyle = "#d0d0d0";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 1400, 188, 28, 0.14, 3);
      },
      (ctx, w, h) => {
        // Reflective sterile medical epoxy floor
        ctx.fillStyle = "#4a4a4a";
        ctx.fillRect(0, 0, w, h);
        drawMicroNoise(ctx, w, h, 1400, 58, 36, 0.22, 6);
      },
      3.8,
      2.8
    );
    ceilTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#e8eff5";
      ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = "rgba(148,163,184,0.45)";
      ctx.lineWidth = 3;
      for (let k = 0; k <= w; k += 64) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
      }
    }, 3.6, 2.4, 512, true);
  }

  _ROOM_TEX_CACHE[key] = {
    wallTex: wall.map,
    wallBump: wall.bumpMap,
    wallRough: wall.roughnessMap,
    floorTex: floor.map,
    floorBump: floor.bumpMap,
    floorRough: floor.roughnessMap,
    ceilTex,
  };
  return _ROOM_TEX_CACHE[key];
}

const DEPLOYMENT_TO_ROOM = {
  house_basel: "basel_basement",
  house_borehole: "borehole_utility",
  alpine_chalet: "alpine_chalet",
  dairy_farm: "emmental_dairy",
  glasshouse_farm: "thurgau_glasshouse",
  municipal_pool: "st_jakob_pool",
  commercial_bakery: "lucerne_bakery",
  hospital_wing: "stgallen_hospital",
};

export class Basement3DStudio {
  constructor(hostEl) {
    this.hostEl = hostEl;
    this.ready = false;
    this.active = false;
    this.rafId = null;
    const draco = new DRACOLoader();
    draco.setDecoderPath("/vendor/three/jsm/libs/draco/gltf/");
    this.loader = new GLTFLoader();
    this.loader.setDRACOLoader(draco);
    this.templates = {};
    this.templatesLoaded = false;
    this.dynamicGroup = null;
    this.pipeHotMats = [];
    this.pipeColdMats = [];
    this.ledMats = [];
    this.tankHotBar = null;
    this.tankColdBar = null;
    this.furnitureGroup = null;
    this.currentRoomStyle = null;
    this.flowParticles = [];
    this.heatPlumes = [];
    this.flowSpeed = 0.65;
    this.lastConfig = null;
    this.cduBox = null;
    this.tankBox = null;
    this.secondaryPaths = null;
    this.tankSightMetrics = null;
    this.trofferLights = [];
    this.accentLight = null;
    this.animatedCausticTex = null;
  }

  init() {
    if (this.ready || !this.hostEl) return;
    this.ready = true;
    this.hostEl.innerHTML = "";

    const canvas = document.createElement("canvas");
    canvas.className = "cfg-3d-canvas";
    canvas.setAttribute("aria-label", "Interactive 3D Plant Room Configurator");
    this.hostEl.appendChild(canvas);

    const legend = document.createElement("div");
    legend.className = "cg-3d-legend";
    legend.innerHTML =
      '<span class="cg-leg-item"><span class="cg-leg-dot is-diel-hot"></span><span class="cg-leg-dot is-diel-cold"></span>Dielectric coolant (servers &harr; exchanger)</span>' +
      '<span class="cg-leg-item"><span class="cg-leg-dot is-water-hot"></span><span class="cg-leg-dot is-water-cold"></span>Heating water (exchanger &rarr; tank &rarr; building)</span>';
    this.hostEl.appendChild(legend);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x2c333d);
    this.scene.fog = new THREE.FogExp2(0x2c333d, 0.019);

    const w = Math.max(300, this.hostEl.clientWidth || 720);
    const h = Math.max(200, this.hostEl.clientHeight || 420);
    this.camera = new THREE.PerspectiveCamera(44, w / h, 0.1, 80);
    this.camera.position.set(0.50, 2.02, 7.62);

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.08;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.shadowMap.autoUpdate = false;
    this.renderer.shadowMap.needsUpdate = true;
    this.renderer.setSize(w, h, false);

    // Architectural studio PBR reflections for stainless steel buffer tank, NVIDIA GB300 copper & glazed tiles
    this.scene.environment = buildStudioEnvironment(this.renderer);
    this.scene.environmentIntensity = 1.15;

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.target.set(0.50, 1.14, -1.20);
    this.controls.minDistance = 2.6;
    this.controls.maxDistance = 12.5;
    this.controls.maxPolarAngle = Math.PI * 0.48;
    this.controls.update();

    // Physically grounded architectural lighting: hemisphere + shadow-casting key + soft fill + 3 LED troffer pools + accent
    this.hemiLight = new THREE.HemisphereLight(0xe8f1fc, 0x455060, 0.92);
    this.scene.add(this.hemiLight);

    this.keyLight = new THREE.DirectionalLight(0xfff5e6, 1.65);
    this.keyLight.position.set(4.0, 5.8, 5.4);
    this.keyLight.castShadow = true;
    this.keyLight.shadow.mapSize.set(2048, 2048);
    this.keyLight.shadow.camera.left = -6.2;
    this.keyLight.shadow.camera.right = 6.2;
    this.keyLight.shadow.camera.top = 4.6;
    this.keyLight.shadow.camera.bottom = -3.8;
    this.keyLight.shadow.camera.near = 1.0;
    this.keyLight.shadow.camera.far = 17.0;
    this.keyLight.shadow.bias = -0.0004;
    this.keyLight.shadow.normalBias = 0.022;
    this.keyLight.shadow.radius = 2.6;
    this.scene.add(this.keyLight);

    this.fillLight = new THREE.DirectionalLight(0xc5ddf8, 0.62);
    this.fillLight.position.set(-4.8, 3.6, 4.4);
    this.scene.add(this.fillLight);

    // Soft uplight bounce illuminating the ceiling slab & beams so upper architecture reads cleanly
    this.ceilingBounceLight = new THREE.DirectionalLight(0xe2e8f0, 0.42);
    this.ceilingBounceLight.position.set(0.0, -3.5, 2.2);
    this.scene.add(this.ceilingBounceLight);

    // Three architectural overhead LED troffer pool lights casting specular sheen onto floor & rack tops
    this.trofferLights = [];
    for (const lx of [-2.1, 0.1, 2.3]) {
      const pl = new THREE.PointLight(0xfff6e8, 3.8, 8.2, 1.75);
      pl.position.set(lx, 2.86, -0.68);
      this.scene.add(pl);
      this.trofferLights.push(pl);
    }

    // Dynamic scenario accent light (window daylight / turquoise pool caustics / grow bar / telemetry glow)
    this.accentLight = new THREE.PointLight(0xbfe2ff, 3.2, 6.8, 1.65);
    this.accentLight.position.set(-4.55, 2.32, -0.35);
    this.scene.add(this.accentLight);

    // Multi-map PBR architectural floor, walls, ceiling slab, structural ceiling beams & recessed LED troffers
    const initTex = getRoomTextures("basel_basement");
    this.floorMat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: initTex.floorTex,
      bumpMap: initTex.floorBump,
      bumpScale: 0.012,
      roughnessMap: initTex.floorRough,
      roughness: 0.84,
      metalness: 0.12,
      envMapIntensity: 1.38,
    });
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(14.5, 11.5), this.floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(0.2, 0.0, 1.4);
    floor.receiveShadow = true;
    this.scene.add(floor);

    this.wallMat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: initTex.wallTex,
      bumpMap: initTex.wallBump,
      bumpScale: 0.024,
      roughnessMap: initTex.wallRough,
      roughness: 0.96,
      metalness: 0.05,
      envMapIntensity: 1.05,
    });
    const backWall = new THREE.Mesh(new THREE.BoxGeometry(12.2, 3.24, 0.14), this.wallMat);
    backWall.position.set(0.2, 1.62, -2.55);
    backWall.receiveShadow = true;
    this.scene.add(backWall);

    const leftWall = new THREE.Mesh(new THREE.BoxGeometry(0.14, 3.24, 9.0), this.wallMat);
    leftWall.position.set(-5.85, 1.62, 1.85);
    leftWall.receiveShadow = true;
    this.scene.add(leftWall);

    const rightWall = new THREE.Mesh(new THREE.BoxGeometry(0.14, 3.24, 9.0), this.wallMat);
    rightWall.position.set(6.25, 1.62, 1.85);
    rightWall.receiveShadow = true;
    this.scene.add(rightWall);

    this.ceilingMat = new THREE.MeshStandardMaterial({
      color: 0xd0d8e2,
      map: initTex.ceilTex,
      roughness: 0.80,
      metalness: 0.06,
      envMapIntensity: 0.72,
    });
    const ceiling = new THREE.Mesh(new THREE.BoxGeometry(12.2, 0.12, 9.0), this.ceilingMat);
    ceiling.position.set(0.2, 3.20, 1.85);
    ceiling.receiveShadow = true;
    this.scene.add(ceiling);

    this.beamMat = new THREE.MeshStandardMaterial({ color: 0x525b68, roughness: 0.62, metalness: 0.16 });
    for (const bx of [-3.4, -1.0, 1.4, 3.8]) {
      const beam = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.22, 8.6), this.beamMat);
      beam.position.set(bx, 3.04, 1.75);
      beam.castShadow = true;
      beam.receiveShadow = true;
      this.scene.add(beam);
    }

    // Architectural ambient occlusion corner & wall-floor seam strips for global-illumination depth
    const aoMat = new THREE.MeshBasicMaterial({
      map: getAoStripTexture(),
      transparent: true,
      depthWrite: false,
      opacity: 0.78,
    });
    const addAoStrip = (wSpan, hSpan, pos, rot) => {
      const p = new THREE.Mesh(new THREE.PlaneGeometry(wSpan, hSpan), aoMat);
      p.position.set(...pos);
      p.rotation.set(...rot);
      this.scene.add(p);
    };
    // Floor-to-back-wall AO band
    addAoStrip(12.0, 0.78, [0.2, 0.004, -2.09], [-Math.PI / 2, 0, Math.PI]);
    // Back-wall-to-floor vertical AO band
    addAoStrip(12.0, 0.52, [0.2, 0.26, -2.474], [0, 0, 0]);
    // Back-wall-to-ceiling upper shadow cove
    addAoStrip(12.0, 0.58, [0.2, 2.85, -2.474], [0, 0, Math.PI]);
    // Left & right floor seam AO bands
    addAoStrip(8.8, 0.70, [-5.43, 0.004, 1.85], [-Math.PI / 2, 0, -Math.PI / 2]);
    addAoStrip(8.8, 0.70, [5.83, 0.004, 1.85], [-Math.PI / 2, 0, Math.PI / 2]);
    // Vertical back-left & back-right corner creases
    addAoStrip(3.16, 0.54, [-5.51, 1.58, -2.473], [0, 0, Math.PI / 2]);
    addAoStrip(3.16, 0.54, [5.91, 1.58, -2.473], [0, 0, -Math.PI / 2]);

    // Recessed architectural aluminum LED light troffers (3 primary + 2 rear wash strips)
    const lampFrameMat = new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.34, metalness: 0.78 });
    this.lampGlowMat = new THREE.MeshBasicMaterial({ color: 0xfffef8 });
    for (const [lx, lz, lw] of [
      [-2.2, -0.75, 1.38],
      [0.2, -0.75, 1.38],
      [2.6, -0.75, 1.38],
      [-1.0, -1.96, 1.18],
      [2.5, -1.96, 1.18],
    ]) {
      const frame = new THREE.Mesh(new THREE.BoxGeometry(lw, 0.055, 0.34), lampFrameMat);
      frame.position.set(lx, 3.12, lz);
      this.scene.add(frame);
      const glow = new THREE.Mesh(new THREE.BoxGeometry(lw - 0.10, 0.024, 0.24), this.lampGlowMat);
      glow.position.set(lx, 3.092, lz);
      this.scene.add(glow);
    }

    // Architectural wall baseboard + rail / dado trim reflecting room type
    const baseMat = new THREE.MeshStandardMaterial({ color: 0x26303d, roughness: 0.52, metalness: 0.28 });
    const baseboard = new THREE.Mesh(new THREE.BoxGeometry(12.2, 0.12, 0.18), baseMat);
    baseboard.position.set(0.2, 0.06, -2.50);
    baseboard.receiveShadow = true;
    this.scene.add(baseboard);

    this.accentMat = new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.48, metalness: 0.22 });
    this.accentBand = new THREE.Mesh(new THREE.BoxGeometry(12.2, 0.14, 0.18), this.accentMat);
    this.accentBand.position.set(0.2, 0.07, -2.50);
    this.accentBand.receiveShadow = true;
    this.scene.add(this.accentBand);

    this.staticGroup = new THREE.Group();
    this.furnitureGroup = new THREE.Group();
    this.dynamicGroup = new THREE.Group();
    this.scene.add(this.staticGroup);
    this.scene.add(this.furnitureGroup);
    this.scene.add(this.dynamicGroup);

    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(this.hostEl);

    this.loadHardwareTemplates();
  }

  async loadHardwareTemplates() {
    const entries = [
      ["gb300", "/assets/glb/hardware_gb300_rack.glb", 0],
      ["ocpPop", "/assets/glb/hardware_ocp_populated_rack.glb", 0],
      ["vr3307", "/assets/glb/hardware_vr3307_rack.glb", 0],
      ["rack", "/assets/glb/hardware_server_rack.glb", 0],
      ["cdu", "/assets/glb/hardware_cdu_exchanger.glb", -Math.PI / 2],
      ["tank", "/assets/glb/hardware_buffer_tank.glb", 0],
      ["valve", "/assets/glb/hardware_valve_3way.glb", 0],
      ["panel", "/assets/glb/hardware_power_panel.glb", 0],
      ["ledPanel", "/assets/glb/hardware_led_panel.glb", 0],
      ["extinguisher", "/assets/glb/hardware_extinguisher.glb", Math.PI * 0.3],
    ];
    await Promise.all(
      entries.map(
        ([k, url, rotY]) =>
          new Promise((resolve) => {
            this.loader.load(
              url,
              (gltf) => {
                this.templates[k] = normalizeHardwareTemplate(gltf.scene, rotY, k);
                resolve();
              },
              undefined,
              (err) => {
                console.error(`Failed loading ${url}:`, err);
                resolve();
              }
            );
          })
      )
    );
    this.templatesLoaded = true;
    this.buildStaticPlantRoom();
    if (this.lastConfig) {
      this.syncConfig(this.lastConfig);
    }
  }

  buildStaticPlantRoom() {
    if (!this.staticGroup) return;
    this.staticGroup.clear();

    const brassMat = new THREE.MeshStandardMaterial({
      color: 0xd8a64c,
      roughness: 0.25,
      metalness: 0.85,
    });
    brassMat.userData.sharedMaterial = true;
    const darkFrameMat = new THREE.MeshStandardMaterial({
      color: 0x334155,
      roughness: 0.40,
      metalness: 0.68,
    });
    darkFrameMat.userData.sharedMaterial = true;
    const galvSteelMat = new THREE.MeshStandardMaterial({
      color: 0x9aa6b4,
      roughness: 0.34,
      metalness: 0.78,
    });
    galvSteelMat.userData.sharedMaterial = true;
    this.brassMat = brassMat;
    this.galvSteelMat = galvSteelMat;

    // Back-wall galvanized Unistrut mounting rails (grounds overhead headers & building risers cleanly)
    for (const uy of [2.48, 1.56]) {
      const rail = new THREE.Mesh(new THREE.BoxGeometry(8.8, 0.045, 0.036), galvSteelMat);
      rail.position.set(0.3, uy, -2.455);
      rail.castShadow = true;
      rail.receiveShadow = true;
      this.staticGroup.add(rail);
    }

    // Overhead galvanized perforated steel cable ladder tray above server row
    const trayY = 2.74;
    const trayZ = -2.02;
    for (const dz of [-0.11, 0.11]) {
      const sideRail = new THREE.Mesh(new THREE.BoxGeometry(5.55, 0.055, 0.022), galvSteelMat);
      sideRail.position.set(-1.12, trayY, trayZ + dz);
      sideRail.castShadow = true;
      this.staticGroup.add(sideRail);
    }
    for (let rxPos = -3.75; rxPos <= 1.55; rxPos += 0.32) {
      const rung = new THREE.Mesh(new THREE.BoxGeometry(0.022, 0.018, 0.22), galvSteelMat);
      rung.position.set(rxPos, trayY - 0.014, trayZ);
      this.staticGroup.add(rung);
    }
    for (const rodX of [-3.4, -1.6, 0.2, 1.4]) {
      const rod = makePipeMesh([rodX, trayY, trayZ], [rodX, 3.14, trayZ], 0.009, galvSteelMat);
      if (rod) this.staticGroup.add(rod);
    }

    // Flush linear stainless steel floor trench drain grate in front of CDU & Buffer Tank
    const drainMesh = new THREE.Mesh(
      new THREE.PlaneGeometry(2.85, 0.19),
      new THREE.MeshStandardMaterial({
        map: getFloorDrainTexture(),
        roughness: 0.36,
        metalness: 0.72,
      })
    );
    drainMesh.rotation.x = -Math.PI / 2;
    drainMesh.position.set(2.26, 0.004, -0.42);
    drainMesh.receiveShadow = true;
    this.staticGroup.add(drainMesh);

    // Vertiv Liebert XDU1350 Plate Heat Exchanger / CDU (bridges isolated primary dielectric <-> secondary water)
    if (this.templates.cdu) {
      const cdu = this.templates.cdu.clone(true);
      cdu.position.set(1.42, 0.0, -1.42);
      cdu.scale.setScalar(0.88);
      cdu.updateMatrixWorld(true);
      this.staticGroup.add(cdu);
      this.cduBox = new THREE.Box3().setFromObject(cdu);
    } else {
      this.cduBox = new THREE.Box3(new THREE.Vector3(1.02, 0, -1.96), new THREE.Vector3(1.82, 1.87, -0.88));
    }
    const cduShadow = makeContactShadow(1.42, 1.52);
    cduShadow.position.set(1.42, 0.005, -1.42);
    this.staticGroup.add(cduShadow);

    // 1,000 L Thermal Buffer Tank (right of heat exchanger)
    if (this.templates.tank) {
      const tank = this.templates.tank.clone(true);
      tank.position.set(3.06, 0.0, -1.42);
      tank.scale.setScalar(0.95);
      tank.updateMatrixWorld(true);
      this.staticGroup.add(tank);
      this.tankBox = new THREE.Box3().setFromObject(tank);
    } else {
      this.tankBox = new THREE.Box3(new THREE.Vector3(2.46, 0, -1.95), new THREE.Vector3(3.66, 1.23, -0.89));
    }
    const tankShadow = makeContactShadow(1.58, 1.48);
    tankShadow.position.set(3.06, 0.005, -1.42);
    this.staticGroup.add(tankShadow);

    const cduRight = this.cduBox.max.x;
    const cduMidZ = (this.cduBox.min.z + this.cduBox.max.z) * 0.5;
    const tankLeft = this.tankBox.min.x;
    const tankTop = this.tankBox.max.y;
    const tankFront = this.tankBox.max.z;
    const tankMidX = (this.tankBox.min.x + this.tankBox.max.x) * 0.5;
    const tankMidZ = (this.tankBox.min.z + this.tankBox.max.z) * 0.5;

    const secHotY = Math.min(tankTop - 0.28, 0.88);
    const secColdY = 0.38;

    // Swiss Reflex-style red hydronic pressure expansion vessel nestled neatly behind the CDU-Tank gap
    const expVesselGrp = new THREE.Group();
    const expRedMat = new THREE.MeshStandardMaterial({ color: 0xc81e1e, roughness: 0.26, metalness: 0.32 });
    const expBody = new THREE.Mesh(new THREE.CylinderGeometry(0.185, 0.185, 0.52, 22), expRedMat);
    expBody.position.y = 0.34;
    expVesselGrp.add(expBody);
    const expTop = new THREE.Mesh(new THREE.SphereGeometry(0.185, 22, 12, 0, Math.PI * 2, 0, Math.PI / 2), expRedMat);
    expTop.position.y = 0.60;
    expVesselGrp.add(expTop);
    const expFoot = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.19, 0.10, 18), darkFrameMat);
    expFoot.position.y = 0.05;
    expVesselGrp.add(expFoot);
    const expBand = new THREE.Mesh(new THREE.CylinderGeometry(0.190, 0.190, 0.022, 22), galvSteelMat);
    expBand.position.y = 0.34;
    expVesselGrp.add(expBand);
    expVesselGrp.position.set(2.14, 0.0, -2.14);
    enableShadows(expVesselGrp, true, true);
    this.staticGroup.add(expVesselGrp);
    const expShadow = makeContactShadow(0.54, 0.54);
    expShadow.position.set(2.14, 0.005, -2.14);
    this.staticGroup.add(expShadow);
    // Stainless braided connector tapping expansion vessel cleanly into the secondary cold return
    buildOrthogonalPipe(
      this.staticGroup,
      [
        [2.14, 0.78, -2.14],
        [2.14, 0.78, -1.94],
        [2.14, secColdY, -1.94],
        [2.14, secColdY, tankMidZ],
      ],
      0.015,
      galvSteelMat,
      brassMat,
      [true, true]
    );

    // Flush-mounted thermal stratification sight-glass bezel on exact front face of the 1,000 L buffer tank
    const sightMinY = 0.24;
    const sightMaxY = Math.max(0.76, tankTop - 0.18);
    const sightH = sightMaxY - sightMinY;
    const sightX = tankMidX - 0.22;
    const sightZ = tankFront + 0.014;
    this.tankSightMetrics = { minY: sightMinY, height: sightH, x: sightX, z: sightZ };

    const sightBezel = new THREE.Mesh(
      new THREE.BoxGeometry(0.11, sightH + 0.06, 0.03),
      darkFrameMat
    );
    sightBezel.position.set(sightX, sightMinY + sightH * 0.5, sightZ - 0.008);
    sightBezel.castShadow = true;
    this.staticGroup.add(sightBezel);
    for (const capY of [sightMinY - 0.02, sightMaxY + 0.02]) {
      const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.036, 0.036, 0.03, 14), brassMat);
      cap.position.set(sightX, capY, sightZ + 0.01);
      this.staticGroup.add(cap);
    }

    this.tankHotBar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.026, 0.026, 1.0, 14),
      new THREE.MeshStandardMaterial({ color: 0xf97316, emissive: 0xea580c, emissiveIntensity: 0.74, roughness: 0.20 })
    );
    this.tankHotBar.position.set(sightX, sightMinY + sightH * 0.7, sightZ + 0.01);
    this.staticGroup.add(this.tankHotBar);

    this.tankColdBar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.026, 0.026, 1.0, 14),
      new THREE.MeshStandardMaterial({ color: 0x0284c7, emissive: 0x0ea5e9, emissiveIntensity: 0.54, roughness: 0.20 })
    );
    this.tankColdBar.position.set(sightX, sightMinY + sightH * 0.25, sightZ + 0.01);
    this.staticGroup.add(this.tankColdBar);

    // ForgeCAD 3-way diverter valve aligned dead-center on the CDU -> Tank hot water supply line
    if (this.templates.valve) {
      const valve = this.templates.valve.clone(true);
      const valveScale = 0.62;
      valve.scale.setScalar(valveScale);
      const vSz = this.templates.valve.userData?.size || { y: 0.74 };
      valve.position.set((cduRight + tankLeft) * 0.5, secHotY - vSz.y * valveScale * 0.30, tankMidZ);
      this.staticGroup.add(valve);
    }

    // Vertiv Power Distribution Panel mounted flush along the left wall (keeps back-left clear for scenario props!)
    if (this.templates.panel) {
      const panel = this.templates.panel.clone(true);
      panel.position.set(-4.84, 0.0, 0.92);
      panel.rotation.y = Math.PI / 2;
      panel.scale.setScalar(0.84);
      this.staticGroup.add(panel);
    }

    // SimReady ceiling LED fixtures & wall safety extinguisher
    if (this.templates.ledPanel) {
      for (const lx of [-1.8, 1.8]) {
        const lp = this.templates.ledPanel.clone(true);
        lp.position.set(lx, 3.05, -0.8);
        lp.scale.set(0.24, 0.06, 0.16);
        this.staticGroup.add(lp);
      }
    }
    if (this.templates.extinguisher) {
      const ext = this.templates.extinguisher.clone(true);
      ext.position.set(-4.86, 0.0, 1.85);
      ext.scale.setScalar(1.12);
      this.staticGroup.add(ext);
    }

    // Fixed SECONDARY building heating water loop (isolated from servers by the Liebert XDU plate heat exchanger)
    const waterHotMat = new THREE.MeshStandardMaterial({
      color: 0xea580c,
      emissive: 0xc2410c,
      emissiveIntensity: 0.36,
      roughness: 0.24,
      metalness: 0.38,
    });
    const waterColdMat = new THREE.MeshStandardMaterial({
      color: 0x0284c7,
      emissive: 0x0369a1,
      emissiveIntensity: 0.28,
      roughness: 0.24,
      metalness: 0.38,
    });
    this.staticHotMat = waterHotMat;
    this.staticColdMat = waterColdMat;

    // 1. CDU right side -> through 3-way valve -> into Tank left side (hot building water)
    const pCduToTankHot = buildOrthogonalPipe(
      this.staticGroup,
      [
        [cduRight - 0.04, secHotY, cduMidZ],
        [tankLeft + 0.04, secHotY, tankMidZ],
      ],
      0.040,
      waterHotMat,
      brassMat,
      [true, true]
    );

    // 2. Tank lower-left -> CDU lower-right (cool building return water)
    const pTankToCduCold = buildOrthogonalPipe(
      this.staticGroup,
      [
        [tankLeft + 0.04, secColdY, tankMidZ],
        [cduRight - 0.04, secColdY, cduMidZ],
      ],
      0.040,
      waterColdMat,
      brassMat,
      [true, true]
    );

    // 3. Top of Tank -> straight UP to ceiling slab flange (hot building heating riser)
    const riserHotX = tankMidX - 0.22;
    const riserColdX = tankMidX + 0.22;
    const pTankToBuildingHot = buildOrthogonalPipe(
      this.staticGroup,
      [
        [riserHotX, tankTop - 0.04, tankMidZ],
        [riserHotX, 3.12, tankMidZ],
      ],
      0.038,
      waterHotMat,
      brassMat,
      [true, true]
    );
    const hotTopNozzle = new THREE.Mesh(new THREE.CylinderGeometry(0.068, 0.072, 0.055, 16), brassMat);
    hotTopNozzle.position.set(riserHotX, tankTop + 0.01, tankMidZ);
    this.staticGroup.add(hotTopNozzle);

    // 4. Ceiling slab flange -> straight DOWN into top of Tank (cool building return downcomer)
    const pBuildingToTankCold = buildOrthogonalPipe(
      this.staticGroup,
      [
        [riserColdX, 3.12, tankMidZ],
        [riserColdX, tankTop - 0.04, tankMidZ],
      ],
      0.038,
      waterColdMat,
      brassMat,
      [true, true]
    );
    const coldTopNozzle = new THREE.Mesh(new THREE.CylinderGeometry(0.068, 0.072, 0.055, 16), brassMat);
    coldTopNozzle.position.set(riserColdX, tankTop + 0.01, tankMidZ);
    this.staticGroup.add(coldTopNozzle);

    // Three analog Swiss brass dial gauges mounted on secondary headers & supply riser
    const gTemp = makeAnalogGauge("temp", brassMat);
    gTemp.position.set(cduRight + 0.14, secHotY + 0.032, tankMidZ);
    this.staticGroup.add(gTemp);

    const gPress = makeAnalogGauge("pressure", brassMat);
    gPress.position.set(tankLeft - 0.16, secColdY + 0.032, tankMidZ);
    this.staticGroup.add(gPress);

    const gRiser = makeAnalogGauge("temp", brassMat);
    gRiser.position.set(riserHotX, 1.78, tankMidZ + 0.04);
    this.staticGroup.add(gRiser);

    this.secondaryPaths = {
      hotCduToTank: pCduToTankHot,
      hotRiser: pTankToBuildingHot,
      coldDowncomer: pBuildingToTankCold,
      coldTankToCdu: pTankToCduCold,
      riserHotX,
      riserColdX,
      tankTop,
      tankMidZ,
      tankMidX,
    };

    if (this.renderer) this.renderer.shadowMap.needsUpdate = true;
  }

  buildRoomFurniture(roomStyle) {
    if (!this.furnitureGroup) return;
    disposeSubtree(this.furnitureGroup);
    this.currentRoomStyle = roomStyle;
    this.animatedCausticTex = null;

    const woodMat = new THREE.MeshStandardMaterial({ color: 0xb6844f, roughness: 0.66, metalness: 0.04 });
    const darkWoodMat = new THREE.MeshStandardMaterial({ color: 0x6b4423, roughness: 0.70, metalness: 0.04 });
    const darkSteel = new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.38, metalness: 0.72 });
    const brushedSteel = new THREE.MeshStandardMaterial({ color: 0xd2dce6, roughness: 0.24, metalness: 0.84 });
    const mirrorSteel = new THREE.MeshStandardMaterial({ color: 0xe2eaf2, roughness: 0.16, metalness: 0.90 });
    const copperMat = new THREE.MeshStandardMaterial({ color: 0xc87444, roughness: 0.26, metalness: 0.86 });
    const whiteEnamel = new THREE.MeshStandardMaterial({ color: 0xf6f9fc, roughness: 0.24, metalness: 0.12 });
    const branchHotMat = new THREE.MeshStandardMaterial({
      color: 0xea580c,
      emissive: 0xc2410c,
      emissiveIntensity: 0.34,
      roughness: 0.26,
      metalness: 0.38,
    });
    const branchColdMat = new THREE.MeshStandardMaterial({
      color: 0x0284c7,
      emissive: 0x0369a1,
      emissiveIntensity: 0.28,
      roughness: 0.26,
      metalness: 0.38,
    });
    const rx = this.secondaryPaths?.riserHotX ?? 2.84;
    const cxR = this.secondaryPaths?.riserColdX ?? 3.28;
    const rz = this.secondaryPaths?.tankMidZ ?? -1.42;

    if (roomStyle === "basel_basement") {
      // 1. BASEL MULTI-FAMILY BASEMENT: Ribbed cast-iron radiator + V-ZUG front-load laundry + oak workbench + basement light-well window
      for (let f = 0; f < 12; f++) {
        const fin = new THREE.Mesh(new THREE.BoxGeometry(0.052, 0.78, 0.12), whiteEnamel);
        fin.position.set(3.86 + f * 0.092, 0.72, -2.38);
        this.furnitureGroup.add(fin);
      }
      // Top & bottom radiator collector headers + brass thermostatic TRV valve head
      for (const ry of [1.04, 0.40]) {
        const hdr = makePipeMesh([3.82, ry, -2.38], [4.92, ry, -2.38], 0.022, whiteEnamel);
        if (hdr) this.furnitureGroup.add(hdr);
      }
      const trv = new THREE.Mesh(new THREE.CylinderGeometry(0.028, 0.028, 0.08, 12), whiteEnamel);
      trv.rotation.z = Math.PI / 2;
      trv.position.set(3.80, 1.04, -2.36);
      this.furnitureGroup.add(trv);

      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [rx, 1.56, -2.36],
          [3.82, 1.56, -2.36],
          [3.82, 1.04, -2.36],
        ],
        0.022,
        branchHotMat,
        this.brassMat,
        [true, true]
      );

      // Swiss V-ZUG style front-load washing machine on back-left (framed cleanly in default hero view)
      const washer = new THREE.Mesh(new THREE.BoxGeometry(0.68, 0.90, 0.66), whiteEnamel);
      washer.position.set(-3.45, 0.45, -1.62);
      this.furnitureGroup.add(washer);
      const wShadow = makeContactShadow(0.92, 0.90);
      wShadow.position.set(-3.45, 0.005, -1.62);
      this.furnitureGroup.add(wShadow);
      const ctrlPanel = new THREE.Mesh(new THREE.BoxGeometry(0.66, 0.14, 0.02), darkSteel);
      ctrlPanel.position.set(-3.45, 0.81, -1.29);
      this.furnitureGroup.add(ctrlPanel);
      const dialKnob = new THREE.Mesh(new THREE.CylinderGeometry(0.036, 0.036, 0.026, 16), brushedSteel);
      dialKnob.rotation.x = Math.PI / 2;
      dialKnob.position.set(-3.45, 0.81, -1.275);
      this.furnitureGroup.add(dialKnob);
      const drumRing = new THREE.Mesh(new THREE.TorusGeometry(0.19, 0.028, 14, 28), mirrorSteel);
      drumRing.position.set(-3.45, 0.42, -1.285);
      this.furnitureGroup.add(drumRing);
      const drumGlass = new THREE.Mesh(
        new THREE.CircleGeometry(0.17, 24),
        new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.10, metalness: 0.65 })
      );
      drumGlass.position.set(-3.45, 0.42, -1.282);
      this.furnitureGroup.add(drumGlass);

      // Oak workbench + pegboard + red steel toolbox on left wall
      const benchTop = new THREE.Mesh(new THREE.BoxGeometry(0.76, 0.06, 1.48), woodMat);
      benchTop.position.set(-4.65, 0.90, -0.45);
      this.furnitureGroup.add(benchTop);
      const benchShelf = new THREE.Mesh(new THREE.BoxGeometry(0.68, 0.03, 1.38), woodMat);
      benchShelf.position.set(-4.65, 0.28, -0.45);
      this.furnitureGroup.add(benchShelf);
      for (const dz of [-0.66, 0.66]) {
        for (const dx of [-0.30, 0.30]) {
          const leg = new THREE.Mesh(new THREE.BoxGeometry(0.055, 0.88, 0.055), darkSteel);
          leg.position.set(-4.65 + dx, 0.44, -0.45 + dz);
          this.furnitureGroup.add(leg);
        }
      }
      const toolbox = new THREE.Mesh(
        new THREE.BoxGeometry(0.30, 0.22, 0.50),
        new THREE.MeshStandardMaterial({ color: 0xdc2626, roughness: 0.28, metalness: 0.48 })
      );
      toolbox.position.set(-4.62, 1.04, -0.35);
      this.furnitureGroup.add(toolbox);

      // High frosted basement light-well window on left wall casting cool daylight
      const lwFrame = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.68, 1.42), darkSteel);
      lwFrame.position.set(-5.15, 2.34, -0.40);
      this.furnitureGroup.add(lwFrame);
      const lwGlass = new THREE.Mesh(
        new THREE.BoxGeometry(0.04, 0.56, 1.30),
        new THREE.MeshBasicMaterial({ color: 0xdbf0ff })
      );
      lwGlass.position.set(-5.12, 2.34, -0.40);
      this.furnitureGroup.add(lwGlass);
    } else if (roomStyle === "borehole_utility") {
      // 2. RIEHEN GEOTHERMAL BOREHOLE UTILITY: Twin SIA 384/6 Erdsonde ground-probe risers + glycol expansion vessel + Minergie brine HMI
      const insulMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.72, metalness: 0.12 });
      const brineGreenMat = new THREE.MeshStandardMaterial({ color: 0x0d9488, roughness: 0.28, metalness: 0.35 });
      const valveRedMat = new THREE.MeshStandardMaterial({ color: 0xdc2626, roughness: 0.35, metalness: 0.4 });
      const valveBlueMat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.35, metalness: 0.4 });

      // Concrete bedrock collar plinth on the right where the 180 m SIA 384/6 borehole enters the ground
      const plinth = new THREE.Mesh(
        new THREE.BoxGeometry(1.18, 0.14, 0.92),
        new THREE.MeshStandardMaterial({ color: 0x64748b, roughness: 0.76, metalness: 0.1 })
      );
      plinth.position.set(4.42, 0.07, -1.25);
      this.furnitureGroup.add(plinth);
      const pShadow = makeContactShadow(1.45, 1.22);
      pShadow.position.set(4.42, 0.005, -1.25);
      this.furnitureGroup.add(pShadow);

      // Twin insulated geothermal probe risers plunging into floor flange sleeves
      for (const [bx, isSupply] of [[4.18, true], [4.62, false]]) {
        const sleeve = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.10, 0.08, 18), brushedSteel);
        sleeve.position.set(bx, 0.16, -1.25);
        this.furnitureGroup.add(sleeve);
        const riser = makePipeMesh([bx, 0.14, -1.25], [bx, 1.68, -1.25], 0.044, insulMat);
        if (riser) this.furnitureGroup.add(riser);
        // Brass ball-valve body + colored lever handle
        const vBody = new THREE.Mesh(new THREE.CylinderGeometry(0.056, 0.056, 0.12, 16), this.brassMat);
        vBody.position.set(bx, 1.05, -1.25);
        this.furnitureGroup.add(vBody);
        const lever = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.02, 0.026), isSupply ? valveRedMat : valveBlueMat);
        lever.position.set(bx + 0.07, 1.05, -1.20);
        this.furnitureGroup.add(lever);
        const g = makeAnalogGauge(isSupply ? "temp" : "pressure", this.brassMat);
        g.position.set(bx, 1.68, -1.25);
        this.furnitureGroup.add(g);
      }

      // Geothermal brine distribution cross-header + summer recharge lines from buffer tank
      const hdrBar = makePipeMesh([4.05, 1.56, -1.25], [4.75, 1.56, -1.25], 0.036, this.brassMat);
      if (hdrBar) this.furnitureGroup.add(hdrBar);
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.18, 1.56, rz],
          [4.18, 1.56, -1.25],
        ],
        0.024,
        branchHotMat,
        this.brassMat,
        [true, true]
      );
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [4.62, 1.24, -1.25],
          [4.62, 1.24, rz],
          [cxR, 1.24, rz],
        ],
        0.022,
        branchColdMat,
        this.brassMat,
        [true, true]
      );

      // Teal glycol brine expansion vessel beside borehole header
      const gExp = new THREE.Mesh(new THREE.CylinderGeometry(0.20, 0.20, 0.62, 22), brineGreenMat);
      gExp.position.set(4.48, 0.42, -0.35);
      this.furnitureGroup.add(gExp);
      const gCap = new THREE.Mesh(new THREE.SphereGeometry(0.20, 22, 12, 0, Math.PI * 2, 0, Math.PI / 2), brineGreenMat);
      gCap.position.set(4.48, 0.73, -0.35);
      this.furnitureGroup.add(gCap);
      const gFoot = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.20, 0.12, 18), darkSteel);
      gFoot.position.set(4.48, 0.06, -0.35);
      this.furnitureGroup.add(gFoot);

      // Wall-mounted Swiss Minergie Erdsonde Telemetry Controller cabinet on back-left (fully visible in default view)
      const hmiCab = new THREE.Mesh(new THREE.BoxGeometry(0.80, 0.94, 0.24), whiteEnamel);
      hmiCab.position.set(-3.45, 1.56, -2.34);
      this.furnitureGroup.add(hmiCab);
      const hmiScreen = new THREE.Mesh(
        new THREE.PlaneGeometry(0.66, 0.66),
        new THREE.MeshBasicMaterial({ map: getBoreholeControllerTexture() })
      );
      hmiScreen.position.set(-3.45, 1.63, -2.215);
      this.furnitureGroup.add(hmiScreen);
    } else if (roomStyle === "alpine_chalet") {
      // 3. DAVOS ALPINE CHALET SPA PLANT ROOM: Panoramic frosted mountain window (clear of risers!) + cedar-copper spa calorifier + alpine timber bench
      const winFrame = new THREE.Mesh(new THREE.BoxGeometry(1.94, 1.06, 0.12), darkWoodMat);
      winFrame.position.set(4.54, 2.24, -2.44);
      this.furnitureGroup.add(winFrame);
      const winGlass = new THREE.Mesh(
        new THREE.PlaneGeometry(1.78, 0.90),
        new THREE.MeshBasicMaterial({ map: getAlpineWindowTexture() })
      );
      winGlass.position.set(4.54, 2.24, -2.375);
      this.furnitureGroup.add(winGlass);
      for (const mx of [4.08, 4.54, 5.00]) {
        const muntin = new THREE.Mesh(new THREE.BoxGeometry(0.042, 0.94, 0.04), darkWoodMat);
        muntin.position.set(mx, 2.24, -2.36);
        this.furnitureGroup.add(muntin);
      }
      // Second crisp alpine snow window on open back-left wall above firewood bench
      const winFrameL = new THREE.Mesh(new THREE.BoxGeometry(1.56, 0.96, 0.12), darkWoodMat);
      winFrameL.position.set(-2.95, 2.24, -2.44);
      this.furnitureGroup.add(winFrameL);
      const winGlassL = new THREE.Mesh(
        new THREE.PlaneGeometry(1.42, 0.80),
        new THREE.MeshBasicMaterial({ map: getAlpineWindowTexture() })
      );
      winGlassL.position.set(-2.95, 2.24, -2.375);
      this.furnitureGroup.add(winGlassL);
      const muntinL = new THREE.Mesh(new THREE.BoxGeometry(0.042, 0.84, 0.04), darkWoodMat);
      muntinL.position.set(-2.95, 2.24, -2.36);
      this.furnitureGroup.add(muntinL);

      // Cedar-clad & copper-banded Alpine Spa & Sauna Calorifier vessel on the right
      const spaTank = new THREE.Mesh(new THREE.CylinderGeometry(0.46, 0.46, 1.38, 26), woodMat);
      spaTank.position.set(4.36, 0.72, -0.92);
      this.furnitureGroup.add(spaTank);
      const spaDome = new THREE.Mesh(
        new THREE.SphereGeometry(0.46, 26, 14, 0, Math.PI * 2, 0, Math.PI / 2),
        copperMat
      );
      spaDome.position.set(4.36, 1.41, -0.92);
      this.furnitureGroup.add(spaDome);
      for (const by of [0.24, 0.72, 1.24]) {
        const hoop = new THREE.Mesh(new THREE.CylinderGeometry(0.472, 0.472, 0.045, 26), copperMat);
        hoop.position.set(4.36, by, -0.92);
        this.furnitureGroup.add(hoop);
      }
      const spaShadow = makeContactShadow(1.22, 1.22);
      spaShadow.position.set(4.36, 0.005, -0.92);
      this.furnitureGroup.add(spaShadow);

      // Gleaming copper supply branch from buffer tank into the alpine spa heat exchanger
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.36, 1.56, rz],
          [4.36, 1.56, -0.92],
          [4.36, 1.82, -0.92],
        ],
        0.025,
        copperMat,
        this.brassMat,
        [true, true]
      );

      // Authentic Alpine spruce utility bench + neatly stacked seasoned firewood logs on back-left
      const bench = new THREE.Mesh(new THREE.BoxGeometry(1.42, 0.08, 0.48), woodMat);
      bench.position.set(-3.12, 0.50, -2.14);
      this.furnitureGroup.add(bench);
      for (const lx of [-3.72, -2.52]) {
        const leg = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.48, 0.44), darkWoodMat);
        leg.position.set(lx, 0.24, -2.14);
        this.furnitureGroup.add(leg);
      }
      for (let r = 0; r < 2; r++) {
        for (let c = 0; c < 5; c++) {
          const log = new THREE.Mesh(new THREE.CylinderGeometry(0.058, 0.062, 0.42, 12), darkWoodMat);
          log.rotation.x = Math.PI / 2;
          log.position.set(-3.50 + c * 0.18 + (r % 2) * 0.06, 0.08 + r * 0.11, -2.14);
          this.furnitureGroup.add(log);
        }
      }
    } else if (roomStyle === "emmental_dairy") {
      // 4. EMMENTAL CREAMERY: Stainless cheese-pasteurisation vat with overhead agitator bridge + polished milk churns + oak Emmental cheese shelves
      const vat = new THREE.Mesh(new THREE.CylinderGeometry(0.62, 0.62, 1.02, 28), mirrorSteel);
      vat.position.set(4.36, 0.51, -0.85);
      this.furnitureGroup.add(vat);
      const vatShadow = makeContactShadow(1.52, 1.52);
      vatShadow.position.set(4.36, 0.005, -0.85);
      this.furnitureGroup.add(vatShadow);
      const vatRim = new THREE.Mesh(new THREE.TorusGeometry(0.62, 0.036, 12, 28), brushedSteel);
      vatRim.rotation.x = Math.PI / 2;
      vatRim.position.set(4.36, 1.02, -0.85);
      this.furnitureGroup.add(vatRim);
      const milkTop = new THREE.Mesh(
        new THREE.CircleGeometry(0.58, 28),
        new THREE.MeshStandardMaterial({ color: 0xfffaf0, roughness: 0.18, metalness: 0.04 })
      );
      milkTop.rotation.x = -Math.PI / 2;
      milkTop.position.set(4.36, 1.01, -0.85);
      this.furnitureGroup.add(milkTop);
      // Motorized overhead stainless agitator bridge & gearbox spanning the cheese vat
      const bridge = new THREE.Mesh(new THREE.BoxGeometry(1.30, 0.07, 0.14), brushedSteel);
      bridge.position.set(4.36, 1.09, -0.85);
      this.furnitureGroup.add(bridge);
      const motor = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.11, 0.24, 16), darkSteel);
      motor.position.set(4.36, 1.24, -0.85);
      this.furnitureGroup.add(motor);
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.32, 10), mirrorSteel);
      shaft.position.set(4.36, 0.96, -0.85);
      this.furnitureGroup.add(shaft);

      // Hot-water feed pipe into cheese pasteurisation vat thermal jacket
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.36, 1.56, rz],
          [4.36, 1.56, -0.85],
          [4.36, 1.05, -1.42],
        ],
        0.024,
        branchHotMat,
        this.brassMat,
        [true, true]
      );

      // 3 Mirror-polished Swiss stainless milk churns with domed lids & handles
      for (const [cx, cz] of [[-3.55, -0.78], [-3.18, -0.52], [-3.72, -0.26]]) {
        const body = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.175, 0.54, 18), mirrorSteel);
        body.position.set(cx, 0.27, cz);
        this.furnitureGroup.add(body);
        const shoulder = new THREE.Mesh(new THREE.CylinderGeometry(0.095, 0.15, 0.12, 18), mirrorSteel);
        shoulder.position.set(cx, 0.59, cz);
        this.furnitureGroup.add(shoulder);
        const lid = new THREE.Mesh(new THREE.CylinderGeometry(0.105, 0.105, 0.04, 18), brushedSteel);
        lid.position.set(cx, 0.67, cz);
        this.furnitureGroup.add(lid);
        const cs = makeContactShadow(0.46, 0.46);
        cs.position.set(cx, 0.005, cz);
        this.furnitureGroup.add(cs);
      }

      // Heavy Bernese oak cheese-aging rack with 9 golden Emmental wheels on back-left wall
      const cheeseMat = new THREE.MeshStandardMaterial({ color: 0xefc040, roughness: 0.48, metalness: 0.04 });
      for (const ux of [-4.22, -2.88]) {
        const upright = new THREE.Mesh(new THREE.BoxGeometry(0.07, 1.88, 0.48), darkWoodMat);
        upright.position.set(ux, 0.94, -2.18);
        this.furnitureGroup.add(upright);
      }
      for (let s = 0; s < 3; s++) {
        const sy = 0.52 + s * 0.52;
        const plank = new THREE.Mesh(new THREE.BoxGeometry(1.42, 0.045, 0.48), woodMat);
        plank.position.set(-3.55, sy, -2.18);
        this.furnitureGroup.add(plank);
        for (let w = 0; w < 3; w++) {
          const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.19, 0.19, 0.135, 22), cheeseMat);
          wheel.position.set(-3.96 + w * 0.41, sy + 0.09, -2.18);
          this.furnitureGroup.add(wheel);
        }
      }
    } else if (roomStyle === "thurgau_glasshouse") {
      // 5. THURGAU HYDROPONIC TOMATO GLASSHOUSE: Structural conservatory mullions + ebb-and-flow tomato bench + overhead grow LED bar + fertigation tank
      // 3D conservatory steel mullions + clean horizontal structural transoms
      const mullionMat = new THREE.MeshStandardMaterial({ color: 0x14532d, roughness: 0.36, metalness: 0.58 });
      for (let mx = -4.8; mx <= 5.4; mx += 1.45) {
        const post = new THREE.Mesh(new THREE.BoxGeometry(0.065, 3.12, 0.085), mullionMat);
        post.position.set(mx, 1.56, -2.45);
        this.furnitureGroup.add(post);
      }
      for (const ty of [1.86, 2.86]) {
        const transom = new THREE.Mesh(new THREE.BoxGeometry(11.4, 0.055, 0.075), mullionMat);
        transom.position.set(0.2, ty, -2.45);
        this.furnitureGroup.add(transom);
      }
      const foliageLight = new THREE.MeshStandardMaterial({ color: 0x16a34a, roughness: 0.65 });
      const foliageDeep = new THREE.MeshStandardMaterial({ color: 0x15803d, roughness: 0.72 });
      const tomatoMat = new THREE.MeshStandardMaterial({ color: 0xdc2626, roughness: 0.24, metalness: 0.05 });
      const potMat = new THREE.MeshStandardMaterial({ color: 0xa16207, roughness: 0.78 });

      // Galvanized ebb-and-flow hydroponic bench on right
      const tray = new THREE.Mesh(new THREE.BoxGeometry(1.28, 0.12, 2.08), brushedSteel);
      tray.position.set(4.38, 0.78, -0.72);
      this.furnitureGroup.add(tray);
      for (const dz of [-0.90, 0.90]) {
        for (const dx of [-0.52, 0.52]) {
          const bLeg = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.76, 10), darkSteel);
          bLeg.position.set(4.38 + dx, 0.38, -0.72 + dz);
          this.furnitureGroup.add(bLeg);
        }
      }
      const benchShadow = makeContactShadow(1.55, 2.35);
      benchShadow.position.set(4.38, 0.005, -0.72);
      this.furnitureGroup.add(benchShadow);

      // Suspended full-spectrum aluminum horticultural LED grow bar above the tomato bench
      const growBar = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.06, 1.92), darkSteel);
      growBar.position.set(4.38, 2.42, -0.72);
      this.furnitureGroup.add(growBar);
      const growEmit = new THREE.Mesh(
        new THREE.BoxGeometry(0.20, 0.02, 1.84),
        new THREE.MeshBasicMaterial({ color: 0xfff2cc })
      );
      growEmit.position.set(4.38, 2.385, -0.72);
      this.furnitureGroup.add(growEmit);
      for (const dz of [-0.75, 0.75]) {
        const wire = makePipeMesh([4.38, 2.45, -0.72 + dz], [4.38, 3.12, -0.72 + dz], 0.006, brushedSteel);
        if (wire) this.furnitureGroup.add(wire);
      }

      // Low-temp 38 C root-zone heating manifold loop running directly beneath the hydroponic bed
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.38, 1.56, rz],
          [4.38, 0.68, rz],
          [4.38, 0.68, -0.72],
        ],
        0.022,
        branchHotMat,
        this.brassMat,
        [true, true]
      );

      // Multi-tiered organic leafy tomato vines, vertical trellis stakes & glossy red tomato clusters
      const leafRand = makeSeededRand(9041);
      for (let r = 0; r < 3; r++) {
        for (let c = 0; c < 2; c++) {
          const px = 4.08 + c * 0.58;
          const pz = -1.32 + r * 0.58;
          const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.10, 0.18, 14), potMat);
          pot.position.set(px, 0.92, pz);
          this.furnitureGroup.add(pot);
          const stake = makePipeMesh([px, 0.95, pz], [px, 2.38, pz], 0.008, mullionMat);
          if (stake) this.furnitureGroup.add(stake);
          // Organic multi-frond leafy canopy (asymmetric faceted foliage clusters)
          for (let tier = 0; tier < 4; tier++) {
            for (let frond = 0; frond < 3; frond++) {
              const ang = frond * 2.1 + tier * 0.9 + leafRand() * 0.5;
              const rad = 0.12 + leafRand() * 0.06;
              const leaf = new THREE.Mesh(
                new THREE.DodecahedronGeometry(rad, 0),
                (tier + frond) % 2 === 0 ? foliageLight : foliageDeep
              );
              leaf.position.set(
                px + Math.cos(ang) * (0.09 + leafRand() * 0.06),
                1.10 + tier * 0.24 + (leafRand() - 0.5) * 0.06,
                pz + Math.sin(ang) * (0.09 + leafRand() * 0.06)
              );
              leaf.rotation.set(leafRand() * 2.5, leafRand() * 3.1, leafRand() * 2.5);
              leaf.scale.set(1.35, 0.48, 0.92);
              this.furnitureGroup.add(leaf);
            }
          }
          for (const [fx, fy, fz] of [[-0.14, 1.24, 0.16], [0.11, 1.46, 0.15], [-0.08, 1.68, 0.14], [0.14, 1.22, -0.10]]) {
            const fruit = new THREE.Mesh(new THREE.SphereGeometry(0.042, 12, 10), tomatoMat);
            fruit.position.set(px + fx, fy, pz + fz);
            this.furnitureGroup.add(fruit);
          }
        }
      }

      // Translucent hydroponic nutrient fertigation dosing drum on back-left (fully visible in default view!)
      const fertTank = new THREE.Mesh(
        new THREE.CylinderGeometry(0.34, 0.34, 1.02, 22),
        new THREE.MeshStandardMaterial({ color: 0xbae6fd, roughness: 0.24, metalness: 0.08 })
      );
      fertTank.position.set(-3.15, 0.51, -1.78);
      this.furnitureGroup.add(fertTank);
      const fertCap = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 0.06, 22), mullionMat);
      fertCap.position.set(-3.15, 1.04, -1.78);
      this.furnitureGroup.add(fertCap);
      const fShadow = makeContactShadow(0.92, 0.92);
      fShadow.position.set(-3.15, 0.005, -1.78);
      this.furnitureGroup.add(fShadow);
    } else if (roomStyle === "st_jakob_pool") {
      // 6. ST. JAKOB MUNICIPAL POOL HALL: Animated turquoise caustic underwater viewing windows (clear of vertical risers!) + twin fiberglass sand filters + life-ring
      const causticTex = getPoolCausticTexture();
      this.animatedCausticTex = causticTex;
      const poolGlassMat = new THREE.MeshStandardMaterial({
        color: 0xe0fbff,
        map: causticTex,
        emissive: 0x06b6d4,
        emissiveMap: causticTex,
        emissiveIntensity: 0.76,
        roughness: 0.08,
        metalness: 0.18,
      });
      for (const [wx, ww] of [[4.56, 2.02], [-2.42, 1.52]]) {
        const poolGlass = new THREE.Mesh(new THREE.BoxGeometry(ww, 1.12, 0.06), poolGlassMat);
        poolGlass.position.set(wx, 2.04, -2.44);
        this.furnitureGroup.add(poolGlass);
        const winFrame = new THREE.Mesh(new THREE.BoxGeometry(ww + 0.14, 1.26, 0.05), mirrorSteel);
        winFrame.position.set(wx, 2.04, -2.465);
        this.furnitureGroup.add(winFrame);
      }

      // Twin commercial cobalt-blue fiberglass high-rate sand-filter vessels with multiport valves & pressure gauges
      const filterMat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.22, metalness: 0.24 });
      for (const [fx, fz] of [[4.42, -1.04], [4.42, -0.10]]) {
        const baseRing = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.37, 0.12, 24), darkSteel);
        baseRing.position.set(fx, 0.06, fz);
        this.furnitureGroup.add(baseRing);
        const vessel = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 0.96, 24), filterMat);
        vessel.position.set(fx, 0.60, fz);
        this.furnitureGroup.add(vessel);
        const dome = new THREE.Mesh(new THREE.SphereGeometry(0.35, 24, 14, 0, Math.PI * 2, 0, Math.PI / 2), filterMat);
        dome.position.set(fx, 1.08, fz);
        this.furnitureGroup.add(dome);
        const hatch = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.15, 0.07, 18), darkSteel);
        hatch.position.set(fx, 1.43, fz);
        this.furnitureGroup.add(hatch);
        const pg = makeAnalogGauge("pressure", this.brassMat);
        pg.position.set(fx - 0.14, 1.40, fz + 0.08);
        this.furnitureGroup.add(pg);
        const fs = makeContactShadow(0.95, 0.95);
        fs.position.set(fx, 0.005, fz);
        this.furnitureGroup.add(fs);
      }

      // Warm basin heat transfer manifold into the sand filter battery
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.42, 1.56, rz],
          [4.42, 1.56, -1.04],
          [4.42, 1.44, -1.04],
        ],
        0.026,
        branchHotMat,
        this.brassMat,
        [true, true]
      );
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [4.42, 1.24, -1.04],
          [4.42, 1.24, -0.10],
        ],
        0.024,
        branchColdMat,
        this.brassMat,
        [true, true]
      );

      // Poolside red-and-white safety life-ring on back-left (clear of left underwater window!)
      const ringMat = new THREE.MeshStandardMaterial({ color: 0xdc2626, roughness: 0.35 });
      const lifeRing = new THREE.Mesh(new THREE.TorusGeometry(0.26, 0.064, 14, 28), ringMat);
      lifeRing.position.set(-3.72, 1.62, -2.41);
      this.furnitureGroup.add(lifeRing);
      for (let k = 0; k < 4; k++) {
        const band = new THREE.Mesh(new THREE.BoxGeometry(0.095, 0.14, 0.14), whiteEnamel);
        const ang = (k * Math.PI) / 2;
        band.position.set(-3.72 + Math.cos(ang) * 0.26, 1.62 + Math.sin(ang) * 0.26, -2.41);
        band.rotation.z = ang;
        this.furnitureGroup.add(band);
      }
    } else if (roomStyle === "lucerne_bakery") {
      // 7. LUCERNE ARTISANAL BAKERY WASHROOM BAY: Insulated stainless 58 C wash-water calorifier + 6-tier golden bread proofing rack + stainless wash sink
      const jacketMat = new THREE.MeshStandardMaterial({ color: 0xcbd5e1, roughness: 0.24, metalness: 0.80 });
      const boiler = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, 1.56, 26), jacketMat);
      boiler.position.set(4.38, 0.78, -0.88);
      this.furnitureGroup.add(boiler);
      const bDome = new THREE.Mesh(new THREE.SphereGeometry(0.45, 26, 14, 0, Math.PI * 2, 0, Math.PI / 2), mirrorSteel);
      bDome.position.set(4.38, 1.56, -0.88);
      this.furnitureGroup.add(bDome);
      for (const by of [0.35, 0.92, 1.45]) {
        const strap = new THREE.Mesh(new THREE.CylinderGeometry(0.458, 0.458, 0.025, 26), darkSteel);
        strap.position.set(4.38, by, -0.88);
        this.furnitureGroup.add(strap);
      }
      const bGauge = makeAnalogGauge("temp", this.brassMat);
      bGauge.position.set(4.38, 1.24, -0.41);
      this.furnitureGroup.add(bGauge);
      const bShadow = makeContactShadow(1.18, 1.18);
      bShadow.position.set(4.38, 0.005, -0.88);
      this.furnitureGroup.add(bShadow);

      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.38, 1.56, rz],
          [4.38, 1.56, -0.88],
        ],
        0.025,
        branchHotMat,
        this.brassMat,
        [true, true]
      );

      // Tall mobile stainless bakery proofing rack with 6 perforated sheet pans & golden Swiss artisan loaves on back-left
      const crustMat = new THREE.MeshStandardMaterial({ color: 0xc67c2c, roughness: 0.62, metalness: 0.04 });
      const rackX = -3.72;
      const rackZ = -1.78;
      for (const dx of [-0.34, 0.34]) {
        for (const dz of [-0.26, 0.26]) {
          const post = new THREE.Mesh(new THREE.CylinderGeometry(0.016, 0.016, 1.76, 10), brushedSteel);
          post.position.set(rackX + dx, 0.90, rackZ + dz);
          this.furnitureGroup.add(post);
        }
      }
      for (let t = 0; t < 6; t++) {
        const ty = 0.26 + t * 0.27;
        const pan = new THREE.Mesh(new THREE.BoxGeometry(0.66, 0.022, 0.48), brushedSteel);
        pan.position.set(rackX, ty, rackZ);
        this.furnitureGroup.add(pan);
        for (let l = 0; l < 2; l++) {
          const loaf = new THREE.Mesh(new THREE.SphereGeometry(0.11, 14, 10), crustMat);
          loaf.scale.set(1.35, 0.68, 0.92);
          loaf.position.set(rackX - 0.16 + l * 0.32, ty + 0.065, rackZ);
          this.furnitureGroup.add(loaf);
        }
      }
      const rShadow = makeContactShadow(0.96, 0.78);
      rShadow.position.set(rackX, 0.005, rackZ);
      this.furnitureGroup.add(rShadow);

      // Commercial stainless washdown sink & arched chrome gooseneck faucet on left wall
      const sinkTop = new THREE.Mesh(new THREE.BoxGeometry(0.72, 0.16, 1.24), brushedSteel);
      sinkTop.position.set(-4.68, 0.86, -0.45);
      this.furnitureGroup.add(sinkTop);
      const splash = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.32, 1.24), mirrorSteel);
      splash.position.set(-5.02, 1.08, -0.45);
      this.furnitureGroup.add(splash);
      const faucet = new THREE.Mesh(new THREE.TorusGeometry(0.11, 0.016, 10, 18, Math.PI), mirrorSteel);
      faucet.position.set(-4.88, 1.12, -0.45);
      this.furnitureGroup.add(faucet);
    } else {
      // 8. ST. GALLEN HOSPITAL STERILE DHW & STERILISATION WING: Twin mirror-stainless hygienic calorifiers + glowing HMI telemetry wall display + medical instrument cart
      for (const [hx, hz] of [[4.24, -1.26], [4.54, -0.28]]) {
        const vessel = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.36, 1.48, 28), mirrorSteel);
        vessel.position.set(hx, 0.80, hz);
        this.furnitureGroup.add(vessel);
        const cap = new THREE.Mesh(new THREE.SphereGeometry(0.36, 28, 14, 0, Math.PI * 2, 0, Math.PI / 2), mirrorSteel);
        cap.position.set(hx, 1.54, hz);
        this.furnitureGroup.add(cap);
        const skirt = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.36, 0.12, 24), darkSteel);
        skirt.position.set(hx, 0.06, hz);
        this.furnitureGroup.add(skirt);
        const manway = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.14, 0.04, 18), brushedSteel);
        manway.rotation.x = Math.PI / 2;
        manway.position.set(hx, 0.62, hz + 0.36);
        this.furnitureGroup.add(manway);
        const hs = makeContactShadow(0.96, 0.96);
        hs.position.set(hx, 0.005, hz);
        this.furnitureGroup.add(hs);
      }

      // Insulated hygienic DHW header linking buffer tank to twin hospital calorifiers
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.24, 1.56, rz],
          [4.24, 1.56, -1.26],
          [4.24, 1.88, -1.26],
        ],
        0.025,
        branchHotMat,
        this.brassMat,
        [true, true]
      );
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [4.24, 1.88, -1.26],
          [4.54, 1.88, -1.26],
          [4.54, 1.88, -0.28],
        ],
        0.022,
        mirrorSteel,
        this.brassMat,
        [true, true]
      );

      // Wall-mounted Hospital Sterile DHW Telemetry Screen on back-right wall (100% clear of tank risers!)
      const monFrame = new THREE.Mesh(new THREE.BoxGeometry(1.74, 0.92, 0.08), darkSteel);
      monFrame.position.set(4.52, 2.24, -2.44);
      this.furnitureGroup.add(monFrame);
      const monScreen = new THREE.Mesh(
        new THREE.PlaneGeometry(1.62, 0.80),
        new THREE.MeshBasicMaterial({ map: getHospitalScreenTexture() })
      );
      monScreen.position.set(4.52, 2.24, -2.395);
      this.furnitureGroup.add(monScreen);

      // Sterile stainless surgical/autoclave instrument prep table + sterilization trays on back-left
      const table = new THREE.Mesh(new THREE.BoxGeometry(1.24, 0.055, 0.72), mirrorSteel);
      table.position.set(-3.35, 0.90, -1.78);
      this.furnitureGroup.add(table);
      const lowerShelf = new THREE.Mesh(new THREE.BoxGeometry(1.16, 0.03, 0.64), brushedSteel);
      lowerShelf.position.set(-3.35, 0.30, -1.78);
      this.furnitureGroup.add(lowerShelf);
      for (const dx of [-0.56, 0.56]) {
        for (const dz of [-0.30, 0.30]) {
          const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.88, 12), mirrorSteel);
          leg.position.set(-3.35 + dx, 0.44, -1.78 + dz);
          this.furnitureGroup.add(leg);
        }
      }
      // Autoclave stainless sterilization cassettes resting cleanly on top
      for (const tx of [-3.61, -3.07]) {
        const cassette = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.14, 0.34), brushedSteel);
        cassette.position.set(tx, 1.00, -1.78);
        this.furnitureGroup.add(cassette);
        const lidTrim = new THREE.Mesh(
          new THREE.BoxGeometry(0.44, 0.024, 0.36),
          new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.3, metalness: 0.2 })
        );
        lidTrim.position.set(tx, 1.075, -1.78);
        this.furnitureGroup.add(lidTrim);
      }
      const tShadow = makeContactShadow(1.48, 0.98);
      tShadow.position.set(-3.35, 0.005, -1.78);
      this.furnitureGroup.add(tShadow);
    }

    // Cast & receive shadows on all room props (except contact shadows / emissive flat screens)
    this.furnitureGroup.traverse((o) => {
      if (!o.isMesh) return;
      if (o.material?.transparent || o.material?.isMeshBasicMaterial) return;
      o.castShadow = true;
      o.receiveShadow = true;
    });
    if (this.renderer) this.renderer.shadowMap.needsUpdate = true;
  }

  applyRoomStyle(roomStyle) {
    const styleKey = ROOM_STYLES[roomStyle] ? roomStyle : "basel_basement";
    const st = ROOM_STYLES[styleKey];
    const tex = getRoomTextures(styleKey);
    if (this.scene) {
      this.scene.background = new THREE.Color(st.bg);
      if (this.scene.fog) {
        this.scene.fog.color.setHex(st.fogHex);
        this.scene.fog.density = st.fogDensity;
      }
    }
    if (this.floorMat) {
      this.floorMat.color.setHex(st.floor);
      this.floorMat.map = tex.floorTex;
      this.floorMat.bumpMap = tex.floorBump;
      this.floorMat.bumpScale = st.floorBump;
      this.floorMat.roughnessMap = tex.floorRough;
      this.floorMat.roughness = st.floorRough;
      this.floorMat.metalness = st.floorMetal;
      this.floorMat.envMapIntensity = st.floorEnv;
      this.floorMat.needsUpdate = true;
    }
    if (this.wallMat) {
      this.wallMat.color.setHex(st.wall);
      this.wallMat.map = tex.wallTex;
      this.wallMat.bumpMap = tex.wallBump;
      this.wallMat.bumpScale = st.wallBump;
      this.wallMat.roughnessMap = tex.wallRough;
      this.wallMat.needsUpdate = true;
    }
    if (this.ceilingMat) {
      this.ceilingMat.color.setHex(st.ceiling);
      this.ceilingMat.map = tex.ceilTex;
      this.ceilingMat.needsUpdate = true;
    }
    if (this.beamMat) this.beamMat.color.setHex(st.beam);
    if (this.accentMat) this.accentMat.color.setHex(st.accent);
    if (this.hemiLight) {
      this.hemiLight.color.setHex(st.hemiTop);
      this.hemiLight.groundColor.setHex(st.hemiBot);
      this.hemiLight.intensity = st.hemiInt;
    }
    if (this.keyLight) {
      this.keyLight.color.setHex(st.keyHex);
      this.keyLight.intensity = st.keyInt;
    }
    if (this.fillLight) {
      this.fillLight.color.setHex(st.fillHex);
      this.fillLight.intensity = st.fillInt;
    }
    for (const tl of this.trofferLights) {
      tl.color.setHex(st.trofferHex);
      tl.intensity = st.trofferInt;
    }
    if (this.accentLight && st.accentLight) {
      this.accentLight.color.setHex(st.accentLight.hex);
      this.accentLight.position.set(...st.accentLight.pos);
      this.accentLight.intensity = st.accentLight.int;
      this.accentLight.distance = st.accentLight.dist;
    }
    if (this.accentBand) {
      this.accentBand.position.y = st.accentY;
      this.accentBand.scale.y = st.accentH / 0.14;
    }
    if (this.currentRoomStyle !== styleKey) {
      this.buildRoomFurniture(styleKey);
    }
    if (this.renderer) this.renderer.shadowMap.needsUpdate = true;
  }

  syncConfig({ modelId, count, activeCount, tankSocPct, loadRatio, roomStyle }) {
    this.lastConfig = { modelId, count, activeCount, tankSocPct, loadRatio, roomStyle };
    if (!this.ready || !this.templatesLoaded || !this.dynamicGroup) return;
    this.applyRoomStyle(roomStyle || "basel_basement");

    // Dispose previous dynamic server units & branch manifolds (preserving shared SimReady template GPU buffers & shared materials)
    disposeSubtree(this.dynamicGroup);
    this.pipeHotMats = [];
    this.pipeColdMats = [];
    this.ledMats = [];
    this.flowParticles = [];
    this.heatPlumes = [];
    this.flowSpeed = 0.25 + (loadRatio || 0.7) * 0.95;

    const spec = MODEL_VISUALS[modelId] || MODEL_VISUALS.hydronode_1kw;
    const activeTpl = this.templates[spec.templateKey] || this.templates.gb300 || this.templates.vr3307 || this.templates.rack;
    const rawSz = activeTpl?.userData?.size || { x: 0.58, y: 2.12, z: 1.26 };
    const rackW = rawSz.x * spec.scale[0];
    const rackH = rawSz.y * spec.scale[1];
    const rackD = rawSz.z * spec.scale[2];
    const plinthH = modelId === "edge_micro_250w" ? 0.44 : 0.0;
    const topH = plinthH + rackH;

    const total = Math.max(1, Math.min(8, Number(count) || 1));
    const activeN = activeCount === undefined ? total : Math.max(0, Math.min(total, Number(activeCount)));

    // Arrange up to 4 SimReady racks in back row, 5..8 in front aisle row with crisp service clearance
    const positions = [];
    const backCount = Math.min(total, 4);
    const stepX = Math.max(0.84, rackW + 0.24);
    const rightAnchorX = Math.min(0.18, (this.cduBox ? this.cduBox.min.x : 1.0) - rackW * 0.5 - 0.34);
    const startX = rightAnchorX - (backCount - 1) * stepX;
    for (let i = 0; i < backCount; i++) {
      positions.push([startX + i * stepX, 0.0, -1.44]);
    }
    const frontCount = total - backCount;
    if (frontCount > 0) {
      const fStartX = rightAnchorX - 0.12 - (frontCount - 1) * stepX;
      for (let i = 0; i < frontCount; i++) {
        positions.push([fStartX + i * stepX, 0.0, 0.48]);
      }
    }

    // PRIMARY DIELECTRIC COOLANT LOOP (non-conductive engineered fluid, isolated from building water at the CDU)
    const headerIntensity = 0.16 + (loadRatio || 0.7) * 0.50;
    const dielHotMat = new THREE.MeshStandardMaterial({
      color: 0xf59e0b,
      emissive: 0xd97706,
      emissiveIntensity: headerIntensity,
      roughness: 0.26,
      metalness: 0.35,
    });
    const dielColdMat = new THREE.MeshStandardMaterial({
      color: 0x0d9488,
      emissive: 0x14b8a6,
      emissiveIntensity: 0.14 + (loadRatio || 0.7) * 0.30,
      roughness: 0.26,
      metalness: 0.35,
    });
    this.pipeHotMats.push(dielHotMat);
    this.pipeColdMats.push(dielColdMat);

    const headerHotY = Math.max(2.46, topH + 0.26);
    const headerColdY = headerHotY - 0.15;
    const headerZ = -2.14;
    const portOffX = Math.min(0.18, rackW * 0.28);
    const portOffZ = -Math.min(0.32, rackD * 0.24);

    const cduLeft = this.cduBox ? this.cduBox.min.x : 1.02;
    const cduTop = this.cduBox ? this.cduBox.max.y : 1.87;
    const cduMidZ = this.cduBox ? (this.cduBox.min.z + this.cduBox.max.z) * 0.5 : -1.42;
    const cduHotX = cduLeft + 0.24;
    const cduColdX = cduLeft + 0.50;

    const minX = Math.min(...positions.map((p) => p[0]));
    const minHotX = minX - portOffX;
    const minColdX = minX + portOffX;
    const brassMat = this.brassMat || new THREE.MeshStandardMaterial({ color: 0xd4a24c, roughness: 0.28, metalness: 0.82 });

    // Orthogonal primary dielectric overhead headers -> clean 90-deg drop into top ports of Liebert XDU heat exchanger
    const mainDielHotPath = buildOrthogonalPipe(
      this.dynamicGroup,
      [
        [minHotX, headerHotY, headerZ],
        [cduHotX, headerHotY, headerZ],
        [cduHotX, headerHotY, cduMidZ],
        [cduHotX, cduTop - 0.03, cduMidZ],
      ],
      spec.pipeRadius * 1.18,
      dielHotMat,
      brassMat,
      [false, true]
    );
    const mainDielColdPath = buildOrthogonalPipe(
      this.dynamicGroup,
      [
        [cduColdX, cduTop - 0.03, cduMidZ],
        [cduColdX, headerColdY, cduMidZ],
        [cduColdX, headerColdY, headerZ],
        [minColdX, headerColdY, headerZ],
      ],
      spec.pipeRadius * 1.18,
      dielColdMat,
      brassMat,
      [true, false]
    );
    // Seated brass bulkhead ports on top of the Liebert XDU + steel ceiling pipe hangers
    for (const pxPort of [cduHotX, cduColdX]) {
      const port = new THREE.Mesh(new THREE.CylinderGeometry(0.056, 0.060, 0.05, 14), brassMat);
      port.position.set(pxPort, cduTop + 0.01, cduMidZ);
      this.dynamicGroup.add(port);
    }
    const hangerMat = new THREE.MeshStandardMaterial({ color: 0x94a3b8, roughness: 0.35, metalness: 0.78 });
    for (const hx of [minHotX + 0.25, (minHotX + cduHotX) * 0.5, cduHotX - 0.22]) {
      const rod = makePipeMesh([hx, headerHotY, headerZ], [hx, 3.16, headerZ], 0.012, hangerMat);
      if (rod) this.dynamicGroup.add(rod);
    }

    positions.forEach(([px, py, pz], idx) => {
      const isRunning = idx < activeN;
      const unitGrp = new THREE.Group();
      unitGrp.position.set(px, py, pz);

      if (plinthH > 0) {
        const plinth = new THREE.Mesh(
          new THREE.BoxGeometry(rackW * 1.04, plinthH, rackD * 1.02),
          new THREE.MeshStandardMaterial({ color: 0x262c36, roughness: 0.44, metalness: 0.52 })
        );
        plinth.position.y = plinthH * 0.5;
        unitGrp.add(plinth);
      }

      let hasBuiltInLed = false;
      if (activeTpl) {
        const rack = activeTpl.clone(true);
        rack.position.y = plinthH;
        rack.scale.set(...spec.scale);
        if (spec.templateKey === "gb300") {
          rack.traverse((o) => {
            if (!o.isMesh || !o.material) return;
            const m = o.material;
            if (!Array.isArray(m) && m.emissive && (m.emissive.r + m.emissive.g + m.emissive.b) > 0.05) {
              const clonedLed = m.clone();
              clonedLed.emissiveIntensity = isRunning ? 1.65 : 0.08;
              if (!isRunning) clonedLed.emissive.setHex(0x334155);
              o.material = clonedLed;
              o.userData.clonedMaterial = true;
              this.ledMats.push({ mat: clonedLed, running: isRunning, phase: idx * 0.7 });
              hasBuiltInLed = true;
            }
          });
        }
        unitGrp.add(rack);
      } else {
        const box = new THREE.Mesh(
          new THREE.BoxGeometry(0.60, 2.0, 1.05),
          new THREE.MeshStandardMaterial({ color: 0x20252d, roughness: 0.34, metalness: 0.65 })
        );
        box.position.y = 1.0;
        unitGrp.add(box);
      }

      // Slim architectural top-bezel status LED strip for Vertiv cabinets (zero obstruction of perforated door or Vertiv badge)
      if (!hasBuiltInLed) {
        const ledMat = new THREE.MeshStandardMaterial({
          color: isRunning ? spec.ledHex : 0x64748b,
          emissive: isRunning ? spec.ledHex : 0x334155,
          emissiveIntensity: isRunning ? 1.25 : 0.10,
          roughness: 0.18,
        });
        this.ledMats.push({ mat: ledMat, running: isRunning, phase: idx * 0.7 });
        const strip = new THREE.Mesh(new THREE.BoxGeometry(rackW * 0.68, 0.018, 0.016), ledMat);
        strip.position.set(0, topH - 0.028, rackD * 0.5 + 0.008);
        unitGrp.add(strip);
      }

      // Machined brass quick-disconnect bulkhead couplings on top-rear roof of each SimReady rack
      for (const bx of [-portOffX, portOffX]) {
        const coupling = new THREE.Mesh(new THREE.CylinderGeometry(0.036, 0.042, 0.045, 14), brassMat);
        coupling.position.set(bx, topH + 0.018, portOffZ);
        unitGrp.add(coupling);
      }

      // Grounded studio contact shadow + subtle architectural floor bay outline under each unit
      unitGrp.add(makeContactShadow(rackW + 0.44, rackD + 0.44));
      const pad = new THREE.Mesh(
        new THREE.BoxGeometry(rackW + 0.12, 0.008, rackD + 0.12),
        new THREE.MeshStandardMaterial({
          color: isRunning ? spec.accentHex : 0x94a3b8,
          transparent: true,
          opacity: isRunning ? 0.14 : 0.06,
          roughness: 0.85,
        })
      );
      pad.position.y = 0.004;
      unitGrp.add(pad);

      this.dynamicGroup.add(unitGrp);

      // Clean 90-degree orthogonal branches from top-rear rack bulkhead couplings straight up & back into overhead dielectric headers
      const portZ = pz + portOffZ;
      const hotBranchCurve = buildOrthogonalPipe(
        this.dynamicGroup,
        [
          [px - portOffX, topH + 0.01, portZ],
          [px - portOffX, headerHotY, portZ],
          [px - portOffX, headerHotY, headerZ],
        ],
        spec.pipeRadius,
        dielHotMat,
        brassMat,
        [true, true]
      );
      const coldBranchCurve = buildOrthogonalPipe(
        this.dynamicGroup,
        [
          [px + portOffX, headerColdY, headerZ],
          [px + portOffX, headerColdY, portZ],
          [px + portOffX, topH + 0.01, portZ],
        ],
        spec.pipeRadius,
        dielColdMat,
        brassMat,
        [true, true]
      );

      // Armored matte-black 3-phase power whip dropping cleanly from the overhead cable ladder tray into rack roof
      const whipMat = new THREE.MeshStandardMaterial({ color: 0x1e242d, roughness: 0.52, metalness: 0.25 });
      buildOrthogonalPipe(
        this.dynamicGroup,
        [
          [px, 2.73, -2.02],
          [px, 2.73, portZ],
          [px, topH + 0.01, portZ],
        ],
        0.014,
        whipMat,
        null,
        [false, false]
      );

      // Animated dielectric coolant flow beads gliding smoothly along the exact orthogonal branches
      if (isRunning) {
        const pGeo = new THREE.SphereGeometry(spec.pipeRadius * 1.48, 10, 8);
        const dielHotBeadMat = new THREE.MeshBasicMaterial({ color: 0xfde047 });
        const dielColdBeadMat = new THREE.MeshBasicMaterial({ color: 0x5eead4 });
        for (let k = 0; k < 2; k++) {
          const hb = new THREE.Mesh(pGeo, dielHotBeadMat);
          this.dynamicGroup.add(hb);
          this.flowParticles.push({ mesh: hb, curve: hotBranchCurve, phase: (k * 0.5 + idx * 0.19) % 1.0, speed: 0.55 });
          const cb = new THREE.Mesh(pGeo, dielColdBeadMat);
          this.dynamicGroup.add(cb);
          this.flowParticles.push({ mesh: cb, curve: coldBranchCurve, phase: (k * 0.5 + idx * 0.23) % 1.0, speed: 0.55 });
        }
      }
    });

    // Main animated fluid particles along BOTH loops:
    // Loop 1 (Gold/Teal): Dielectric coolant along header <-> Liebert XDU heat exchanger
    // Loop 2 (Vermilion/Sky): Building heating water from XDU -> Valve -> Buffer Tank -> Building Riser
    if (activeN > 0) {
      const mGeo = new THREE.SphereGeometry(0.052, 10, 8);
      const dielHotFlowMat = new THREE.MeshBasicMaterial({ color: 0xfef08a });
      const dielColdFlowMat = new THREE.MeshBasicMaterial({ color: 0x99f6e4 });
      const waterHotFlowMat = new THREE.MeshBasicMaterial({ color: 0xfdba74 });
      const waterColdFlowMat = new THREE.MeshBasicMaterial({ color: 0xbae6fd });

      const loopStreams = [
        [mainDielHotPath, 5, 0.34, dielHotFlowMat],
        [mainDielColdPath, 5, 0.34, dielColdFlowMat],
      ];
      if (this.secondaryPaths) {
        loopStreams.push(
          [this.secondaryPaths.hotCduToTank, 2, 0.62, waterHotFlowMat],
          [this.secondaryPaths.hotRiser, 3, 0.48, waterHotFlowMat],
          [this.secondaryPaths.coldDowncomer, 3, 0.48, waterColdFlowMat],
          [this.secondaryPaths.coldTankToCdu, 2, 0.62, waterColdFlowMat]
        );
      }

      for (const [cv, cnt, spd, mat] of loopStreams) {
        for (let k = 0; k < cnt; k++) {
          const m = new THREE.Mesh(mGeo, mat);
          this.dynamicGroup.add(m);
          this.flowParticles.push({ mesh: m, curve: cv, phase: k / cnt, speed: spd });
        }
      }

      // Gentle rising thermal shimmer rings centered neatly around the building hot-water supply riser above the tank
      const plumePos = this.secondaryPaths
        ? [this.secondaryPaths.riserHotX, this.secondaryPaths.tankTop + 0.16, this.secondaryPaths.tankMidZ]
        : [2.84, 1.40, -1.42];
      for (let k = 0; k < 3; k++) {
        const ringMat = new THREE.MeshBasicMaterial({
          color: 0xf97316,
          transparent: true,
          opacity: 0.42,
          side: THREE.DoubleSide,
        });
        const ring = new THREE.Mesh(new THREE.RingGeometry(0.16, 0.21, 24), ringMat);
        ring.rotation.x = -Math.PI / 2;
        ring.position.set(plumePos[0], plumePos[1], plumePos[2]);
        this.dynamicGroup.add(ring);
        this.heatPlumes.push({ mesh: ring, mat: ringMat, basePos: plumePos, phase: k / 3 });
      }
    }

    // Update flush buffer tank thermal stratification sight glass strictly within the tank bezel bounds
    const soc = Math.max(0.15, Math.min(0.92, (Number(tankSocPct) || 65) / 100.0));
    if (this.tankHotBar && this.tankColdBar && this.tankSightMetrics) {
      const { minY, height } = this.tankSightMetrics;
      const hHot = Math.max(0.08, height * soc);
      const hCold = Math.max(0.08, height * (1.0 - soc));
      this.tankColdBar.scale.y = hCold;
      this.tankColdBar.position.y = minY + hCold * 0.5;
      this.tankHotBar.scale.y = hHot;
      this.tankHotBar.position.y = minY + hCold + hHot * 0.5;
    }

    if (this.staticHotMat) {
      this.staticHotMat.emissiveIntensity = headerIntensity;
    }
    if (this.renderer) this.renderer.shadowMap.needsUpdate = true;
    this.renderOnce();
  }

  resize() {
    if (!this.renderer || !this.camera || !this.hostEl) return;
    const w = Math.max(300, this.hostEl.clientWidth);
    const h = Math.max(200, this.hostEl.clientHeight);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.renderOnce();
  }

  resetCamera() {
    if (!this.camera || !this.controls) return;
    this.camera.position.set(0.50, 2.02, 7.62);
    this.controls.target.set(0.50, 1.14, -1.20);
    this.controls.update();
    this.renderOnce();
  }

  renderOnce() {
    if (this.renderer && this.scene && this.camera) {
      this.renderer.render(this.scene, this.camera);
    }
  }

  start() {
    this.init();
    this.active = true;
    this.resize();
    if (this.rafId !== null) return;
    const clock = new THREE.Clock();
    const tick = () => {
      if (!this.active) {
        this.rafId = null;
        return;
      }
      this.rafId = requestAnimationFrame(tick);
      const t = clock.getElapsedTime();
      if (this.controls) this.controls.update();
      for (const item of this.ledMats) {
        if (item.running) {
          item.mat.emissiveIntensity = 0.95 + 0.42 * Math.sin(t * 3.6 + item.phase);
        }
      }
      for (const fp of this.flowParticles) {
        const u = (t * fp.speed * this.flowSpeed + fp.phase) % 1.0;
        if (typeof fp.curve.getPointAt === "function") {
          fp.curve.getPointAt(u, fp.mesh.position);
        } else {
          fp.curve.getPoint(u, fp.mesh.position);
        }
      }
      for (const hp of this.heatPlumes) {
        const u = (t * 0.42 * this.flowSpeed + hp.phase) % 1.0;
        hp.mesh.position.y = hp.basePos[1] + u * 1.15;
        const sc = 0.75 + u * 0.75;
        hp.mesh.scale.set(sc, sc, 1);
        hp.mat.opacity = Math.sin(u * Math.PI) * 0.38;
      }
      if (this.animatedCausticTex) {
        this.animatedCausticTex.offset.x = Math.sin(t * 0.42) * 0.045;
        this.animatedCausticTex.offset.y = (t * 0.018) % 1.0;
      }
      this.renderOnce();
    };
    this.rafId = requestAnimationFrame(tick);
  }

  stop() {
    this.active = false;
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }
}

export function createConfiguratorAndLedgerController({ onOpenTab }) {
  const cfgOverlay = document.querySelector("[data-testid='configurator-overlay']");
  const hlOverlay = document.querySelector("[data-testid='heatledger-overlay']");
  const cfgStage3d = document.getElementById("cfg-3d-host");
  const cfgExterior = document.getElementById("cfg-exterior-stage");
  const cfgExtVideo = document.getElementById("cfg-exterior-video");
  const cfgExtBadge = document.getElementById("cfg-exterior-badge");
  const cfg3dBadge = document.getElementById("cfg-3d-badge");
  const hlStage3d = document.getElementById("hl-3d-host");
  const hl3dBadge = document.getElementById("hl-3d-badge");

  if (!cfgOverlay || !hlOverlay) {
    return {
      openConfigurator: async () => {},
      openHeatLedger: async () => {},
      closeAll: () => {},
      isOpen: () => false,
      prefillFromNode: async () => {},
    };
  }

  const cfgStudio = new Basement3DStudio(cfgStage3d);
  const hlStudio = new Basement3DStudio(hlStage3d);

  let catalog = null;
  let latestQuote = null;
  let latestSiteTelemetry = null;
  let latestLogs = null;
  let logCategoryFilter = "";
  let activePane = null; // "configure" | "heatledger" | null
  let cfgViewMode = "basement"; // "basement" | "exterior"
  let hlPollTimer = null;
  let quoteAbort = null;
  let telemetryAbort = null;
  let logsAbort = null;

  const state = {
    deploymentId: "house_basel",
    serverModelId: "hydronode_1kw",
    serverCount: 4,
    heatTargetPct: 100,
    indoorTargetC: 21.0,
    hasExistingBoiler: false,
    heaterType: "",
  };

  function renewAbort(prev) {
    if (prev) prev.abort();
    return new AbortController();
  }

  async function ensureCatalog() {
    if (catalog) return catalog;
    const resp = await fetch("/api/v1/configurator/catalog");
    if (!resp.ok) throw new Error(`Catalog HTTP ${resp.status}`);
    catalog = await resp.json();
    populateSelectors();
    return catalog;
  }

  function populateSelectors() {
    if (!catalog) return;
    const depSelect = document.getElementById("cfg-deployment-select");
    const hlDepSelect = document.getElementById("hl-deployment-select");
    const buildDepFrag = () => {
      const frag = document.createDocumentFragment();
      for (const d of catalog.deployments) {
        frag.appendChild(domEl("option", { value: d.id, text: d.label }));
      }
      return frag;
    };
    if (depSelect) {
      depSelect.replaceChildren(buildDepFrag());
      depSelect.value = state.deploymentId;
    }
    if (hlDepSelect) {
      hlDepSelect.replaceChildren(buildDepFrag());
      hlDepSelect.value = state.deploymentId;
    }

    const modelsHost = document.getElementById("cfg-server-models");
    if (modelsHost) {
      const frag = document.createDocumentFragment();
      for (const m of catalog.server_models) {
        const waterKw = (m.electrical_kw * m.recovery_fraction).toFixed(2);
        const capTag = domEl("span", {
          className: "cfg-model-cap",
          text: m.gpu_count > 0 ? `${m.gpu_count} GPUs/rack · ${waterKw} kW heat` : `${waterKw} kW hot water`,
        });
        const btn = domEl(
          "button",
          {
            type: "button",
            className: "cfg-model-card",
            dataset: { modelId: m.id },
          },
          [
            domEl("div", { className: "cfg-model-top" }, [
              domEl("strong", { text: m.name }),
              domEl("span", { className: "cfg-model-price is-hosted", text: "CHF 0 · Supplied" }),
            ]),
            domEl("div", {
              className: "cfg-model-meta",
              text: `${m.vendor_ref} · ${m.cooling_type}`,
            }),
            domEl("div", { className: "cfg-model-tags" }, [
              capTag,
              domEl("span", { text: m.workload_profile }),
            ]),
          ]
        );
        btn.addEventListener("click", async () => {
          state.serverModelId = m.id;
          const maxU = getSiteMaxUnits(state.deploymentId, m.id);
          if (state.serverCount > maxU) state.serverCount = maxU;
          setCfgViewMode("basement");
          await refreshQuote();
        });
        frag.appendChild(btn);
      }
      modelsHost.replaceChildren(frag);
    }
  }

  function setCfgViewMode(mode) {
    cfgViewMode = mode;
    const isExt = mode === "exterior";
    if (cfgExterior) cfgExterior.hidden = !isExt;
    if (cfgStage3d) cfgStage3d.hidden = isExt;
    if (cfg3dBadge) cfg3dBadge.hidden = isExt;
    const btnExt = document.getElementById("cfg-mode-exterior");
    const btnBas = document.getElementById("cfg-mode-basement");
    btnExt?.classList.toggle("is-active", isExt);
    btnBas?.classList.toggle("is-active", !isExt);
    const cfgPipBtn = document.getElementById("cfg-pip-btn");
    const cfgPipVid = document.getElementById("cfg-pip-video");
    if (cfgPipBtn) cfgPipBtn.hidden = isExt;
    if (isExt) {
      cfgStudio.stop();
      if (cfgPipVid) cfgPipVid.pause();
      if (cfgExtVideo) cfgExtVideo.play().catch(() => {});
    } else {
      if (cfgExtVideo) cfgExtVideo.pause();
      if (cfgPipVid) cfgPipVid.play().catch(() => {});
      cfgStudio.start();
      if (latestQuote) {
        cfgStudio.syncConfig({
          modelId: state.serverModelId,
          count: state.serverCount,
          activeCount: state.serverCount,
          tankSocPct: 68,
          loadRatio: 0.85,
          roomStyle: DEPLOYMENT_TO_ROOM[state.deploymentId] || "basement",
        });
      }
    }
  }

  async function refreshSiteTelemetry() {
    if (!latestQuote?.deployment?.preview_node_id) return;
    telemetryAbort = renewAbort(telemetryAbort);
    const siteId = latestQuote.deployment.preview_node_id;
    try {
      const resp = await fetch(
        `/api/v1/fleet/telemetry?site_id=${encodeURIComponent(siteId)}`,
        { signal: telemetryAbort.signal }
      );
      if (!resp.ok) return;
      latestSiteTelemetry = await resp.json();
      renderSiteTelemetry();
    } catch (err) {
      if (err?.name !== "AbortError") {
        // Non-fatal telemetry poll error
      }
    }
  }

  async function refreshAppLogs() {
    logsAbort = renewAbort(logsAbort);
    const qs = new URLSearchParams({ limit: "35", order: "desc" });
    if (logCategoryFilter) qs.set("category", logCategoryFilter);
    try {
      const resp = await fetch(`/api/v1/logs?${qs.toString()}`, {
        signal: logsAbort.signal,
      });
      if (!resp.ok) return;
      latestLogs = await resp.json();
      const logsEl = document.getElementById("cg-app-logs");
      renderLogFeed(
        logsEl,
        latestLogs,
        logCategoryFilter,
        (cat) => {
          logCategoryFilter = cat;
          refreshAppLogs().catch(() => {});
        },
        () => refreshAppLogs().catch(() => {})
      );
    } catch (err) {
      if (err?.name !== "AbortError") {
        // Non-fatal logs poll error
      }
    }
  }

  async function refreshQuote() {
    await ensureCatalog();
    quoteAbort = renewAbort(quoteAbort);
    const qs = new URLSearchParams({
      deployment_id: state.deploymentId,
      server_model_id: state.serverModelId,
      server_count: String(state.serverCount),
      heat_target_pct: String(state.heatTargetPct),
      indoor_target_c: String(state.indoorTargetC),
      has_existing_boiler: state.hasExistingBoiler ? "true" : "false",
    });
    if (state.heaterType) {
      qs.set("heater_type", state.heaterType);
    }
    try {
      const resp = await fetch(`/api/v1/configurator/quote?${qs.toString()}`, {
        signal: quoteAbort.signal,
      });
      if (!resp.ok) throw new Error(`Quote HTTP ${resp.status}`);
      latestQuote = await resp.json();
      renderConfigurator();
      renderHeatLedger();
      if (activePane === "heatledger") {
        refreshSiteTelemetry().catch(() => {});
        refreshAppLogs().catch(() => {});
      }
      return latestQuote;
    } catch (err) {
      if (err?.name === "AbortError") return latestQuote;
      throw err;
    }
  }

  function buildHeatSplitBar(hostHorizon) {
    if (!hostHorizon) return [];
    const cgPct = hostHorizon.calorgrid_heat_share_pct;
    const defPct = hostHorizon.default_heater_share_pct;
    const cgPctTxt = cgPct !== null && cgPct !== undefined ? `${cgPct.toFixed(0)}%` : "--%";
    const defPctTxt = defPct !== null && defPct !== undefined ? `${defPct.toFixed(0)}%` : "--%";
    const barCgW = cgPct !== null && cgPct !== undefined ? Math.max(0, Math.min(100, cgPct)) : 0;
    const barDefW = defPct !== null && defPct !== undefined ? Math.max(0, Math.min(100, defPct)) : 0;

    return [
      domEl("div", { className: "cg-split-meta" }, [
        domEl("span", {
          className: "cg-split-cg",
          text: `CalorGrid ${fmtNum(hostHorizon.calorgrid_heat_kwh, 0)} kWh (${cgPctTxt})`,
        }),
        domEl("span", {
          className: "cg-split-def",
          text: `${hostHorizon.heater_label.replace(/\s*\([^)]*\)/, "")} ${fmtNum(hostHorizon.default_heater_heat_kwh, 0)} kWh (${defPctTxt})`,
        }),
      ]),
      domEl("div", { className: "cg-split-bar" }, [
        domEl("div", { className: "cg-split-seg is-cg", style: { width: `${barCgW}%` } }),
        domEl("div", { className: "cg-split-seg is-def", style: { width: `${barDefW}%` } }),
      ]),
    ];
  }

  function getSiteMaxUnits(depId, modelId) {
    const dep = catalog?.deployments?.find((d) => d.id === depId);
    if (dep?.max_units_by_model && dep.max_units_by_model[modelId]) {
      return Number(dep.max_units_by_model[modelId]);
    }
    const mdl = catalog?.server_models?.find((m) => m.id === modelId);
    return mdl ? Number(mdl.max_units) : 8;
  }

  function renderConfigurator() {
    if (!latestQuote) return;
    const dep = latestQuote.deployment;
    const mdl = latestQuote.server_model;
    const sz = latestQuote.sizing;
    const fin = latestQuote.financials;
    const hostLedger = latestQuote.host_ledger;
    const opLedger = latestQuote.operator_ledger;

    const depSel = document.getElementById("cfg-deployment-select");
    if (depSel && depSel.value !== dep.id) depSel.value = dep.id;
    const cfgHeaterSel = document.getElementById("cfg-heater-select");
    if (cfgHeaterSel && !state.heaterType && hostLedger?.heater_type) {
      cfgHeaterSel.value = "";
    }

    const extSrc = `/media/twins/${dep.preview_node_id}.mp4`;
    if (cfgExtVideo) {
      if (cfgExtVideo.getAttribute("src") !== extSrc) {
        cfgExtVideo.setAttribute("src", extSrc);
        cfgExtVideo.load();
      }
      if (activePane === "configure" && cfgViewMode === "exterior") {
        cfgExtVideo.play().catch(() => {});
      }
    }
    const cfgPip = document.getElementById("cfg-pip-video");
    if (cfgPip && cfgPip.getAttribute("src") !== extSrc) {
      cfgPip.setAttribute("src", extSrc);
      cfgPip.load();
    }
    const cfgPipWrap = document.getElementById("cfg-pip-btn");
    if (cfgPipWrap) cfgPipWrap.hidden = cfgViewMode === "exterior";
    if (cfgPip && activePane === "configure" && cfgViewMode === "basement") {
      cfgPip.play().catch(() => {});
    }
    if (cfgExtBadge) {
      cfgExtBadge.textContent = `${dep.label}`;
    }
    const siteMaxU = Number(sz.site_max_units || getSiteMaxUnits(dep.id, mdl.id));
    if (cfg3dBadge) {
      const gpuPart = sz.installed_gpus > 0 ? ` (${sz.installed_gpus} GPUs)` : "";
      cfg3dBadge.textContent = `${latestQuote.server_count} x ${mdl.name}${gpuPart} · ${sz.peak_water_kw.toFixed(1)} kW heat (${mdl.supply_temp_c} C)`;
    }

    document.querySelectorAll("#cfg-server-models [data-model-id]").forEach((btn) => {
      const mid = btn.dataset.modelId;
      btn.classList.toggle("is-selected", mid === mdl.id);
      const capEl = btn.querySelector(".cfg-model-cap");
      const mSpec = catalog?.server_models?.find((x) => x.id === mid);
      if (capEl && mSpec) {
        const mMax = getSiteMaxUnits(dep.id, mid);
        const wKw = (mSpec.electrical_kw * mSpec.recovery_fraction).toFixed(2);
        capEl.textContent =
          mSpec.gpu_count > 0
            ? `Up to ${mMax} racks (${mMax * mSpec.gpu_count} GPUs) · ${wKw} kW`
            : `Up to ${mMax} units · ${wKw} kW heat`;
      }
    });

    const countVal = document.getElementById("cfg-count-val");
    if (countVal) countVal.textContent = String(latestQuote.server_count);
    const minusBtn = document.getElementById("cfg-count-minus");
    const plusBtn = document.getElementById("cfg-count-plus");
    if (minusBtn) minusBtn.disabled = latestQuote.server_count <= 1;
    if (plusBtn) plusBtn.disabled = latestQuote.server_count >= siteMaxU;
    const countSub = document.getElementById("cfg-count-sub");
    if (countSub) {
      countSub.textContent =
        sz.installed_gpus > 0
          ? `${sz.installed_gpus} GPUs (max ${sz.site_max_gpus}) · ${sz.peak_water_kw.toFixed(1)} kW heat`
          : `Max ${siteMaxU} units · ${sz.peak_water_kw.toFixed(1)} kW hot water`;
    }

    const fitEl = document.getElementById("cfg-fit-banner");
    if (fitEl) {
      fitEl.className = `cfg-fit-banner is-${sz.fit_status}`;
      fitEl.replaceChildren(
        domEl("strong", { text: sz.fit_label }),
        domEl("span", { text: sz.fit_note })
      );
    }

    const boilerToggle = document.getElementById("cfg-existing-boiler-toggle");
    if (boilerToggle) boilerToggle.checked = Boolean(fin.has_existing_boiler);
    const boilerStateLbl = document.getElementById("cfg-boiler-state-lbl");
    if (boilerStateLbl) boilerStateLbl.textContent = fin.has_existing_boiler ? "Yes" : "No";
    const boilerSub = document.getElementById("cfg-boiler-sub");
    if (boilerSub) {
      boilerSub.textContent = fin.has_existing_boiler
        ? "Keep existing heating system · CHF 0 new boiler cost"
        : `Optional subsidized boiler: CHF ${Math.round(fin.boiler_subsidized_chf).toLocaleString()} (was CHF ${Math.round(fin.boiler_retail_chf).toLocaleString()})`;
    }

    const kpisEl = document.getElementById("cfg-live-kpis");
    if (kpisEl) {
      const paybackTxt = fin.host_payback_years ? `${fin.host_payback_years} yr payback` : "Instant payback";
      const boilerDetail = fin.has_existing_boiler
        ? `Tie-in only (CHF ${Math.round(fin.tie_in_chf).toLocaleString()})`
        : `Boiler CHF ${Math.round(fin.boiler_cost_chf).toLocaleString()} + ${Math.round(fin.tie_in_chf).toLocaleString()} tie-in`;
      const baselineYr = hostLedger ? Math.round(hostLedger.baseline_heating_bill_chf) : 0;
      const backupYr = hostLedger ? Math.round(hostLedger.backup_heater_cost_chf) : 0;
      const opCostYr = opLedger ? Math.round(opLedger.calorgrid_operating_cost_chf) : 0;
      kpisEl.replaceChildren(
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Annual heating saved" }),
          domEl("strong", { className: "accent-hosted", text: `+CHF ${Math.round(fin.host_annual_heat_savings_chf).toLocaleString()}/yr` }),
          domEl("small", { text: `${paybackTxt}, ${(sz.co2_avoided_kg_yr / 1000).toFixed(1)} t CO2 avoided` }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Remaining backup bill" }),
          domEl("strong", { text: `CHF ${backupYr.toLocaleString()}/yr` }),
          domEl("small", { text: `Down from CHF ${baselineYr.toLocaleString()}/yr baseline` }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Your upfront cost" }),
          domEl("strong", { text: `CHF ${Math.round(fin.host_upfront_chf).toLocaleString()}` }),
          domEl("small", { text: boilerDetail }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Servers & server power" }),
          domEl("strong", { className: "accent-hosted", text: "CHF 0" }),
          domEl("small", { text: `CalorGrid pays CHF ${opCostYr.toLocaleString()}/yr opex` }),
        ])
      );
    }

    const cfgBreakdownEl = document.getElementById("cfg-host-breakdown");
    if (cfgBreakdownEl && hostLedger) {
      cfgBreakdownEl.replaceChildren(...buildHeatSplitBar(hostLedger));
    }

    if (activePane === "configure" && cfgViewMode === "basement") {
      cfgStudio.syncConfig({
        modelId: mdl.id,
        count: latestQuote.server_count,
        activeCount: latestQuote.server_count,
        tankSocPct: 68,
        loadRatio: 0.85,
        roomStyle: DEPLOYMENT_TO_ROOM[state.deploymentId] || "basement",
      });
    }
  }

  function renderSiteTelemetry() {
    const hostEl = document.getElementById("hl-live-telemetry");
    if (!hostEl) return;
    if (!latestSiteTelemetry?.sites?.length) {
      hostEl.replaceChildren(
        domEl("div", { className: "cg-log-empty", text: "Loading site GPU telemetry..." })
      );
      return;
    }
    const site = latestSiteTelemetry.sites[0];
    const jobs = Array.isArray(latestSiteTelemetry.active_jobs)
      ? latestSiteTelemetry.active_jobs
      : [];

    const tierPretty = { on_demand: "On-Demand", spot: "Spot", backfill: "Backfill" };
    const jobCountTxt = jobs.length === 1 ? "1 active job" : `${jobs.length} active jobs`;
    const header = domEl("div", { className: "hl-tel-summary" }, [
      domEl("div", {}, [
        domEl("strong", { text: `${site.name} (${site.canton})` }),
        domEl("span", {
          text: `Indoor ${site.indoor_temp_c}°C (target ${site.target_temp_c}°C), outdoor ${site.outdoor_temp_c}°C, ${fmtNum(site.thermal_headroom_kw, 1)} kW headroom`,
        }),
      ]),
      domEl("div", { className: "hl-tel-badges" }, [
        domEl("span", {
          className: "hl-proc-pill is-run",
          text: `${site.gpus_busy}/${site.gpus_total} GPUs active, ${fmtNum(site.kw_th, 2)} kW heat`,
        }),
        domEl("span", {
          className: "hl-proc-pill is-offload",
          text: jobCountTxt,
        }),
      ]),
    ]);

    const jobRowList = domEl("div", { className: "hl-tel-jobs" });
    if (jobs.length > 0) {
      for (const j of jobs.slice(0, 4)) {
        jobRowList.appendChild(
          domEl("div", { className: "hl-tel-job-pill" }, [
            domEl("strong", { text: `${j.job_name}` }),
            domEl("span", {
              text: `${tierPretty[j.tier] || j.tier} · ${fmtNum(j.kw_th, 2)} kW heat · ${j.remaining_hours}h left`,
            }),
          ])
        );
      }
    } else {
      jobRowList.appendChild(
        domEl("div", {
          className: "hl-tel-job-pill is-empty",
          text: "No active customer jobs assigned to this site right now.",
        })
      );
    }

    hostEl.replaceChildren(header, jobRowList);
  }

  function renderHeatLedger() {
    if (!latestQuote) return;
    const dep = latestQuote.deployment;
    const mdl = latestQuote.server_model;
    const fc = latestQuote.forecast;
    const hl = latestQuote.heatledger_tomorrow;
    const hostTom = latestQuote.host_ledger?.tomorrow;
    const opTom = latestQuote.operator_ledger?.tomorrow;

    const hlDepSel = document.getElementById("hl-deployment-select");
    if (hlDepSel && hlDepSel.value !== dep.id) hlDepSel.value = dep.id;

    const hlPip = document.getElementById("hl-pip-video");
    const hlPipSrc = `/media/twins/${dep.preview_node_id}.mp4`;
    if (hlPip) {
      if (hlPip.getAttribute("src") !== hlPipSrc) {
        hlPip.setAttribute("src", hlPipSrc);
        hlPip.load();
      }
      if (activePane === "heatledger") hlPip.play().catch(() => {});
    }

    const weatherEl = document.getElementById("hl-weather-card");
    if (weatherEl) {
      weatherEl.replaceChildren(
        domEl("div", { className: "hl-weather-top" }, [
          domEl("div", {}, [
            domEl("span", { className: "hl-weather-loc", text: `${fc.city}, ${fc.canton} · ${fc.date}` }),
            domEl("strong", { text: `${fc.temp_min_c} C to ${fc.temp_max_c} C · ${fc.condition}` }),
          ]),
          domEl("span", { className: "hl-source-caption", text: "Open-Meteo" }),
        ]),
        domEl("div", {
          className: "hl-weather-sub",
          text: `Mean outdoor ${fc.temp_mean_c} C · Building demand tomorrow: ${hl.recommended_kwh} kWh`,
        })
      );
    }

    const pctSlider = document.getElementById("hl-heat-slider");
    const pctOut = document.getElementById("hl-heat-readout");
    if (pctSlider && Number(pctSlider.value) !== state.heatTargetPct) pctSlider.value = String(state.heatTargetPct);
    if (pctOut) pctOut.textContent = `${hl.target_kwh} kWh (${hl.heat_target_pct}%)`;

    const tempSlider = document.getElementById("hl-indoor-slider");
    const tempOut = document.getElementById("hl-indoor-readout");
    if (tempSlider && Number(tempSlider.value) !== state.indoorTargetC) tempSlider.value = String(state.indoorTargetC);
    if (tempOut) tempOut.textContent = `${hl.indoor_target_c.toFixed(1)} °C`;

    document.querySelectorAll("[data-hl-preset]").forEach((btn) => {
      btn.classList.toggle("is-active", Number(btn.dataset.hlPreset) === state.heatTargetPct);
    });

    const hlSrvSummary = document.getElementById("hl-server-summary");
    if (hlSrvSummary) {
      hlSrvSummary.textContent = `${hl.active_servers} of ${hl.installed_servers} servers heating (${mdl.name})`;
    }
    const hlRemBtn = document.getElementById("hl-rem-server-btn");
    const hlAddBtn = document.getElementById("hl-add-server-btn");
    const siteMaxU = Number(latestQuote.sizing?.site_max_units || getSiteMaxUnits(dep.id, mdl.id));
    if (hlRemBtn) hlRemBtn.disabled = hl.installed_servers <= 1;
    if (hlAddBtn) hlAddBtn.disabled = hl.installed_servers >= siteMaxU;

    if (hl3dBadge) {
      hl3dBadge.textContent = `${hl.active_servers} of ${hl.installed_servers} Heating · ${hl.live_water_kw} kW (${hl.supply_temp_c} C) · Tank ${hl.tank_soc_pct}%`;
    }

    // Unified Tomorrow (per-day) Cost & Thermal Ledger - explicit /day + annual equivalent
    const kpiEl = document.getElementById("hl-live-kpis");
    if (kpiEl) {
      const annualSavings = latestQuote.host_ledger?.heating_bill_savings_chf ?? 0;
      const baseDay = hostTom ? hostTom.baseline_heating_bill_chf : 0;
      const backupDay = hostTom ? hostTom.backup_heater_cost_chf : 0;
      const saveDay = hostTom ? hostTom.heating_bill_savings_chf : hl.tomorrow_heat_credit_chf;
      const opDay = opTom?.calorgrid_operating_cost_chf ?? hl.tomorrow_elec_chf;
      kpiEl.replaceChildren(
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Heating bill saved (tomorrow)" }),
          domEl("strong", { className: "accent-hosted", text: `+CHF ${saveDay.toFixed(2)}/day` }),
          domEl("small", { text: `~CHF ${Math.round(annualSavings).toLocaleString()}/yr annual savings` }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Backup heater bill (tomorrow)" }),
          domEl("strong", { text: `CHF ${backupDay.toFixed(2)}/day` }),
          domEl("small", { text: `Down from CHF ${baseDay.toFixed(2)}/day baseline` }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Live heat output" }),
          domEl("strong", { className: "accent-heat", text: `${hl.live_water_kw.toFixed(1)} kW` }),
          domEl("small", { text: `${hl.supply_temp_c}°C supply, tank ${hl.tank_soc_pct}%` }),
        ]),
        domEl("div", { className: "cg-metric-cell" }, [
          domEl("span", { text: "Server power (paid by CalorGrid)" }),
          domEl("strong", { text: `CHF ${opDay.toFixed(2)}/day` }),
          domEl("small", { text: "CHF 0.00 on your electricity bill" }),
        ])
      );
    }

    const hlBreakdownEl = document.getElementById("hl-host-breakdown");
    if (hlBreakdownEl && hostTom) {
      hlBreakdownEl.replaceChildren(...buildHeatSplitBar(hostTom));
    }

    // 24-hour tomorrow forecast bar chart (demand vs server heat)
    const chartEl = document.getElementById("hl-hourly-chart");
    if (chartEl && Array.isArray(hl.hourly)) {
      const maxKw = Math.max(2.0, ...hl.hourly.map((x) => Math.max(x.demand_kw, x.server_heat_kw)));
      const frag = document.createDocumentFragment();
      for (const row of hl.hourly) {
        const dh = Math.max(4, Math.round((row.demand_kw / maxKw) * 64));
        const sh = Math.max(2, Math.round((row.server_heat_kw / maxKw) * 64));
        const col = domEl(
          "div",
          {
            className: "hl-bar-col",
            title: `${String(row.hour).padStart(2, "0")}:00 | ${row.temp_c} C | Demand ${row.demand_kw} kW | CalorGrid ${row.calorgrid_heat_kw ?? row.server_heat_kw} kW | Backup ${row.default_heater_kw ?? 0} kW`,
          },
          [
            domEl("div", { className: "hl-bar-stack" }, [
              domEl("div", { className: "hl-bar-dem", style: { height: `${dh}px` } }),
              domEl("div", { className: "hl-bar-srv", style: { height: `${sh}px` } }),
            ]),
            domEl("span", { text: row.hour % 6 === 0 ? String(row.hour).padStart(2, "0") : "" }),
          ]
        );
        frag.appendChild(col);
      }
      chartEl.replaceChildren(frag);
    }

    // Live Running Processes & Nested GPU Slots List
    const procEl = document.getElementById("hl-process-list");
    if (procEl) {
      const frag = document.createDocumentFragment();
      for (const p of hl.processes || []) {
        const cls =
          p.status === "RUNNING"
            ? "is-run"
            : p.status === "FIBER_OFFLOAD"
              ? "is-offload"
              : "is-stop";
        const lbl =
          p.status === "RUNNING"
            ? `${p.cpu_pct ?? 0}% load · ${p.heat_kw} kW heat`
            : p.status === "FIBER_OFFLOAD"
              ? "Shared over fiber"
              : "Standby";

        const srcBadge = domEl("span", {
          className: `hl-src-badge ${p.source === "live_pid" ? "is-live-pid" : "is-sim"}`,
          text: p.source === "live_pid" ? "Live PID" : "Simulated",
        });

        const pctVal = Math.max(0, Math.min(100, Number(p.cpu_pct ?? 0)));
        const rowBox = domEl("div", { className: "hl-proc-card" }, [
          domEl("div", { className: "hl-proc-row" }, [
            domEl("div", { className: "hl-proc-main" }, [
              domEl("strong", {}, [
                `Unit ${p.unit} (${p.process}) `,
                srcBadge,
              ]),
              domEl("span", { text: `${p.workload} · PID ${p.pid}` }),
            ]),
            domEl("span", { className: `hl-proc-pill ${cls}`, text: lbl }),
          ]),
          domEl("div", { className: "hl-gpu-bar hl-unit-bar" }, [
            domEl("div", {
              className: `hl-gpu-bar-fill${p.status !== "RUNNING" ? " is-idle" : ""}`,
              style: { width: `${p.status === "RUNNING" ? pctVal : 6}%` },
            }),
          ]),
        ]);

        const gpuGrid = buildGpuSlotsElement(p.gpu_slots);
        if (gpuGrid) rowBox.appendChild(gpuGrid);
        frag.appendChild(rowBox);
      }
      procEl.replaceChildren(frag);
    }

    if (activePane === "heatledger") {
      const loadRatio = hl.installed_servers > 0 ? hl.active_servers / hl.installed_servers : 0.0;
      hlStudio.syncConfig({
        modelId: mdl.id,
        count: hl.installed_servers,
        activeCount: hl.active_servers,
        tankSocPct: hl.tank_soc_pct,
        loadRatio,
        roomStyle: DEPLOYMENT_TO_ROOM[state.deploymentId] || "basement",
      });
    }
  }

  async function openConfigurator(viewMode = "basement") {
    activePane = "configure";
    hlOverlay.hidden = true;
    hlStudio.stop();
    if (hlPollTimer) {
      clearInterval(hlPollTimer);
      hlPollTimer = null;
    }
    cfgOverlay.hidden = false;
    await refreshQuote();
    setCfgViewMode(viewMode);
  }

  async function openHeatLedger() {
    activePane = "heatledger";
    cfgOverlay.hidden = true;
    cfgStudio.stop();
    if (cfgExtVideo) cfgExtVideo.pause();
    hlOverlay.hidden = false;
    hlStudio.start();
    await refreshQuote();
    await Promise.all([refreshSiteTelemetry(), refreshAppLogs()]);
    if (hlPollTimer) clearInterval(hlPollTimer);
    hlPollTimer = setInterval(() => {
      if (activePane !== "heatledger") return;
      refreshSiteTelemetry().catch(() => {});
      refreshAppLogs().catch(() => {});
    }, 5000);
  }

  function closeAll() {
    activePane = null;
    cfgOverlay.hidden = true;
    hlOverlay.hidden = true;
    cfgStudio.stop();
    hlStudio.stop();
    if (cfgExtVideo) cfgExtVideo.pause();
    if (hlPollTimer) {
      clearInterval(hlPollTimer);
      hlPollTimer = null;
    }
    quoteAbort?.abort();
    telemetryAbort?.abort();
    logsAbort?.abort();
  }

  const NODE_TO_ARCHETYPE = {
    basel_klingental: "house_basel",
    riehen_villa: "house_borehole",
    basel_erdsonde: "house_borehole",
    basel_st_jakob: "municipal_pool",
    zurich_oerlikon: "house_basel",
    bern_matte: "house_basel",
    emmental_dairy: "dairy_farm",
    thurgau_greenhouse: "glasshouse_farm",
    geneva_carouge: "house_basel",
    valais_dryer: "dairy_farm",
    lucerne_bakery: "commercial_bakery",
    stgallen_hospital: "hospital_wing",
    davos_alpine: "alpine_chalet",
    lugano_lakeside: "house_basel",
  };

  async function prefillFromNode(nodeId, targetTab = "configure") {
    await ensureCatalog();
    const mappedId = NODE_TO_ARCHETYPE[nodeId];
    const match =
      catalog.deployments.find((d) => d.preview_node_id === nodeId) ||
      catalog.deployments.find((d) => d.id === mappedId) ||
      catalog.deployments[0];
    if (match) {
      state.deploymentId = match.id;
      state.serverModelId = match.default_model_id;
      state.serverCount = match.default_count;
    }
    if (onOpenTab) await onOpenTab(targetTab);
    else if (targetTab === "heatledger") await openHeatLedger();
    else await openConfigurator("basement");
  }

  // Wire Configurator Controls
  document.getElementById("cfg-deployment-select")?.addEventListener("change", async (e) => {
    state.deploymentId = e.target.value;
    const dep = catalog?.deployments.find((d) => d.id === state.deploymentId);
    if (dep) {
      state.serverModelId = dep.default_model_id;
      state.serverCount = dep.default_count;
    }
    await refreshQuote();
  });

  document.getElementById("cfg-heater-select")?.addEventListener("change", async (e) => {
    state.heaterType = e.target.value;
    const hlH = document.getElementById("hl-heater-select");
    if (hlH) hlH.value = state.heaterType;
    await refreshQuote();
  });

  document.getElementById("hl-heater-select")?.addEventListener("change", async (e) => {
    state.heaterType = e.target.value;
    const cfgH = document.getElementById("cfg-heater-select");
    if (cfgH) cfgH.value = state.heaterType;
    await refreshQuote();
  });

  document.getElementById("cfg-mode-exterior")?.addEventListener("click", () => setCfgViewMode("exterior"));
  document.getElementById("cfg-mode-basement")?.addEventListener("click", () => setCfgViewMode("basement"));
  document.getElementById("cfg-enter-basement-btn")?.addEventListener("click", () => setCfgViewMode("basement"));
  document.getElementById("cfg-pip-btn")?.addEventListener("click", () => setCfgViewMode("exterior"));
  document.getElementById("cfg-reset-3d-btn")?.addEventListener("click", () => cfgStudio.resetCamera());
  document.getElementById("hl-reset-3d-btn")?.addEventListener("click", () => hlStudio.resetCamera());

  document.getElementById("cfg-count-minus")?.addEventListener("click", async () => {
    if (state.serverCount > 1) {
      state.serverCount -= 1;
      setCfgViewMode("basement");
      await refreshQuote();
    }
  });
  document.getElementById("cfg-count-plus")?.addEventListener("click", async () => {
    const maxU = getSiteMaxUnits(state.deploymentId, state.serverModelId);
    if (state.serverCount < maxU) {
      state.serverCount += 1;
      setCfgViewMode("basement");
      await refreshQuote();
    }
  });

  document.getElementById("cfg-test-heatledger-btn")?.addEventListener("click", () => {
    if (onOpenTab) onOpenTab("heatledger");
    else openHeatLedger();
  });

  document.getElementById("cfg-existing-boiler-toggle")?.addEventListener("change", async (e) => {
    state.hasExistingBoiler = Boolean(e.target.checked);
    await refreshQuote();
  });

  document.getElementById("cfg-reserve-btn")?.addEventListener("click", () => {
    const toast = document.getElementById("cfg-reserve-summary");
    if (!toast || !latestQuote) return;
    const dep = latestQuote.deployment;
    const mdl = latestQuote.server_model;
    const fin = latestQuote.financials;
    const boilerTxt = fin.has_existing_boiler
      ? "Existing heater kept (CHF 0)"
      : `Subsidized boiler CHF ${Math.round(fin.boiler_cost_chf).toLocaleString()} (was CHF ${Math.round(fin.boiler_retail_chf).toLocaleString()})`;
    toast.hidden = false;
    toast.replaceChildren(
      domEl("strong", {
        text: `Host Estimate Saved: ${latestQuote.server_count}x ${mdl.name} (CHF 0 servers, electricity paid by CalorGrid)`,
      }),
      domEl("span", {
        text: `Site: ${dep.label} · ${boilerTxt} + CHF ${Math.round(fin.tie_in_chf).toLocaleString()} tie-in = CHF ${Math.round(fin.host_upfront_chf).toLocaleString()} · Saves +CHF ${Math.round(fin.host_annual_heat_savings_chf).toLocaleString()}/yr on heating bill.`,
      })
    );
  });

  // Wire HeatLedger Controls (150ms debounce + AbortController)
  document.getElementById("hl-deployment-select")?.addEventListener("change", async (e) => {
    state.deploymentId = e.target.value;
    const dep = catalog?.deployments.find((d) => d.id === state.deploymentId);
    if (dep) {
      state.serverModelId = dep.default_model_id;
      state.serverCount = dep.default_count;
    }
    await refreshQuote();
  });

  let sliderDebounce = null;
  function scheduleQuoteRefresh() {
    if (sliderDebounce) clearTimeout(sliderDebounce);
    sliderDebounce = setTimeout(() => {
      refreshQuote().catch(() => {});
    }, 150);
  }

  document.getElementById("hl-heat-slider")?.addEventListener("input", (e) => {
    state.heatTargetPct = Number(e.target.value);
    const out = document.getElementById("hl-heat-readout");
    if (out && latestQuote?.heatledger_tomorrow) {
      const estKwh = ((latestQuote.heatledger_tomorrow.recommended_kwh * state.heatTargetPct) / 100).toFixed(1);
      out.textContent = `${estKwh} kWh (${state.heatTargetPct}%)`;
    }
    scheduleQuoteRefresh();
  });
  document.getElementById("hl-indoor-slider")?.addEventListener("input", (e) => {
    state.indoorTargetC = Number(e.target.value);
    const out = document.getElementById("hl-indoor-readout");
    if (out) out.textContent = `${state.indoorTargetC.toFixed(1)} °C`;
    scheduleQuoteRefresh();
  });

  // Wire HeatLedger right-panel sub-tabs (Overview / Servers & GPUs / Logs)
  const hlSubtabBtns = Array.from(document.querySelectorAll("[data-hl-subtab]"));
  const hlSubtabPanes = Array.from(document.querySelectorAll("[data-hl-pane]"));
  function setHlSubtab(tab) {
    hlSubtabBtns.forEach((b) => {
      const act = b.dataset.hlSubtab === tab;
      b.classList.toggle("is-active", act);
      b.setAttribute("aria-selected", act ? "true" : "false");
    });
    hlSubtabPanes.forEach((p) => {
      p.hidden = p.dataset.hlPane !== tab;
    });
    if (tab === "servers") refreshSiteTelemetry().catch(() => {});
    if (tab === "logs") refreshAppLogs().catch(() => {});
  }
  hlSubtabBtns.forEach((b) => {
    b.addEventListener("click", () => setHlSubtab(b.dataset.hlSubtab));
  });
  document.querySelectorAll("[data-hl-preset]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.heatTargetPct = Number(btn.dataset.hlPreset);
      await refreshQuote();
    });
  });

  document.getElementById("hl-add-server-btn")?.addEventListener("click", async () => {
    const maxU = getSiteMaxUnits(state.deploymentId, state.serverModelId);
    if (state.serverCount < maxU) {
      state.serverCount += 1;
      await refreshQuote();
    }
  });
  document.getElementById("hl-rem-server-btn")?.addEventListener("click", async () => {
    if (state.serverCount > 1) {
      state.serverCount -= 1;
      await refreshQuote();
    }
  });

  const realWorkerBtn = document.getElementById("hl-real-worker-btn");
  const realWorkerStatus = document.getElementById("hl-real-worker-status");
  let liveWorkerRunning = false;
  realWorkerBtn?.addEventListener("click", async () => {
    try {
      if (!liveWorkerRunning) {
        const r = await fetch("/api/v1/worker/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_type: "archive_ocr", continuous: true }),
        });
        const d = await r.json();
        liveWorkerRunning = true;
        realWorkerBtn.textContent = "Send SIGSTOP Pause";
        if (realWorkerStatus) realWorkerStatus.textContent = `Live Linux PID ${d.pid} (${d.state})`;
      } else {
        const r = await fetch("/api/v1/worker/pause", { method: "POST" });
        const d = await r.json();
        liveWorkerRunning = false;
        realWorkerBtn.textContent = "Run Live Linux OCR";
        if (realWorkerStatus) realWorkerStatus.textContent = `PID ${d.pid} -> ${d.state} (SIGSTOP)`;
      }
      await refreshQuote();
    } catch (err) {
      if (realWorkerStatus) realWorkerStatus.textContent = `Worker check: ${err.message}`;
    }
  });

  return {
    openConfigurator,
    openHeatLedger,
    closeAll,
    isOpen: () => activePane !== null,
    getActivePane: () => activePane,
    prefillFromNode,
    setCfgViewMode,
    getState: () => ({ ...state }),
  };
}
