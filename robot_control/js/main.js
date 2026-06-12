// ─── Sim glue, control-mode switching & boot ──────────────────────────────────
// Loaded LAST. Bridges the servo sliders to the 3D sim iframe, owns the
// keyboard/controller/avatar mode switch, and runs the startup sequence once
// every other module is defined.

// ─── 3D sim iframe ────────────────────────────────────────────────────────────
function postSimState(angles) {
  const iframe = document.getElementById('sim-iframe');
  if (!iframe || !iframe.contentWindow) return;
  iframe.contentWindow.postMessage({ type: 'state', angles, cal: SIM_CAL }, '*');
}

function pushSimFromSliders() {
  if (simFromUDPS) return;
  const angles = SERVOS.map((s, i) => {
    const slider = document.getElementById('sv-slider-' + s.id);
    return slider ? parseInt(slider.value) : SIM_INIT[SIM_KEYS[i]]?.tgt ?? 90;
  });
  simAngles = angles;
  postSimState(angles);
}

// Push sim state at a modest interval so it stays in sync even without a slider change
setInterval(() => {
  if (!simFromUDPS) pushSimFromSliders();
}, 200);

// ─── Control mode toggle ──────────────────────────────────────────────────────
// Avatar note: the avatar arm is driven server-side by AvatarBridge in
// ws_server.py. The client only toggles it on/off here ({cmd:"avatar"}); inbound
// "avatar_status" / "servo" echoes are handled in websocket.js.
function cycleControlMode() {
  const i = CONTROL_MODES.indexOf(controlMode);
  setControlMode(CONTROL_MODES[(i + 1) % CONTROL_MODES.length]);
}

function setControlMode(mode) {
  if (!CONTROL_MODES.includes(mode)) mode = "keyboard";
  controlMode = mode;
  // Safety stop: clear any held movement input from the previous mode
  keys.w = keys.a = keys.s = keys.d = false;
  ["w","a","s","d"].forEach(k => setKey(k, false));
  gpPrev = {};
  sendMotionNow();
  // Start/stop the server-side avatar arm bridge as we enter/leave avatar mode.
  send({ cmd: "avatar", state: mode === "avatar" ? 1 : 0 });
  updateModeUI();
  saveSettings();
  log("Control mode: " + mode, "info");
}

function updateModeUI() {
  const btn = document.getElementById("mode-toggle");
  if (btn) btn.textContent = MODE_LABELS[controlMode];
  const show = (id, on) => {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? "" : "none";
  };
  show("kb-visual", controlMode === "keyboard");
  show("gamepad-status", controlMode === "controller");
  show("avatar-placeholder", controlMode === "avatar");
  if (controlMode === "controller") updateGamepadStatusUI();
}

// ─── Webcam overlay ───────────────────────────────────────────────────────────
// Toggles the full-screen placeholder webcam view (replaces the dual CAM 1/CAM 2
// panes). The feed itself is a placeholder until the Pi 4 stream is wired in.
function toggleWebcam() {
  const overlay = document.getElementById("webcam-overlay");
  const btn = document.getElementById("webcam-toggle-btn");
  const toolbar = document.getElementById("webcam-toolbar");
  if (!overlay) return;
  const open = overlay.classList.toggle("open");
  if (btn) btn.classList.toggle("active", open);
  if (toolbar) toolbar.classList.toggle("open", open);
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const overlay = document.getElementById("webcam-overlay");
    if (overlay && overlay.classList.contains("open")) toggleWebcam();
  }
});

// ─── Global hotkeys ───────────────────────────────────────────────────────────
// Fire regardless of the active control mode (so the keyboard still works while
// on controller/avatar). Bindings live in HOTKEYS and are configured in
// Parameters. Ignored while typing in a form field or with a modifier held.
function matchesHotkey(eventKey, binding) {
  return !!binding && eventKey === binding.toLowerCase();
}

function handleHotkey(e) {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return false;
  if (e.ctrlKey || e.altKey || e.metaKey) return false;
  const k = (e.key || "").toLowerCase();
  if (!k) return false;
  if (matchesHotkey(k, HOTKEYS.cycleMode)) { cycleControlMode(); return true; }
  if (matchesHotkey(k, HOTKEYS.webcam))    { toggleWebcam();     return true; }
  for (const name of POSTURE_NAMES) {
    if (matchesHotkey(k, HOTKEYS.postures[name])) { applyPosture(name); return true; }
  }
  return false;
}

document.addEventListener("keydown", (e) => {
  if (handleHotkey(e)) e.preventDefault();
});

// ─── Boot ─────────────────────────────────────────────────────────────────────
log("Ready. Enter WebSocket URL and connect.", "info");
loadParamSettings();  // Async - loads from file in background

document.getElementById("ws-input").addEventListener("change", saveSettings);

loadSettings();
