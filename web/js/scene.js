import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { cloneNodeMaterials } from "./provenance.js";
import { createHydronicSystem } from "./hydronics.js";
import { buildProceduralSwissTwin } from "./fleet_twins.js";

const Y_FLOOR = -3.179;

const BASEMENT_HARDWARE_LAYOUT = [
  {
    key: "powerPanel",
    url: "/assets/glb/hardware_power_panel.glb",
    pos: [-2.25, Y_FLOOR, -1.35],
    rotY: 0,
    scale: [1, 1, 1],
  },
  {
    key: "rack",
    url: "/assets/glb/hardware_server_rack.glb",
    pos: [-1.0, Y_FLOOR, -1.35],
    rotY: 0,
    scale: [1, 1, 1],
  },
  {
    key: "cdu",
    url: "/assets/glb/hardware_cdu_exchanger.glb",
    pos: [0.4, Y_FLOOR, -1.35],
    rotY: -Math.PI / 2,
    scale: [1, 1, 1],
  },
  {
    key: "tank",
    url: "/assets/glb/hardware_buffer_tank.glb",
    pos: [1.85, Y_FLOOR, -1.35],
    rotY: 0,
    scale: [1, 1, 1],
  },
  {
    key: "valve",
    url: "/assets/glb/hardware_valve_3way.glb",
    pos: [2.9, -2.5, -1.1],
    rotY: 0,
    scale: [1, 1, 1],
  },
  {
    key: "piping",
    url: "/assets/glb/hardware_piping.glb",
    pos: [0.45, Y_FLOOR, -2.62],
    rotY: Math.PI / 2,
    scale: [0.78, 1.0, 0.78],
  },
];

const EXTRA_BASEMENT_PROPS = [
  {
    key: "ledPanelLeft",
    url: "/assets/glb/hardware_led_panel.glb",
    pos: [-1.45, -0.5, -2.18],
    rotY: 0,
    scale: [0.22, 0.06, 0.16],
    isLed: true,
  },
  {
    key: "ledPanelRight",
    url: "/assets/glb/hardware_led_panel.glb",
    pos: [1.45, -0.5, -2.18],
    rotY: 0,
    scale: [0.22, 0.06, 0.16],
    isLed: true,
  },
  {
    key: "extinguisher",
    url: "/assets/glb/hardware_extinguisher.glb",
    pos: [-3.05, Y_FLOOR, -1.72],
    rotY: Math.PI * 0.35,
    scale: [1.2, 1.2, 1.2],
    isLed: false,
  },
];

const SKY_VERTEX_SHADER = `
  varying vec3 vWorldDir;
  void main() {
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vWorldDir = normalize(position);
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

const SKY_FRAGMENT_SHADER = `
  uniform vec3 uZenithColor;
  uniform vec3 uMidColor;
  uniform vec3 uHorizonColor;
  uniform vec3 uGroundColor;
  uniform vec3 uSunDir;
  uniform float uSunStrength;
  varying vec3 vWorldDir;

  void main() {
    vec3 dir = normalize(vWorldDir);
    float h = dir.y;
    vec3 skyCol;
    if (h >= 0.0) {
      float t1 = smoothstep(0.0, 0.28, h);
      float t2 = smoothstep(0.22, 0.85, h);
      skyCol = mix(uHorizonColor, uMidColor, t1);
      skyCol = mix(skyCol, uZenithColor, t2);
    } else {
      skyCol = mix(uHorizonColor, uGroundColor, smoothstep(0.0, -0.25, h));
    }

    float sunDot = max(dot(dir, normalize(uSunDir)), 0.0);
    float halo = pow(sunDot, 16.0) * 0.22 * uSunStrength;
    float disc = smoothstep(0.997, 0.9994, sunDot) * 0.85 * uSunStrength;
    skyCol += vec3(1.0, 0.96, 0.86) * (halo + disc);

    gl_FragColor = vec4(skyCol, 1.0);
  }
`;

function createDaylightSkyDome() {
  const geo = new THREE.SphereGeometry(850, 32, 20);
  const mat = new THREE.ShaderMaterial({
    vertexShader: SKY_VERTEX_SHADER,
    fragmentShader: SKY_FRAGMENT_SHADER,
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
    uniforms: {
      uZenithColor: { value: new THREE.Color(0x4f8cc9) },
      uMidColor: { value: new THREE.Color(0x9bc2e5) },
      uHorizonColor: { value: new THREE.Color(0xe6eef6) },
      uGroundColor: { value: new THREE.Color(0xd2dce4) },
      uSunDir: { value: new THREE.Vector3(0.48, 0.68, 0.55).normalize() },
      uSunStrength: { value: 1.0 },
    },
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = "alpine_daylight_skydome";
  mesh.userData.skipProvenance = true;
  mesh.renderOrder = -10;
  return { mesh, mat };
}

function buildProceduralEnvironment(renderer) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envScene = new THREE.Scene();
  envScene.background = new THREE.Color(0xdce8f4);

  const skyGeo = new THREE.SphereGeometry(24, 24, 16);
  const skyMat = new THREE.MeshBasicMaterial({ color: 0xb9d6f2, side: THREE.BackSide });
  envScene.add(new THREE.Mesh(skyGeo, skyMat));

  const warmSunPanel = new THREE.Mesh(
    new THREE.PlaneGeometry(14, 14),
    new THREE.MeshBasicMaterial({ color: 0xfff8e7, side: THREE.DoubleSide })
  );
  warmSunPanel.position.set(10, 12, 10);
  warmSunPanel.lookAt(0, 0, 0);
  envScene.add(warmSunPanel);

  const coolFillPanel = new THREE.Mesh(
    new THREE.PlaneGeometry(18, 12),
    new THREE.MeshBasicMaterial({ color: 0xdbeafe, side: THREE.DoubleSide })
  );
  coolFillPanel.position.set(-12, 8, -8);
  coolFillPanel.lookAt(0, 0, 0);
  envScene.add(coolFillPanel);

  const groundBounce = new THREE.Mesh(
    new THREE.PlaneGeometry(36, 36),
    new THREE.MeshBasicMaterial({ color: 0xb8b2a6, side: THREE.DoubleSide })
  );
  groundBounce.rotation.x = -Math.PI / 2;
  groundBounce.position.set(0, -10, 0);
  envScene.add(groundBounce);

  const tex = pmrem.fromScene(envScene, 0.035).texture;
  skyGeo.dispose();
  skyMat.dispose();
  pmrem.dispose();
  return tex;
}

function makeBaselFacadeTextures() {
  const size = 512;
  const colCanvas = document.createElement("canvas");
  colCanvas.width = size;
  colCanvas.height = size;
  const ctx = colCanvas.getContext("2d");

  // Warm Swiss sandstone / limestone plaster base
  ctx.fillStyle = "#ebe5d8";
  ctx.fillRect(0, 0, size, size);

  // Subtle mineral plaster variation
  let seed = 1337;
  const rand = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
  for (let i = 0; i < 4200; i += 1) {
    const x = rand() * size;
    const y = rand() * size;
    const w = 3 + rand() * 14;
    const h = 2 + rand() * 8;
    const v = 220 + Math.floor(rand() * 24);
    ctx.fillStyle = `rgba(${v}, ${v - 5}, ${v - 14}, 0.14)`;
    ctx.fillRect(x, y, w, h);
  }

  // Horizontal storey cornice bands (4 storeys per 12.8 m tile)
  const rows = 4;
  const cols = 4;
  const rowH = size / rows;
  const colW = size / cols;

  for (let r = 0; r < rows; r += 1) {
    const y0 = r * rowH;
    // Slight subtle facade tone banding per floor
    ctx.fillStyle = r % 2 === 0 ? "rgba(255,252,245,0.12)" : "rgba(185,176,160,0.08)";
    ctx.fillRect(0, y0, size, rowH);

    // Limestone stringcourse cornice
    ctx.fillStyle = "#d9d1c2";
    ctx.fillRect(0, y0, size, 4);
    ctx.fillStyle = "rgba(90, 82, 70, 0.18)";
    ctx.fillRect(0, y0 + 4, size, 2);

    for (let c = 0; c < cols; c += 1) {
      const x0 = c * colW;
      const wx = x0 + 24;
      const wy = y0 + 22;
      const ww = colW - 48;
      const wh = rowH - 44;

      // Recessed sandstone surround & shadow
      ctx.fillStyle = "#cfc6b5";
      ctx.fillRect(wx - 4, wy - 4, ww + 8, wh + 9);
      ctx.fillStyle = "#9b9284";
      ctx.fillRect(wx - 2, wy - 2, ww + 4, wh + 4);

      // Architectural glazed window with subtle sky gradient
      const grad = ctx.createLinearGradient(wx, wy, wx, wy + wh);
      grad.addColorStop(0, "#3b4c5e");
      grad.addColorStop(0.55, "#4c6278");
      grad.addColorStop(1, "#2f3d4c");
      ctx.fillStyle = grad;
      ctx.fillRect(wx, wy, ww, wh);

      // Subtle pale architectural mullions
      ctx.fillStyle = "#e6e0d4";
      ctx.fillRect(wx + ww * 0.5 - 1.5, wy, 3, wh);
      ctx.fillRect(wx, wy + wh * 0.34, ww, 2.5);

      // Crisp limestone sill below window
      ctx.fillStyle = "#dfd8ca";
      ctx.fillRect(wx - 6, wy + wh + 2, ww + 12, 6);
      ctx.fillStyle = "rgba(70, 62, 54, 0.22)";
      ctx.fillRect(wx - 5, wy + wh + 8, ww + 10, 3);
    }
  }

  const map = new THREE.CanvasTexture(colCanvas);
  map.wrapS = THREE.RepeatWrapping;
  map.wrapT = THREE.RepeatWrapping;
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;

  // Matching roughness map: matte plaster (0.88), glossier glass (0.28)
  const roughCanvas = document.createElement("canvas");
  roughCanvas.width = size;
  roughCanvas.height = size;
  const rctx = roughCanvas.getContext("2d");
  rctx.fillStyle = "#e0e0e0";
  rctx.fillRect(0, 0, size, size);
  rctx.fillStyle = "#484848";
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      rctx.fillRect(c * colW + 24, r * rowH + 22, colW - 48, rowH - 44);
    }
  }
  const roughnessMap = new THREE.CanvasTexture(roughCanvas);
  roughnessMap.wrapS = THREE.RepeatWrapping;
  roughnessMap.wrapT = THREE.RepeatWrapping;
  roughnessMap.anisotropy = 4;

  return { map, roughnessMap };
}

function makeBaselRoofTexture() {
  const size = 512;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");

  ctx.fillStyle = "#a35b48";
  ctx.fillRect(0, 0, size, size);

  let seed = 9001;
  const rand = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };

  // Overlapping Basel clay Biberschwanz / terracotta tile courses
  const rows = 24;
  const cols = 16;
  const rh = size / rows;
  const cw = size / cols;
  for (let r = 0; r < rows; r += 1) {
    const offset = (r % 2) * (cw * 0.5);
    for (let c = -1; c <= cols; c += 1) {
      const shade = rand();
      const rr = Math.floor(148 + shade * 40);
      const gg = Math.floor(82 + shade * 28);
      const bb = Math.floor(64 + shade * 22);
      ctx.fillStyle = `rgb(${rr},${gg},${bb})`;
      ctx.fillRect(c * cw + offset, r * rh, cw - 1, rh - 1.5);
    }
    // Horizontal lap shadow
    ctx.fillStyle = "rgba(56, 26, 18, 0.24)";
    ctx.fillRect(0, (r + 1) * rh - 2.2, size, 2.2);
  }

  const map = new THREE.CanvasTexture(canvas);
  map.wrapS = THREE.RepeatWrapping;
  map.wrapT = THREE.RepeatWrapping;
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;
  return map;
}

function makeWaterBumpTexture() {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#808080";
  ctx.fillRect(0, 0, size, size);
  for (let y = 0; y < size; y += 3) {
    for (let x = 0; x < size; x += 6) {
      const wave =
        Math.sin((x / size) * Math.PI * 10 + (y / size) * Math.PI * 4) * 0.5 +
        Math.cos((y / size) * Math.PI * 14 - (x / size) * Math.PI * 6) * 0.5;
      const g = Math.floor(128 + wave * 42);
      ctx.fillStyle = `rgb(${g},${g},${g})`;
      ctx.fillRect(x, y, 6, 3);
    }
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(22, 22);
  return tex;
}

function makeAsphaltTextures() {
  const size = 256;
  const colCanvas = document.createElement("canvas");
  colCanvas.width = size;
  colCanvas.height = size;
  const ctx = colCanvas.getContext("2d");
  const bumpCanvas = document.createElement("canvas");
  bumpCanvas.width = size;
  bumpCanvas.height = size;
  const bctx = bumpCanvas.getContext("2d");
  const cImg = ctx.createImageData(size, size);
  const bImg = bctx.createImageData(size, size);
  let seed = 424242;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  for (let i = 0; i < size * size; i++) {
    const fine = rand();
    const speck = rand() > 0.94 ? 26 : 0;
    const base = Math.round(72 + fine * 24 + speck);
    const idx = i * 4;
    cImg.data[idx] = base;
    cImg.data[idx + 1] = base + 2;
    cImg.data[idx + 2] = base + 5;
    cImg.data[idx + 3] = 255;
    const bv = Math.round(96 + fine * 64 + speck);
    bImg.data[idx] = bv;
    bImg.data[idx + 1] = bv;
    bImg.data[idx + 2] = bv;
    bImg.data[idx + 3] = 255;
  }
  ctx.putImageData(cImg, 0, 0);
  bctx.putImageData(bImg, 0, 0);

  const map = new THREE.CanvasTexture(colCanvas);
  map.wrapS = THREE.RepeatWrapping;
  map.wrapT = THREE.RepeatWrapping;
  map.colorSpace = THREE.SRGBColorSpace;
  const bumpMap = new THREE.CanvasTexture(bumpCanvas);
  bumpMap.wrapS = THREE.RepeatWrapping;
  bumpMap.wrapT = THREE.RepeatWrapping;
  return { map, bumpMap };
}

function makeMeadowSkirtTexture() {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(size, size);
  let seed = 881723;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const grain = (rand() - 0.5) * 20;
      const idx = (y * size + x) * 4;
      img.data[idx] = Math.max(0, Math.min(255, Math.round(96 + grain)));
      img.data[idx + 1] = Math.max(0, Math.min(255, Math.round(115 + grain * 1.1)));
      img.data[idx + 2] = Math.max(0, Math.min(255, Math.round(80 + grain * 0.85)));
      img.data[idx + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(36, 36);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function makeTerrainDetailBumpTexture() {
  const size = 256;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(size, size);
  let seed = 1357911;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  for (let i = 0; i < size * size; i++) {
    const v = Math.round(88 + rand() * 88);
    const idx = i * 4;
    img.data[idx] = v;
    img.data[idx + 1] = v;
    img.data[idx + 2] = v;
    img.data[idx + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(44, 44);
  return tex;
}

function enhanceTerrainOrthoTexture(origTex, maxAniso) {
  const src = origTex?.image;
  if (!src || !src.width || !src.height) return origTex;
  const w = Math.min(1024, src.width);
  const h = Math.min(1024, src.height);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(src, 0, 0, w, h);
  const img = ctx.getImageData(0, 0, w, h);
  const d = img.data;
  let seed = 918273;
  for (let i = 0; i < d.length; i += 4) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const grain = ((seed >>> 24) / 255 - 0.5) * 14;
    let r = d[i] / 255;
    let g = d[i + 1] / 255;
    let b = d[i + 2] / 255;
    const luma = 0.299 * r + 0.587 * g + 0.114 * b;
    const gEx = g - 0.5 * (r + b);
    const grassW = Math.max(0, Math.min(1, (gEx + 0.008) / 0.055));
    const darkBlobW = Math.max(0, Math.min(1, (0.28 - luma) / 0.18));

    if (grassW > 0.05) {
      // Natural Swiss alpine/park grass tone + blade micro-grain
      const gr = 0.28 + 0.34 * Math.min(1, luma * 1.15);
      const gg = 0.37 + 0.36 * Math.min(1, luma * 1.15);
      const gb = 0.20 + 0.24 * Math.min(1, luma * 1.05);
      r = r * (1 - grassW * 0.62) + gr * (grassW * 0.62);
      g = g * (1 - grassW * 0.62) + gg * (grassW * 0.62);
      b = b * (1 - grassW * 0.62) + gb * (grassW * 0.62);
    } else if (darkBlobW > 0.05) {
      // Soften harsh dark 2D satellite tree shadows so real 3D shadows dominate
      r = r * (1 - darkBlobW * 0.45) + 0.30 * (darkBlobW * 0.45);
      g = g * (1 - darkBlobW * 0.45) + 0.35 * (darkBlobW * 0.45);
      b = b * (1 - darkBlobW * 0.45) + 0.26 * (darkBlobW * 0.45);
    }

    // Compress bleached-out aerial street glare into warm weathered stone/asphalt
    if (luma > 0.68) {
      const comp = 0.68 + (luma - 0.68) * 0.42;
      const scale = comp / Math.max(luma, 1e-4);
      r *= scale * 0.98;
      g *= scale * 0.97;
      b *= scale * 0.94;
    }

    d[i] = Math.max(0, Math.min(255, Math.round(r * 255 + grain)));
    d[i + 1] = Math.max(0, Math.min(255, Math.round(g * 255 + grain * 1.08)));
    d[i + 2] = Math.max(0, Math.min(255, Math.round(b * 255 + grain * 0.85)));
  }
  ctx.putImageData(img, 0, 0);
  const out = new THREE.CanvasTexture(canvas);
  out.flipY = origTex.flipY;
  out.wrapS = origTex.wrapS;
  out.wrapT = origTex.wrapT;
  out.colorSpace = THREE.SRGBColorSpace;
  out.anisotropy = maxAniso;
  return out;
}

function makePoolMosaicTexture() {
  const size = 512;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");

  ctx.fillStyle = "#56c7de";
  ctx.fillRect(0, 0, size, size);

  const tiles = 32;
  const step = size / tiles;
  let seed = 7751;
  const rand = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
  for (let r = 0; r < tiles; r += 1) {
    for (let c = 0; c < tiles; c += 1) {
      const j = Math.floor((rand() - 0.5) * 18);
      ctx.fillStyle = `rgb(${78 + j}, ${194 + j}, ${220 + j})`;
      ctx.fillRect(c * step + 1, r * step + 1, step - 1.5, step - 1.5);
    }
  }

  // Dark navy Olympic swim lane markers and T-ends
  ctx.fillStyle = "#0d3b75";
  for (const lane of [4, 12, 20, 28]) {
    const x = lane * step;
    ctx.fillRect(x + 2, 24, step - 4, size - 48);
    ctx.fillRect(x - step, 24, step * 3, 9);
    ctx.fillRect(x - step, size - 33, step * 3, 9);
  }

  // Floating lane ropes with alternating buoy colors
  for (const rope of [8, 16, 24]) {
    const rx = rope * step;
    for (let y = 0; y < size; y += 16) {
      if (y < 70 || y > size - 70) {
        ctx.fillStyle = "#df3838";
      } else {
        ctx.fillStyle = (y / 16) % 2 === 0 ? "#f4f9fc" : "#1c7ad4";
      }
      ctx.fillRect(rx - 2.5, y + 1, 5, 13);
    }
  }

  // Subtle sunlit caustic shimmer bands
  ctx.strokeStyle = "rgba(225, 252, 255, 0.24)";
  ctx.lineWidth = 3;
  for (let i = 0; i < 18; i += 1) {
    ctx.beginPath();
    for (let x = 0; x <= size; x += 24) {
      const y = ((i * 31) % size) + Math.sin(x * 0.045 + i * 1.3) * 14;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function applyArchitecturalBoxUVs(geometry, tileU, tileV) {
  if (!geometry || !geometry.attributes?.position) return geometry;
  const nonIdx = geometry.index ? geometry.toNonIndexed() : geometry;
  const pos = nonIdx.attributes.position;
  const count = pos.count;
  const uvs = new Float32Array(count * 2);

  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  const ab = new THREE.Vector3();
  const ac = new THREE.Vector3();
  const n = new THREE.Vector3();

  for (let i = 0; i < count; i += 3) {
    a.fromBufferAttribute(pos, i);
    b.fromBufferAttribute(pos, i + 1);
    c.fromBufferAttribute(pos, i + 2);
    ab.subVectors(b, a);
    ac.subVectors(c, a);
    n.crossVectors(ab, ac).normalize();

    const ax = Math.abs(n.x);
    const ay = Math.abs(n.y);
    const az = Math.abs(n.z);

    for (let k = 0; k < 3; k += 1) {
      const idx = i + k;
      const px = pos.getX(idx);
      const py = pos.getY(idx);
      const pz = pos.getZ(idx);
      let u = 0;
      let v = 0;
      if (ay >= ax && ay >= az) {
        u = px / tileU;
        v = pz / tileV;
      } else if (ax >= az) {
        u = pz / tileU;
        v = py / tileV;
      } else {
        u = px / tileU;
        v = py / tileV;
      }
      uvs[idx * 2] = u;
      uvs[idx * 2 + 1] = v;
    }
  }
  nonIdx.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  nonIdx.computeVertexNormals();
  return nonIdx;
}

function enhanceDarkCabinetMaterials(root) {
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      const c = mat.color;
      const isVeryDark = c && c.r < 0.03 && c.g < 0.03 && c.b < 0.03;
      if (mat.name === "vertiv_black" || isVeryDark) {
        mat.color = new THREE.Color(0x232a36);
        mat.metalness = Math.min(mat.metalness ?? 0.45, 0.55);
        mat.roughness = 0.38;
        mat.emissive = new THREE.Color(0x111827);
        mat.emissiveIntensity = 0.22;
        mat.needsUpdate = true;
      }
    }
  });
}

function enhanceLedPanelMaterials(root) {
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const mat of mats) {
      if ((mat.name || "").toLowerCase().includes("emissive")) {
        mat.color = new THREE.Color(0xffffff);
        mat.emissive = new THREE.Color(0xfffaf0);
        mat.emissiveIntensity = 1.6;
        mat.roughness = 0.2;
        mat.needsUpdate = true;
      }
    }
  });
}

function extractMeshPrimitives(root) {
  const parts = [];
  root.updateMatrixWorld(true);
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.geometry) return;
    const geo = obj.geometry.clone();
    geo.applyMatrix4(obj.matrixWorld);
    const mat = Array.isArray(obj.material) ? obj.material[0].clone() : obj.material.clone();
    mat.metalness = 0.0;
    mat.side = THREE.DoubleSide;
    parts.push({ geometry: geo, material: mat, name: obj.name || "" });
  });
  return parts;
}

export function createCockpitScene(canvasEl) {
  const renderer = new THREE.WebGLRenderer({
    canvas: canvasEl,
    antialias: true,
    powerPreference: "high-performance",
  });
  const glCtx = renderer.getContext();
  const dbgExt = glCtx ? glCtx.getExtension("WEBGL_debug_renderer_info") : null;
  const rendererRaw = glCtx ? String(glCtx.getParameter(glCtx.RENDERER) || "") : "";
  const gpuRendererStr =
    rendererRaw + " " + (dbgExt ? String(glCtx.getParameter(dbgExt.UNMASKED_RENDERER_WEBGL) || "") : "");
  const isSoftwareGl = /swiftshader|llvmpipe|software|mesa offscreen/i.test(gpuRendererStr);
  const maxAniso = isSoftwareGl ? 1 : Math.min(8, renderer.capabilities.getMaxAnisotropy());

  renderer.setPixelRatio(isSoftwareGl ? 1 : Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.08;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = isSoftwareGl ? THREE.PCFShadowMap : THREE.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;
  renderer.shadowMap.needsUpdate = true;

  function markShadowsDirty() {
    renderer.shadowMap.needsUpdate = true;
  }

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xdbe7f3);
  scene.fog = new THREE.FogExp2(0xdce8f3, 0.00115);
  scene.environment = buildProceduralEnvironment(renderer);
  scene.environmentIntensity = 0.92;

  const skyDome = createDaylightSkyDome();
  scene.add(skyDome.mesh);

  const facadeTex = makeBaselFacadeTextures();
  facadeTex.map.anisotropy = maxAniso;
  facadeTex.roughnessMap.anisotropy = maxAniso;
  const roofTex = makeBaselRoofTexture();
  roofTex.anisotropy = maxAniso;
  const waterBumpTex = makeWaterBumpTexture();
  const poolMosaicTex = makePoolMosaicTexture();
  poolMosaicTex.anisotropy = maxAniso;
  const asphaltTex = makeAsphaltTextures();
  asphaltTex.map.anisotropy = maxAniso;
  const terrainDetailBump = makeTerrainDetailBumpTexture();
  const meadowSkirtTex = makeMeadowSkirtTexture();
  meadowSkirtTex.anisotropy = maxAniso;

  const camera = new THREE.PerspectiveCamera(
    45,
    Math.max(1, window.innerWidth) / Math.max(1, window.innerHeight),
    0.15,
    1400
  );
  camera.position.set(68, 44, 86);

  const controls = new OrbitControls(camera, canvasEl);
  controls.target.set(0, 4.0, 0);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.maxPolarAngle = Math.PI * 0.495;
  controls.minDistance = 1.8;
  controls.maxDistance = 260;
  controls.update();

  // Daylight Architectural Lighting Rig
  const hemiLight = new THREE.HemisphereLight(0xe0f0ff, 0x8d877c, 1.05);
  scene.add(hemiLight);

  const sunLight = new THREE.DirectionalLight(0xfff5e0, 2.25);
  sunLight.position.set(55, 82, 60);
  sunLight.castShadow = true;
  const shadowDim = isSoftwareGl ? 1024 : 2048;
  sunLight.shadow.mapSize.set(shadowDim, shadowDim);
  sunLight.shadow.bias = -0.0003;
  sunLight.shadow.normalBias = 0.04;
  sunLight.shadow.camera.near = 1;
  sunLight.shadow.camera.far = 380;
  sunLight.shadow.camera.left = -55;
  sunLight.shadow.camera.right = 55;
  sunLight.shadow.camera.top = 55;
  sunLight.shadow.camera.bottom = -55;
  sunLight.name = "solar_directional_light";
  sunLight.userData.provenanceCategory = "official_benchmark";
  scene.add(sunLight);

  const solarRigMat = new THREE.MeshBasicMaterial({ color: 0xfde047, toneMapped: false });
  const solarSunRig = new THREE.Mesh(new THREE.SphereGeometry(2.0, 16, 12), solarRigMat);
  solarSunRig.name = "solar_sun_rig";
  solarSunRig.userData.provenanceCategory = "official_benchmark";
  solarSunRig.userData.originalMaterial = solarRigMat;
  solarSunRig.position.set(35, 55, 40);
  scene.add(solarSunRig);

  // Scene hierarchy groups
  const siteGroup = new THREE.Group();
  siteGroup.name = "site_context_group";
  siteGroup.userData.provenanceCategory = "geodata_cad_blueprint";
  scene.add(siteGroup);

  const vegetationGroup = new THREE.Group();
  vegetationGroup.name = "site_vegetation_group";
  vegetationGroup.userData.provenanceCategory = "geodata_cad_blueprint";
  scene.add(vegetationGroup);

  const houseGroup = new THREE.Group();
  houseGroup.name = "house_bim_group";
  houseGroup.userData.provenanceCategory = "geodata_cad_blueprint";
  scene.add(houseGroup);

  const basementGroup = new THREE.Group();
  basementGroup.name = "basement_plant_group";
  basementGroup.userData.provenanceCategory = "geodata_cad_blueprint";
  scene.add(basementGroup);

  const fleetTwinGroup = new THREE.Group();
  fleetTwinGroup.name = "swiss_fleet_twin_group";
  fleetTwinGroup.visible = false;
  scene.add(fleetTwinGroup);
  let fleetTwinBeads = [];

  // Gallery-grade basement interior lighting
  const basementKeyLight1 = new THREE.PointLight(0xfffaf0, 18.0, 14.0, 1.5);
  basementKeyLight1.position.set(-0.9, -0.82, -1.0);
  basementGroup.add(basementKeyLight1);

  const basementKeyLight2 = new THREE.PointLight(0xe0f2fe, 14.0, 12.0, 1.5);
  basementKeyLight2.position.set(1.8, -0.85, 0.8);
  basementGroup.add(basementKeyLight2);

  const basementFillLight = new THREE.PointLight(0xfef3c7, 6.5, 10.0, 1.6);
  basementFillLight.position.set(0.0, -2.1, 1.6);
  basementGroup.add(basementFillLight);

  // Surface Verbund marker for remote sites / dual-sink surface loop
  const verbundHubGroup = new THREE.Group();
  verbundHubGroup.name = "verbund_surface_hub";
  verbundHubGroup.visible = false;
  scene.add(verbundHubGroup);

  const beaconMat = new THREE.MeshStandardMaterial({
    color: 0x15803d,
    emissive: 0x16a34a,
    emissiveIntensity: 0.48,
    roughness: 0.28,
    metalness: 0.15,
  });
  const beaconPillar = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.85, 5.5, 20), beaconMat);
  beaconPillar.position.set(0, 2.75, 0);
  beaconPillar.userData.provenanceCategory = "official_benchmark";
  beaconPillar.userData.originalMaterial = beaconMat;
  verbundHubGroup.add(beaconPillar);

  const beaconRingMat = beaconMat.clone();
  const beaconRing = new THREE.Mesh(new THREE.TorusGeometry(4.0, 0.28, 12, 36), beaconRingMat);
  beaconRing.rotation.x = Math.PI / 2;
  beaconRing.position.set(0, 1.0, 0);
  beaconRing.userData.provenanceCategory = "official_benchmark";
  beaconRing.userData.originalMaterial = beaconRingMat;
  verbundHubGroup.add(beaconRing);

  // Geothermal borehole visual for basel_erdsonde
  const erdsondeGroup = new THREE.Group();
  erdsondeGroup.name = "erdsonde_borehole_rig";
  erdsondeGroup.visible = false;
  scene.add(erdsondeGroup);
  const boreMat = new THREE.MeshStandardMaterial({
    color: 0x0284c7,
    emissive: 0x0369a1,
    emissiveIntensity: 0.45,
    roughness: 0.3,
  });
  for (const dz of [-1.2, -1.8]) {
    const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 4.0, 12), boreMat.clone());
    tube.position.set(5.2, -1.4, dz);
    tube.userData.provenanceCategory = "official_benchmark";
    tube.userData.originalMaterial = tube.material;
    erdsondeGroup.add(tube);
  }

  // GLTF & Draco Loader
  const dracoLoader = new DRACOLoader();
  dracoLoader.setDecoderPath("/vendor/three/jsm/libs/draco/gltf/");
  const gltfLoader = new GLTFLoader();
  gltfLoader.setDRACOLoader(dracoLoader);

  const glbCache = new Map();
  const hardwareGroups = {
    enclosure: null,
    powerPanel: null,
    rack: null,
    cdu: null,
    tank: null,
    valve: null,
    piping: null,
  };

  let vegetationCoords = {};
  let vegTemplates = null;
  const siteVegetationCache = new Map();

  let currentSiteUrl = "/assets/glb/context_neighborhood.glb";
  let currentSiteRoot = null;
  let xrayActive = false;
  let provenanceCtrl = null;
  let assetsMeta = {};
  let hydronics = null;
  let workerRack = null;
  let cameraTween = null;
  let orbitTween = null;

  controls.addEventListener("start", () => {
    cameraTween = null;
    orbitTween = null;
  });

  function loadRawGlb(url) {
    return new Promise((resolve, reject) => {
      gltfLoader.load(url, (gltf) => resolve(gltf.scene), undefined, reject);
    });
  }

  function enhanceGeodataSite(root, siteUrl = "") {
    const isPoolSite = siteUrl.includes("st_jakob");
    root.traverse((obj) => {
      if (!obj.isMesh) return;
      const nm = obj.name || "";

      if (nm === "neighborhood_buildings_walls" || nm === "cutaway_house_walls") {
        obj.geometry = applyArchitecturalBoxUVs(obj.geometry, 12.8, 12.8);
        obj.material = new THREE.MeshStandardMaterial({
          name: `${nm}_basel_plaster`,
          color: nm === "cutaway_house_walls" ? 0xf7f3eb : 0xf2ece1,
          map: facadeTex.map,
          roughnessMap: facadeTex.roughnessMap,
          roughness: 0.9,
          metalness: 0.04,
        });
        obj.castShadow = true;
        obj.receiveShadow = true;
      } else if (nm === "neighborhood_buildings_roofs" || nm === "cutaway_house_roof") {
        obj.geometry = applyArchitecturalBoxUVs(obj.geometry, 7.5, 7.5);
        obj.material = new THREE.MeshStandardMaterial({
          name: `${nm}_basel_clay_roof`,
          color: nm === "cutaway_house_roof" ? 0xfaf2ec : 0xf4ebe5,
          map: roofTex,
          roughness: 0.85,
          metalness: 0.02,
        });
        obj.castShadow = true;
        obj.receiveShadow = true;
      } else if (nm === "osm_water_pools") {
        obj.geometry = applyArchitecturalBoxUVs(obj.geometry, isPoolSite ? 22.0 : 25.0, isPoolSite ? 22.0 : 25.0);
        obj.position.y += isPoolSite ? 0.22 : 0.14;
        obj.material = new THREE.MeshStandardMaterial({
          name: isPoolSite ? "osm_pool_mosaic_water" : "osm_water_physical",
          color: isPoolSite ? 0xeafcff : 0x16687e,
          map: isPoolSite ? poolMosaicTex : null,
          roughness: isPoolSite ? 0.12 : 0.15,
          metalness: isPoolSite ? 0.16 : 0.24,
          bumpMap: waterBumpTex,
          bumpScale: isPoolSite ? 0.06 : 0.08,
          polygonOffset: true,
          polygonOffsetFactor: -2,
          polygonOffsetUnits: -2,
        });
        obj.castShadow = false;
        obj.receiveShadow = true;
      } else if (nm === "osm_roads") {
        obj.geometry = applyArchitecturalBoxUVs(obj.geometry, 7.5, 7.5);
        obj.material = new THREE.MeshStandardMaterial({
          name: "osm_roads_asphalt",
          color: 0xffffff,
          map: asphaltTex.map,
          bumpMap: asphaltTex.bumpMap,
          bumpScale: 0.055,
          roughness: 0.89,
          metalness: 0.02,
          transparent: true,
          opacity: 0.86,
          polygonOffset: true,
          polygonOffsetFactor: -1,
          polygonOffsetUnits: -1,
        });
        obj.castShadow = false;
        obj.receiveShadow = true;
      } else if (nm === "terrain_ground") {
        if (obj.material) {
          obj.material.roughness = 0.94;
          obj.material.metalness = 0.0;
          if (obj.material.map) {
            obj.material.map = enhanceTerrainOrthoTexture(obj.material.map, maxAniso);
          }
          obj.material.bumpMap = terrainDetailBump;
          obj.material.bumpScale = 0.14;
          obj.material.needsUpdate = true;
        }
        obj.castShadow = false;
        obj.receiveShadow = true;
      }
    });

    // Seamless surrounding valley ground skirt so terrain never ends as a floating tile
    if (!root.getObjectByName("horizon_meadow_skirt")) {
      const box = new THREE.Box3().setFromObject(root);
      const skirtY = Number.isFinite(box.min.y) ? box.min.y - 0.7 : -3.0;
      const skirt = new THREE.Mesh(
        new THREE.CircleGeometry(1200, 48),
        new THREE.MeshStandardMaterial({
          name: "horizon_meadow_mat",
          map: meadowSkirtTex,
          bumpMap: terrainDetailBump,
          bumpScale: 0.12,
          roughness: 0.96,
          metalness: 0.0,
        })
      );
      skirt.name = "horizon_meadow_skirt";
      skirt.rotation.x = -Math.PI / 2;
      skirt.position.set(0, skirtY, 0);
      skirt.receiveShadow = true;
      root.add(skirt);
    }
  }

  function enhanceHouseBimShell(root) {
    root.traverse((obj) => {
      if (!obj.isMesh || !obj.material) return;
      const pName = (obj.parent?.name || "").toLowerCase();
      const mName = (obj.name || "").toLowerCase();
      if (pName === "shell_roof" || mName.includes("roof")) {
        obj.material = new THREE.MeshStandardMaterial({
          name: `${mName}_clay_roof`,
          color: 0x8c4435,
          roughness: 0.84,
          metalness: 0.04,
        });
      } else if (pName === "shell_walls_front" || pName === "shell_walls_rear_side") {
        if (mName.includes("cellar")) {
          obj.material = new THREE.MeshStandardMaterial({
            name: `${mName}_concrete`,
            color: 0xc9c4ba,
            roughness: 0.88,
            metalness: 0.04,
          });
        } else {
          obj.material = new THREE.MeshStandardMaterial({
            name: `${mName}_plaster`,
            color: 0xf6f3ec,
            roughness: 0.85,
            metalness: 0.02,
          });
        }
      } else if (pName === "shell_slabs") {
        obj.material = new THREE.MeshStandardMaterial({
          name: `${mName}_oak_slab`,
          color: mName.includes("cellar") ? 0xb8b4ac : 0xcfb492,
          roughness: 0.78,
          metalness: 0.04,
        });
      }
    });
  }

  function loadGlb(url, options = {}) {
    if (glbCache.has(url)) {
      return glbCache.get(url);
    }
    const promise = loadRawGlb(url).then((root) => {
      if (options.isGeodata) {
        enhanceGeodataSite(root, url);
      } else if (options.isHouseShell) {
        enhanceHouseBimShell(root);
      }
      cloneNodeMaterials(root, "geodata_cad_blueprint");
      root.traverse((obj) => {
        if (obj.isMesh && !options.isGeodata) {
          obj.castShadow = true;
          obj.receiveShadow = true;
        }
      });
      return root;
    });
    glbCache.set(url, promise);
    return promise;
  }

  async function loadVegetationLibrary() {
    try {
      const [coordsResp, beechRoot, firRoot, shrubRoot] = await Promise.all([
        fetch("/assets/vegetation_coords.json").then((r) => (r.ok ? r.json() : {})),
        loadRawGlb("/assets/glb/env_tree_beech.glb"),
        loadRawGlb("/assets/glb/env_tree_fir.glb"),
        loadRawGlb("/assets/glb/env_shrub_boxwood.glb"),
      ]);
      vegetationCoords = coordsResp || {};
      vegTemplates = {
        beech: extractMeshPrimitives(beechRoot),
        fir: extractMeshPrimitives(firRoot),
        shrub: extractMeshPrimitives(shrubRoot),
      };
    } catch {
      vegTemplates = null;
    }
  }

  function buildInstancedParts(parts, entries, paletteHexes, groupName) {
    if (!parts || !parts.length || !entries || !entries.length) return null;
    const container = new THREE.Group();
    container.name = groupName;
    const dummy = new THREE.Object3D();
    const tint = new THREE.Color();

    for (const part of parts) {
      const isFoliage = part.name.toLowerCase().includes("foliage");
      const mat = part.material.clone();
      mat.roughness = isFoliage ? 0.82 : 0.92;
      mat.metalness = 0.0;
      const inst = new THREE.InstancedMesh(part.geometry, mat, entries.length);
      inst.name = `${groupName}_${part.name}`;
      inst.castShadow = !isSoftwareGl && !isFoliage;
      inst.receiveShadow = !isSoftwareGl;
      inst.userData.provenanceCategory = "geodata_cad_blueprint";
      inst.userData.originalMaterial = mat;

      for (let i = 0; i < entries.length; i += 1) {
        const [x, y, z, sc, yaw] = entries[i];
        dummy.position.set(x, y, z);
        dummy.rotation.set(0, yaw, 0);
        dummy.scale.set(sc, sc * (0.92 + ((i * 37) % 17) * 0.01), sc);
        dummy.updateMatrix();
        inst.setMatrixAt(i, dummy.matrix);
        if (isFoliage && paletteHexes.length) {
          tint.setHex(paletteHexes[i % paletteHexes.length]);
          inst.setColorAt(i, tint);
        }
      }
      inst.instanceMatrix.needsUpdate = true;
      if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
      container.add(inst);
    }
    return container;
  }

  function mountSiteVegetation(siteKey) {
    while (vegetationGroup.children.length > 0) {
      vegetationGroup.remove(vegetationGroup.children[0]);
    }
    if (!vegTemplates || !vegetationCoords[siteKey]) return;

    if (!siteVegetationCache.has(siteKey)) {
      const data = vegetationCoords[siteKey];
      const siteVeg = new THREE.Group();
      siteVeg.name = `veg_${siteKey}`;
      siteVeg.userData.provenanceCategory = "geodata_cad_blueprint";

      const maxTrees = isSoftwareGl ? 95 : 260;
      const maxShrubs = isSoftwareGl ? 85 : 260;
      const rawTrees = (data.trees || []).slice(0, maxTrees);
      const rawShrubs = (data.shrubs || []).slice(0, maxShrubs);
      const isAlpine = siteKey === "verbund_davos";

      const beechTrees = [];
      const firTrees = [];
      for (let i = 0; i < rawTrees.length; i += 1) {
        const useFir = isAlpine ? i % 5 !== 0 : i % 5 === 0;
        if (useFir) firTrees.push(rawTrees[i]);
        else beechTrees.push(rawTrees[i]);
      }

      const beechLayer = buildInstancedParts(
        vegTemplates.beech,
        beechTrees,
        [0xffffff, 0xe8f3da, 0xd4e7c5, 0xf2efd8, 0xcde2bc],
        "trees_beech"
      );
      const firLayer = buildInstancedParts(
        vegTemplates.fir,
        firTrees,
        [0xffffff, 0xd8ebdd, 0xc7dfd0, 0xe5f0e2],
        "trees_fir"
      );
      const shrubLayer = buildInstancedParts(
        vegTemplates.shrub,
        rawShrubs,
        [0xffffff, 0xe4f1d4, 0xd2e5be, 0xf0f4da],
        "shrubs_boxwood"
      );

      if (beechLayer) siteVeg.add(beechLayer);
      if (firLayer) siteVeg.add(firLayer);
      if (shrubLayer) siteVeg.add(shrubLayer);
      siteVegetationCache.set(siteKey, siteVeg);
    }

    const vegNode = siteVegetationCache.get(siteKey);
    vegetationGroup.add(vegNode);
    if (provenanceCtrl && provenanceCtrl.isActive()) {
      provenanceCtrl.applyToObject(vegNode, "geodata_cad_blueprint");
    }
  }

  function updateCutawayHouseVisibility() {
    if (!currentSiteRoot) return;
    const hideCutaway = Boolean(houseGroup.visible && houseGroup.children.length > 0);
    const isolateBasement = Boolean(xrayActive && houseGroup.visible);
    currentSiteRoot.traverse((obj) => {
      if (obj.name === "cutaway_house_walls" || obj.name === "cutaway_house_roof") {
        obj.visible = !hideCutaway;
      } else if (
        obj.name === "terrain_ground" ||
        obj.name === "osm_roads" ||
        obj.name === "neighborhood_buildings_walls" ||
        obj.name === "neighborhood_buildings_roofs"
      ) {
        obj.visible = !isolateBasement;
      }
    });
    vegetationGroup.visible = !isolateBasement;
  }

  function applyXrayToHouse() {
    if (!houseGroup.children.length) return;
    const opacityRules = {
      shell_slabs: 0.14,
      shell_walls_rear_side: 0.28,
      shell_interior: 0.35,
      shell_hvac_chimney: 0.35,
    };

    houseGroup.traverse((obj) => {
      if (obj.name === "shell_roof" || obj.name === "shell_walls_front") {
        obj.visible = !xrayActive;
      }
    });

    for (const [groupName, targetOp] of Object.entries(opacityRules)) {
      const grp = houseGroup.getObjectByName(groupName);
      if (!grp) continue;
      grp.traverse((obj) => {
        if (!obj.isMesh) return;
        const mats = [];
        if (obj.userData.originalMaterial) {
          mats.push(
            ...(Array.isArray(obj.userData.originalMaterial)
              ? obj.userData.originalMaterial
              : [obj.userData.originalMaterial])
          );
        }
        if (obj.userData.provenanceMaterial) {
          mats.push(
            ...(Array.isArray(obj.userData.provenanceMaterial)
              ? obj.userData.provenanceMaterial
              : [obj.userData.provenanceMaterial])
          );
        }
        if (obj.material) {
          mats.push(...(Array.isArray(obj.material) ? obj.material : [obj.material]));
        }
        for (const mat of mats) {
          mat.transparent = xrayActive;
          mat.opacity = xrayActive ? targetOp : 1.0;
          mat.depthWrite = !xrayActive;
          mat.needsUpdate = true;
        }
      });
    }
  }

  function setXray(enabled) {
    xrayActive = Boolean(enabled);
    applyXrayToHouse();
    updateCutawayHouseVisibility();
    markShadowsDirty();
  }

  function setShadowFrustum(halfExtent) {
    sunLight.shadow.camera.left = -halfExtent;
    sunLight.shadow.camera.right = halfExtent;
    sunLight.shadow.camera.top = halfExtent;
    sunLight.shadow.camera.bottom = -halfExtent;
    sunLight.shadow.camera.updateProjectionMatrix();
  }

  function siteKeyFromUrl(glbUrl) {
    const fname = (glbUrl || "").split("/").pop() || "";
    return fname.replace(/\.glb$/i, "");
  }

  async function mountSiteUrl(glbUrl) {
    const siteKey = siteKeyFromUrl(glbUrl);
    if (currentSiteUrl === glbUrl && currentSiteRoot) {
      mountSiteVegetation(siteKey);
      updateCutawayHouseVisibility();
      return currentSiteRoot;
    }
    const root = await loadGlb(glbUrl, { isGeodata: true });
    while (siteGroup.children.length > 0) {
      siteGroup.remove(siteGroup.children[0]);
    }
    currentSiteUrl = glbUrl;
    currentSiteRoot = root;
    siteGroup.add(root);
    mountSiteVegetation(siteKey);
    updateCutawayHouseVisibility();
    if (provenanceCtrl && provenanceCtrl.isActive()) {
      provenanceCtrl.applyToObject(root, "geodata_cad_blueprint");
    }
    markShadowsDirty();
    return root;
  }

  async function initAssets(provenanceData) {
    assetsMeta = provenanceData?.assets || {};

    const [siteRoot, houseRoot, enclosureRoot] = await Promise.all([
      loadGlb("/assets/glb/context_neighborhood.glb", { isGeodata: true }),
      loadGlb("/assets/glb/house_bim_shell.glb", { isHouseShell: true }),
      loadGlb("/assets/glb/hardware_basement_enclosure.glb"),
      loadVegetationLibrary(),
    ]);

    currentSiteRoot = siteRoot;
    siteGroup.add(siteRoot);
    mountSiteVegetation("context_neighborhood");

    houseGroup.add(houseRoot);
    updateCutawayHouseVisibility();

    enclosureRoot.position.set(0.0, -3.2, -1.305);
    basementGroup.add(enclosureRoot);
    hardwareGroups.enclosure = enclosureRoot;

    await Promise.all([
      ...BASEMENT_HARDWARE_LAYOUT.map(async (item) => {
        const root = await loadGlb(item.url);
        root.position.set(...item.pos);
        root.rotation.y = item.rotY;
        root.scale.set(...item.scale);
        enhanceDarkCabinetMaterials(root);
        basementGroup.add(root);
        hardwareGroups[item.key] = root;
      }),
      ...EXTRA_BASEMENT_PROPS.map(async (item) => {
        try {
          const raw = await loadRawGlb(item.url);
          if (item.isLed) enhanceLedPanelMaterials(raw);
          cloneNodeMaterials(raw, "geodata_cad_blueprint");
          raw.position.set(...item.pos);
          raw.rotation.y = item.rotY;
          raw.scale.set(...item.scale);
          raw.traverse((o) => {
            if (o.isMesh) {
              o.castShadow = !item.isLed;
              o.receiveShadow = true;
            }
          });
          basementGroup.add(raw);
        } catch {
          // Optional extra visual props
        }
      }),
    ]);

    hydronics = createHydronicSystem({ scene, hardwareGroups });
    applyXrayToHouse();

    if (provenanceCtrl && provenanceCtrl.isActive()) {
      provenanceCtrl.applyToObject(scene, "geodata_cad_blueprint");
    }
    markShadowsDirty();
  }

  function setCameraPreset(preset, durationMs = 1050) {
    if (!preset || !preset.position || !preset.target) return;
    orbitTween = null;
    const fromPos = camera.position.clone();
    const toPos = new THREE.Vector3(...preset.position);
    const fromTarget = controls.target.clone();
    const toTarget = new THREE.Vector3(...preset.target);
    const fromFov = camera.fov;
    const toFov = preset.fov_deg || preset.fov || 45;

    if (durationMs <= 0) {
      cameraTween = null;
      camera.position.copy(toPos);
      controls.target.copy(toTarget);
      camera.fov = toFov;
      camera.updateProjectionMatrix();
      controls.update();
      return;
    }

    cameraTween = {
      startMs: performance.now(),
      durationMs,
      fromPos,
      toPos,
      fromTarget,
      toTarget,
      fromFov,
      toFov,
    };
  }

  function smoothOrbit(degDelta = 35, durationMs = 2400) {
    cameraTween = null;
    const offset = camera.position.clone().sub(controls.target);
    const sph = new THREE.Spherical().setFromVector3(offset);
    orbitTween = {
      startMs: performance.now(),
      durationMs,
      startTheta: sph.theta,
      deltaTheta: THREE.MathUtils.degToRad(degDelta),
      phi: sph.phi,
      radius: sph.radius,
    };
  }

  function updateSolar(hourRow) {
    if (!hourRow || !Array.isArray(hourRow.sun_direction_xyz)) return;
    const [dx, dy, dz] = hourRow.sun_direction_xyz;
    const el = Number(hourRow.solar_elevation_deg ?? 0);
    const rad = Number(hourRow.shortwave_radiation_wm2 ?? 0);

    if (el > 0 && rad > 5) {
      sunLight.position.set(dx * 120, Math.max(18, dy * 120), dz * 120);
      sunLight.intensity = 1.15 + 1.45 * Math.min(1.0, rad / 820.0);
      const tWarm = Math.max(0.0, Math.min(1.0, (el - 8.0) / 34.0));
      sunLight.color.setRGB(1.0, 0.84 + 0.14 * tWarm, 0.66 + 0.28 * tWarm);
      hemiLight.intensity = 0.85 + 0.28 * tWarm;
      solarSunRig.position.set(dx * 85, Math.max(14, dy * 85), dz * 85);
      solarSunRig.visible = true;

      skyDome.mat.uniforms.uZenithColor.value.setRGB(
        0.28 + 0.04 * tWarm,
        0.48 + 0.1 * tWarm,
        0.74 + 0.08 * tWarm
      );
      skyDome.mat.uniforms.uMidColor.value.setRGB(
        0.58 + 0.06 * tWarm,
        0.72 + 0.07 * tWarm,
        0.86 + 0.05 * tWarm
      );
      skyDome.mat.uniforms.uHorizonColor.value.setRGB(0.9, 0.93, 0.96);
      skyDome.mat.uniforms.uSunDir.value.set(dx, Math.max(0.12, dy), dz).normalize();
      skyDome.mat.uniforms.uSunStrength.value = 0.6 + 0.45 * tWarm;
      scene.fog.color.setRGB(0.86, 0.91, 0.95);
    } else {
      // Crisp readable alpine blue-hour lighting (never pitch-black void)
      sunLight.position.set(-45, 72, 38);
      sunLight.intensity = 0.72;
      sunLight.color.setHex(0xcbdcf5);
      hemiLight.intensity = 0.78;
      solarSunRig.visible = false;

      skyDome.mat.uniforms.uZenithColor.value.setHex(0x284369);
      skyDome.mat.uniforms.uMidColor.value.setHex(0x4c6b94);
      skyDome.mat.uniforms.uHorizonColor.value.setHex(0x8aa2be);
      skyDome.mat.uniforms.uSunStrength.value = 0.0;
      scene.fog.color.setHex(0x8aa2be);
    }
    markShadowsDirty();
  }

  function updateThermalAndSolar(hourRow, dayMeta, verbundState) {
    updateSolar(hourRow);
    if (hydronics) {
      hydronics.updateData({
        hourRow,
        verbundScene: verbundState?.verbundScene || null,
        isVerbundMode: Boolean(verbundState?.isVerbundMode),
      });
    }
  }

  function hasCutawayForUrl(glbUrl) {
    const key = siteKeyFromUrl(glbUrl);
    const meta = assetsMeta[key];
    if (meta && "cutaway_box" in meta) {
      return meta.cutaway_box !== null;
    }
    return key === "context_neighborhood";
  }

  async function setVerbundScene(payload) {
    if (!payload) return;
    clearSwissTwin();
    const glbUrl = payload.glb_url || "/assets/glb/context_neighborhood.glb";
    const supportsBasementCutaway = hasCutawayForUrl(glbUrl);

    await mountSiteUrl(glbUrl);

    if (supportsBasementCutaway) {
      houseGroup.visible = true;
      basementGroup.visible = true;
      if (hydronics) hydronics.setVisible(true);
      verbundHubGroup.visible = false;
      erdsondeGroup.visible = payload.scene_id === "basel_erdsonde";
      setShadowFrustum(55);
    } else {
      houseGroup.visible = false;
      basementGroup.visible = false;
      if (hydronics) hydronics.setVisible(false);
      erdsondeGroup.visible = false;
      verbundHubGroup.visible = true;

      const dumpFrac = payload.three_way_valve?.dump_fraction || 0;
      const colHex = dumpFrac > 0.01 ? 0xc81e1e : 0x15803d;
      beaconMat.color.setHex(colHex);
      beaconMat.emissive.setHex(colHex);
      beaconRingMat.color.setHex(colHex);
      beaconRingMat.emissive.setHex(colHex);
      setShadowFrustum(140);
    }

    updateCutawayHouseVisibility();

    if (payload.camera_preset) {
      setCameraPreset(payload.camera_preset);
    }
    if (hydronics) {
      hydronics.updateData({
        hourRow: null,
        verbundScene: payload,
        isVerbundMode: true,
      });
    }
    if (workerRack) {
      workerRack.setAshraeBadge(payload);
    }
  }

  function clearSwissTwin() {
    while (fleetTwinGroup.children.length > 0) {
      fleetTwinGroup.remove(fleetTwinGroup.children[0]);
    }
    fleetTwinBeads = [];
    fleetTwinGroup.visible = false;
    siteGroup.visible = true;
    vegetationGroup.visible = true;
    markShadowsDirty();
  }

  async function showSwissTwin(node) {
    if (!node) return;
    if (node.glb_scene_key === "context_neighborhood") {
      clearSwissTwin();
      await resetToBaselHouse();
      setXray(false);
      setCameraPreset({ position: [44, 28, 52], target: [0, 3, 0], fov_deg: 42 }, 0);
      return;
    }
    if (node.glb_scene_key === "verbund_st_jakob" || node.glb_scene_key === "verbund_davos") {
      clearSwissTwin();
      await mountSiteUrl(`/assets/glb/${node.glb_scene_key}.glb`);
      houseGroup.visible = false;
      basementGroup.visible = false;
      if (hydronics) hydronics.setVisible(false);
      erdsondeGroup.visible = false;
      verbundHubGroup.visible = true;
      setShadowFrustum(140);
      setCameraPreset(
        node.glb_scene_key === "verbund_st_jakob"
          ? { position: [70, 40, 72], target: [0, 3, 0], fov_deg: 42 }
          : { position: [72, 44, 74], target: [0, 5, 0], fov_deg: 42 },
        0
      );
      markShadowsDirty();
      return;
    }
    while (fleetTwinGroup.children.length > 0) {
      fleetTwinGroup.remove(fleetTwinGroup.children[0]);
    }
    siteGroup.visible = false;
    vegetationGroup.visible = false;
    houseGroup.visible = false;
    basementGroup.visible = false;
    verbundHubGroup.visible = false;
    erdsondeGroup.visible = false;
    if (hydronics) hydronics.setVisible(false);

    const twinMesh = buildProceduralSwissTwin(node, {
      meadowSkirtTex,
      terrainDetailBump,
      asphaltTex,
      roofTex,
      waterBumpTex,
      vegTemplates,
    });
    fleetTwinGroup.add(twinMesh);
    fleetTwinGroup.visible = true;
    fleetTwinBeads = twinMesh.userData.beads || [];
    setShadowFrustum(42);
    setCameraPreset({ position: [21, 13, 25], target: [0.5, 3.2, 0], fov_deg: 40 }, 0);
    markShadowsDirty();
  }

  async function resetToBaselHouse() {
    clearSwissTwin();
    await mountSiteUrl("/assets/glb/context_neighborhood.glb");
    houseGroup.visible = true;
    basementGroup.visible = true;
    if (hydronics) hydronics.setVisible(true);
    verbundHubGroup.visible = false;
    erdsondeGroup.visible = false;
    setShadowFrustum(55);
    updateCutawayHouseVisibility();
  }

  function attachControllers({ provenance, workerRackCtrl }) {
    provenanceCtrl = provenance;
    workerRack = workerRackCtrl;
    if (provenanceCtrl && provenanceCtrl.isActive()) {
      provenanceCtrl.applyToObject(scene, "geodata_cad_blueprint");
    }
    markShadowsDirty();
  }

  function handleResize() {
    const w = Math.max(1, window.innerWidth);
    const h = Math.max(1, window.innerHeight);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  }
  window.addEventListener("resize", handleResize);

  let rafId = null;
  let prevMs = performance.now();
  let lastRenderMs = 0;
  const startMs = prevMs;
  const tmpSph = new THREE.Spherical();
  const tmpVec = new THREE.Vector3();

  function animate(nowMs) {
    rafId = requestAnimationFrame(animate);
    const swissOverlay = document.querySelector("[data-testid='swiss-network-overlay']");
    const swissRtx = document.querySelector("[data-testid='swiss-rtx-stage']");
    const cfgOverlay = document.querySelector("[data-testid='configurator-overlay']");
    const hlOverlay = document.querySelector("[data-testid='heatledger-overlay']");
    if (
      (swissOverlay && !swissOverlay.hidden) ||
      (swissRtx && !swissRtx.hidden) ||
      (cfgOverlay && !cfgOverlay.hidden) ||
      (hlOverlay && !hlOverlay.hidden)
    ) {
      return;
    }
    if (isSoftwareGl && nowMs - lastRenderMs < 55) {
      return;
    }
    lastRenderMs = nowMs;
    const dt = Math.min(0.05, Math.max(0.001, (nowMs - prevMs) / 1000.0));
    prevMs = nowMs;
    const elapsedS = (nowMs - startMs) / 1000.0;

    if (cameraTween) {
      const tRaw = Math.min(1.0, (nowMs - cameraTween.startMs) / cameraTween.durationMs);
      // Quintic smootherstep for cinema-smooth acceleration & deceleration
      const t = tRaw * tRaw * tRaw * (tRaw * (tRaw * 6 - 15) + 10);
      camera.position.lerpVectors(cameraTween.fromPos, cameraTween.toPos, t);
      controls.target.lerpVectors(cameraTween.fromTarget, cameraTween.toTarget, t);
      const nextFov = cameraTween.fromFov + (cameraTween.toFov - cameraTween.fromFov) * t;
      if (Math.abs(camera.fov - nextFov) > 0.01) {
        camera.fov = nextFov;
        camera.updateProjectionMatrix();
      }
      if (tRaw >= 1.0) {
        cameraTween = null;
      }
    } else if (orbitTween) {
      const tRaw = Math.min(1.0, (nowMs - orbitTween.startMs) / orbitTween.durationMs);
      const t = tRaw * tRaw * tRaw * (tRaw * (tRaw * 6 - 15) + 10);
      tmpSph.set(orbitTween.radius, orbitTween.phi, orbitTween.startTheta + orbitTween.deltaTheta * t);
      tmpVec.setFromSpherical(tmpSph).add(controls.target);
      camera.position.copy(tmpVec);
      if (tRaw >= 1.0) {
        orbitTween = null;
      }
    }

    waterBumpTex.offset.x = (elapsedS * 0.018) % 1;
    waterBumpTex.offset.y = (elapsedS * 0.011) % 1;
    if (fleetTwinGroup.visible && fleetTwinBeads.length > 0) {
      for (const bead of fleetTwinBeads) {
        const u = (bead.userData.phase + elapsedS * 0.32) % 1;
        bead.userData.curve.getPointAt(u, bead.position);
      }
    }
    if (hydronics) hydronics.tick(elapsedS, dt);
    if (workerRack) workerRack.tick(elapsedS, dt);
    controls.update();
    renderer.render(scene, camera);
  }
  rafId = requestAnimationFrame(animate);

  function getDebugState() {
    const hardwareBounds = {};
    for (const [key, grp] of Object.entries(hardwareGroups)) {
      if (!grp) continue;
      grp.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(grp);
      hardwareBounds[key] = {
        min: [box.min.x, box.min.y, box.min.z],
        max: [box.max.x, box.max.y, box.max.z],
      };
    }
    return {
      currentSiteUrl,
      houseVisible: houseGroup.visible,
      basementVisible: basementGroup.visible,
      xrayActive,
      hardwareBounds,
      dumpDebug: hydronics ? hydronics.getDumpDebugState() : null,
    };
  }

  function dispose() {
    if (rafId !== null) cancelAnimationFrame(rafId);
    window.removeEventListener("resize", handleResize);
    if (hydronics) hydronics.dispose();
    controls.dispose();
    renderer.dispose();
  }

  return {
    scene,
    camera,
    controls,
    renderer,
    siteGroup,
    vegetationGroup,
    houseGroup,
    basementGroup,
    hardwareGroups,
    initAssets,
    setXray,
    setCameraPreset,
    smoothOrbit,
    updateSolar,
    updateThermalAndSolar,
    setVerbundScene,
    resetToBaselHouse,
    showSwissTwin,
    clearSwissTwin,
    attachControllers,
    getDebugState,
    dispose,
  };
}
