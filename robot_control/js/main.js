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
  fetch("/api/settings")
    .then(r => r.json())
    .then(s => {
      s.imuZero = IMU_ZERO;
      return fetch("/api/settings", {
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
  updateModeUI();
  saveSettings();
  log("Control mode: " + mode, "info");
}

// ─── Avatar toggle ────────────────────────────────────────────────────────────
// The avatar arm is driven server-side by AvatarBridge in ws_server.py and runs
// independently of the drive control mode. The client only flips it on/off here
// ({cmd:"avatar"}); inbound "avatar_status" / "servo" echoes are handled in
// websocket.js. The bridge is re-armed on (re)connect from ws.onopen.
function toggleAvatar() {
  setAvatar(!avatarOn);
}

function setAvatar(on) {
  avatarOn = !!on;
  send({ cmd: "avatar", state: avatarOn ? 1 : 0 });
  updateModeUI();
  saveSettings();
  log("Avatar arm: " + (avatarOn ? "on" : "off"), "info");
}

// Apply the server's authoritative avatar on/off state (sent on connect and
// whenever the physical toggle button on the avatar board flips it). Mirrors
// setAvatar()'s UI update but does NOT send {cmd:"avatar"} back to the
// server — that command is what changes state in the first place, so echoing
// it here would just bounce straight back as another avatar_sync.
function applyAvatarSync(on) {
  if (avatarOn === on) return;
  avatarOn = on;
  updateModeUI();
  saveSettings();
  log("Avatar arm: " + (avatarOn ? "on" : "off") + " (synced from board)", "info");
}

function updateModeUI() {
  const btn = document.getElementById("mode-toggle");
  if (btn) btn.textContent = MODE_LABELS[controlMode];
  const avBtn = document.getElementById("avatar-toggle");
  if (avBtn) {
    avBtn.textContent = "Avatar: " + (avatarOn ? "On" : "Off");
    avBtn.classList.toggle("active", avatarOn);
  }
  const ikBtn = document.getElementById("ik-toggle");
  if (ikBtn) {
    ikBtn.textContent = "IK: " + (ikOn ? "On" : "Off");
    ikBtn.classList.toggle("active", ikOn);
  }
  const show = (id, on) => {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? "" : "none";
  };
  show("kb-visual", controlMode === "keyboard");
  show("gamepad-status", controlMode === "controller");
  show("avatar-placeholder", avatarOn);
  show("ik-readout", ikOn);
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
// Toggles the full-screen webcam view (replaces the dual CAM 1/CAM 2 panes) and
// drives the live Raspberry Pi 4 MJPEG stream. The stream only runs while the
// overlay is open — stopping it on close frees camera bandwidth and avoids a
// frozen last frame on reopen.
//
// Memory: an MJPEG (multipart/x-mixed-replace) stream rendered in an <img> makes
// Chromium accumulate decoded-frame memory for as long as the connection stays
// open, so a long webcam session steadily grows the tab's RAM. Instead we pull
// the stream with fetch(), split out each JPEG frame ourselves (same SOI/EOI
// scan as open_mjpeg_latest in ws_server.py), and decode only the newest one via
// createImageBitmap() → drawImage() → bitmap.close(). Closing each bitmap frees
// its decoded pixels immediately, so RAM stays flat no matter how long the feed
// runs — and there's no periodic teardown/flicker to hide the leak.

// Live MJPEG stream from the Pi 4 (Camera Module 3). Change IP/port here only.
const WEBCAM_STREAM_URL = "http://192.168.1.167:8000/stream.mjpg";

let webcamRetryTimer = null;
let webcamAbort      = null;   // AbortController for the active fetch, or null
let webcamGen        = 0;      // bumped on every (re)start/stop to retire stale loops
let webcamPending    = null;   // newest undecoded JPEG bytes (older frames dropped)
let webcamDecoding   = false;  // a createImageBitmap() is currently in flight
let webcamGotFrame   = false;  // first frame drawn since the latest (re)start

// Apply the configured clockwise rotation to the stream <img>. For 90/270 the
// image box is sized to the container's swapped dimensions so it still fills
// full-bleed (object-fit:cover) after the rotation.
function applyWebcamRotation() {
  const img = document.getElementById("webcam-stream");
  const feed = document.getElementById("webcam-feed");
  if (!img || !feed) return;
  const r = ((Number(WEBCAM_ROTATION) || 0) % 360 + 360) % 360;
  if (r === 90 || r === 270) {
    img.style.position = "absolute";
    img.style.top = "50%";
    img.style.left = "50%";
    img.style.width = feed.clientHeight + "px";
    img.style.height = feed.clientWidth + "px";
    img.style.transform = "translate(-50%,-50%) rotate(" + r + "deg)";
  } else {
    img.style.position = "";
    img.style.top = "";
    img.style.left = "";
    img.style.width = "100%";
    img.style.height = "100%";
    img.style.transform = r === 180 ? "rotate(180deg)" : "";
  }
}

function startWebcamStream() {
  const canvas = document.getElementById("webcam-stream");
  if (!canvas || !canvas.getContext) return;
  stopWebcamStream();              // tear down any previous connection first
  const gen = ++webcamGen;         // this run's token (stopWebcamStream bumped gen)
  webcamGotFrame = false;
  applyWebcamRotation();
  // Cache-bust to force a fresh multipart connection rather than a cached one.
  runWebcamStream(canvas, WEBCAM_STREAM_URL + "?t=" + Date.now(), gen);
}

// Find a 2-byte marker (e.g. JPEG SOI 0xFFD8 / EOI 0xFFD9) in a byte array.
function findWebcamMarker(buf, b1, b0, from) {
  for (let i = from; i < buf.length - 1; i++) {
    if (buf[i] === b1 && buf[i + 1] === b0) return i;
  }
  return -1;
}

// Pull the MJPEG stream byte-by-byte, draw the newest complete JPEG to the
// canvas, and retire if our generation token is superseded (stop/restart).
async function runWebcamStream(canvas, url, gen) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  webcamAbort = new AbortController();
  let reader;
  try {
    const resp = await fetch(url, { signal: webcamAbort.signal, cache: "no-store" });
    if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
    reader = resp.body.getReader();
  } catch (e) {
    if (gen === webcamGen) onWebcamError();
    return;
  }

  let buf = new Uint8Array(0);
  try {
    while (gen === webcamGen) {
      const { value, done } = await reader.read();
      if (done) break;
      if (!value || !value.length) continue;
      // Append the chunk to whatever partial frame we're carrying.
      if (buf.length === 0) {
        buf = value;
      } else {
        const merged = new Uint8Array(buf.length + value.length);
        merged.set(buf, 0); merged.set(value, buf.length);
        buf = merged;
      }
      // Pull every complete JPEG (SOI … EOI) out of the buffer, keeping only the
      // last — older frames are dropped so the decoder never lags real time.
      let frame = null, soi;
      while ((soi = findWebcamMarker(buf, 0xFF, 0xD8, 0)) !== -1) {
        const eoi = findWebcamMarker(buf, 0xFF, 0xD9, soi + 2);
        if (eoi === -1) break;
        frame = buf.slice(soi, eoi + 2);   // own copy, detached from buf
        buf = buf.slice(eoi + 2);
      }
      if (frame) {
        webcamPending = frame;             // drop any older undecoded frame
        if (!webcamDecoding) decodeWebcamFrames(canvas, ctx, gen);
      }
      if (buf.length > 4_000_000) buf = buf.slice(buf.length - 1_000_000);  // growth guard
    }
  } catch (e) {
    // AbortError from stopWebcamStream() bumps the generation, so it's ignored here.
  }
  if (gen === webcamGen) onWebcamError();   // stream ended/failed while still wanted
}

// Decode pending JPEGs newest-first and draw them, closing each bitmap right
// after to free its decoded pixels immediately (the actual memory fix).
async function decodeWebcamFrames(canvas, ctx, gen) {
  webcamDecoding = true;
  while (webcamPending && gen === webcamGen) {
    const bytes = webcamPending;
    webcamPending = null;
    let bitmap;
    try {
      bitmap = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
    } catch (e) {
      continue;   // partial/corrupt frame — skip to the next one
    }
    if (gen !== webcamGen) { bitmap.close(); break; }
    if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
    }
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close();
    if (!webcamGotFrame) { webcamGotFrame = true; onWebcamLoad(); }
  }
  webcamDecoding = false;
}

// Re-fit a rotated 90/270 stream when the window (and thus the feed) resizes.
window.addEventListener("resize", () => { if (webcamOverlayOpen()) applyWebcamRotation(); });

function stopWebcamStream() {
  webcamGen++;                     // retire the running fetch/decode loop
  webcamPending = null;
  if (webcamRetryTimer) { clearTimeout(webcamRetryTimer); webcamRetryTimer = null; }
  if (webcamAbort) { try { webcamAbort.abort(); } catch (e) {} webcamAbort = null; }
  // Clear the last frame so a reopen doesn't flash a stale image.
  const canvas = document.getElementById("webcam-stream");
  if (canvas && canvas.getContext) {
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

function webcamOverlayOpen() {
  const overlay = document.getElementById("webcam-overlay");
  return !!overlay && overlay.classList.contains("open");
}

// Stream loaded successfully — hide the "No signal" fallback, show the video.
function onWebcamLoad() {
  const img = document.getElementById("webcam-stream");
  const fallback = document.getElementById("webcam-fallback");
  if (img) img.style.display = "block";
  if (fallback) fallback.style.display = "none";
  applyWebcamRotation();
}

// Stream failed — show the fallback and, while still open, retry shortly after.
function onWebcamError() {
  // Ignore the error that fires when we deliberately clear the stream to show
  // the thermal view — otherwise the "No signal" fallback covers the heatmap.
  if (thermalViewActive) return;
  const img = document.getElementById("webcam-stream");
  const fallback = document.getElementById("webcam-fallback");
  if (img) img.style.display = "none";
  if (fallback) fallback.style.display = "flex";
  if (webcamRetryTimer) { clearTimeout(webcamRetryTimer); webcamRetryTimer = null; }
  if (webcamOverlayOpen()) {
    webcamRetryTimer = setTimeout(() => {
      webcamRetryTimer = null;
      if (webcamOverlayOpen()) startWebcamStream();
    }, 3000);
  }
}

function toggleWebcam() {
  const overlay = document.getElementById("webcam-overlay");
  const btn = document.getElementById("webcam-toggle-btn");
  const toolbar = document.getElementById("webcam-toolbar");
  if (!overlay) return;
  const open = overlay.classList.toggle("open");
  if (btn) btn.classList.toggle("active", open);
  if (toolbar) toolbar.classList.toggle("open", open);
  if (open) { startWebcamStream(); }
  // Closing the overlay always drops back to the normal webcam stream so the
  // next open starts on the camera, not the thermal view.
  else { stopWebcamStream(); setThermalView(false); }
}

// ─── Thermal camera (AMG8833) view ────────────────────────────────────────────
// BTN 6 in the webcam toolbar swaps the live MJPEG stream for the Teensy's
// 8x8 thermal grid. Data arrives as {"type":"thermal","data":[64 floats]} and
// is drawn onto an 8x8 offscreen buffer that the browser upscales (bilinear)
// to fill the feed — same idea as testcode2/thermal.py's bicubic interpolation.
// Colour-scale bounds (°C) and the mirror flips live in the THERMAL global,
// configured in Parameters and persisted to settings.json.

let thermalViewActive = false;
let latestThermal = null;   // most recent 64-value frame, drawn on demand

// Inferno-ish colour ramp: black → purple → red → orange → yellow → white.
// Stops are [position 0-1, r, g, b]; we linearly interpolate between them.
const THERMAL_RAMP = [
  [0.0,   0,   0,   0],
  [0.25, 60,   8,  90],
  [0.5, 170,  35,  60],
  [0.7, 235, 100,  20],
  [0.85,250, 190,  40],
  [1.0, 255, 255, 220],
];

function thermalColor(t) {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < THERMAL_RAMP.length; i++) {
    const b = THERMAL_RAMP[i];
    if (x <= b[0]) {
      const a = THERMAL_RAMP[i - 1];
      const f = (x - a[0]) / (b[0] - a[0] || 1);
      return [
        Math.round(a[1] + f * (b[1] - a[1])),
        Math.round(a[2] + f * (b[2] - a[2])),
        Math.round(a[3] + f * (b[3] - a[3])),
      ];
    }
  }
  const last = THERMAL_RAMP[THERMAL_RAMP.length - 1];
  return [last[1], last[2], last[3]];
}

// Called from websocket.js for every {"type":"thermal"} frame.
function applyThermal(obj) {
  if (!obj || !Array.isArray(obj.data) || obj.data.length < 64) return;
  latestThermal = obj.data;
  if (thermalViewActive) renderThermal(latestThermal);
}

// Called from websocket.js for {"type":"compass"} frames. Stores the latest
// reading, runs the magnet-pole detector, and refreshes the on-screen readout
// (and the live values in Parameters if it's open).
function applyCompass(obj) {
  if (!obj) return;
  latestCompass = {
    x: Number(obj.x), y: Number(obj.y), z: Number(obj.z),
    heading: Number(obj.heading),
  };
  setMagnetDisplay(detectPole(latestCompass));
  updateCompassParamReadout();
}

// Classify the configured axis against ±threshold. Returns "NORTH"/"SOUTH"/"NONE".
function detectPole(c) {
  if (!c) return "NONE";
  const v = c[COMPASS.axis];
  if (!Number.isFinite(v)) return "NONE";
  const t = Math.abs(COMPASS.threshold);
  if (v >= t)  return "NORTH";
  if (v <= -t) return "SOUTH";
  return "NONE";
}

// Update the magnet readout above the board-status block. `pole` is one of
// "NORTH"/"SOUTH"/"NONE", or null to blank it (e.g. when the Teensy is offline).
function setMagnetDisplay(pole) {
  const row = document.getElementById("magnet-row");
  const val = document.getElementById("magnet-value");
  if (!row || !val) return;
  if (pole === null) {
    val.textContent = "—";
    row.className = "board-row magnet-row";
    return;
  }
  val.textContent = pole;
  row.className = "board-row magnet-row"
    + (pole === "NORTH" ? " north" : pole === "SOUTH" ? " south" : "");
}

function renderThermal(data) {
  const canvas = document.getElementById("thermal-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(8, 8);
  const span = (THERMAL.max - THERMAL.min) || 1;   // guard against min == max
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      // Read from a (possibly mirrored) source cell; write to the natural one.
      const sr = THERMAL.flipV ? 7 - row : row;
      const sc = THERMAL.flipH ? 7 - col : col;
      const norm = (data[sr * 8 + sc] - THERMAL.min) / span;
      const [r, g, b] = thermalColor(norm);
      const p = (row * 8 + col) * 4;
      img.data[p] = r; img.data[p + 1] = g; img.data[p + 2] = b; img.data[p + 3] = 255;
    }
  }
  // Draw the 8x8 buffer; CSS scales the canvas up and the browser's image
  // smoothing interpolates between cells for a continuous heatmap.
  ctx.putImageData(img, 0, 0);
  const status = document.getElementById("thermal-status");
  if (status) status.style.display = "none";
}

function setThermalView(on) {
  thermalViewActive = on;
  const img    = document.getElementById("webcam-stream");
  const canvas = document.getElementById("thermal-canvas");
  const status = document.getElementById("thermal-status");
  const fallback = document.getElementById("webcam-fallback");
  const btn    = document.getElementById("thermal-toggle-btn");
  if (btn) btn.classList.toggle("active", on);
  if (on) {
    // Free camera bandwidth while showing thermal, and hide the stream/fallback.
    stopWebcamStream();
    if (img) img.style.display = "none";
    if (fallback) fallback.style.display = "none";
    if (canvas) canvas.style.display = "block";
    if (status) status.style.display = latestThermal ? "none" : "block";
    if (latestThermal) renderThermal(latestThermal);
  } else {
    if (canvas) canvas.style.display = "none";
    if (status) status.style.display = "none";
    // Resume the live camera stream if the overlay is still open.
    if (img) img.style.display = "block";
    if (webcamOverlayOpen()) startWebcamStream();
  }
}

function toggleThermalView() {
  if (!webcamOverlayOpen()) return;
  setThermalView(!thermalViewActive);
}

// ─── QR code logging (BTN 1) ──────────────────────────────────────────────────
// Decoding runs server-side: ws_server.py reads the Pi MJPEG feed, decodes QR
// codes, and appends each unique one to a timestamped CSV in savedCSV/. BTN 1
// just toggles it; the button's lit state is reconciled from the qr_status the
// server echoes back (so a failed start, e.g. OpenCV missing, un-lights it).
let qrLogging = false;
function toggleQrDecode() {
  qrLogging = !qrLogging;
  const btn = document.getElementById("qr-toggle-btn");
  if (btn) btn.classList.toggle("active", qrLogging);
  send({ cmd: "qr", state: qrLogging ? 1 : 0 });
  log(qrLogging ? "QR logging requested…" : "QR logging stopped", "info");
}

// Called from websocket.js for {"cmd":"qr_status"} / {"cmd":"qr_detect"}.
function applyQrStatus(obj) {
  qrLogging = !!obj.running;
  const btn = document.getElementById("qr-toggle-btn");
  if (btn) btn.classList.toggle("active", qrLogging);
  if (obj.text) log("[QR] " + obj.text, obj.running ? "info" : "warn");
  if (!qrLogging) clearQrOverlay();   // wipe boxes when logging stops
}

function applyQrDetect(obj) {
  log("[QR] #" + obj.n + " " + obj.time + "  " + obj.data, "info");
}

// ─── QR bounding-box overlay ──────────────────────────────────────────────────
// The server decodes against the raw Pi frame, so it sends each code's polygon
// in source-frame pixels (w×h). We map those onto the displayed <img>, which
// fills the feed with object-fit:cover and may be rotated by WEBCAM_ROTATION.
let qrOverlayTimer = null;

function clearQrOverlay() {
  if (qrOverlayTimer) { clearTimeout(qrOverlayTimer); qrOverlayTimer = null; }
  const c = document.getElementById("qr-overlay-canvas");
  if (c && c.getContext) c.getContext("2d").clearRect(0, 0, c.width, c.height);
}

// Map a source-frame point through rotation + object-fit:cover to canvas pixels.
// Shared by the QR and AI overlays (the server sends coords in source-frame px).
function mapStreamPoint(sx, sy, fw, fh, dw, dh) {
  const r = ((Number(WEBCAM_ROTATION) || 0) % 360 + 360) % 360;
  let x = sx, y = sy, rw = fw, rh = fh;            // rotated-frame coords + dims
  if (r === 90)  { x = fh - sy; y = sx;       rw = fh; rh = fw; }
  else if (r === 180) { x = fw - sx; y = fh - sy; }
  else if (r === 270) { x = sy;      y = fw - sx; rw = fh; rh = fw; }
  // object-fit:cover — scale to fill, centre, crop the overflow.
  const scale = Math.max(dw / rw, dh / rh);
  return [x * scale + (dw - rw * scale) / 2, y * scale + (dh - rh * scale) / 2];
}

function drawQrOverlay(obj) {
  const canvas = document.getElementById("qr-overlay-canvas");
  const feed = document.getElementById("webcam-feed");
  if (!canvas || !feed || !obj || !obj.w || !obj.h) return;
  // Thermal view hides the camera stream — don't paint boxes over the heatmap.
  if (thermalViewActive) { clearQrOverlay(); return; }
  // Match the canvas backing store to its displayed size for crisp lines.
  const dw = feed.clientWidth, dh = feed.clientHeight;
  if (canvas.width !== dw)  canvas.width = dw;
  if (canvas.height !== dh) canvas.height = dh;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, dw, dh);

  (obj.codes || []).forEach(code => {
    const poly = (code.poly || []).map(p => mapStreamPoint(p[0], p[1], obj.w, obj.h, dw, dh));
    if (poly.length < 2) return;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#c8ff00";
    ctx.beginPath();
    ctx.moveTo(poly[0][0], poly[0][1]);
    for (let i = 1; i < poly.length; i++) ctx.lineTo(poly[i][0], poly[i][1]);
    ctx.closePath();
    ctx.stroke();
    // Label at the top-most corner.
    const top = poly.reduce((a, b) => (b[1] < a[1] ? b : a), poly[0]);
    ctx.font = "bold 14px " + (getComputedStyle(document.body).fontFamily || "monospace");
    const text = code.data || "";
    const tw = ctx.measureText(text).width;
    ctx.fillStyle = "rgba(0,0,0,0.65)";
    ctx.fillRect(top[0], top[1] - 20, tw + 10, 18);
    ctx.fillStyle = "#c8ff00";
    ctx.fillText(text, top[0] + 5, top[1] - 6);
  });

  // Auto-clear if the server goes quiet (code left the frame / logging stopped).
  if (qrOverlayTimer) clearTimeout(qrOverlayTimer);
  qrOverlayTimer = setTimeout(clearQrOverlay, 700);
}

// ─── AI object detection (BTN 2) ──────────────────────────────────────────────
// Inference runs server-side on the GPU (X7 YOLO11s via onnxruntime-gpu); the
// server streams bounding boxes that we draw over the live feed. BTN 2 toggles
// it; the lit state is reconciled from the ai_status the server echoes back.
let aiDetecting = false;
let aiOverlayTimer = null;

function toggleAiDetect() {
  aiDetecting = !aiDetecting;
  const btn = document.getElementById("ai-toggle-btn");
  if (btn) btn.classList.toggle("active", aiDetecting);
  send({ cmd: "ai", state: aiDetecting ? 1 : 0 });
  log(aiDetecting ? "AI detection requested…" : "AI detection stopped", "info");
}

function applyAiStatus(obj) {
  aiDetecting = !!obj.running;
  const btn = document.getElementById("ai-toggle-btn");
  if (btn) btn.classList.toggle("active", aiDetecting);
  if (obj.text) log("[AI] " + obj.text, obj.running ? "info" : "warn");
  if (!aiDetecting) clearAiOverlay();
}

function clearAiOverlay() {
  if (aiOverlayTimer) { clearTimeout(aiOverlayTimer); aiOverlayTimer = null; }
  const c = document.getElementById("ai-overlay-canvas");
  if (c && c.getContext) c.getContext("2d").clearRect(0, 0, c.width, c.height);
}

function drawAiOverlay(obj) {
  const canvas = document.getElementById("ai-overlay-canvas");
  const feed = document.getElementById("webcam-feed");
  if (!canvas || !feed || !obj || !obj.w || !obj.h) return;
  if (thermalViewActive) { clearAiOverlay(); return; }
  const dw = feed.clientWidth, dh = feed.clientHeight;
  if (canvas.width !== dw)  canvas.width = dw;
  if (canvas.height !== dh) canvas.height = dh;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, dw, dh);

  (obj.boxes || []).forEach(b => {
    const [x1, y1, x2, y2] = b.box;
    // Map the two box corners through the same cover/rotation transform.
    const [px1, py1] = mapStreamPoint(x1, y1, obj.w, obj.h, dw, dh);
    const [px2, py2] = mapStreamPoint(x2, y2, obj.w, obj.h, dw, dh);
    const rx = Math.min(px1, px2), ry = Math.min(py1, py2);
    const rw = Math.abs(px2 - px1), rh = Math.abs(py2 - py1);
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#00d0ff";
    ctx.strokeRect(rx, ry, rw, rh);
    const text = b.label + " " + Math.round((b.conf || 0) * 100) + "%";
    ctx.font = "bold 14px " + (getComputedStyle(document.body).fontFamily || "monospace");
    const tw = ctx.measureText(text).width;
    ctx.fillStyle = "rgba(0,0,0,0.65)";
    ctx.fillRect(rx, ry - 20, tw + 10, 18);
    ctx.fillStyle = "#00d0ff";
    ctx.fillText(text, rx + 5, ry - 6);
  });

  if (aiOverlayTimer) clearTimeout(aiOverlayTimer);
  aiOverlayTimer = setTimeout(clearAiOverlay, 700);
}

// ─── Motion detection (BTN 3) ─────────────────────────────────────────────────
// Detection runs server-side: ws_server.py frame-differences the Pi MJPEG feed
// and streams boxes around whatever is moving, which we draw over the live feed.
// BTN 3 toggles it; the lit state is reconciled from the motion_status the
// server echoes back (so a failed start, e.g. OpenCV missing, un-lights it).
let motionDetecting = false;
let motionOverlayTimer = null;

function toggleMotionDetect() {
  motionDetecting = !motionDetecting;
  const btn = document.getElementById("motion-toggle-btn");
  if (btn) btn.classList.toggle("active", motionDetecting);
  send({ cmd: "mdet", state: motionDetecting ? 1 : 0 });
  log(motionDetecting ? "Motion detection requested…" : "Motion detection stopped", "info");
}

function applyMotionStatus(obj) {
  motionDetecting = !!obj.running;
  const btn = document.getElementById("motion-toggle-btn");
  if (btn) btn.classList.toggle("active", motionDetecting);
  if (obj.text) log("[MOTION] " + obj.text, obj.running ? "info" : "warn");
  if (!motionDetecting) clearMotionOverlay();
}

function clearMotionOverlay() {
  if (motionOverlayTimer) { clearTimeout(motionOverlayTimer); motionOverlayTimer = null; }
  const c = document.getElementById("motion-overlay-canvas");
  if (c && c.getContext) c.getContext("2d").clearRect(0, 0, c.width, c.height);
}

function drawMotionOverlay(obj) {
  const canvas = document.getElementById("motion-overlay-canvas");
  const feed = document.getElementById("webcam-feed");
  if (!canvas || !feed || !obj || !obj.w || !obj.h) return;
  if (thermalViewActive) { clearMotionOverlay(); return; }
  const dw = feed.clientWidth, dh = feed.clientHeight;
  if (canvas.width !== dw)  canvas.width = dw;
  if (canvas.height !== dh) canvas.height = dh;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, dw, dh);

  (obj.boxes || []).forEach(b => {
    const [x1, y1, x2, y2] = b.box;
    const [px1, py1] = mapStreamPoint(x1, y1, obj.w, obj.h, dw, dh);
    const [px2, py2] = mapStreamPoint(x2, y2, obj.w, obj.h, dw, dh);
    const rx = Math.min(px1, px2), ry = Math.min(py1, py2);
    const rw = Math.abs(px2 - px1), rh = Math.abs(py2 - py1);
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#ff5a00";
    ctx.strokeRect(rx, ry, rw, rh);
    ctx.font = "bold 14px " + (getComputedStyle(document.body).fontFamily || "monospace");
    const text = "motion";
    const tw = ctx.measureText(text).width;
    ctx.fillStyle = "rgba(0,0,0,0.65)";
    ctx.fillRect(rx, ry - 20, tw + 10, 18);
    ctx.fillStyle = "#ff5a00";
    ctx.fillText(text, rx + 5, ry - 6);
  });

  if (motionOverlayTimer) clearTimeout(motionOverlayTimer);
  motionOverlayTimer = setTimeout(clearMotionOverlay, 700);
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
  if (matchesHotkey(k, HOTKEYS.avatar))    { toggleAvatar();     return true; }
  if (matchesHotkey(k, HOTKEYS.webcam))    { toggleWebcam();     return true; }
  if (matchesHotkey(k, HOTKEYS.ik))        { toggleIK();         return true; }
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