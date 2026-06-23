// ─── Inverse kinematics (IK) arm control ──────────────────────────────────────
// Drives the arm in Cartesian space. While ikOn, a 50 ms tick integrates operator
// input into ikState (y/z/pitch), clamps it to IK.min/max, and streams
// {cmd:"ik",...} to the robot (relayed to the Teensy by ws_server.py). Input is
// rate-based / hold-to-move:
//   • keyboard mode → ←/→ = Y, ↓/↑ = Z, Ctrl/Shift = pitch (independent of WASD)
//   • controller mode → right stick = Y/Z, left stick = pitch
// State + config (ikOn, ikState, ikKeys, IK) live in state.js.

function clampIK(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// Per-click step for the clickable IK nudge buttons, scaled by the active servo
// step (the 1-5 pips) so one coarseness control drives both servo and IK nudges.
// These are the per-servo-step base amounts (y/z in cm, pitch in deg).
const IK_NUDGE_YZ    = 1;
const IK_NUDGE_PITCH = 5;

// Step an IK axis by one click in `dir` (±1). The base step is multiplied by the
// current SERVO_STEP, mirroring how the servo +/- nudges scale. Clamped to the
// same bounds as the arrow-key/stick integration.
function nudgeIK(axis, dir) {
  if (!ikOn) return;
  const step = SERVO_STEP;
  if (axis === "y")          ikState.y     = clampIK(ikState.y     + dir * IK_NUDGE_YZ    * step, IK.min.y,     IK.max.y);
  else if (axis === "z")     ikState.z     = clampIK(ikState.z     + dir * IK_NUDGE_YZ    * step, IK.min.z,     IK.max.z);
  else if (axis === "pitch") ikState.pitch = clampIK(ikState.pitch + dir * IK_NUDGE_PITCH * step, IK.min.pitch, IK.max.pitch);
  else return;
  updateIKReadout();
  sendIK();
}

// ─── Toggle ───────────────────────────────────────────────────────────────────
// Mirrors toggleAvatar/setAvatar in main.js. Enabling sends the current pose
// immediately; disabling stops sending so the arm holds its last position.
function toggleIK() {
  setIK(!ikOn);
}

function setIK(on) {
  ikOn = !!on;
  updateModeUI();
  saveSettings();
  if (ikOn) { updateIKReadout(); sendIK(); }
  log("IK arm: " + (ikOn ? "on" : "off"), "info");
}

function sendIK() {
  send({
    cmd: "ik",
    y: +ikState.y.toFixed(1),
    z: +ikState.z.toFixed(1),
    pitch: Math.round(ikState.pitch),
  });
}

function updateIKReadout() {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("ik-y", ikState.y.toFixed(1));
  set("ik-z", ikState.z.toFixed(1));
  set("ik-pitch", Math.round(ikState.pitch));
}

// ─── Keyboard input ───────────────────────────────────────────────────────────
// Arrow keys + Shift/Ctrl, active only while ikOn. Separate from WASD so the base
// keeps driving. preventDefault on arrows stops the page from scrolling.
function ikKeyFlag(e, down) {
  switch (e.key) {
    case "ArrowRight": ikKeys.yInc = down; break;
    case "ArrowLeft":  ikKeys.yDec = down; break;
    case "ArrowUp":    ikKeys.zInc = down; break;
    case "ArrowDown":  ikKeys.zDec = down; break;
    case "Shift":      ikKeys.pInc = down; break;
    case "Control":    ikKeys.pDec = down; break;
    default: return false;
  }
  return true;
}

document.addEventListener("keydown", e => {
  if (!ikOn) return;
  if (e.target && e.target.tagName === "INPUT") return;
  if (ikKeyFlag(e, true) && e.key.startsWith("Arrow")) e.preventDefault();
});

document.addEventListener("keyup", e => {
  ikKeyFlag(e, false);   // always clear, even if IK was toggled off mid-press
});

// ─── Integration tick ─────────────────────────────────────────────────────────
function ikAxis(v) {
  return Math.abs(v) < CONTROLLER.deadzone ? 0 : v;
}

let ikLastSent = 0;
function ikTick() {
  if (!ikOn) return;
  const dt = 0.05;   // matches the 50 ms interval
  let dy = 0, dz = 0, dp = 0;

  if (controlMode === "keyboard") {
    dy = (ikKeys.yInc ? 1 : 0) - (ikKeys.yDec ? 1 : 0);
    dz = (ikKeys.zInc ? 1 : 0) - (ikKeys.zDec ? 1 : 0);
    dp = (ikKeys.pInc ? 1 : 0) - (ikKeys.pDec ? 1 : 0);
  } else if (controlMode === "controller") {
    const gp = (typeof activeGamepad === "function") ? activeGamepad() : null;
    const ax = gp && gp.axes ? gp.axes : [];
    dy =  ikAxis(ax[2] || 0);   // right stick X → Y
    dz = -ikAxis(ax[3] || 0);   // right stick Y → Z (up = +)
    dp = -ikAxis(ax[1] || 0);   // left stick Y  → pitch (up = +)
  }

  if (dy === 0 && dz === 0 && dp === 0) return;

  ikState.y     = clampIK(ikState.y     + dy * IK.rateYZ    * dt, IK.min.y,     IK.max.y);
  ikState.z     = clampIK(ikState.z     + dz * IK.rateYZ    * dt, IK.min.z,     IK.max.z);
  ikState.pitch = clampIK(ikState.pitch + dp * IK.ratePitch * dt, IK.min.pitch, IK.max.pitch);

  updateIKReadout();
  const now = Date.now();
  if (now - ikLastSent >= 1000 / SEND_HZ) { sendIK(); ikLastSent = now; }
}

setInterval(ikTick, 50);
