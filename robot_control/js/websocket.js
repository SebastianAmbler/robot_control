// ─── WebSocket connection, messaging & logging ────────────────────────────────
// Opens the WS link to ws_server.py, routes inbound messages to the feature
// modules (drive telemetry, arm angles), and provides send()/log() used app-wide.

let autoConnect    = false;
let reconnectTimer = null;
let reconnectDelay = 1000;   // ms, doubles on each failure up to max
const RECONNECT_MAX = 16000;

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
      // Plain-text feedback from UDPS: "ANGLES:90,90,..." or "DATA:..."
      if (typeof e.data === "string" && e.data.startsWith("ANGLES:")) {
        applyAngles(e.data.slice(7).trim());
        return;
      }
      const obj = JSON.parse(e.data);
      // Avatar arm echo: update slider/label + 3D sim without re-sending to server.
      if (obj.cmd === "servo") { setServoAngle(obj.id, obj.angle, false); return; }
      if (obj.cmd === "avatar_status") {
        const el = document.getElementById("avatar-placeholder");
        if (el && controlMode === "avatar") el.textContent = obj.text;
        return;
      }
      if (obj.T === 3) updateMotor(obj);
      else if (obj.T === 21) log("[ESP32] " + obj.info);
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
