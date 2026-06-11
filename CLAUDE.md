# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a multi-board robot control stack: a browser UI talks to a Windows PC bridge over WebSocket, which forwards commands over UDP to a Raspberry Pi, which talks over Serial to microcontrollers (ESP32 for tracked drive, Arduino Mega/Teensy for arm servos and an "avatar" teleop arm).

## Running it

```
pip install websockets pyserial
python robot_control/ws_server.py
```
Then open `http://localhost:8766/control.html` in a browser (the HTTP server on port 8766 also serves static files and the `/api/settings` JSON API; the WebSocket server runs on port 8765).

There is no build step, linter, or test suite — this is plain HTML/JS/Python/Arduino code edited directly.

## Architecture / data flow

```
Browser (control.html, embeds index.html in an <iframe> for the 3D sim)
    |  WebSocket ws://<pc>:8765
    v
ws_server.py  (Windows PC) — also serves static files + settings API on :8766
    |  UDP struct.pack('ff', linear, angular)        -> PI_IP:3390  (drive)
    |  UDP struct.pack('Bb', 0xFF, state)             -> PI_IP:3390  (motor lock)
    |  UDP 0xAA + JSON {"cmd":"servo","id":N,"angle":A} -> PI_IP:3391 (servo)
    v
Raspberry Pi
    |  raspberry_pi/UDP.py  (esp32_bridge) — drive/lock packets, port 3390 <-> ESP32 over Serial
    |  raspberry_pi/UDPS.py (servo bridge) — port 3391, JSON marker 0xAA <-> Arduino Mega over Serial
    v
ESP32 (lowlevelcode/ddsmmotor)        Arduino Mega / Teensy (lowlevelcode/teensyCOde, teensytestcode)
    |  UART JSON {"T":1,"l":..,"r":..} drives DDSM115 track motors
    |  feedback {"T":3,...} (tor/spd/temp/err) flows back PC<-3391<-3390 path
```

Key points:
- **`ws_server.py`** is the central hub. It is the only piece of code that knows `PI_IP` (the Raspberry Pi's LAN address) — must be edited before deployment.
- **Settings persistence**: `robot_control/settings.json` is the single source of truth for gears, torque/temp thresholds, posture angle presets, servo calibration (`simCal`/`simInit`), and gamepad/controller button mappings. Read/written via `GET/POST /api/settings` (`load_settings`/`save_settings` in `ws_server.py`). `control.html` and `index.html` both fetch this on load and merge with in-code defaults (`mergeControllerSettings`, `loadPostures`, etc.) — never break backward compatibility of this schema without checking both consumers.
- **`control.html`** is the main UI: keyboard/gamepad/avatar drive controls, servo sliders (`SERVOS` array, ids 1-8), posture presets, gear selection, and the parameters/settings panel. It embeds `index.html` via `<iframe id="sim-iframe">` and pushes joint state to it via `postMessage({type:'state', angles, cal})`. Its JavaScript is split into plain `<script src>` files under `robot_control/js/` loaded in order: `state.js` (shared globals/config — loads first), `websocket.js` (connection + `log()`), `drive.js` (motor/gear/WASD/telemetry), `arm.js` (servos + postures), `controller.js` (gamepad), `settings.js` (localStorage + `/api/settings` params modal), `main.js` (3D-sim glue + mode switching + boot — loads last). These are **classic scripts sharing one global scope** (not ES modules) because the markup uses inline `onclick=` handlers and the code relies on shared globals; load order matters and top-level `let`/`const` must be declared exactly once across the set.
- **`index.html`** is the Three.js 3D simulation (STL models in `models/`). It maps an 8-element angle array to joint keys via `IDX_TO_KEY = ['front','back','arm1','arm2','arm3','arm4','arm5','gripper']` — this ordering must stay in sync with `SERVOS`/`simInit`/`simCal` in `control.html`/`settings.json`.
- **Avatar mode**: `AvatarBridge` in `ws_server.py` (ported from `testcode2/avatar.py`) reads a physical teleop arm (Teensy over USB serial, 3 potentiometers) and drives servos 3/4/5 (shoulder/elbow/extend) by sending the same `cmd:"servo"` UDP packets, echoed back to the UI so sliders/sim track the physical arm.
- **Packet protocols** (must stay consistent across `ws_server.py`, `raspberry_pi/UDP.py`, `raspberry_pi/UDPS.py`, and the `.ino` sketches):
  - Motion: 8-byte `struct.pack('ff', linear, angular)` on port 3390.
  - Lock: 2-byte `struct.pack('Bb', 0xFF, state)` on port 3390.
  - Servo: `0xAA` + JSON `{"cmd":"servo","id":1-8,"angle":0-180}` on port 3391.
  - ESP32 feedback: JSON `{"T":3,...}` forwarded as-is from serial to PC port 3391.
  - Mega angle readback: lines starting `ANGLES:`/`DATA:` forwarded from serial to PC port 3392.

## Other directories

- `lowlevelcode/` — Arduino/Teensy `.ino` sketches for the drive ESP32 (`ddsmmotor`) and arm controller (`teensyCOde`, `teensytestcode`).
- `rescue_old/` — older standalone experiments (camera detection, QR logging); not part of the active stack.
- `testcode/`, `testcode2/` — earlier prototypes/standalone versions of the sim and avatar bridge; `ws_server.py`'s avatar logic is the actively maintained successor of `testcode2/avatar.py`.
