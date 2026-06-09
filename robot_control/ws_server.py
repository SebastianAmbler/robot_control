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
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ─── Config ───────────────────────────────────────────────────────────────────
PI_IP         = "192.168.1.101"   # <-- SET THIS to your Pi's IP address
UDP_CMD_PORT  = 3390              # port esp32_bridge listens on
UDP_FB_PORT   = 3391              # port esp32_bridge sends feedback to

WS_HOST       = "0.0.0.0"
WS_PORT       = 8765
HTTP_PORT     = 8766              # HTTP server for settings file I/O

# Settings file location - use absolute path relative to this script
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")

# Default parameters
DEFAULT_SETTINGS = {
    "gears": [40, 80, 120, 160, 200],
    "thresholds": {
        "tor": {"warn": 0.96, "crit": 2.0},
        "tmp": {"warn": 60, "crit": 75}
    }
}
# ──────────────────────────────────────────────────────────────────────────────

udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_sock.bind(("0.0.0.0", UDP_FB_PORT))
udp_sock.settimeout(0.1)

connected_clients = set()


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
    """HTTP handler for settings file I/O."""
    
    def do_GET(self):
        """Handle GET requests for loading settings."""
        if self.path == "/api/settings":
            settings = load_settings()
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(settings).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
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
                    self.send_header("Access-Control-Allow-Origin", "*")
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def run_http_server():
    """Run HTTP server for settings I/O in a separate thread."""
    server = HTTPServer(("0.0.0.0", HTTP_PORT), SettingsHTTPHandler)
    print(f"[HTTP] Settings server running on http://0.0.0.0:{HTTP_PORT}")
    server.serve_forever()


def udp_feedback_thread(loop):
    """Background thread: read UDP feedback from Pi, forward to all WS clients."""
    while True:
        try:
            data, _ = udp_sock.recvfrom(256)
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
                    sid   = int(obj.get("id", 1))
                    angle = int(obj.get("angle", 90))
                    import json as _json
                    payload = bytes([0xAA]) + _json.dumps({"cmd":"servo","id":sid,"angle":angle}).encode()
                    udp_sock.sendto(payload, (PI_IP, 3391))  # UDPS.py listens on 3391

            except Exception as e:
                print(f"[WS] Error handling message: {e}")
    finally:
        connected_clients.discard(websocket)
        print(f"[WS] Client disconnected: {websocket.remote_address}")


async def main():
    loop = asyncio.get_event_loop()
    
    # Start HTTP server in background thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # Start UDP feedback thread
    t = threading.Thread(target=udp_feedback_thread, args=(loop,), daemon=True)
    t.start()

    print(f"[WS] Server running on ws://{WS_HOST}:{WS_PORT}")
    print(f"[WS] Forwarding UDP to {PI_IP}:{UDP_CMD_PORT}")
    print(f"[WS] Listening for UDP feedback on port {UDP_FB_PORT}")
    print(f"[Settings] File-based storage enabled: {SETTINGS_FILE}")
    print(f"[Settings] Open control.html in your browser to start.\n")

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
