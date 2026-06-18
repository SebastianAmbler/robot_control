import socket
import cv2
import numpy as np
import struct
import threading
import queue
from ultralytics import YOLO

# ═══════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════
LISTEN_IP   = "0.0.0.0"
LISTEN_PORT = 5000
MODEL_PATH  = "A5.pt"

# Create a thread-safe Queue to pass images from the network to the UI
image_queue = queue.Queue(maxsize=10)
# ═══════════════════════════════════════════════════════════════

def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Pi disconnected mid-transfer")
        buf += chunk
    return buf

def receive_snapshot(conn):
    payload_size = struct.calcsize(">L")
    msg_size     = struct.unpack(">L", recv_exact(conn, payload_size))[0]
    frame_data   = recv_exact(conn, msg_size)
    arr          = np.frombuffer(frame_data, dtype=np.uint8)
    frame        = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Failed to decode received JPEG")
    return frame

# --- THREAD 1: The Network Listener ---
def network_thread_func():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((LISTEN_IP, LISTEN_PORT))
        server.listen(5)
        print(f"[TCP] Waiting for snapshots on port {LISTEN_PORT}…")
    except Exception as e:
        print(f"\n[FATAL ERROR] Cannot bind to port! Is your old script still running?\nDetails: {e}")
        return

    while True:
        try:
            # This will block and wait for the Pi, but it WON'T freeze the GUI
            conn, addr = server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(10.0)
            print(f"[TCP] Snapshot from {addr[0]}…")
            
            frame = receive_snapshot(conn)
            
            # Put the frame in the queue (drop the oldest if the queue is full)
            if image_queue.full():
                image_queue.get() 
            image_queue.put(frame)
            
        except Exception as e:
            print(f"[ERROR] Network issue: {e}")
        finally:
            if 'conn' in locals():
                conn.close()

# --- THREAD 2: The Main UI & AI Loop ---
def main():
    print(f"[INFO] Loading YOLO model: {MODEL_PATH}")
    try:
        model = YOLO(MODEL_PATH)
        print("[INFO] Model loaded.")
    except Exception as e:
        print(f"[FATAL ERROR] YOLO failed to load: {e}")
        input("Press Enter to exit...") # Stops the window from instantly vanishing
        return

    # Start the network listener in the background
    net_thread = threading.Thread(target=network_thread_func, daemon=True)
    net_thread.start()

    # Setup the OpenCV Window
    cv2.namedWindow("Robot Snapshot", cv2.WINDOW_NORMAL)
    count = 0
    print("[INFO] GUI Ready. Waiting for images... (Press 'q' to quit)")

    try:
        while True:
            # 1. Check if there is a new image from the background network thread
            try:
                frame = image_queue.get_nowait()
                
                # We got a new image! Process AI.
                count += 1
                results = model(frame, verbose=False, conf=0.1)
                display_frame = results[0].plot()
                dets = len(results[0].boxes)
                
                print(f"[YOLO] Snapshot #{count}: {dets} detection(s)")
                
                # Update the window
                cv2.imshow("Robot Snapshot", display_frame)
                cv2.setWindowTitle("Robot Snapshot", f"Snapshot #{count} — {dets} detections [Press 'q' to quit]")
                
            except queue.Empty:
                # No new image in the box. Just pass and keep the GUI alive.
                pass

            # 2. The OpenCV heartbeat - Keeps the window responsive and checks for 'q'
            # (If no image has arrived yet, this just loops smoothly)
            if cv2.waitKey(10) & 0xFF == ord('q'):
                print("[INFO] 'q' pressed. Quitting...")
                break

    except KeyboardInterrupt:
        print("\n[INFO] Shutting down.")
    finally:
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()