#!/usr/bin/env python3
"""
AMG8833 thermal viewer (PC side)

Reads the 64 comma-separated values streamed by the Teensy and shows
a live, bicubic-interpolated thermal image.

Setup:
    pip install pyserial numpy matplotlib

Before running:
    - Close the Arduino IDE Serial Monitor (the port can only be open once).
    - Set PORT below to your Teensy's port.
        Windows : "COM3"  (check Arduino IDE > Tools > Port, or Device Manager)
        macOS   : "/dev/cu.usbmodem12345"
        Linux   : "/dev/ttyACM0"
"""

import serial
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# ---------------- config ----------------
PORT = "COM4"          # <-- change to your port
BAUD = 115200

MIN_TEMP = 25       # color scale lower bound (deg C)
MAX_TEMP = 35      # color scale upper bound (deg C)
COLORMAP = "inferno"   # try: inferno, jet, plasma, turbo

# Set these if the image comes out mirrored/upside-down for your mounting.
FLIP_HORIZONTAL = False
FLIP_VERTICAL = False
# ----------------------------------------

ser = serial.Serial(PORT, BAUD, timeout=1)

fig, ax = plt.subplots()
img = ax.imshow(
    np.zeros((8, 8)),
    cmap=COLORMAP,
    interpolation="bicubic",   # <-- the upscaling happens here, for free
    vmin=MIN_TEMP,
    vmax=MAX_TEMP,
)
plt.colorbar(img, label="Temperature (°C)")
ax.set_title("AMG8833 Thermal Camera")
ax.axis("off")


def update(_frame):
    line = ser.readline().decode("utf-8", errors="ignore").strip()
    if not line:
        return (img,)

    parts = line.split(",")
    if len(parts) != 64:
        return (img,)   # skip partial/garbled lines

    try:
        data = np.array(parts, dtype=float).reshape(8, 8)
    except ValueError:
        return (img,)

    if FLIP_HORIZONTAL:
        data = np.fliplr(data)
    if FLIP_VERTICAL:
        data = np.flipud(data)

    img.set_data(data)
    return (img,)


ani = FuncAnimation(fig, update, interval=50, blit=True, cache_frame_data=False)
plt.show()

ser.close()