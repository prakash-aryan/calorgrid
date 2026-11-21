import * as THREE from "three";

function makeSolidMat(color, roughness = 0.78, metalness = 0.08, extras = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extras });
}

function makeGlowMat(color, emissive, intensity = 0.9, opacity = 1.0) {
  return new THREE.MeshStandardMaterial({
    color,
    emissive,
    emissiveIntensity: intensity,
    roughness: 0.28,
    metalness: 0.25,
    transparent: opacity < 1.0,
    opacity,
  });
}

function addBox(parent, w, h, d, x, y, z, mat, cast = true) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z);
  m.castShadow = cast;
  m.receiveShadow = true;
  parent.add(m);
  return m;
}

function addGableRoof(parent, width, depth, ridgeH, x, y, z, mat) {
  const shape = new THREE.Shape();
  shape.moveTo(-width / 2, 0);
  shape.lineTo(width / 2, 0);
  shape.lineTo(0, ridgeH);
  shape.closePath();
  const geo = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false });
  geo.translate(0, 0, -depth / 2);
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  parent.add(m);
  return m;
}

function addTreeCluster(parent, coords, conifer = false) {
  const trunkMat = makeSolidMat(0x5b422d, 0.92, 0.0);
  const leafMatA = makeSolidMat(conifer ? 0x264c34 : 0x3b6e35, 0.85, 0.0);
  const leafMatB = makeSolidMat(conifer ? 0x1d3d29 : 0x4b8040, 0.85, 0.0);
  coords.forEach(([x, z, s], idx) => {
    const g = new THREE.Group();
    g.position.set(x, 0, z);
    const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.18 * s, 0.26 * s, 2.2 * s, 7), trunkMat);
    trunk.position.y = 1.1 * s;
    trunk.castShadow = true;
    g.add(trunk);
    if (conifer) {
      for (let tier = 0; tier < 3; tier += 1) {
        const cone = new THREE.Mesh(
          new THREE.ConeGeometry((2.1 - tier * 0.45) * s, (3.2 - tier * 0.4) * s, 8),
          tier % 2 === 0 ? leafMatA : leafMatB
        );
        cone.position.y = (2.8 + tier * 1.65) * s;
        cone.castShadow = true;
        g.add(cone);
      }
    } else {
      const crown = new THREE.Mesh(new THREE.DodecahedronGeometry(2.1 * s, 1), idx % 2 === 0 ? leafMatA : leafMatB);
      crown.position.y = 3.8 * s;
      crown.scale.set(1.05, 0.9, 1.0);
      crown.castShadow = true;
      g.add(crown);
    }
    parent.add(g);
  });
}

function addWindowGrid(parent, x0, y0, z0, cols, rows, dx, dy, w, h, faceAxis = "z", mat) {
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const ox = (c - (cols - 1) / 2) * dx;
      const oy = y0 + r * dy;
      if (faceAxis === "z") {
        addBox(parent, w, h, 0.12, x0 + ox, oy, z0, mat, false);
      } else {
        addBox(parent, 0.12, h, w, x0, oy, z0 + ox, mat, false);
      }
    }
  }
}

function addComputePodAndLoop(parent, x, z, serversCount, sinkPos, stateColorHex) {
  const podGroup = new THREE.Group();
  podGroup.name = "twin_compute_plant";
  const podW = Math.min(6.4, 2.4 + serversCount * 0.22);
  const darkRack = makeSolidMat(0x1f2733, 0.42, 0.55);
  const glassFront = makeGlowMat(0x0f172a, stateColorHex, 0.72, 0.88);
  const pipeMat = makeGlowMat(stateColorHex, stateColorHex, 0.85, 0.95);
  const tankMat = makeSolidMat(0xcbd5e1, 0.34, 0.62);

  // Edge server enclosure / basement plant cutaway box
  addBox(podGroup, podW, 2.7, 2.5, x, 1.35, z, darkRack);
  addBox(podGroup, podW - 0.35, 2.15, 0.08, x, 1.4, z + 1.27, glassFront, false);

  // Individual server blades glowing inside
  const nBlades = Math.min(8, Math.max(2, Math.ceil(serversCount / 2)));
  const bladeMat = makeGlowMat(0x38bdf8, stateColorHex, 1.15);
  for (let i = 0; i < nBlades; i += 1) {
    const bx = x - (podW - 0.8) / 2 + ((i + 0.5) / nBlades) * (podW - 0.8);
    addBox(podGroup, 0.28, 1.65, 0.2, bx, 1.35, z + 1.12, bladeMat, false);
  }

  // Insulated buffer tank cylinder beside pod
  const tankR = 0.65 + Math.min(0.65, serversCount * 0.03);
  const tankH = 2.6 + Math.min(1.8, serversCount * 0.08);
  const tank = new THREE.Mesh(new THREE.CylinderGeometry(tankR, tankR, tankH, 20), tankMat);
  tank.position.set(x + podW / 2 + tankR + 0.35, tankH / 2, z);
  tank.castShadow = true;
  podGroup.add(tank);

  // Glowing hydronic supply pipe bridge from compute pod to building thermal sink
  const p0 = new THREE.Vector3(x + podW / 2 + tankR + 0.35, 1.7, z);
  const p1 = new THREE.Vector3(sinkPos[0], sinkPos[1], sinkPos[2]);
  const mid = new THREE.Vector3((p0.x + p1.x) / 2, Math.max(p0.y, p1.y) + 0.3, (p0.z + p1.z) / 2);
  const curve = new THREE.CatmullRomCurve3([p0, mid, p1]);
  const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 24, 0.16, 10, false), pipeMat);
  tube.name = "twin_thermal_pipe";
  podGroup.add(tube);

  // Animated heat pulse beads stored in userData for smooth animation
  const beads = [];
  const beadGeo = new THREE.SphereGeometry(0.26, 10, 10);
  const beadMat = makeGlowMat(0xffffff, stateColorHex, 1.5);
  for (let k = 0; k < 5; k += 1) {
    const b = new THREE.Mesh(beadGeo, beadMat);
    b.userData.curve = curve;
    b.userData.phase = k / 5;
    podGroup.add(b);
    beads.push(b);
  }
  podGroup.userData.beads = beads;
  parent.add(podGroup);
  return beads;
}

function addRealisticForest(parent, vegTemplates, isAlpine, isLakeside, seedBase = 17) {
  let seed = seedBase * 997 + 13;
  const rand = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
  const treeEntries = [];
  const shrubEntries = [];
  for (let i = 0; i < 62; i += 1) {
    const ang = (i / 62) * Math.PI * 2 + (rand() - 0.5) * 0.25;
    const rad = 24 + rand() * 58;
    const tx = Math.cos(ang) * rad;
    const tz = Math.sin(ang) * rad;
    if (isLakeside && tz > 24) continue;
    if (tx > 4 && tx < 34 && tz > 4 && tz < 34) continue; // Keep camera sightline open
    if (Math.abs(tz) < 5 && tx < -14) continue; // Keep road clear
    treeEntries.push([tx, 0, tz, 0.85 + rand() * 0.85, rand() * Math.PI * 2]);
  }
  for (let i = 0; i < 42; i += 1) {
    const ang = rand() * Math.PI * 2;
    const rad = 18 + rand() * 34;
    const sx = Math.cos(ang) * rad;
    const sz = Math.sin(ang) * rad;
    if (isLakeside && sz > 24) continue;
    if (sx > 2 && sx < 28 && sz > 2 && sz < 28) continue;
    shrubEntries.push([sx, 0, sz, 0.75 + rand() * 0.75, rand() * Math.PI * 2]);
  }

  if (vegTemplates && (vegTemplates.beech || vegTemplates.fir)) {
    const dummy = new THREE.Object3D();
    const tint = new THREE.Color();
    const addLayer = (parts, entries, palette, name) => {
      if (!parts || !parts.length || !entries.length) return;
      for (const part of parts) {
        const isFol = part.name.toLowerCase().includes("foliage");
        const mat = part.material.clone();
        mat.roughness = isFol ? 0.82 : 0.92;
        const inst = new THREE.InstancedMesh(part.geometry, mat, entries.length);
        inst.name = `${name}_${part.name}`;
        inst.receiveShadow = true;
        for (let i = 0; i < entries.length; i += 1) {
          const [x, y, z, sc, yaw] = entries[i];
          dummy.position.set(x, y, z);
          dummy.rotation.set(0, yaw, 0);
          dummy.scale.set(sc, sc * (0.9 + ((i * 23) % 15) * 0.01), sc);
          dummy.updateMatrix();
          inst.setMatrixAt(i, dummy.matrix);
          if (isFol && palette.length) {
            tint.setHex(palette[i % palette.length]);
            inst.setColorAt(i, tint);
          }
        }
        inst.instanceMatrix.needsUpdate = true;
        if (inst.instanceColor) inst.instanceColor.needsUpdate = true;
        parent.add(inst);
      }
    };
    const firs = treeEntries.filter((_, i) => (isAlpine ? i % 5 !== 0 : i % 3 === 0));
    const beeches = treeEntries.filter((_, i) => (isAlpine ? i % 5 === 0 : i % 3 !== 0));
    addLayer(vegTemplates.fir, firs, [0x4c7c48, 0x3b683b, 0x56844e], "twin_fir");
    addLayer(vegTemplates.beech, beeches, [0x588a46, 0x4b7a3d, 0x67944e], "twin_beech");
    addLayer(vegTemplates.shrub, shrubEntries, [0x558442, 0x4a773c, 0x638e49], "twin_shrub");
    return;
  }
  addTreeCluster(parent, treeEntries.slice(0, 16).map(([x, , z, s]) => [x, z, s]), isAlpine);
}

export function buildProceduralSwissTwin(node, shared = {}) {
  const root = new THREE.Group();
  root.name = `swiss_twin_${node.node_id}`;

  const state = node.live?.scheduler_state || "RUNNING_LOCAL";
  const stateColor =
    state === "DUMPING_HEAT"
      ? 0xef4444
      : state === "RECEIVING_MIGRATED"
        ? 0x38bdf8
        : state.includes("OFFLOADING")
          ? 0x60a5fa
          : 0xf97316;

  // Wide textured Swiss valley floor (real grass texture + detail bump, zero toy sphere horizon!)
  const isLakeside = node.twin_style === "lugano_villa" || node.twin_style === "geneva_block" || node.twin_style === "bern_rowhouse";
  const isAlpine = node.elevation_m >= 650;
  const grassTint = isAlpine ? 0x9fc28e : node.twin_style === "lugano_villa" ? 0xb4ca8e : 0xa5c892;

  const meadowMat = shared.meadowSkirtTex
    ? new THREE.MeshStandardMaterial({
        color: grassTint,
        map: shared.meadowSkirtTex,
        bumpMap: shared.terrainDetailBump || null,
        bumpScale: 0.14,
        roughness: 0.95,
        metalness: 0.0,
      })
    : makeSolidMat(isAlpine ? 0x4b7438 : 0x4e7c3c, 0.94, 0.0);

  const meadow = new THREE.Mesh(new THREE.CircleGeometry(650, 64), meadowMat);
  meadow.rotation.x = -Math.PI / 2;
  meadow.position.y = -0.05;
  meadow.receiveShadow = true;
  root.add(meadow);

  // Real textured gravel/stone courtyard pad + asphalt access road
  const plazaMat = new THREE.MeshStandardMaterial({
    color: 0xd0c8bc,
    bumpMap: shared.terrainDetailBump || null,
    bumpScale: 0.06,
    roughness: 0.88,
    metalness: 0.02,
  });
  addBox(root, 36, 0.14, 26, 0, 0.02, 0, plazaMat, false);

  const roadMat = shared.asphaltTex
    ? new THREE.MeshStandardMaterial({
        color: 0xffffff,
        map: shared.asphaltTex.map,
        bumpMap: shared.asphaltTex.bumpMap,
        bumpScale: 0.05,
        roughness: 0.88,
        metalness: 0.02,
      })
    : makeSolidMat(0x474c54, 0.9, 0.02);
  addBox(root, 90, 0.12, 5.4, -60, 0.01, 0, roadMat, false);

  if (isLakeside) {
    const waterColor = node.twin_style === "lugano_villa" ? 0x166ea8 : 0x1b7a8a;
    const lake = new THREE.Mesh(
      new THREE.PlaneGeometry(520, 260),
      new THREE.MeshStandardMaterial({
        color: waterColor,
        roughness: 0.15,
        metalness: 0.24,
        bumpMap: shared.waterBumpTex || null,
        bumpScale: 0.08,
      })
    );
    lake.rotation.x = -Math.PI / 2;
    lake.position.set(0, 0.03, 155);
    root.add(lake);
  }

  addRealisticForest(root, shared.vegTemplates, isAlpine, isLakeside, node.elevation_m || 400);

  const stuccoWarm = makeSolidMat(0xece2d2, 0.84, 0.02);
  const stuccoOchre = makeSolidMat(0xe6c896, 0.84, 0.02);
  const sandstone = makeSolidMat(0xc9bda9, 0.86, 0.02);
  const concrete = makeSolidMat(0xd4d6d8, 0.80, 0.05);
  const timber = makeSolidMat(0x865634, 0.84, 0.02);
  const clayRoof = shared.roofTex
    ? new THREE.MeshStandardMaterial({ color: 0xf4ebe5, map: shared.roofTex, roughness: 0.84, metalness: 0.02 })
    : makeSolidMat(0xa44d38, 0.82, 0.02);
  const slateRoof = makeSolidMat(0x475569, 0.72, 0.18);
  const glassWin = makeSolidMat(0x28435e, 0.18, 0.35);
  const glassClear = makeSolidMat(0x9bd6e0, 0.08, 0.15, { transparent: true, opacity: 0.42 });
  const cropMat = makeSolidMat(0x2f8f38, 0.86, 0.0);
  const metalMat = makeSolidMat(0x94a3b8, 0.35, 0.65);

  let beads = [];
  const style = node.twin_style;

  if (style === "emmental_dairy") {
    // Emmental Cheese Dairy Barn + Milk Pasteurisation Silos
    addBox(root, 18, 3.4, 11, -5, 1.7, -2, stuccoWarm);
    addBox(root, 18.4, 3.8, 11.4, -5, 5.2, -2, timber);
    addGableRoof(root, 13.6, 20.6, 5.2, -5, 7.0, -2, clayRoof);
    root.children[root.children.length - 1].rotation.y = Math.PI / 2;
    addWindowGrid(root, -5, 5.2, 3.78, 6, 1, 2.6, 2.0, 1.1, 1.5, "z", glassWin);
    for (const [sx, sz, h] of [[5.8, -4.2, 6.2], [5.8, -0.8, 5.2]]) {
      const silo = new THREE.Mesh(new THREE.CylinderGeometry(1.45, 1.45, h, 24), metalMat);
      silo.position.set(sx, h / 2, sz);
      silo.castShadow = true;
      root.add(silo);
    }
    beads = addComputePodAndLoop(root, 8.5, 5.5, node.servers_installed, [5.8, 2.2, -0.8], stateColor);
  } else if (style === "thurgau_greenhouse") {
    // 3-Bay Commercial Hydroponic Glasshouse + Crop Rows
    for (let b = 0; b < 3; b += 1) {
      const bz = -7.5 + b * 7.5;
      addBox(root, 20, 0.6, 6.6, 1.5, 0.3, bz, concrete);
      for (const rz of [-1.8, 0, 1.8]) {
        addBox(root, 18.2, 1.0, 1.1, 1.5, 1.0, bz + rz, cropMat, false);
      }
      addBox(root, 20, 2.8, 6.6, 1.5, 2.0, bz, glassClear, false);
      addGableRoof(root, 6.8, 20.2, 1.9, 1.5, 3.4, bz, glassClear);
      root.children[root.children.length - 1].rotation.y = Math.PI / 2;
      for (let px = -8; px <= 11; px += 3.8) {
        addBox(root, 0.14, 3.1, 0.14, px, 2.0, bz - 3.3, metalMat, false);
        addBox(root, 0.14, 3.1, 0.14, px, 2.0, bz + 3.3, metalMat, false);
      }
    }
    beads = addComputePodAndLoop(root, -12.5, 2.5, node.servers_installed, [-8.0, 1.2, 0.0], stateColor);
  } else if (style === "minergie_erdsonde") {
    // Modern Minergie Townhouse + Cutaway Geothermal Boreholes
    addBox(root, 14, 6.8, 9.5, -2, 3.4, -2, concrete);
    addBox(root, 14.3, 0.5, 9.8, -2, 7.0, -2, slateRoof);
    addBox(root, 5.5, 6.2, 0.25, -2, 3.4, 2.82, timber);
    addWindowGrid(root, -2, 1.9, 2.86, 4, 2, 3.1, 3.1, 1.9, 1.8, "z", glassWin);
    // Twin vertical geothermal Erdsonde boreholes glowing teal-blue into ground
    const boreMat = makeGlowMat(0x0284c7, 0x38bdf8, 1.1);
    for (const bx of [8.5, 11.2]) {
      const well = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.28, 6.5, 16), boreMat);
      well.position.set(bx, -2.5, -1.5);
      root.add(well);
      const collar = new THREE.Mesh(new THREE.TorusGeometry(0.95, 0.14, 10, 24), boreMat);
      collar.rotation.x = Math.PI / 2;
      collar.position.set(bx, 0.15, -1.5);
      root.add(collar);
    }
    beads = addComputePodAndLoop(root, 5.5, 5.2, node.servers_installed, [8.5, 0.4, -1.5], stateColor);
  } else if (style === "zurich_coop") {
    // 6-Storey Zurich Co-op Apartment Block + Rooftop PV + Glowing Riser
    addBox(root, 22, 15.5, 11.5, -2, 7.75, -2, stuccoWarm);
    addBox(root, 22.4, 0.6, 11.9, -2, 15.8, -2, slateRoof);
    addWindowGrid(root, -2, 2.2, 3.82, 7, 5, 2.8, 2.6, 1.6, 1.5, "z", glassWin);
    // Warm hydronic riser shaft visible along facade
    const riserMat = makeGlowMat(stateColor, stateColor, 0.9);
    addBox(root, 0.45, 14.5, 0.45, 9.2, 7.4, 1.2, riserMat, false);
    // Rooftop solar PV array
    const pvMat = makeSolidMat(0x1e3a5f, 0.24, 0.45);
    for (let i = -7; i <= 4; i += 3.6) {
      const pv = addBox(root, 2.8, 0.12, 6.8, i, 16.5, -2, pvMat);
      pv.rotation.z = 0.2;
    }
    beads = addComputePodAndLoop(root, 12.2, 5.5, node.servers_installed, [9.2, 2.0, 1.2], stateColor);
  } else if (style === "stgallen_hospital") {
    // Multi-Wing Cantonal Hospital Annex + 24/7 DHW Buffers
    addBox(root, 22, 11.5, 9.5, -2, 5.75, -3, concrete);
    addBox(root, 9.5, 8.8, 12.5, -8.5, 4.4, 3.5, concrete);
    addWindowGrid(root, -2, 2.2, 1.82, 7, 4, 2.6, 2.4, 1.8, 1.35, "z", glassWin);
    addWindowGrid(root, -2, 2.2, -7.82, 7, 4, 2.6, 2.4, 1.8, 1.35, "z", glassWin);
    addWindowGrid(root, 9.08, 2.2, -3, 3, 4, 2.6, 2.4, 1.8, 1.35, "x", glassWin);
    // Red Swiss cross architectural emblem on hospital facade
    const redMat = makeSolidMat(0xdc2626, 0.4, 0.1);
    addBox(root, 1.8, 0.55, 0.18, -8.5, 7.1, 9.82, redMat, false);
    addBox(root, 0.55, 1.8, 0.18, -8.5, 7.1, 9.82, redMat, false);
    for (const tz of [-4.8, -2.0, 0.8]) {
      const tank = new THREE.Mesh(new THREE.CylinderGeometry(1.05, 1.05, 4.6, 20), metalMat);
      tank.position.set(10.8, 2.3, tz);
      tank.castShadow = true;
      root.add(tank);
    }
    beads = addComputePodAndLoop(root, 9.8, 6.8, node.servers_installed, [10.8, 2.2, 0.8], stateColor);
  } else if (style === "lucerne_industrial") {
    // Sawtooth-Roof Swiss Commercial Laundry & Bakery Boiler Hall
    addBox(root, 22, 6.2, 13, -2, 3.1, -2, concrete);
    for (let sx = -10; sx <= 6; sx += 5.4) {
      addGableRoof(root, 5.4, 13.2, 2.3, sx, 6.2, -2, slateRoof);
    }
    addWindowGrid(root, -2, 3.5, 4.58, 6, 1, 3.2, 2.0, 2.2, 2.2, "z", glassWin);
    const chimney = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.72, 12.5, 16), metalMat);
    chimney.position.set(7.5, 6.25, -6.5);
    chimney.castShadow = true;
    root.add(chimney);
    beads = addComputePodAndLoop(root, 11.8, 4.2, node.servers_installed, [8.5, 2.5, -2.0], stateColor);
  } else if (style === "valais_winery") {
    // Valais Stone Winery + Stepped Fruit-Drying Kiln + Terraced Vineyard
    addBox(root, 16, 5.2, 10, -4, 2.6, -2, sandstone);
    addGableRoof(root, 11.2, 17.4, 3.4, -4, 5.2, -2, clayRoof);
    root.children[root.children.length - 1].rotation.y = Math.PI / 2;
    addBox(root, 7.5, 3.8, 7.5, 7.5, 1.9, -2.5, concrete);
    addWindowGrid(root, -4, 2.8, 3.08, 4, 1, 3.2, 2.0, 1.4, 1.8, "z", glassWin);
    // Vineyard vine rows in front terrace
    for (let vz = 8; vz <= 18; vz += 2.5) {
      addBox(root, 24, 1.0, 0.55, 0, 0.5, vz, cropMat);
    }
    beads = addComputePodAndLoop(root, 12.0, 5.2, node.servers_installed, [7.5, 2.0, -2.5], stateColor);
  } else if (style === "bern_rowhouse") {
    // Bern Matte Sandstone Old-Town Rowhouse Trio
    for (let i = -1; i <= 1; i += 1) {
      const bx = i * 7.4 - 2;
      const h = 9.2 + (i === 0 ? 1.4 : 0);
      addBox(root, 7.1, h, 9.6, bx, h / 2, -2, i === 0 ? sandstone : stuccoWarm);
      addGableRoof(root, 10.6, 7.2, 4.0, bx, h, -2, clayRoof);
      root.children[root.children.length - 1].rotation.y = Math.PI / 2;
      addWindowGrid(root, bx, 2.4, 2.88, 2, 3, 2.8, 2.4, 1.2, 1.5, "z", glassWin);
    }
    beads = addComputePodAndLoop(root, 11.2, 4.8, node.servers_installed, [5.4, 2.2, -1.5], stateColor);
  } else if (style === "lugano_villa") {
    // Ticino Warm Ochre Lakeside Residence
    addBox(root, 15, 7.4, 10.5, -2, 3.7, -2, stuccoOchre);
    addGableRoof(root, 12.0, 16.8, 2.6, -2, 7.4, -2, clayRoof);
    root.children[root.children.length - 1].rotation.y = Math.PI / 2;
    addWindowGrid(root, -2, 2.2, 3.32, 4, 2, 3.2, 3.0, 1.5, 1.8, "z", glassWin);
    addBox(root, 14, 0.35, 3.6, -2, 0.25, 5.0, sandstone);
    beads = addComputePodAndLoop(root, 9.8, 4.5, node.servers_installed, [5.2, 2.0, -1.0], stateColor);
  } else if (style === "geneva_block") {
    // Geneva Carouge Mixed-Use Limestone-Timber Residence
    addBox(root, 18, 10.5, 10.5, -2, 5.25, -2, stuccoWarm);
    addGableRoof(root, 11.4, 18.8, 2.8, -2, 10.5, -2, slateRoof);
    root.children[root.children.length - 1].rotation.y = Math.PI / 2;
    addWindowGrid(root, -2, 2.2, 3.32, 5, 3, 3.1, 2.8, 1.5, 1.8, "z", glassWin);
    beads = addComputePodAndLoop(root, 10.8, 4.8, node.servers_installed, [6.8, 2.2, -1.5], stateColor);
  } else {
    // Riehen Suburban Swiss Family Villa (default clean Swiss SFH)
    addBox(root, 12, 5.8, 9.5, -2, 2.9, -2, stuccoWarm);
    addGableRoof(root, 11.2, 13.6, 3.8, -2, 5.8, -2, clayRoof);
    root.children[root.children.length - 1].rotation.y = Math.PI / 2;
    addWindowGrid(root, -2, 1.9, 2.82, 3, 2, 3.2, 2.5, 1.4, 1.5, "z", glassWin);
    beads = addComputePodAndLoop(root, 7.6, 4.2, node.servers_installed, [3.8, 1.8, -1.5], stateColor);
  }

  root.userData.beads = beads;
  return root;
}
