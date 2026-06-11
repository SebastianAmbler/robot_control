// ─── Shared state & config ────────────────────────────────────────────────────
// Loaded FIRST. Holds the constants and mutable globals every other module
// references. All files share one global scope (classic scripts), so anything
// declared here is visible everywhere below.

// ─── Constants ────────────────────────────────────────────────────────────────
const MAX_RPM = 200;
const GEARS   = [40, 80, 120, 160, 200];  // RPM ceiling per gear
const SEND_HZ = 20;  // command rate in Hz

const THRESHOLDS = {
  tor: { warn: 0.96, crit: 2.0 },   // Nm: rated torque (warn), stall torque (crit)
  tmp: { warn: 60,   crit: 75  },
};

// ─── Sim ──────────────────────────────────────────────────────────────────────
// Maps SERVOS index → sim servo key (matches IDX_TO_KEY in index.html)
const SIM_KEYS = ['front','back','arm1','arm2','extend','arm4','arm5','gripper'];

let simFromUDPS = false;  // if true, use ANGLES: feedback; else use slider positions

// Initial servo states for the simulation
const SIM_INIT = {
  front:   { cur: 85,  tgt: 85  },
  back:    { cur: 96,  tgt: 96  },
  arm1:    { cur: 175, tgt: 175 },
  arm2:    { cur: 30,  tgt: 30  },
  extend:  { cur: 0,   tgt: 0   },
  arm4:    { cur: 60,  tgt: 60  },
  arm5:    { cur: 110, tgt: 110 },
  gripper: { cur: 90,  tgt: 90  },
};

// Calibration values for the simulation
const SIM_CAL = {
  front:   { n: 85,  dir: 1,  scale: 0.95 },
  back:    { n: 96,  dir: 1,  scale: 0.85 },
  arm1:    { n: 175, dir: 1,  scale: 1.2  },
  arm2:    { n: 30,  dir: 1,  scale: 1.5  },
  extend:  { n: 0,   dir: -1, scale: 1.0  },
  arm4:    { n: 60,  dir: -1, scale: 1.0  },
  arm5:    { n: 90,  dir: 1,  scale: 1.0  },
  gripper: { n: 90,  dir: 1,  scale: 1.0  },
};

// Track latest angles for the sim (indexed 0-7)
let simAngles = SIM_KEYS.map(k => SIM_INIT[k].tgt);

// ─── State ────────────────────────────────────────────────────────────────────
let ws        = null;
let gear      = 0;   // 0-indexed gear (0 = gear 1)
let isLocked  = false;
let autoBrake = false;
let keys      = { w:false, a:false, s:false, d:false };
let lastLinear  = null;
let lastAngular = null;

// ─── Servo definitions (matches Arduino sketch) ───────────────────────────────
// id: 1-based servo id sent in JSON cmd
// min/max: hard limits from SERVO_MIN/MAX in sketch
// home: initial angle set at boot
const SERVOS = [
  { id: 1, key: "y", name: "Front Flip", min:   0, max: 180, home:  90 },
  { id: 2, key: "u", name: "Back Flip",  min:  30, max: 150, home:  90 },
  { id: 3, key: "i", name: "Shoulder",   min:   0, max: 180, home:  90 },
  { id: 4, key: "o", name: "Elbow",      min:  25, max: 150, home:  25 },
  { id: 5, key: "h", name: "Extend",     min:   0, max: 180, home:  20 },
  { id: 6, key: "j", name: "Wrist",      min:   0, max: 180, home:  85 },
  { id: 7, key: "k", name: "Wrist Roll", min:   0, max: 180, home: 122 },
  { id: 8, key: "l", name: "Gripper",    min:   0, max: 180, home:  90 },
];
const SERVO_STEP = 5;
let activeServoId = null;

// In-code defaults for servo travel limits (used by Reset)
const SERVO_LIMIT_DEFAULTS = SERVOS.map(s => ({ id: s.id, min: s.min, max: s.max }));

// Apply a saved {id:{min,max}} map onto SERVOS, validating each entry.
// Invalid/missing values keep the servo's existing limit.
function applyServoLimits(saved) {
  if (!saved) return;
  SERVOS.forEach(s => {
    const lim = saved[s.id] || saved[String(s.id)];
    if (!lim) return;
    let min = Number(lim.min);
    let max = Number(lim.max);
    if (!Number.isFinite(min)) min = s.min;
    if (!Number.isFinite(max)) max = s.max;
    min = Math.max(0, Math.min(180, Math.round(min)));
    max = Math.max(0, Math.min(180, Math.round(max)));
    if (min < max) { s.min = min; s.max = max; }
  });
}

// Build a {id:{min,max}} map from current SERVOS (for persistence)
function servoLimitsMap() {
  return Object.fromEntries(SERVOS.map(s => [s.id, { min: s.min, max: s.max }]));
}
const POSTURE_NAMES = ['home','stair','ramp','fold','giraffe','finish','backramp'];
const DEFAULT_POSTURE_ANGLES = [90, 90, 90, 90, 90, 90, 90, 90];
const POSTURES = Object.fromEntries(
  POSTURE_NAMES.map(name => [name, DEFAULT_POSTURE_ANGLES.slice()])
);
let activePostureName = null;

// ─── Controller (gamepad) config ──────────────────────────────────────────────
// Default button indices follow the W3C "standard" gamepad mapping, which both
// Xbox and PS4 (DualShock) pads expose in modern browsers. Remappable in
// Parameters for pads that report a non-standard mapping.
const CONTROL_MODES = ["keyboard", "controller", "avatar"];
const MODE_LABELS = { keyboard: "⌨ Keyboard", controller: "🎮 Controller", avatar: "👤 Avatar" };
let controlMode  = "keyboard";
let gamepadIndex = null;
let gpPrev = {};   // previous pressed state per logical button (edge detection)

const FLIPPER_FRONT_ID = 1;  // SERVOS id "Front Flip"
const FLIPPER_BACK_ID  = 2;  // SERVOS id "Back Flip"

const CONTROLLER_DEFAULTS = {
  buttons: { dpadUp: 12, dpadDown: 13, dpadLeft: 14, dpadRight: 15,
             lb: 4, rb: 5, lt: 6, rt: 7, r3: 11, a: 0, b: 1, x: 2, y: 3 },
  posturesByButton: { a: "home", b: "fold", x: "stair", y: "finish" },
  flipper: { stepDeg: 2, frontRaiseSign: 1, backRaiseSign: 1 },
  triggerThreshold: 0.5,
  deadzone: 0.15,
};
const CONTROLLER = JSON.parse(JSON.stringify(CONTROLLER_DEFAULTS));

function mergeControllerSettings(c) {
  if (!c) return;
  if (c.buttons) {
    Object.keys(CONTROLLER.buttons).forEach(k => {
      const v = parseInt(c.buttons[k]);
      if (!isNaN(v) && v >= 0) CONTROLLER.buttons[k] = v;
    });
  }
  if (c.posturesByButton) {
    Object.keys(CONTROLLER.posturesByButton).forEach(k => {
      if (POSTURE_NAMES.includes(c.posturesByButton[k])) CONTROLLER.posturesByButton[k] = c.posturesByButton[k];
    });
  }
  if (c.flipper) {
    const step = parseFloat(c.flipper.stepDeg);
    if (!isNaN(step) && step > 0) CONTROLLER.flipper.stepDeg = step;
    if (c.flipper.frontRaiseSign === 1 || c.flipper.frontRaiseSign === -1) CONTROLLER.flipper.frontRaiseSign = c.flipper.frontRaiseSign;
    if (c.flipper.backRaiseSign  === 1 || c.flipper.backRaiseSign  === -1) CONTROLLER.flipper.backRaiseSign  = c.flipper.backRaiseSign;
  }
  const tt = parseFloat(c.triggerThreshold);
  if (!isNaN(tt) && tt > 0 && tt < 1) CONTROLLER.triggerThreshold = tt;
  const dz = parseFloat(c.deadzone);
  if (!isNaN(dz) && dz >= 0 && dz < 1) CONTROLLER.deadzone = dz;
}
