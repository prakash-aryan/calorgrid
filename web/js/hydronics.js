import * as THREE from "three";

const TANK_VERTEX_SHADER = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const TANK_FRAGMENT_SHADER = `
  uniform float uSocFrac;
  uniform float uTime;
  uniform float uProvenanceActive;
  uniform vec3 uProvenanceColor;
  varying vec2 vUv;

  void main() {
    if (uProvenanceActive > 0.5) {
      gl_FragColor = vec4(uProvenanceColor, 0.92);
      return;
    }

    float soc = clamp(uSocFrac, 0.0, 1.0);
    vec3 coldCol = vec3(0.05, 0.55, 0.92);
    vec3 midCol = vec3(0.96, 0.62, 0.04);
    vec3 hotCol = vec3(0.94, 0.30, 0.10);

    vec3 fluidCol = mix(coldCol, midCol, smoothstep(0.0, 0.55, vUv.y));
    fluidCol = mix(fluidCol, hotCol, smoothstep(0.50, 1.0, vUv.y));
    float shimmer = 0.045 * sin(vUv.y * 24.0 - uTime * 3.0);
    fluidCol = clamp(fluidCol + shimmer, 0.0, 1.0);

    vec3 emptyCol = vec3(0.10, 0.14, 0.20);
    float tick = step(0.96, fract(vUv.y * 4.0)) * 0.18;
    emptyCol += vec3(tick);

    float filled = step(vUv.y, soc);
    vec3 col = mix(emptyCol, fluidCol, filled);

    float meniscus = 1.0 - smoothstep(0.0, 0.022, abs(vUv.y - soc));
    col = mix(col, vec3(1.0, 0.96, 0.85), meniscus * 0.85);

    float alpha = mix(0.45, 0.94, filled);
    gl_FragColor = vec4(col, alpha);
  }
`;

function computeAnchorBox(group, fallbackCenter, fallbackSize) {
  if (group) {
    group.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(group);
    if (!box.isEmpty()) {
      const center = new THREE.Vector3();
      box.getCenter(center);
      return { box, center };
    }
  }
  const half = new THREE.Vector3(
    fallbackSize[0] * 0.5,
    fallbackSize[1] * 0.5,
    fallbackSize[2] * 0.5
  );
  const center = new THREE.Vector3(...fallbackCenter);
  const box = new THREE.Box3(center.clone().sub(half), center.clone().add(half));
  return { box, center };
}

function makePipeTube(points, radius, material, name) {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)));
  const geom = new THREE.TubeGeometry(curve, 52, radius, 10, false);
  const mesh = new THREE.Mesh(geom, material);
  mesh.name = name;
  mesh.userData.provenanceCategory = "official_benchmark";
  mesh.userData.originalMaterial = material;
  return { mesh, curve };
}

export function createHydronicSystem({ scene, hardwareGroups }) {
  const root = new THREE.Group();
  root.name = "hydronics_system_root";
  root.userData.provenanceCategory = "official_benchmark";
  scene.add(root);

  const rackA = computeAnchorBox(hardwareGroups?.rack, [-1.0, -2.03, -1.35], [1.08, 2.29, 0.6]);
  const cduA = computeAnchorBox(hardwareGroups?.cdu, [0.4, -2.12, -1.35], [0.9, 2.12, 1.25]);
  const tankA = computeAnchorBox(hardwareGroups?.tank, [1.85, -2.53, -1.35], [1.26, 1.3, 1.11]);
  const valveA = computeAnchorBox(hardwareGroups?.valve, [2.9, -2.13, -1.1], [0.7, 0.75, 0.2]);

  const supplyMat = new THREE.MeshStandardMaterial({
    color: 0xf59e0b,
    emissive: 0xd97706,
    emissiveIntensity: 0.45,
    roughness: 0.35,
    metalness: 0.4,
  });

  const returnMat = new THREE.MeshStandardMaterial({
    color: 0x0284c7,
    emissive: 0x0369a1,
    emissiveIntensity: 0.3,
    roughness: 0.38,
    metalness: 0.4,
  });

  const verbundMat = new THREE.MeshStandardMaterial({
    color: 0x334155,
    emissive: 0x06b6d4,
    emissiveIntensity: 0.08,
    roughness: 0.4,
    metalness: 0.35,
  });

  const dumpMat = new THREE.MeshStandardMaterial({
    color: 0x27272a,
    emissive: 0xef4444,
    emissiveIntensity: 0.0,
    roughness: 0.35,
    metalness: 0.35,
  });

  // Primary supply curve: Rack -> CDU -> Tank -> Valve -> Plumbing Riser
  const supplyPoints = [
    [rackA.box.max.x - 0.1, -1.45, rackA.center.z],
    [cduA.box.min.x + 0.05, -1.5, cduA.center.z],
    [cduA.box.max.x - 0.05, -1.45, cduA.center.z],
    [tankA.center.x, -1.92, tankA.center.z],
    [tankA.box.max.x - 0.05, -2.15, tankA.center.z + 0.1],
    [valveA.center.x, -2.25, valveA.center.z],
    [3.22, -0.35, valveA.center.z],
    [3.22, 1.85, valveA.center.z],
  ];
  const supplyPipe = makePipeTube(supplyPoints, 0.042, supplyMat, "hydronic_pipe_supply");
  root.add(supplyPipe.mesh);

  // Return curve: Plumbing Riser -> Tank bottom -> CDU -> Rack
  const returnPoints = [
    [3.04, 1.85, -0.85],
    [3.04, -2.85, -0.85],
    [tankA.box.min.x + 0.15, -2.85, tankA.center.z],
    [cduA.box.max.x - 0.05, -2.25, cduA.center.z],
    [rackA.box.max.x - 0.1, -2.15, rackA.center.z],
  ];
  const returnPipe = makePipeTube(returnPoints, 0.038, returnMat, "hydronic_pipe_return");
  root.add(returnPipe.mesh);

  // Verbund branch: Valve -> front external manifold stub
  const verbundPoints = [
    [valveA.center.x, -2.25, valveA.center.z],
    [3.3, -2.45, -0.1],
    [3.3, -2.45, 2.35],
  ];
  const verbundPipe = makePipeTube(verbundPoints, 0.036, verbundMat, "hydronic_pipe_verbund_branch");
  root.add(verbundPipe.mesh);

  const verbundCollarMat = verbundMat.clone();
  const verbundCollar = new THREE.Mesh(
    new THREE.TorusGeometry(0.11, 0.025, 10, 20),
    verbundCollarMat
  );
  verbundCollar.position.set(3.3, -2.45, 2.35);
  verbundCollar.name = "hydronic_pipe_verbund_collar";
  verbundCollar.userData.provenanceCategory = "official_benchmark";
  verbundCollar.userData.originalMaterial = verbundCollarMat;
  root.add(verbundCollar);

  // July Dump Bypass: Valve -> outer wall dry-cooler stack
  const dumpPoints = [
    [valveA.center.x, -2.25, valveA.center.z],
    [3.48, -2.25, -1.8],
    [4.55, -1.0, -1.8],
    [4.55, 0.45, -1.8],
  ];
  const dumpPipe = makePipeTube(dumpPoints, 0.04, dumpMat, "hydronic_pipe_dump_bypass");
  root.add(dumpPipe.mesh);

  const dumpBeaconMat = dumpMat.clone();
  const dumpBeacon = new THREE.Mesh(
    new THREE.BoxGeometry(0.58, 0.42, 0.38),
    dumpBeaconMat
  );
  dumpBeacon.position.set(4.55, 0.65, -1.8);
  dumpBeacon.name = "hydronic_dump_radiator";
  dumpBeacon.userData.provenanceCategory = "official_benchmark";
  dumpBeacon.userData.originalMaterial = dumpBeaconMat;
  root.add(dumpBeacon);

  // Front-mounted Tank Stratification Sight Glass
  const tankShaderMat = new THREE.ShaderMaterial({
    vertexShader: TANK_VERTEX_SHADER,
    fragmentShader: TANK_FRAGMENT_SHADER,
    transparent: true,
    depthWrite: false,
    uniforms: {
      uSocFrac: { value: 0.5 },
      uTime: { value: 0.0 },
      uProvenanceActive: { value: 0.0 },
      uProvenanceColor: { value: new THREE.Color("#f59e0b") },
    },
  });

  const sightGlass = new THREE.Mesh(
    new THREE.CylinderGeometry(0.11, 0.11, 1.04, 24),
    tankShaderMat
  );
  sightGlass.position.set(tankA.center.x, tankA.box.min.y + 0.64, tankA.box.max.z + 0.04);
  sightGlass.name = "tank_thermal_fluid";
  sightGlass.userData.provenanceCategory = "official_benchmark";
  sightGlass.userData.originalMaterial = tankShaderMat;
  root.add(sightGlass);

  // 3-Way Valve Diverter Indicator
  const valveIndicatorMat = new THREE.MeshStandardMaterial({
    color: 0x38bdf8,
    emissive: 0x38bdf8,
    emissiveIntensity: 0.65,
    roughness: 0.3,
  });
  const valveIndicator = new THREE.Mesh(
    new THREE.ConeGeometry(0.09, 0.24, 12),
    valveIndicatorMat
  );
  valveIndicator.position.set(valveA.center.x, valveA.box.max.y + 0.14, valveA.center.z);
  valveIndicator.name = "valve_diverter_indicator";
  valveIndicator.userData.provenanceCategory = "official_benchmark";
  valveIndicator.userData.originalMaterial = valveIndicatorMat;
  root.add(valveIndicator);

  // Basement 1-node lumped air light
  const basementAirLight = new THREE.PointLight(0x38bdf8, 0.6, 8.0, 1.5);
  basementAirLight.position.set(0.0, -1.4, -1.3);
  basementAirLight.name = "basement_air_node_indicator";
  basementAirLight.userData.provenanceCategory = "official_benchmark";
  root.add(basementAirLight);

  // InstancedMesh Flow Particles (4 draw calls)
  const particleGeo = new THREE.SphereGeometry(0.055, 8, 8);
  function makeParticleMesh(count, colorHex, name) {
    const mat = new THREE.MeshBasicMaterial({ color: colorHex, toneMapped: false });
    const inst = new THREE.InstancedMesh(particleGeo, mat, count);
    inst.name = name;
    inst.userData.provenanceCategory = "official_benchmark";
    inst.userData.originalMaterial = mat;
    inst.frustumCulled = false;
    root.add(inst);
    return inst;
  }

  const supplyParticles = makeParticleMesh(20, 0xfbbf24, "hydronic_particles_supply");
  const returnParticles = makeParticleMesh(20, 0x38bdf8, "hydronic_particles_return");
  const verbundParticles = makeParticleMesh(14, 0x22c55e, "hydronic_particles_verbund");
  const dumpParticles = makeParticleMesh(16, 0xef4444, "hydronic_particles_dump");

  const state = {
    flowLMin: 1.5,
    supplyActive: true,
    verbundActive: false,
    dumpActive: false,
    targetValveAngle: 0.0,
    phase: 0.0,
  };

  const dummyPos = new THREE.Vector3();
  const dummyMat = new THREE.Matrix4();

  function updateData({ hourRow, verbundScene, isVerbundMode }) {
    const row = hourRow || {};
    const socKwh = typeof row.tank_soc_kwh === "number" ? row.tank_soc_kwh : 0.0;
    tankShaderMat.uniforms.uSocFrac.value = Math.max(0.0, Math.min(1.0, socKwh / 35.0));

    const flow = typeof row.loop_flow_l_per_min === "number" ? row.loop_flow_l_per_min : 0.0;
    const srvKw = typeof row.server_power_kw === "number" ? row.server_power_kw : 0.0;
    const dumpKw = typeof row.heat_dumped_kw === "number" ? row.heat_dumped_kw : 0.0;
    const secKw = typeof row.heat_used_secondary_kw === "number" ? row.heat_used_secondary_kw : 0.0;

    const valve = verbundScene?.three_way_valve || {};
    const secFrac = typeof valve.secondary_fraction === "number" ? valve.secondary_fraction : 0.0;
    const dumpFrac = typeof valve.dump_fraction === "number" ? valve.dump_fraction : 0.0;

    state.flowLMin = isVerbundMode
      ? Math.max(flow, verbundScene?.primary_flow_l_per_min || 2.0)
      : flow;
    state.supplyActive = srvKw > 1e-4 || flow > 1e-4 || isVerbundMode;
    state.verbundActive = (isVerbundMode && secFrac > 0.01) || secKw > 1e-4;
    state.dumpActive = isVerbundMode ? dumpFrac > 0.01 : dumpKw > 1e-4;

    if (state.dumpActive) {
      state.targetValveAngle = -0.9;
      valveIndicatorMat.color.setHex(0xef4444);
      valveIndicatorMat.emissive.setHex(0xef4444);
    } else if (state.verbundActive) {
      state.targetValveAngle = 0.9;
      valveIndicatorMat.color.setHex(0x22c55e);
      valveIndicatorMat.emissive.setHex(0x22c55e);
    } else {
      state.targetValveAngle = 0.0;
      valveIndicatorMat.color.setHex(0x38bdf8);
      valveIndicatorMat.emissive.setHex(0x38bdf8);
    }

    if (state.verbundActive) {
      verbundMat.color.setHex(0x06b6d4);
      verbundMat.emissiveIntensity = 0.75;
      verbundCollarMat.color.setHex(0x22c55e);
      verbundCollarMat.emissiveIntensity = 0.85;
    } else {
      verbundMat.color.setHex(0x334155);
      verbundMat.emissiveIntensity = 0.06;
      verbundCollarMat.color.setHex(0x334155);
      verbundCollarMat.emissiveIntensity = 0.06;
    }

    if (!state.dumpActive) {
      dumpMat.color.setHex(0x27272a);
      dumpMat.emissiveIntensity = 0.0;
      dumpBeaconMat.color.setHex(0x27272a);
      dumpBeaconMat.emissiveIntensity = 0.0;
    } else {
      dumpMat.color.setHex(0xef4444);
      dumpBeaconMat.color.setHex(0xef4444);
    }

    const retTemp = typeof row.return_temp_c === "number" ? row.return_temp_c : 30.0;
    const retNorm = Math.max(0.0, Math.min(1.0, (retTemp - 30.0) / 25.0));
    returnMat.color.setRGB(0.02 + 0.25 * retNorm, 0.52 - 0.1 * retNorm, 0.78 - 0.2 * retNorm);

    const airTemp = typeof row.basement_air_temp_c === "number" ? row.basement_air_temp_c : 14.0;
    const airNorm = Math.max(0.0, Math.min(1.0, (airTemp - 10.0) / 15.0));
    basementAirLight.color.setRGB(0.2 + 0.75 * airNorm, 0.65 - 0.1 * airNorm, 0.95 - 0.7 * airNorm);
    basementAirLight.intensity = 0.45 + 0.9 * airNorm;

    supplyParticles.visible = state.supplyActive;
    returnParticles.visible = state.supplyActive;
    verbundParticles.visible = state.verbundActive;
    dumpParticles.visible = state.dumpActive;
  }

  function updateCurveInstances(inst, curve, count, phaseOffset) {
    if (!inst.visible) return;
    for (let i = 0; i < count; i += 1) {
      const t = (state.phase + phaseOffset + i / count) % 1.0;
      curve.getPointAt(t, dummyPos);
      dummyMat.makeTranslation(dummyPos.x, dummyPos.y, dummyPos.z);
      inst.setMatrixAt(i, dummyMat);
    }
    inst.instanceMatrix.needsUpdate = true;
  }

  function tick(elapsedS, dt) {
    tankShaderMat.uniforms.uTime.value = elapsedS;
    const speed = state.flowLMin > 0.01 ? Math.min(1.4, Math.max(0.18, state.flowLMin / 8.0)) : 0.0;
    state.phase = (state.phase + dt * speed * 0.32) % 1.0;

    valveIndicator.rotation.z += (state.targetValveAngle - valveIndicator.rotation.z) * Math.min(1.0, dt * 8.0);

    if (state.dumpActive) {
      const pulse = 1.0 + 0.65 * Math.sin(elapsedS * 6.0);
      dumpMat.emissiveIntensity = pulse;
      dumpBeaconMat.emissiveIntensity = pulse;
    }

    updateCurveInstances(supplyParticles, supplyPipe.curve, 20, 0.0);
    updateCurveInstances(returnParticles, returnPipe.curve, 20, 0.25);
    updateCurveInstances(verbundParticles, verbundPipe.curve, 14, 0.12);
    updateCurveInstances(dumpParticles, dumpPipe.curve, 16, 0.4);
  }

  function setVisible(visible) {
    root.visible = Boolean(visible);
  }

  function getDumpDebugState() {
    return {
      dumpActive: state.dumpActive,
      dumpParticlesVisible: dumpParticles.visible,
      dumpColorHex: "#" + dumpMat.color.getHexString(),
    };
  }

  function dispose() {
    particleGeo.dispose();
  }

  return {
    root,
    updateData,
    tick,
    setVisible,
    getDumpDebugState,
    dispose,
  };
}
