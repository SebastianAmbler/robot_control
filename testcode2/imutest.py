"""
Reader for WitMotion BWT901CL IMU sensor.

The sensor (in its default UART output mode) streams 11-byte packets:
    0x55, <type>, d0, d1, d2, d3, d4, d5, d6, d7, <checksum>

Packet types:
    0x51 - Time
    0x52 - Acceleration
    0x53 - Angular velocity
    0x54 - Angle (roll/pitch/yaw)
    0x55 - Magnetic field
    0x56 - Port status
    0x57 - Pressure / altitude
    0x58 - GPS lon/lat
    0x59 - GPS yaw / speed
    0x5A - Quaternion
    0x5B - GPS accuracy

Each data field is a signed 16-bit little-endian value that must be
scaled to physical units as shown below.
"""

import serial
import time

PORT = "/dev/ttyUSB0"
BAUDRATE = 115200

PACKET_HEADER = 0x55
PACKET_LEN = 11


def to_signed_short(low, high):
    value = (high << 8) | low
    if value >= 32768:
        value -= 65536
    return value


def parse_packet(packet):
    """Parse an 11-byte packet and return (type, dict of values) or None."""
    if len(packet) != PACKET_LEN:
        return None
    if packet[0] != PACKET_HEADER:
        return None

    checksum = sum(packet[0:10]) & 0xFF
    if checksum != packet[10]:
        return None

    ptype = packet[1]
    data = packet[2:10]

    if ptype == 0x51:  # Time
        return ptype, {
            "year": data[0],
            "month": data[1],
            "day": data[2],
            "hour": data[3],
            "minute": data[4],
            "second": data[5],
            "millisecond": to_signed_short(data[6], data[7]),
        }

    if ptype == 0x52:  # Acceleration (g)
        ax = to_signed_short(data[0], data[1]) / 32768.0 * 16
        ay = to_signed_short(data[2], data[3]) / 32768.0 * 16
        az = to_signed_short(data[4], data[5]) / 32768.0 * 16
        temp = to_signed_short(data[6], data[7]) / 100.0
        return ptype, {"ax": ax, "ay": ay, "az": az, "temp": temp}

    if ptype == 0x53:  # Angular velocity (deg/s)
        gx = to_signed_short(data[0], data[1]) / 32768.0 * 2000
        gy = to_signed_short(data[2], data[3]) / 32768.0 * 2000
        gz = to_signed_short(data[4], data[5]) / 32768.0 * 2000
        temp = to_signed_short(data[6], data[7]) / 100.0
        return ptype, {"gx": gx, "gy": gy, "gz": gz, "temp": temp}

    if ptype == 0x54:  # Angle (deg)
        roll = to_signed_short(data[0], data[1]) / 32768.0 * 180
        pitch = to_signed_short(data[2], data[3]) / 32768.0 * 180
        yaw = to_signed_short(data[4], data[5]) / 32768.0 * 180
        temp = to_signed_short(data[6], data[7]) / 100.0
        return ptype, {"roll": roll, "pitch": pitch, "yaw": yaw, "temp": temp}

    if ptype == 0x55:  # Magnetic field (raw)
        mx = to_signed_short(data[0], data[1])
        my = to_signed_short(data[2], data[3])
        mz = to_signed_short(data[4], data[5])
        return ptype, {"mx": mx, "my": my, "mz": mz}

    if ptype == 0x59:  # Quaternion
        q0 = to_signed_short(data[0], data[1]) / 32768.0
        q1 = to_signed_short(data[2], data[3]) / 32768.0
        q2 = to_signed_short(data[4], data[5]) / 32768.0
        q3 = to_signed_short(data[6], data[7]) / 32768.0
        return ptype, {"q0": q0, "q1": q1, "q2": q2, "q3": q3}

    return ptype, {"raw": list(data)}


# --- Configuration commands ---------------------------------------------
#
# The sensor accepts 5-byte configuration commands of the form:
#     0xFF, 0xAA, <register>, <data_low>, <data_high>
#
# Registers used below:
#   0x69 KEY     - unlock register writes (value 0xB588)
#   0x01 CALSW   - calibration switch
#                     0x00 = normal output
#                     0x01 = accelerometer (gravity) calibration
#                     0x04 = heading (yaw) reset to zero
#   0x00 SAVE    - save current configuration to flash (value 0x00)

def _send_command(ser, register, value=0x0000):
    cmd = bytes([0xFF, 0xAA, register, value & 0xFF, (value >> 8) & 0xFF])
    ser.write(cmd)
    time.sleep(0.1)


def unlock(ser):
    """Unlock the sensor's configuration registers for writing."""
    _send_command(ser, 0x69, 0xB588)


def save_config(ser):
    """Persist the current configuration to flash."""
    unlock(ser)
    _send_command(ser, 0x00, 0x00)


def calibrate_accel(ser, duration=5):
    """
    Run the accelerometer (zero-g) calibration.

    Keep the sensor flat and perfectly still for the whole duration.
    """
    unlock(ser)
    _send_command(ser, 0x01, 0x01)  # enter accel calibration
    time.sleep(duration)
    unlock(ser)
    _send_command(ser, 0x01, 0x00)  # back to normal output
    save_config(ser)


def reset_yaw(ser):
    """Set the current heading as the new yaw = 0 reference."""
    unlock(ser)
    _send_command(ser, 0x01, 0x04)
    save_config(ser)


def read_imu(port=PORT, baudrate=BAUDRATE):
    ser = serial.Serial(port, baudrate, timeout=1)

    try:
        while True:
            byte = ser.read(1)
            if not byte:
                continue
            if byte[0] != PACKET_HEADER:
                continue

            packet = byte + ser.read(PACKET_LEN - 1)
            if len(packet) != PACKET_LEN:
                continue

            result = parse_packet(packet)
            if result is None:
                continue

            ptype, values = result

            if ptype == 0x52:
                print(
                    "Accel: ax={ax:.3f}g ay={ay:.3f}g az={az:.3f}g temp={temp:.1f}C".format(
                        **values
                    )
                )
            elif ptype == 0x53:
                print(
                    "Gyro:  gx={gx:.2f}deg/s gy={gy:.2f}deg/s gz={gz:.2f}deg/s".format(
                        **values
                    )
                )
            elif ptype == 0x54:
                print(
                    "Angle: roll={roll:.2f} pitch={pitch:.2f} yaw={yaw:.2f}".format(
                        **values
                    )
                )
            elif ptype == 0x55:
                print(
                    "Mag:   mx={mx} my={my} mz={mz}".format(**values)
                )

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "calibrate-accel":
        port = serial.Serial(PORT, BAUDRATE, timeout=1)
        print("Keep the sensor flat and still...")
        calibrate_accel(port)
        print("Accelerometer calibration saved.")
        port.close()
    elif len(sys.argv) > 1 and sys.argv[1] == "reset-yaw":
        port = serial.Serial(PORT, BAUDRATE, timeout=1)
        reset_yaw(port)
        print("Yaw reset to 0 and saved.")
        port.close()
    else:
        read_imu()
