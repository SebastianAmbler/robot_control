// ═══════════════════════════════════════
//  CONFIG
// ═══════════════════════════════════════
const SERVO_NAMES    = ["J1 Shoulder","J2 Elbow","J3 Extend","J4 Wrist","J5 Tool","J6 Gripper","Flip-F","Flip-R"];
const SERVO_KEYS_MAP = ['y','u','i','o','h','j','k','l'];
const SERVO_DEFAULTS = [ 50, 130,   0,  90,  90,  70, 100, 100];
const SERVO_MINS     = [ 50,  10,   0,   0,   0,  10,  45,  45];
const SERVO_MAXS     = [150, 130, 180, 150, 180,  90, 160, 160];
const NUM_SERVOS     = 8;

let MOVE_KEYS = new Set(['w','a','s','d','q','e','z','c']);
const POSTURE_FKEYS  = {'F1':'home','F2':'horizontal','F3':'guard','F4':'giraff','F5':'stair','F6':'custom_1','F7':'custom_2','F8':'custom_3','F9':'custom_4'};

let state = {
  angles:      [...SERVO_DEFAULTS],
  selected:    0,
  servo_step:  1,
  motor_state: 'stop',
  locked:      false,
  active_keys: new Set(),
  lin_spd:     0.15,
  ang_spd:     0.50,
  rpm_spd:     80,
  lr: 0, rr: 0, heading: 0, victimCount: 0,
};

const ARM_CFG = {
  L1: 192, L2: 133, L3: 125,
  IDX_SHOULDER: 0, IDX_ELBOW: 1, IDX_WRIST: 3,
  S_OFFSET: 60, E_OFFSET: 10, W_OFFSET: 20,
};

const IK_JOINTS = new Set([ARM_CFG.IDX_SHOULDER, ARM_CFG.IDX_ELBOW, ARM_CFG.IDX_WRIST]);
const CTRL = { JOINT: 'joint', INVERT: 'invert', HYBRID: 'hybrid' };
let ctrlMode = CTRL.JOINT;

const cart = { y: 30.0, z: 18.0, step: 1, pitch: 30.0 };

let cartHoldTimer    = null, cartHoldInterval    = null;
let pitchHoldTimer   = null, pitchHoldInterval   = null;


// ═══════════════════════════════════════
//  FLASK / QR SERVER HELPER
// ═══════════════════════════════════════
/**
 * Returns base URL of Flask QR server from the #flaskAddr input.
 * Falls back to piAddr if flaskAddr is empty.
 * Normalises: strips trailing slash, prepends http:// if no scheme.
 */
function getFlaskBase() {
  const raw = (document.getElementById('flaskAddr')?.value || '').trim()
            || document.getElementById('piAddr')?.textContent.trim()
            || '127.0.0.1';
  // Add scheme if missing
  const withScheme = raw.startsWith('http') ? raw : `http://${raw}`;
  // Add port 5000 if no port is specified (no colon after host)
  const url = new URL(withScheme);
  if (!url.port) url.port = '5000';
  return url.origin; // e.g. http://192.168.0.100:5000
}


// ═══════════════════════════════════════
//  CARTESIAN CONTROL
// ═══════════════════════════════════════
function _sendGOTO() {
  sendCmd({ type: 'arm_raw', cmd: `GOTO ${cart.y} ${cart.z} ${cart.pitch}` });
}

function cartMove(dy, dz) {
  cart.y = parseFloat((cart.y + dy * cart.step).toFixed(1));
  cart.z = parseFloat((cart.z + dz * cart.step).toFixed(1));
  _sendGOTO();
  updateCartUI();
  addLog(`GOTO y:${cart.y} z:${cart.z} p:${cart.pitch}`, 'ok', 'move');
}

function cartPitch(dp) {
  cart.pitch = parseFloat((cart.pitch + dp * cart.step).toFixed(1));
  _sendGOTO();
  updateCartUI();
  addLog(`Pitch → ${cart.pitch}°`, 'ok', 'move');
}

function solveFK() {
  cart.y = 30.0; cart.z = 18.0; cart.pitch = 30.0;
  updateCartUI();
  addLog(`FK sync → homeYZP y:${cart.y} z:${cart.z} p:${cart.pitch}`, 'warn', 'move');
}

function updateCartUI() {
  const xEl  = document.getElementById('cartX');
  const zEl  = document.getElementById('cartZ');
  const pEl  = document.getElementById('cartP');
  const xBox = xEl?.closest('.cart-val-box');
  const zBox = zEl?.closest('.cart-val-box');
  if (xEl) xEl.textContent = cart.y.toFixed(1);
  if (zEl) zEl.textContent = cart.z.toFixed(1);
  if (pEl) pEl.textContent = cart.pitch.toFixed(1);
  if (xBox) { xBox.classList.remove('flash-x'); void xBox.offsetWidth; xBox.classList.add('flash-x'); }
  if (zBox) { zBox.classList.remove('flash-z'); void zBox.offsetWidth; zBox.classList.add('flash-z'); }
}

function startCartHold(dy, dz) {
  cartMove(dy, dz);
  cartHoldTimer = setTimeout(() => {
    cartHoldInterval = setInterval(() => cartMove(dy, dz), 80);
  }, 350);
}
function stopCartHold() {
  clearTimeout(cartHoldTimer); clearInterval(cartHoldInterval);
  cartHoldTimer = null; cartHoldInterval = null;
}

function startPitchHold(dir) {
  cartPitch(dir);
  pitchHoldTimer = setTimeout(() => {
    pitchHoldInterval = setInterval(() => cartPitch(dir), 80);
  }, 350);
}
function stopPitchHold() {
  clearTimeout(pitchHoldTimer); clearInterval(pitchHoldInterval);
  pitchHoldTimer = null; pitchHoldInterval = null;
}

function setCartStep(n, el) {
  cart.step = n;
  el.closest('.step-row')?.querySelectorAll('.step-btn')
    .forEach(b => b.classList.toggle('active', b === el));
}


// ═══════════════════════════════════════
//  CONTROL MODE
// ═══════════════════════════════════════
function setCtrlMode(mode) {
  ctrlMode = mode;
  if ((mode === CTRL.INVERT || mode === CTRL.HYBRID) && ikOptions.autoFK) solveFK();

  const styleMap = { joint:'active-joint', invert:'active-invert', hybrid:'active-hybrid' };
  ['joint','invert','hybrid'].forEach(m => {
    const btn = document.getElementById(`modeBtn-${m}`);
    if (!btn) return;
    btn.className = 'ctrl-mode-btn' + (m === mode ? ` ${styleMap[m]}` : '');
  });

  const cartPanel = document.getElementById('cart-panel');
  const jointWrap = document.getElementById('joint-controls-wrap');
  const showCart  = (mode === CTRL.INVERT || mode === CTRL.HYBRID);
  cartPanel.style.display = showCart ? 'block' : 'none';
  jointWrap.style.display = (mode === CTRL.INVERT) ? 'none' : 'block';

  if (mode === CTRL.HYBRID) {
    IK_JOINTS.clear();
    Object.entries(hybridJointAssign).forEach(([i, ik]) => { if (ik) IK_JOINTS.add(+i); });
  } else if (mode === CTRL.INVERT) {
    IK_JOINTS.clear();
    [ARM_CFG.IDX_SHOULDER, ARM_CFG.IDX_ELBOW, ARM_CFG.IDX_WRIST].forEach(i => IK_JOINTS.add(i));
  } else {
    IK_JOINTS.clear();
  }

  for (let i = 0; i < NUM_SERVOS; i++) {
    const row = document.getElementById(`srv-row-${i}`);
    if (!row) continue;
    if (IK_JOINTS.has(i)) row.classList.add('ik-joint');
    else row.classList.remove('ik-joint');
  }

  const labels = {
    joint:  'JOINT mode',
    invert: 'IK mode — Arrow=Y/Z  PgUp/PgDn=Pitch',
    hybrid: 'HYBRID mode — IK arm + direct joints',
  };
  addLog(labels[mode], 'ok', 'move');
  renderAll();
}


// ═══════════════════════════════════════
//  WEBSOCKET
// ═══════════════════════════════════════
let ws = null, retryTimer = null, retryDelay = 1000;
let pingStart = 0, pingInterval = null;
window.ws = null;

(function autoFillWS() {
  const host = window.location.hostname;
  document.getElementById('wsAddr').value   = `${host}:8765`;
  document.getElementById('piAddr').textContent = host || '192.168.1.138';
})();

function connectWS() {
  const host = document.getElementById('wsAddr').value.trim() || 'localhost:8765';
  const st   = document.getElementById('wsStatus');
  st.textContent = 'CONNECTING…'; st.className = 'retry';
  ws = new WebSocket(`ws://${host}`); window.ws = ws;

  ws.onopen = () => {
    retryDelay = 1000;
    st.textContent = '✓ CONNECTED'; st.className = 'connected';
    addLog('WebSocket connected', 'ok');
    startPing();
  };

  ws.onmessage = evt => {
    if (evt.data === '__pong__') { measurePing(); return; }
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'state') {
        if (Array.isArray(msg.angles) && !msg.from_serial) state.angles = msg.angles;
        if (msg.selected !== undefined) state.selected    = msg.selected;
        if (msg.motor_state)            state.motor_state = msg.motor_state;
        if (msg.log)                    applyLog(msg.log);
        if (msg.serial !== undefined)   applySerial(msg.serial);
        if (msg.heading !== undefined)  state.heading     = msg.heading;
        applyLRR(msg.lr ?? state.lr, msg.rr ?? state.rr);
        if (msg.lin_spd !== undefined)  state.lin_spd = msg.lin_spd;
        if (msg.ang_spd !== undefined)  state.ang_spd = msg.ang_spd;
        if (ctrlMode !== CTRL.JOINT) updateCartUI();
      }
      if (msg.type === 'snap_ack') handleSnapAck(msg);
      if (msg.type === 'custom_posture_ack') handleCustomPostureAck(msg);
      renderAll();
    } catch(e) { addLog('msg err: ' + e.message, 'err'); }
  };

  ws.onerror = () => { document.getElementById('wsStatus').className = 'error'; };

  ws.onclose = () => {
    ws = null; clearInterval(pingInterval);
    const st = document.getElementById('wsStatus');
    st.textContent = `RETRY ${(retryDelay/1000).toFixed(1)}s`; st.className = 'retry';
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      retryDelay = Math.min(10000, retryDelay * 1.5); connectWS();
    }, retryDelay);
  };
}

document.getElementById('btnConnect').onclick = () => {
  clearTimeout(retryTimer); retryDelay = 1000;
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  connectWS();
};

function sendCmd(payload) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(payload));
}

function startPing() {
  clearInterval(pingInterval);
  pingInterval = setInterval(() => {
    if (ws && ws.readyState === 1) { pingStart = performance.now(); ws.send('__ping__'); }
  }, 2000);
}
function measurePing() {
  const ms = Math.round(performance.now() - pingStart);
  const el = document.getElementById('pingDisplay');
  el.textContent = `PING: ${ms}ms`;
  el.className = ms < 50 ? 'good' : ms < 150 ? 'med' : 'bad';
}

const hbDot = document.getElementById('hbDot');
let hbAliveTimer = null;
function pingHB() {
  hbDot.classList.add('alive');
  clearTimeout(hbAliveTimer);
  hbAliveTimer = setTimeout(() => hbDot.classList.remove('alive'), 400);
}
setInterval(() => { if (ws && ws.readyState === 1) pingHB(); }, 1000);

function applyLog(lines)  { if (!lines?.length) return; addLog(lines[lines.length-1], 'ok', 'sys'); }
function applySerial(ok)  { const b = document.getElementById('modeBadge'); b.textContent = ok ? 'SERIAL OK' : 'NO SERIAL'; b.className = 'mode-badge ' + (ok ? 'serial' : 'udp'); }
function applyLRR(lr, rr) { state.lr = lr; state.rr = rr; }


// ═══════════════════════════════════════
//  LOG SYSTEM
// ═══════════════════════════════════════
const LOG_MAX = 120;
let sysLogEntries  = [];
let moveLogEntries = [];
let detLogEntries  = [];
let moveLogOpen    = false;
let detectLogOpen  = false;

function _ts() { return new Date().toLocaleTimeString('en-GB',{hour12:false}); }

function _refreshLogBox() {
  const box = document.getElementById('logBox');
  if (!box) return;
  box.innerHTML = '';
  sysLogEntries.forEach(e => {
    const div = document.createElement('div');
    div.className = `log-entry ${e.cls}`;
    div.textContent = e.msg;
    box.appendChild(div);
  });
  box.scrollTop = box.scrollHeight;
}

function clearSysLog() {
  sysLogEntries = [];
  const box = document.getElementById('logBox');
  if (box) box.innerHTML = '';
}

function addLog(msg, cls = 'ok', cat = 'sys') {
  if (cat === 'sys' || cat === 'move') {
    sysLogEntries.push({ msg, cls });
    if (sysLogEntries.length > 50) sysLogEntries.shift();
    _refreshLogBox();
  }

  const entry = { msg, cls, ts: _ts() };

  if (cat === 'move') {
    moveLogEntries.push(entry);
    if (moveLogEntries.length > LOG_MAX) moveLogEntries.shift();
    _renderLogPanel('move', moveLogEntries);
  } else if (cat === 'detect') {
    detLogEntries.push(entry);
    if (detLogEntries.length > LOG_MAX) detLogEntries.shift();
    _renderLogPanel('detect', detLogEntries);
    sysLogEntries.push({ msg, cls });
    if (sysLogEntries.length > 50) sysLogEntries.shift();
    _refreshLogBox();
  }
}

function addMoveLog(msg, cls = 'ok')   { addLog(msg, cls, 'move'); }
function addDetectLog(msg, cls = 'ok') { addLog(msg, cls, 'detect'); }

function _renderLogPanel(type, entries) {
  const bodyId  = type === 'move' ? 'move-log-body'  : 'detect-log-body';
  const countId = type === 'move' ? 'move-log-count' : 'detect-log-count';
  const body    = document.getElementById(bodyId);
  const countEl = document.getElementById(countId);
  if (!body) return;
  const wasAtBottom = body.scrollHeight - body.scrollTop <= body.clientHeight + 8;
  body.innerHTML = '';
  entries.forEach(e => {
    const div = document.createElement('div');
    div.className = `log-line ${e.cls}`;
    div.innerHTML = `<span class="ll-ts">${e.ts}</span><span class="ll-msg">${e.msg}</span>`;
    body.appendChild(div);
  });
  if (countEl) countEl.textContent = entries.length;
  if (wasAtBottom) body.scrollTop = body.scrollHeight;
}

function toggleMoveLog() {
  moveLogOpen = !moveLogOpen;
  document.getElementById('log-panel-move').classList.toggle('open', moveLogOpen);
  ['btnMoveLog','btnMoveLogInline'].forEach(id => { const el = document.getElementById(id); if (el) el.classList.toggle('active-move', moveLogOpen); });
  if (moveLogOpen) _renderLogPanel('move', moveLogEntries);
}

function toggleDetectLog() {
  detectLogOpen = !detectLogOpen;
  document.getElementById('log-panel-detect').classList.toggle('open', detectLogOpen);
  ['btnDetectLog','btnDetectLogInline'].forEach(id => { const el = document.getElementById(id); if (el) el.classList.toggle('active-detect', detectLogOpen); });
  if (detectLogOpen) _renderLogPanel('detect', detLogEntries);
}

function clearMoveLog()   { moveLogEntries = []; _renderLogPanel('move',   moveLogEntries); }
function clearDetectLog() { detLogEntries  = []; _renderLogPanel('detect', detLogEntries);  }


// ═══════════════════════════════════════
//  IP CAMERAS
// ═══════════════════════════════════════
const camState = [
  { live: false, url: '', mode: 'img' },
  { live: false, url: '', mode: 'img' },
  { live: false, url: '', mode: 'img' },  // CAM 3 (AI panel)
];

function setCamMode(idx, mode) {
  camState[idx].mode = mode;
  document.getElementById(`camModeImg${idx}`).className   = 'cam-mode-btn' + (mode === 'img'    ? ' active' : '');
  document.getElementById(`camModeFrame${idx}`).className = 'cam-mode-btn' + (mode === 'iframe' ? ' active https-mode' : '');
  if (camState[idx].live) { disconnectCam(idx); setTimeout(() => connectCam(idx), 50); }
}

function toggleCam(idx) { camState[idx].live ? disconnectCam(idx) : connectCam(idx); }

function connectCam(idx) {
  const url = document.getElementById(`camUrl${idx}`).value.trim();
  if (!url) { setCamStatus(idx,'err','NO URL'); return; }

  const isHttps   = url.toLowerCase().startsWith('https://');
  const pageIsHttp = window.location.protocol === 'http:';

  if (isHttps && pageIsHttp && camState[idx].mode === 'img') {
    addLog(`CAM ${idx+1}: HTTPS → auto-switch FRAME mode`, 'warn', 'detect');
    setCamMode(idx, 'iframe');
  }

  const mode   = camState[idx].mode;
  const img    = document.getElementById(`camImg${idx}`);
  const frame  = document.getElementById(`camFrame${idx}`);
  const ph     = document.getElementById(`camPh${idx}`);
  const btn    = document.getElementById(`camBtn${idx}`);
  const badge  = document.getElementById(`camHttpsBadge${idx}`);
  const mixWarn = document.getElementById(`camMixedWarn${idx}`);

  mixWarn.style.display = 'none';
  badge.style.display   = isHttps ? 'block' : 'none';
  camState[idx].live = true;
  camState[idx].url  = url;
  btn.textContent = 'DISCONNECT';
  btn.classList.add('live'); btn.classList.remove('err');

  if (mode === 'iframe') {
    img.style.display   = 'none'; img.src = '';
    frame.style.display = 'block'; frame.src = url;
    ph.style.display    = 'none';
    setCamStatus(idx, 'live', '● LIVE (FRAME)');
    addLog(`CAM ${idx+1} → FRAME: ${url}`, 'ok', 'detect');
    frame.onerror = () => {
      setCamStatus(idx, 'err', 'FRAME ERR');
      btn.classList.add('err'); btn.classList.remove('live');
    };
  } else {
    frame.style.display = 'none'; frame.src = '';
    if (isHttps && pageIsHttp) {
      img.style.display = 'none'; ph.style.display = 'none';
      mixWarn.style.display = 'flex';
      camState[idx].live = false;
      btn.textContent = 'CONNECT'; btn.classList.remove('live');
      setCamStatus(idx, 'err', 'BLOCKED');
      addLog(`CAM ${idx+1}: Mixed content blocked → use FRAME`, 'err', 'detect');
      return;
    }
    img.onload  = () => {
      img.style.display = 'block'; ph.style.display = 'none';
      btn.textContent = 'DISCONNECT'; btn.classList.remove('err'); btn.classList.add('live');
      setCamStatus(idx, 'live', '● LIVE');
      addLog(`CAM ${idx+1} connected`, 'ok', 'detect');
    };
    img.onerror = () => {
      if (!camState[idx].live) {
        setCamStatus(idx, 'err', 'ERROR');
        btn.classList.add('err'); btn.classList.remove('live');
      }
    };
    img.src = url + (url.includes('?') ? '&' : '?') + '_t=' + Date.now();
    img.style.display = 'block'; ph.style.display = 'none';
    setCamStatus(idx, 'live', '● LIVE');
    addLog(`CAM ${idx+1} → IMG: ${url}`, 'ok', 'detect');
  }
}

function disconnectCam(idx) {
  const img     = document.getElementById(`camImg${idx}`);
  const frame   = document.getElementById(`camFrame${idx}`);
  const badge   = document.getElementById(`camHttpsBadge${idx}`);
  const mixWarn = document.getElementById(`camMixedWarn${idx}`);
  img.src = ''; img.style.display = 'none';
  frame.src = ''; frame.style.display = 'none';
  badge.style.display = 'none'; mixWarn.style.display = 'none';
  document.getElementById(`camPh${idx}`).style.display = 'block';
  const btn = document.getElementById(`camBtn${idx}`);
  btn.textContent = 'CONNECT'; btn.classList.remove('live','err');
  camState[idx].live = false;
  setCamStatus(idx, '', 'OFFLINE');
  addLog(`CAM ${idx+1} disconnected`, 'warn', 'detect');
}

function setCamStatus(idx, cls, text) {
  const el = document.getElementById(`camStat${idx}`);
  el.textContent = text; el.className = 'cam-status-badge ' + (cls || '');
}

[0,1].forEach(idx => {
  document.getElementById(`camUrl${idx}`).addEventListener('keydown', e => {
    if (e.key === 'Enter') connectCam(idx);
  });
  document.getElementById(`camUrl${idx}`).addEventListener('input', e => {
    const val = e.target.value.trim().toLowerCase();
    if (val.startsWith('https://') && camState[idx].mode === 'img') {
      setCamMode(idx, 'iframe');
      addLog(`CAM ${idx+1}: HTTPS → auto-switch FRAME mode`, 'warn', 'detect');
    }
  });
});


// ═══════════════════════════════════════
//  SERVO LIST
// ═══════════════════════════════════════
function buildServoList() {
  const list = document.getElementById('servoList');
  list.innerHTML = '';
  for (let i = 0; i < NUM_SERVOS; i++) {
    const row = document.createElement('div');
    row.className = 'servo-row' + (i === state.selected ? ' selected' : '') + (i === 7 ? ' motor-row' : '');
    row.id = `srv-row-${i}`;
    row.onclick = () => selectServo(i);
    row.innerHTML = `
      <div class="srv-key" id="srv-key-${i}">${SERVO_KEYS_MAP[i].toUpperCase()}</div>
      <div class="srv-name">${SERVO_NAMES[i]}</div>
      <div class="srv-bar-wrap"><div class="srv-bar${i===7?' motor-bar':''}" id="srv-bar-${i}"></div></div>
      <div class="srv-deg" id="srv-deg-${i}">0°</div>
    `;
    list.appendChild(row);
  }
}


// ═══════════════════════════════════════
//  RENDER
// ═══════════════════════════════════════
let rafPending = false;
function renderAll() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => { rafPending = false; _render(); });
}

function _render() {
  for (let i = 0; i < NUM_SERVOS; i++) {
    const angle = state.angles[i], lo = SERVO_MINS[i], hi = SERVO_MAXS[i];
    const pct  = ((angle-lo) / Math.max(1, hi-lo) * 100).toFixed(1);
    const frac = (angle-lo) / Math.max(1, hi-lo);
    const bar  = document.getElementById(`srv-bar-${i}`);
    const deg  = document.getElementById(`srv-deg-${i}`);
    const row  = document.getElementById(`srv-row-${i}`);
    if (!bar) continue;
    bar.style.width = pct + '%';
    deg.textContent = angle.toFixed(0) + '°';
    deg.className = 'srv-deg' + (frac<=0.05||frac>=0.95?' danger':frac<=0.12||frac>=0.88?' warn':'');
    const isIK = (ctrlMode !== CTRL.JOINT) && IK_JOINTS.has(i);
    row.className = 'servo-row'
      + (i===state.selected ? ' selected' : '')
      + (i===7 ? ' motor-row' : '')
      + (isIK ? ' ik-joint' : '');
  }

  document.getElementById('selName').textContent = SERVO_NAMES[state.selected];
  const mb = document.getElementById('motorBadge');
  mb.textContent = state.motor_state.toUpperCase();
  mb.className   = 'motorBadge ' + state.motor_state;
  mb.style.display = state.selected === 7 ? 'inline-block' : 'none';

  for (let n = 1; n <= 5; n++)
    document.getElementById(`step${n}`).className = 'step-btn' + (n === state.servo_step ? ' active' : '');

  const maxRPM = Math.max(1, state.rpm_spd);
  document.getElementById('rpmBarL').style.width = Math.min(100, Math.abs(state.lr)/maxRPM*100) + '%';
  document.getElementById('rpmBarR').style.width = Math.min(100, Math.abs(state.rr)/maxRPM*100) + '%';
  document.getElementById('rpmNumL').textContent = state.lr > 0 ? `+${state.lr}` : state.lr;
  document.getElementById('rpmNumR').textContent = state.rr > 0 ? `+${state.rr}` : state.rr;

  const ws_el = document.getElementById('wheelStatus');
  if (state.locked)              { ws_el.textContent='⛔ BRAKE';   ws_el.style.color='var(--danger)'; }
  else if (state.active_keys.size>0) { ws_el.textContent='▶ MOVING'; ws_el.style.color='var(--ok)'; }
  else                           { ws_el.textContent='● IDLE';    ws_el.style.color='var(--text-dim)'; }

  MOVE_KEYS.forEach(k => document.getElementById(`btn-${k}`)?.classList.toggle('pressed', state.active_keys.has(k)));
  document.getElementById('btn-x')?.classList.toggle('pressed', state.locked);

  drawCompass(state.heading);

  const iframe = document.querySelector('#mini-3d-wrap iframe');
  if (iframe?.contentWindow)
    iframe.contentWindow.postMessage({ type:'state', angles:state.angles, selected:state.selected }, '*');
}


// ═══════════════════════════════════════
//  COMPASS
// ═══════════════════════════════════════
function drawCompass(deg) {
  const canvas = document.getElementById('compass-canvas');
  if (!canvas) return;   // ← guard: control.html ไม่มี compass canvas
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height, cx = W/2, cy = H/2, r = W/2-4;
  ctx.clearRect(0,0,W,H);
  ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.strokeStyle='#1c2a42'; ctx.lineWidth=1.5; ctx.stroke();
  for (let i=0;i<36;i++) {
    const a = (i*10-90)*Math.PI/180, inner = i%9===0 ? r-8 : r-4;
    ctx.beginPath(); ctx.moveTo(cx+Math.cos(a)*(r-1),cy+Math.sin(a)*(r-1)); ctx.lineTo(cx+Math.cos(a)*inner,cy+Math.sin(a)*inner);
    ctx.strokeStyle = i%9===0 ? '#2d4268' : '#1c2a42'; ctx.lineWidth = i%9===0 ? 1.5 : 0.75; ctx.stroke();
  }
  [['N',0],['E',90],['S',180],['W',270]].forEach(([label,angle]) => {
    const a = (angle-90)*Math.PI/180;
    ctx.fillStyle = label==='N' ? '#ff3355' : '#3d5570';
    ctx.font = 'bold 7px Orbitron, monospace'; ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(label, cx+Math.cos(a)*(r-14), cy+Math.sin(a)*(r-14));
  });
  const rad  = (deg-90)*Math.PI/180;
  const tipX = cx+Math.cos(rad)*(r-16), tipY = cy+Math.sin(rad)*(r-16);
  const tailX= cx-Math.cos(rad)*(r-22), tailY= cy-Math.sin(rad)*(r-22);
  const perpX=-Math.sin(rad)*4, perpY=Math.cos(rad)*4;
  ctx.beginPath(); ctx.moveTo(tipX,tipY); ctx.lineTo(cx+perpX,cy+perpY); ctx.lineTo(cx-perpX,cy-perpY); ctx.closePath();
  ctx.fillStyle='#ff3355'; ctx.shadowColor='#ff335580'; ctx.shadowBlur=6; ctx.fill(); ctx.shadowBlur=0;
  ctx.beginPath(); ctx.moveTo(tailX,tailY); ctx.lineTo(cx+perpX,cy+perpY); ctx.lineTo(cx-perpX,cy-perpY); ctx.closePath();
  ctx.fillStyle='#00d4ff'; ctx.fill();
  ctx.beginPath(); ctx.arc(cx,cy,3,0,Math.PI*2); ctx.fillStyle='#e8f4ff'; ctx.fill();
  const dirs = ['NORTH','NE','EAST','SE','SOUTH','SW','WEST','NW'];
  document.getElementById('compass-heading').textContent = String(Math.round(deg)).padStart(3,'0')+'°';
  document.getElementById('compass-dir').textContent     = dirs[Math.round(deg/45)%8];
}
setInterval(() => {
  if (ws && ws.readyState===1) return;
  state.heading = (state.heading+0.3)%360; renderAll();
}, 100);


// ═══════════════════════════════════════
//  MINIMAP
// ═══════════════════════════════════════
let mapMarkers=[], mapMode='victim', robotPos={x:70,y:50};
const mapColors = { victim:'#ff3355', hazard:'#ffaa00', visited:'#00d4ff' };

function setMapMode(mode) { mapMode = mode; }
function clearMap() { mapMarkers=[]; addLog('Map cleared','warn'); }
function drawMinimap() {}


// ═══════════════════════════════════════
//  SERVO ACTIONS
// ═══════════════════════════════════════
function selectServo(i) { state.selected=i; sendCmd({type:'select_servo',index:i}); renderAll(); }

function servoStep(dir) {
  const i = state.selected;
  if (ctrlMode===CTRL.INVERT && IK_JOINTS.has(i)) { addLog('IK mode: use arrow keys', 'warn', 'move'); return; }

  if (i===5) {
    const lo  = SERVO_MINS[5];
    const hi  = SERVO_MAXS[5];
    const newVal = Math.min(hi, Math.max(lo, state.angles[5] + state.servo_step * dir));
    state.angles[5] = newVal;
    sendCmd({type:'servo_set', index:5, angle:newVal});
    renderAll();
    addLog(`J6 Gripper → ${newVal}°`, 'ok', 'move');
    return;
  }
  const lo  = SERVO_MINS[i], hi = SERVO_MAXS[i];
  const newVal = Math.min(hi, Math.max(lo, state.angles[i] + state.servo_step*dir));
  state.angles[i] = newVal;
  sendCmd({type:'servo_set', index:i, angle:newVal});
  renderAll();
  addLog(`${SERVO_NAMES[i]} → ${newVal}°`, 'ok', 'move');
}

function setStep(n) { state.servo_step=n; sendCmd({type:'set_step',step:n}); renderAll(); }
// sendPosture defined below in CUSTOM POSTURE section

function gripOpen() {
  state.angles[5] = 70;
  state.motor_state = 'reverse';
  sendCmd({type:'servo_set', index:5, angle:70});
  addLog('J6 Gripper → OPEN (70°)', 'ok', 'move');
  renderAll();
}

function gripClose() {
  state.angles[5] = 10;
  state.motor_state = 'grip';
  sendCmd({type:'servo_set', index:5, angle:10});
  addLog('J6 Gripper → CLOSE (10°)', 'ok', 'move');
  renderAll();
}

function sendSnap() {
  sendCmd({type:'snapshot'});
  document.getElementById('snapStatus').textContent = 'SNAP: sent…';
  document.getElementById('snapStatus').style.color = 'var(--warn)';
  if (!aiOpen) toggleAI();
  addLog('Snapshot requested', 'warn', 'detect');
}

function handleSnapAck(msg) {
  if (msg.image_b64) {
    const img = document.getElementById('camFeed');
    img.src = 'data:image/jpeg;base64,' + msg.image_b64; img.style.display='block';
    document.getElementById('camPlaceholder').style.display = 'none';
    const det = document.getElementById('detBadge');
    det.style.display='block'; det.textContent=(msg.detections??0)+' detection(s)';
    document.getElementById('snapStatus').textContent = `SNAP: ${msg.detections??0} det`;
    document.getElementById('snapStatus').style.color = 'var(--ok)';
    if (!aiOpen) toggleAI();
    addLog(`Snapshot: ${msg.detections??0} detection(s)`, 'ok', 'detect');
  }
}


// ═══════════════════════════════════════
//  AI PANEL
// ═══════════════════════════════════════
let aiOpen = false;
function toggleAI() {
  aiOpen = !aiOpen;
  document.getElementById('ai-panel').classList.toggle('open', aiOpen);
  const btn = document.getElementById('ai-trigger-btn');
  btn.textContent     = aiOpen ? '✕ CLOSE' : 'AI';
  btn.style.borderColor = aiOpen ? 'var(--danger)' : '';
  btn.style.color       = aiOpen ? 'var(--danger)' : '';
  // เชื่อม CAM 3 อัตโนมัติเมื่อเปิด AI panel
  if (aiOpen) connectCam3();
}

function connectCam3() {
  const url = document.getElementById('cam3Url')?.value?.trim();
  if (!url) return;
  const img = document.getElementById('cam3Feed');
  if (!img) return;
  img.src = url + (url.includes('?') ? '&' : '?') + '_t=' + Date.now();
  img.style.display = 'block';
  document.getElementById('cam3Placeholder').style.display = 'none';
  addLog('CAM 3 → ' + url, 'ok', 'detect');
}


// ═══════════════════════════════════════
//  QR PANEL
// ═══════════════════════════════════════

// ═══════════════════════════════════════
//  QR SNAPSHOT PANEL
// ═══════════════════════════════════════
let qrOpen = false;
let qrScanning = false;

function toggleQR() {
  qrOpen = !qrOpen;
  document.getElementById('qr-panel').classList.toggle('open', qrOpen);
  const btn   = document.getElementById('qr-trigger-btn');
  const frame = document.getElementById('qrFlaskFrame');
  const ph    = document.getElementById('qrPlaceholder');

  btn.style.borderColor = qrOpen ? 'var(--ok)' : '';
  btn.style.color       = qrOpen ? 'var(--ok)' : '';

  if (qrOpen && aiOpen) toggleAI();

  if (qrOpen) {
    const base = getFlaskBase();   // → http://127.0.0.1:5000
    frame.src           = base;
    frame.style.display = 'block';
    if (ph) ph.style.display = 'none';
    addLog('QR Panel → ' + base, 'ok', 'detect');
  } else {
    frame.src           = '';
    frame.style.display = 'none';
    if (ph) ph.style.display = 'flex';
  }
}

async function doQRScan() {
  if (qrScanning) return;
  qrScanning = true;

  // แสดง scanning overlay
  const overlay = document.getElementById('qrScanningOverlay');
  const btn     = document.getElementById('qrScanBtn');
  const ph      = document.getElementById('qrSnapPlaceholder');
  if (overlay) overlay.style.display = 'flex';
  if (btn)     { btn.disabled = true; btn.textContent = '⏳ SCANNING...'; }

  try {
    // เรียก Flask /scan_qr → ถ่ายภาพ + detect QR
    const resp = await fetch(FLASK_BASE + '/scan_qr', { method: 'POST' });
    const data = await resp.json();

    // แสดงภาพ snapshot
    const img = document.getElementById('qrSnapImg');
    if (img && data.image) {
      img.src = 'data:image/jpeg;base64,' + (data.image_b64 || data.image || '');
      img.style.display = 'block';
      if (ph) ph.style.display = 'none';
    }

    // แสดงผล QR
    // codes อาจเป็น [{data, type}] หรือ string[] — normalize ให้เป็น string[]
    const rawCodes = data.codes || [];
    const codeList = rawCodes.map(c => typeof c === 'object' ? c.data : c).filter(Boolean);
    const found    = codeList.length > 0;
    const badge   = document.getElementById('qrFoundBadge');
    const result  = document.getElementById('qrResultBox');
    const noResult= document.getElementById('qrNoResult');
    const txtEl   = document.getElementById('qrResultText');
    const cntEl   = document.getElementById('qrResultCount');

    if (found) {
      if (badge)    { badge.style.display = 'block'; }
      if (result)   { result.style.display = 'block'; }
      if (noResult) { noResult.style.display = 'none'; }
      if (txtEl)    { txtEl.innerHTML = codeList.map(c => `<div>• ${c}</div>`).join(''); }
      if (cntEl)    { cntEl.textContent = codeList.length + ' code(s)'; }
      // ส่งผลผ่าน WebSocket
      sendCmd({ type: 'qr_result', codes: codeList });
      addLog('QR: ' + codeList.join(' | '), 'ok', 'detect');
    } else {
      if (badge)    { badge.style.display = 'none'; }
      if (result)   { result.style.display = 'none'; }
      if (noResult) { noResult.style.display = 'block'; }
      addLog('QR: no code found', 'warn', 'detect');
    }

  } catch(err) {
    addLog('QR scan error: ' + err, 'err', 'detect');
    // ถ้า Flask ไม่รัน — ลอง /status แทน
    try {
      const r2 = await fetch(FLASK_BASE + '/status');
      const d2 = await r2.json();
      if (d2.qr_found && d2.all_qr && d2.all_qr.length > 0) {
        const txtEl = document.getElementById('qrResultText');
        const cntEl = document.getElementById('qrResultCount');
        const result = document.getElementById('qrResultBox');
        const noResult = document.getElementById('qrNoResult');
        if (txtEl)    txtEl.innerHTML = d2.all_qr.map(c => `<div>• ${c}</div>`).join('');
        if (cntEl)    cntEl.textContent = d2.all_qr.length + ' code(s)';
        if (result)   result.style.display = 'block';
        if (noResult) noResult.style.display = 'none';
        addLog('QR (status): ' + d2.all_qr.join(' | '), 'ok', 'detect');
      }
    } catch(e2) {}
  } finally {
    qrScanning = false;
    const overlay = document.getElementById('qrScanningOverlay');
    const btn     = document.getElementById('qrScanBtn');
    if (overlay) overlay.style.display = 'none';
    if (btn)     { btn.disabled = false; btn.textContent = '⬡ SCAN QR CODE'; }
  }
}

function copyQRResult() {
  const text = document.getElementById('qrResultText')?.innerText || '';
  navigator.clipboard?.writeText(text).then(() => {
    const btn = document.getElementById('qrCopyBtn');
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = 'COPIED!';
    btn.style.color = 'var(--ok)';
    btn.style.borderColor = 'var(--ok)';
    setTimeout(() => {
      btn.textContent = orig;
      btn.style.color = '';
      btn.style.borderColor = '';
    }, 1500);
  });
}


// ═══════════════════════════════════════
//  GEAR SPEED
// ═══════════════════════════════════════
let LIN_GEARS = [0.05, 0.15, 0.30, 0.50, 0.80];
let ANG_GEARS = [0.20, 0.50, 1.00, 1.80, 2.50];
let linGear = 2, angGear = 2;

function _applyGearUI(prefix, activeGear) {
  for (let g=1; g<=5; g++) {
    const btn = document.getElementById(`${prefix}G${g}`);
    if (!btn) continue;
    btn.className = 'gear-btn'
      + (g===4 ? ' g-warn'   : '')
      + (g===5 ? ' g-danger' : '')
      + (g===activeGear ? ' active' : '');
  }
  for (let g=1; g<=5; g++) {
    const seg = document.getElementById(`${prefix}Seg${g}`);
    if (!seg) continue;
    seg.className = g <= activeGear ? `gear-seg lit-${g}` : 'gear-seg';
  }
}

function setLinGear(g) {
  linGear = g; state.lin_spd = LIN_GEARS[g-1];
  const valEl = document.getElementById('linVal');
  if (valEl) valEl.textContent = state.lin_spd.toFixed(2);
  _applyGearUI('lin', g);
  sendCmd({type:'set_speed', lin:state.lin_spd, ang:state.ang_spd});
  addLog(`LINEAR G${g} → ${state.lin_spd.toFixed(2)} m/s`, 'ok', 'move');
}
function setAngGear(g) {
  angGear = g; state.ang_spd = ANG_GEARS[g-1];
  const valEl = document.getElementById('angVal');
  if (valEl) valEl.textContent = state.ang_spd.toFixed(2);
  _applyGearUI('ang', g);
  sendCmd({type:'set_speed', lin:state.lin_spd, ang:state.ang_spd});
  addLog(`ANGULAR G${g} → ${state.ang_spd.toFixed(2)} r/s`, 'ok', 'move');
}


// ═══════════════════════════════════════
//  KEYBOARD
// ═══════════════════════════════════════
const keyHeld = new Set();
let KB_REV = {};
function rebuildKBRev() { KB_REV={}; Object.entries(KB).forEach(([a,k])=>{ KB_REV[k]=a; }); }

const ACTION_TO_MOVEKEY = { move_forward:'w', move_back:'s', move_left:'a', move_right:'d', move_diag_fl:'q', move_diag_fr:'e', move_diag_bl:'z', move_diag_br:'c' };
const ACTION_TO_POSTURE = { posture_home:'home', posture_horizontal:'horizontal', posture_guard:'guard', posture_giraff:'giraff', posture_stair:'stair', posture_custom_1:'custom_1', posture_custom_2:'custom_2', posture_custom_3:'custom_3', posture_custom_4:'custom_4' };
const ACTION_TO_SERVO   = { servo_0:0, servo_1:1, servo_2:2, servo_3:3, servo_4:4, servo_5:5, servo_6:6, servo_7:7 };

function handleKeyDown(e) {
  if (document.activeElement?.tagName === 'INPUT' || document.activeElement?.tagName === 'TEXTAREA') return;
  const rawKey = e.key;

  if (rawKey==='Escape') {
    if (panelOverlayOpen) { closePanelOverlay(); return; }
    if (fsActive) closeFullscreen();
    return;
  }
  if (rawKey==='r' || rawKey==='R') {
    if (document.activeElement?.tagName !== 'INPUT') {
      panelOverlayOpen ? closePanelOverlay() : openPanelOverlay();
      return;
    }
  }
  if (rawKey==='Tab') { e.preventDefault(); switchTab(currentTab==='settings'?'control':'settings'); return; }

  // ── Gear shortcuts: 1-5 = Linear Gear ──
  if (!e.shiftKey && ['1','2','3','4','5'].includes(rawKey)) {
    e.preventDefault();
    setLinGear(parseInt(rawKey));
    return;
  }

  if (ctrlMode===CTRL.INVERT || ctrlMode===CTRL.HYBRID) {
    if (rawKey==='ArrowUp')    { e.preventDefault(); cartMove(0,+1);  return; }
    if (rawKey==='ArrowDown')  { e.preventDefault(); cartMove(0,-1);  return; }
    if (rawKey==='ArrowRight') { e.preventDefault(); cartMove(+1,0);  return; }
    if (rawKey==='ArrowLeft')  { e.preventDefault(); cartMove(-1,0);  return; }
    if (rawKey==='PageUp')     { e.preventDefault(); cartPitch(+1);   return; }
    if (rawKey==='PageDown')   { e.preventDefault(); cartPitch(-1);   return; }
  }

  const action   = KB_REV[rawKey];
  const repeatOk = (action==='servo_inc' || action==='servo_dec');
  if (keyHeld.has(rawKey) && !repeatOk) return;
  keyHeld.add(rawKey);
  if (action) e.preventDefault();
  if (!action) return;

  const moveKey = ACTION_TO_MOVEKEY[action];
  if (moveKey) {
    if (state.locked) { state.locked=false; sendCmd({type:'unlock'}); }
    state.active_keys.add(moveKey);
    document.querySelector(`.btn[data-key="${moveKey}"]`)?.classList.add('pressed');
    sendCmd({type:'move_start', key:moveKey, lin:state.lin_spd, ang:state.ang_spd});
    renderAll(); return;
  }

  if (action==='brake') { state.locked=!state.locked; sendCmd({type:state.locked?'lock':'unlock'}); if(state.locked)state.active_keys.clear(); renderAll(); return; }

  const srvIdx = ACTION_TO_SERVO[action];
  if (srvIdx !== undefined) { selectServo(srvIdx); return; }

  if (action==='servo_inc') { servoStep(+1); return; }
  if (action==='servo_dec') { servoStep(-1); return; }
  if (action==='motor_stop')    { sendCmd({type:'motor_stop'});    addLog('Motor STOP', 'warn', 'move'); return; }
  if (action==='motor_grip')    { sendCmd({type:'motor_grip'});    addLog('Motor GRIP', 'ok', 'move');   return; }
  if (action==='motor_reverse') { sendCmd({type:'motor_reverse'}); addLog('Motor REV', 'warn', 'move');  return; }

  const postureName = ACTION_TO_POSTURE[action];
  if (postureName) { sendPosture(postureName); return; }

  if (action==='snapshot')   { sendSnap(); return; }
  if (action==='fullscreen') { if (!fsActive) openFullscreen(0); return; }
  if (action==='quit_warn')  { addLog('Close tab to stop rescue.py','warn'); return; }
}

function handleKeyUp(e) {
  if (document.activeElement?.tagName==='INPUT' || document.activeElement?.tagName==='TEXTAREA') return;
  const rawKey = e.key;
  keyHeld.delete(rawKey);
  const action  = KB_REV[rawKey];
  const moveKey = action ? ACTION_TO_MOVEKEY[action] : null;
  if (moveKey) {
    state.active_keys.delete(moveKey);
    document.querySelector(`.btn[data-key="${moveKey}"]`)?.classList.remove('pressed');
    sendCmd({type:'move_stop', key:moveKey});
    renderAll();
  }
}

window.addEventListener('keydown', handleKeyDown);
window.addEventListener('keyup',   handleKeyUp);

document.querySelectorAll('.btn[data-key]').forEach(btn => {
  const k = btn.dataset.key;
  btn.addEventListener('mousedown',  () => handleKeyDown(new KeyboardEvent('keydown',{key:k,bubbles:true})));
  btn.addEventListener('mouseup',    () => handleKeyUp(new KeyboardEvent('keyup',{key:k})));
  btn.addEventListener('mouseleave', () => { if(keyHeld.has(k)) handleKeyUp(new KeyboardEvent('keyup',{key:k})); });
  btn.addEventListener('touchstart', e => { e.preventDefault(); handleKeyDown(new KeyboardEvent('keydown',{key:k,bubbles:true})); }, {passive:false});
  btn.addEventListener('touchend',   e => { e.preventDefault(); handleKeyUp(new KeyboardEvent('keyup',{key:k})); }, {passive:false});
});

let angleHoldTimer = null, angleHoldInterval = null;
function startAngleHold(dir) { servoStep(dir); angleHoldTimer=setTimeout(()=>{ angleHoldInterval=setInterval(()=>servoStep(dir),60); },350); }
function stopAngleHold()  { clearTimeout(angleHoldTimer); clearInterval(angleHoldInterval); angleHoldTimer=null; angleHoldInterval=null; }

['mousedown','touchstart'].forEach(ev => {
  document.getElementById('btnDec').addEventListener(ev, e => { e.preventDefault(); startAngleHold(-1); }, {passive:false});
  document.getElementById('btnInc').addEventListener(ev, e => { e.preventDefault(); startAngleHold(+1); }, {passive:false});
});
['mouseup','mouseleave','touchend','touchcancel'].forEach(ev => {
  document.getElementById('btnDec').addEventListener(ev, stopAngleHold);
  document.getElementById('btnInc').addEventListener(ev, stopAngleHold);
});


// ═══════════════════════════════════════
//  FULLSCREEN
// ═══════════════════════════════════════
let fsActive=false, fsCamIdx=0, fsUpdateInterval=null;

function openFullscreen(idx) {
  fsCamIdx=idx; fsActive=true;
  const overlay = document.getElementById('fullscreen-overlay');
  const fsImg   = document.getElementById('fs-img');
  const fsFrame = document.getElementById('fs-frame');
  const fsPh    = document.getElementById('fs-placeholder');
  const isLive  = camState[idx].live;
  const mode    = camState[idx].mode;

  document.getElementById('fs-label').textContent     = `CAM ${idx+1} — ${isLive?'LIVE':'OFFLINE'}`;
  document.getElementById('fs-status').style.display  = isLive ? 'inline-block' : 'none';
  fsImg.style.display='none'; fsFrame.style.display='none'; fsPh.style.display='none';

  if (isLive && mode==='iframe') {
    fsFrame.src=camState[idx].url; fsFrame.style.display='block';
  } else if (isLive && mode==='img') {
    const src = document.getElementById(`camImg${idx}`);
    if (src?.src && src.src!==window.location.href) { fsImg.src=src.src; fsImg.style.display='block'; }
    else fsPh.style.display='block';
    clearInterval(fsUpdateInterval);
    fsUpdateInterval = setInterval(() => {
      const s = document.getElementById(`camImg${idx}`);
      if (s && camState[idx].live && s.src && s.src!==fsImg.src) { fsImg.src=s.src; fsImg.style.display='block'; fsPh.style.display='none'; }
    }, 500);
  } else {
    fsPh.style.display='block';
  }

  overlay.classList.add('open');
  overlay.requestFullscreen?.().catch(()=>{});
}

function closeFullscreen() {
  fsActive=false;
  document.getElementById('fullscreen-overlay').classList.remove('open');
  clearInterval(fsUpdateInterval);
  document.getElementById('fs-img').src   = '';
  document.getElementById('fs-frame').src = '';
  document.getElementById('fs-frame').style.display = 'none';
  document.getElementById('fs-img').style.display   = 'none';
  document.fullscreenElement && document.exitFullscreen().catch(()=>{});
}

document.getElementById('fullscreen-overlay').addEventListener('click', e => {
  if (e.target===document.getElementById('fullscreen-overlay') || e.target===document.getElementById('fs-img'))
    closeFullscreen();
});
document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && fsActive) closeFullscreen(); });


// ═══════════════════════════════════════
//  PANEL EXPAND OVERLAY (ARM)
// ═══════════════════════════════════════
let panelOverlayOpen = false;

function openPanelOverlay() {
  panelOverlayOpen = true;
  const overlay = document.getElementById('panel-overlay');
  const body    = document.getElementById('panel-overlay-body');
  const rightPanel = document.getElementById('right');
  body.innerHTML = '';
  const compass = rightPanel.querySelector('#compass-section');
  if (compass) body.appendChild(compass.cloneNode(true));
  const panelBody = rightPanel.querySelector('.panel-body');
  if (panelBody) {
    const clone = panelBody.cloneNode(true);
    clone.style.overflow = 'visible';
    clone.style.flex = 'none';
    body.appendChild(clone);
  }
  overlay.classList.add('open');
  overlay.requestFullscreen?.().catch(()=>{});
  addLog('ARM panel expanded','ok');
}

function closePanelOverlay() {
  panelOverlayOpen = false;
  document.getElementById('panel-overlay').classList.remove('open');
  document.getElementById('panel-overlay-body').innerHTML = '';
  document.fullscreenElement && document.exitFullscreen().catch(()=>{});
}

document.getElementById('panel-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('panel-overlay')) closePanelOverlay();
});
document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement && panelOverlayOpen) closePanelOverlay();
});


// ═══════════════════════════════════════
//  TAB SWITCHER
// ═══════════════════════════════════════
let currentTab = 'control';
function switchTab(tab) {
  currentTab = tab;
  document.getElementById('settings-overlay').classList.toggle('open', tab==='settings');
  document.getElementById('tab-control').classList.toggle('active',   tab==='control');
  document.getElementById('tab-settings').classList.toggle('active',  tab==='settings');
  if (tab==='settings') { buildSettingsUI(); buildIKSettingsUI(); _syncGearInputs(); }
}


// ═══════════════════════════════════════
//  KEYBIND SYSTEM
// ═══════════════════════════════════════
const KB_DEFAULTS = {
  move_forward:'w', move_back:'s', move_left:'a', move_right:'d',
  move_diag_fl:'q', move_diag_fr:'e', move_diag_bl:'z', move_diag_br:'c', brake:'x',
  servo_0:'y', servo_1:'u', servo_2:'i', servo_3:'o',
  servo_4:'h', servo_5:'j', servo_6:'k', servo_7:'l',
  servo_inc:'=', servo_dec:'-',
  motor_grip:'.', motor_reverse:',', motor_stop:' ',
  posture_home:'F1', posture_horizontal:'F2', posture_guard:'F3', posture_giraff:'F4', posture_stair:'F5',
  posture_custom_1:'F6', posture_custom_2:'F7', posture_custom_3:'F8', posture_custom_4:'F9',
  snapshot:'p', fullscreen:'f', quit_warn:'`',
};

const KB_LABELS = {
  move_forward:{label:'Forward',cat:'move'}, move_back:{label:'Backward',cat:'move'},
  move_left:{label:'Turn Left',cat:'move'}, move_right:{label:'Turn Right',cat:'move'},
  move_diag_fl:{label:'Diag Fwd-Left',cat:'move'}, move_diag_fr:{label:'Diag Fwd-Right',cat:'move'},
  move_diag_bl:{label:'Diag Back-Left',cat:'move'}, move_diag_br:{label:'Diag Back-Right',cat:'move'},
  brake:{label:'Brake (toggle)',cat:'move'},
  servo_0:{label:'J1 Shoulder',cat:'servo'}, servo_1:{label:'J2 Elbow',cat:'servo'},
  servo_2:{label:'J3 Extend',cat:'servo'},   servo_3:{label:'J4 Wrist',cat:'servo'},
  servo_4:{label:'J5 Tool',cat:'servo'},     servo_5:{label:'J6 Gripper',cat:'servo'},
  servo_6:{label:'Flip-F',cat:'servo'},      servo_7:{label:'Flip-R',cat:'servo'},
  servo_inc:{label:'Angle +',cat:'servo'}, servo_dec:{label:'Angle −',cat:'servo'},
  motor_grip:{label:'Motor Grip',cat:'servo'}, motor_reverse:{label:'Motor Reverse',cat:'servo'}, motor_stop:{label:'Motor Stop',cat:'servo'},
  posture_home:{label:'Posture: Home',cat:'posture'}, posture_horizontal:{label:'Posture: HomeIK',cat:'posture'},
  posture_guard:{label:'Posture: Guard',cat:'posture'}, posture_giraff:{label:'GIRAFF',cat:'posture'}, posture_stair:{label:'STAIR',cat:'posture'},
  posture_custom_1:{label:'Custom 1',cat:'posture'}, posture_custom_2:{label:'Custom 2',cat:'posture'},
  posture_custom_3:{label:'Custom 3',cat:'posture'}, posture_custom_4:{label:'Custom 4',cat:'posture'},
  snapshot:{label:'AI Snapshot',cat:'misc'}, fullscreen:{label:'Fullscreen',cat:'misc'}, quit_warn:{label:'Quit Warning',cat:'misc'},
};

let KB = { ...KB_DEFAULTS };
function loadKeybinds()  { try { const s=localStorage.getItem('rescuebot_kb'); if(s) KB={...KB_DEFAULTS,...JSON.parse(s)}; } catch(e){} }
function saveKeybinds()  {
  const vals = Object.values(KB);
  if (vals.some((v,i)=>vals.indexOf(v)!==i)) { addLog('⚠ Conflict! Fix duplicate keys first.','err'); return; }
  try { localStorage.setItem('rescuebot_kb',JSON.stringify(KB)); } catch(e){}
  applyKeybinds();
  if (typeof savePostureNames === 'function') savePostureNames();
  addLog('Keybinds + posture names saved ✓','ok');
  const btn=document.querySelector('.s-btn-save'); const orig=btn.textContent;
  btn.textContent='✓ SAVED!'; btn.style.background='var(--ok)'; btn.style.color='#000';
  setTimeout(()=>{ btn.textContent=orig; btn.style.background=''; btn.style.color=''; },1200);
}
function resetKeybinds() {
  KB={...KB_DEFAULTS}; applyKeybinds();
  if (typeof CUSTOM_POSTURE_DEFAULTS !== 'undefined') {
    postureNames = { ...CUSTOM_POSTURE_DEFAULTS };
    applyPostureNames();
  }
  buildSettingsUI();
  if (typeof buildPostureCfgUI === 'function') buildPostureCfgUI();
  addLog('Keybinds reset','warn');
}
function applyKeybinds() {
  rebuildKBRev(); MOVE_KEYS.clear();
  Object.entries(ACTION_TO_MOVEKEY).forEach(([action])=>{ MOVE_KEYS.add(KB[action]); });
  Object.entries(ACTION_TO_MOVEKEY).forEach(([action,serverKey])=>{
    const btn=document.querySelector(`.btn[data-key="${serverKey}"]`);
    if (btn) { const badge=btn.querySelector('.key-badge'); if(badge) badge.textContent=KB[action]===' '?'SPC':KB[action].toUpperCase(); }
  });
  const brakeBtn=document.getElementById('btn-x');
  if (brakeBtn) { const badge=brakeBtn.querySelector('.key-badge'); if(badge) badge.textContent=(KB.brake||'x').toUpperCase(); }
}

let listeningEl = null;
function buildSettingsUI() {
  const cats = { move:{grid:'kb-move-grid',keys:[]}, servo:{grid:'kb-servo-grid',keys:[]}, posture:{grid:'kb-posture-grid',keys:[]}, misc:{grid:'kb-misc-grid',keys:[]} };
  Object.entries(KB_LABELS).forEach(([id,meta])=>{ cats[meta.cat].keys.push(id); });
  Object.entries(cats).forEach(([,{grid,keys}])=>{
    const el=document.getElementById(grid); if(!el) return; el.innerHTML='';
    keys.forEach(id=>{
      const meta=KB_LABELS[id], curKey=KB[id], dk=curKey===' '?'SPC':curKey.toUpperCase();
      const row=document.createElement('div'); row.className='kb-row'; row.id=`kb-row-${id}`;
      row.innerHTML=`<div class="kb-action">${meta.label}</div><input class="kb-input" id="kb-${id}" value="${dk}" readonly>`;
      el.appendChild(row);
      row.querySelector(`#kb-${id}`).addEventListener('click',()=>startListening(id,row.querySelector(`#kb-${id}`)));
    });
  });
  highlightConflicts();
  if (typeof buildPostureCfgUI === 'function') buildPostureCfgUI();
}

function startListening(id, inp) {
  if (listeningEl && listeningEl!==inp) listeningEl.classList.remove('listening');
  listeningEl=inp; inp.classList.add('listening'); inp.value='…';
  function onKey(e) {
    e.preventDefault(); e.stopPropagation();
    if (e.key==='Escape') { inp.value=KB[id]===' '?'SPC':KB[id].toUpperCase(); inp.classList.remove('listening'); listeningEl=null; window.removeEventListener('keydown',onKey,true); return; }
    KB[id]=e.key; inp.value=e.key===' '?'SPC':e.key.toUpperCase();
    inp.classList.remove('listening'); listeningEl=null; window.removeEventListener('keydown',onKey,true); highlightConflicts();
  }
  window.addEventListener('keydown', onKey, true);
}

function highlightConflicts() {
  const allKeys=Object.keys(KB), conflicts=new Set();
  allKeys.forEach((id,i)=>allKeys.forEach((id2,j)=>{ if(i!==j&&KB[id]===KB[id2]){conflicts.add(id);conflicts.add(id2);} }));
  allKeys.forEach(id=>{ const row=document.getElementById(`kb-row-${id}`); if(row) row.classList.toggle('kb-conflict',conflicts.has(id)); });
}


// ═══════════════════════════════════════
//  CUSTOM POSTURE NAMES + ANGLES (F6-F9)
// ═══════════════════════════════════════
const CUSTOM_POSTURE_IDS = ['giraff','stair','custom_1','custom_2','custom_3','custom_4'];
const CUSTOM_POSTURE_DEFAULTS = {
  giraff:   'GIRAFF',   stair:    'STAIR',
  custom_1: 'CUSTOM 1', custom_2: 'CUSTOM 2',
  custom_3: 'CUSTOM 3', custom_4: 'CUSTOM 4'
};

// เก็บ angles ของแต่ละ custom posture [J1,J2,J3,J4,J5,J6,Flip-F,Flip-R]
// giraff/stair มีค่า default จาก rescue_win.py
let customPostureAngles = {
  giraff:   [60, 130,  0,  90, 90, 70,  57,  80],  // F4 default
  stair:    [60, 130,  0,  90, 90, 70, 160,  45],  // F5 default
  custom_1: [60, 130,  0,  90, 90, 70, 100, 100],  // F6 default = HOME
  custom_2: [106, 103, 0, 69, 90, 70, 140, 45],  // F7 QR SCAN + Flipper down
  custom_3: [60, 130,  0,  90, 90, 70, 100, 100],  // F8 default = HOME
  custom_4: [60, 130,  0,  90, 90, 70, 100, 100],  // F9 default = HOME
};

let postureNames = { ...CUSTOM_POSTURE_DEFAULTS };

// รับ ack จาก rescue_win.py
function handleCustomPostureAck(msg) {
  if (!msg.ok) { addLog('Save ' + msg.name + ' failed', 'err', 'move'); return; }
  addLog('✓ ' + msg.name + ' saved to robot', 'ok', 'move');
  const btn = document.getElementById('posture-' + msg.name);
  if (btn) { btn.classList.remove('posture-slot-empty'); btn.classList.add('posture-slot-set'); }
}

function loadPostureNames() {
  try { const s=localStorage.getItem('rescuebot_posture_names'); if(s) postureNames={...CUSTOM_POSTURE_DEFAULTS,...JSON.parse(s)}; } catch(e){}
  try { const a=localStorage.getItem('rescuebot_posture_angles'); if(a) { const saved=JSON.parse(a); customPostureAngles={...customPostureAngles,...saved}; } } catch(e){}
  applyPostureNames();
}

function savePostureNames() {
  try { localStorage.setItem('rescuebot_posture_names', JSON.stringify(postureNames)); } catch(e){}
  try { localStorage.setItem('rescuebot_posture_angles', JSON.stringify(customPostureAngles)); } catch(e){}
  applyPostureNames();
}

function applyPostureNames() {
  CUSTOM_POSTURE_IDS.forEach(id => {
    const nameEl = document.getElementById('pname-' + id);
    if (nameEl) nameEl.textContent = postureNames[id] || CUSTOM_POSTURE_DEFAULTS[id];
    const actionId = 'posture_' + id;
    if (KB_LABELS[actionId]) KB_LABELS[actionId].label = postureNames[id] || CUSTOM_POSTURE_DEFAULTS[id];
    const btn = document.getElementById('posture-' + id);
    if (btn) {
      // ทุก slot มี default แล้ว — ไม่มี empty state
      btn.classList.remove('posture-slot-empty');
      btn.classList.add('posture-slot-set');
    }
  });
}

// ── บันทึกท่าปัจจุบันของหุ่น → custom posture ──
function saveCustomPosture(id) {
  const angles = [...state.angles];
  customPostureAngles[id] = angles;
  try { localStorage.setItem('rescuebot_posture_angles', JSON.stringify(customPostureAngles)); } catch(e){}
  sendCmd({ type: 'save_custom_posture', name: id, angles: angles });
  addLog('Saving ' + id + ': [' + angles.map(a=>Math.round(a)).join(', ') + ']', 'warn', 'move');
  applyPostureNames();
  buildPostureCfgUI();
}

function clearCustomPosture(id) {
  customPostureAngles[id] = null;
  try { localStorage.setItem('rescuebot_posture_angles', JSON.stringify(customPostureAngles)); } catch(e){}
  applyPostureNames();
  buildPostureCfgUI();
  addLog('Cleared ' + id, 'warn', 'move');
}

function buildPostureCfgUI() {
  const grid = document.getElementById('posture-cfg-grid');
  if (!grid) return;
  grid.innerHTML = '';
  CUSTOM_POSTURE_IDS.forEach((id, i) => {
    // giraff=F4, stair=F5, custom_1=F6...
    const keyMap = {giraff:'F4', stair:'F5', custom_1:'F6', custom_2:'F7', custom_3:'F8', custom_4:'F9'};
    const keyLabel  = keyMap[id] || KB['posture_' + id] || ('F' + (6+i));
    const angles    = customPostureAngles[id];
    const hasAngles = angles !== null;

    const row = document.createElement('div');
    row.className = 'posture-cfg-row';
    row.style.cssText = 'flex-direction:column;align-items:stretch;gap:6px;';

    // Row 1: key badge + name input + clear name
    const row1 = document.createElement('div');
    row1.style.cssText = 'display:flex;align-items:center;gap:8px;';
    row1.innerHTML =
      '<div class="posture-cfg-key-badge">' + keyLabel.toUpperCase() + '</div>' +
      '<input class="posture-cfg-input" id="pcfg-' + id + '"' +
             ' value="' + (postureNames[id] !== CUSTOM_POSTURE_DEFAULTS[id] ? postureNames[id] : '') + '"' +
             ' placeholder="' + CUSTOM_POSTURE_DEFAULTS[id] + '"' +
             ' maxlength="16" spellcheck="false" style="flex:1;">' +
      '<button class="posture-cfg-clear" onclick="clearPostureName(\'' + id + '\')" title="Reset name">✕</button>';
    row.appendChild(row1);
    row1.querySelector('#pcfg-' + id).addEventListener('input', function(e) {
      const val = e.target.value.trim().toUpperCase();
      postureNames[id] = val || CUSTOM_POSTURE_DEFAULTS[id];
      applyPostureNames();
    });

    // Row 2: angles display + SAVE + CLEAR
    const row2 = document.createElement('div');
    row2.style.cssText = 'display:flex;align-items:center;gap:6px;';

    const angDiv = document.createElement('div');
    angDiv.style.cssText = 'flex:1;font-family:var(--mono);font-size:9px;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:4px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
    if (hasAngles) {
      angDiv.style.color = 'var(--ok)';
      angDiv.textContent = angles.map(function(a,i){return 'J'+(i+1)+':'+Math.round(a);}).join(' ');
    } else {
      angDiv.style.color = 'var(--text-dim)';
      angDiv.textContent = 'จัดท่าหุ่นแล้วกด SAVE';
    }
    row2.appendChild(angDiv);

    const saveBtn = document.createElement('button');
    saveBtn.className = 'posture-cfg-clear';
    saveBtn.style.cssText = 'width:auto;padding:3px 10px;border-color:var(--ok);color:var(--ok);font-family:var(--head);font-size:8px;font-weight:700;letter-spacing:1px;white-space:nowrap;cursor:pointer;';
    saveBtn.textContent = '⏺ SAVE';
    saveBtn.title = 'บันทึกท่าปัจจุบันของหุ่น';
    saveBtn.onclick = function() { saveCustomPosture(id); };
    row2.appendChild(saveBtn);

    if (hasAngles) {
      const clrBtn = document.createElement('button');
      clrBtn.className = 'posture-cfg-clear';
      clrBtn.style.cssText = 'width:auto;padding:3px 8px;white-space:nowrap;cursor:pointer;';
      clrBtn.textContent = '🗑';
      clrBtn.title = 'ลบท่านี้';
      clrBtn.onclick = function() { clearCustomPosture(id); };
      row2.appendChild(clrBtn);
    }

    row.appendChild(row2);
    grid.appendChild(row);
  });
}

function clearPostureName(id) {
  postureNames[id] = CUSTOM_POSTURE_DEFAULTS[id];
  const inp = document.getElementById('pcfg-' + id);
  if (inp) inp.value = '';
  applyPostureNames();
}

function sendPosture(name) {
  // ส่งตรงไป rescue_win.py — ซึ่ง handle แต่ละ posture เอง
  // F7 custom_2: rescue_win.py จะส่ง GOTO 35 18 2 + flipper
  // F4-F9 อื่นๆ: rescue_win.py จะส่ง servo_set ทีละ joint
  sendCmd({ type: 'posture', name: name });
  addLog('Posture → ' + name, 'ok', 'move');
}

// ═══════════════════════════════════════
//  THEME
// ═══════════════════════════════════════
const THEMES = { cyan:{label:'CYBER BLUE',attr:''}, purple:{label:'NEON PURPLE',attr:'purple'}, orange:{label:'HEAT ORANGE',attr:'orange'} };
let currentTheme = 'cyan';
function setTheme(name) {
  currentTheme=name; const attr=THEMES[name]?.attr||'';
  if (attr) document.documentElement.setAttribute('data-theme',attr);
  else document.documentElement.removeAttribute('data-theme');
  Object.keys(THEMES).forEach(t=>{ document.getElementById(`theme-card-${t}`)?.classList.toggle('active',t===name); });
  try { localStorage.setItem('rescuebot_theme',name); } catch(e){}
  addLog(`Theme → ${THEMES[name].label}`,'ok');
}
function loadTheme() { try { const s=localStorage.getItem('rescuebot_theme'); if(s&&THEMES[s]) setTheme(s); } catch(e){} }


// ═══════════════════════════════════════
//  IK / HYBRID SETTINGS
// ═══════════════════════════════════════
let hybridJointAssign = { 0:true,1:true,2:false,3:true,4:false,5:false,6:false,7:false };
let ikOptions = { solution:'up', wristLock:true, ikWarn:true, autoFK:true, defStep:1 };

function loadIKCfg() {
  try {
    const s = localStorage.getItem('rescuebot_ik');
    if (!s) return;
    const d = JSON.parse(s);
    if (d.L1) ARM_CFG.L1=d.L1; if (d.L2) ARM_CFG.L2=d.L2; if (d.L3) ARM_CFG.L3=d.L3;
    if (d.S_OFFSET!==undefined) ARM_CFG.S_OFFSET=d.S_OFFSET;
    if (d.E_OFFSET!==undefined) ARM_CFG.E_OFFSET=d.E_OFFSET;
    if (d.W_OFFSET!==undefined) ARM_CFG.W_OFFSET=d.W_OFFSET;
    if (d.assign)  hybridJointAssign = {...hybridJointAssign,...d.assign};
    if (d.options) ikOptions = {...ikOptions,...d.options};
    cart.step = ikOptions.defStep;
    IK_JOINTS.clear();
    Object.entries(hybridJointAssign).forEach(([i,ik])=>{ if(ik) IK_JOINTS.add(+i); });
  } catch(e) {}
}

function saveIKCfg() {
  const L1=+document.getElementById('cfg-L1').value, L2=+document.getElementById('cfg-L2').value, L3=+document.getElementById('cfg-L3').value;
  const SO=+document.getElementById('cfg-S_OFFSET').value, EO=+document.getElementById('cfg-E_OFFSET').value, WO=+document.getElementById('cfg-W_OFFSET').value;
  ARM_CFG.L1=L1; ARM_CFG.L2=L2; ARM_CFG.L3=L3;
  ARM_CFG.S_OFFSET=SO; ARM_CFG.E_OFFSET=EO; ARM_CFG.W_OFFSET=WO;
  IK_JOINTS.clear(); Object.entries(hybridJointAssign).forEach(([i,ik])=>{ if(ik) IK_JOINTS.add(+i); });
  const data = {L1,L2,L3,S_OFFSET:SO,E_OFFSET:EO,W_OFFSET:WO,assign:hybridJointAssign,options:ikOptions};
  try { localStorage.setItem('rescuebot_ik',JSON.stringify(data)); } catch(e){}
  updateReachDisplay();
  addLog(`IK applied: L1=${L1} L2=${L2} L3=${L3}`,'ok');
  const btn=document.querySelector('[onclick="saveIKCfg()"]');
  if (btn) { const orig=btn.textContent; btn.textContent='✓ APPLIED!'; btn.style.background='var(--ok)'; btn.style.color='#000'; setTimeout(()=>{ btn.textContent=orig; btn.style.background=''; btn.style.color=''; },1200); }
}

function resetIKCfg() {
  ARM_CFG.L1=192; ARM_CFG.L2=133; ARM_CFG.L3=125;
  ARM_CFG.S_OFFSET=60; ARM_CFG.E_OFFSET=10; ARM_CFG.W_OFFSET=20;
  hybridJointAssign = {0:true,1:true,2:false,3:true,4:false,5:false,6:false,7:false};
  ikOptions = {solution:'up',wristLock:true,ikWarn:true,autoFK:true,defStep:1};
  cart.y=30.0; cart.z=18.0; cart.pitch=30.0; cart.step=1;
  IK_JOINTS.clear(); [0,1,3].forEach(i=>IK_JOINTS.add(i));
  try { localStorage.removeItem('rescuebot_ik'); } catch(e){}
  buildIKSettingsUI();
  addLog('IK reset → homeYZP y:30 z:18 p:30','warn');
}

function syncCfgRange(key) {
  const val = +document.getElementById(`cfg-${key}-range`).value;
  const inp = document.getElementById(`cfg-${key}`);
  if (inp) inp.value=val;
  updateReachDisplay();
}

function updateReachDisplay() {
  const L1=+document.getElementById('cfg-L1')?.value||ARM_CFG.L1;
  const L2=+document.getElementById('cfg-L2')?.value||ARM_CFG.L2;
  const maxR=L1+L2, minR=Math.abs(L1-L2);
  const disp=document.getElementById('reach-display'), dispMin=document.getElementById('reach-min-display'), bar=document.getElementById('reach-bar');
  if (disp) disp.textContent=maxR+' mm';
  if (dispMin) dispMin.textContent=minR+' mm';
  if (bar) bar.style.width=Math.min(100,maxR/6)+'%';
}

document.addEventListener('input', e => {
  const id=e.target.id;
  if (id?.startsWith('cfg-') && !id.endsWith('-range')) {
    const key=id.replace('cfg-','');
    const rangeEl=document.getElementById(`cfg-${key}-range`);
    if (rangeEl) rangeEl.value=e.target.value;
    updateReachDisplay();
  }
});

function buildJointAssignGrid() {
  const grid=document.getElementById('joint-assign-grid'); if(!grid) return;
  grid.innerHTML='';
  for (let i=0;i<NUM_SERVOS;i++) {
    const isIK=hybridJointAssign[i];
    const row=document.createElement('div');
    row.className='joint-assign-row'+(isIK?' ik-assigned':''); row.id=`ja-row-${i}`;
    row.innerHTML=`<div class="ja-idx">${i}</div><div class="ja-name">${SERVO_NAMES[i]}</div><div class="ja-btn ${isIK?'ja-ik':'ja-direct'}" id="ja-ik-${i}" onclick="setJointAssign(${i},true)">IK</div><div class="ja-btn ${!isIK?'ja-ik':'ja-direct'}" id="ja-dir-${i}" onclick="setJointAssign(${i},false)">DIRECT</div>`;
    grid.appendChild(row);
  }
}

function setJointAssign(i, useIK) {
  hybridJointAssign[i]=useIK;
  const row=document.getElementById(`ja-row-${i}`), ikBtn=document.getElementById(`ja-ik-${i}`), drBtn=document.getElementById(`ja-dir-${i}`);
  if (row) row.className='joint-assign-row'+(useIK?' ik-assigned':'');
  if (ikBtn) ikBtn.className='ja-btn '+(useIK?'ja-ik':'ja-direct');
  if (drBtn) drBtn.className='ja-btn '+(!useIK?'ja-ik':'ja-direct');
}

function setIKSolution(type)  { ikOptions.solution=type; document.getElementById('sol-elbow-up')?.classList.toggle('active',type==='up'); document.getElementById('sol-elbow-down')?.classList.toggle('active',type==='down'); }
function setDefaultCartStep(n,el) { ikOptions.defStep=n; cart.step=n; el.closest('div')?.querySelectorAll('.ik-sol-btn').forEach(b=>b.classList.toggle('active',b===el)); }
function toggleWristLock() { ikOptions.wristLock=!ikOptions.wristLock; const el=document.getElementById('toggle-wrist-lock'); if(el) el.dataset.on=ikOptions.wristLock; }
function toggleIKWarn()    { ikOptions.ikWarn=!ikOptions.ikWarn;       const el=document.getElementById('toggle-ik-warn');   if(el) el.dataset.on=ikOptions.ikWarn; }
function toggleAutoFK()    { ikOptions.autoFK=!ikOptions.autoFK;       const el=document.getElementById('toggle-auto-fk');   if(el) el.dataset.on=ikOptions.autoFK; }

function buildIKSettingsUI() {
  ['L1','L2','L3','S_OFFSET','E_OFFSET','W_OFFSET'].forEach(k=>{
    const el=document.getElementById(`cfg-${k}`), rangeEl=document.getElementById(`cfg-${k}-range`);
    if(el) el.value=ARM_CFG[k]; if(rangeEl) rangeEl.value=ARM_CFG[k];
  });
  updateReachDisplay();
  document.getElementById('sol-elbow-up')?.classList.toggle('active',  ikOptions.solution==='up');
  document.getElementById('sol-elbow-down')?.classList.toggle('active', ikOptions.solution==='down');
  [2,5,10,20].forEach(n=>{ document.getElementById(`defstep-${n}`)?.classList.toggle('active',n===ikOptions.defStep); });
  const tw=document.getElementById('toggle-wrist-lock'), tiw=document.getElementById('toggle-ik-warn'), taf=document.getElementById('toggle-auto-fk');
  if(tw)  tw.dataset.on  = ikOptions.wristLock;
  if(tiw) tiw.dataset.on = ikOptions.ikWarn;
  if(taf) taf.dataset.on = ikOptions.autoFK;
  buildJointAssignGrid();
}


// ═══════════════════════════════════════
//  LASER CONTROL
// ═══════════════════════════════════════
let laserState = false;

function setLaser(on) {
  laserState = on;
  sendCmd({ type: 'laser', value: on });
  const btnOn  = document.getElementById('btnLaserOn');
  const btnOff = document.getElementById('btnLaserOff');
  if (btnOn) {
    btnOn.style.borderColor = on ? 'var(--danger)' : '';
    btnOn.style.color       = on ? 'var(--danger)' : '';
    btnOn.style.background  = on ? '#200010'       : '';
  }
  if (btnOff) {
    btnOff.style.borderColor = !on ? 'var(--accent)' : '';
    btnOff.style.color       = !on ? 'var(--accent)' : '';
    btnOff.style.background  = !on ? '#061520'       : '';
  }
  addLog('Laser → ' + (on ? 'ON' : 'OFF'), on ? 'warn' : 'ok', 'move');
}


// ═══════════════════════════════════════
//  GEAR SPEED SETTINGS
// ═══════════════════════════════════════
const GEAR_DEFAULTS = {
  lin: [0.05, 0.15, 0.30, 0.50, 0.80],
  ang: [0.20, 0.50, 1.00, 1.80, 2.50],
};

function loadGearSpeeds() {
  try {
    const saved = localStorage.getItem('rescuebot_gears');
    if (!saved) return;
    const d = JSON.parse(saved);
    if (d.lin && d.lin.length === 5) LIN_GEARS.splice(0, 5, ...d.lin);
    if (d.ang && d.ang.length === 5) ANG_GEARS.splice(0, 5, ...d.ang);
  } catch(e) {}
  // sync inputs if settings open
  _syncGearInputs();
}

function _syncGearInputs() {
  for (let g = 1; g <= 5; g++) {
    const li = document.getElementById('linG-' + g);
    const ai = document.getElementById('angG-' + g);
    if (li) li.value = LIN_GEARS[g-1].toFixed(2);
    if (ai) ai.value = ANG_GEARS[g-1].toFixed(2);
  }
  // update gear button labels
  const linLabels = ['0.10','0.30','0.60','1.00','1.50']; // old placeholders
  for (let g = 1; g <= 5; g++) {
    const btn = document.getElementById('linG' + g);
    if (btn) { const spd = btn.querySelector('.gear-spd'); if (spd) spd.textContent = LIN_GEARS[g-1].toFixed(2); }
    const abtn = document.getElementById('angG' + g);
    if (abtn) { const spd = abtn.querySelector('.gear-spd'); if (spd) spd.textContent = ANG_GEARS[g-1].toFixed(2); }
  }
}

function saveGearSpeeds() {
  // Read from inputs
  const newLin = [], newAng = [];
  let valid = true;
  for (let g = 1; g <= 5; g++) {
    const lv = parseFloat(document.getElementById('linG-' + g)?.value);
    const av = parseFloat(document.getElementById('angG-' + g)?.value);
    if (isNaN(lv) || isNaN(av) || lv <= 0 || av <= 0) { valid = false; break; }
    // Ensure ascending order
    if (g > 1 && (lv <= newLin[g-2] || av <= newAng[g-2])) {
      addLog('⚠ Gear values must be ascending G1 < G2 < ... < G5', 'err'); return;
    }
    newLin.push(parseFloat(lv.toFixed(2)));
    newAng.push(parseFloat(av.toFixed(2)));
  }
  if (!valid) { addLog('⚠ Invalid gear values', 'err'); return; }

  LIN_GEARS.splice(0, 5, ...newLin);
  ANG_GEARS.splice(0, 5, ...newAng);

  try { localStorage.setItem('rescuebot_gears', JSON.stringify({lin:newLin, ang:newAng})); } catch(e){}

  // Re-apply current gear
  setLinGear(linGear);
  setAngGear(angGear);
  _syncGearInputs();

  addLog('Gear speeds saved ✓', 'ok');
  const btn = document.querySelector('[onclick="saveGearSpeeds()"]');
  if (btn) { const orig=btn.textContent; btn.textContent='✓ SAVED!'; btn.style.background='var(--ok)'; btn.style.color='#000'; setTimeout(()=>{ btn.textContent=orig; btn.style.background=''; btn.style.color=''; },1200); }
}

function resetGearSpeeds() {
  LIN_GEARS.splice(0, 5, ...GEAR_DEFAULTS.lin);
  ANG_GEARS.splice(0, 5, ...GEAR_DEFAULTS.ang);
  try { localStorage.removeItem('rescuebot_gears'); } catch(e){}
  setLinGear(2); setAngGear(2);
  _syncGearInputs();
  addLog('Gear speeds reset', 'warn');
}


// ═══════════════════════════════════════
//  INIT
// ═══════════════════════════════════════
buildServoList();
renderAll();
loadKeybinds();
applyKeybinds();
loadPostureNames();
loadTheme();
loadIKCfg();
loadGearSpeeds();
updateCartUI();
setCtrlMode(CTRL.JOINT);
window.addEventListener('load', () => {
  setTimeout(connectWS, 400);
  buildPostureCfgUI();
});