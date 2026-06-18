import serial
import time

ser = serial.Serial('COM13', baudrate=115200, timeout=0.1)

def set_speed(motor_id, rpm):
    rpm = max(-1000, min(1000, rpm))
    high = (rpm >> 8) & 0xFF
    low  = rpm & 0xFF
    packet = bytes([
        motor_id,
        0x64,
        high,
        low,
        0x00, 0x00, 0x00, 0x00, 0x00,
        (motor_id + 0x64 + high + low) & 0xFF
    ])
    ser.write(packet)
    ser.flush()
    print("Sent:", packet.hex(' '))

# Try broadcast ID with higher RPM
set_speed(0x00, 500)
time.sleep(3)
set_speed(0x00, 0)
ser.close()