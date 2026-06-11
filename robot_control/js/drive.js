// ─── Drive / motor control ────────────────────────────────────────────────────
// Tracked-drive motion: gears, WASD keyboard input, the motion compute/send
// pipeline (shared with the gamepad in controller.js), motor lock, and the
// DDSM115 telemetry readout.

// ─── Gear UI build ────────────────────────────────────────────────────────────
function buildGearUI() {
  // Gear pips in left panel
  const gd = document.getElementById("gear-display");
  gd.innerHTML = "";
  for (let i = 0; i < GEARS.length; i++) {
    const pip = document.createElement("div");
    pip.className = "gear-pip" + (i === gear ? " active" : "");
    pip.id = "gpip-" + i;
    pip.textContent = i + 1;
    gd.appendChild(pip);
  }
  const lbl = document.createElement("div");
  lbl.id = "gear-label";
  lbl.className = "gear-label";
  lbl.textContent = "Gear";
  gd.appendChild(lbl);
}

function updateGearUI() {
  for (let i = 0; i < GEARS.length; i++) {
    document.getElementById("gpip-" + i).className = "gear-pip" + (i === gear ? " active" : "");
  }
}

// Note: buildGearUI() is called after settings are loaded in loadParamSettings()

// ─── Lock ─────────────────────────────────────────────────────────────────────
function toggleLock() {
  isLocked = !isLocked;
  send({ cmd: "lock", state: isLocked ? 1 : 0 });
  document.getElementById("lock-btn").className = isLocked ? "locked" : "";
  document.getElementById("lock-btn").textContent = isLocked ? "Unlock" : "Lock";
  log("Motors " + (isLocked ? "LOCKED" : "RELEASED"), isLocked ? "warn" : "info");
}

// ─── Key handling (send on change + keepalive) ────────────────────────────────

function setKey(k, active) {
  const el = document.getElementById("key-" + k);
  if (el) el.className = "key" + (active ? " active" : "");
}

// ─── Motion compute + send loop ───────────────────────────────────────────────
function computeMotion() {
  let lin = 0, ang = 0;
  if (keys.w) lin += 1;
  if (keys.s) lin -= 1;
  if (keys.a) ang += 1;
  if (keys.d) ang -= 1;

  // Normalize diagonal
  if (lin !== 0 && ang !== 0) {
    lin *= 0.707;
    ang *= 0.707;
  }

  const scale = GEARS[gear] / MAX_RPM;
  lin *= scale;
  ang *= scale;

  return { linear: lin, angular: ang };
}

function computeRPM(linear, angular) {
  const left  = Math.round((linear - angular / 2.0) * MAX_RPM);
  const right = Math.round((linear + angular / 2.0) * MAX_RPM);
  return {
    left:  Math.max(-MAX_RPM, Math.min(MAX_RPM, left)),
    right: Math.max(-MAX_RPM, Math.min(MAX_RPM, right))
  };
}

let lastSentMs = 0;
const KEEPALIVE_MS = 100;  // send at least every 200ms while moving to beat the 500ms watchdog

function updateRPMDisplay(left, right) {
  document.getElementById("rpm-l").textContent = left;
  document.getElementById("rpm-r").textContent = right;
  const isMoving = left !== 0 || right !== 0;
  document.getElementById("rpm-l").style.color = isMoving ? "var(--accent)" : "";
  document.getElementById("rpm-r").style.color = isMoving ? "var(--accent)" : "";
}

function sendMotion() {
  if (isLocked) return;
  const { linear, angular } = computeMotion();
  // Skip keepalive if socket buffer is backed up — stale motion packets are harmless to drop
  if (ws && ws.bufferedAmount > 512) return;
  send({ cmd: "motion", linear, angular });
  lastSentMs  = Date.now();
  lastLinear  = linear;
  lastAngular = angular;

  const rpm = computeRPM(linear, angular);
  const isMoving = linear !== 0 || angular !== 0;
  document.getElementById("rpm-l").textContent = rpm.left;
  document.getElementById("rpm-r").textContent = rpm.right;
  document.getElementById("rpm-l").style.color = isMoving ? "var(--accent)" : "";
  document.getElementById("rpm-r").style.color = isMoving ? "var(--accent)" : "";
}

// For key events: always send stop, skip motion if buffer is backed up (stale motion is harmless)
function sendMotionNow() {
  if (isLocked) return;
  const { linear, angular } = computeMotion();
  const isStopping = linear === 0 && angular === 0;

  // Drop backed-up motion packets — they're already stale.
  // But never drop a stop command; it must always get through immediately.
  if (!isStopping && ws && ws.bufferedAmount > 512) return;

  send({ cmd: "motion", linear, angular });
  lastSentMs  = Date.now();
  lastLinear  = linear;
  lastAngular = angular;

  const rpm = computeRPM(linear, angular);
  const isMoving = linear !== 0 || angular !== 0;
  document.getElementById("rpm-l").textContent = rpm.left;
  document.getElementById("rpm-r").textContent = rpm.right;
  document.getElementById("rpm-l").style.color = isMoving ? "var(--accent)" : "";
  document.getElementById("rpm-r").style.color = isMoving ? "var(--accent)" : "";
}

// Send immediately on any state change — bypasses buffer check
let autoBraked = false;  // true when auto-brake has engaged (separate from manual isLocked)

function cycleGear() {
  gear = (gear + 1) % GEARS.length;
  updateGearUI();
  sendMotionNow();
  log("Gear " + (gear+1) + " — " + GEARS[gear] + " RPM max");
}

// Shared by keyboard and gamepad: call after `keys` changes.
// Releases the auto-brake when movement starts, engages it when all input stops.
function applyMoveChange() {
  const anyKey = keys.w || keys.a || keys.s || keys.d;
  if (anyKey) {
    if (autoBrake && autoBraked && !isLocked) {
      autoBraked = false;
      send({ cmd: "lock", state: 0 });   // release brake
      setTimeout(sendMotionNow, 80);     // give ESP32 time to switch back to speed mode
    } else {
      sendMotionNow();
    }
  } else {
    if (autoBrake && !isLocked) {
      autoBraked = true;
      send({ cmd: "lock", state: 1 });
      updateRPMDisplay(0, 0);
    } else {
      sendMotionNow();
    }
  }
}

document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  const k = e.key.toLowerCase();
  if (selectServoByKey(k)) { e.preventDefault(); return; }
  if (k === "+" || k === "=") {
    if (nudgeActiveServo(SERVO_STEP)) e.preventDefault();
    return;
  }
  if (k === "-" || k === "_") {
    if (nudgeActiveServo(-SERVO_STEP)) e.preventDefault();
    return;
  }
  if (k === "escape") { activeServoId = null; updateActiveServoUI(); }
  if (controlMode !== "keyboard") return;  // WASD/gear only in keyboard mode
  let moved = false;
  if (k === "w") { keys.w = true; setKey("w", true); moved = true; }
  if (k === "a") { keys.a = true; setKey("a", true); moved = true; }
  if (k === "s") { keys.s = true; setKey("s", true); moved = true; }
  if (k === "d") { keys.d = true; setKey("d", true); moved = true; }
  if (moved) applyMoveChange();
  if (k === "g") cycleGear();
  if (["w","a","s","d"].includes(k)) e.preventDefault();
});

document.addEventListener("keyup", e => {
  if (controlMode !== "keyboard") return;
  const k = e.key.toLowerCase();
  let moved = false;
  if (k === "w") { keys.w = false; setKey("w", false); moved = true; }
  if (k === "a") { keys.a = false; setKey("a", false); moved = true; }
  if (k === "s") { keys.s = false; setKey("s", false); moved = true; }
  if (k === "d") { keys.d = false; setKey("d", false); moved = true; }
  if (moved) applyMoveChange();
});

// Keepalive: self-throttles via bufferedAmount guard in sendMotionNow
setInterval(() => {
  if (isLocked || autoBraked) return;
  const anyKey = keys.w || keys.a || keys.s || keys.d;
  if (anyKey && Date.now() - lastSentMs >= KEEPALIVE_MS) sendMotionNow();
}, 50);

// ─── Motor telemetry ──────────────────────────────────────────────────────────
// tor is torque current as signed int16: -32767~32767 = -8A~8A
// Torque constant: 0.75 Nm/A  →  Nm = raw * (8 / 32767) * 0.75
const TOR_TO_NM = (8 / 32767) * 0.75;  // ≈ 0.0001831 Nm per raw unit
// Thresholds in Nm:
//   Rated torque  0.96 Nm  (yellow)
//   Stall torque  2.0  Nm  (red)

function colorFor(val, warn, crit) {
  const abs = Math.abs(val);
  if (abs >= crit) return "var(--danger)";
  if (abs >= warn) return "var(--warn)";
  return "";
}

function updateMotor(obj) {
  const id = obj.id;
  if (id < 1 || id > 4) return;
  const el = s => document.getElementById("m" + id + "-" + s);

  // SPD — negate for M2 and M4 (reversed polarity)
  const spdEl = el("spd");
  if (spdEl) spdEl.textContent = (id === 2 || id === 4) ? -obj.spd : obj.spd;

  // TOR — convert raw to N·m, yellow at rated torque, red at stall torque
  // negate for M2 and M4 (reversed polarity)
  const torEl = el("tor");
  if (torEl) {
    let nm = obj.tor * TOR_TO_NM;
    if (id === 2 || id === 4) nm = -nm;
    torEl.textContent = nm.toFixed(2) + "Nm";
    torEl.style.color = colorFor(Math.abs(nm), THRESHOLDS.tor.warn, THRESHOLDS.tor.crit);
  }

  // TMP — yellow at 60C, red at 75C
  const tmpEl = el("tmp");
  if (tmpEl) {
    tmpEl.textContent = obj.temp + "C";
    tmpEl.style.color = colorFor(obj.temp, THRESHOLDS.tmp.warn, THRESHOLDS.tmp.crit);
  }

  // Card border reflects worst condition
  const card = document.getElementById("mc" + id);
  if (card) {
    const torNm = Math.abs(obj.tor) * TOR_TO_NM;
    const isCrit = torNm >= THRESHOLDS.tor.crit || obj.temp >= THRESHOLDS.tmp.crit;
    const isWarn = !isCrit && (torNm >= THRESHOLDS.tor.warn || obj.temp >= THRESHOLDS.tmp.warn);
    card.style.borderColor = isCrit ? "var(--danger)" : isWarn ? "var(--warn)" : "";
  }
}
