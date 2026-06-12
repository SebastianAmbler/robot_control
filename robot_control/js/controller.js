// ─── Gamepad / controller input ───────────────────────────────────────────────
// Polls a connected gamepad and maps it onto the existing pipelines: D-pad →
// `keys` (drive), triggers/bumpers → flipper servos, R3 → gear, face buttons →
// postures. Active only while controlMode === "controller".

function updateGamepadStatusUI() {
  const dot  = document.getElementById("gp-dot");
  const name = document.getElementById("gp-name");
  if (!dot || !name) return;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const gp = gamepadIndex !== null ? pads[gamepadIndex] : null;
  if (gp) {
    dot.classList.add("on");
    name.textContent = gp.id + (gp.mapping === "standard" ? "" : "  [non-standard mapping — remap in Parameters]");
  } else {
    dot.classList.remove("on");
    name.textContent = "No gamepad — press any button";
  }
}

window.addEventListener("gamepadconnected", e => {
  if (gamepadIndex === null) gamepadIndex = e.gamepad.index;
  log("Gamepad connected: " + e.gamepad.id + " (mapping: " + (e.gamepad.mapping || "non-standard") + ")", "info");
  updateGamepadStatusUI();
});

window.addEventListener("gamepaddisconnected", e => {
  if (e.gamepad.index === gamepadIndex) {
    gamepadIndex = null;
    gpPrev = {};
    if (controlMode === "controller") {
      keys.w = keys.a = keys.s = keys.d = false;
      applyMoveChange();  // safety stop
    }
  }
  log("Gamepad disconnected: " + e.gamepad.id, "warn");
  updateGamepadStatusUI();
});

function activeGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  if (gamepadIndex !== null && pads[gamepadIndex]) return pads[gamepadIndex];
  // Adopt the first available pad (Chrome only exposes pads after a button press)
  for (const gp of pads) {
    if (gp) { gamepadIndex = gp.index; updateGamepadStatusUI(); return gp; }
  }
  return null;
}

function padDown(gp, name) {
  const b = gp.buttons[CONTROLLER.buttons[name]];
  if (!b) return false;
  return b.pressed || b.value > CONTROLLER.triggerThreshold;
}

function setDpadVisual(up, down, left, right) {
  const set = (id, on) => {
    const el = document.getElementById(id);
    if (el) el.className = "key" + (on ? " active" : "");
  };
  set("dpad-up", up); set("dpad-down", down);
  set("dpad-left", left); set("dpad-right", right);
}

function nudgeFlipper(id, delta) {
  const s = SERVOS.find(sv => sv.id === id);
  if (!s) return;
  const cur = getServoAngle(id);
  const target = Math.max(s.min, Math.min(s.max, Math.round(cur + delta)));
  if (target !== cur) setServoAngle(id, target);
}

function pollGamepad() {
  if (controlMode !== "controller") return;
  const gp = activeGamepad();
  if (!gp) return;

  // D-pad → movement, reusing the keyboard motion pipeline via `keys`
  const dUp = padDown(gp, "dpadUp"), dDown = padDown(gp, "dpadDown");
  const dLeft = padDown(gp, "dpadLeft"), dRight = padDown(gp, "dpadRight");
  setDpadVisual(dUp, dDown, dLeft, dRight);
  if (dUp !== keys.w || dDown !== keys.s || dLeft !== keys.a || dRight !== keys.d) {
    keys.w = dUp; keys.s = dDown; keys.a = dLeft; keys.d = dRight;
    applyMoveChange();
  }

  // Triggers/bumpers → flippers, hold-to-move
  const step = CONTROLLER.flipper.stepDeg;
  if (padDown(gp, "rt")) nudgeFlipper(FLIPPER_FRONT_ID,  CONTROLLER.flipper.frontRaiseSign * step);
  if (padDown(gp, "rb")) nudgeFlipper(FLIPPER_FRONT_ID, -CONTROLLER.flipper.frontRaiseSign * step);
  if (padDown(gp, "lt")) nudgeFlipper(FLIPPER_BACK_ID,   CONTROLLER.flipper.backRaiseSign * step);
  if (padDown(gp, "lb")) nudgeFlipper(FLIPPER_BACK_ID,  -CONTROLLER.flipper.backRaiseSign * step);

  // Edge-detected buttons
  const edge = name => {
    const down = padDown(gp, name);
    const was = gpPrev[name];
    gpPrev[name] = down;
    return down && !was;
  };
  if (edge("r3")) cycleGear();
  Object.keys(CONTROLLER.posturesByButton).forEach(btn => {
    if (edge(btn)) applyPosture(CONTROLLER.posturesByButton[btn]);
  });
}

setInterval(pollGamepad, 50);
