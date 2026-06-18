import socket
import struct
import serial
import serial.tools.list_ports
import json
import time
import threading
import sys
import os
import asyncio
import logging
import websockets
import webbrowser
import csv

from http.server import SimpleHTTPRequestHandler, HTTPServer

# Suppress noisy websockets handshake error tracebacks — these are benign
logging.getLogger('websockets').setLevel(logging.CRITICAL)
logging.getLogger('websockets.server').setLevel(logging.CRITICAL)

# ═══════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════
PI_IP         = "192.168.1.101"
AUDIO_WS_URL  = f"ws://{PI_IP}:5010"      # pi_audio_ws.py runs on Pi

ESP32_PORT    = 3390
MEGA_PORT_UDP = 3391
WS_PORT       = 8765
HTTP_PORT     = 8766
ANGLES_LISTEN_PORT = 3392

COM_PORT      = None
BAUD_RATE     = 115200
LINEAR_SPEED  = 0.3
ANGULAR_SPEED = 0.8
BASE_RPM      = 80
SERVO_STEP    = 1

# ── Packet markers ────────────────────────────────────────────
SERVO_MARKER = 0xAA
LOCK_MARKER  = 0xFF

CMD_DDSM_STOP = 10000
CMD_DDSM_CTRL = 10010
ACT_SPEED     = 2
CMD_DELAY     = 0.005

ESP32_KEYWORDS = ['CP210', 'CH340', 'FTDI', 'USB Serial', 'ESP32', 'Silicon Labs']
MOVE_KEYS = {'w', 'a', 's', 'd', 'q', 'e', 'z', 'c'}
MOTOR_LABELS = {
    'w': 'Forward', 's': 'Backward', 'a': 'Left', 'd': 'Right',
    'q': 'Fwd-Left  (diag)', 'e': 'Fwd-Right (diag)',
    'z': 'Bwd-Left  (diag)', 'c': 'Bwd-Right (diag)',
}

NUM_SERVOS = 8
SERVO_KEYS = {'y':0,'u':1,'i':2,'o':3,'h':4,'j':5,'k':6,'l':7}
KEY_SERVO  = {v:k for k,v in SERVO_KEYS.items()}
SERVO_NAMES    = ["Front motor","Back motor","Arm 1","Arm 2","Arm 3","Arm 4","Arm 5","Gripper motor"]
SERVO_DEFAULTS = [85, 96, 165, 180,  20, 85, 122, 90]
SERVO_MINS     = [  0,   0,  40,  110,  20,  0,  60,  0]
SERVO_MAXS     = [180, 180, 165, 180, 180, 180, 180, 180]
MOTOR_STOP    = 100
MOTOR_GRIP    = 160
MOTOR_REVERSE = 85

POSTURE_KEYS = {'F1':"home",'F2':"ramp",'F3':"guard",'F4':"giraff",'F5':"stair",'F6':"finish",'F7':"backramp,"}
POSTURE_ANGLES = {
    "home":   [85,  96, 165, 180,  20,  85, 122, 90],
    "ramp":   [110, 96, 165, 180,  20,  85, 122, 90],
    "giraff": [0,   180, 165, 180,  20,  85, 122, 90],
    "guard":  [180,  25, 165, 180,  20,  85, 122, 90],
    "stair":  [125, 145, 165, 180,  20,  130, 122, 90],
    "finish": [140,  60, 165, 180,  20,  85, 122, 90],
    "backramp":[45, 75, 165, 180,  20,  85, 122, 90],
}

QR_CSV_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qr_detections.csv")


# ══════════════════════════════════════════════════════════════
#  AUDIO PROXY — connects to pi_audio_ws.py on the Pi and
#  forwards audio_chunk messages to all browser WS clients.
# ══════════════════════════════════════════════════════════════
_audio_active = False
_audio_lock   = threading.Lock()

def _stop_audio_stream():
    global _audio_active
    with _audio_lock:
        _audio_active = False
    print("[AUDIO] Stop requested")

async def _audio_proxy_task():
    """Async task: connect to Pi audio WS → forward chunks to browsers."""
    global _audio_active
    print(f"[AUDIO] Connecting to Pi audio stream: {AUDIO_WS_URL}")
    try:
        async with websockets.connect(AUDIO_WS_URL, ping_interval=None) as pi_ws:
            print("[AUDIO] Connected to Pi mic stream")
            async for raw in pi_ws:
                if not _audio_active:
                    break
                # Forward the chunk as-is to all browser clients
                dead = set()
                for browser_ws in list(_ws_clients):
                    try:
                        await browser_ws.send(raw)
                    except Exception:
                        dead.add(browser_ws)
                _ws_clients.difference_update(dead)
    except Exception as e:
        print(f"[AUDIO] Pi audio WS error: {e}")
    finally:
        _audio_active = False
        print("[AUDIO] Proxy stopped")

def _start_audio_stream(loop: asyncio.AbstractEventLoop):
    global _audio_active
    with _audio_lock:
        if _audio_active:
            return
        _audio_active = True
    asyncio.run_coroutine_threadsafe(_audio_proxy_task(), loop)
    print("[AUDIO] Proxy task scheduled")


# ══════════════════════════════════════════════════════════════
#  COMBINED CONTROLLER
# ══════════════════════════════════════════════════════════════
def find_esp32_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or '') + (p.manufacturer or '')
        if any(kw.lower() in desc.lower() for kw in ESP32_KEYWORDS):
            return p.device
    return ports[0].device if len(ports) == 1 else None


class RobotController:
    def __init__(self):
        self._mu       = threading.Lock()
        self._log_mu   = threading.Lock()
        self.running   = True
        self.quit_flag = False
        self.active_keys   = set()
        self.is_locked     = False
        self.linear_speed  = LINEAR_SPEED
        self.angular_speed = ANGULAR_SPEED
        self.base_rpm      = BASE_RPM
        self.serial_ok     = False
        self.angles       = list(SERVO_DEFAULTS)
        self.selected     = 0
        self.servo_step   = 1
        self.motor_state  = "stop"
        self.posture_lock_until = 0.0
        self.heading      = 0.0
        self.log_lines = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        self.ser = None
        port = COM_PORT or find_esp32_port()
        if port:
            try:
                self.ser = serial.Serial(port, BAUD_RATE, timeout=1)
                time.sleep(1.5)
                self.serial_ok = True
                self._log(f"Serial OK → {port}")
                threading.Thread(target=self._serial_rx, daemon=True).start()
            except serial.SerialException as e:
                self._log(f"Serial failed: {e}")
        else:
            self._log("No serial found — UDP only mode")
        threading.Thread(target=self._send_loop, daemon=True).start()
        threading.Thread(target=self._angles_rx_loop, daemon=True).start()
        time.sleep(0.3)
        self._udp_servo({"cmd": "config", "stop": MOTOR_STOP, "grip": MOTOR_GRIP, "reverse": MOTOR_REVERSE})
        self._send_posture("home")

    def _log(self, msg):
        ts = time.strftime("%H:%M:%S")
        with self._log_mu:
            self.log_lines.append(f"{ts}  {msg}")
            if len(self.log_lines) > 30:
                self.log_lines.pop(0)

    def _angles_rx_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', ANGLES_LISTEN_PORT))
        sock.settimeout(1.0)
        self._log(f"Angles RX listening on :{ANGLES_LISTEN_PORT}")
        while self.running:
            try:
                data, _ = sock.recvfrom(256)
                line = data.decode(errors='ignore').strip()
                if line.startswith("ANGLES:"):
                    parts = [int(x) for x in line[7:].split(",")]
                    if len(parts) == 8:
                        with self._mu:
                            if time.monotonic() > self.posture_lock_until:
                                self.angles = parts
                elif line.startswith("DATA:"):
                    parts = line[5:].split(",")
                    if len(parts) == 3:
                        with self._mu:
                            self.heading = float(parts[2])
            except socket.timeout:
                continue
            except Exception as e:
                self._log(f"Angles RX error: {e}")
        sock.close()

    def _udp_motion(self, lin, ang):
        try:
            self.sock.sendto(struct.pack('ff', lin, ang), (PI_IP, ESP32_PORT))
        except Exception:
            pass

    def _udp_lock(self, state):
        try:
            self.sock.sendto(struct.pack('Bb', LOCK_MARKER, state), (PI_IP, ESP32_PORT))
        except Exception:
            pass

    def _serial_json(self, cmd):
        if not self.ser: return
        try:
            self.ser.write((json.dumps(cmd) + '\n').encode())
        except Exception:
            pass

    def _serial_motor(self, mid, rpm):
        self._serial_json({"T": CMD_DDSM_CTRL, "id": mid, "cmd": rpm, "act": ACT_SPEED})
        time.sleep(CMD_DELAY)

    def _serial_drive(self, l, r):
        self._serial_motor(1,  l); self._serial_motor(3,  l)
        self._serial_motor(2, -r); self._serial_motor(4, -r)

    def _serial_stop_all(self):
        for mid in [1, 2, 3, 4]:
            self._serial_json({"T": CMD_DDSM_STOP, "id": mid})
            time.sleep(CMD_DELAY)

    def _serial_rx(self):
        while self.running and self.ser:
            try:
                if self.ser.in_waiting:
                    line = self.ser.readline().decode(errors='ignore').strip()
                    if line:
                        self._log(f"Serial: {line}")
            except Exception:
                break
            time.sleep(0.01)

    def do_lock(self):
        self.is_locked = True
        self._udp_motion(0.0, 0.0)
        self._udp_lock(1)
        self._log("BRAKE ENGAGED")

    def do_unlock(self):
        self.is_locked = False
        self._udp_lock(0)
        self._log("BRAKE RELEASED")

    def _calc_vel(self):
        lin = self.linear_speed
        ang = self.angular_speed
        rpm = self.base_rpm
        k   = self.active_keys
        lv = av = lr = rr = 0
        if   'w' in k: lv= lin; lr= rpm;      rr= rpm
        elif 's' in k: lv=-lin; lr=-rpm;      rr=-rpm
        elif 'a' in k: av= ang; lr=-rpm;      rr= rpm
        elif 'd' in k: av=-ang; lr= rpm;      rr=-rpm
        elif 'q' in k: lv= lin; av= ang; lr=rpm//2; rr= rpm
        elif 'e' in k: lv= lin; av=-ang; lr=rpm;    rr=rpm//2
        elif 'z' in k: lv=-lin; av= ang; lr=-rpm//2;rr=-rpm
        elif 'c' in k: lv=-lin; av=-ang; lr=-rpm;   rr=-rpm//2
        return lv, av, int(lr), int(rr)

    def _send_loop(self):
        while self.running:
            with self._mu:
                locked = self.is_locked
                lv, av, lr, rr = self._calc_vel()
            if locked:
                self._udp_lock(1)
                time.sleep(0.1)
            else:
                self._udp_motion(lv, av)
                if self.ser and (lr or rr):
                    self._serial_drive(lr, rr)
                time.sleep(0.02)

    def _udp_servo(self, payload: dict):
        try:
            data = bytes([SERVO_MARKER]) + json.dumps(payload).encode()
            self.sock.sendto(data, (PI_IP, MEGA_PORT_UDP))
        except Exception as e:
            self._log(f"Servo UDP error: {e}")

    def _send_servo(self, idx, angle):
        self._udp_servo({"cmd": "servo", "id": idx + 1, "angle": angle})

    def _motor_stop_instant(self):
        with self._mu:
            self.motor_state = "stop"
            self.angles[7]   = MOTOR_STOP
        self._udp_servo({"cmd": "servo", "id": 8, "angle": MOTOR_STOP})
        self._log("Motor STOP (90)")

    def _motor_grip(self):
        with self._mu:
            self.motor_state = "grip"
            self.angles[7]   = MOTOR_GRIP
        self._udp_servo({"cmd": "servo", "id": 8, "angle": MOTOR_GRIP})
        self._log(f"Motor GRIP ({MOTOR_GRIP})")

    def _motor_reverse(self):
        with self._mu:
            self.motor_state = "reverse"
            self.angles[7]   = MOTOR_REVERSE
        self._udp_servo({"cmd": "servo", "id": 8, "angle": MOTOR_REVERSE})
        self._log(f"Motor REVERSE ({MOTOR_REVERSE})")

    def _send_posture(self, name):
        angles = POSTURE_ANGLES.get(name)
        if not angles:
            self._log(f"Unknown posture: {name}")
            return
        self._motor_stop_instant()
        with self._mu:
            self.angles = list(angles)
            self.posture_lock_until = time.monotonic() + 1.0
        for idx, angle in enumerate(angles):
            self._udp_servo({"cmd": "servo", "id": idx + 1, "angle": angle})
            time.sleep(0.02)
        self._log(f"Posture → {name}  ({angles})")

    def on_press(self, k):
        if k in ('`', 'esc'):
            self.quit_flag = True
            return
        if k == '>':
            with self._mu:
                self.linear_speed  = min(1.5, round(self.linear_speed  * 1.15, 3))
                self.angular_speed = min(3.0, round(self.angular_speed * 1.15, 3))
                self.base_rpm      = min(200, int(self.base_rpm * 1.15))
            self._log(f"Speed UP  lin={self.linear_speed:.2f} ang={self.angular_speed:.2f} rpm={self.base_rpm}")
            return
        if k == '<':
            with self._mu:
                self.linear_speed  = max(0.05, round(self.linear_speed  * 0.85, 3))
                self.angular_speed = max(0.1,  round(self.angular_speed * 0.85, 3))
                self.base_rpm      = max(10,   int(self.base_rpm * 0.85))
            self._log(f"Speed DN  lin={self.linear_speed:.2f} ang={self.angular_speed:.2f} rpm={self.base_rpm}")
            return
        if k == 'x':
            with self._mu:
                if self.is_locked:
                    self.do_unlock()
                else:
                    self.active_keys.clear()
                    self.do_lock()
            return
        if k in MOVE_KEYS:
            with self._mu:
                if self.is_locked:
                    self.do_unlock()
                self.active_keys.add(k)
            return
        if k in ('1','2','3','4','5'):
            with self._mu:
                self.servo_step = int(k)
            self._log(f"Step → {k}°")
            return
        if k in SERVO_KEYS:
            with self._mu:
                self.selected = SERVO_KEYS[k]
            self._log(f"Selected {SERVO_NAMES[self.selected]}")
            return
        if k == ' ':
            self._motor_stop_instant()
            return
        if k in ('=', '+'):
            with self._mu:
                idx  = self.selected
                step = self.servo_step
            if idx == 7:
                self._motor_grip()
            else:
                with self._mu:
                    new = min(SERVO_MAXS[idx], self.angles[idx] + step)
                    self.angles[idx] = new
                self._send_servo(idx, new)
                self._log(f"{SERVO_NAMES[idx]} → {new}°")
            return
        if k == '-':
            with self._mu:
                idx  = self.selected
                step = self.servo_step
            if idx == 7:
                self._motor_reverse()
            else:
                with self._mu:
                    new = max(SERVO_MINS[idx], self.angles[idx] - step)
                    self.angles[idx] = new
                self._send_servo(idx, new)
                self._log(f"{SERVO_NAMES[idx]} → {new}°")
            return

    def on_press_special(self, key):
        if key in POSTURE_KEYS:
            self._send_posture(POSTURE_KEYS[key])

    def on_release(self, k):
        if k in MOVE_KEYS:
            with self._mu:
                self.active_keys.discard(k)
                if not self.active_keys:
                    self._udp_motion(0.0, 0.0)
                    if self.ser:
                        threading.Thread(target=self._serial_stop_all, daemon=True).start()
                    self.do_lock()
                    self._log("Auto-brake engaged")

    def snapshot(self):
        with self._mu:
            lv, av, lr, rr = self._calc_vel()
            return dict(
                locked=self.is_locked, keys=set(self.active_keys),
                lv=lv, av=av, lr=lr, rr=rr,
                lin_spd=self.linear_speed, ang_spd=self.angular_speed, rpm_spd=self.base_rpm,
                serial=self.serial_ok, angles=list(self.angles),
                selected=self.selected, motor_state=self.motor_state,
                servo_step=self.servo_step, heading=self.heading,
                log=list(self.log_lines),
            )

    def cleanup(self):
        self.running = False
        if self.is_locked:
            self.do_unlock()
        self._udp_motion(0.0, 0.0)
        if self.ser:
            self._serial_stop_all()
            self.ser.close()
        self.sock.close()


# ══════════════════════════════════════════════════════════════
#  AI STREAM SERVER
# ══════════════════════════════════════════════════════════════
AI_STREAM_PORT = 5001
PI_CAM_URL     = f"http://{PI_IP}:5000/stream"

import urllib.request
import numpy as np
import cv2 as _cv2

_ai_frame_lock = threading.Lock()
_ai_latest_frame = None
_ai_model = None
_ai_running = False

_qr_model        = None
_qr_frame_lock   = threading.Lock()
_qr_latest_frame = None
_last_qr_texts   = []
_last_hazmat_labels: set = set()   # deduplicate hazmat detections across frames

QR_COLOUR  = (0, 255, 0)
QR_PENDING = (0, 200, 255)
AI_INFER_SIZE = 416

HAZARD_COLOUR = (0, 0, 220)
LABEL_BG      = (0, 0, 180)
CONF_THRESH   = 0.35

# ── IOU / NMS settings ───────────────────────────────────────
NMS_IOU_THRESH = 0.45   # boxes with IOU above this are suppressed
# ─────────────────────────────────────────────────────────────


def _compute_iou(box_a, box_b):
    """
    Compute Intersection over Union (IOU) between two boxes.
    Each box is (x1, y1, x2, y2).
    Returns a float in [0, 1].
    """
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union_area = area_a + area_b - inter_area

    return inter_area / union_area if union_area > 0 else 0.0


def _nms(detections, iou_thresh=NMS_IOU_THRESH):
    """
    Apply class-aware Non-Maximum Suppression using IOU.

    detections: list of dicts with keys:
        'box'   → (x1, y1, x2, y2)
        'conf'  → float confidence
        'cls'   → int class id
        'label' → str label

    Returns a filtered list of detections, keeping only the
    highest-confidence box when two boxes of the same class
    overlap with IOU > iou_thresh.
    """
    if not detections:
        return []

    # Sort by confidence descending so we keep the best box first
    detections = sorted(detections, key=lambda d: d['conf'], reverse=True)
    kept = []

    while detections:
        best = detections.pop(0)
        kept.append(best)
        remaining = []
        for det in detections:
            # Only suppress within the same class
            if det['cls'] == best['cls']:
                iou = _compute_iou(best['box'], det['box'])
                if iou > iou_thresh:
                    continue        # suppressed — too much overlap
            remaining.append(det)
        detections = remaining

    return kept


def _load_ai_model():
    global _ai_model, _qr_model
    try:
        import torch
        _ai_device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"[AI] Using device: {_ai_device}")
    except ImportError:
        _ai_device = 'cpu'
    try:
        from ultralytics import YOLO
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "X7.pt")
        _ai_model = YOLO(model_path)
        _ai_model.to(_ai_device)
        print(f"[AI] YOLO11 model loaded: {model_path}")
    except Exception as e:
        print(f"[AI] Failed to load X7.pt: {e}")
        _ai_model = None
    try:
        from ultralytics import YOLO
        qr_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "QR1.pt")
        _qr_model = YOLO(qr_path)
        _qr_model.to(_ai_device)
        print(f"[QR] QR model loaded: {qr_path}")
    except Exception as e:
        print(f"[QR] Failed to load QR1.pt: {e}")
        _qr_model = None
    # Ensure the shared CSV log exists with the correct header
    if not os.path.exists(QR_CSV_LOG):
        try:
            with open(QR_CSV_LOG, 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(["timestamp", "type", "data"])
            print(f"[CSV] Created log: {QR_CSV_LOG}")
        except Exception as e:
            print(f"[CSV] Could not create log: {e}")


def _draw_boxes(frame, results):
    """
    Extract detections from YOLO results, apply IOU-based NMS,
    then draw the surviving boxes on the frame.
    """
    # ── Collect all detections above confidence threshold ────
    detections = []
    for r in results:
        for box in r.boxes:
            conf = float(box.conf[0])
            if conf < CONF_THRESH:
                continue
            cls_id = int(box.cls[0])
            label  = _ai_model.names.get(cls_id, str(cls_id)) if _ai_model else str(cls_id)
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            detections.append({
                'box':   (x1, y1, x2, y2),
                'conf':  conf,
                'cls':   cls_id,
                'label': label,
            })

    # ── Apply NMS using IOU ──────────────────────────────────
    detections = _nms(detections, iou_thresh=NMS_IOU_THRESH)

    # ── Log new hazmat labels to the shared CSV ──────────────
    global _last_hazmat_labels
    for det in detections:
        label = det['label']
        if label not in _last_hazmat_labels:
            _last_hazmat_labels.add(label)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[AI] New hazmat detected: {label}")
            try:
                with open(QR_CSV_LOG, 'a', newline='', encoding='utf-8') as f:
                    csv.writer(f).writerow([ts, "HAZMAT", label])
                print(f"[AI] Saved to CSV: {ts}  HAZMAT  {label}")
            except Exception as e:
                print(f"[AI] CSV write error: {e}")

    # ── Draw surviving detections ────────────────────────────
    for det in detections:
        x1, y1, x2, y2 = det['box']
        label = det['label']
        conf  = det['conf']
        _cv2.rectangle(frame, (x1, y1), (x2, y2), HAZARD_COLOUR, 2)
        tag = f"{label} {conf:.0%}"
        (tw, th), _ = _cv2.getTextSize(tag, _cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        _cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 6, y1), LABEL_BG, -1)
        _cv2.putText(frame, tag, (x1 + 3, y1 - 4),
                     _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, _cv2.LINE_AA)
    return frame


_ai_raw_lock  = threading.Lock()
_ai_raw_frame = None

def _ai_grab_loop():
    global _ai_raw_frame, _ai_running
    _ai_running = True
    print(f"[AI] Grab loop connecting to: {PI_CAM_URL}")
    while _ai_running:
        try:
            req = urllib.request.Request(PI_CAM_URL)
            stream = urllib.request.urlopen(req, timeout=10)
            buf = b""
            while _ai_running:
                chunk = stream.read(8192)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    if start == -1:
                        buf = b""; break
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end == -1:
                        buf = buf[start:]; break
                    jpg = buf[start:end + 2]
                    buf = buf[end + 2:]
                    if len(jpg) < 100:
                        continue
                    arr = np.frombuffer(jpg, dtype=np.uint8)
                    frame = _cv2.imdecode(arr, _cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    with _ai_raw_lock:
                        _ai_raw_frame = frame
                if len(buf) > 500_000:
                    buf = b""
        except Exception as e:
            print(f"[AI] Grab error: {e} — retrying in 2s")
            time.sleep(2)

def _ai_inference_loop():
    global _ai_latest_frame
    print("[AI] Inference loop started")
    while _ai_running:
        with _ai_raw_lock:
            frame = _ai_raw_frame
        if frame is None:
            time.sleep(0.01); continue
        frame = frame.copy()
        if _ai_model is not None:
            results = _ai_model(frame, verbose=False, device=_ai_model.device)
            frame   = _draw_boxes(frame, results)
        _, jpeg = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 80])
        with _ai_frame_lock:
            _ai_latest_frame = jpeg.tobytes()

def _qr_inference_loop():
    global _qr_latest_frame, _last_qr_texts
    detector = _cv2.QRCodeDetector()
    print("[QR] OpenCV built-in QRCodeDetector ready")
    _last_processed = None

    # Write CSV header if file doesn't exist yet
    if not os.path.exists(QR_CSV_LOG):
        with open(QR_CSV_LOG, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(["timestamp", "type", "data"])

    while _ai_running:
        with _ai_raw_lock:
            frame_ptr = _ai_raw_frame
        if frame_ptr is None or frame_ptr is _last_processed:
            time.sleep(0.005); continue
        _last_processed = frame_ptr
        frame = frame_ptr.copy()
        retval, decoded_list, points_list, _ = detector.detectAndDecodeMulti(frame)
        if retval and decoded_list:
            for i, data in enumerate(decoded_list):
                if not data: continue
                if points_list is not None and i < len(points_list):
                    pts = points_list[i].astype(int)
                    for j in range(4):
                        _cv2.line(frame, tuple(pts[j]), tuple(pts[(j + 1) % 4]), QR_COLOUR, 2)
                    rx, ry = pts[0]
                    lbl_w = min(len(data) * 9 + 10, frame.shape[1] - int(rx))
                    _cv2.rectangle(frame, (int(rx), max(0, int(ry) - 35)), (int(rx) + lbl_w, int(ry)), QR_COLOUR, -1)
                    _cv2.putText(frame, data, (int(rx) + 5, max(10, int(ry) - 10)), _cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 2, _cv2.LINE_AA)
                if data not in _last_qr_texts:
                    _last_qr_texts = (_last_qr_texts + [data])[-5:]
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[QR] Decoded: {data}")
                    # ── Save to CSV ──────────────────────────────
                    try:
                        with open(QR_CSV_LOG, 'a', newline='', encoding='utf-8') as f:
                            csv.writer(f).writerow([ts, "QR", data])
                        print(f"[QR] Saved to CSV: {ts}  {data}")
                    except Exception as e:
                        print(f"[QR] CSV write error: {e}")
                    # ─────────────────────────────────────────────
        _, jpeg = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 65])
        with _qr_frame_lock:
            _qr_latest_frame = jpeg.tobytes()

def _qr_mjpeg_generate():
    while True:
        with _qr_frame_lock:
            frame = _qr_latest_frame
        if frame:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(0.04)

# ── Motion / HSV lock-target config ──────────────────────────
MOT_BLACK_V_MAX      = 70       # HSV V upper bound for "black"
MOT_BLACK_S_MAX      = 90       # HSV S upper bound for "black"
MOT_MIN_BLOB_AREA    = 120      # px² — ignore smaller blobs
MOT_MAX_BLOB_AREA    = 12000    # px² — ignore larger blobs
MOT_LOCK_MAX_DIST    = 35.0     # max centroid jump (px) to keep lock
MOT_LOCK_LOST_LIMIT  = 8        # frames without a match before lock drops
MOT_CENTER_X_TOL     = 160      # horizontal band (px) preferred for locking
MOT_AREA_WEIGHT      = 0.002    # area bonus in initial-lock scoring
MOT_CENTER_WEIGHT    = 0.25     # pull toward frame centre when re-matching
MOT_COLOUR           = (0, 220, 80)
MOT_LABEL_BG         = (0, 160, 60)
MOT_VIEW_W           = 640
MOT_VIEW_H           = 480
# ─────────────────────────────────────────────────────────────

_box_frame_lock   = threading.Lock()
_box_latest_frame = None

# Shared motion state (readable by other parts of the system)
_mot_found        = False
_mot_center       = None        # (cx, cy) int tuple or None
_mot_box          = None        # (x, y, w, h) or None
_mot_locked       = None        # current locked-target dict or None
_mot_lost_count   = 0
_mot_state_lock   = threading.Lock()


def _mot_distance(p1, p2):
    return float(np.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2))


def _mot_choose_target(candidates):
    """
    Replicate the Flask app's choose_locked_target() logic:
    - First detection → pick the blob closest to frame centre (with area bonus).
    - Subsequent frames → snap to nearest blob within MOT_LOCK_MAX_DIST,
      with a gentle pull back toward the frame centre.
    - If no match for MOT_LOCK_LOST_LIMIT frames → release lock.
    """
    global _mot_locked, _mot_lost_count 

    frame_center = (MOT_VIEW_W / 2.0, MOT_VIEW_H / 2.0)

    if not candidates:
        _mot_lost_count += 1
        if _mot_lost_count > MOT_LOCK_LOST_LIMIT:
            _mot_locked = None
        return None

    if _mot_locked is None:
        # Initial lock — choose best candidate
        _mot_lost_count = 0
        best, best_score = None, 1e9
        for c in candidates:
            score = (_mot_distance(c['center'], frame_center)
                     - c['area'] * MOT_AREA_WEIGHT)
            if score < best_score:
                best_score = score
                best = c
        _mot_locked = best
        return _mot_locked

    # Re-lock — find nearest candidate to previous centroid
    old_center = _mot_locked['center']
    nearest, best_score = None, 1e9
    for c in candidates:
        score = (_mot_distance(old_center, c['center'])
                 + MOT_CENTER_WEIGHT * _mot_distance(c['center'], frame_center))
        if score < best_score:
            best_score = score
            nearest = c

    if nearest and _mot_distance(old_center, nearest['center']) <= MOT_LOCK_MAX_DIST:
        _mot_locked = nearest
        _mot_lost_count = 0
        return _mot_locked

    _mot_lost_count += 1
    if _mot_lost_count > MOT_LOCK_LOST_LIMIT:
        _mot_locked = None
    return _mot_locked


def _box_inference_loop():
    """
    HSV-based black-object motion detector with persistent target lock,
    ported from the Flask app's detect_black_motion_and_lock().
    Publishes its annotated MJPEG to _box_latest_frame exactly as before
    so the /box_stream Flask route continues to work unchanged.
    """
    global _box_latest_frame, _mot_found, _mot_center, _mot_box

    print("[MOT] HSV motion detector loop started")
    while _ai_running:
        with _ai_raw_lock:
            frame = _ai_raw_frame
        if frame is None:
            time.sleep(0.02)
            continue

        # ── Resize to working resolution ─────────────────────
        img = _cv2.resize(frame, (MOT_VIEW_W, MOT_VIEW_H))
        display = img.copy()

        # ── HSV black-colour mask ─────────────────────────────
        hsv  = _cv2.cvtColor(img, _cv2.COLOR_BGR2HSV)
        mask = _cv2.inRange(
            hsv,
            np.array([0,   0,              0],            dtype=np.uint8),
            np.array([180, MOT_BLACK_S_MAX, MOT_BLACK_V_MAX], dtype=np.uint8),
        )
        mask = _cv2.GaussianBlur(mask, (5, 5), 0)
        _, mask = _cv2.threshold(mask, 40, 255, _cv2.THRESH_BINARY)
        mask = _cv2.erode(mask,  None, iterations=1)
        mask = _cv2.dilate(mask, None, iterations=2)

        # ── Find contour candidates ───────────────────────────
        contours, _ = _cv2.findContours(mask, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
        frame_cx     = MOT_VIEW_W / 2.0
        candidates   = []

        for cnt in contours:
            area = _cv2.contourArea(cnt)
            if area < MOT_MIN_BLOB_AREA or area > MOT_MAX_BLOB_AREA:
                continue
            x, y, w, h = _cv2.boundingRect(cnt)
            cx, cy = x + w / 2.0, y + h / 2.0
            # Prefer blobs near horizontal centre
            if abs(cx - frame_cx) <= MOT_CENTER_X_TOL:
                candidates.append({'area': area, 'rect': (x, y, w, h), 'center': (cx, cy)})

        # Fall back to all blobs if nothing is central
        if not candidates:
            for cnt in contours:
                area = _cv2.contourArea(cnt)
                if area < MOT_MIN_BLOB_AREA or area > MOT_MAX_BLOB_AREA:
                    continue
                x, y, w, h = _cv2.boundingRect(cnt)
                candidates.append({'area': area, 'rect': (x, y, w, h),
                                   'center': (x + w / 2.0, y + h / 2.0)})

        # ── Target lock logic ─────────────────────────────────
        target = _mot_choose_target(candidates)

        with _mot_state_lock:
            if target is None:
                _mot_found  = False
                _mot_center = None
                _mot_box    = None
            else:
                x, y, w, h  = target['rect']
                cx, cy       = target['center']
                _mot_found   = True
                _mot_center  = (int(cx), int(cy))
                _mot_box     = (x, y, w, h)

                # Draw locked bounding box + centroid
                _cv2.rectangle(display, (x, y), (x + w, y + h), MOT_COLOUR, 2)
                _cv2.circle(display, (int(cx), int(cy)), 5, MOT_COLOUR, -1)
                tag = f"LOCKED  {int(cx)},{int(cy)}  {int(target['area'])}px²"
                (tw, th), _ = _cv2.getTextSize(tag, _cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                lbl_y = max(th + 6, y - 6)
                _cv2.rectangle(display, (x, lbl_y - th - 6), (x + tw + 6, lbl_y), MOT_LABEL_BG, -1)
                _cv2.putText(display, tag, (x + 3, lbl_y - 3),
                             _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, _cv2.LINE_AA)

        # ── Status badge ──────────────────────────────────────
        badge     = "LOCKED" if target else "NO TARGET"
        badge_col = MOT_COLOUR if target else (120, 120, 120)
        _cv2.putText(display, f"MOTION: {badge}", (8, 22),
                     _cv2.FONT_HERSHEY_SIMPLEX, 0.55, badge_col, 1, _cv2.LINE_AA)

        _, jpeg = _cv2.imencode(".jpg", display, [_cv2.IMWRITE_JPEG_QUALITY, 80])
        with _box_frame_lock:
            _box_latest_frame = jpeg.tobytes()

def _box_mjpeg_generate():
    while True:
        with _box_frame_lock:
            frame = _box_latest_frame
        if frame:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(0.04)

def _ai_mjpeg_generate():
    while True:
        with _ai_frame_lock:
            frame = _ai_latest_frame
        if frame:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(0.04)

def _start_ai_flask():
    from flask import Flask as _Flask, Response as _Response
    ai_app = _Flask("ai_stream")

    @ai_app.route("/ai_stream")
    def ai_stream():
        resp = _Response(_ai_mjpeg_generate(), mimetype="multipart/x-mixed-replace; boundary=frame")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @ai_app.route("/qr_stream")
    def qr_stream():
        resp = _Response(_qr_mjpeg_generate(), mimetype="multipart/x-mixed-replace; boundary=frame")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @ai_app.route("/box_stream")
    def box_stream():
        resp = _Response(_box_mjpeg_generate(), mimetype="multipart/x-mixed-replace; boundary=frame")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @ai_app.route("/qr_detections")
    def qr_detections():
        import json as _json
        payload = _json.dumps({"texts": list(_last_qr_texts)})
        resp = _Response(payload, mimetype="application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @ai_app.route("/")
    def ai_root():
        return _Response(_ai_mjpeg_generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    print(f"[AI] Annotated stream → http://localhost:{AI_STREAM_PORT}/ai_stream")
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.ERROR)
    ai_app.run(host="0.0.0.0", port=AI_STREAM_PORT, threaded=True)


# ══════════════════════════════════════════════════════════════
#  WEBSOCKET SERVER
# ══════════════════════════════════════════════════════════════
_ws_clients: set = set()
_ws_ctrl = None


async def _ws_handler(websocket):
    global _ws_clients
    _ws_clients.add(websocket)
    try:
        async for raw in websocket:
            if raw == '__ping__':
                await websocket.send('__pong__')
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ctrl = _ws_ctrl
            if ctrl is None:
                continue
            t = msg.get('type', '')

            if t == 'move_start':
                ctrl.on_press(msg.get('key', ''))
            elif t == 'move_stop':
                ctrl.on_release(msg.get('key', ''))
            elif t == 'lock':
                with ctrl._mu:
                    ctrl.active_keys.clear()
                    ctrl.do_lock()
            elif t == 'unlock':
                ctrl.do_unlock()
            elif t == 'servo_set':
                idx   = int(msg.get('index', 0))
                angle = int(msg.get('angle', 90))
                if 0 <= idx < NUM_SERVOS:
                    angle = max(SERVO_MINS[idx], min(SERVO_MAXS[idx], angle))
                    with ctrl._mu:
                        ctrl.angles[idx] = angle
                    ctrl._send_servo(idx, angle)
                    ctrl._log(f"{SERVO_NAMES[idx]} → {angle}° [GUI]")
            elif t == 'ik':
                y     = float(msg.get('y', 0))
                z     = float(msg.get('z', 0))
                pitch = float(msg.get('pitch', 0))
                ctrl._udp_servo({"cmd": "ik", "y": y, "z": z, "pitch": pitch})
                ctrl._log(f"IK → y:{y:.1f}cm z:{z:.1f}cm pitch:{pitch:.1f}° [GUI→Mega]")
            elif t == 'servo_set_multi':
                joints = msg.get('joints', {})
                for idx_str, angle in joints.items():
                    idx   = int(idx_str)
                    angle = int(angle)
                    if 0 <= idx < NUM_SERVOS:
                        angle = max(SERVO_MINS[idx], min(SERVO_MAXS[idx], angle))
                        with ctrl._mu:
                            ctrl.angles[idx] = angle
                        ctrl._send_servo(idx, angle)
                ctrl._log(f"IK → {' '.join(f'{SERVO_NAMES[int(k)]}:{int(v)}°' for k,v in joints.items())} [GUI]")
            elif t == 'select_servo':
                idx = int(msg.get('index', 0))
                if 0 <= idx < NUM_SERVOS:
                    with ctrl._mu:
                        ctrl.selected = idx
            elif t == 'set_step':
                step = int(msg.get('step', 1))
                if 1 <= step <= 5:
                    with ctrl._mu:
                        ctrl.servo_step = step
            elif t == 'motor_stop':
                ctrl._motor_stop_instant()
            elif t == 'motor_grip':
                ctrl._motor_grip()
            elif t == 'motor_reverse':
                ctrl._motor_reverse()
            elif t == 'posture':
                name = msg.get('name', '')
                if name in POSTURE_ANGLES:
                    ctrl._send_posture(name)
            elif t == 'set_speed':
                with ctrl._mu:
                    if 'lin' in msg: ctrl.linear_speed  = max(0.01, min(5,   float(msg['lin'])))
                    if 'ang' in msg: ctrl.angular_speed = max(0.01, min(5,   float(msg['ang'])))
                    if 'rpm' in msg: ctrl.base_rpm      = max(1,   min(200,  int(msg['rpm'])))
            elif t == 'snapshot':
                ctrl._log("Snapshot requested [GUI]")
            elif t == 'laser':
                state = int(msg.get('state', 0))
                ctrl._udp_servo({"cmd": "laser", "state": state})
                ctrl._log(f"Laser {'ON' if state else 'OFF'} [GUI]")

            # ── AUDIO ────────────────────────────────────────
            elif t == 'audio_start':
                loop = asyncio.get_event_loop()
                _start_audio_stream(loop)
                ctrl._log(f"Audio proxy → {AUDIO_WS_URL}")
                await websocket.send(json.dumps({"type": "audio_state", "active": True}))

            elif t == 'audio_stop':
                _stop_audio_stream()
                ctrl._log("Audio stream stopped")
                await websocket.send(json.dumps({"type": "audio_state", "active": False}))
            # ─────────────────────────────────────────────────

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(websocket)


async def _ws_broadcast_loop():
    global _ws_clients
    while True:
        await asyncio.sleep(0.1)
        ctrl = _ws_ctrl
        if ctrl is None or not _ws_clients:
            continue
        s = ctrl.snapshot()
        payload = json.dumps({
            "type":        "state",
            "angles":      s['angles'],
            "selected":    s['selected'],
            "motor_state": s['motor_state'],
            "serial":      s['serial'],
            "lr":          s['lr'],
            "rr":          s['rr'],
            "heading":     s['heading'],
            "log":         s['log'][-5:],
        })
        dead = set()
        for ws in list(_ws_clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        _ws_clients -= dead


async def _ws_serve():
    async with websockets.serve(
        _ws_handler, "0.0.0.0", WS_PORT,
        logger=None,
        process_request=None,
    ) as server:
        server.logger.setLevel(logging.CRITICAL)
        await _ws_broadcast_loop()


def _http_server(base):
    os.chdir(base)
    handler = SimpleHTTPRequestHandler
    handler.log_message = lambda *a: None
    server = HTTPServer(('0.0.0.0', HTTP_PORT), handler)
    server.serve_forever()


def main():
    global _ws_ctrl
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    threading.Thread(target=_http_server, args=(base,), daemon=True).start()
    _load_ai_model()
    threading.Thread(target=_ai_grab_loop,       daemon=True).start()
    threading.Thread(target=_ai_inference_loop,  daemon=True).start()
    threading.Thread(target=_qr_inference_loop,  daemon=True).start()
    threading.Thread(target=_box_inference_loop, daemon=True).start()
    threading.Thread(target=_start_ai_flask,     daemon=True).start()

    ctrl = RobotController()
    _ws_ctrl = ctrl

    def open_browser():
        time.sleep(1.0)
        webbrowser.open(f'http://localhost:{HTTP_PORT}/control.html')
    threading.Thread(target=open_browser, daemon=True).start()

    print(f"RescueBot running — http://localhost:{HTTP_PORT}/control.html")
    print(f"Listening for Mega angles on UDP :{ANGLES_LISTEN_PORT}")
    print("Close this window to stop.")

    try:
        asyncio.run(_ws_serve())
    except KeyboardInterrupt:
        pass
    finally:
        ctrl.cleanup()
    print("\nRobot control stopped.")


if __name__ == '__main__':
    main()