# Standalone avatar-arm bridge + calibration tool.
# NOTE: this logic is also embedded in robot_control/ws_server.py (AvatarBridge,
# parse_line, to_angle, find_avatar_teensy) so the web UI's "Avatar" mode can use
# it. Keep the two in sync when editing the pot→servo mapping or CALIB table.
import serial
import serial.tools.list_ports
import socket
import json
import time
import sys


T1_PORT = "COM3"             
PI_IP   = "192.168.1.101"   
PI_PORT = 3391          
BAUD         = 115200
SERVO_MARKER = 0xAA

SERVO_MIN = [0,   30,  40,  30,  20,  70,  80,  35]
SERVO_MAX = [180,150, 165, 180, 180, 150, 180, 140]

# ── 3 channel ตาม ขา 3,4,5 ───────────────────────────
CALIB = [
    dict(pot=0, sid=3, rev=False, in0=0, in1=180, out0=40,  out1=165, name='Arm1 shoulder'),
    dict(pot=1, sid=4, rev=True, in0=122, in1=180, out0=30, out1=150, name='Arm2 elbow'),  
    dict(pot=2, sid=5, rev=False, in0=0, in1=180, out0=20,  out1=180, name='Arm3 wrist'),
]

DEADBAND    = 2
NUM_POTS    = len(CALIB)
TEENSY_PINS = [3, 4, 5]   

# ══════════════════════════════════════════════════════
#  List / find ports
# ══════════════════════════════════════════════════════
def find_teensy():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = ((p.description or '') + (p.manufacturer or '')).lower()
        if any(kw in desc for kw in ['teensy', 'usb serial', 'arduino']):
            return p.device
    return ports[0].device if len(ports) == 1 else None

def list_ports():
    print("─" * 50)
    print("Serial ports ที่พบ:")
    for p in serial.tools.list_ports.comports():
        print(f"  {p.device:8s} — {p.description}")
    print("─" * 50)
    print(f"T1_PORT = {T1_PORT}  (Teensy จำลอง)")
    print(f"PI_IP   = {PI_IP}:{PI_PORT}  (Pi UDPS.py)")
    print("─" * 50)
    print()


def parse_line(raw):
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
    if len(parts) >= NUM_POTS:
        try:
            vals = [int(p.strip()) for p in parts[:NUM_POTS]]
            if all(0 <= v <= 180 for v in vals):
                return vals
        except Exception:
            pass

    return None

# ══════════════════════════════════════════════════════
#  Map pot → servo angle
# ══════════════════════════════════════════════════════
def to_angle(val, c):
    ratio = (val - c['in0']) / max(1, c['in1'] - c['in0'])
    ratio = max(0.0, min(1.0, ratio))
    if c['rev']:
        ratio = 1.0 - ratio
    angle = c['out0'] + ratio * (c['out1'] - c['out0'])
    idx   = c['sid'] - 1
    return max(SERVO_MIN[idx], min(SERVO_MAX[idx], int(angle)))

# ══════════════════════════════════════════════════════
#  Send UDP → Pi → UDPS.py → Teensy หุ่นยนต์ → Servo
# ══════════════════════════════════════════════════════
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_servo(sid, angle):
    payload = {"cmd": "servo", "id": sid, "angle": angle}
    data    = bytes([SERVO_MARKER]) + json.dumps(payload).encode()
    sock.sendto(data, (PI_IP, PI_PORT))

# ══════════════════════════════════════════════════════
#  Calibration mode   python test.py --calib
# ══════════════════════════════════════════════════════
def calib_mode(t1):
    print("\n=== CALIBRATION MODE ===")
    print("ขยับ pot แต่ละตัว ดูค่า raw และองศาที่ map ได้")
    print("กด Ctrl+C เพื่อออก\n")
    while True:
        try:
            raw = t1.readline().decode(errors='ignore').strip()
            if not raw:
                continue
            vals = parse_line(raw)
            if vals is None:
                continue
            angles = [to_angle(vals[i], CALIB[i]) for i in range(NUM_POTS)]
            parts  = [f"P{i}={vals[i]:3d}→{angles[i]:3d}°" for i in range(NUM_POTS)]
            print("  ".join(parts))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERR] {e}")
    print("\nออกจาก calibration mode")

# ══════════════════════════════════════════════════════
#  Bridge
#  Teensy จำลอง → parse PWM → map องศา → UDP → Pi
#  Pi (UDPS.py) → Serial → Teensy หุ่นยนต์ → Servo
# ══════════════════════════════════════════════════════
def bridge(t1):
    prev = {}
    print(f"[OK] Bridge running")
    print(f"     {T1_PORT} → UDP → {PI_IP}:{PI_PORT} → Teensy หุ่นยนต์")
    print(f"     Ctrl+C เพื่อหยุด\n")

    while True:
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
                if sid not in prev or abs(angle - prev[sid]) > DEADBAND:
                    send_servo(sid, angle)
                    print(f"P{i}={vals[i]:3d} → {c['name']:14s} id{sid}: {angle:3d}°  →Pi")
                    prev[sid] = angle

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERR] {e}")
            time.sleep(0.5)

# ══════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════
def main():
    list_ports()
    port = find_teensy()
    
    # Optional fallback: If it can't find it automatically, use the hardcoded COM3
    if not port:
        print(f"[WARN] ไม่เจอ Teensy อัตโนมัติ! กำลังลองเชื่อมต่อกับ {T1_PORT}")
        port = T1_PORT
    else:
        print(f"[AUTO] พบ Teensy ที่: {port}\n")

    try:
        t1 = serial.Serial(port, BAUD, timeout=2)
    except serial.SerialException as e:
        print(f"[ERR] {e}")
        print(f"      แก้ T1_PORT = 'COMx' บรรทัดที่ 9")
        sys.exit(1)

    time.sleep(2.0)
    print(f"[OK] Teensy จำลอง : {port}")
    print(f"[OK] Pi target    : {PI_IP}:{PI_PORT}\n")

    try:
        if '--calib' in sys.argv:
            calib_mode(t1)
        else:
            bridge(t1)
    except KeyboardInterrupt:
        pass
    finally:
        t1.close()
        sock.close()
        print("หยุดแล้ว")

if __name__ == '__main__':
    main()