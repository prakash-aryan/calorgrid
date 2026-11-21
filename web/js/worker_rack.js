import * as THREE from "three";

function drawPillCanvas(canvas, lines, borderHex, bgHex = "rgba(251, 250, 247, 0.96)") {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  ctx.fillStyle = bgHex;
  ctx.strokeStyle = borderHex;
  ctx.lineWidth = 8;
  ctx.beginPath();
  ctx.roundRect(6, 6, w - 12, h - 12, 10);
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = borderHex;
  ctx.font = "bold 28px monospace";
  ctx.textBaseline = "top";
  ctx.fillText(lines[0] || "", 24, 18);

  ctx.fillStyle = "#17171a";
  ctx.font = "700 22px sans-serif";
  if (lines[1]) ctx.fillText(lines[1], 24, 58);
  ctx.fillStyle = "#4a4a52";
  ctx.font = "600 20px monospace";
  if (lines[2]) ctx.fillText(lines[2], 24, 94);
}

export function createWorkerRackOverlay({ rackGroup, cduGroup, onStatusUpdate }) {
  let lastStatus = null;
  let fastMode = false;
  let pollTimer = null;
  let inFlight = false;
  let lastPidKey = "";
  let lastAshraeKey = "";
  let provenanceHighlightHex = null;

  // Rack Live Overlay
  const overlayGroup = new THREE.Group();
  overlayGroup.name = "live_rack_throttle_overlay";
  overlayGroup.userData.provenanceCategory = "measured_pid";
  if (rackGroup) {
    rackGroup.add(overlayGroup);
  }

  const bladeCount = 16;
  const bladeGeo = new THREE.BoxGeometry(0.72, 0.035, 0.018);
  const bladeMat = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
  const bladeInst = new THREE.InstancedMesh(bladeGeo, bladeMat, bladeCount);
  bladeInst.name = "rack_blade_leds";
  bladeInst.userData.provenanceCategory = "measured_pid";
  bladeInst.userData.originalMaterial = bladeMat;
  const dummy = new THREE.Object3D();
  const bladeColor = new THREE.Color();

  for (let i = 0; i < bladeCount; i += 1) {
    dummy.position.set(0.0, 0.25 + i * 0.115, 0.308);
    dummy.updateMatrix();
    bladeInst.setMatrixAt(i, dummy.matrix);
    bladeInst.setColorAt(i, new THREE.Color(0x334155));
  }
  bladeInst.instanceMatrix.needsUpdate = true;
  if (bladeInst.instanceColor) bladeInst.instanceColor.needsUpdate = true;
  overlayGroup.add(bladeInst);

  // Right-side vertical load bar
  const loadTrackMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.7 });
  const loadTrack = new THREE.Mesh(new THREE.BoxGeometry(0.075, 1.84, 0.016), loadTrackMat);
  loadTrack.position.set(0.44, 1.15, 0.304);
  loadTrack.userData.originalMaterial = loadTrackMat;
  overlayGroup.add(loadTrack);

  const loadBarGeo = new THREE.BoxGeometry(0.055, 1.8, 0.022);
  loadBarGeo.translate(0, 0.9, 0);
  const loadBarMat = new THREE.MeshStandardMaterial({
    color: 0x22c55e,
    emissive: 0x22c55e,
    emissiveIntensity: 0.85,
    roughness: 0.3,
  });
  const loadBar = new THREE.Mesh(loadBarGeo, loadBarMat);
  loadBar.position.set(0.44, 0.25, 0.309);
  loadBar.scale.y = 0.05;
  loadBar.userData.originalMaterial = loadBarMat;
  overlayGroup.add(loadBar);

  // Thermal plume motes above rack
  const plumeCount = 10;
  const plumeGeo = new THREE.SphereGeometry(0.04, 8, 8);
  const plumeMat = new THREE.MeshBasicMaterial({
    color: 0x22c55e,
    transparent: true,
    opacity: 0.7,
    toneMapped: false,
  });
  const plumeInst = new THREE.InstancedMesh(plumeGeo, plumeMat, plumeCount);
  plumeInst.visible = false;
  plumeInst.userData.originalMaterial = plumeMat;
  overlayGroup.add(plumeInst);

  // Worker PID Sprite Beacon
  const pidCanvas = document.createElement("canvas");
  pidCanvas.width = 512;
  pidCanvas.height = 144;
  const pidTex = new THREE.CanvasTexture(pidCanvas);
  const pidSpriteMat = new THREE.SpriteMaterial({
    map: pidTex,
    transparent: true,
    depthWrite: false,
    depthTest: false,
  });
  const pidSprite = new THREE.Sprite(pidSpriteMat);
  pidSprite.name = "worker_pid_beacon";
  pidSprite.renderOrder = 20;
  pidSprite.userData.provenanceCategory = "measured_pid";
  pidSprite.position.set(0.0, 2.6, 0.0);
  pidSprite.scale.set(1.7, 0.48, 1.0);
  if (rackGroup) {
    rackGroup.add(pidSprite);
  }

  function redrawPidSprite(status) {
    const st = status?.state || "idle";
    const pid = status?.pid ?? "-";
    const job = status?.job_type || "archive_ocr";
    const units = status?.units_completed ?? 0;
    const cpu = Math.round(status?.cpu_percent || 0);
    const scaledKw = status?.scaled_1kw?.reference_server_power_kw ?? 0.0;
    const key = `${st}|${pid}|${job}|${units}|${cpu}|${provenanceHighlightHex || ""}`;
    if (key === lastPidKey) return;
    lastPidKey = key;

    const border =
      provenanceHighlightHex ||
      (st === "running" ? "#22c55e" : st === "paused" ? "#f59e0b" : "#64748b");
    const head =
      st === "running"
        ? `PID ${pid} | RUNNING`
        : st === "paused"
          ? `PID ${pid} | SIGSTOP PAUSED`
          : "WORKER IDLE";
    const line2 = `${job} | ${scaledKw.toFixed(1)} kW eq`;
    const line3 = `CPU ${cpu}% | ${units} units`;
    drawPillCanvas(pidCanvas, [head, line2, line3], border);
    pidTex.needsUpdate = true;
  }

  pidSprite.userData.setProvenanceHighlight = (enabled, colorHex) => {
    provenanceHighlightHex = enabled ? colorHex : null;
    redrawPidSprite(lastStatus);
  };

  // ASHRAE W45 Badge & Pilot Reality Overlay on CDU
  const ashraeCanvas = document.createElement("canvas");
  ashraeCanvas.width = 512;
  ashraeCanvas.height = 140;
  const ashraeTex = new THREE.CanvasTexture(ashraeCanvas);
  const ashraeMat = new THREE.SpriteMaterial({
    map: ashraeTex,
    transparent: true,
    depthWrite: false,
    depthTest: false,
  });
  const ashraeSprite = new THREE.Sprite(ashraeMat);
  ashraeSprite.name = "ashrae_w45_thermal_badge";
  ashraeSprite.renderOrder = 20;
  ashraeSprite.userData.provenanceCategory = "pilot_reality_sensitivity";
  ashraeSprite.position.set(0.0, 2.42, 0.0);
  ashraeSprite.scale.set(1.65, 0.45, 1.0);
  if (cduGroup) {
    cduGroup.add(ashraeSprite);
  }

  const pilotCollarMat = new THREE.MeshBasicMaterial({ color: 0xa855f7, toneMapped: false });
  const pilotCollar = new THREE.Mesh(
    new THREE.RingGeometry(0.75, 0.85, 32),
    pilotCollarMat
  );
  pilotCollar.rotation.x = -Math.PI / 2;
  pilotCollar.position.set(0.0, 0.04, 0.0);
  pilotCollar.visible = false;
  pilotCollar.name = "pilot_reality_overlay";
  pilotCollar.userData.provenanceCategory = "pilot_reality_sensitivity";
  pilotCollar.userData.originalMaterial = pilotCollarMat;
  if (cduGroup) {
    cduGroup.add(pilotCollar);
  }

  function setAshraeBadge(verbundScene) {
    const cls = verbundScene?.ashrae_class || "W45";
    const compliant = verbundScene?.ashrae_w45_compliant !== false;
    const temp =
      verbundScene?.secondary_supply_temp_c ??
      verbundScene?.primary_supply_temp_c ??
      42.0;
    const key = `${cls}|${compliant}|${temp}`;
    if (key === lastAshraeKey) return;
    lastAshraeKey = key;

    const border = compliant ? "#a855f7" : "#f59e0b";
    const line1 = `ASHRAE ${cls} (${temp.toFixed(0)} C)`;
    const line2 = compliant ? "W45 Compliant <= 45 C" : "Exceeds W45 (> 45 C)";
    const line3 = verbundScene?.scene_id || "basel_erdsonde";
    drawPillCanvas(ashraeCanvas, [line1, line2, line3], border);
    ashraeTex.needsUpdate = true;
  }

  function setSensitivityHighlight(enabled) {
    pilotCollar.visible = Boolean(enabled);
  }

  redrawPidSprite(null);
  setAshraeBadge(null);

  function applyStatus(status) {
    lastStatus = status;
    redrawPidSprite(status);
    if (typeof onStatusUpdate === "function") {
      onStatusUpdate(status);
    }
    scheduleNextPoll();
  }

  async function pollOnce() {
    if (inFlight) return lastStatus;
    inFlight = true;
    try {
      const resp = await fetch("/api/v1/worker/status");
      if (resp.ok) {
        const data = await resp.json();
        applyStatus(data);
      }
    } catch {
      // Keep existing state on transient poll interruption
    } finally {
      inFlight = false;
    }
    return lastStatus;
  }

  function scheduleNextPoll() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
    }
    const st = lastStatus?.state || "idle";
    const delay = st === "running" || st === "paused" || fastMode ? 750 : 2200;
    pollTimer = setTimeout(pollOnce, delay);
  }

  function startPolling() {
    pollOnce();
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function setFastMode(enabled) {
    fastMode = Boolean(enabled);
    scheduleNextPoll();
  }

  function tick(elapsedS, dt) {
    const st = lastStatus?.state || "idle";
    const cpuNorm = Math.max(0.0, Math.min(1.0, (lastStatus?.cpu_percent || 0) / 100.0));
    const targetScaleY =
      st === "running" ? Math.max(0.35, cpuNorm) : st === "paused" ? 0.15 : 0.04;
    loadBar.scale.y += (targetScaleY - loadBar.scale.y) * Math.min(1.0, dt * 8.0);

    if (st === "running") {
      loadBarMat.color.setHex(0x22c55e);
      loadBarMat.emissive.setHex(0x22c55e);
      plumeInst.visible = true;
      for (let i = 0; i < bladeCount; i += 1) {
        const wave = 0.45 + 0.55 * Math.sin(elapsedS * (4.0 + cpuNorm * 8.0) - i * 0.65);
        bladeColor.setRGB(0.08 * wave, 0.85 * wave, 0.35 * wave);
        bladeInst.setColorAt(i, bladeColor);
      }
      for (let i = 0; i < plumeCount; i += 1) {
        const p = (elapsedS * 0.6 + i / plumeCount) % 1.0;
        dummy.position.set(
          Math.sin(i * 1.9 + elapsedS) * 0.22,
          2.3 + p * 0.8,
          Math.cos(i * 1.3) * 0.14
        );
        const s = 1.0 - p * 0.6;
        dummy.scale.set(s, s, s);
        dummy.updateMatrix();
        plumeInst.setMatrixAt(i, dummy.matrix);
      }
      plumeInst.instanceMatrix.needsUpdate = true;
    } else if (st === "paused") {
      loadBarMat.color.setHex(0xf59e0b);
      loadBarMat.emissive.setHex(0xf59e0b);
      plumeInst.visible = false;
      const pulse = 0.35 + 0.18 * Math.sin(elapsedS * 1.8);
      for (let i = 0; i < bladeCount; i += 1) {
        bladeColor.setRGB(0.95 * pulse, 0.62 * pulse, 0.05 * pulse);
        bladeInst.setColorAt(i, bladeColor);
      }
    } else {
      loadBarMat.color.setHex(0x475569);
      loadBarMat.emissive.setHex(0x1e293b);
      plumeInst.visible = false;
      for (let i = 0; i < bladeCount; i += 1) {
        bladeColor.setHex(0x334155);
        bladeInst.setColorAt(i, bladeColor);
      }
    }
    if (bladeInst.instanceColor) bladeInst.instanceColor.needsUpdate = true;
  }

  function getLastStatus() {
    return lastStatus;
  }

  function dispose() {
    stopPolling();
    bladeGeo.dispose();
    plumeGeo.dispose();
    loadBarGeo.dispose();
    pidTex.dispose();
    ashraeTex.dispose();
  }

  return {
    applyStatus,
    pollOnce,
    startPolling,
    stopPolling,
    setFastMode,
    setAshraeBadge,
    setSensitivityHighlight,
    tick,
    getLastStatus,
    dispose,
  };
}
