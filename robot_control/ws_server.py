#!/usr/bin/env python3
"""
ws_server.py  -  Windows WebSocket <-> UDP bridge
Runs on the PC. The HTML UI connects here via WebSocket.
This script translates WebSocket messages into UDP packets
sent to the Raspberry Pi (esp32_bridge.py).

Install:  pip install websockets
          pip install pyserial            (avatar mode)
          pip install opencv-python pyzbar  (QR logging; pyzbar = zbar decoder)
          pip install onnxruntime-gpu nvidia-cudnn-cu12 nvidia-cublas-cu12 \
              nvidia-cuda-runtime-cu12 nvidia-cufft-cu12 nvidia-curand-cu12
                                            (AI detection on GPU; CUDA-12 build)
Run:      python ws_server.py
"""

import asyncio
import websockets
import socket
import struct
import json
import threading
import os
import time
import mimetypes
import webbrowser
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None   # Avatar mode unavailable until `pip install pyserial`

try:
    import cv2          # QR logging unavailable until `pip install opencv-python`
    import numpy as np
except ImportError:
    cv2 = None
    np = None
try:
    # QR decoding uses pyzbar (zbar): `pip install pyzbar`
    from pyzbar.pyzbar import decode as _zbar_decode
    from pyzbar.pyzbar import ZBarSymbol
except Exception:
    _zbar_decode = None
    ZBarSymbol = None

import ast


def _add_cuda_dll_dirs():
    """Put the nvidia-*-cu12 pip wheels' bin folders on the DLL search path so
    onnxruntime's CUDA EP (and cuDNN's lazily-loaded sub-DLLs) can be found.
    No-op if the nvidia wheels aren't installed."""
    try:
        import nvidia, glob
        base = os.path.dirname(nvidia.__file__)
        for d in glob.glob(os.path.join(base, "*", "bin")):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


try:
    # AI detection (BTN 2) runs the X7 ONNX model on the GPU via onnxruntime-gpu.
    _add_cuda_dll_dirs()      # must run before onnxruntime loads its CUDA provider
    import onnxruntime as ort
except Exception:
    ort = None

# ─── Config ───────────────────────────────────────────────────────────────────
PI_IP         = "192.168.1.167"   # <-- SET THIS to your Pi's IP address
UDP_CMD_PORT  = 3390              # port esp32_bridge listens on
UDP_FB_PORT   = 3391              # port esp32_bridge sends feedback to
UDP_SERVO_PORT = 3391             # port UDPS.py listens on for servo JSON packets
UDP_ANGLES_PORT = 3392            # port UDPS.py forwards ANGLES:/DATA: readback to

WS_HOST       = "0.0.0.0"
WS_PORT       = 8765
HTTP_PORT     = 8780              # HTTP server for settings file I/O (8766 collides with VS Code's node port-forwarder)

# ─── Avatar mode config (ported from testcode2/avatar.py) ──────────────────────
# A physical "avatar" arm (a Teensy exposing 3 potentiometers over USB serial)
# drives servos 3/4/5 (Shoulder/Elbow/Extend). See testcode2/avatar.py for the
# original standalone bridge — this is the same logic embedded in the server.
AVATAR_PORT  = "COM16"     # fallback if auto-detect fails
AVATAR_BAUD  = 115200
SERVO_MARKER = 0xAA

SERVO_MIN = [0,   30,  50,  25,  0,  0,  0,  0]
SERVO_MAX = [180, 150, 130, 140, 155, 110, 180, 180]

# 3 channels mapped to servo ids 3, 4, 5
CALIB = [
    dict(pot=0, sid=3, rev=False, in0=0,   in1=180, out0=40, out1=165, name='Arm1 shoulder'),
    dict(pot=1, sid=4, rev=True,  in0=122, in1=180, out0=30, out1=150, name='Arm2 elbow'),
    dict(pot=2, sid=6, rev=False, in0=44,   in1=133, out0=0, out1=110, name='Wrist J6'),
    dict(pot=3, sid=5, rev=False, in0=15,  in1=76,  out0=0, out1=155, name='Extend'),
    dict(pot=4, sid=7, rev=False, in0=107, in1=170, out0=0, out1=180, name='Wrist roll'),
    dict(pot=5, sid=8, rev=False, in0=75,  in1=113, out0=0, out1=180, name='Gripper'),
]
AVATAR_DEADBAND = 2
AVATAR_NUM_POTS = len(CALIB)
TEENSY_PINS     = [3, 4, 5, 6, 7, 8]

# Settings file location - use absolute path relative to this script
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")

# Destination for maps/markers auto-uploaded from the Pi (slam_manager) over
# POST /api/upload-map — replaces the manual `scp -r pi@...:savedMaps .` step.
SAVED_MAPS_DIR = os.path.join(SCRIPT_DIR, "savedMaps")

# ─── QR code logging config (BTN 1 on the webcam toolbar) ──────────────────────
# Decodes QR codes off the Pi's MJPEG feed and logs each unique code to a
# timestamped CSV in savedCSV/. Header/filename fields below match the RoboCup
# POI spec. Must match WEBCAM_STREAM_URL in robot_control/js/main.js.
QR_STREAM_URL = f"http://{PI_IP}:8000/stream.mjpg"
SAVED_CSV_DIR = os.path.join(SCRIPT_DIR, "savedCSV")
QR_EVENT      = "RoboCup2026"
QR_TEAM       = "Born2Shine"
QR_CHALLENGE  = "Labyrinth"
QR_COUNTRY    = "Thailand"
# Cooldown (seconds) before the same QR value is logged again. The same code
# seen within this window is ignored so a code held in frame isn't written every
# frame; re-reading it after the buffer elapses logs a fresh row.
QR_REPEAT_SEC = 5

# On each logged QR, also tell the Pi's SLAM to drop a marker at the robot's
# current map pose (see Pi qr_marker_node). Marker byte 0xCC + JSON, mirroring
# the 0xAA servo / 0xFF lock framing, on its own UDP port. The throttle below is
# a global minimum spacing between markers so a burst of codes doesn't stack.
UDP_MARKER_PORT   = 3393      # Pi qr_marker_node listens here (0xCC + JSON)
QR_MARKER_MARKER  = 0xCC
QR_MARKER_MIN_SEC = 3         # min seconds between markers sent to SLAM

# ─── AI detection config (BTN 2 on the webcam toolbar) ─────────────────────────
# Runs the X7 ONNX model (Ultralytics YOLO11s, HazMat placards) on the same Pi
# MJPEG feed, on the GPU via onnxruntime-gpu, and streams bounding boxes to the
# UI. See _add_cuda_dll_dirs() above for the CUDA DLL setup.
AI_STREAM_URL = QR_STREAM_URL
AI_MODEL_PATH = os.path.join(SCRIPT_DIR, "X7.onnx")
AI_IMGSZ      = 640      # model input size (square); from the model's imgsz metadata
AI_CONF       = 0.35     # min confidence to keep a detection
AI_IOU        = 0.45     # NMS IoU threshold

# ─── Motion detection config (BTN 3 on the webcam toolbar) ─────────────────────
# Frame-differences the same Pi MJPEG feed (OpenCV only, no model) and streams a
# box around each moving region to the UI. No GPU/model needed.
MOTION_STREAM_URL = QR_STREAM_URL
MOTION_MIN_AREA   = 400    # ignore moving blobs smaller than this many px² (noise)
MOTION_THRESH     = 70     # per-pixel diff threshold (0-255) to count as "changed"
MOTION_BLUR       = 21     # gaussian blur kernel (odd) to damp sensor noise

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
    },
    "hotkeys": {
        "cycleMode": "m",
        "webcam": "p",
        "ik": ";",
        "postures": {
            "home": "F1", "stair": "F2", "ramp": "F3", "fold": "F4",
            "giraffe": "F5", "finish": "F6", "backramp": "F7"
        }
    },
    "avatar": {
        "deadband": AVATAR_DEADBAND,
        "calib": CALIB,
    },
    "cameras": [
        {"proto": "http://", "url": "", "zoom": 50},
        {"proto": "http://", "url": "", "zoom": 50},
    ],
    "webcamRotation": 0,
    "thermal": {"min": 25, "max": 35, "flipH": False, "flipV": False},
    "compass": {"axis": "z", "threshold": 500},
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
        """Handle POST requests for saving settings, and file uploads from the
        Pi (slam_manager pushing saved maps/markers — see do_POST_upload_map)."""
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
        elif self.path == "/api/upload-map":
            self._handle_upload_map()
        else:
            self.send_response(404)
            self.end_headers()

    def _json_response(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def _handle_upload_map(self):
        """Receive one raw file in the request body and write it under
        SAVED_MAPS_DIR. The Pi (slam_manager._upload_files) sends one POST per
        file, naming it via the X-Filename header and (optionally) placing it
        in a subfolder via X-Subdir — this is the auto-upload replacement for
        the manual `scp -r pi@...:savedMaps .` step.

        Both headers are sanitised the same way _serve_file guards against
        path traversal: reject anything that normalises outside SAVED_MAPS_DIR.
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._json_response(400, {"status": "error", "error": "bad Content-Length"})
            return

        raw_name = self.headers.get("X-Filename", "")
        raw_subdir = self.headers.get("X-Subdir", "")
        filename = os.path.basename(unquote(raw_name)).strip()
        if not filename:
            self._json_response(400, {"status": "error", "error": "missing X-Filename"})
            return

        subdir = unquote(raw_subdir).strip().strip("/\\")
        dest_dir = os.path.normpath(os.path.join(SAVED_MAPS_DIR, subdir)) if subdir else SAVED_MAPS_DIR
        root = os.path.normpath(SAVED_MAPS_DIR)
        # Anchor on root + sep (not just root) so a sibling dir like
        # "savedMaps2" can't pass a bare startswith(root) check.
        if not (dest_dir == root or dest_dir.startswith(root + os.sep)):
            self._json_response(403, {"status": "error", "error": "invalid subdir"})
            return
        dest_path = os.path.normpath(os.path.join(dest_dir, filename))
        if not (dest_path == root or dest_path.startswith(root + os.sep)):
            self._json_response(403, {"status": "error", "error": "invalid filename"})
            return

        try:
            os.makedirs(dest_dir, exist_ok=True)
            written = 0
            with open(dest_path, "wb") as f:
                remaining = content_length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
            self._json_response(200, {"status": "ok", "path": dest_path, "bytes": written})
            print(f"[Upload] {dest_path}  ({written} bytes)")
        except Exception as e:
            print(f"[Upload] Error writing {dest_path}: {e}")
            self._json_response(500, {"status": "error", "error": str(e)})

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self._send_cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename, X-Subdir")
        self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging (file serves are logged explicitly above)."""
        pass


def run_http_server():
    """Run HTTP server for settings I/O in a separate thread."""
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), SettingsHTTPHandler)
    print(f"[HTTP] Static file + settings server on http://localhost:{HTTP_PORT}")
    server.serve_forever()


def udp_feedback_thread(loop, sock=None):
    """Background thread: read UDP feedback from Pi, forward to all WS clients."""
    if sock is None:
        sock = udp_sock
    while True:
        try:
            # 4096: a thermal frame ({"type":"thermal","data":[64 floats]}) is
            # ~400 bytes — well over the old 256 cap, which truncated it into
            # invalid JSON. Keep this comfortably above the largest broadcast.
            data, _ = sock.recvfrom(4096)
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
        # Pull calibration from settings.json so saved values take effect,
        # falling back to the in-code CALIB / AVATAR_DEADBAND defaults.
        avatar_cfg = load_settings().get("avatar", {})
        calib = avatar_cfg.get("calib") or CALIB
        deadband = avatar_cfg.get("deadband", AVATAR_DEADBAND)

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
                    for i, c in enumerate(calib):
                        if i >= len(vals):
                            continue
                        angle = to_angle(vals[i], c)
                        sid   = c['sid']
                        if sid not in prev or abs(angle - prev[sid]) > deadband:
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


# ─── QR code logging (ported intent: decode Pi camera, write POI CSV) ──────────
def qr_status(text, running, count=None):
    """Broadcast QR logging status to WS clients so BTN 1 can reflect state."""
    if event_loop is not None and connected_clients:
        msg = {"cmd": "qr_status", "running": bool(running), "text": text}
        if count is not None:
            msg["count"] = count
        asyncio.run_coroutine_threadsafe(broadcast(json.dumps(msg)), event_loop)


def _csv_field(value):
    """Quote a data field per the POI spec: wrap in double quotes when it
    contains a space (also comma/quote/newline so the CSV stays valid), and
    double any embedded quotes."""
    s = str(value)
    if any(ch in s for ch in (' ', ',', '"', '\n', '\r')):
        return '"' + s.replace('"', '""') + '"'
    return s


def _rect_poly(rect):
    """Fallback 4-corner polygon from a pyzbar Rect (left, top, width, height)."""
    l, t, w, h = rect.left, rect.top, rect.width, rect.height
    return [[l, t], [l + w, t], [l + w, t + h], [l, t + h]]


def open_mjpeg_latest(url, stop_event, tag, timeout=5):
    """Open an MJPEG stream and start a daemon grabber that keeps only the most
    recent complete JPEG in latest['jpg'] (older frames are dropped, so decoders
    never fall behind real time). Returns (stream, latest, lock, thread); stream
    is None on failure."""
    try:
        stream = urllib.request.urlopen(url, timeout=timeout)
    except Exception as e:
        print(f"[{tag}] Could not open stream {url}: {e}")
        return None, None, None, None

    latest = {"jpg": None}
    lock = threading.Lock()

    def _grab():
        buf = b""
        try:
            while not stop_event.is_set():
                chunk = stream.read(8192)
                if not chunk:
                    break
                buf += chunk
                # Pull every complete JPEG (SOI 0xFFD8 … EOI 0xFFD9) out of the
                # buffer, keeping only the last — older frames are dropped.
                while True:
                    soi = buf.find(b"\xff\xd8")
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if soi != -1 and eoi != -1:
                        with lock:
                            latest["jpg"] = buf[soi:eoi + 2]
                        buf = buf[eoi + 2:]
                    else:
                        break
                if len(buf) > 4_000_000:   # guard against unbounded growth
                    buf = buf[-1_000_000:]
        except Exception as e:
            if not stop_event.is_set():
                print(f"[{tag}] Stream read error: {e}")

    thread = threading.Thread(target=_grab, daemon=True)
    thread.start()
    return stream, latest, lock, thread


class QrBridge:
    """Decodes QR codes from the Pi camera MJPEG stream and appends each unique
    code to a timestamped CSV in savedCSV/. Toggled by BTN 1 (cmd:"qr").

    Filename: RoboCup2026-Born2Shine-Labyrinth-HH-MM-SS-pois.csv
    Header:   "Born2Shine" / "Thailand" / start date / start time, then the
              column row `detection,detectedtime,qrcode detected data`.
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
        if cv2 is None:
            print("[QR] OpenCV not installed — run `pip install opencv-python`")
            qr_status("QR unavailable — pip install opencv-python", False)
            return
        if _zbar_decode is None:
            print("[QR] pyzbar not installed — run `pip install pyzbar`")
            qr_status("QR unavailable — pip install pyzbar", False)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        # Thread is daemon and releases the capture/file on exit; no join needed.
        self._thread = None

    def _decode(self, frame):
        """Return [{'data': str, 'poly': [[x,y],...]}] for QR codes in a frame,
        using pyzbar (zbar).

        poly is the code's 4 corners in source-frame pixels, used by the UI to
        draw a bounding box over the live stream.
        """
        results = []
        for sym in _zbar_decode(frame, symbols=[ZBarSymbol.QRCODE]):
            try:
                data = sym.data.decode("utf-8", errors="replace")
            except Exception:
                continue
            if not data:
                continue
            poly = [[int(p.x), int(p.y)] for p in sym.polygon] or _rect_poly(sym.rect)
            results.append({"data": data, "poly": poly})
        return results

    def _broadcast_detect(self, n, detected, data):
        if event_loop is not None and connected_clients:
            asyncio.run_coroutine_threadsafe(
                broadcast(json.dumps({
                    "cmd": "qr_detect", "n": n, "time": detected, "data": data
                })), event_loop)

    def _send_marker(self, n, detected, data):
        """Tell the Pi's SLAM to drop a map marker at the robot's current pose.
        `n` matches the CSV `detection` column; `data` is the decoded QR string.
        Best-effort UDP — a send failure must not kill the decode loop."""
        try:
            payload = json.dumps({
                "cmd": "qr_marker", "n": n, "id": data, "time": detected
            }).encode()
            udp_sock.sendto(bytes([QR_MARKER_MARKER]) + payload,
                            (PI_IP, UDP_MARKER_PORT))
        except Exception as e:
            print(f"[QR] marker send failed: {e}")

    def _send_overlay(self, w, h, detections):
        """Push every currently-visible code's polygon to the UI so it can draw
        bounding boxes over the stream. Sent each decode cycle (not deduped) so
        boxes track the codes live; an empty list clears the overlay."""
        if event_loop is None or not connected_clients:
            return
        codes = [{"data": d["data"], "poly": d["poly"]} for d in detections]
        asyncio.run_coroutine_threadsafe(
            broadcast(json.dumps({"cmd": "qr_overlay", "w": w, "h": h, "codes": codes})),
            event_loop)

    def _run(self):
        start = time.localtime()
        fname = "%s-%s-%s-%s-pois.csv" % (
            QR_EVENT, QR_TEAM, QR_CHALLENGE, time.strftime("%H-%M-%S", start))
        path = os.path.join(SAVED_CSV_DIR, fname)

        try:
            os.makedirs(SAVED_CSV_DIR, exist_ok=True)
            f = open(path, "w", newline="", encoding="utf-8")
        except Exception as e:
            print(f"[QR] Could not create {path}: {e}")
            qr_status(f"QR file error: {e}", False)
            return

        # POI header: four quoted fields, then the column row (written verbatim).
        f.write('"%s"\n' % QR_TEAM)
        f.write('"%s"\n' % QR_COUNTRY)
        f.write('"%s"\n' % time.strftime("%d/%m/%Y", start))
        f.write('"%s"\n' % time.strftime("%H:%M:%S", start))
        f.write("detection,detectedtime,qrcode detected data\n")
        f.flush()

        # Read the MJPEG socket directly and decode only the newest frame so QR
        # detection stays on the live image (see open_mjpeg_latest).
        stream, latest, latest_lock, grabber = open_mjpeg_latest(QR_STREAM_URL, self._stop, "QR")
        if stream is None:
            qr_status("QR: cannot open camera stream", False)
            f.close()
            return

        last_logged = {}   # qr value -> monotonic time it was last written
        count = 0
        last_marker = 0.0  # monotonic time a marker was last sent to SLAM
        had_overlay = False
        print(f"[QR] Logging to {path}")
        qr_status(f"QR logging → {fname}", True, 0)
        try:
            while not self._stop.is_set():
                # Decode at ~7 Hz, always on the newest grabbed JPEG.
                time.sleep(0.14)
                with latest_lock:
                    jpg = latest["jpg"]
                    latest["jpg"] = None   # consume so we never re-decode a stale frame
                if jpg is None:
                    continue
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue

                detections = self._decode(frame)
                # Live bounding-box overlay for every visible code (send one
                # empty frame on the falling edge to clear the UI overlay).
                if detections:
                    self._send_overlay(frame.shape[1], frame.shape[0], detections)
                    had_overlay = True
                elif had_overlay:
                    self._send_overlay(frame.shape[1], frame.shape[0], [])
                    had_overlay = False

                # CSV logging: log each code, but not more than once per
                # QR_REPEAT_SEC so a code held in frame isn't written every frame.
                now = time.monotonic()
                for item in detections:
                    data = item["data"]
                    prev = last_logged.get(data)
                    if prev is not None and now - prev < QR_REPEAT_SEC:
                        continue
                    last_logged[data] = now
                    count += 1
                    detected = time.strftime("%H:%M:%S")
                    f.write("%d,%s,%s\n" % (count, detected, _csv_field(data)))
                    f.flush()
                    print(f"[QR] #{count} {detected} {data!r}")
                    self._broadcast_detect(count, detected, data)
                    # Drop a marker on the SLAM map, but no faster than
                    # QR_MARKER_MIN_SEC so a burst of codes doesn't stack.
                    if now - last_marker >= QR_MARKER_MIN_SEC:
                        self._send_marker(count, detected, data)
                        last_marker = now
                    qr_status(f"QR logging → {fname}", True, count)
        finally:
            # Close the socket first so a blocked stream.read() unblocks, then
            # let the grabber notice the stop flag and exit.
            try:
                stream.close()
            except Exception:
                pass
            grabber.join(timeout=1.0)
            try:
                f.close()
            except Exception:
                pass
            print(f"[QR] Stopped ({count} codes) → {path}")
            qr_status(f"QR stopped — {count} code(s) saved", False, count)


qr_bridge = QrBridge()


# ─── AI object detection (BTN 2): X7 YOLO11s on the GPU ────────────────────────
def ai_status(text, running, provider=None):
    """Broadcast AI detection status to WS clients so BTN 2 reflects state."""
    if event_loop is not None and connected_clients:
        msg = {"cmd": "ai_status", "running": bool(running), "text": text}
        if provider is not None:
            msg["provider"] = provider
        asyncio.run_coroutine_threadsafe(broadcast(json.dumps(msg)), event_loop)


class AiBridge:
    """Runs the X7 ONNX model (Ultralytics YOLO11s, anchor-free) on the Pi camera
    MJPEG stream and streams bounding boxes to the UI. Toggled by BTN 2
    (cmd:"ai"). Inference runs on the GPU (CUDAExecutionProvider) when available,
    falling back to CPU with a warning."""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._session = None
        self._input_name = None
        self._names = {}
        self._provider = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _load_session(self):
        """Create the ONNX session once, preferring the CUDA GPU provider."""
        if self._session is not None:
            return True
        if ort is None or cv2 is None:
            return False
        if not os.path.isfile(AI_MODEL_PATH):
            print(f"[AI] Model not found: {AI_MODEL_PATH}")
            ai_status("AI: model X7.onnx not found", False)
            return False
        try:
            if hasattr(ort, "preload_dlls"):
                try:
                    ort.preload_dlls()
                except Exception:
                    pass
            providers = ort.get_available_providers()
            use = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in providers]
            sess = ort.InferenceSession(AI_MODEL_PATH, providers=use)
            self._session = sess
            self._input_name = sess.get_inputs()[0].name
            self._provider = sess.get_providers()[0]
            # Class names are embedded in the model metadata as a dict literal.
            meta = sess.get_modelmeta().custom_metadata_map
            try:
                self._names = ast.literal_eval(meta.get("names", "{}"))
            except Exception:
                self._names = {}
            if self._provider != "CUDAExecutionProvider":
                print("[AI] WARNING: running on CPU — CUDA provider unavailable")
            print(f"[AI] Model loaded on {self._provider}")
            return True
        except Exception as e:
            print(f"[AI] Failed to load model: {e}")
            ai_status(f"AI load error: {e}", False)
            return False

    def start(self):
        if self.running:
            return
        if ort is None or cv2 is None:
            print("[AI] onnxruntime/opencv missing — run `pip install onnxruntime-gpu opencv-python`")
            ai_status("AI unavailable — pip install onnxruntime-gpu", False)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    def _preprocess(self, frame):
        """Letterbox to AI_IMGSZ and return (tensor, ratio, pad_x, pad_y)."""
        h, w = frame.shape[:2]
        r = min(AI_IMGSZ / h, AI_IMGSZ / w)
        nw, nh = round(w * r), round(h * r)
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((AI_IMGSZ, AI_IMGSZ, 3), 114, dtype=np.uint8)
        px, py = (AI_IMGSZ - nw) // 2, (AI_IMGSZ - nh) // 2
        canvas[py:py + nh, px:px + nw] = resized
        blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None])  # 1,3,H,W
        return blob, r, px, py

    def _postprocess(self, out, r, px, py, fw, fh):
        """Decode YOLO output (1, 4+nc, 8400) → [{label, conf, box:[x1,y1,x2,y2]}]
        in original-frame pixel coords, with NMS."""
        preds = out[0].T                       # (8400, 4+nc)
        cls_scores = preds[:, 4:]
        class_ids = np.argmax(cls_scores, axis=1)
        confs = cls_scores[np.arange(cls_scores.shape[0]), class_ids]
        keep = confs > AI_CONF
        if not np.any(keep):
            return []
        boxes = preds[keep, :4]
        confs = confs[keep]
        class_ids = class_ids[keep]
        # cx,cy,w,h (letterbox space) → x,y,w,h (original-frame space) for NMS.
        cx, cy, bw, bh = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        x = (cx - bw / 2 - px) / r
        y = (cy - bh / 2 - py) / r
        ww, hh = bw / r, bh / r
        rects = np.stack([x, y, ww, hh], axis=1)
        idxs = cv2.dnn.NMSBoxes(rects.tolist(), confs.tolist(), AI_CONF, AI_IOU)
        if len(idxs) == 0:
            return []
        results = []
        for i in np.array(idxs).flatten():
            x1 = max(0, min(fw, float(x[i])))
            y1 = max(0, min(fh, float(y[i])))
            x2 = max(0, min(fw, float(x[i] + ww[i])))
            y2 = max(0, min(fh, float(y[i] + hh[i])))
            cid = int(class_ids[i])
            results.append({
                "label": self._names.get(cid, str(cid)),
                "conf": round(float(confs[i]), 2),
                "box": [round(x1), round(y1), round(x2), round(y2)],
            })
        return results

    def _send_overlay(self, w, h, boxes):
        if event_loop is None or not connected_clients:
            return
        asyncio.run_coroutine_threadsafe(
            broadcast(json.dumps({"cmd": "ai_overlay", "w": w, "h": h, "boxes": boxes})),
            event_loop)

    def _run(self):
        if not self._load_session():
            return
        stream, latest, lock, grabber = open_mjpeg_latest(AI_STREAM_URL, self._stop, "AI")
        if stream is None:
            ai_status("AI: cannot open camera stream", False)
            return

        had_overlay = False
        print(f"[AI] Detection running on {self._provider}")
        ai_status(f"AI detection on ({self._provider})", True, self._provider)
        try:
            while not self._stop.is_set():
                time.sleep(0.06)   # ~15 Hz cap; inference itself is the real limiter
                with lock:
                    jpg = latest["jpg"]
                    latest["jpg"] = None
                if jpg is None:
                    continue
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                fh, fw = frame.shape[:2]
                blob, r, px, py = self._preprocess(frame)
                try:
                    out = self._session.run(None, {self._input_name: blob})[0]
                except Exception as e:
                    print(f"[AI] Inference error: {e}")
                    continue
                boxes = self._postprocess(out, r, px, py, fw, fh)
                if boxes:
                    self._send_overlay(fw, fh, boxes)
                    had_overlay = True
                elif had_overlay:
                    self._send_overlay(fw, fh, [])
                    had_overlay = False
        finally:
            try:
                stream.close()
            except Exception:
                pass
            grabber.join(timeout=1.0)
            print("[AI] Detection stopped")
            ai_status("AI detection off", False)


ai_bridge = AiBridge()


# ─── Motion detection (frame differencing — flags things that are moving) ───────
def motion_status(text, running):
    """Broadcast motion detection status to WS clients so BTN 3 reflects state."""
    if event_loop is not None and connected_clients:
        msg = {"cmd": "motion_status", "running": bool(running), "text": text}
        asyncio.run_coroutine_threadsafe(broadcast(json.dumps(msg)), event_loop)


class MotionBridge:
    """Detects movement in the Pi camera MJPEG stream by differencing consecutive
    frames and streams a bounding box around each moving region to the UI.
    Toggled by BTN 3 (cmd:"mdet"). Pure OpenCV — no model or GPU required."""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        if cv2 is None:
            print("[MOTION] OpenCV not installed — run `pip install opencv-python`")
            motion_status("Motion unavailable — pip install opencv-python", False)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    def _send_overlay(self, w, h, boxes):
        if event_loop is None or not connected_clients:
            return
        asyncio.run_coroutine_threadsafe(
            broadcast(json.dumps({"cmd": "motion_overlay", "w": w, "h": h, "boxes": boxes})),
            event_loop)

    def _prep(self, frame):
        """Grayscale + blur so only real movement (not sensor noise) survives."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (MOTION_BLUR, MOTION_BLUR), 0)

    def _run(self):
        stream, latest, lock, grabber = open_mjpeg_latest(MOTION_STREAM_URL, self._stop, "MOTION")
        if stream is None:
            motion_status("Motion: cannot open camera stream", False)
            return

        prev = None
        had_overlay = False
        print("[MOTION] Detection running")
        motion_status("Motion detection on", True)
        try:
            while not self._stop.is_set():
                time.sleep(0.06)   # ~15 Hz cap
                with lock:
                    jpg = latest["jpg"]
                    latest["jpg"] = None
                if jpg is None:
                    continue
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                fh, fw = frame.shape[:2]
                cur = self._prep(frame)
                if prev is None:
                    prev = cur
                    continue
                # Absolute difference → threshold → dilate to merge nearby pixels.
                delta = cv2.absdiff(prev, cur)
                prev = cur
                thresh = cv2.threshold(delta, MOTION_THRESH, 255, cv2.THRESH_BINARY)[1]
                thresh = cv2.dilate(thresh, None, iterations=2)
                contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                boxes = []
                for c in contours:
                    if cv2.contourArea(c) < MOTION_MIN_AREA:
                        continue
                    x, y, w, h = cv2.boundingRect(c)
                    boxes.append({"box": [x, y, x + w, y + h]})
                if boxes:
                    self._send_overlay(fw, fh, boxes)
                    had_overlay = True
                elif had_overlay:
                    self._send_overlay(fw, fh, [])
                    had_overlay = False
        finally:
            try:
                stream.close()
            except Exception:
                pass
            grabber.join(timeout=1.0)
            print("[MOTION] Detection stopped")
            motion_status("Motion detection off", False)


motion_bridge = MotionBridge()


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

                elif cmd == "ik":
                    # Cartesian arm target solved on the Teensy (applyIK). Forward
                    # over the same 0xAA-marked serial path as servo/laser/led.
                    y     = float(obj.get("y"))
                    z     = float(obj.get("z"))
                    pitch = float(obj.get("pitch", 0.0))
                    payload = json.dumps({"cmd": "ik", "y": y, "z": z, "pitch": pitch}).encode()
                    udp_sock.sendto(bytes([0xAA]) + payload, (PI_IP, UDP_SERVO_PORT))

                elif cmd in ("laser", "led"):
                    # Forward straight to the Teensy over the same serial path as
                    # servo (0xAA-marked JSON on UDP_SERVO_PORT). state: 1=on, 0=off.
                    state = int(obj.get("state", 0))
                    payload = json.dumps({"cmd": cmd, "state": state}).encode()
                    udp_sock.sendto(bytes([0xAA]) + payload, (PI_IP, UDP_SERVO_PORT))

                elif cmd == "avatar":
                    state = int(obj.get("state", 0))
                    if state:
                        avatar_bridge.start()
                    else:
                        avatar_bridge.stop()

                elif cmd == "qr":
                    state = int(obj.get("state", 0))
                    if state:
                        qr_bridge.start()
                    else:
                        qr_bridge.stop()

                elif cmd == "ai":
                    state = int(obj.get("state", 0))
                    if state:
                        ai_bridge.start()
                    else:
                        ai_bridge.stop()

                elif cmd == "mdet":
                    state = int(obj.get("state", 0))
                    if state:
                        motion_bridge.start()
                    else:
                        motion_bridge.stop()

            except Exception as e:
                print(f"[WS] Error handling message: {e}")
    finally:
        connected_clients.discard(websocket)
        # Safety: release the avatar arm and stop QR/AI/motion workers if no UI is left.
        if not connected_clients:
            avatar_bridge.stop()
            qr_bridge.stop()
            ai_bridge.stop()
            motion_bridge.stop()
        print(f"[WS] Client disconnected: {websocket.remote_address}")


async def main():
    global event_loop
    loop = asyncio.get_event_loop()
    event_loop = loop   # let background threads (UDP feedback, avatar) reach the loop

    # Start HTTP server in background thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    # Open the UI in the default browser once the HTTP server is up
    ui_url = f"http://localhost:{HTTP_PORT}/control.html"
    threading.Timer(1.0, lambda: webbrowser.open(ui_url)).start()

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
    print(f"[Upload]  Maps/markers pushed from the Pi land in: {SAVED_MAPS_DIR}")
    print(f"[HTTP]    Open  http://localhost:{HTTP_PORT}/control.html  in your browser.\n")

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())