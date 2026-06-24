// ─── WebSocket connection, messaging & logging ────────────────────────────────
// Opens the WS link to ws_server.py, routes inbound messages to the feature
// modules (drive telemetry, arm angles), and provides send()/log() used app-wide.

let autoConnect    = false;
let reconnectTimer = null;
let reconnectDelay = 1000;   // ms, doubles on each failure up to max
const RECONNECT_MAX = 16000;

// Board liveness: timestamp of the last message proving each board is talking to
// the Pi. ESP32 → motor telemetry (T:3); Teensy/arm → ANGLES:/DATA: readback.
let lastEsp32Msg   = 0;
let lastTeensyMsg  = 0;
let lastLidarMsg   = 0;   // set from Lidar.html iframe postMessage while /scan flows
const BOARD_TIMEOUT = 2500;  // ms without data before a board is considered offline

function setAutoStatus(msg) {
  document.getElementById("auto-status").textContent = msg;
}

function scheduleReconnect() {
  if (!autoConnect) return;
  clearTimeout(reconnectTimer);
  setAutoStatus("retry in " + (reconnectDelay / 1000).toFixed(0) + "s…");
  reconnectTimer = setTimeout(() => {
    if (autoConnect) connectWS(true);
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX);
}

function toggleAutoConnect() {
  autoConnect = document.getElementById("auto-connect-chk").checked;
  if (autoConnect) {
    reconnectDelay = 1000;
    connectWS(true);
  } else {
    clearTimeout(reconnectTimer);
    setAutoStatus("");
  }
  saveSettings();
}

function connectWS(silent) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    autoConnect = false;
    document.getElementById("auto-connect-chk").checked = false;
    clearTimeout(reconnectTimer);
    setAutoStatus("");
    ws.close();
    return;
  }
  const url = document.getElementById("ws-input").value.trim();
  if (ws) { try { ws.close(); } catch(_) {} ws = null; }
  if (!silent) log("Connecting to " + url + "...", "info");
  ws = new WebSocket(url);

  ws.onopen = () => {
    reconnectDelay = 1000;
    clearTimeout(reconnectTimer);
    setAutoStatus(autoConnect ? "connected" : "");
    setStatus(true);
    document.getElementById("ws-connect").textContent = "Disconnect";
    log("Connected", "info");
    // Re-arm the server-side avatar bridge if the toggle was left on.
    if (avatarOn) send({ cmd: "avatar", state: 1 });
  };
  ws.onclose = () => {
    setStatus(false);
    document.getElementById("ws-connect").textContent = "Connect";
    log("Disconnected", "warn");
    scheduleReconnect();
  };
  ws.onerror = () => {
    if (!silent) log("Connection error", "error");
    // onclose fires after onerror, so reconnect is handled there
  };
  ws.onmessage = (e) => {
    try {
      // Plain-text feedback from UDPS: "DATA:..."
      if (typeof e.data === "string" && e.data.startsWith("DATA:")) {
        lastTeensyMsg = Date.now();
        return;
      }
      // JSON.parse is inside this try{}: malformed/partial serial lines throw
      // and are swallowed by the catch below, so the connection never crashes.
      const obj = JSON.parse(e.data);
      // Teensy feedback now arrives as typed JSON. Three shapes:
      //   {"type":"angles","front":90,...,"grip":90}   — joint readback (10 Hz)
      //   {"type":"compass","x":..,"y":..,"z":..,"heading":..}  — magnetometer (5 Hz)
      //   {"type":"thermal","data":[64 floats]}        — AMG8833 8x8 grid (10 Hz)
      // Any typed Teensy message proves the board is alive; route by type.
      if (obj.type) {
        lastTeensyMsg = Date.now();
        if (obj.type === "thermal")      applyThermal(obj);
        else if (obj.type === "compass") applyCompass(obj);
        else                             applyAngles(obj);
        return;
      }
      // Avatar arm echo: update slider/label + 3D sim without re-sending to server.
      if (obj.cmd === "servo") { setServoAngle(obj.id, obj.angle, false); return; }
      if (obj.cmd === "avatar_status") { return; }
      // Avatar on/off state, pushed by the server when the PHYSICAL button on the
      // avatar arm toggles it, or right after connecting (to sync to the bridge's
      // real current state). Update the UI in place — do NOT call setAvatar()/
      // send {cmd:"avatar"} back here, or we'd loop the command back to the server.
      if (obj.cmd === "avatar") {
        avatarOn = !!obj.state;
        updateModeUI();
        saveSettings();
        log("Avatar arm: " + (avatarOn ? "on" : "off"), "info");
        return;
      }
      // QR logging (BTN 1): status drives the button's lit state; detect logs a code.
      if (obj.cmd === "qr_status") { applyQrStatus(obj); return; }
      if (obj.cmd === "qr_detect") { applyQrDetect(obj); return; }
      if (obj.cmd === "qr_overlay") { drawQrOverlay(obj); return; }
      // AI detection (BTN 2): status drives the button; overlay draws boxes.
      if (obj.cmd === "ai_status") { applyAiStatus(obj); return; }
      if (obj.cmd === "ai_overlay") { drawAiOverlay(obj); return; }
      // Motion detection (BTN 3): status drives the button; overlay draws boxes.
      if (obj.cmd === "motion_status") { applyMotionStatus(obj); return; }
      if (obj.cmd === "motion_overlay") { drawMotionOverlay(obj); return; }
      // IMU orientation from the Pi: light the status dot and forward roll/pitch
      // to the 3D sim iframe so the robot body tilts with the real robot. Yaw is
      // omitted on purpose — the heading is unreliable due to interference.
      if (obj.cmd === "imu") {
        const dot = document.getElementById("imu-dot");
        if (dot) dot.classList.add("on");
        const f = document.getElementById("sim-iframe");
        if (f && f.contentWindow) {
          try { f.contentWindow.postMessage({ type: "imu", roll: obj.roll, pitch: obj.pitch }, "*"); } catch(_) {}
        }
        return;
      }
      if (obj.T === 3) { lastEsp32Msg = Date.now(); updateMotor(obj); }
      else if (obj.T === 21) { lastEsp32Msg = Date.now(); log("[ESP32] " + obj.info); }
    } catch(_) {}
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function setStatus(connected) {
  document.getElementById("status-dot").className = connected ? "connected" : "";
  document.getElementById("status-text").textContent = connected ? "Connected" : "Disconnected";
}

// Light each board dot when its telemetry is fresh and the WS link is up.
function updateBoardStatus() {
  const open = ws && ws.readyState === WebSocket.OPEN;
  const now  = Date.now();
  const setDot = (id, alive) => {
    const el = document.getElementById(id);
    if (el) el.className = "board-dot" + (alive ? " on" : "");
  };
  setDot("ws-dot",     open);
  setDot("esp32-dot",  open && (now - lastEsp32Msg)  < BOARD_TIMEOUT);
  const teensyAlive = open && (now - lastTeensyMsg) < BOARD_TIMEOUT;
  setDot("teensy-dot", teensyAlive);
  // Lidar runs over its own rosbridge link (the iframe), not the main WS — so it's
  // gated only on fresh /scan reports, independent of `open`.
  setDot("lidar-dot", (now - lastLidarMsg) < BOARD_TIMEOUT);
  // Magnet detection comes from the Teensy's compass; blank it when stale so a
  // stale "NORTH"/"SOUTH" never lingers after the board goes quiet.
  if (!teensyAlive && typeof setMagnetDisplay === "function") setMagnetDisplay(null);
}
setInterval(updateBoardStatus, 500);

// ─── Log ──────────────────────────────────────────────────────────────────────
const MAX_LOG = 80;
function log(msg, type) {
  const logEl = document.getElementById("log");
  const line  = document.createElement("div");
  const now   = new Date();
  const ts    = now.toTimeString().slice(0,8);
  line.className = "log-line" + (type ? " " + type : "");
  line.textContent = ts + "  " + msg;
  logEl.appendChild(line);
  while (logEl.children.length > MAX_LOG) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}