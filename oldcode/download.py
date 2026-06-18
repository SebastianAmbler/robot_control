import os
import urllib.request

def _ensure_wechat_qr_models():
    """Downloads the 4 required model files for WeChatQRCode if they don't exist."""
    base_url = "https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/"
    files = ["detect.prototxt", "detect.caffemodel", "sr.prototxt", "sr.caffemodel"]
    
    for f in files:
        if not os.path.exists(f):
            print(f"[QR] Downloading {f}...")
            urllib.request.urlretrieve(base_url + f, f)

# Call this right before you load your YOLO models
_ensure_wechat_qr_models()