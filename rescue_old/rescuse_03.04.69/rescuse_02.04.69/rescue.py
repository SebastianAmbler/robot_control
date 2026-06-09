import socket
import json
import time
import threading
import sys
import os
import asyncio
import websockets
import webbrowser
import urllib
from http.server import SimpleHTTPRequestHandler, HTTPServer

# ═══════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════
PI_IP       = "192.168.0.138"
PI_TCP_PORT = 9000
WS_PORT     = 8765
HTTP_PORT   = 8766

LINEAR_SPEED  = 0.3
ANGULAR_SPEED = 0.8
BASE_RPM      = 80

STATE_PUSH_HZ    = 20
WS_SEND_TIMEOUT  = 0.08
EVENT_QUEUE_MAX  = 200

# Camera / QR
RTSP_URL = "rtsp://admin:admin@192.168.0.106:554/live"
QR_ENABLE = True
QR_JPEG_QUALITY = 70
QR_DETECT_WIDTH = 960
QR_SEND_FPS = 8

# Flask QR server (python qr_server.py รันแยก)
FLASK_BASE = "http://127.0.0.1:5000"   # ← แก้ IP ถ้า Flask อยู่เครื่องอื่น

# ═══════════════════════════════════════════════════════════════
#  Servo config
# ═══════════════════════════════════════════════════════════════
NUM_SERVOS = 8
SERVO_NAMES = [
    "J1 Shoulder","J2 Elbow","J3 Extend",
    "J4 Wrist","J5 Tool","J6 Gripper",
    "Flip-F","Flip-R",
]
SERVO_DEFAULTS = [50, 130,  0, 90, 90, 70, 100, 100]
SERVO_MINS     = [50,  10,  0,  0,  0, 10,  45,  45]
SERVO_MAXS     = [150,130,180,125,180, 90, 160, 160]

GRIP_OPEN  = 70
GRIP_CLOSE = 10
GRIP_MID   = (GRIP_OPEN + GRIP_CLOSE) // 2

HOME_Y, HOME_Z, HOME_P = 30.0, 18.0, 30.0

POSTURE_ANGLES = {
    "home":   [50, 130,  0,  90, 90, 70, 100, 100],
    "guard":  [50, 130,  0,  90, 90, 70,  45, 150],
    "giraff": [50, 130,  0,  90, 90, 70,  57,  80],
    "stair":  [50,130, 90, 110, 90, 70, 160, 45],
}

# Custom postures F4-F9 — กำหนดจาก Browser Settings UI
# format: [J1, J2, J3, J4, J5, J6, Flip-F, Flip-R]
# giraff/stair มีค่า default แต่ override ได้จาก UI
CUSTOM_POSTURES = {
    "giraff":   [50, 130,  0,  120, 90, 70,  57,  60],   # F4 default
    "stair":    [50,130, 90, 120, 90, 70, 135, 105],   # F5 default
    "custom_1": [50, 130,  0,  90, 90, 70, 140, 45],   # F6 default = ยีราฟ
    "custom_2": [106, 103, 0, 69, 90, 70, 140, 45],   # F7 QR SCAN + Flipper down
    "custom_3": [60, 130,  0,  90, 90, 70, 100, 100],   # F8 default = HOME
    "custom_4": [60, 130,  0,  90, 90, 70, 126, 113],   # F9 default = HOME
}

MOVE_KEYS = {'w','a','s','d','q','e','z','c'}


# ═══════════════════════════════════════════════════════════════
#  TCP → Pi
# ═══════════════════════════════════════════════════════════════
_pi_sock = None
_pi_lock = threading.Lock()
_ws_loop = None
_ws_ctrl = None
_ws_clients = set()
_ws_queue = None
_qr_worker = None

def _sv_clamp(idx, v):
    return int(max(SERVO_MINS[idx], min(SERVO_MAXS[idx], v)))


def _close_pi_sock():
    global _pi_sock
    with _pi_lock:
        if _pi_sock is not None:
            try: _pi_sock.shutdown(socket.SHUT_RDWR)
            except: pass
            try: _pi_sock.close()
            except: pass
            _pi_sock = None


def _send_pi(payload):
    global _pi_sock
    with _pi_lock:
        if _pi_sock is None: return
        try:
            _pi_sock.sendall((json.dumps(payload)+'\n').encode('utf-8'))
        except Exception as e:
            print(f"[rescue_win] Pi send error: {e}")
            try: _pi_sock.shutdown(socket.SHUT_RDWR)
            except: pass
            try: _pi_sock.close()
            except: pass
            _pi_sock = None


def _pi_recv_thread(ctrl):
    global _pi_sock
    buf = ""
    while True:
        with _pi_lock:
            sock = _pi_sock
        if sock is None:
            time.sleep(0.3); continue
        try:
            data = sock.recv(4096)
            if not data:
                ctrl._log("Pi disconnected")
                _close_pi_sock(); time.sleep(0.5); continue
            buf += data.decode("utf-8", errors="ignore")
            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                line = line.strip()
                if not line: continue
                try: ctrl._apply_from_pi(json.loads(line))
                except: pass
        except Exception as e:
            ctrl._log(f"Pi recv error: {e}")
            _close_pi_sock(); time.sleep(1)


def _pi_connect_thread(ctrl):
    global _pi_sock
    while True:
        with _pi_lock:
            connected = _pi_sock is not None
        if connected:
            time.sleep(2); continue
        s = None
        try:
            ctrl._log(f"Connecting {PI_IP}:{PI_TCP_PORT}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((PI_IP, PI_TCP_PORT))
            s.settimeout(None)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with _pi_lock:
                if _pi_sock is not None:
                    try: _pi_sock.close()
                    except: pass
                _pi_sock = s
            ctrl._log("Pi connected!")
        except Exception as e:
            ctrl._log(f"Pi connect failed: {e}")
            try:
                if s: s.close()
            except: pass
            time.sleep(3)


# ═══════════════════════════════════════════════════════════════
#  QR Worker
#  Poll Flask qr_server.py /status ทุก 0.5 วิ
#  แล้ว push ผล QR ผ่าน WebSocket ไปยัง Browser
#  Flask รันแยกต่างหาก: python qr_server.py
# ═══════════════════════════════════════════════════════════════
class QRWorker:
    def __init__(self, rtsp_url, push_callback, log_callback,
                 jpeg_quality=70, detect_width=960, send_fps=8):
        self._push     = push_callback
        self._log      = log_callback
        self._stop_evt = threading.Event()
        self._thread   = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_evt.set()

    def _run(self):
        self._log(f"[QRWorker] polling {FLASK_BASE}/status")
        last_qr = ""
        while not self._stop_evt.is_set():
            try:
                with urllib.request.urlopen(
                    f"{FLASK_BASE}/status", timeout=2
                ) as resp:
                    data = json.loads(resp.read().decode())

                qr_text = data.get("qr", "")
                all_qr  = data.get("all_qr", [])
                found   = bool(data.get("qr_found", False))

                # push ทุกครั้งที่ QR เปลี่ยน
                if found and qr_text != last_qr:
                    last_qr = qr_text
                    self._push({
                        "type":   "qr_found",
                        "qr":     qr_text,
                        "all_qr": all_qr,
                    })
                    self._log(f"[QR] {qr_text}")

            except Exception:
                # Flask ยังไม่รัน หรือ network error → รอต่อ
                pass

            self._stop_evt.wait(0.5)   # poll ทุก 0.5 วิ


# ═══════════════════════════════════════════════════════════════
#  Robot Controller
# ═══════════════════════════════════════════════════════════════
class RobotController:
    def __init__(self):
        self._mu     = threading.Lock()
        self._log_mu = threading.Lock()
        self.running = True

        self.active_keys   = set()
        self.is_locked     = False
        self.linear_speed  = LINEAR_SPEED
        self.angular_speed = ANGULAR_SPEED
        self.base_rpm      = BASE_RPM

        self.angles      = list(SERVO_DEFAULTS)
        self.selected    = 0
        self.servo_step  = 1
        self.motor_state = "stop"
        self.serial_ok   = False
        self.log_lines   = []

        # ── Send Loop 50Hz ───────────────────────────────────
        self._current_key       = ''
        self._send_loop_running = True
        threading.Thread(target=self._send_loop, daemon=True).start()

    # ── Send Loop ────────────────────────────────────────────
    def _send_loop(self):
        """ส่ง move_start ทุก 20ms พร้อม lin/ang ล่าสุด"""
        while self._send_loop_running:
            key = self._current_key
            if key and not self.is_locked:
                _send_pi({
                    "type": "move_start",
                    "key":  key,
                    "lin":  self.linear_speed,
                    "ang":  self.angular_speed,
                })
            time.sleep(0.02)  # 50Hz

    # ── Logging ──────────────────────────────────────────────
    def _log(self, msg):
        ts   = time.strftime("%H:%M:%S")
        line = f"{ts}  {msg}"
        print(line)
        with self._log_mu:
            self.log_lines.append(line)
            if len(self.log_lines) > 50: self.log_lines.pop(0)
        _push_ws({"type":"log_event","line":line})
        _push_ws({"type":"state",**self._state_dict()})

    def _apply_from_pi(self, msg):
        t = msg.get("type","")
        if t == "arm_status":
            self.serial_ok = True
            self._log(f"Teensy: {msg.get('status','')}")
        elif t == "state":
            if "angles" in msg and not msg.get("from_serial", False):
                self.angles = msg["angles"]
            if "serial" in msg:
                self.serial_ok = msg["serial"]
            _push_ws({"type":"state",**self._state_dict()})
        elif t == "arm_log":
            text = msg.get("text","")
            if text:
                _push_ws({"type":"arm_log","data":text})  # ← แก้ bug indent

    # ── Servo ────────────────────────────────────────────────
    def _servo_cmd(self, idx, angle):
        safe = _sv_clamp(idx, angle)
        self.angles[idx] = safe
        if idx == 5:
            if safe >= GRIP_MID:
                _send_pi({"type":"motor_reverse"})
                self.angles[5] = GRIP_OPEN
                self.motor_state = "reverse"
            else:
                _send_pi({"type":"motor_grip"})
                self.angles[5] = GRIP_CLOSE
                self.motor_state = "grip"
        else:
            _send_pi({"type":"servo_set","index":idx,"angle":safe})

    # ── Posture ──────────────────────────────────────────────
    def _send_posture(self, name):
        if name == 'horizontal':
            _send_pi({"type":"arm_raw",
                      "cmd":f"GOTO {HOME_Y} {HOME_Z} {HOME_P}"})
            self._log("Posture → horizontal")
            return
        if name == 'home':
            _send_pi({"type":"posture","name":"home"})
            self.angles = list(POSTURE_ANGLES['home'])
            self.motor_state = "stop"
            self._log("Posture → home")
            return
        # ── F7 QR SCAN — ส่ง GOTO ตรงๆ เหมือน F2 ──
        if name == 'custom_2':
            _send_pi({"type": "arm_raw", "cmd": "GOTO 35 18 2"})
            self._log("Posture → F7 QR SCAN (GOTO 35 18 2)")
            # ขยับ Flipper
            _send_pi({"type": "servo_set", "index": 6, "angle": 140})
            _send_pi({"type": "servo_set", "index": 7, "angle": 45})
            self.angles[6] = 140
            self.angles[7] = 45
            _push_ws({"type": "state", **self._state_dict()})
            return

        # ── Custom postures F4-F9 (giraff, stair, custom_1-4) ──
        if name in CUSTOM_POSTURES or name.startswith("custom_"):
            angles = CUSTOM_POSTURES.get(name)
            if angles is None:
                self._log(f"Posture {name} not set — กำหนดใน Settings UI")
                return
            self.angles = list(angles)
            self.motor_state = "stop"
            self._log(f"Posture → {name}: {angles}")
            for idx, angle in enumerate(angles):
                _send_pi({"type":"servo_set","index":idx,"angle":_sv_clamp(idx, angle)})
            _push_ws({"type":"state",**self._state_dict()})
            return

        angles = POSTURE_ANGLES.get(name)
        if not angles:
            self._log(f"Unknown posture: {name}"); return
        self.angles = list(angles)
        self.motor_state = "stop"
        self._log(f"Posture → {name}")
        for idx, angle in enumerate(angles):
            _send_pi({"type":"servo_set","index":idx,"angle":angle})

    # ── Wheel ────────────────────────────────────────────────
    def do_lock(self):
        self._current_key = ''
        self.is_locked = True
        _send_pi({"type":"lock"})
        _send_pi({"type":"move_stop"})
        self._log("BRAKE ENGAGED")

    def do_unlock(self):
        self.is_locked = False
        _send_pi({"type":"unlock"})
        self._log("BRAKE RELEASED")

    # ── State ────────────────────────────────────────────────
    def _state_dict(self):
        return {
            "angles":      self.angles,
            "selected":    self.selected,
            "motor_state": self.motor_state,
            "serial":      self.serial_ok,
            "lr":          0,
            "rr":          0,
            "heading":     0.0,
            "battery":     0,
            "log":         self.log_lines[-8:],
            "lin_spd":     self.linear_speed,
            "ang_spd":     self.angular_speed,
            "locked":      self.is_locked,
        }

    def snapshot(self): return self._state_dict()

    # ── WebSocket handler ────────────────────────────────────
    def handle_ws(self, msg):
        t = msg.get("type","")

        # ── Wheel ─────────────────────────────────────────────
        if t == "move_start":
            k = msg.get("key","")
            if k in MOVE_KEYS:
                self.linear_speed  = float(msg.get("lin", self.linear_speed))
                self.angular_speed = float(msg.get("ang", self.angular_speed))
                if self.is_locked:
                    self.do_unlock()
                self.active_keys.add(k)
                self._current_key = k

        elif t == "move_stop":
            k = msg.get("key","")
            self.active_keys.discard(k)
            if not self.active_keys:
                self._current_key = ''
                _send_pi({"type":"move_stop"})
                _send_pi({"type":"lock"})
                self.is_locked = True
                self._log("Auto-brake")
            else:
                self._current_key = next(iter(self.active_keys))

        elif t == "lock":
            self.active_keys.clear()
            self.do_lock()

        elif t == "unlock":
            self.do_unlock()

        elif t == "set_speed":
            self.linear_speed  = float(msg.get("lin", self.linear_speed))
            self.angular_speed = float(msg.get("ang", self.angular_speed))
            self.base_rpm      = int(msg.get("rpm", self.base_rpm))
            _send_pi({
                "type": "set_speed",
                "lin":  self.linear_speed,
                "ang":  self.angular_speed,
            })
            self._log(f"Speed → lin:{self.linear_speed:.2f} ang:{self.angular_speed:.2f}")

        # ── Servo ─────────────────────────────────────────────
        elif t == "servo_set":
            self._servo_cmd(int(msg.get("index",0)),
                            float(msg.get("angle",90)))

        elif t == "servo_set_multi":
            for idx, angle in msg.get("joints",{}).items():
                self._servo_cmd(int(idx), float(angle))

        elif t == "select_servo":
            self.selected = int(msg.get("index",0))
            _push_ws({"type":"state",**self._state_dict()})

        elif t == "set_step":
            self.servo_step = int(msg.get("step",1))

        # ── Gripper ───────────────────────────────────────────
        elif t == "motor_grip":
            self.motor_state = "grip"
            self.angles[5]   = GRIP_CLOSE
            _send_pi({"type":"motor_grip"})
            self._log("Gripper → CLOSE")

        elif t == "motor_reverse":
            self.motor_state = "reverse"
            self.angles[5]   = GRIP_OPEN
            _send_pi({"type":"motor_reverse"})
            self._log("Gripper → OPEN")

        elif t == "motor_stop":
            self.motor_state = "stop"
            self.angles[5]   = GRIP_OPEN
            _send_pi({"type":"motor_stop"})
            self._log("Gripper → STOP")

        # ── Posture ───────────────────────────────────────────
        elif t == "posture":
            self._send_posture(msg.get("name",""))

        # ── Save custom posture angles from Browser Settings ──
        elif t == "save_custom_posture":
            name   = msg.get("name","")
            angles = msg.get("angles", [])
            if name in CUSTOM_POSTURES and len(angles) == NUM_SERVOS:
                CUSTOM_POSTURES[name] = [float(a) for a in angles]
                self._log(f"Custom posture saved: {name} = {angles}")
                _push_ws({"type":"custom_posture_ack","name":name,"ok":True})
            else:
                self._log(f"save_custom_posture: invalid data name={name} len={len(angles)}")

        # ── arm_raw ───────────────────────────────────────────
        elif t == "arm_raw":
            _send_pi(msg)
            self._log(f"arm_raw: {msg.get('cmd','')}")

        elif t == "leds":
            _send_pi(msg)

        elif t == "laser":
            val = bool(msg.get("value", False))
            _send_pi({"type": "laser", "value": val})
            self._log(f"Laser → {'ON' if val else 'OFF'}")

        elif t == "snapshot":
            self._log("Snapshot requested")

        elif t == "qr_result":
            # Browser ส่ง QR result มาให้ log
            codes = msg.get("codes", [])
            if codes:
                self._log(f"[QR] Browser result: {' | '.join(codes)}")

    def cleanup(self):
        self.running = False
        self._send_loop_running = False
        self._current_key = ''
        _send_pi({"type":"move_stop"})
        _close_pi_sock()


# ═══════════════════════════════════════════════════════════════
#  WebSocket Server
# ═══════════════════════════════════════════════════════════════
def _push_ws(payload):
    global _ws_loop, _ws_queue
    if _ws_loop and _ws_loop.is_running() and _ws_queue is not None:
        try:
            asyncio.run_coroutine_threadsafe(_ws_queue.put(payload), _ws_loop)
        except Exception as e:
            print(f"[WS PUSH ERROR] {e}")

def _push_log(msg):
    if _ws_ctrl:
        _ws_ctrl._log(msg)
    else:
        print(msg)

async def _send_one(ws, data):
    await asyncio.wait_for(ws.send(data), timeout=WS_SEND_TIMEOUT)


async def _broadcast(payload):
    global _ws_clients
    if not _ws_clients: return
    data = json.dumps(payload)
    clients = list(_ws_clients)
    tasks = [asyncio.create_task(_send_one(ws, data)) for ws in clients]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    dead = set()
    for ws, result in zip(clients, results):
        if isinstance(result, Exception): dead.add(ws)
    if dead: _ws_clients -= dead


async def _ws_handler(websocket, path=None):
    global _ws_clients
    _ws_clients.add(websocket)
    if _ws_ctrl:
        await websocket.send(json.dumps({"type":"state",**_ws_ctrl.snapshot()}))
    try:
        async for raw in websocket:
            if raw == "__ping__":
                await websocket.send("__pong__"); continue
            try:    msg = json.loads(raw)
            except: continue
            if _ws_ctrl: _ws_ctrl.handle_ws(msg)
    except websockets.exceptions.ConnectionClosedOK: pass
    except Exception as e: print(f"[ws] error: {e}")
    finally: _ws_clients.discard(websocket)


async def _ws_event_loop():
    global _ws_queue
    while True:
        payload = await _ws_queue.get()
        try:    await _broadcast(payload)
        except Exception as e: print(f"[WS EVENT ERROR] {e}")


async def _ws_state_loop():
    while True:
        await asyncio.sleep(1.0 / STATE_PUSH_HZ)
        if _ws_ctrl and _ws_clients:
            try:    await _broadcast({"type":"state",**_ws_ctrl.snapshot()})
            except Exception as e: print(f"[WS STATE ERROR] {e}")


async def _ws_serve():
    global _ws_queue
    _ws_queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
    async with websockets.serve(
        _ws_handler, "0.0.0.0", WS_PORT,
        max_queue=64, ping_interval=10,
        ping_timeout=10, close_timeout=1,
    ):
        await asyncio.gather(_ws_event_loop(), _ws_state_loop())


# ═══════════════════════════════════════════════════════════════
#  HTTP Server
# ═══════════════════════════════════════════════════════════════
def _http_server(base):
    class CORSHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=base, **kwargs)

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin",   "*")
            self.send_header("Access-Control-Allow-Methods",  "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",  "Content-Type")
            self.send_header("X-Frame-Options",               "ALLOWALL")
            self.send_header("Content-Security-Policy",       "frame-ancestors *")
            super().end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    HTTPServer(("0.0.0.0", HTTP_PORT), CORSHandler).serve_forever()

# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════
def main():
    global _ws_ctrl, _ws_loop, _qr_worker

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    base = sys._MEIPASS if getattr(sys,"frozen",False) \
        else os.path.dirname(os.path.abspath(__file__))

    print(f"[HTTP] Serving from: {base}")
    threading.Thread(target=_http_server, args=(base,), daemon=True).start()

    ctrl = RobotController()
    _ws_ctrl = ctrl

    threading.Thread(target=_pi_connect_thread, args=(ctrl,), daemon=True).start()
    threading.Thread(target=_pi_recv_thread,    args=(ctrl,), daemon=True).start()

    if QR_ENABLE:
        _qr_worker = QRWorker(
            rtsp_url=RTSP_URL,
            push_callback=_push_ws,
            log_callback=_push_log,
            jpeg_quality=QR_JPEG_QUALITY,
            detect_width=QR_DETECT_WIDTH,
            send_fps=QR_SEND_FPS,
        )
        _qr_worker.start()

    def _open():
        time.sleep(1.2)
        webbrowser.open(f"http://localhost:{HTTP_PORT}/control.html")
    threading.Thread(target=_open, daemon=True).start()

    print(f"\n{'='*64}")
    print("  RescueBot Windows Bridge + QR")
    print(f"  WS   → ws://localhost:{WS_PORT}")
    print(f"  HTTP → http://localhost:{HTTP_PORT}/control.html")
    print(f"  Pi   → {PI_IP}:{PI_TCP_PORT}")
    print(f"  RTSP → {RTSP_URL}")
    print(f"  Flask QR → {FLASK_BASE}")
    print(f"{'='*64}\n")

    _ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_ws_loop)
    try:
        _ws_loop.run_until_complete(_ws_serve())
    except KeyboardInterrupt:
        pass
    finally:
        if _qr_worker:
            _qr_worker.stop()
        ctrl.cleanup()

    print("\nStopped.")


if __name__ == "__main__":
    main()