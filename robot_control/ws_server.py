#!/usr/bin/env python3
"""
ws_server.py  -  Windows WebSocket <-> UDP bridge
Runs on the PC. The HTML UI connects here via WebSocket.
This script translates WebSocket messages into UDP packets
sent to the Raspberry Pi (esp32_bridge.py).

Install:  pip install websockets
Run:      python ws_server.py
"""

import asyncio
import websockets
import socket
import struct
import json
import threading
import os
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None   # Avatar mode unavailable until `pip install pyserial`

# ─── Config ───────────────────────────────────────────────────────────────────
PI_IP         = "192.168.1.167"   # <-- SET THIS to your Pi's IP address
UDP_CMD_PORT  = 3390              # port esp32_bridge listens on
UDP_FB_PORT   = 3391              # port esp32_bridge sends feedback to
UDP_SERVO_PORT = 3391             # port UDPS.py listens on for servo JSON packets
UDP_ANGLES_PORT = 3392            # port UDPS.py forwards ANGLES:/DATA: readback to

WS_HOST       = "0.0.0.0"
WS_PORT       = 8765
HTTP_PORT     = 8766              # HTTP server for settings file I/O

# ─── Avatar mode config (ported from testcode2/avatar.py) ──────────────────────
# A physical "avatar" arm (a Teensy exposing 3 potentiometers over USB serial)
# drives servos 3/4/5 (Shoulder/Elbow/Extend). See testcode2/avatar.py for the
# original standalone bridge — this is the same logic embedded in the server.
AVATAR_PORT  = "COM3"     # fallback if auto-detect fails
AVATAR_BAUD  = 115200
SERVO_MARKER = 0xAA

SERVO_MIN = [0,   30,  40,  30,  20,  70,  80,  35]
SERVO_MAX = [180, 150, 165, 180, 180, 150, 180, 140]

# 3 channels mapped to servo ids 3, 4, 5
CALIB = [
    dict(pot=0, sid=3, rev=False, in0=0,   in1=180, out0=40, out1=165, name='Arm1 shoulder'),
    dict(pot=1, sid=4, rev=True,  in0=122, in1=180, out0=30, out1=150, name='Arm2 elbow'),
    dict(pot=2, sid=5, rev=False, in0=0,   in1=180, out0=20, out1=180, name='Arm3 wrist'),
]
AVATAR_DEADBAND = 2
AVATAR_NUM_POTS = len(CALIB)
TEENSY_PINS     = [3, 4, 5]

# Settings file location - use absolute path relative to this script
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")

# Default parameters
DEFAULT_SETTINGS = {
    "gears": [40, 80, 120, 160, 200],
    "thresholds": {
        "tor": {"warn": 0.96, "crit": 2.0},
        "tmp": {"warn": 60, "crit": 75}
    },
    "postures": {
        "home": [90, 90, 90, 90, 90, 90, 90, 90],
        "stair": [90, 90, 90, 90, 90, 90, 90, 90],
        "ramp": [90, 90, 90, 90, 90, 90, 90, 90],
        "fold": [90, 90, 90, 90, 90, 90, 90, 90],
        "giraffe": [90, 90, 90, 90, 90, 90, 90, 90],
        "finish": [90, 90, 90, 90, 90, 90, 90, 90],
        "backramp": [90, 90, 90, 90, 90, 90, 90, 90]
    }
}
# ──────────────────────────────────────────────────────────────────────────────

udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_sock.bind(("0.0.0.0", UDP_FB_PORT))
udp_sock.settimeout(0.1)

angles_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
angles_sock.bind(("0.0.0.0", UDP_ANGLES_PORT))
angles_sock.settimeout(0.1)

connected_clients = set()

# Captured in main() so background threads can schedule coroutines on the loop.
event_loop = None


def load_settings():
    """Load settings from file, or return defaults if file doesn't exist."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Settings] Error loading settings: {e}")
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    """Save settings to file."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        print(f"[Settings] Error saving settings: {e}")
        return False


class SettingsHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler: /api/settings JSON API  +  static file server for the UI."""

    # Extra MIME types the standard library doesn't always know
    _EXTRA_MIME = {
        ".stl":  "model/stl",
        ".js":   "application/javascript",
        ".mjs":  "application/javascript",
        ".wasm": "application/wasm",
        ".map":  "application/json",
    }

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def _serve_file(self, rel_path):
        """Serve a file from SCRIPT_DIR, resolving .. to prevent path traversal."""
        # Decode percent-encoding and strip leading slash
        rel_path = unquote(rel_path).lstrip("/")
        # Normalise and guard against path traversal
        safe = os.path.normpath(os.path.join(SCRIPT_DIR, rel_path))
        if not safe.startswith(os.path.normpath(SCRIPT_DIR)):
            self.send_response(403)
            self.end_headers()
            return
        if not os.path.isfile(safe):
            self.send_response(404)
            self._send_cors()
            self.end_headers()
            return
        ext = os.path.splitext(safe)[1].lower()
        mime = self._EXTRA_MIME.get(ext) or mimetypes.guess_type(safe)[0] or "application/octet-stream"
        try:
            with open(safe, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self._send_cors()
            self.end_headers()
            self.wfile.write(data)
            print(f"[HTTP] GET {rel_path}  ({len(data)} bytes)")
        except Exception as exc:
            print(f"[HTTP] Error serving {safe}: {exc}")
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        """API route first; fall through to static file server."""
        if self.path == "/api/settings":
            settings = load_settings()
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self._send_cors()
            self.end_headers()
            self.wfile.write(json.dumps(settings).encode())
        elif self.path in ("/", ""):
            # Redirect bare root to control.html
            self.send_response(302)
            self.send_header("Location", "/control.html")
            self.end_headers()
        else:
            self._serve_file(self.path)

    def do_POST(self):
        """Handle POST requests for saving settings."""
        if self.path == "/api/settings":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                settings = json.loads(body.decode())
                
                if save_settings(settings):
                    self.send_response(200)
                    self.send_header("Content-type", "application/json")
                    self._send_cors()
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok"}).encode())
                    print(f"[Settings] Saved: {settings}")
                else:
                    self.send_response(500)
                    self.end_headers()
            except Exception as e:
                print(f"[Settings] Error: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self._send_cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging (file serves are logged explicitly above)."""
        pass


def run_http_server():
    """Run HTTP server for settings I/O in a separate thread."""
    server = HTTPServer(("0.0.0.0", HTTP_PORT), SettingsHTTPHandler)
    print(f"[HTTP] Static file + settings server on http://localhost:{HTTP_PORT}")
    server.serve_forever()


def udp_feedback_thread(loop, sock=None):
    """Background thread: read UDP feedback from Pi, forward to all WS clients."""
    if sock is None:
        sock = udp_sock
    while True:
        try:
            data, _ = sock.recvfrom(256)
            msg = data.decode("utf-8", errors="ignore").strip()
            if msg and connected_clients:
                asyncio.run_coroutine_threadsafe(
                    broadcast(msg), loop
                )
        except socket.timeout:
            pass
        except Exception:
            pass


async def broadcast(msg):
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send(msg)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


# ─── Avatar bridge (ported from testcode2/avatar.py) ───────────────────────────
def find_avatar_teensy():
    """Auto-detect the avatar-arm Teensy by USB description."""
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = ((p.description or '') + (p.manufacturer or '')).lower()
        if any(kw in desc for kw in ['teensy', 'usb serial', 'arduino']):
            return p.device
    return ports[0].device if len(ports) == 1 else None


def parse_line(raw):
    """Parse a serial line into a list of pot angles (0-180). See avatar.py."""
    if raw.startswith(('ANGLES:', 'DATA:', '===')):
        return None

    # Format 2: D3=1770 D4=0 D5=1500
    if '=' in raw and raw[0] in ('D', 'A'):
        vals = {}
        for part in raw.split():
            if '=' in part:
                try:
                    k, v = part.split('=')
                    vals[int(k[1:])] = int(v)
                except Exception:
                    pass
        if vals:
            result = []
            for pin in TEENSY_PINS:
                pw = vals.get(pin, 0)
                result.append(90 if pw == 0 else
                               max(0, min(180, int((pw - 1000) * 180 / 1000))))
            return result

    # Format 1: comma "90,145,60"
    parts = raw.split(',')
    if len(parts) >= AVATAR_NUM_POTS:
        try:
            vals = [int(p.strip()) for p in parts[:AVATAR_NUM_POTS]]
            if all(0 <= v <= 180 for v in vals):
                return vals
        except Exception:
            pass

    return None


def to_angle(val, c):
    """Map a raw pot value to a clamped servo angle using a CALIB channel."""
    ratio = (val - c['in0']) / max(1, c['in1'] - c['in0'])
    ratio = max(0.0, min(1.0, ratio))
    if c['rev']:
        ratio = 1.0 - ratio
    angle = c['out0'] + ratio * (c['out1'] - c['out0'])
    idx   = c['sid'] - 1
    return max(SERVO_MIN[idx], min(SERVO_MAX[idx], int(angle)))


def avatar_status(text):
    """Broadcast an avatar status line to all WS clients (UI placeholder)."""
    if event_loop is not None and connected_clients:
        asyncio.run_coroutine_threadsafe(
            broadcast(json.dumps({"cmd": "avatar_status", "text": text})), event_loop
        )


class AvatarBridge:
    """Reads the physical avatar arm and drives servos 3/4/5.

    Started/stopped on demand when the UI enters/leaves Avatar mode. Sends servo
    commands to the Pi over UDP (same path as cmd:"servo") and echoes each angle
    back to WS clients so the sliders / 3D sim track the physical arm.
    """

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        if serial is None:
            print("[Avatar] pyserial not installed — run `pip install pyserial`")
            avatar_status("Avatar unavailable — pyserial not installed")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        # Thread is daemon and closes its own serial port on exit; no join needed.
        self._thread = None

    def _run(self):
        port = None
        try:
            port = find_avatar_teensy()
        except Exception as e:
            print(f"[Avatar] Port scan failed: {e}")
        if not port:
            print(f"[Avatar] No Teensy auto-detected, trying {AVATAR_PORT}")
            port = AVATAR_PORT

        try:
            t1 = serial.Serial(port, AVATAR_BAUD, timeout=1)
        except Exception as e:
            print(f"[Avatar] Could not open {port}: {e}")
            avatar_status(f"Avatar arm not found ({port})")
            return

        print(f"[Avatar] Bridge running on {port} → servos 3/4/5 → Pi")
        avatar_status(f"Avatar arm connected ({port}) → Shoulder / Elbow / Extend")
        prev = {}
        try:
            while not self._stop.is_set():
                try:
                    raw = t1.readline().decode(errors='ignore').strip()
                    if not raw:
                        continue
                    vals = parse_line(raw)
                    if vals is None:
                        continue
                    for i, c in enumerate(CALIB):
                        if i >= len(vals):
                            continue
                        angle = to_angle(vals[i], c)
                        sid   = c['sid']
                        if sid not in prev or abs(angle - prev[sid]) > AVATAR_DEADBAND:
                            self._send_servo(sid, angle)
                            prev[sid] = angle
                except Exception as e:
                    print(f"[Avatar] {e}")
        finally:
            try:
                t1.close()
            except Exception:
                pass
            print("[Avatar] Bridge stopped")
            avatar_status("Avatar mode idle")

    def _send_servo(self, sid, angle):
        # 1) Drive the real servo via the Pi (same path as cmd:"servo").
        payload = json.dumps({"cmd": "servo", "id": sid, "angle": angle}).encode()
        udp_sock.sendto(bytes([SERVO_MARKER]) + payload, (PI_IP, UDP_SERVO_PORT))
        # 2) Echo to UI clients so sliders / 3D sim follow the physical arm.
        if event_loop is not None and connected_clients:
            asyncio.run_coroutine_threadsafe(
                broadcast(json.dumps({"cmd": "servo", "id": sid, "angle": angle})),
                event_loop,
            )


avatar_bridge = AvatarBridge()


async def handler(websocket):
    connected_clients.add(websocket)
    print(f"[WS] Client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            try:
                obj = json.loads(message)
                cmd = obj.get("cmd")

                if cmd == "motion":
                    linear  = float(obj.get("linear",  0.0))
                    angular = float(obj.get("angular", 0.0))
                    pkt = struct.pack("ff", linear, angular)
                    udp_sock.sendto(pkt, (PI_IP, UDP_CMD_PORT))

                elif cmd == "lock":
                    state = int(obj.get("state", 0))
                    pkt = struct.pack("Bb", 0xFF, state)
                    udp_sock.sendto(pkt, (PI_IP, UDP_CMD_PORT))

                elif cmd == "servo":
                    servo_id = int(obj.get("id", 0))
                    angle = int(obj.get("angle", 90))
                    if servo_id < 1 or servo_id > 8:
                        raise ValueError(f"servo id out of range: {servo_id}")
                    if angle < 0 or angle > 180:
                        raise ValueError(f"servo angle out of range: {angle}")
                    payload = json.dumps({
                        "cmd": "servo",
                        "id": servo_id,
                        "angle": angle
                    }).encode()
                    udp_sock.sendto(bytes([0xAA]) + payload, (PI_IP, UDP_SERVO_PORT))

                elif cmd == "avatar":
                    state = int(obj.get("state", 0))
                    if state:
                        avatar_bridge.start()
                    else:
                        avatar_bridge.stop()

            except Exception as e:
                print(f"[WS] Error handling message: {e}")
    finally:
        connected_clients.discard(websocket)
        # Safety: release the avatar arm if no UI is left to drive it.
        if not connected_clients:
            avatar_bridge.stop()
        print(f"[WS] Client disconnected: {websocket.remote_address}")


async def main():
    global event_loop
    loop = asyncio.get_event_loop()
    event_loop = loop   # let background threads (UDP feedback, avatar) reach the loop

    # Start HTTP server in background thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Start UDP feedback threads (ESP32 telemetry on 3391, servo ANGLES readback on 3392)
    t = threading.Thread(target=udp_feedback_thread, args=(loop,), daemon=True)
    t.start()
    t2 = threading.Thread(target=udp_feedback_thread, args=(loop, angles_sock), daemon=True)
    t2.start()

    print(f"[WS] Server running on ws://{WS_HOST}:{WS_PORT}")
    print(f"[WS] Forwarding UDP to {PI_IP}:{UDP_CMD_PORT}")
    print(f"[WS] Listening for UDP feedback on port {UDP_FB_PORT}")
    print(f"[WS] Listening for servo angle readback on port {UDP_ANGLES_PORT}")
    print(f"[Settings] File-based storage enabled: {SETTINGS_FILE}")
    print(f"[HTTP]    Open  http://localhost:{HTTP_PORT}/control.html  in your browser.\n")

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
