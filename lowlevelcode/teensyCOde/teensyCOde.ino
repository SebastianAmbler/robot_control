#include <PWMServo.h>
#include <ArduinoJson.h>
#include <math.h>

// ── Forward Kinematics (YZ plane) ────────────────────────────
struct Vec2 { float y, z; };

// ── PIN assignments ──────────────────────────────────────────+
const int PIN_FRONT = 10;
const int PIN_BACK  = 9;
const int PIN_ARM1  = 7;   // IK Shoulder (q1)
const int PIN_ARM2  = 6;   // IK Elbow    (q2)
const int PIN_ARM3  = 5;  // IK Wrist    (q3)
const int PIN_ARM4  = 4;
const int PIN_ARM5  = 3;
const int PIN_MOTOR = 2;
const int LASER = 11;
const int LED = 12;

// ── Servo objects ────────────────────────────────────────────
PWMServo servoFrontMotor;
PWMServo servoBackMotor;
PWMServo servoarm1;      // IK Shoulder
PWMServo servoarm2;      // IK Elbow
PWMServo servoarm3;      // IK Wrist
PWMServo servoarm4;
PWMServo servoarm5;
PWMServo gripper;

// ── Homing flag ──────────────────────────────────────────────
bool homed = false;

// ── Serial input buffer ──────────────────────────────────────
String inputBuffer = "";

// ── Current servo angles ─────────────────────────────────────
int currentAngles[8] = {90, 90, 165, 23, 20, 85, 122, 90};
//                       0     1     2    3   4     5    6   7
// idx:                front back  arm1 arm2 arm3 arm4 arm5 grip

#define LED_PIN             13
#define ANGLE_BROADCAST_MS  100


// ═══════════════════════════════════════════════════════════════
//  HARD-CODED JOINT LIMITS
//  Index = servo id - 1
//  Gripper (id 8, index 7): clamped dynamically to [motorReverse, motorGrip]
//  in moveServoById(); the values here are secondary guards only.
// ═══════════════════════════════════════════════════════════════

const int SERVO_MIN[8] = {0,   30,  0,  25,  0,  0,  0,  0};

const int SERVO_MAX[8] = {180, 150, 180, 150, 180, 180, 180, 180};

// ── Helper: clamp angle to per-joint hard limits ─────────────
// id is 1-based (servo id as used in JSON commands)
int clampToLimits(int id, int angle) {
  if (id < 1 || id > 8) return angle;   // safety: unknown id, pass through
  int idx = id - 1;
  int lo  = SERVO_MIN[idx];
  int hi  = SERVO_MAX[idx];
  if (angle < lo) {
    Serial.print(F("[LIMIT] id ")); Serial.print(id);
    Serial.print(F(" clamped ")); Serial.print(angle);
    Serial.print(F(" → ")); Serial.println(lo);
    return lo;
  }
  if (angle > hi) {
    Serial.print(F("[LIMIT] id ")); Serial.print(id);
    Serial.print(F(" clamped ")); Serial.print(angle);
    Serial.print(F(" → ")); Serial.println(hi);
    return hi;
  }
  return angle;
}


// ═══════════════════════════════════════════════════════════════
//  IK CONFIGURATION  (units: cm — match your arm measurements)
// ═══════════════════════════════════════════════════════════════

// Link lengths (cm)
float IK_H  = 11.5f;   // floor → Shoulder pivot height
float IK_L1 = 18.5f;   // Shoulder → Elbow
float IK_L2 = 13.5f;   // Elbow → Wrist
float IK_L3 = 12.0f;   // Wrist → TCP (end-effector)

// Joint offsets: raw servo angle when joint is at 0°
float IK_OFF[3] = { 30.0f, 20.0f, 20.0f };   // [Shoulder, Elbow, Wrist]

// Inversion flags: true = servo decreases as joint angle increases
bool  IK_INV[3] = { false, true, false };

// Servo angle clamps for IK joints — kept in sync with SERVO_MIN/MAX
// [Shoulder=id3, Elbow=id4, Wrist=id5]
float IK_SV_MIN[3] = { 90.0f,  60.0f,  0.0f };
float IK_SV_MAX[3] = { 180.0f, 180.0f, 90.0f };

// IK solution branch: true = elbow-up, false = elbow-down
bool IK_ELBOW_UP = true;


// ═══════════════════════════════════════════════════════════════
//  IK MATH HELPERS
// ═══════════════════════════════════════════════════════════════

float ik_r2d(float r) { return r * 180.0f / PI; }
float ik_d2r(float d) { return d * PI / 180.0f; }
float ik_cf(float x, float a, float b) { return x < a ? a : (x > b ? b : x); }

// Convert joint angle (degrees from 0° reference) → raw servo angle
float jointToServo(int id, float jDeg) {
  float raw = IK_INV[id]
    ? (IK_OFF[id] - jDeg)
    : (IK_OFF[id] + jDeg);
  return ik_cf(raw, IK_SV_MIN[id], IK_SV_MAX[id]);
}


Vec2 fk2D(float q1d, float q2d, float q3d) {
  float q1 = ik_d2r(q1d);
  float q2 = ik_d2r(q2d);
  float q3 = ik_d2r(q3d);
  float pitch = q1 + q2 + q3;
  float y = IK_L1 * cos(q1) + IK_L2 * cos(q1 + q2) + IK_L3 * cos(pitch);
  float z = IK_H  + IK_L1 * sin(q1) + IK_L2 * sin(q1 + q2) + IK_L3 * sin(pitch);
  return { y, z };
}

// ── Inverse Kinematics (YZ plane) ────────────────────────────
bool solveIK(float y, float z, float pitchDeg,
            float &q1, float &q2, float &q3) {

  float pitch = ik_d2r(pitchDeg);

  float yw = y  - IK_L3 * cos(pitch);
  float zw = (z - IK_H) - IK_L3 * sin(pitch);

  float dist2 = yw * yw + zw * zw;
  float cq2   = (dist2 - IK_L1 * IK_L1 - IK_L2 * IK_L2)
                / (2.0f * IK_L1 * IK_L2);

  if (cq2 < -1.0f || cq2 > 1.0f) {
    Serial.print(F("[IK FAIL] target out of reach. y="));
    Serial.print(y, 2);
    Serial.print(F(" z=")); Serial.print(z, 2);
    Serial.print(F(" dist=")); Serial.println(sqrt(dist2), 2);
    return false;
  }

  cq2 = ik_cf(cq2, -1.0f, 1.0f);
  float sq2 = sqrt(1.0f - cq2 * cq2);

  float _q2, _q1;
  if (IK_ELBOW_UP) {
    _q2 = atan2(-sq2, cq2);
  } else {
    _q2 = atan2( sq2, cq2);
  }
  _q1 = atan2(zw, yw) - atan2(IK_L2 * sin(_q2), IK_L1 + IK_L2 * cos(_q2));
  float _q3 = pitch - _q1 - _q2;

  q1 = ik_r2d(_q1);
  q2 = ik_r2d(_q2);
  q3 = ik_r2d(_q3);

  Vec2 fk = fk2D(q1, q2, q3);
  float ey = fabs(fk.y - y);
  float ez = fabs(fk.z - z);
  if (ey > 0.5f || ez > 0.5f) {
    Serial.print(F("[IK WARN] FK error: ey="));
    Serial.print(ey, 2);
    Serial.print(F(" ez=")); Serial.println(ez, 2);
  }

  return true;
}

// ── Apply IK solution to arm servos ──────────────────────────
// Joint limits are enforced via clampToLimits() (id 3, 4, 5).
bool applyIK(float y, float z, float pitchDeg) {
  float q1, q2, q3;
  if (!solveIK(y, z, pitchDeg, q1, q2, q3)) return false;

  // Compute raw servo angles (IK_SV_MIN/MAX first pass from jointToServo)
  int s0 = (int)ik_cf(jointToServo(0, q1), 0, 180);  // Shoulder → id 3
  int s1 = (int)ik_cf(jointToServo(1, q2), 0, 180);  // Elbow    → id 4
  int s2 = (int)ik_cf(jointToServo(2, q3), 0, 180);  // Wrist    → id 5

  // Apply hard joint limits (second pass, consistent with direct servo cmd)
  s0 = clampToLimits(3, s0);
  s1 = clampToLimits(4, s1);
  s2 = clampToLimits(5, s2);

  servoarm1.write(s0); currentAngles[2] = s0;
  servoarm2.write(s1); currentAngles[3] = s1;
  servoarm3.write(s2); currentAngles[4] = s2;

  Serial.print(F("[IK OK] q1=")); Serial.print(q1, 1);
  Serial.print(F(" q2="));        Serial.print(q2, 1);
  Serial.print(F(" q3="));        Serial.print(q3, 1);
  Serial.print(F("  sv="));       Serial.print(s0);
  Serial.print(F(","));           Serial.print(s1);
  Serial.print(F(","));           Serial.println(s2);
  return true;
}


// ═══════════════════════════════════════════════════════════════
//  SERVO CONTROL
// ═══════════════════════════════════════════════════════════════

// All angle commands pass through clampToLimits() before writing.
void moveServoById(int id, int angle) {

  // Apply hard joint limits first for all ids except 8
  // (id 8 gripper has its own directional logic below)
  if (id >= 1 && id <= 8) {
    angle = clampToLimits(id, angle);
  }

  switch (id) {
    case 1:
      servoFrontMotor.write(angle);
      currentAngles[0] = angle;
      break;
    case 2:
      servoBackMotor.write(angle);
      currentAngles[1] = angle;
      break;
    case 3:
      servoarm1.write(angle);
      currentAngles[2] = angle;
      break;
    case 4:
      servoarm2.write(angle);
      currentAngles[3] = angle;
      break;
    case 5:
      servoarm3.write(angle);
      currentAngles[4] = angle;
      break;
    case 6:
      servoarm4.write(angle);
      currentAngles[5] = angle;
      break;
    case 7:
      servoarm5.write(angle);
      currentAngles[6] = angle;
      break;
    case 8: {
      gripper.write(angle);
      currentAngles[7] = angle;
      break;
    }
  }
}


// ═══════════════════════════════════════════════════════════════
//  JSON HANDLER
// ═══════════════════════════════════════════════════════════════

void handleJson(String &line) {
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    Serial.print(F("JSON err: "));
    Serial.println(err.c_str());
    return;
  }

  const char *cmd = doc["cmd"];
  if (!cmd) return;

  // ── Direct servo ──────────────────────────────────────────
  if (strcmp(cmd, "servo") == 0) {
    int id    = doc["id"]    | 0;
    int angle = doc["angle"] | 90;
    moveServoById(id, angle);
  }

  // ── Gripper config ───────────────────────────────────────
  // else if (strcmp(cmd, "config") == 0) {
  //   if (doc.containsKey("stop"))    motorStop    = doc["stop"];
  //   if (doc.containsKey("grip"))    motorGrip    = doc["grip"];
  //   if (doc.containsKey("reverse")) motorReverse = doc["reverse"];
  //   Serial.print(F("Config: stop="));  Serial.print(motorStop);
  //   Serial.print(F(" grip="));         Serial.print(motorGrip);
  //   Serial.print(F(" reverse="));      Serial.println(motorReverse);
  // }

  // ── IK move ──────────────────────────────────────────────
  else if (strcmp(cmd, "ik") == 0) {
    if (!doc.containsKey("y") || !doc.containsKey("z")) {
      Serial.println(F("[IK ERR] missing y or z"));
    } else {
      float y     = doc["y"].as<float>();
      float z     = doc["z"].as<float>();
      float pitch = doc["pitch"] | 0.0f;
      applyIK(y, z, pitch);
    }
  }

  // ── IK config ────────────────────────────────────────────
  else if (strcmp(cmd, "ikconfig") == 0) {
    if (doc.containsKey("H"))       IK_H        = doc["H"].as<float>();
    if (doc.containsKey("L1"))      IK_L1       = doc["L1"].as<float>();
    if (doc.containsKey("L2"))      IK_L2       = doc["L2"].as<float>();
    if (doc.containsKey("L3"))      IK_L3       = doc["L3"].as<float>();
    if (doc.containsKey("S_OFF"))   IK_OFF[0]   = doc["S_OFF"].as<float>();
    if (doc.containsKey("E_OFF"))   IK_OFF[1]   = doc["E_OFF"].as<float>();
    if (doc.containsKey("W_OFF"))   IK_OFF[2]   = doc["W_OFF"].as<float>();
    if (doc.containsKey("S_INV"))   IK_INV[0]   = (bool)(int)doc["S_INV"];
    if (doc.containsKey("E_INV"))   IK_INV[1]   = (bool)(int)doc["E_INV"];
    if (doc.containsKey("W_INV"))   IK_INV[2]   = (bool)(int)doc["W_INV"];
    if (doc.containsKey("elbow_up"))IK_ELBOW_UP = (bool)doc["elbow_up"];
    Serial.print(F("[IK CFG] H=")); Serial.print(IK_H, 1);
    Serial.print(F(" L1="));        Serial.print(IK_L1, 1);
    Serial.print(F(" L2="));        Serial.print(IK_L2, 1);
    Serial.print(F(" L3="));        Serial.print(IK_L3, 1);
    Serial.print(F(" OFF="));
    Serial.print(IK_OFF[0],1); Serial.print(F(","));
    Serial.print(IK_OFF[1],1); Serial.print(F(","));
    Serial.println(IK_OFF[2],1);
  }
  // ── Laser control ─────────────────────────────────────────
  else if (strcmp(cmd, "laser") == 0) {
    int state = doc["state"] | 0;   // 1 = on, 0 = off
    digitalWrite(LASER, state ? HIGH : LOW);
    Serial.print(F("[LASER] "));
    Serial.println(state ? F("ON") : F("OFF"));
  }
  else {
    Serial.print(F("[WARN] unknown cmd: ")); Serial.println(cmd);
  }

  // Blink LED on any valid command
  digitalWrite(LED_PIN, HIGH);
  delay(5);
  digitalWrite(LED_PIN, LOW);
}


// ═══════════════════════════════════════════════════════════════
//  BROADCASTS
// ═══════════════════════════════════════════════════════════════

void broadcastAngles() {
  Serial.print(F("ANGLES:"));
  Serial.print(servoFrontMotor.read()); Serial.print(F(","));
  Serial.print(servoBackMotor.read());  Serial.print(F(","));
  Serial.print(servoarm1.read());       Serial.print(F(","));
  Serial.print(servoarm2.read());       Serial.print(F(","));
  Serial.print(servoarm3.read());       Serial.print(F(","));
  Serial.print(servoarm4.read());       Serial.print(F(","));
  Serial.print(servoarm5.read());       Serial.print(F(","));
  Serial.println(gripper.read());
}


// ═══════════════════════════════════════════════════════════════
//  SETUP
// ═══════════════════════════════════════════════════════════════

void setup() {
  pinMode(LED_PIN, OUTPUT);
  pinMode(LASER, OUTPUT);

  for (int i = 0; i < 5; i++) {
    digitalWrite(LED_PIN, HIGH); delay(200);
    digitalWrite(LED_PIN, LOW);  delay(200);
  }

  delay(1000);

  Serial.begin(115200);
  Serial.println(F("=== BOOT — rescue_arm_ik ==="));
  digitalWrite(LASER, 0);

  // Wire.begin();
  // if (!mpu.setup(0x68)) { Serial.println(F("MPU FAIL")); }
  // else                  { Serial.println(F("MPU OK"));   }

  servoFrontMotor.attach(PIN_FRONT);  servoFrontMotor.write(90);   currentAngles[0] = 90;
  servoBackMotor.attach(PIN_BACK);    servoBackMotor.write(90);   currentAngles[1] = 90;
  servoarm1.attach(PIN_ARM1);         servoarm1.write(90);        currentAngles[2] = 90;
  servoarm2.attach(PIN_ARM2);         servoarm2.write(25);        currentAngles[3] = 25;
  servoarm3.attach(PIN_ARM3);         servoarm3.write(20);          currentAngles[4] = 20;
  servoarm4.attach(PIN_ARM4);         servoarm4.write(85);        currentAngles[5] = 85;
  servoarm5.attach(PIN_ARM5);         servoarm5.write(122);        currentAngles[6] = 122;
  gripper.attach(PIN_MOTOR);          gripper.write(90);    currentAngles[7] = 90;

  Serial.println(F("Servos attached. Home on first loop."));
  Serial.println(F("IK JSON: {\"cmd\":\"ik\",\"y\":30,\"z\":18,\"pitch\":0}"));

  // Print active joint limits at boot for easy verification
  Serial.println(F("--- Joint Limits ---"));
  const char* names[8] = {
    "id1 FrontFlip", "id2 BackFlip ", "id3 Shoulder ",
    "id4 Elbow    ", "id5 Wrist    ", "id6 WristRoll",
    "id7 Tool     ", "id8 Gripper  "
  };
  for (int i = 0; i < 8; i++) {
    Serial.print(names[i]);
    Serial.print(F("  min="));
    Serial.print(SERVO_MIN[i]);
    Serial.print(F("  max="));
    Serial.println(SERVO_MAX[i]);
  }
  Serial.println(F("--------------------"));
}


// ═══════════════════════════════════════════════════════════════
//  LOOP
// ═══════════════════════════════════════════════════════════════

void loop() {
  // runIMU();

  // Home once
  if (!homed) {
    servoFrontMotor.write(90);  currentAngles[0] = 90;  delay(200);
    servoBackMotor.write(90);  currentAngles[1] = 90; delay(200);
    servoarm1.write(90);       currentAngles[2] = 90; delay(200);
    servoarm2.write(25);       currentAngles[3] = 25; delay(200);
    servoarm3.write(20);        currentAngles[4] = 20;  delay(200);
    servoarm4.write(85);        currentAngles[5] = 85;  delay(200);
    servoarm5.write(122);       currentAngles[6] = 122; delay(200);
    gripper.write(90);   currentAngles[7] = 90;
    homed = true;
    Serial.println(F("Homed"));
  }
  // Broadcast angles at 10 Hz
  static uint32_t lastBroadcast = 0;
  if (millis() - lastBroadcast >= ANGLE_BROADCAST_MS) {
    broadcastAngles();
    lastBroadcast = millis();
  }

  // Read incoming JSON
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      inputBuffer.trim();
      if (inputBuffer.length() > 0) {
        handleJson(inputBuffer);
      }
      inputBuffer = "";
    } else {
      inputBuffer += c;
    }
  }
}