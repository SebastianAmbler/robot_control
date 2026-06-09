// robot_drive.ino
// DDSM115 track controller
// Communication: JSON over USB Serial
//
// JSON commands received:
//   {"T":1,"l":<-1000..1000>,"r":<-1000..1000>}  -- set left/right track speed
//   {"T":2,"lock":1}                               -- brake all 4 motors
//   {"T":2,"lock":0}                               -- release brake
//
// JSON sent back:
//   {"T":3,"id":<1-4>,"mode":<n>,"tor":<n>,"spd":<n>,"temp":<n>,"u8":<n>,"err":<n>}  -- all 4 motors every 500ms
//   {"T":21,"info":"..."}                          -- info / ack messages

#include <ArduinoJson.h>
#include <ddsm_ctrl.h>

DDSM_CTRL dc;

// ── Pin definitions ───────────────────────────────────────────────────────────
#define DDSM_RX       18
#define DDSM_TX       19
#define DDSM_BAUDRATE 115200

const size_t PACKET_LEN = 10;

// ── State ─────────────────────────────────────────────────────────────────────
int  motorSpeedLeft  = 0;
int  motorSpeedRight = 0;
bool isLocked        = false;

unsigned long lastInfoMs = 0;
const unsigned long INFO_INTERVAL_MS = 500;

// poll one motor per loop, rotating through 1-4
uint8_t pollIdx = 0;
unsigned long lastPollMs = 0;
const unsigned long POLL_INTERVAL_MS = 125; // 4 motors × 125ms = one full cycle per 500ms

// ── JSON buffers ──────────────────────────────────────────────────────────────
StaticJsonDocument<128> rxDoc;
StaticJsonDocument<128> txDoc;
String rxBuffer;

// ─────────────────────────────────────────────────────────────────────────────
// CRC-8/MAXIM
// ─────────────────────────────────────────────────────────────────────────────
uint8_t crc8_maxim(uint8_t *data, uint8_t len) {
  uint8_t crc = 0x00;
  for (uint8_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; b++) crc = (crc & 0x01) ? (crc >> 1) ^ 0x8C : crc >> 1;
  }
  return crc;
}

// ─────────────────────────────────────────────────────────────────────────────
// Info send
// ─────────────────────────────────────────────────────────────────────────────
void sendInfo(const char* msg) {
  txDoc.clear();
  txDoc["T"]    = 21;
  txDoc["info"] = msg;
  serializeJson(txDoc, Serial);
  Serial.println();
}

// ─────────────────────────────────────────────────────────────────────────────
// Send get_info request to one motor and immediately read its reply
// Returns true if a valid packet was received and published
// ─────────────────────────────────────────────────────────────────────────────
bool pollAndPublishMotor(uint8_t id) {
  // build 0x74 info request packet
  uint8_t pkt[PACKET_LEN] = {id, 0x74, 0,0,0,0,0,0,0,0};
  pkt[9] = crc8_maxim(pkt, 9);

  // flush any stale bytes before sending
  while (Serial1.available()) Serial1.read();

  Serial1.write(pkt, PACKET_LEN);

  // wait for reply (10 bytes) with a short timeout
  unsigned long t = millis();
  while (Serial1.available() < (int)PACKET_LEN) {
    if (millis() - t > 20) return false;  // timeout
  }

  uint8_t data[PACKET_LEN];
  Serial1.readBytes(data, PACKET_LEN);

  // CRC check
  if (crc8_maxim(data, 9) != data[9]) return false;

  int16_t torque = (int16_t)((data[2] << 8) | data[3]);
  int16_t speed  = (int16_t)((data[4] << 8) | data[5]);
  uint8_t temp   = data[6];
  uint8_t u8     = data[7];
  uint8_t err    = data[8];

  txDoc.clear();
  txDoc["T"]    = 3;
  txDoc["id"]   = id;
  txDoc["mode"] = data[1];
  txDoc["tor"]  = torque;
  txDoc["spd"]  = speed;
  txDoc["temp"] = temp;
  txDoc["u8"]   = u8;
  txDoc["err"]  = err;
  serializeJson(txDoc, Serial);
  Serial.println();

  return true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Poll all 4 motors back to back then go quiet for INFO_INTERVAL_MS
// ─────────────────────────────────────────────────────────────────────────────
void pollAllMotors() {
  for (uint8_t id = 1; id <= 4; id++) {
    pollAndPublishMotor(id);
    delay(4);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Motor init
// ─────────────────────────────────────────────────────────────────────────────
void initMotors() {
  for (int i = 0; i < 3; i++) {
    dc.ddsm_change_mode(1, 2); delay(4);
    dc.ddsm_change_mode(2, 2); delay(4);
    dc.ddsm_change_mode(3, 2); delay(4);
    dc.ddsm_change_mode(4, 2); delay(4);
  }
  sendInfo("motors ready");
}

// ─────────────────────────────────────────────────────────────────────────────
// Brake / release
// ─────────────────────────────────────────────────────────────────────────────
void send_brake(uint8_t motor_id) {
  uint8_t pkt[PACKET_LEN] = {motor_id, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00};
  pkt[9] = crc8_maxim(pkt, 9);
  Serial1.write(pkt, PACKET_LEN);
  delay(4);
}

void lockMotors() {
  isLocked = true;
  motorSpeedLeft = motorSpeedRight = 0;
  for (int i = 0; i < 3; i++) {
    send_brake(1); delay(4);
    send_brake(2); delay(4);
    send_brake(3); delay(4);
    send_brake(4); delay(4);
  }
  sendInfo("locked");
}

void releaseMotors() {
  isLocked = false;
  for (int i = 0; i < 3; i++) {
    dc.ddsm_ctrl(1, 0, 2); delay(4);
    dc.ddsm_ctrl(2, 0, 2); delay(4);
    dc.ddsm_ctrl(3, 0, 2); delay(4);
    dc.ddsm_ctrl(4, 0, 2); delay(4);
  }
  sendInfo("released");
}

// ─────────────────────────────────────────────────────────────────────────────
// Motor drive
// ─────────────────────────────────────────────────────────────────────────────
void setMotorSpeed(int speedLeft, int speedRight) {
  if (isLocked) return;
  for (int i = 0; i < 3; i++) {
    dc.ddsm_ctrl(1,  speedLeft,  2); delay(4);
    dc.ddsm_ctrl(3,  speedLeft,  2); delay(4);
    dc.ddsm_ctrl(2, -speedRight, 2); delay(4);
    dc.ddsm_ctrl(4, -speedRight, 2); delay(4);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// JSON command handler
// ─────────────────────────────────────────────────────────────────────────────
void handleJsonCmd(const String& json) {
  DeserializationError err = deserializeJson(rxDoc, json);
  if (err) return;

  int T = rxDoc["T"] | -1;
  switch (T) {

    case 1: {
        int l = rxDoc["l"] | 0;
        int r = rxDoc["r"] | 0;
        motorSpeedLeft  = constrain(l, -1000, 1000);
        motorSpeedRight = constrain(r, -1000, 1000);
        setMotorSpeed(motorSpeedLeft, motorSpeedRight);
        break;
      }

    case 2: {
      int lock = rxDoc["lock"] | 0;
      if      (lock == 1 && !isLocked) lockMotors();
      else if (lock == 0 &&  isLocked) releaseMotors();
      break;
    }

    default:
      sendInfo("unknown cmd");
      break;
  }
}

void serialCtrl() {
    // Drain the buffer keeping only the last complete command
    // This prevents stale motion commands queuing ahead of a stop
    static String latest = "";
    
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n') {
            if (rxBuffer.length() > 0) {
                latest = rxBuffer;
                rxBuffer = "";
            }
        } else {
            rxBuffer += c;
        }
    }
    
    // Only execute the most recent complete command
    if (latest.length() > 0) {
        handleJsonCmd(latest);
        latest = "";
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// setup / loop
// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  Serial1.begin(DDSM_BAUDRATE, SERIAL_8N1, DDSM_RX, DDSM_TX);
  dc.pSerial = &Serial1;
  dc.set_ddsm_type(115);
  dc.clear_ddsm_buffer();
  initMotors();
}

void loop() {
  serialCtrl();  // always runs first, never blocked

  unsigned long now = millis();
  if (now - lastPollMs >= POLL_INTERVAL_MS) {
      lastPollMs = now;
      pollIdx = (pollIdx % 4) + 1;  // cycles 1,2,3,4,1,2,3,...
      pollAndPublishMotor(pollIdx);  // only one motor, only one 20ms window
  }
}