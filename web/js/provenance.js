import * as THREE from "three";

const DEFAULT_CATEGORY_COLORS = {
  measured_pid: "#22c55e",
  official_benchmark: "#f59e0b",
  geodata_cad_blueprint: "#38bdf8",
  pilot_reality_sensitivity: "#a855f7",
};

export function cloneNodeMaterials(root, defaultCategory = "geodata_cad_blueprint") {
  if (!root) return;
  if (!root.userData.provenanceCategory) {
    root.userData.provenanceCategory = defaultCategory;
  }
  root.traverse((obj) => {
    if (!obj.isMesh || !obj.material) return;
    if (Array.isArray(obj.material)) {
      obj.material = obj.material.map((m) => m.clone());
      obj.userData.originalMaterial = obj.material;
      obj.userData.origStates = obj.material.map((m) => ({
        opacity: m.opacity,
        transparent: m.transparent,
        depthWrite: m.depthWrite,
      }));
    } else {
      obj.material = obj.material.clone();
      obj.userData.originalMaterial = obj.material;
      obj.userData.origOpacity = obj.material.opacity;
      obj.userData.origTransparent = obj.material.transparent;
      obj.userData.origDepthWrite = obj.material.depthWrite;
    }
  });
}

function makeProvenanceMaterial(srcMat, colorHex) {
  if (srcMat.isShaderMaterial) {
    return srcMat;
  }
  const mat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(colorHex),
    emissive: new THREE.Color(colorHex),
    emissiveIntensity: 0.24,
    roughness: 0.55,
    metalness: 0.12,
    transparent: Boolean(srcMat.transparent),
    opacity: typeof srcMat.opacity === "number" ? srcMat.opacity : 1.0,
    depthWrite: srcMat.depthWrite !== undefined ? srcMat.depthWrite : true,
    side: srcMat.side ?? THREE.FrontSide,
  });
  return mat;
}

export function createProvenanceController({ scene, provenanceData, legendEl, onChange }) {
  let active = false;
  let data = provenanceData || null;
  const meshGroupMap = new Map();

  function rebuildGroupMap() {
    meshGroupMap.clear();
    const cats = data?.categories || {};
    for (const [catId, meta] of Object.entries(cats)) {
      const groups = meta?.mesh_groups || [];
      for (const name of groups) {
        meshGroupMap.set(name, catId);
      }
    }
  }
  rebuildGroupMap();

  function getCategoryColor(catId) {
    const hex = data?.categories?.[catId]?.color_hex || DEFAULT_CATEGORY_COLORS[catId] || "#38bdf8";
    return hex;
  }

  function resolveCategory(obj, fallback = "geodata_cad_blueprint") {
    let cur = obj;
    while (cur && cur !== scene) {
      if (cur.userData && cur.userData.provenanceCategory) {
        return cur.userData.provenanceCategory;
      }
      if (cur.name && meshGroupMap.has(cur.name)) {
        return meshGroupMap.get(cur.name);
      }
      cur = cur.parent;
    }
    return fallback;
  }

  function applyMeshProvenance(obj, enable, fallbackCategory) {
    if (obj.userData?.skipProvenance) return;
    if (obj.isSprite && typeof obj.userData?.setProvenanceHighlight === "function") {
      const catId = resolveCategory(obj, fallbackCategory);
      obj.userData.setProvenanceHighlight(enable, getCategoryColor(catId));
      return;
    }
    if (!obj.isMesh || !obj.material) return;

    if (!obj.userData.originalMaterial) {
      obj.userData.originalMaterial = obj.material;
    }
    const orig = obj.userData.originalMaterial;

    if (enable) {
      const catId = resolveCategory(obj, fallbackCategory);
      const colorHex = getCategoryColor(catId);

      if (orig.isShaderMaterial && orig.uniforms && "uProvenanceActive" in orig.uniforms) {
        orig.uniforms.uProvenanceActive.value = 1.0;
        if (orig.uniforms.uProvenanceColor?.value) {
          orig.uniforms.uProvenanceColor.value.set(colorHex);
        }
        return;
      }

      if (!obj.userData.provenanceMaterial) {
        obj.userData.provenanceMaterial = Array.isArray(orig)
          ? orig.map((m) => makeProvenanceMaterial(m, colorHex))
          : makeProvenanceMaterial(orig, colorHex);
      }

      const provMat = obj.userData.provenanceMaterial;
      if (Array.isArray(provMat) && Array.isArray(orig)) {
        provMat.forEach((pm, idx) => {
          const om = orig[idx];
          pm.color.set(colorHex);
          pm.emissive.set(colorHex);
          pm.transparent = Boolean(om.transparent);
          pm.opacity = typeof om.opacity === "number" ? om.opacity : 1.0;
          pm.depthWrite = om.depthWrite !== undefined ? om.depthWrite : true;
          pm.needsUpdate = true;
        });
      } else if (!Array.isArray(provMat) && !Array.isArray(orig)) {
        provMat.color.set(colorHex);
        provMat.emissive.set(colorHex);
        provMat.transparent = Boolean(orig.transparent);
        provMat.opacity = typeof orig.opacity === "number" ? orig.opacity : 1.0;
        provMat.depthWrite = orig.depthWrite !== undefined ? orig.depthWrite : true;
        provMat.needsUpdate = true;
      }
      obj.material = provMat;
    } else {
      if (orig.isShaderMaterial && orig.uniforms && "uProvenanceActive" in orig.uniforms) {
        orig.uniforms.uProvenanceActive.value = 0.0;
        return;
      }
      obj.material = orig;
    }
  }

  function applyToObject(root, defaultCategory = "geodata_cad_blueprint") {
    if (!root) return;
    if (!root.userData.provenanceCategory) {
      root.userData.provenanceCategory = defaultCategory;
    }
    root.traverse((obj) => {
      applyMeshProvenance(obj, active, defaultCategory);
    });
  }

  function renderLegend() {
    if (!legendEl) return;
    const discEl = legendEl.querySelector("#provenance-disclaimer");
    const gridEl = legendEl.querySelector("#provenance-categories-grid");
    if (discEl && data?.blueprint_disclaimer) {
      discEl.textContent = data.blueprint_disclaimer;
    }
    if (gridEl && data?.categories) {
      const order = [
        "measured_pid",
        "official_benchmark",
        "geodata_cad_blueprint",
        "pilot_reality_sensitivity",
      ];
      gridEl.innerHTML = "";
      for (const catId of order) {
        const meta = data.categories[catId];
        if (!meta) continue;
        const item = document.createElement("div");
        item.className = "legend-item";
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = meta.color_hex || DEFAULT_CATEGORY_COLORS[catId];
        const body = document.createElement("div");
        const label = document.createElement("div");
        label.className = "legend-label";
        label.textContent = meta.label || catId;
        const desc = document.createElement("div");
        desc.className = "legend-desc";
        desc.textContent = meta.description || "";
        body.appendChild(label);
        body.appendChild(desc);
        item.appendChild(swatch);
        item.appendChild(body);
        gridEl.appendChild(item);
      }
    }
    legendEl.hidden = !active;
  }

  function setProvenanceData(nextData) {
    data = nextData || data;
    rebuildGroupMap();
    renderLegend();
    if (active && scene) {
      applyToObject(scene);
    }
  }

  function setActive(nextActive) {
    active = Boolean(nextActive);
    if (scene) {
      scene.traverse((obj) => {
        applyMeshProvenance(obj, active, "geodata_cad_blueprint");
      });
    }
    renderLegend();
    if (typeof onChange === "function") {
      onChange(active);
    }
  }

  function toggle() {
    setActive(!active);
    return active;
  }

  function isActive() {
    return active;
  }

  renderLegend();

  return {
    isActive,
    setActive,
    toggle,
    applyToObject,
    renderLegend,
    setProvenanceData,
    getCategoryColor,
  };
}
