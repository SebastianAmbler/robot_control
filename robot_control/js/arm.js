// ─── Robot arm: servos & postures ─────────────────────────────────────────────
// Servo slider UI + per-servo control, posture presets (build/load/apply), and
// the ANGLES: feedback handler that mirrors the physical arm into the UI/sim.

// ─── Servo UI build ───────────────────────────────────────────────────────────
function buildServoUI() {
  const container = document.getElementById("servo-container");
  container.innerHTML = "";
  SERVOS.forEach(s => {
    const row = document.createElement("div");
    row.className = "servo-row";
    row.id = `sv-row-${s.id}`;
    row.innerHTML = `
      <div class="servo-header">
        <span class="servo-name">${s.name}</span>
        <span class="servo-angle" id="sv-angle-${s.id}">${s.home}°</span>
      </div>
      <div class="servo-hotkey">${s.key}</div>
      <div class="servo-range">${s.min}-${s.max}</div>
      <input type="hidden" id="sv-slider-${s.id}" value="${s.home}">`;
    container.appendChild(row);
  });
  updateActiveServoUI();
}

// ─── Servo send ───────────────────────────────────────────────────────────────
function sendServo(id, angle) {
  const ok = ws && ws.readyState === WebSocket.OPEN;
  send({ cmd: "servo", id, angle });
  log((ok ? "servo" : "servo [NO WS]") + "  id=" + id + "  angle=" + angle, ok ? "" : "warn");
}

function setServoAngle(id, angle, shouldSend = true) {
  const s = SERVOS.find(servo => servo.id === id);
  if (!s) return;
  const v = Math.max(s.min, Math.min(s.max, Math.round(angle)));
  const value = document.getElementById("sv-slider-" + id);
  const label = document.getElementById("sv-angle-" + id);
  if (value) value.value = v;
  if (label) {
    label.textContent = v + "°";
    label.classList.toggle("live", false);
  }
  if (shouldSend) {
    sendServo(id, v);
    pushSimFromSliders();
  }
}

function getServoAngle(id) {
  const s = SERVOS.find(servo => servo.id === id);
  const value = document.getElementById("sv-slider-" + id);
  const v = value ? parseInt(value.value) : NaN;
  return isNaN(v) ? (s ? s.home : 90) : v;
}

function postureLabel(name) {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function clampServoAngleByIndex(index, angle) {
  const servo = SERVOS[index];
  const fallback = DEFAULT_POSTURE_ANGLES[index] ?? 90;
  const numeric = angle === "" || angle === null || angle === undefined ? NaN : Number(angle);
  const raw = Number.isFinite(numeric) ? numeric : fallback;
  const rounded = Math.round(raw);
  if (!servo) return Math.max(0, Math.min(180, rounded));
  return Math.max(servo.min, Math.min(servo.max, rounded));
}

function clampPostureAngles(name, angles) {
  const source = Array.isArray(angles) ? angles : POSTURES[name];
  return SERVOS.map((_, i) => clampServoAngleByIndex(i, source?.[i]));
}

function createDefaultPostures() {
  return Object.fromEntries(
    POSTURE_NAMES.map(name => [name, DEFAULT_POSTURE_ANGLES.slice()])
  );
}

function loadPostures(settings) {
  const saved = settings?.postures || {};
  POSTURE_NAMES.forEach(name => {
    POSTURES[name] = clampPostureAngles(name, Array.isArray(saved[name]) ? saved[name] : DEFAULT_POSTURE_ANGLES);
  });
}

function updatePostureUI() {
  POSTURE_NAMES.forEach(name => {
    const btn = document.getElementById("posture-btn-" + name);
    if (btn) btn.classList.toggle("active", name === activePostureName);
  });
}

function buildPostureUI() {
  const container = document.getElementById("posture-buttons");
  if (!container) return;
  container.innerHTML = "";
  POSTURE_NAMES.forEach(name => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "posture-btn";
    btn.id = "posture-btn-" + name;
    btn.textContent = postureLabel(name);
    btn.title = "Apply " + postureLabel(name) + " posture";
    btn.addEventListener("click", () => applyPosture(name));
    container.appendChild(btn);
  });
  updatePostureUI();
}

function buildPostureParamUI() {
  const grid = document.getElementById("posture-param-grid");
  if (!grid) return;
  grid.innerHTML = "";

  const blank = document.createElement("div");
  blank.className = "posture-param-head";
  grid.appendChild(blank);
  SERVOS.forEach(s => {
    const head = document.createElement("div");
    head.className = "posture-param-head";
    head.textContent = "S" + s.id;
    head.title = s.name;
    grid.appendChild(head);
  });

  POSTURE_NAMES.forEach(name => {
    const label = document.createElement("div");
    label.className = "posture-param-label";
    label.textContent = postureLabel(name);
    grid.appendChild(label);

    const angles = clampPostureAngles(name, POSTURES[name]);
    POSTURES[name] = angles;
    SERVOS.forEach((servo, i) => {
      const input = document.createElement("input");
      input.type = "number";
      input.id = `posture-${name}-${i}`;
      input.min = servo.min;
      input.max = servo.max;
      input.step = 1;
      input.value = angles[i];
      input.title = `${postureLabel(name)} ${servo.name}`;
      grid.appendChild(input);
    });
  });
}

function readPosturesFromParams() {
  POSTURE_NAMES.forEach(name => {
    const values = SERVOS.map((_, i) => {
      const input = document.getElementById(`posture-${name}-${i}`);
      return input ? input.value : POSTURES[name]?.[i];
    });
    POSTURES[name] = clampPostureAngles(name, values);
  });
}

// ─── Servo limit (min/max) param UI ───────────────────────────────────────────
function buildServoLimitParamUI() {
  const grid = document.getElementById("servo-limit-grid");
  if (!grid) return;
  grid.innerHTML = "";

  ["Servo", "Min", "Max"].forEach(text => {
    const head = document.createElement("div");
    head.className = "servo-limit-head";
    head.textContent = text;
    grid.appendChild(head);
  });

  SERVOS.forEach(s => {
    const label = document.createElement("div");
    label.className = "servo-limit-label";
    label.textContent = s.name;
    grid.appendChild(label);

    const minInput = document.createElement("input");
    minInput.type = "number";
    minInput.id = "servo-min-" + s.id;
    minInput.min = 0;
    minInput.max = 180;
    minInput.step = 1;
    minInput.value = s.min;
    minInput.title = s.name + " minimum angle";
    grid.appendChild(minInput);

    const maxInput = document.createElement("input");
    maxInput.type = "number";
    maxInput.id = "servo-max-" + s.id;
    maxInput.min = 0;
    maxInput.max = 180;
    maxInput.step = 1;
    maxInput.value = s.max;
    maxInput.title = s.name + " maximum angle";
    grid.appendChild(maxInput);
  });
}

// Read limit inputs back into SERVOS, then re-clamp live state to the new bounds.
function readServoLimitsFromParams() {
  SERVOS.forEach(s => {
    const minEl = document.getElementById("servo-min-" + s.id);
    const maxEl = document.getElementById("servo-max-" + s.id);
    let min = minEl ? Math.round(Number(minEl.value)) : NaN;
    let max = maxEl ? Math.round(Number(maxEl.value)) : NaN;
    if (!Number.isFinite(min)) min = s.min;
    if (!Number.isFinite(max)) max = s.max;
    min = Math.max(0, Math.min(180, min));
    max = Math.max(0, Math.min(180, max));
    if (min < max) { s.min = min; s.max = max; }
  });
  // Re-clamp current slider positions and postures to the new limits.
  SERVOS.forEach(s => setServoAngle(s.id, getServoAngle(s.id), false));
  POSTURE_NAMES.forEach(name => { POSTURES[name] = clampPostureAngles(name, POSTURES[name]); });
}

function applyPosture(name) {
  if (!POSTURE_NAMES.includes(name)) return;
  const angles = clampPostureAngles(name, POSTURES[name]);
  POSTURES[name] = angles;
  activePostureName = name;
  updatePostureUI();
  angles.forEach((angle, i) => {
    const servo = SERVOS[i];
    if (servo) setServoAngle(servo.id, angle, true);
  });
  log("Posture applied: " + postureLabel(name), "info");
}

function updateActiveServoUI() {
  SERVOS.forEach(s => {
    const row = document.getElementById("sv-row-" + s.id);
    if (row) row.classList.toggle("active", s.id === activeServoId);
  });
}

function selectServoByKey(key) {
  const servo = SERVOS.find(s => s.key === key);
  if (!servo) return false;
  activeServoId = servo.id;
  updateActiveServoUI();
  log("Servo selected: " + servo.name + " (" + servo.key.toUpperCase() + ")", "info");
  return true;
}

function nudgeActiveServo(delta) {
  if (!activeServoId) return false;
  setServoAngle(activeServoId, getServoAngle(activeServoId) + delta);
  return true;
}

// ─── Update sliders from ANGLES: feedback ─────────────────────────────────────
// Format: ANGLES:90,90,90,25,20,85,122,90
function applyAngles(csv) {
  const parts = csv.split(",");
  const angles = [];
  SERVOS.forEach((s, i) => {
    const v = parseInt(parts[i]);
    if (isNaN(v)) { angles.push(simAngles[i] ?? 90); return; }
    angles.push(v);
    const slider = document.getElementById("sv-slider-" + s.id);
    const label  = document.getElementById("sv-angle-"  + s.id);
    if (slider) slider.value = v;
    if (!label) return;
    label.textContent = v + "°";
    label.classList.toggle("live", true);
  });
  // Forward to sim if UDPS source is enabled
  if (simFromUDPS) {
    simAngles = angles;
    postSimState(angles);
  }
}
