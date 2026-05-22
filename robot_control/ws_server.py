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

# ─── Config ───────────────────────────────────────────────────────────────────
PI_IP         = "192.168.1.101"   # <-- SET THIS to your Pi's IP address
UDP_CMD_PORT  = 3390              # port esp32_bridge listens on
UDP_FB_PORT   = 3391              # port esp32_bridge sends feedback to

WS_HOST       = "0.0.0.0"
WS_PORT       = 8765
# ──────────────────────────────────────────────────────────────────────────────

udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_sock.bind(("0.0.0.0", UDP_FB_PORT))
udp_sock.settimeout(0.1)

connected_clients = set()


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

            except Exception as e:
                print(f"[WS] Error handling message: {e}")
    finally:
        connected_clients.discard(websocket)
        print(f"[WS] Client disconnected: {websocket.remote_address}")


async def main():
    loop = asyncio.get_event_loop()
    t = threading.Thread(target=udp_feedback_thread, args=(loop,), daemon=True)
    t.start()

    print(f"[WS] Server running on ws://{WS_HOST}:{WS_PORT}")
    print(f"[WS] Forwarding UDP to {PI_IP}:{UDP_CMD_PORT}")
    print(f"[WS] Listening for UDP feedback on port {UDP_FB_PORT}")
    print(f"[WS] Open control.html in your browser to start.\n")

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
