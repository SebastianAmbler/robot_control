// ─── Sim glue, control-mode switching & boot ──────────────────────────────────
// Loaded LAST. Bridges the servo sliders to the 3D sim iframe, owns the
// keyboard/controller/avatar mode switch, and runs the startup sequence once
// every other module is defined.

// ─── 3D sim iframe ────────────────────────────────────────────────────────────
function postSimState(angles) {
  const iframe = document.getElementById('sim-iframe');
  if (!iframe || !iframe.contentWindow) return;
  // imuZero rides along so the sim always has the current IMU baseline.
  iframe.contentWindow.postMessage({ type: 'state', angles, cal: SIM_CAL, imuZero: IMU_ZERO }, '*');
}

// Persist the IMU zero to settings.json without disturbing other settings:
// read the file, merge in imuZero, write it back (POST overwrites the whole file).
function persistImuZero() {
  fetch("http://localhost:8766/api/settings")
    .then(r => r.json())
    .then(s => {
      s.imuZero = IMU_ZERO;
      return fetch("http://localhost:8766/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      });
    })
    .then(() => log(`IMU zero saved (roll ${IMU_ZERO.roll.toFixed(1)}, pitch ${IMU_ZERO.pitch.toFixed(1)})`, "info"))
    .catch(e => log("Error saving IMU zero: " + e.message, "warn"));
}

// The sim (index.html) reports a new baseline up here when the user re-zeros it.
window.addEventListener("message", (e) => {
  const m = e.data;
  if (!m || m.type !== "imuZeroChanged" || !m.offset) return;
  const roll = Number(m.offset.roll), pitch = Number(m.offset.pitch);
  if (!Number.isFinite(roll) || !Number.isFinite(pitch)) return;
  IMU_ZERO = { roll, pitch };   // update in memory first so the next state push is consistent
  persistImuZero();
});

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

// ─── IP cameras (CAM 1 / CAM 2) ────────────────────────────────────────────────
// Each pane shows the camera's own web page loaded into a sandboxed <iframe>,
// shrunk to fit via CSS transform: scale(). IP/host comes from the CAMERAS
// global (configured in Parameters, persisted to settings.json). The per-pane
// button connects/reconnects; the slider scales the feed.
const camState = [{ live: false }, { live: false }];

function setCamStatus(idx, cls, text) {
  const el = document.getElementById("camStat" + idx);
  if (!el) return;
  el.textContent = text;
  el.className = "cam-status-badge" + (cls ? " " + cls : "");
}

// Resize + scale the iframe: render at container/scale, then scale back down so
// the whole camera page is visible (lower % = more of the page in view).
function setCamZoom(idx, pct) {
  const p = parseInt(pct, 10);
  if (CAMERAS[idx]) CAMERAS[idx].zoom = p;
  const frame = document.getElementById("camImg" + idx);
  const wrap  = document.getElementById("camCell" + idx);
  const valEl = document.getElementById("camZoomVal" + idx);
  if (valEl) valEl.textContent = p + "%";
  if (!frame || !wrap) return;
  const scale = p / 100;
  const w = wrap.offsetWidth  || 500;
  const h = wrap.offsetHeight || 400;
  frame.style.width  = (w / scale) + "px";
  frame.style.height = (h / scale) + "px";
  frame.style.transform = "scale(" + scale + ")";
  frame.style.transformOrigin = "top left";
}

function connectCam(idx) {
  const cam = CAMERAS[idx] || {};
  const host = (cam.url || "").trim();
  if (!host) {
    disconnectCam(idx);
    setCamStatus(idx, "err", "NO URL");
    log(`Cam ${idx + 1}: no IP set (Parameters → Cameras)`, "warn");
    return;
  }
  const url = (cam.proto || "http://") + host;
  const frame = document.getElementById("camImg" + idx);
  const ph    = document.getElementById("camPh" + idx);
  const btn   = document.getElementById("camBtn" + idx);
  if (!frame) return;
  frame.onerror = () => {
    setCamStatus(idx, "err", "ERROR");
    if (btn) { btn.classList.add("err"); btn.classList.remove("live"); }
  };
  frame.src = url;
  frame.style.display = "block";
  if (ph) ph.style.display = "none";
  camState[idx].live = true;
  setCamZoom(idx, cam.zoom || 50);
  if (btn) { btn.textContent = "RECONNECT"; btn.classList.add("live"); btn.classList.remove("err"); }
  setCamStatus(idx, "live", "● LIVE");
  log(`Cam ${idx + 1} → ${url}`, "info");
}

function disconnectCam(idx) {
  const frame = document.getElementById("camImg" + idx);
  const ph    = document.getElementById("camPh" + idx);
  const btn   = document.getElementById("camBtn" + idx);
  if (frame) { frame.src = "about:blank"; frame.style.display = "none"; }
  if (ph) ph.style.display = "flex";
  if (btn) { btn.textContent = "CONNECT"; btn.classList.remove("live", "err"); }
  camState[idx].live = false;
  setCamStatus(idx, "", "OFFLINE");
}

// Button action: connect when offline, reconnect (reload the feed) when live.
function toggleCam(idx) {
  connectCam(idx);
}

// Sync sliders to saved zoom + size the (hidden) iframes before first connect.
function initCameras() {
  CAMERAS.forEach((c, i) => {
    const slider = document.getElementById("camZoom" + i);
    if (slider) slider.value = c.zoom;
    setCamZoom(i, c.zoom);
  });
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
