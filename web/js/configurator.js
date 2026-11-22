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
  // Mid-dark architectural studio dome (matches Omniverse / SimReady Central viewport contrast)
  // so brushed black aluminum stays deep obsidian & NVIDIA GB300 copper stays rich saturated bronze
  envScene.background = new THREE.Color(0x465160);
  const floorBounce = new THREE.Mesh(
    new THREE.PlaneGeometry(36, 36),
    new THREE.MeshBasicMaterial({ color: 0x2a313c, side: THREE.DoubleSide })
  );
  floorBounce.rotation.x = Math.PI / 2;
  floorBounce.position.y = -8;
  envScene.add(floorBounce);
  const topPanel = new THREE.Mesh(
    new THREE.PlaneGeometry(20, 14),
    new THREE.MeshBasicMaterial({ color: 0xf8fafc, side: THREE.DoubleSide })
  );
  topPanel.rotation.x = Math.PI / 2;
  topPanel.position.y = 11;
  envScene.add(topPanel);
  const warmKey = new THREE.Mesh(
    new THREE.PlaneGeometry(10, 8),
    new THREE.MeshBasicMaterial({ color: 0xfff3de, side: THREE.DoubleSide })
  );
  warmKey.position.set(11, 6.5, 10);
  warmKey.lookAt(0, 0, 0);
  envScene.add(warmKey);
  const coolFill = new THREE.Mesh(
    new THREE.PlaneGeometry(9, 7),
    new THREE.MeshBasicMaterial({ color: 0xd0e2f8, side: THREE.DoubleSide })
  );
  coolFill.position.set(-11, 5.0, 9);
  coolFill.lookAt(0, 0, 0);
  envScene.add(coolFill);
  // Crisp narrow studio rim strips for gleaming specular highlights along metallic rack bevels
  const specMat = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide });
  for (const [sx, sy, sz] of [[7.5, 4.5, 13], [-7.5, 5.0, 13]]) {
    const strip = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 8.5), specMat);
    strip.position.set(sx, sy, sz);
    strip.lookAt(0, 1.0, 0);
    envScene.add(strip);
  }
  const tex = pmrem.fromScene(envScene, 0.036).texture;
  pmrem.dispose();
  return tex;
}

let cachedShadowTex = null;
function getContactShadowTexture() {
  if (cachedShadowTex) return cachedShadowTex;
  const c = document.createElement("canvas");
  c.width = 128;
  c.height = 128;
  const ctx = c.getContext("2d");
  const grad = ctx.createRadialGradient(64, 64, 10, 64, 64, 62);
  grad.addColorStop(0, "rgba(15, 23, 42, 0.44)");
  grad.addColorStop(0.58, "rgba(15, 23, 42, 0.22)");
  grad.addColorStop(1, "rgba(15, 23, 42, 0.0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 128, 128);
  cachedShadowTex = new THREE.CanvasTexture(c);
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

function enhanceHardwareMaterials(root, key = "") {
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    obj.userData.sharedTemplate = true;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if (key === "gb300") {
        // Rich dark anodized NVIDIA chassis + gleaming saturated rose-copper 18-tray compute stack + vivid green LEDs
        mat.color = new THREE.Color(0xdee2e8);
        mat.envMapIntensity = 0.96;
        if (mat.emissive && (mat.emissive.r + mat.emissive.g + mat.emissive.b) > 0.05) {
          mat.color = new THREE.Color(0xffffff);
          mat.emissiveIntensity = 1.80;
        }
        mat.needsUpdate = true;
        continue;
      }
      if (key === "ocpPop" || key === "vr3307") {
        // Authentic Vertiv dark steel cabinet + brushed stainless OCP shelves + vivid green hot-swap handles + crisp white Vertiv badge
        mat.color = new THREE.Color(key === "ocpPop" ? 0xd4dae2 : 0xc4ccd6);
        mat.envMapIntensity = 0.88;
        mat.needsUpdate = true;
        continue;
      }
      const c = mat.color;
      const isVeryDark = c && c.r < 0.04 && c.g < 0.04 && c.b < 0.04;
      if (mat.name === "vertiv_black" || isVeryDark) {
        // Sleek brushed anodized charcoal (zero flat blue wash)
        mat.color = new THREE.Color(0x1b2028);
        mat.metalness = 0.66;
        mat.roughness = 0.30;
        mat.emissive = new THREE.Color(0x000000);
        mat.emissiveIntensity = 0.0;
        mat.envMapIntensity = 1.05;
        mat.needsUpdate = true;
      } else if (key === "tank") {
        // Brushed stainless steel pressure vessel with distinct metallic reflection bands
        mat.color = new THREE.Color(0x8c9aa8);
        mat.metalness = 0.78;
        mat.roughness = 0.28;
        mat.envMapIntensity = 1.05;
        mat.needsUpdate = true;
      } else if (mat.metalness !== undefined && mat.metalness > 0.7) {
        mat.roughness = Math.max(0.20, Math.min(0.42, mat.roughness ?? 0.28));
        mat.envMapIntensity = 1.05;
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
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, len, 12), mat);
  mesh.position.copy(v1).addScaledVector(dir, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  return mesh;
}

function makeFlangeCollar(point, towardPoint, radius, mat) {
  const v1 = new THREE.Vector3(...point);
  const v2 = new THREE.Vector3(...towardPoint);
  const dir = v2.sub(v1);
  if (dir.lengthSq() < 1e-6) return null;
  const collar = new THREE.Mesh(
    new THREE.CylinderGeometry(radius * 1.65, radius * 1.65, 0.048, 14),
    mat
  );
  collar.position.copy(v1);
  collar.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  return collar;
}

function buildOrthogonalPipe(parent, waypoints, radius, pipeMat, flangeMat = null, flangeEnds = [false, false]) {
  const curvePath = new THREE.CurvePath();
  if (!waypoints || waypoints.length < 2) return curvePath;
  const elbowGeo = new THREE.SphereGeometry(radius * 1.12, 10, 8);
  for (let i = 0; i < waypoints.length - 1; i++) {
    const a = waypoints[i];
    const b = waypoints[i + 1];
    const seg = makePipeMesh(a, b, radius, pipeMat);
    if (seg) parent.add(seg);
    curvePath.add(new THREE.LineCurve3(new THREE.Vector3(...a), new THREE.Vector3(...b)));
    if (i > 0) {
      const elbow = new THREE.Mesh(elbowGeo, pipeMat);
      elbow.position.set(a[0], a[1], a[2]);
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

const ROOM_STYLES = {
  basement: {
    bg: 0x3b424d,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0x5c6470,
    beam: 0x454d58,
    accent: 0x475569,
    accentY: 0.07,
    accentH: 0.14,
    hemiTop: 0xf1f5f9,
    hemiBot: 0x475569,
    keyHex: 0xfff8ed,
    label: "Residential Basement",
  },
  farm_barn: {
    bg: 0x4a392a,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0x6b4c32,
    beam: 0x4a311e,
    accent: 0x7c5330,
    accentY: 1.48,
    accentH: 0.14,
    hemiTop: 0xfff4e0,
    hemiBot: 0x5c4632,
    keyHex: 0xffe8c2,
    label: "Dairy Utility Room",
  },
  greenhouse: {
    bg: 0x466659,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0xb9e3cf,
    beam: 0x14532d,
    accent: 0x15803d,
    accentY: 1.65,
    accentH: 0.10,
    hemiTop: 0xf0fdf4,
    hemiBot: 0x4d6b57,
    keyHex: 0xfffbeb,
    label: "Glasshouse Boiler Bay",
  },
  pool_plant: {
    bg: 0x2d4a5e,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0x94a9ba,
    beam: 0x64748b,
    accent: 0x0284c7,
    accentY: 1.35,
    accentH: 0.22,
    hemiTop: 0xe0f2fe,
    hemiBot: 0x38586e,
    keyHex: 0xe8f6ff,
    label: "Aquatic Plant Room",
  },
  commercial: {
    bg: 0x47525e,
    floor: 0xffffff,
    wall: 0xffffff,
    ceiling: 0x7a8594,
    beam: 0x475569,
    accent: 0x64748b,
    accentY: 1.15,
    accentH: 0.14,
    hemiTop: 0xf8fafc,
    hemiBot: 0x4b5563,
    keyHex: 0xf8fafc,
    label: "Commercial Plant Bay",
  },
};

const _ROOM_TEX_CACHE = {};

function makeCanvasTex(drawFn, repeatX, repeatY) {
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 512;
  const ctx = c.getContext("2d");
  drawFn(ctx, 512, 512);
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeatX, repeatY);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

function getRoomTextures(styleKey) {
  if (_ROOM_TEX_CACHE[styleKey]) return _ROOM_TEX_CACHE[styleKey];
  let wallTex, floorTex;
  if (styleKey === "basement") {
    wallTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#9aa2ac";
      ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 4000; i++) {
        const g = 132 + ((i * 37) % 36);
        ctx.fillStyle = `rgba(${g},${g + 2},${g + 5},0.22)`;
        ctx.fillRect((i * 73) % w, (i * 139) % h, 5, 3);
      }
      for (let y = 0; y < h; y += 64) {
        ctx.fillStyle = "rgba(42,48,56,0.45)";
        ctx.fillRect(0, y, w, 3);
        ctx.fillStyle = "rgba(220,226,232,0.22)";
        ctx.fillRect(0, y + 3, w, 2);
        for (let x = 64; x < w; x += 128) {
          ctx.beginPath();
          ctx.arc(x, y + 32, 5.5, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(38,44,52,0.55)";
          ctx.fill();
        }
      }
    }, 3.6, 1.3);
    floorTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#5c6470";
      ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 3200; i++) {
        const v = 72 + ((i * 29) % 44);
        ctx.fillStyle = `rgba(${v},${v + 4},${v + 8},0.25)`;
        ctx.fillRect((i * 97) % w, (i * 191) % h, 4, 4);
      }
      ctx.strokeStyle = "rgba(30,36,44,0.42)";
      ctx.lineWidth = 3;
      for (let k = 0; k <= w; k += 128) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
      }
    }, 4.5, 3.2);
  } else if (styleKey === "farm_barn") {
    wallTex = makeCanvasTex((ctx, w, h) => {
      const tones = ["#b38252", "#a87646", "#bd8c5a", "#9f6e3f", "#b88756", "#a47447", "#c0905e", "#ab7a4c"];
      for (let r = 0; r < 8; r++) {
        const y0 = r * 64;
        ctx.fillStyle = tones[r % tones.length];
        ctx.fillRect(0, y0, w, 64);
        for (let g = 0; g < 16; g++) {
          ctx.fillStyle = g % 2 === 0 ? "rgba(70,40,16,0.12)" : "rgba(255,228,185,0.09)";
          ctx.fillRect(0, y0 + g * 4, w, 2);
        }
        ctx.fillStyle = "rgba(56,34,14,0.62)";
        ctx.fillRect(0, y0, w, 3);
      }
    }, 3.4, 1.35);
    floorTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#7a6e60";
      ctx.fillRect(0, 0, w, h);
      for (let r = 0; r < 4; r++) {
        for (let c = 0; c < 4; c++) {
          const shade = 108 + ((r * 7 + c * 13) % 24);
          ctx.fillStyle = `rgb(${shade + 12},${shade + 4},${shade - 6})`;
          ctx.fillRect(c * 128 + 3, r * 128 + 3, 122, 122);
        }
      }
      ctx.strokeStyle = "#463e36";
      ctx.lineWidth = 4;
      for (let k = 0; k <= w; k += 128) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
      }
    }, 4.2, 3.0);
  } else if (styleKey === "greenhouse") {
    wallTex = makeCanvasTex((ctx, w, h) => {
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, "#cdece0");
      grad.addColorStop(0.65, "#dcf4e8");
      grad.addColorStop(1, "#a6cbb9");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = "#166534";
      ctx.lineWidth = 8;
      for (let x = 0; x <= w; x += 128) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      }
      for (let y = 0; y <= h; y += 170) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      }
    }, 3.2, 1.15);
    floorTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#6e7d74";
      ctx.fillRect(0, 0, w, h);
      for (let r = 0; r < 4; r++) {
        for (let c = 0; c < 4; c++) {
          const v = (r + c) % 2 === 0 ? "#859184" : "#778578";
          ctx.fillStyle = v;
          ctx.fillRect(c * 128 + 3, r * 128 + 3, 122, 122);
        }
      }
    }, 4.2, 3.0);
  } else if (styleKey === "pool_plant") {
    wallTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#94b5ca";
      ctx.fillRect(0, 0, w, h);
      for (let r = 0; r < 16; r++) {
        for (let c = 0; c < 16; c++) {
          const isBand = r >= 9 && r <= 12;
          ctx.fillStyle = isBand
            ? ((r + c) % 2 === 0 ? "#0284c7" : "#0ea5e9")
            : ((r + c) % 2 === 0 ? "#dceef8" : "#d0e7f5");
          ctx.fillRect(c * 32 + 2, r * 32 + 2, 28, 28);
        }
      }
    }, 3.6, 1.3);
    floorTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#485e6e";
      ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = "#314350";
      ctx.lineWidth = 3;
      for (let k = 0; k <= w; k += 64) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
      }
    }, 4.5, 3.2);
  } else {
    wallTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#dbe2ea";
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#94a3b8";
      ctx.fillRect(0, h * 0.58, w, h * 0.42);
      ctx.strokeStyle = "rgba(71,85,105,0.28)";
      ctx.lineWidth = 2;
      for (let k = 0; k <= w; k += 64) {
        ctx.beginPath(); ctx.moveTo(k, 0); ctx.lineTo(k, h * 0.58); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k); ctx.lineTo(w, k); ctx.stroke();
      }
    }, 3.4, 1.25);
    floorTex = makeCanvasTex((ctx, w, h) => {
      ctx.fillStyle = "#687380";
      ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 3600; i++) {
        const c = 80 + ((i * 41) % 60);
        ctx.fillStyle = `rgba(${c},${c + 4},${c + 8},0.28)`;
        ctx.fillRect((i * 83) % w, (i * 157) % h, 3, 3);
      }
    }, 4.4, 3.1);
  }
  _ROOM_TEX_CACHE[styleKey] = { wallTex, floorTex };
  return _ROOM_TEX_CACHE[styleKey];
}

const DEPLOYMENT_TO_ROOM = {
  house_basel: "basement",
  house_borehole: "basement",
  alpine_chalet: "pool_plant",
  dairy_farm: "farm_barn",
  glasshouse_farm: "greenhouse",
  municipal_pool: "pool_plant",
  commercial_bakery: "commercial",
  hospital_wing: "commercial",
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
    this.scene.background = new THREE.Color(0xeef3f8);

    const w = Math.max(300, this.hostEl.clientWidth || 720);
    const h = Math.max(200, this.hostEl.clientHeight || 420);
    this.camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 80);
    this.camera.position.set(1.25, 2.42, 6.45);

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.06;
    this.renderer.setSize(w, h, false);

    // Architectural studio PBR reflections for stainless steel buffer tank, NVIDIA GB300 copper & Vertiv racks
    this.scene.environment = buildStudioEnvironment(this.renderer);
    this.scene.environmentIntensity = 1.12;

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.target.set(0.45, 1.08, -1.05);
    this.controls.minDistance = 2.6;
    this.controls.maxDistance = 12.0;
    this.controls.maxPolarAngle = Math.PI * 0.48;
    this.controls.update();

    // Architectural plant-room directional + hemisphere lighting (adapted per room environment)
    this.hemiLight = new THREE.HemisphereLight(0xf1f5f9, 0x475569, 1.05);
    this.scene.add(this.hemiLight);
    this.keyLight = new THREE.DirectionalLight(0xfff8ed, 1.50);
    this.keyLight.position.set(4.2, 6.2, 5.8);
    this.scene.add(this.keyLight);
    this.fillLight = new THREE.DirectionalLight(0xdbeafe, 0.72);
    this.fillLight.position.set(-4.8, 3.8, 4.5);
    this.scene.add(this.fillLight);

    // Textured architectural floor, walls, ceiling slab, structural ceiling beams & recessed LED fixtures
    const initTex = getRoomTextures("basement");
    this.floorMat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: initTex.floorTex,
      roughness: 0.56,
      metalness: 0.08,
    });
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(12.5, 9.0), this.floorMat);
    floor.rotation.x = -Math.PI / 2;
    this.scene.add(floor);

    this.wallMat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: initTex.wallTex,
      roughness: 0.78,
      metalness: 0.04,
    });
    const backWall = new THREE.Mesh(new THREE.BoxGeometry(10.8, 3.24, 0.14), this.wallMat);
    backWall.position.set(0.1, 1.62, -2.55);
    this.scene.add(backWall);

    const leftWall = new THREE.Mesh(new THREE.BoxGeometry(0.14, 3.24, 6.4), this.wallMat);
    leftWall.position.set(-5.25, 1.62, 0.6);
    this.scene.add(leftWall);

    const rightWall = new THREE.Mesh(new THREE.BoxGeometry(0.14, 3.24, 6.4), this.wallMat);
    rightWall.position.set(5.45, 1.62, 0.6);
    this.scene.add(rightWall);

    this.ceilingMat = new THREE.MeshStandardMaterial({ color: 0x5c6470, roughness: 0.82, metalness: 0.05 });
    const ceiling = new THREE.Mesh(new THREE.BoxGeometry(10.8, 0.12, 6.4), this.ceilingMat);
    ceiling.position.set(0.1, 3.20, 0.6);
    this.scene.add(ceiling);

    this.beamMat = new THREE.MeshStandardMaterial({ color: 0x454d58, roughness: 0.68, metalness: 0.14 });
    for (const bx of [-3.2, -1.0, 1.2, 3.4]) {
      const beam = new THREE.Mesh(new THREE.BoxGeometry(0.20, 0.22, 6.2), this.beamMat);
      beam.position.set(bx, 3.04, 0.5);
      this.scene.add(beam);
    }

    // Recessed architectural LED light troughs along ceiling
    const lampFrameMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.4, metalness: 0.7 });
    const lampGlowMat = new THREE.MeshBasicMaterial({ color: 0xfffef8 });
    for (const lx of [-2.1, 0.1, 2.3]) {
      const frame = new THREE.Mesh(new THREE.BoxGeometry(1.25, 0.05, 0.34), lampFrameMat);
      frame.position.set(lx, 3.12, -0.75);
      this.scene.add(frame);
      const glow = new THREE.Mesh(new THREE.BoxGeometry(1.14, 0.02, 0.24), lampGlowMat);
      glow.position.set(lx, 3.09, -0.75);
      this.scene.add(glow);
    }

    // Architectural wall baseboard + rail / dado band reflecting room type
    const baseMat = new THREE.MeshStandardMaterial({ color: 0x26303d, roughness: 0.55, metalness: 0.25 });
    const baseboard = new THREE.Mesh(new THREE.BoxGeometry(10.8, 0.12, 0.18), baseMat);
    baseboard.position.set(0.1, 0.06, -2.50);
    this.scene.add(baseboard);

    this.accentMat = new THREE.MeshStandardMaterial({ color: 0x475569, roughness: 0.55 });
    this.accentBand = new THREE.Mesh(new THREE.BoxGeometry(10.8, 0.14, 0.18), this.accentMat);
    this.accentBand.position.set(0.1, 0.07, -2.50);
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
      color: 0xd4a24c,
      roughness: 0.28,
      metalness: 0.82,
    });
    const darkFrameMat = new THREE.MeshStandardMaterial({
      color: 0x334155,
      roughness: 0.42,
      metalness: 0.65,
    });
    this.brassMat = brassMat;

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
    const cduShadow = makeContactShadow(1.38, 1.48);
    cduShadow.position.set(1.42, 0.004, -1.42);
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
    const tankShadow = makeContactShadow(1.56, 1.44);
    tankShadow.position.set(3.06, 0.004, -1.42);
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
    this.staticGroup.add(sightBezel);
    for (const capY of [sightMinY - 0.02, sightMaxY + 0.02]) {
      const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.036, 0.036, 0.03, 12), brassMat);
      cap.position.set(sightX, capY, sightZ + 0.01);
      this.staticGroup.add(cap);
    }

    this.tankHotBar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.026, 0.026, 1.0, 12),
      new THREE.MeshStandardMaterial({ color: 0xf97316, emissive: 0xea580c, emissiveIntensity: 0.72, roughness: 0.22 })
    );
    this.tankHotBar.position.set(sightX, sightMinY + sightH * 0.7, sightZ + 0.01);
    this.staticGroup.add(this.tankHotBar);

    this.tankColdBar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.026, 0.026, 1.0, 12),
      new THREE.MeshStandardMaterial({ color: 0x0284c7, emissive: 0x0ea5e9, emissiveIntensity: 0.52, roughness: 0.22 })
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

    // Vertiv Power Distribution Panel mounted on far-left wall
    if (this.templates.panel) {
      const panel = this.templates.panel.clone(true);
      panel.position.set(-4.55, 0.0, -1.55);
      panel.scale.setScalar(0.86);
      this.staticGroup.add(panel);
    }

    // SimReady ceiling LED fixtures & wall safety extinguisher for plant-room realism
    if (this.templates.ledPanel) {
      for (const lx of [-1.8, 1.8]) {
        const lp = this.templates.ledPanel.clone(true);
        lp.position.set(lx, 3.04, -0.8);
        lp.scale.set(0.24, 0.06, 0.16);
        this.staticGroup.add(lp);
      }
    }
    if (this.templates.extinguisher) {
      const ext = this.templates.extinguisher.clone(true);
      ext.position.set(-4.85, 0.0, 0.45);
      ext.scale.setScalar(1.15);
      this.staticGroup.add(ext);
    }

    // Fixed SECONDARY building heating water loop (isolated from servers by the Liebert XDU plate heat exchanger)
    const waterHotMat = new THREE.MeshStandardMaterial({
      color: 0xea580c,
      emissive: 0xc2410c,
      emissiveIntensity: 0.38,
      roughness: 0.28,
      metalness: 0.35,
    });
    const waterColdMat = new THREE.MeshStandardMaterial({
      color: 0x0284c7,
      emissive: 0x0369a1,
      emissiveIntensity: 0.30,
      roughness: 0.28,
      metalness: 0.35,
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

    // 3. Top of Tank -> straight UP to ceiling slab flange (hot building heating riser, ZERO air gap!)
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
    // Seated brass bulkhead nozzle right on the top surface of the tank
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
  }

  buildRoomFurniture(roomStyle) {
    if (!this.furnitureGroup) return;
    while (this.furnitureGroup.children.length > 0) {
      const ch = this.furnitureGroup.children.pop();
      ch.traverse((o) => {
        if (o.isMesh) {
          if (o.geometry) o.geometry.dispose();
          if (o.material) {
            if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
            else o.material.dispose();
          }
        }
      });
    }
    this.currentRoomStyle = roomStyle;

    const woodMat = new THREE.MeshStandardMaterial({ color: 0xba8b57, roughness: 0.72, metalness: 0.05 });
    const darkSteel = new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.42, metalness: 0.65 });
    const brushedSteel = new THREE.MeshStandardMaterial({ color: 0xcbd5e1, roughness: 0.30, metalness: 0.78 });
    const whiteEnamel = new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.28, metalness: 0.12 });
    const branchHotMat = new THREE.MeshStandardMaterial({
      color: 0xea580c,
      emissive: 0xc2410c,
      emissiveIntensity: 0.34,
      roughness: 0.3,
      metalness: 0.35,
    });
    const rx = this.secondaryPaths?.riserHotX ?? 2.84;
    const rz = this.secondaryPaths?.tankMidZ ?? -1.42;

    if (roomStyle === "basement") {
      // Residential Basement: Wall hydronic radiator connected to the hot riser + washing machine & oak workbench
      for (let f = 0; f < 10; f++) {
        const fin = new THREE.Mesh(new THREE.BoxGeometry(0.055, 0.72, 0.11), whiteEnamel);
        fin.position.set(3.88 + f * 0.095, 0.68, -2.38);
        this.furnitureGroup.add(fin);
      }
      // Copper/hot tap-off line connecting the buffer tank hot riser straight to the basement radiator!
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [rx, 1.56, -2.36],
          [3.88, 1.56, -2.36],
          [3.88, 0.98, -2.36],
        ],
        0.022,
        branchHotMat,
        this.brassMat,
        [true, true]
      );

      // Front-load Swiss washing machine visible on back-left
      const washer = new THREE.Mesh(new THREE.BoxGeometry(0.64, 0.88, 0.64), whiteEnamel);
      washer.position.set(-4.05, 0.44, -1.65);
      this.furnitureGroup.add(washer);
      const drumRing = new THREE.Mesh(new THREE.TorusGeometry(0.18, 0.028, 12, 24), brushedSteel);
      drumRing.position.set(-4.05, 0.42, -1.32);
      this.furnitureGroup.add(drumRing);
      const drumGlass = new THREE.Mesh(
        new THREE.CircleGeometry(0.16, 20),
        new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.15, metalness: 0.5 })
      );
      drumGlass.position.set(-4.05, 0.42, -1.318);
      this.furnitureGroup.add(drumGlass);

      // Oak workbench + red toolbox along left wall
      const benchTop = new THREE.Mesh(new THREE.BoxGeometry(0.76, 0.06, 1.55), woodMat);
      benchTop.position.set(-4.55, 0.90, 0.55);
      this.furnitureGroup.add(benchTop);
      for (const dz of [-0.66, 0.66]) {
        for (const dx of [-0.30, 0.30]) {
          const leg = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.88, 0.06), darkSteel);
          leg.position.set(-4.55 + dx, 0.44, 0.55 + dz);
          this.furnitureGroup.add(leg);
        }
      }
      const toolbox = new THREE.Mesh(
        new THREE.BoxGeometry(0.32, 0.22, 0.52),
        new THREE.MeshStandardMaterial({ color: 0xdc2626, roughness: 0.35, metalness: 0.45 })
      );
      toolbox.position.set(-4.55, 1.04, 0.65);
      this.furnitureGroup.add(toolbox);
    } else if (roomStyle === "farm_barn") {
      // Emmental Dairy: Stainless cheese-pasteurisation vat fed directly by the hot water loop + milk churns + cheese wheels
      const vat = new THREE.Mesh(new THREE.CylinderGeometry(0.64, 0.64, 1.05, 24), brushedSteel);
      vat.position.set(4.32, 0.525, -0.78);
      this.furnitureGroup.add(vat);
      const vatRim = new THREE.Mesh(new THREE.TorusGeometry(0.64, 0.036, 10, 24), darkSteel);
      vatRim.rotation.x = Math.PI / 2;
      vatRim.position.set(4.32, 1.05, -0.78);
      this.furnitureGroup.add(vatRim);
      const milkTop = new THREE.Mesh(
        new THREE.CircleGeometry(0.60, 24),
        new THREE.MeshStandardMaterial({ color: 0xfffbeb, roughness: 0.25 })
      );
      milkTop.rotation.x = -Math.PI / 2;
      milkTop.position.set(4.32, 1.045, -0.78);
      this.furnitureGroup.add(milkTop);

      // Hot-water feed line from buffer tank riser straight into dairy pasteurisation vat jacket
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.32, 1.56, rz],
          [4.32, 1.56, -0.78],
          [4.32, 1.05, -0.78],
        ],
        0.024,
        branchHotMat,
        this.brassMat,
        [true, true]
      );

      // 3 Stainless Milk Churns visible on left-front
      for (const [cx, cz] of [[-3.85, -0.85], [-3.85, -0.35], [-3.40, -0.60]]) {
        const body = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.18, 0.56, 16), brushedSteel);
        body.position.set(cx, 0.28, cz);
        this.furnitureGroup.add(body);
        const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.10, 0.14, 0.14, 16), brushedSteel);
        neck.position.set(cx, 0.62, cz);
        this.furnitureGroup.add(neck);
      }

      // Oak cheese-aging shelves with golden Emmental wheels along back-left wall
      const cheeseMat = new THREE.MeshStandardMaterial({ color: 0xf4c430, roughness: 0.55 });
      for (let s = 0; s < 3; s++) {
        const sy = 0.55 + s * 0.52;
        const plank = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.04, 0.48), woodMat);
        plank.position.set(-3.95, sy, -2.22);
        this.furnitureGroup.add(plank);
        for (let w = 0; w < 3; w++) {
          const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.20, 0.20, 0.13, 18), cheeseMat);
          wheel.position.set(-4.42 + w * 0.47, sy + 0.085, -2.22);
          this.furnitureGroup.add(wheel);
        }
      }
    } else if (roomStyle === "greenhouse") {
      // Thurgau Hydroponic Glasshouse: Conservatory mullions + raised tomato grow bench fed by warm root-zone line
      const mullionMat = new THREE.MeshStandardMaterial({ color: 0x14532d, roughness: 0.4, metalness: 0.5 });
      for (let mx = -4.2; mx <= 4.4; mx += 1.4) {
        const post = new THREE.Mesh(new THREE.BoxGeometry(0.06, 3.1, 0.08), mullionMat);
        post.position.set(mx, 1.55, -2.46);
        this.furnitureGroup.add(post);
      }
      const foliageMat = new THREE.MeshStandardMaterial({ color: 0x16a34a, roughness: 0.72 });
      const tomatoMat = new THREE.MeshStandardMaterial({ color: 0xdc2626, roughness: 0.35 });
      const potMat = new THREE.MeshStandardMaterial({ color: 0xb45309, roughness: 0.78 });
      const tray = new THREE.Mesh(new THREE.BoxGeometry(1.30, 0.10, 2.05), brushedSteel);
      tray.position.set(4.32, 0.78, -0.68);
      this.furnitureGroup.add(tray);
      for (const dz of [-0.90, 0.90]) {
        const bLeg = new THREE.Mesh(new THREE.BoxGeometry(1.20, 0.76, 0.06), darkSteel);
        bLeg.position.set(4.32, 0.38, -0.68 + dz);
        this.furnitureGroup.add(bLeg);
      }
      // Root-zone heating pipe from buffer tank riser right under the hydroponic grow tray!
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.32, 1.56, rz],
          [4.32, 0.70, rz],
          [4.32, 0.70, -0.68],
        ],
        0.022,
        branchHotMat,
        this.brassMat,
        [true, true]
      );
      for (let r = 0; r < 3; r++) {
        for (let c = 0; c < 2; c++) {
          const px = 4.02 + c * 0.60;
          const pz = -1.25 + r * 0.56;
          const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.10, 0.18, 12), potMat);
          pot.position.set(px, 0.92, pz);
          this.furnitureGroup.add(pot);
          const plant = new THREE.Mesh(new THREE.DodecahedronGeometry(0.24, 0), foliageMat);
          plant.position.set(px, 1.24, pz);
          this.furnitureGroup.add(plant);
          const fruit = new THREE.Mesh(new THREE.SphereGeometry(0.048, 10, 8), tomatoMat);
          fruit.position.set(px - 0.14, 1.20, pz + 0.14);
          this.furnitureGroup.add(fruit);
        }
      }
    } else if (roomStyle === "pool_plant") {
      // St. Jakob / Davos Aquatic Plant Room: Illuminated turquoise pool window + twin blue sand filter vessels
      const poolGlass = new THREE.Mesh(
        new THREE.BoxGeometry(3.2, 1.15, 0.08),
        new THREE.MeshStandardMaterial({
          color: 0x38bdf8,
          emissive: 0x0ea5e9,
          emissiveIntensity: 0.45,
          roughness: 0.12,
          metalness: 0.2,
        })
      );
      poolGlass.position.set(3.55, 1.85, -2.44);
      this.furnitureGroup.add(poolGlass);
      const frame = new THREE.Mesh(new THREE.BoxGeometry(3.34, 1.28, 0.05), brushedSteel);
      frame.position.set(3.55, 1.85, -2.47);
      this.furnitureGroup.add(frame);

      const filterMat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.32, metalness: 0.25 });
      for (const [fx, fz] of [[4.38, -0.92], [4.38, -0.05]]) {
        const vessel = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 1.05, 20), filterMat);
        vessel.position.set(fx, 0.525, fz);
        this.furnitureGroup.add(vessel);
        const dome = new THREE.Mesh(new THREE.SphereGeometry(0.34, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2), filterMat);
        dome.position.set(fx, 1.05, fz);
        this.furnitureGroup.add(dome);
      }
      // Pool-basin warm-water heat transfer branch from hot riser into the blue filter manifold
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.38, 1.56, rz],
          [4.38, 1.56, -0.92],
          [4.38, 1.38, -0.92],
        ],
        0.024,
        branchHotMat,
        this.brassMat,
        [true, true]
      );
    } else {
      // Commercial Bakery / Hospital Plant Bay: Brushed stainless hot-water boiler + steel prep bench
      const boiler = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.42, 1.55, 20), brushedSteel);
      boiler.position.set(4.32, 0.775, -0.82);
      this.furnitureGroup.add(boiler);
      buildOrthogonalPipe(
        this.furnitureGroup,
        [
          [rx, 1.56, rz],
          [4.32, 1.56, rz],
          [4.32, 1.56, -0.82],
        ],
        0.024,
        branchHotMat,
        this.brassMat,
        [true, true]
      );
      const table = new THREE.Mesh(new THREE.BoxGeometry(0.80, 0.06, 1.75), brushedSteel);
      table.position.set(-4.35, 0.90, -0.25);
      this.furnitureGroup.add(table);
      for (const dz of [-0.76, 0.76]) {
        for (const dx of [-0.32, 0.32]) {
          const l = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.88, 10), brushedSteel);
          l.position.set(-4.35 + dx, 0.44, -0.25 + dz);
          this.furnitureGroup.add(l);
        }
      }
    }
  }

  applyRoomStyle(roomStyle) {
    const styleKey = ROOM_STYLES[roomStyle] ? roomStyle : "basement";
    const st = ROOM_STYLES[styleKey];
    const tex = getRoomTextures(styleKey);
    if (this.scene) this.scene.background = new THREE.Color(st.bg);
    if (this.floorMat) {
      this.floorMat.color.setHex(st.floor);
      this.floorMat.map = tex.floorTex;
      this.floorMat.needsUpdate = true;
    }
    if (this.wallMat) {
      this.wallMat.color.setHex(st.wall);
      this.wallMat.map = tex.wallTex;
      this.wallMat.needsUpdate = true;
    }
    if (this.ceilingMat) this.ceilingMat.color.setHex(st.ceiling);
    if (this.beamMat) this.beamMat.color.setHex(st.beam);
    if (this.accentMat) this.accentMat.color.setHex(st.accent);
    if (this.hemiLight) {
      this.hemiLight.color.setHex(st.hemiTop);
      this.hemiLight.groundColor.setHex(st.hemiBot);
    }
    if (this.keyLight) this.keyLight.color.setHex(st.keyHex);
    if (this.accentBand) {
      this.accentBand.position.y = st.accentY;
      this.accentBand.scale.y = st.accentH / 0.14;
    }
    if (this.currentRoomStyle !== styleKey) {
      this.buildRoomFurniture(styleKey);
    }
  }

  syncConfig({ modelId, count, activeCount, tankSocPct, loadRatio, roomStyle }) {
    this.lastConfig = { modelId, count, activeCount, tankSocPct, loadRatio, roomStyle };
    if (!this.ready || !this.templatesLoaded || !this.dynamicGroup) return;
    this.applyRoomStyle(roomStyle || "basement");

    // Dispose previous dynamic server units & branch manifolds (preserving shared SimReady template GPU buffers)
    while (this.dynamicGroup.children.length > 0) {
      const ch = this.dynamicGroup.children.pop();
      ch.traverse((o) => {
        if (o.isMesh) {
          if (!o.userData.sharedTemplate && o.geometry) o.geometry.dispose();
          if (o.material) {
            if (!o.userData.sharedTemplate || o.userData.clonedMaterial) {
              if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
              else o.material.dispose();
            }
          }
        }
      });
    }
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
    this.camera.position.set(1.25, 2.42, 6.45);
    this.controls.target.set(0.45, 1.08, -1.05);
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
