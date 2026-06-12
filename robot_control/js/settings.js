// ─── Settings & parameters ────────────────────────────────────────────────────
// Two stores:
//   • localStorage  — per-browser UI prefs (ws URL, auto-connect/brake, sim src)
//   • /api/settings — robot config persisted to settings.json by ws_server.py
//                     (gears, thresholds, sim init/cal, postures, controller map)

// ─── Settings persist (localStorage) ──────────────────────────────────────────
function saveSettings() {
  localStorage.setItem("rc_settings", JSON.stringify({
    wsUrl:       document.getElementById("ws-input").value,
    autoConnect: document.getElementById("auto-connect-chk").checked,
    autoBrake:   document.getElementById("auto-brake-chk").checked,
    simFromUDPS: document.getElementById("sim-from-udps-chk").checked,
    controlMode: controlMode,
  }));
}

function loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem("rc_settings") || "{}");
    if (s.wsUrl)      document.getElementById("ws-input").value = s.wsUrl;
    if (s.autoBrake)  {
      autoBrake = true;
      document.getElementById("auto-brake-chk").checked = true;
      document.getElementById("auto-brake-status").textContent = "Brakes engage when all keys released";
    }
    if (s.simFromUDPS) {
      simFromUDPS = true;
      document.getElementById("sim-from-udps-chk").checked = true;
      document.getElementById("sim-source-status").textContent = "Using UDPS ANGLES: feedback";
      const tag = document.getElementById("sim-source-tag");
      tag.textContent = "udps";
      tag.classList.add("live");
    }
    if (CONTROL_MODES.includes(s.controlMode)) {
      controlMode = s.controlMode;
    }
    updateModeUI();
    if (s.autoConnect) {
      document.getElementById("auto-connect-chk").checked = true;
      autoConnect = true;
      reconnectDelay = 1000;
      connectWS(true);
    }
  } catch(_) {}
}

// ─── Settings overlay ────────────────────────────────────────────────────────
function openSettings() {
  document.getElementById("settings-overlay").classList.add("open");
}
function closeSettings() {
  document.getElementById("settings-overlay").classList.remove("open");
}
function overlayClick(e) {
  if (e.target === document.getElementById("settings-overlay")) closeSettings();
}

function toggleAutoBrake() {
  autoBrake = document.getElementById("auto-brake-chk").checked;
  document.getElementById("auto-brake-status").textContent =
    autoBrake ? "Brakes engage when all keys released" : "";
  log("Auto-brake " + (autoBrake ? "ON" : "OFF"), "info");
  saveSettings();
}

function toggleSimSource() {
  simFromUDPS = document.getElementById("sim-from-udps-chk").checked;
  const tag = document.getElementById("sim-source-tag");
  const status = document.getElementById("sim-source-status");
  if (simFromUDPS) {
    tag.textContent = "udps";
    tag.classList.add("live");
    status.textContent = "Using UDPS ANGLES: feedback";
    log("Sim: angles from UDPS feedback", "info");
  } else {
    tag.textContent = "client";
    tag.classList.remove("live");
    status.textContent = "Using client-side slider positions";
    log("Sim: angles from client sliders", "info");
    pushSimFromSliders();
  }
  saveSettings();
}

// ─── Parameters overlay ───────────────────────────────────────────────────────
function openParams() {
  document.getElementById("params-overlay").classList.add("open");
  loadParams();
}
function closeParams() {
  document.getElementById("params-overlay").classList.remove("open");
}
function paramsOverlayClick(e) {
  if (e.target === document.getElementById("params-overlay")) closeParams();
}

function buildControllerParamUI() {
  Object.keys(CONTROLLER.posturesByButton).forEach(btn => {
    const sel = document.getElementById("ctl-posture-" + btn);
    if (!sel) return;
    sel.innerHTML = "";
    POSTURE_NAMES.forEach(name => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = postureLabel(name);
      sel.appendChild(opt);
    });
  });
}

function populateControllerParams() {
  buildControllerParamUI();
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  Object.keys(CONTROLLER.buttons).forEach(k => set("ctl-btn-" + k, CONTROLLER.buttons[k]));
  set("ctl-flip-step",   CONTROLLER.flipper.stepDeg);
  set("ctl-front-sign",  CONTROLLER.flipper.frontRaiseSign);
  set("ctl-back-sign",   CONTROLLER.flipper.backRaiseSign);
  set("ctl-trig-thresh", CONTROLLER.triggerThreshold);
  set("ctl-deadzone",    CONTROLLER.deadzone);
  Object.keys(CONTROLLER.posturesByButton).forEach(btn => set("ctl-posture-" + btn, CONTROLLER.posturesByButton[btn]));
}

function readControllerFromParams() {
  Object.keys(CONTROLLER.buttons).forEach(k => {
    const v = parseInt(document.getElementById("ctl-btn-" + k)?.value);
    if (!isNaN(v) && v >= 0) CONTROLLER.buttons[k] = v;
  });
  const step = parseFloat(document.getElementById("ctl-flip-step")?.value);
  if (!isNaN(step) && step > 0) CONTROLLER.flipper.stepDeg = step;
  const fs = parseInt(document.getElementById("ctl-front-sign")?.value);
  if (fs === 1 || fs === -1) CONTROLLER.flipper.frontRaiseSign = fs;
  const bs = parseInt(document.getElementById("ctl-back-sign")?.value);
  if (bs === 1 || bs === -1) CONTROLLER.flipper.backRaiseSign = bs;
  const tt = parseFloat(document.getElementById("ctl-trig-thresh")?.value);
  if (!isNaN(tt) && tt > 0 && tt < 1) CONTROLLER.triggerThreshold = tt;
  const dz = parseFloat(document.getElementById("ctl-deadzone")?.value);
  if (!isNaN(dz) && dz >= 0 && dz < 1) CONTROLLER.deadzone = dz;
  Object.keys(CONTROLLER.posturesByButton).forEach(btn => {
    const v = document.getElementById("ctl-posture-" + btn)?.value;
    if (POSTURE_NAMES.includes(v)) CONTROLLER.posturesByButton[btn] = v;
  });
}

// ─── Hotkey param UI ──────────────────────────────────────────────────────────
// Each binding is a "capture" input: focus it and press a key to rebind. The
// stored value is the raw key (single chars lowercased, e.g. "m"; function keys
// as "F1"). Escape/Tab leave the field without changing the binding.
function attachHotkeyCapture(input) {
  if (input.dataset.hkBound) return;
  input.dataset.hkBound = "1";
  input.readOnly = true;
  input.addEventListener("keydown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape" || e.key === "Tab") { input.blur(); return; }
    input.value = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  });
}

function buildHotkeyParamUI() {
  const grid = document.getElementById("hotkey-posture-grid");
  if (!grid) return;
  grid.innerHTML = "";
  POSTURE_NAMES.forEach(name => {
    const label = document.createElement("label");
    label.textContent = postureLabel(name);
    grid.appendChild(label);
    const input = document.createElement("input");
    input.type = "text";
    input.id = "hk-posture-" + name;
    input.className = "hotkey-input";
    input.placeholder = "unset";
    input.title = "Click and press a key to bind " + postureLabel(name);
    attachHotkeyCapture(input);
    grid.appendChild(input);
  });
}

function populateHotkeyParams() {
  buildHotkeyParamUI();
  const setHk = (id, v) => {
    const el = document.getElementById(id);
    if (!el) return;
    attachHotkeyCapture(el);
    el.value = v || "";
  };
  setHk("hk-cycleMode", HOTKEYS.cycleMode);
  setHk("hk-webcam",    HOTKEYS.webcam);
  POSTURE_NAMES.forEach(name => setHk("hk-posture-" + name, HOTKEYS.postures[name]));
}

function readHotkeysFromParams() {
  const cm = document.getElementById("hk-cycleMode")?.value.trim();
  if (cm) HOTKEYS.cycleMode = cm;
  const wc = document.getElementById("hk-webcam")?.value.trim();
  if (wc) HOTKEYS.webcam = wc;
  POSTURE_NAMES.forEach(name => {
    const v = document.getElementById("hk-posture-" + name)?.value.trim();
    if (v) HOTKEYS.postures[name] = v;
  });
}

function loadParams() {
  // Load from file-based API
  fetch("http://localhost:8766/api/settings")
    .then(r => r.json())
    .then(p => {
      // Update GEARS from file
      if (p.gears && Array.isArray(p.gears)) {
        for (let i = 0; i < p.gears.length; i++) {
          if (p.gears[i] > 0 && p.gears[i] <= 200) GEARS[i] = p.gears[i];
        }
      }
      // Update THRESHOLDS from file
      if (p.thresholds) {
        if (p.thresholds.tor) {
          THRESHOLDS.tor.warn = p.thresholds.tor.warn || 0.96;
          THRESHOLDS.tor.crit = p.thresholds.tor.crit || 2.0;
        }
        if (p.thresholds.tmp) {
          THRESHOLDS.tmp.warn = p.thresholds.tmp.warn || 60;
          THRESHOLDS.tmp.crit = p.thresholds.tmp.crit || 75;
        }
      }
      // Update SIM_INIT and SIM_CAL from file
      if (p.simInit) {
        SIM_KEYS.forEach(k => {
          if (p.simInit[k]) {
            if (p.simInit[k].cur !== undefined) SIM_INIT[k].cur = p.simInit[k].cur;
            if (p.simInit[k].tgt !== undefined) SIM_INIT[k].tgt = p.simInit[k].tgt;
          }
        });
      }
      if (p.simCal) {
        SIM_KEYS.forEach(k => {
          if (p.simCal[k]) {
            if (p.simCal[k].n     !== undefined) SIM_CAL[k].n     = p.simCal[k].n;
            if (p.simCal[k].dir   !== undefined) SIM_CAL[k].dir   = p.simCal[k].dir;
            if (p.simCal[k].scale !== undefined) SIM_CAL[k].scale = p.simCal[k].scale;
          }
        });
      }
      applyServoLimits(p.servoLimits);
      applyAvatarCalib(p.avatar);
      loadPostures(p);
      // Populate form fields
      for (let i = 0; i < GEARS.length; i++) {
        document.getElementById("gear-" + i).value = GEARS[i];
      }
      document.getElementById("tor-warn").value = THRESHOLDS.tor.warn;
      document.getElementById("tor-crit").value = THRESHOLDS.tor.crit;
      document.getElementById("tmp-warn").value = THRESHOLDS.tmp.warn;
      document.getElementById("tmp-crit").value = THRESHOLDS.tmp.crit;
      // Populate sim init fields
      SIM_KEYS.forEach(k => {
        const curEl = document.getElementById("sim-init-" + k + "-cur");
        const tgtEl = document.getElementById("sim-init-" + k + "-tgt");
        if (curEl) curEl.value = SIM_INIT[k].cur;
        if (tgtEl) tgtEl.value = SIM_INIT[k].tgt;
      });
      // Populate sim cal fields
      const calKeys = ['front','back','arm1','arm2','extend','arm4','gripper'];
      calKeys.forEach(k => {
        const nEl     = document.getElementById("cal-" + k + "-n");
        const dirEl   = document.getElementById("cal-" + k + "-dir");
        const scaleEl = document.getElementById("cal-" + k + "-scale");
        if (nEl)     nEl.value     = SIM_CAL[k].n;
        if (dirEl)   dirEl.value   = SIM_CAL[k].dir;
        if (scaleEl) scaleEl.value = SIM_CAL[k].scale;
      });
      mergeControllerSettings(p.controller);
      mergeHotkeySettings(p.hotkeys);
      populateControllerParams();
      populateHotkeyParams();
      buildPostureParamUI();
      buildServoLimitParamUI();
      buildAvatarCalibParamUI();
      const dbEl = document.getElementById("avatar-deadband");
      if (dbEl) dbEl.value = AVATAR_DEADBAND;
    })
    .catch(e => {
      populateControllerParams();
      populateHotkeyParams();
      buildPostureParamUI();
      buildServoLimitParamUI();
      buildAvatarCalibParamUI();
      const dbEl = document.getElementById("avatar-deadband");
      if (dbEl) dbEl.value = AVATAR_DEADBAND;
      log("Error loading parameters: " + e.message, "warn");
    });
}

function saveParams() {
  // Update GEARS array from form
  for (let i = 0; i < GEARS.length; i++) {
    const val = parseFloat(document.getElementById("gear-" + i).value);
    if (!isNaN(val) && val > 0 && val <= 200) {
      GEARS[i] = val;
    }
  }
  // Update THRESHOLDS from form
  const torWarn = parseFloat(document.getElementById("tor-warn").value);
  if (!isNaN(torWarn) && torWarn >= 0) THRESHOLDS.tor.warn = torWarn;

  const torCrit = parseFloat(document.getElementById("tor-crit").value);
  if (!isNaN(torCrit) && torCrit >= 0) THRESHOLDS.tor.crit = torCrit;

  const tmpWarn = parseFloat(document.getElementById("tmp-warn").value);
  if (!isNaN(tmpWarn) && tmpWarn >= 0) THRESHOLDS.tmp.warn = tmpWarn;

  const tmpCrit = parseFloat(document.getElementById("tmp-crit").value);
  if (!isNaN(tmpCrit) && tmpCrit >= 0) THRESHOLDS.tmp.crit = tmpCrit;

  readPosturesFromParams();
  readControllerFromParams();
  readHotkeysFromParams();
  readServoLimitsFromParams();
  readAvatarCalibFromParams();

  // Update SIM_INIT from form
  SIM_KEYS.forEach(k => {
    const curEl = document.getElementById("sim-init-" + k + "-cur");
    const tgtEl = document.getElementById("sim-init-" + k + "-tgt");
    if (curEl) { const v = parseFloat(curEl.value); if (!isNaN(v)) SIM_INIT[k].cur = v; }
    if (tgtEl) { const v = parseFloat(tgtEl.value); if (!isNaN(v)) SIM_INIT[k].tgt = v; }
  });

  // Update SIM_CAL from form
  const calKeys = ['front','back','arm1','arm2','extend','arm4','gripper'];
  calKeys.forEach(k => {
    const nEl     = document.getElementById("cal-" + k + "-n");
    const dirEl   = document.getElementById("cal-" + k + "-dir");
    const scaleEl = document.getElementById("cal-" + k + "-scale");
    if (nEl)     { const v = parseFloat(nEl.value);     if (!isNaN(v)) SIM_CAL[k].n     = v; }
    if (dirEl)   { const v = parseFloat(dirEl.value);   if (!isNaN(v)) SIM_CAL[k].dir   = v; }
    if (scaleEl) { const v = parseFloat(scaleEl.value); if (!isNaN(v)) SIM_CAL[k].scale = v; }
  });

  // Save to file via API
  fetch("http://localhost:8766/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      gears: GEARS,
      thresholds: THRESHOLDS,
      simInit: SIM_INIT,
      simCal: SIM_CAL,
      postures: POSTURES,
      controller: CONTROLLER,
      hotkeys: HOTKEYS,
      servoLimits: servoLimitsMap(),
      avatar: avatarCalibMap(),
    })
  })
  .then(r => r.json())
  .then(data => {
    buildGearUI();
    buildServoUI();
    buildPostureUI();
    // Push updated CAL to iframe immediately
    postSimState(simAngles);
    log("Parameters saved: " + GEARS.map((g,i) => `G${i+1}=${g}`).join("  "), "info");
    closeParams();
  })
  .catch(e => log("Error saving parameters: " + e.message, "error"));
}

function resetParams() {
  if (confirm("Reset all parameters to default values?")) {
    const defaults = {
      gears: [40, 80, 120, 160, 200],
      thresholds: {
        tor: { warn: 0.96, crit: 2.0 },
        tmp: { warn: 60, crit: 75 }
      },
      simInit: {
        front:   { cur: 85,  tgt: 85  },
        back:    { cur: 96,  tgt: 96  },
        arm1:    { cur: 175, tgt: 175 },
        arm2:    { cur: 30,  tgt: 30  },
        extend:  { cur: 0,   tgt: 0   },
        arm4:    { cur: 60,  tgt: 60  },
        arm5:    { cur: 110, tgt: 110 },
        gripper: { cur: 90,  tgt: 90  },
      },
      simCal: {
        front:   { n: 85,  dir: 1,  scale: 0.95 },
        back:    { n: 96,  dir: 1,  scale: 0.85 },
        arm1:    { n: 175, dir: 1,  scale: 1.2  },
        arm2:    { n: 30,  dir: 1,  scale: 1.5  },
        extend:  { n: 0,   dir: -1, scale: 1.0  },
        arm4:    { n: 60,  dir: -1, scale: 1.0  },
        arm5:    { n: 90,  dir: 1,  scale: 1.0  },
        gripper: { n: 90,  dir: 1,  scale: 1.0  },
      },
      postures: createDefaultPostures(),
      controller: JSON.parse(JSON.stringify(CONTROLLER_DEFAULTS)),
      hotkeys: JSON.parse(JSON.stringify(HOTKEYS_DEFAULTS)),
      servoLimits: Object.fromEntries(SERVO_LIMIT_DEFAULTS.map(s => [s.id, { min: s.min, max: s.max }])),
      avatar: { deadband: AVATAR_DEADBAND_DEFAULT, calib: AVATAR_CALIB_DEFAULTS.map(c => ({ ...c })) },
    };

    fetch("http://localhost:8766/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(defaults)
    })
    .then(() => {
      GEARS.length = 0;
      GEARS.push(...defaults.gears);
      THRESHOLDS.tor = defaults.thresholds.tor;
      THRESHOLDS.tmp = defaults.thresholds.tmp;
      SIM_KEYS.forEach(k => {
        if (defaults.simInit[k]) { SIM_INIT[k].cur = defaults.simInit[k].cur; SIM_INIT[k].tgt = defaults.simInit[k].tgt; }
        if (defaults.simCal[k])  { SIM_CAL[k].n = defaults.simCal[k].n; SIM_CAL[k].dir = defaults.simCal[k].dir; SIM_CAL[k].scale = defaults.simCal[k].scale; }
      });
      applyServoLimits(defaults.servoLimits);
      applyAvatarCalib(defaults.avatar);
      loadPostures(defaults);
      mergeControllerSettings(defaults.controller);
      mergeHotkeySettings(defaults.hotkeys);
      populateControllerParams();
      populateHotkeyParams();
      buildGearUI();
      buildServoUI();
      buildPostureUI();
      buildPostureParamUI();
      buildServoLimitParamUI();
      buildAvatarCalibParamUI();
      const dbEl = document.getElementById("avatar-deadband");
      if (dbEl) dbEl.value = AVATAR_DEADBAND;
      postSimState(simAngles);
      log("Parameters reset to defaults", "info");
      closeParams();
    })
    .catch(e => log("Error resetting parameters: " + e.message, "error"));
  }
}

// ─── Parameters persist ───────────────────────────────────────────────────────
function loadParamSettings() {
  // Load from file-based API on startup
  fetch("http://localhost:8766/api/settings")
    .then(r => r.json())
    .then(p => {
      if (p.gears && Array.isArray(p.gears)) {
        for (let i = 0; i < p.gears.length; i++) {
          if (p.gears[i] > 0 && p.gears[i] <= 200) GEARS[i] = p.gears[i];
        }
      }
      if (p.thresholds) {
        if (p.thresholds.tor) {
          THRESHOLDS.tor.warn = p.thresholds.tor.warn || 0.96;
          THRESHOLDS.tor.crit = p.thresholds.tor.crit || 2.0;
        }
        if (p.thresholds.tmp) {
          THRESHOLDS.tmp.warn = p.thresholds.tmp.warn || 60;
          THRESHOLDS.tmp.crit = p.thresholds.tmp.crit || 75;
        }
      }
      if (p.simInit) {
        SIM_KEYS.forEach(k => {
          if (p.simInit[k]) {
            if (p.simInit[k].cur !== undefined) SIM_INIT[k].cur = p.simInit[k].cur;
            if (p.simInit[k].tgt !== undefined) SIM_INIT[k].tgt = p.simInit[k].tgt;
          }
        });
      }
      if (p.simCal) {
        SIM_KEYS.forEach(k => {
          if (p.simCal[k]) {
            if (p.simCal[k].n     !== undefined) SIM_CAL[k].n     = p.simCal[k].n;
            if (p.simCal[k].dir   !== undefined) SIM_CAL[k].dir   = p.simCal[k].dir;
            if (p.simCal[k].scale !== undefined) SIM_CAL[k].scale = p.simCal[k].scale;
          }
        });
      }
      applyServoLimits(p.servoLimits);
      applyAvatarCalib(p.avatar);
      loadPostures(p);
      mergeControllerSettings(p.controller);
      mergeHotkeySettings(p.hotkeys);
      // Rebuild UI with loaded settings
      buildGearUI();
      buildServoUI();
      buildPostureUI();
      log("Gears: " + GEARS.map((g,i) => `G${i+1}=${g}`).join("  "));
      // Push initial sim state after a short delay (give iframe time to load)
      setTimeout(() => pushSimFromSliders(), 1500);
    })
    .catch(e => {
      log("Warning: Could not load parameters from file. Using defaults. (" + e.message + ")", "warn");
      // Build UI with defaults as fallback
      buildGearUI();
      buildServoUI();
      buildPostureUI();
      log("Gears: " + GEARS.map((g,i) => `G${i+1}=${g}`).join("  "));
      setTimeout(() => pushSimFromSliders(), 1500);
    });
  return { gears: GEARS, thresholds: THRESHOLDS };
}
