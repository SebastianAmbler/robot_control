# Robot Control UI - Setup

## Files
- `control.html`  — open in any browser on the PC
- `ws_server.py`  — WebSocket server (run on the PC)

---

## Setup

### 1. Install Python dependency (once)
```
pip install websockets
```

### 2. Set your Pi's IP address
Open `ws_server.py` and change line:
```python
PI_IP = "192.168.1.100"
```
to your Raspberry Pi's actual IP on the LAN.

### 3. Make sure esp32_bridge.py is running on the Pi
```
python3 esp32_bridge.py
```

### 4. Run the WebSocket server on the PC
```
python ws_server.py
```

### 5. Open control.html in your browser
- Type `ws://localhost:8765` in the URL field (already pre-filled)
- Click **Connect**

---

## Controls

| Key      | Action                      |
|----------|-----------------------------|
| W        | Forward                     |
| S        | Reverse                     |
| A        | Turn left                   |
| D        | Turn right                  |
| W+A      | Forward-left (diagonal)     |
| W+D      | Forward-right (diagonal)    |
| S+A      | Reverse-left (diagonal)     |
| S+D      | Reverse-right (diagonal)    |
| G        | Cycle gear up (1-5, wraps)  |
| L        | Toggle motor lock           |

## Gears
| Gear | Max RPM |
|------|---------|
| 1    | 40      |
| 2    | 80      |
| 3    | 120     |
| 4    | 160     |
| 5    | 200     |

## Camera feeds
The two camera slots in the center are placeholders.
To add live feeds, replace the `.cam-feed` divs in `control.html`
with `<img>` or `<video>` elements pointing to your camera stream URLs
(e.g. MJPEG streams, WebRTC, or ROS web_video_server).

---

## Architecture
```
Browser (control.html)
    |  WebSocket ws://localhost:8765
    v
ws_server.py  (PC)
    |  UDP struct.pack('ff', linear, angular)  -> port 3390
    |  UDP struct.pack('Bb', 0xFF, state)      -> port 3390
    |  UDP JSON feedback                       <- port 3391
    v
Raspberry Pi (esp32_bridge.py)
    |  Serial JSON
    v
ESP32 (robot_drive.ino)
    |  UART
    v
DDSM115 motors
```
