from flask import Flask, jsonify, send_file
import cv2
import time
import subprocess
import traceback
from urllib.parse import quote
import os

app = Flask(__name__)

USER = "admin"
PASSWORD = "admin"
IP = "192.168.0.100"
PATH = "live"
FFMPEG_PATH = r"C:\Users\TEERASAK\ffmpeg-8.1-essentials_build\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
SNAPSHOT_PATH = "qr_snapshot.jpg"
ANNOTATED_PATH = "qr_annotated.jpg"

pw = quote(PASSWORD)
RTSP_URL = f"rtsp://{USER}:{pw}@{IP}:554/{PATH}"

last_qr = ""
last_time = 0
DEBOUNCE_SEC = 3


def capture_snapshot():
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-rtsp_transport", "tcp",
        "-i", RTSP_URL,
        "-frames:v", "1",
        "-q:v", "2",
        SNAPSHOT_PATH
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr
    }


def draw_qr_box_and_text(img, points, qr_text):
    if points is None:
        return img

    pts = points.astype(int).reshape(-1, 2)

    if len(pts) >= 4:
        for i in range(len(pts)):
            p1 = tuple(pts[i])
            p2 = tuple(pts[(i + 1) % len(pts)])
            cv2.line(img, p1, p2, (0, 255, 0), 3)

        x, y = pts[0]
        label = f"QR: {qr_text}" if qr_text else "QR FOUND"
        text_y = max(30, y - 10)

        cv2.putText(
            img,
            label,
            (x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )

    return img


def read_qr_from_image():
    if not os.path.exists(SNAPSHOT_PATH):
        return {"ok": False, "error": "snapshot_not_found"}

    img = cv2.imread(SNAPSHOT_PATH)
    if img is None:
        return {"ok": False, "error": "cannot_read_snapshot"}

    detector = cv2.QRCodeDetector()

    # ปรับขนาดเพื่อให้ detect ง่ายขึ้น
    img = cv2.resize(img, (640, 480))
    annotated = img.copy()

    data = ""
    points = None

    try:
        # ลองแบบหลาย QR ก่อน
        retval, decoded_info, points_multi, _ = detector.detectAndDecodeMulti(img)
        if retval and points_multi is not None and len(points_multi) > 0:
            texts = []
            for qr_text, pts in zip(decoded_info, points_multi):
                qr_text = qr_text or ""
                annotated = draw_qr_box_and_text(annotated, pts, qr_text)
                if qr_text:
                    texts.append(qr_text)

            cv2.imwrite(ANNOTATED_PATH, annotated)

            return {
                "ok": True,
                "found": len(texts) > 0,
                "qr": texts[0] if texts else "",
                "all_qr": texts,
                "image_path": ANNOTATED_PATH
            }
    except Exception:
        pass

    # fallback: single QR
    data, points, _ = detector.detectAndDecode(img)

    if points is not None and len(points) > 0:
        annotated = draw_qr_box_and_text(annotated, points, data)
    else:
        cv2.putText(
            annotated,
            "NO QR",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 180, 255),
            2,
            cv2.LINE_AA
        )

    cv2.imwrite(ANNOTATED_PATH, annotated)

    return {
        "ok": True,
        "found": bool(data),
        "qr": data if data else "",
        "all_qr": [data] if data else [],
        "image_path": ANNOTATED_PATH
    }


@app.route("/qr", methods=["POST"])
def run_qr():
    global last_qr, last_time

    try:
        snap = capture_snapshot()
        if not snap["ok"]:
            return jsonify({
                "ok": False,
                "error": "snapshot_failed",
                "stderr": snap["stderr"][-1000:]
            }), 200

        result = read_qr_from_image()
        if not result["ok"]:
            return jsonify(result), 200

        if result["found"]:
            now = time.time()
            qr = result["qr"]
            duplicated = (qr == last_qr and (now - last_time) < DEBOUNCE_SEC)

            if not duplicated:
                last_qr = qr
                last_time = now

            return jsonify({
                "ok": True,
                "found": True,
                "qr": qr,
                "all_qr": result.get("all_qr", []),
                "duplicated": duplicated,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "image_url": "/qr/image"
            }), 200

        return jsonify({
            "ok": True,
            "found": False,
            "qr": "",
            "all_qr": [],
            "duplicated": False,
            "image_url": "/qr/image"
        }), 200

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }), 200


@app.route("/qr/image", methods=["GET"])
def get_qr_image():
    if not os.path.exists(ANNOTATED_PATH):
        return jsonify({"ok": False, "error": "annotated_image_not_found"}), 404
    return send_file(ANNOTATED_PATH, mimetype="image/jpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)