from flask import Flask, jsonify, Response, render_template_string, request, make_response
import csv
from flask_cors import CORS
import cv2
import base64
import time
import threading
import numpy as np

# ── YOLO11 ──
try:
    from ultralytics import YOLO as _YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

app = Flask(__name__)
CORS(app)

@app.after_request
def allow_iframe(response):
    response.headers.pop("X-Frame-Options", None)
    response.headers["X-Frame-Options"]       = "ALLOWALL"
    response.headers["Content-Security-Policy"] = "frame-ancestors *"
    return response

# =========================================================
# CONFIG
# =========================================================
RTSP_URL = "rtsp://192.168.0.138:8554/unicast"

VIEW_WIDTH = 640
VIEW_HEIGHT = 480

# ===== motion config =====
WHITE_V_MIN          = 160
WHITE_S_MAX          = 60
WHITE_AREA_MIN       = 800
WHITE_AREA_MAX       = 80000
WHITE_SOLIDITY       = 0.65
BLACK_IN_WHITE_V_MAX = 130
BLACK_IN_WHITE_S_MAX = 100
BLACK_DOT_AREA_MIN   = 10
BLACK_DOT_AREA_MAX   = 5000

# ===== target lock config =====
LOCK_MAX_DIST = 35.0
LOCK_LOST_LIMIT = 8
CENTER_X_TOL = 160
CENTER_AREA_WEIGHT = 0.002
LOCK_CENTER_WEIGHT = 0.25

# ===== qr config =====
DEBOUNCE_SEC = 2
QR_LOG_MAX = 20

# ===== csv config =====
CSV_FILE = "qr_log.csv"

# ===== AI config =====
AI_MODEL_PATH = "X7.pt"
AI_CONF   = 0.35
AI_IOU    = 0.45
AI_IMGSZ  = 640
AI_TASK   = "detect"

# ===== AI CSV config =====
AI_CSV_FILE  = "ai_detection_log.csv"
AI_LOG_MAX   = 500

# =========================================================
# GLOBAL STATE
# =========================================================
latest_raw_frame = None
latest_display_frame = None
frame_lock = threading.Lock()
new_frame_event = threading.Event()

camera_ok = False
detect_mode = "motion"

last_qr = ""
last_qr_time = 0
latest_qr_found = False
latest_qr_text = ""
latest_qr_all = []

qr_read_log = []

motion_found = False
motion_center = None
motion_box = None

locked_target = None
lock_lost_count = 0

# ── AI state ──
yolo_model        = None
yolo_model_loaded = False
yolo_model_error  = ""
yolo_conf         = AI_CONF
yolo_iou          = AI_IOU
yolo_imgsz        = AI_IMGSZ
yolo_latest_dets  = []
yolo_fps          = 0.0
yolo_log          = []
yolo_full_log     = []
yolo_lock         = threading.Lock()

# =========================================================
# AI HELPERS
# =========================================================
AI_COLORS = [
    (57,255,20),(0,200,255),(255,80,80),(255,165,0),(180,0,255),
    (0,255,180),(255,255,0),(255,20,147),(0,128,255),(128,255,0),
]

def load_yolo_model(path):
    global yolo_model, yolo_model_loaded, yolo_model_error
    if not YOLO_AVAILABLE:
        yolo_model_error = "ultralytics ไม่ได้ติดตั้ง (pip install ultralytics)"
        return False
    try:
        yolo_model        = _YOLO(path)
        yolo_model_loaded = True
        yolo_model_error  = ""
        log(f"[AI] โหลด model สำเร็จ: {path} | classes={len(yolo_model.names)}")
        return True
    except Exception as e:
        yolo_model_loaded = False
        yolo_model_error  = str(e)
        log(f"[AI] โหลดล้มเหลว: {e}")
        return False

def run_yolo_on_frame(frame):
    global yolo_latest_dets, yolo_fps, yolo_log, yolo_full_log
    img = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))
    if not yolo_model_loaded or yolo_model is None:
        cv2.putText(img, "AI: model ยังไม่โหลด", (10,30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,80,255), 2)
        return img, []
    t0 = time.perf_counter()
    try:
        results = yolo_model.predict(img, conf=yolo_conf, iou=yolo_iou,
                                     imgsz=yolo_imgsz, verbose=False)
    except Exception as e:
        annotated = img.copy()
        cv2.putText(annotated, f"AI error: {e}", (10,30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,80,255), 2)
        return annotated, []
    fps_val   = 1.0 / max(time.perf_counter() - t0, 1e-6)
    annotated = img.copy()
    dets = []
    r = results[0]
    if r.boxes is not None:
        for box in r.boxes:
            cls_id   = int(box.cls[0])
            cls_name = yolo_model.names[cls_id]
            conf_val = float(box.conf[0])
            x1,y1,x2,y2 = [int(v) for v in box.xyxy[0].tolist()]
            color = AI_COLORS[cls_id % len(AI_COLORS)]
            cv2.rectangle(annotated, (x1,y1), (x2,y2), color, 2)
            label = f"{cls_name} {conf_val:.2f}"
            (tw,th),_ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
            ty = max(th+6, y1)
            cv2.rectangle(annotated, (x1,ty-th-4), (x1+tw+6,ty+2), color, -1)
            cv2.putText(annotated, label, (x1+3,ty-1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 2, cv2.LINE_AA)
            dets.append({"class":cls_name,"conf":round(conf_val,3),"bbox":[x1,y1,x2,y2]})
    if r.masks is not None:
        overlay = annotated.copy()
        for idx, mxy in enumerate(r.masks.xy):
            if mxy is None or len(mxy)==0: continue
            pts   = mxy.astype(np.int32)
            color = AI_COLORS[(int(r.boxes.cls[idx]) if r.boxes else 0) % len(AI_COLORS)]
            cv2.fillPoly(overlay, [pts], color)
        annotated = cv2.addWeighted(annotated, 0.6, overlay, 0.4, 0)
    cv2.putText(annotated, f"YOLO11 | {len(dets)} obj | {fps_val:.1f} FPS",
                (8, VIEW_HEIGHT-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200,200,200), 1)

    with yolo_lock:
        yolo_latest_dets = dets
        yolo_fps = round(fps_val, 1)
        if dets:
            ts  = time.strftime("%Y-%m-%d %H:%M:%S")
            s   = ", ".join(f"{d['class']}({d['conf']:.2f})" for d in dets[:5])
            yolo_log.insert(0, {"time": time.strftime("%H:%M:%S"), "count": len(dets), "summary": s})
            if len(yolo_log) > 50: yolo_log.pop()
            for d in dets:
                yolo_full_log.append({
                    "timestamp" : ts,
                    "fps"       : round(fps_val, 1),
                    "class"     : d["class"],
                    "confidence": d["conf"],
                    "bbox_x1"   : d["bbox"][0],
                    "bbox_y1"   : d["bbox"][1],
                    "bbox_x2"   : d["bbox"][2],
                    "bbox_y2"   : d["bbox"][3],
                })
            if len(yolo_full_log) > AI_LOG_MAX:
                yolo_full_log = yolo_full_log[-AI_LOG_MAX:]

    return annotated, dets

# =========================================================
# AI CSV HELPERS
# =========================================================
AI_CSV_HEADERS = ["timestamp", "fps", "class", "confidence",
                  "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"]

# =========================================================
# UI HTML  — layout 2-column ใหม่, logic เดิมทั้งหมด
# =========================================================
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pi RTSP → PC Motion / QR UI</title>
  <style>
    html,body{height:100%;margin:0;padding:0;font-family:Arial,sans-serif;background:#111;color:#eee;overflow:hidden}
    .wrap{height:100vh;display:flex;flex-direction:column;padding:8px 12px;box-sizing:border-box}
    .title{font-size:16px;font-weight:700;margin-bottom:6px;flex-shrink:0}

    /* ── 2-column main layout ── */
    .main-grid{display:grid;grid-template-columns:1fr 300px;gap:10px;flex:1;min-height:0;overflow:hidden}

    /* ── Left: video column ── */
    .video-col{display:flex;flex-direction:column;min-height:0;gap:6px}
    .imgbox{flex:1;min-height:0;background:#000;border-radius:8px;overflow:hidden;position:relative;}
    .imgbox img{width:100%;height:100%;object-fit:contain;display:block}
    .buttons{display:flex;flex-wrap:wrap;gap:5px;flex-shrink:0}

    /* ── Right: info column ── */
    .info-col{display:flex;flex-direction:column;min-height:0;gap:8px;overflow-y:auto}
    .card{background:#1b1b1b;border:1px solid #333;border-radius:10px;padding:10px;flex-shrink:0}
    .card-fill{background:#1b1b1b;border:1px solid #333;border-radius:10px;padding:10px;display:flex;flex-direction:column;flex:1;min-height:0}

    button{border:none;border-radius:8px;padding:7px 11px;font-size:11px;font-weight:600;cursor:pointer}
    .primary{background:#28a745;color:white}
    .warn{background:#ff9800;color:#111}
    .danger{background:#e53935;color:white}
    .secondary{background:#2f2f2f;color:#fff}
    .scan-btn{background:#005533;color:#57ff9a;border:1px solid #57ff9a !important;font-weight:700;}
    .scan-btn:disabled{opacity:0.5;cursor:not-allowed;}
    .ai-btn{background:#1a1a40;color:#7b9fff;border:1px solid #7b9fff55 !important;font-weight:700;}
    .info{line-height:1.6;font-size:12px}
    .label{color:#aaa;display:inline-block;min-width:100px;vertical-align:top}
    .ok{color:#57d957;font-weight:700}
    .bad{color:#ff6b6b;font-weight:700}
    .warntext{color:#ffc14d;font-weight:700}
    .qrtext{font-size:13px;font-weight:700;color:#57d957;word-break:break-word}
    .log-wrap{flex:1;min-height:0;display:flex;flex-direction:column}
    .log{background:#0d0d0d;border:1px solid #2c2c2c;border-radius:8px;padding:8px;flex:1;min-height:0;overflow-y:auto;white-space:pre-wrap;font-family:Consolas,monospace;font-size:11px}
    ul{margin:4px 0 0 16px;padding:0}
    li{margin-bottom:3px;font-size:12px}

    #snapOverlay{display:none;position:absolute;inset:0;background:#000;z-index:10;border-radius:8px;overflow:hidden;flex-direction:column;}
    #snapOverlay.show{display:flex;}
    #snapOverlay img{flex:1;width:100%;object-fit:contain;}
    #snapOverlay .snap-bar{display:flex;align-items:center;gap:8px;padding:6px 10px;background:#111;flex-shrink:0;}
    #snapOverlay .snap-result{font-family:Consolas,monospace;font-size:11px;color:#57ff9a;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    #snapOverlay .snap-close{background:none;border:1px solid #444;color:#aaa;border-radius:6px;padding:2px 10px;font-size:11px;cursor:pointer;}

    /* ── AI Modal ── */
    #aiModal{display:none;position:fixed;inset:0;z-index:100;background:#000a;align-items:center;justify-content:center;}
    #aiModal.show{display:flex;}
    .ai-modal-box{background:#161616;border:1px solid #2a2a2a;border-radius:14px;width:min(98vw,900px);height:min(96vh,680px);display:flex;flex-direction:column;overflow:hidden;}
    .ai-modal-head{display:flex;align-items:center;gap:8px;padding:10px 14px;background:#1c1c1c;border-bottom:1px solid #2a2a2a;flex-shrink:0;}
    .ai-modal-head .title{font-size:14px;font-weight:700;color:#7b9fff;margin:0;flex:1;}
    .ai-modal-close{background:none;border:1px solid #444;color:#aaa;border-radius:6px;padding:3px 12px;font-size:12px;cursor:pointer;}
    .ai-modal-body{display:grid;grid-template-columns:1fr 280px;flex:1;min-height:0;overflow:hidden;gap:0;}
    .ai-feed-col{display:flex;flex-direction:column;min-height:0;padding:10px;gap:8px;}
    .ai-feed-box{flex:1;min-height:0;background:#000;border-radius:8px;overflow:hidden;position:relative;}
    .ai-feed-box img{width:100%;height:100%;object-fit:contain;display:block;}
    .ai-feed-badge{position:absolute;top:8px;left:8px;background:#1a1a40cc;border:1px solid #7b9fff44;border-radius:6px;padding:3px 10px;font-size:11px;color:#7b9fff;font-weight:700;}
    .ai-snap-overlay{display:none;position:absolute;inset:0;background:#000;z-index:10;border-radius:8px;overflow:hidden;flex-direction:column;}
    .ai-snap-overlay.show{display:flex;}
    .ai-snap-overlay img{flex:1;width:100%;object-fit:contain;}
    .ai-snap-overlay .snap-bar{display:flex;align-items:center;gap:8px;padding:6px 10px;background:#111;flex-shrink:0;}
    .ai-snap-overlay .snap-result{font-family:Consolas,monospace;font-size:11px;color:#7b9fff;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .ai-snap-overlay .snap-close{background:none;border:1px solid #444;color:#aaa;border-radius:6px;padding:2px 10px;font-size:11px;cursor:pointer;}
    .ai-side-col{display:flex;flex-direction:column;padding:10px 10px 10px 0;gap:8px;min-height:0;overflow-y:auto;}
    .ai-card{background:#1b1b1b;border:1px solid #2a2a2a;border-radius:10px;padding:10px;flex-shrink:0;}
    .ai-card h3{font-size:11px;color:#7b9fff;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;}
    .ai-row{display:flex;align-items:center;gap:6px;margin-bottom:6px;}
    .ai-row label{font-size:11px;color:#aaa;min-width:55px;}
    .ai-row input[type=range]{flex:1;accent-color:#7b9fff;}
    .ai-row .val{font-size:11px;color:#7b9fff;min-width:30px;text-align:right;}
    .ai-row input[type=text]{flex:1;background:#111;border:1px solid #333;border-radius:5px;color:#eee;padding:3px 7px;font-size:11px;}
    .ai-row select{flex:1;background:#111;border:1px solid #333;border-radius:5px;color:#eee;padding:3px 7px;font-size:11px;}
    .ai-stat{font-size:11px;line-height:1.8;}
    .ai-stat .sk{color:#888;}
    .ai-stat .sv{color:#7b9fff;font-weight:700;}
    .csv-card{background:#0d1a0d;border:1px solid #1a3a1a;border-radius:10px;padding:10px;flex-shrink:0;}
    .csv-card h3{font-size:11px;color:#57ff9a;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;}
    .csv-msg{font-size:11px;margin-top:6px;min-height:14px;font-weight:600;}
    .csv-msg.ok{color:#57ff9a;}  .csv-msg.err{color:#ff6b6b;}
    .ai-det-log{flex:1;min-height:100px;overflow-y:auto;white-space:pre-wrap;font-family:Consolas,monospace;font-size:11px;color:#ccc;background:#0d0d0d;border:1px solid #222;border-radius:8px;padding:7px;}
    .ai-det-entry{padding:2px 0;border-bottom:1px solid #1a1a1a;line-height:1.5;}
    .ai-det-entry .t{color:#555;} .ai-det-entry .n{color:#7b9fff;font-weight:700;} .ai-det-entry .s{color:#aaa;}
    .ai-btn-row{display:flex;gap:6px;flex-wrap:wrap;}
    .ai-err{font-size:11px;color:#ff6b6b;margin-top:4px;min-height:14px;}
  </style>
</head>
<body>
<div class="wrap">
  <div class="title">Pi RTSP → PC Motion / QR UI</div>

  <div class="main-grid">

    <!-- ══ LEFT: Video ══ -->
    <div class="video-col">
      <div class="imgbox">
        <img id="liveFeed" src="/video_feed">
        <div id="snapOverlay">
          <img id="snapImg" alt="QR Snapshot">
          <div class="snap-bar">
            <span style="font-size:11px;color:#57ff9a;font-weight:700;">⬡ SNAPSHOT</span>
            <span id="snapResult" class="snap-result">—</span>
            <button class="snap-close" onclick="closeSnap()">✕ CLOSE</button>
          </div>
        </div>
      </div>

      <div class="buttons">
        <button class="warn"      onclick="setMode('motion')">Motion Mode</button>
        <button class="primary"   onclick="setMode('qr')">QR Realtime</button>
        <button class="scan-btn"  id="scanBtn" onclick="scanQR()">⬡ SCAN QR</button>
        <button class="ai-btn"    onclick="openAI()">🤖 AI Detection</button>
        <button class="secondary" onclick="ping()">Refresh Status</button>
        <button class="secondary" onclick="exportCSV()">⬇ Export CSV</button>
        <button class="danger"    onclick="clearLog()">✕ Clear Log</button>
      </div>
    </div>

    <!-- ══ RIGHT: Info + Log ══ -->
    <div class="info-col">

      <div class="card">
        <div style="font-size:11px;color:#57ff9a;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">📡 Status</div>
        <div class="info">
          <div><span class="label">Camera:</span> <span id="cameraStatus">-</span></div>
          <div><span class="label">Mode:</span> <span id="modeStatus">-</span></div>
          <div><span class="label">Motion:</span> <span id="motionStatus">-</span></div>
          <div><span class="label">Locked Target:</span> <span id="lockStatus">-</span></div>
          <div><span class="label">Motion Center:</span> <span id="motionCenter">-</span></div>
          <div><span class="label">QR Found:</span> <span id="qrFoundStatus">-</span></div>
          <div><span class="label">Main QR:</span> <span id="qrText" class="qrtext">-</span></div>
          <div><span class="label">All QR:</span> <span id="allQrText">-</span></div>
          <div><span class="label">Timestamp:</span> <span id="timeStatus">-</span></div>
        </div>
      </div>

      <div class="card-fill">
        <div style="font-size:11px;color:#57ff9a;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">🔍 QR Read Log</div>
        <div class="log-wrap">
          <div id="logBox" class="log">No QR log yet</div>
        </div>
      </div>

    </div>
  </div>
</div>

<!-- ══ AI MODAL ══ -->
<div id="aiModal">
  <div class="ai-modal-box">
    <div class="ai-modal-head">
      <span class="title">🤖 YOLO11 AI Detection</span>
      <button class="ai-modal-close" onclick="closeAI()">✕ ปิด</button>
    </div>
    <div class="ai-modal-body">
      <div class="ai-feed-col">
        <div class="ai-feed-box">
          <img id="aiFeed" src="/video_feed" alt="AI Feed">
          <div class="ai-feed-badge" id="aiFeedBadge">YOLO11 — กำลังรอ...</div>
          <div id="aiSnapOverlay" class="ai-snap-overlay">
            <img id="aiSnapImg" alt="AI Snapshot">
            <div class="snap-bar">
              <span style="font-size:11px;color:#7b9fff;font-weight:700;">📸 SNAPSHOT</span>
              <span id="aiSnapResult" class="snap-result">—</span>
              <button class="snap-close" onclick="closeAISnap()">✕</button>
            </div>
          </div>
        </div>
        <div class="ai-btn-row">
          <button class="ai-btn" id="aiSnapBtn" onclick="aiSnapshot()">📸 Snapshot</button>
          <button class="primary" id="startAIBtn" onclick="startAI()">▶ Start AI</button>
          <button class="danger"                  onclick="stopAI()">■ Stop</button>
        </div>
      </div>
      <div class="ai-side-col">
        <div class="ai-card">
          <h3>⚙ Settings</h3>
          <div class="ai-row">
            <label>Model</label>
            <input type="text" id="aiModelPath" value="best.pt" placeholder="best.pt">
          </div>
          <div class="ai-row">
            <label>Task</label>
            <select id="aiTask">
              <option value="detect">detect</option>
              <option value="segment">segment</option>
            </select>
          </div>
          <div class="ai-row">
            <label>Conf</label>
            <input type="range" id="aiConf" min="0.05" max="0.95" step="0.05" value="0.35"
                   oninput="document.getElementById('aiConfVal').textContent=parseFloat(this.value).toFixed(2)">
            <span class="val" id="aiConfVal">0.35</span>
          </div>
          <div class="ai-row">
            <label>IoU</label>
            <input type="range" id="aiIou" min="0.1" max="0.9" step="0.05" value="0.45"
                   oninput="document.getElementById('aiIouVal').textContent=parseFloat(this.value).toFixed(2)">
            <span class="val" id="aiIouVal">0.45</span>
          </div>
          <div class="ai-row">
            <label>ImgSz</label>
            <select id="aiImgsz">
              <option value="320">320</option>
              <option value="416">416</option>
              <option value="640" selected>640</option>
              <option value="1280">1280</option>
            </select>
          </div>
          <div class="ai-btn-row" style="margin-top:6px;">
            <button class="ai-btn" id="loadBtn" onclick="loadModel()">⬡ Load Model</button>
          </div>
          <div class="ai-err" id="aiErr"></div>
        </div>

        <div class="ai-card">
          <h3>📊 Status</h3>
          <div class="ai-stat">
            <div><span class="sk">Model: </span><span class="sv" id="aiModelSt">-</span></div>
            <div><span class="sk">Task: </span><span class="sv" id="aiTaskSt">-</span></div>
            <div><span class="sk">FPS: </span><span class="sv" id="aiFpsSt">-</span></div>
            <div><span class="sk">Objects: </span><span class="sv" id="aiObjSt">-</span></div>
            <div><span class="sk">Mode: </span><span class="sv" id="aiModeSt">-</span></div>
            <div><span class="sk">Log rows: </span><span class="sv" id="aiLogRows">0</span></div>
          </div>
        </div>

        <div class="csv-card">
          <h3>💾 AI Log → CSV</h3>
          <div class="ai-btn-row">
            <button style="background:#003320;color:#57ff9a;border:1px solid #57ff9a66;border-radius:8px;padding:7px 12px;font-size:12px;font-weight:700;cursor:pointer;"
                    onclick="exportAICSV()">⬇ Export AI CSV</button>
            <button class="danger" onclick="clearAILog()">✕ Clear</button>
          </div>
          <div id="csvMsg" class="csv-msg"></div>
        </div>

        <div class="ai-card" style="flex:1;min-height:0;display:flex;flex-direction:column;">
          <h3>🔍 Detection Log</h3>
          <div class="ai-det-log" id="aiDetLog">ยังไม่มี detection</div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
function renderAllQr(items){
  const box = document.getElementById("allQrText");
  if(!items||items.length===0){box.innerHTML="-";return;}
  box.innerHTML="<ul>"+items.map(x=>"<li>"+String(x)+"</li>").join("")+"</ul>";
}
function renderQrLog(items){
  const box=document.getElementById("logBox");
  if(!items||items.length===0){box.textContent="No QR log yet";return;}
  box.textContent=items.map(item=>{
    const values=(item.values||[]).join(", ");
    return `[${item.time}] (${item.mode}) ${values}`;
  }).join("\\n");
}
async function ping(){
  try{
    const r=await fetch("/status");
    const j=await r.json();
    document.getElementById("cameraStatus").textContent=j.camera_ok?"CONNECTED":"NOT READY";
    document.getElementById("cameraStatus").className=j.camera_ok?"ok":"bad";
    document.getElementById("modeStatus").textContent=(j.mode||"-").toUpperCase();
    document.getElementById("motionStatus").textContent=j.motion_found?"TRACKING":"NO MOTION";
    document.getElementById("motionStatus").className=j.motion_found?"ok":"warntext";
    document.getElementById("lockStatus").textContent=j.locked?"LOCKED":"UNLOCKED";
    document.getElementById("lockStatus").className=j.locked?"ok":"bad";
    document.getElementById("motionCenter").textContent=j.motion_center?`${j.motion_center[0]}, ${j.motion_center[1]}`:"-";
    document.getElementById("qrFoundStatus").textContent=j.qr_found?"YES":"NO";
    document.getElementById("qrFoundStatus").className=j.qr_found?"ok":"bad";
    document.getElementById("qrText").textContent=j.qr||"-";
    renderAllQr(j.all_qr||[]);
    renderQrLog(j.qr_log||[]);
    document.getElementById("timeStatus").textContent=j.timestamp||"-";
  }catch(e){console.log("Status error:",e);}
}
async function setMode(mode){
  try{
    await fetch("/mode",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:mode})});
    ping();
  }catch(e){console.log("Set mode error:",e);}
}
async function scanQR() {
  const btn = document.getElementById('scanBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Scanning...';
  try {
    const r = await fetch('/scan_qr');
    const d = await r.json();
    const overlay = document.getElementById('snapOverlay');
    const snapImg = document.getElementById('snapImg');
    const snapResult = document.getElementById('snapResult');
    if (d.image_b64) {
      snapImg.src = 'data:image/jpeg;base64,' + d.image_b64;
      overlay.classList.add('show');
    }
    const codes = (d.codes||[]).map(c=>typeof c==='object'?c.data:c).filter(Boolean);
    if (codes.length > 0) {
      snapResult.textContent = codes.join(' | ');
      snapResult.style.color = '#57ff9a';
    } else {
      snapResult.textContent = 'No QR found';
      snapResult.style.color = '#ffc14d';
    }
    ping();
  } catch(e) {
    alert('Scan error: ' + e);
  } finally {
    btn.disabled = false;
    btn.textContent = '⬡ SCAN QR';
  }
}
function closeSnap() {
  document.getElementById('snapOverlay').classList.remove('show');
}
async function exportCSV(){
  try{
    const r = await fetch("/export_csv");
    if(!r.ok){ const j=await r.json().catch(()=>({})); alert("Export failed: "+(j.error||r.status)); return; }
    const blob=await r.blob(), url=URL.createObjectURL(blob), a=document.createElement("a");
    a.href=url;
    const cd=r.headers.get("Content-Disposition")||"", m=cd.match(/filename=([^;]+)/);
    a.download=m?m[1]:"qr_log.csv";
    document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
  }catch(e){ alert("Export error: "+e); }
}
async function clearLog(){
  if(!confirm("Clear QR log?")) return;
  try{
    const r=await fetch("/clear_log",{method:"POST"});
    const j=await r.json();
    if(j.ok){ document.getElementById("logBox").textContent="No QR log yet"; ping(); }
    else alert("Error: "+j.error);
  }catch(e){ alert("Error: "+e); }
}
setInterval(ping, 400);
window.onload=function(){ping();}

// ── AI Modal ──
var aiPollTimer = null;

function openAI(){
  document.getElementById('aiModal').classList.add('show');
  fetch('/mode', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: 'ai'})
  }).then(() => ping()).catch(() => {});
  const feed = document.getElementById('aiFeed');
  feed.src = '/video_feed?' + Date.now();
  aiPollTimer = setInterval(refreshAISt, 600);
  refreshAISt();
}

function closeAI(){
  document.getElementById('aiModal').classList.remove('show');
  if(aiPollTimer){ clearInterval(aiPollTimer); aiPollTimer = null; }
  document.getElementById('aiFeed').src = '';
  fetch('/mode', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: 'motion'})
  }).then(() => ping()).catch(() => {});
}

async function loadModel(){
  const btn=document.getElementById('loadBtn');
  btn.disabled=true; btn.textContent='⏳...';
  try{
    const path=document.getElementById('aiModelPath').value.trim();
    const task=document.getElementById('aiTask').value;
    const r=await fetch('/ai/load',{method:'POST',headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify({path,task})});
    const j=await r.json();
    document.getElementById('aiErr').textContent = j.ok ? '' : ('❌ '+j.error);
    refreshAISt();
  }catch(e){ document.getElementById('aiErr').textContent='❌ '+e; }
  finally{ btn.disabled=false; btn.textContent='⬡ Load Model'; }
}

async function startAI(){
  const conf  = parseFloat(document.getElementById('aiConf').value);
  const iou   = parseFloat(document.getElementById('aiIou').value);
  const imgsz = parseInt(document.getElementById('aiImgsz').value);
  await fetch('/ai/config',{method:'POST',headers:{'Content-Type':'application/json'},
                             body:JSON.stringify({conf,iou,imgsz})});
  await fetch('/mode',{method:'POST',headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({mode:'ai'})});
  ping();
}

async function stopAI(){
  await fetch('/mode',{method:'POST',headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({mode:'motion'})});
  ping();
}

async function aiSnapshot(){
  const btn=document.getElementById('aiSnapBtn');
  btn.disabled=true; btn.textContent='⏳...';
  try{
    const r=await fetch('/ai/snapshot'); const d=await r.json();
    if(d.image_b64){
      document.getElementById('aiSnapImg').src='data:image/jpeg;base64,'+d.image_b64;
      document.getElementById('aiSnapOverlay').classList.add('show');
    }
    const res=document.getElementById('aiSnapResult');
    if(d.detections&&d.detections.length){
      res.textContent=d.detections.slice(0,5).map(x=>x.class+' '+x.conf).join(' | ');
      res.style.color='#7b9fff';
    }else{ res.textContent='No objects'; res.style.color='#ffc14d'; }
  }catch(e){ alert('Error: '+e); }
  finally{ btn.disabled=false; btn.textContent='📸 Snapshot'; }
}
function closeAISnap(){
  document.getElementById('aiSnapOverlay').classList.remove('show');
}

async function refreshAISt(){
  try{
    const r=await fetch('/ai/status'); const j=await r.json();
    document.getElementById('aiModelSt').textContent=j.loaded?'✓ Loaded':'✗ Not loaded';
    document.getElementById('aiModelSt').style.color=j.loaded?'#57ff9a':'#ff6b6b';
    document.getElementById('aiTaskSt').textContent=j.task||'-';
    document.getElementById('aiFpsSt').textContent=(j.fps||0)+' fps';
    document.getElementById('aiObjSt').textContent=j.obj_count||0;
    document.getElementById('aiModeSt').textContent=j.mode_active?'🟢 AI Running':'⚪ Standby';
    document.getElementById('aiLogRows').textContent=j.log_rows||0;
    document.getElementById('aiFeedBadge').textContent=
      `YOLO11 | ${j.obj_count||0} obj | ${j.fps||0} fps`;
    const log=j.ai_log||[];
    const box=document.getElementById('aiDetLog');
    if(!log.length){box.textContent='ยังไม่มี detection';return;}
    box.innerHTML=log.map(e=>
      `<div class="ai-det-entry"><span class="t">[${e.time}]</span> `+
      `<span class="n">${e.count} obj</span> `+
      `<span class="s">${e.summary}</span></div>`
    ).join('');
  }catch(e){}
}

function showCsvMsg(text, isOk){
  const el = document.getElementById('csvMsg');
  el.textContent = text;
  el.className = 'csv-msg ' + (isOk ? 'ok' : 'err');
  setTimeout(() => { el.textContent = ''; el.className = 'csv-msg'; }, 3500);
}

async function exportAICSV(){
  try{
    const r = await fetch('/ai/export_csv');
    if(!r.ok){
      const j = await r.json().catch(()=>({}));
      showCsvMsg('❌ ' + (j.error||'Export failed'), false);
      return;
    }
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    const cd   = r.headers.get('Content-Disposition') || '';
    const m    = cd.match(/filename=([^;]+)/);
    a.download = m ? m[1].trim() : 'ai_detection_log.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showCsvMsg('✓ บันทึก CSV สำเร็จ', true);
    refreshAISt();
  }catch(e){
    showCsvMsg('❌ ' + e, false);
  }
}

async function clearAILog(){
  if(!confirm('ล้าง AI Detection Log ทั้งหมด?')) return;
  try{
    const r = await fetch('/ai/clear_log', {method:'POST'});
    const j = await r.json();
    if(j.ok){
      showCsvMsg('✓ ล้าง log สำเร็จ', true);
      document.getElementById('aiDetLog').textContent = 'ยังไม่มี detection';
      document.getElementById('aiLogRows').textContent = '0';
    } else {
      showCsvMsg('❌ ' + j.error, false);
    }
  }catch(e){ showCsvMsg('❌ ' + e, false); }
}
</script>
</body>
</html>
"""

# =========================================================
# HELPERS
# =========================================================
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def add_qr_log(mode, qr_list):
    global qr_read_log
    if not qr_list:
        return
    entry = {"time": time.strftime("%H:%M:%S"), "mode": mode, "values": qr_list}
    qr_read_log.insert(0, entry)
    qr_read_log = qr_read_log[:QR_LOG_MAX]

def export_csv():
    try:
        with open(CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "qr_data"])
            for entry in reversed(qr_read_log):
                ts = entry.get("time", "")
                for val in entry.get("values", []):
                    writer.writerow([ts, val])
        return sum(len(e.get("values",[])) for e in qr_read_log)
    except Exception as e:
        log(f"[CSV] export error: {e}")
        return -1

def get_latest_raw_frame():
    with frame_lock:
        if latest_raw_frame is None:
            return None
        return latest_raw_frame.copy()

def set_latest_display_frame(frame):
    global latest_display_frame
    with frame_lock:
        latest_display_frame = frame.copy()

def get_latest_display_frame():
    with frame_lock:
        if latest_display_frame is not None:
            return latest_display_frame.copy()
        if latest_raw_frame is not None:
            return latest_raw_frame.copy()
        return None

def dedupe_preserve_order(items):
    out, seen = [], set()
    for x in items:
        key = (x or "").strip()
        if not key or key in seen:
            continue
        seen.add(key); out.append(key)
    return out

def polygon_area(pts):
    if pts is None:
        return 0.0
    return abs(cv2.contourArea(pts.astype(np.float32).reshape(-1, 2)))

def draw_qr_box_and_text(img, points, qr_text, color=(0, 255, 0)):
    if points is None:
        return img
    pts = points.astype(int).reshape(-1, 2)
    if len(pts) >= 4:
        for i in range(len(pts)):
            cv2.line(img, tuple(pts[i]), tuple(pts[(i+1)%len(pts)]), color, 2)
        x, y = pts[0]
        label = f"QR: {qr_text}" if qr_text else "QR"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
        ty = max(th+6, y-8)
        cv2.rectangle(img, (x, ty-th-4), (x+tw+6, ty+2), (0,180,0), -1)
        cv2.putText(img, label, (x+3, ty-1), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 2, cv2.LINE_AA)
    return img

def distance(p1, p2):
    return float(np.linalg.norm(np.array(p1,dtype=np.float32) - np.array(p2,dtype=np.float32)))

def frame_to_b64(frame):
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("utf-8")

# =========================================================
# QR DETECTION
# =========================================================
def preprocess_candidates(img):
    candidates = [("color", img)]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    candidates.append(("gray", gray))
    blur = cv2.GaussianBlur(gray, (0,0), 2)
    sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
    candidates.append(("sharp", sharp))
    return candidates

def detect_qr_fullframe(frame, annotated):
    global latest_qr_found, latest_qr_text, latest_qr_all, last_qr, last_qr_time

    resized = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))
    detector = cv2.QRCodeDetector()
    all_texts, all_boxes = [], []

    for _, candidate in preprocess_candidates(resized):
        try:
            retval, decoded_info, points_multi, _ = detector.detectAndDecodeMulti(candidate)
            if retval and points_multi is not None:
                for qr_text, pts in zip(decoded_info, points_multi):
                    qr_text = (qr_text or "").strip()
                    if pts is not None and len(pts) > 0:
                        all_boxes.append((pts, qr_text))
                    if qr_text:
                        all_texts.append(qr_text)
        except Exception:
            pass
        try:
            data, points, _ = detector.detectAndDecode(candidate)
            data = (data or "").strip()
            if points is not None and len(points) > 0:
                all_boxes.append((points, data))
            if data:
                all_texts.append(data)
        except Exception:
            pass

    all_texts = dedupe_preserve_order(all_texts)
    best_text, best_area = "", -1

    for pts, txt in all_boxes:
        annotated = draw_qr_box_and_text(annotated, pts, txt if txt else "QR")
        area = polygon_area(pts)
        if txt and area > best_area:
            best_area = area; best_text = txt

    latest_qr_found = len(all_texts) > 0
    latest_qr_text = best_text if best_text else (all_texts[0] if all_texts else "")
    latest_qr_all = all_texts

    if latest_qr_text:
        now = time.time()
        if not (latest_qr_text == last_qr and (now - last_qr_time) < DEBOUNCE_SEC):
            last_qr = latest_qr_text; last_qr_time = now
            add_qr_log(detect_mode, all_texts)

    return annotated

# =========================================================
# MOTION LOCK TARGET
# =========================================================
def choose_locked_target(candidates):
    global locked_target, lock_lost_count

    if not candidates:
        lock_lost_count += 1
        if lock_lost_count > LOCK_LOST_LIMIT:
            locked_target = None
        return None

    frame_center = (VIEW_WIDTH/2.0, VIEW_HEIGHT/2.0)

    if locked_target is None:
        lock_lost_count = 0
        best, best_score = None, 1e9
        for c in candidates:
            score = distance(c["center"], frame_center) - (c["area"] * CENTER_AREA_WEIGHT)
            if score < best_score:
                best_score = score; best = c
        locked_target = best
        return locked_target

    old_center = locked_target["center"]
    nearest, best_score = None, 1e9
    for c in candidates:
        score = distance(old_center, c["center"]) + (LOCK_CENTER_WEIGHT * distance(c["center"], frame_center))
        if score < best_score:
            best_score = score; nearest = c

    if nearest and distance(old_center, nearest["center"]) <= LOCK_MAX_DIST:
        locked_target = nearest; lock_lost_count = 0
        return locked_target

    lock_lost_count += 1
    if lock_lost_count > LOCK_LOST_LIMIT:
        locked_target = None
    return locked_target


def _find_white_circles(img):
    hsv  = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv,
                       np.array([0,   0,   WHITE_V_MIN], dtype=np.uint8),
                       np.array([180, WHITE_S_MAX, 255], dtype=np.uint8))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask   = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    mask   = cv2.dilate(mask, kernel, iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    results = []
    for c in contours:
        area = cv2.contourArea(c)
        if not (WHITE_AREA_MIN <= area <= WHITE_AREA_MAX):
            continue
        hull_area = cv2.contourArea(cv2.convexHull(c))
        solidity  = area / hull_area if hull_area > 0 else 0
        if solidity < WHITE_SOLIDITY:
            continue
        x, y, w, h = cv2.boundingRect(c)
        results.append((c, (x, y, w, h), x+w/2.0, y+h/2.0, area))
    return results, mask


def _find_black_in_roi(img, roi_rect, margin=4):
    x, y, w, h = roi_rect
    x1 = max(0, x-margin);            y1 = max(0, y-margin)
    x2 = min(img.shape[1], x+w+margin); y2 = min(img.shape[0], y+h+margin)
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    hsv_roi    = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    black_mask = cv2.inRange(hsv_roi,
                              np.array([0, 0, 0], dtype=np.uint8),
                              np.array([180, BLACK_IN_WHITE_S_MAX,
                                        BLACK_IN_WHITE_V_MAX], dtype=np.uint8))
    b = 3
    black_mask[:b,:]=0; black_mask[-b:,:]=0
    black_mask[:,:b]=0; black_mask[:,-b:]=0
    contours_b, _ = cv2.findContours(black_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None; best_area = -1
    for cb in contours_b:
        a = cv2.contourArea(cb)
        if not (BLACK_DOT_AREA_MIN <= a <= BLACK_DOT_AREA_MAX):
            continue
        if a > best_area:
            best_area = a; best = cb
    if best is None:
        return None
    xb, yb, wb, hb = cv2.boundingRect(best)
    return (x1 + xb + wb/2.0, y1 + yb + hb/2.0, best_area)


# วาง CODE นี้แทนที่ detect_black_motion_and_lock เดิมใน app_yolo11.py
# ============================================================

# ── WHITE CIRCLE + BLACK DOT CONFIG ─────────────────────


def _find_white_circles(img):
    hsv  = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv,
                       np.array([0,   0,   WHITE_V_MIN], dtype=np.uint8),
                       np.array([180, WHITE_S_MAX, 255], dtype=np.uint8))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask   = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    mask   = cv2.dilate(mask, kernel, iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    results = []
    for c in contours:
        area = cv2.contourArea(c)
        if not (WHITE_AREA_MIN <= area <= WHITE_AREA_MAX):
            continue
        hull_area = cv2.contourArea(cv2.convexHull(c))
        solidity  = area / hull_area if hull_area > 0 else 0
        if solidity < WHITE_SOLIDITY:
            continue
        x, y, w, h = cv2.boundingRect(c)
        results.append((c, (x, y, w, h), x+w/2.0, y+h/2.0, area))
    return results, mask


def _find_black_in_roi(img, roi_rect, margin=4):
    x, y, w, h = roi_rect
    x1 = max(0, x-margin);            y1 = max(0, y-margin)
    x2 = min(img.shape[1], x+w+margin); y2 = min(img.shape[0], y+h+margin)
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    hsv_roi    = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    black_mask = cv2.inRange(hsv_roi,
                              np.array([0, 0, 0], dtype=np.uint8),
                              np.array([180, BLACK_IN_WHITE_S_MAX,
                                        BLACK_IN_WHITE_V_MAX], dtype=np.uint8))
    b = 3
    black_mask[:b,:]=0; black_mask[-b:,:]=0
    black_mask[:,:b]=0; black_mask[:,-b:]=0
    contours_b, _ = cv2.findContours(black_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None; best_area = -1
    for cb in contours_b:
        a = cv2.contourArea(cb)
        if not (BLACK_DOT_AREA_MIN <= a <= BLACK_DOT_AREA_MAX):
            continue
        if a > best_area:
            best_area = a; best = cb
    if best is None:
        return None
    xb, yb, wb, hb = cv2.boundingRect(best)
    return (x1 + xb + wb/2.0, y1 + yb + hb/2.0, best_area)


def detect_black_motion_and_lock(frame, annotated):
    global motion_found, motion_center, motion_box, locked_target

    img = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))

    # Step 1: หาวงกลมขาว
    white_blobs, white_mask = _find_white_circles(img)
    if not white_blobs:
        motion_found = False; motion_center = None; motion_box = None
        mini = cv2.resize(white_mask, (VIEW_WIDTH//4, VIEW_HEIGHT//4))
        annotated[VIEW_HEIGHT-VIEW_HEIGHT//4:, :VIEW_WIDTH//4] = cv2.cvtColor(mini, cv2.COLOR_GRAY2BGR)
        cv2.putText(annotated, "NO WHITE CIRCLE",
                    (5, VIEW_HEIGHT-VIEW_HEIGHT//4-5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0,180,255), 1, cv2.LINE_AA)
        return annotated

    # Step 2: หาจุดดำในวงขาว
    cands = []
    for c_white, rect_w, cx_w, cy_w, area_w in white_blobs:
        cv2.drawContours(annotated, [c_white], -1, (200,200,0), 1)
        result = _find_black_in_roi(img, rect_w)
        if result is None:
            continue
        cx_b, cy_b, area_b = result
        cands.append({"area":area_b,"rect":rect_w,"center":(cx_b,cy_b),"score":area_w})

    if not cands:
        motion_found = False; motion_center = None; motion_box = None
        mini = cv2.resize(white_mask, (VIEW_WIDTH//4, VIEW_HEIGHT//4))
        annotated[VIEW_HEIGHT-VIEW_HEIGHT//4:, :VIEW_WIDTH//4] = cv2.cvtColor(mini, cv2.COLOR_GRAY2BGR)
        cv2.putText(annotated, "WHITE OK / NO BLACK DOT",
                    (5, VIEW_HEIGHT-VIEW_HEIGHT//4-5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,200,255), 1, cv2.LINE_AA)
        return annotated

    # Step 3: lock target
    t = choose_locked_target(cands)
    if t is None:
        motion_found = False; motion_center = None; motion_box = None
        return annotated

    x, y, w, h = t["rect"]; cx, cy = t["center"]
    motion_found  = True
    motion_center = (int(cx), int(cy))
    motion_box    = (x, y, w, h)

    # กรอบเขียว = วงกลมขาว (bounding rect)
    cv2.rectangle(annotated, (x,y), (x+w,y+h), (0,255,0), 2)

    # กรอบแดง = จุดดำ (คำนวณจาก area_b)
    dot_r = max(8, int((t["area"]**0.5) * 0.8))
    cx_i, cy_i = int(cx), int(cy)
    cv2.rectangle(annotated,
                  (cx_i - dot_r, cy_i - dot_r),
                  (cx_i + dot_r, cy_i + dot_r),
                  (0, 0, 255), 2)

    # crosshair
    cv2.line(annotated, (cx_i-14, cy_i), (cx_i+14, cy_i), (0,0,255), 2)
    cv2.line(annotated, (cx_i, cy_i-14), (cx_i, cy_i+14), (0,0,255), 2)
    cv2.circle(annotated, (cx_i, cy_i), 4, (0,0,255), -1)

    cv2.putText(annotated, "BLACK DOT LOCKED",
                (x, max(20, y-6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,255,0), 2, cv2.LINE_AA)
    mini = cv2.resize(white_mask, (VIEW_WIDTH//4, VIEW_HEIGHT//4))
    annotated[VIEW_HEIGHT-VIEW_HEIGHT//4:, :VIEW_WIDTH//4] = cv2.cvtColor(mini, cv2.COLOR_GRAY2BGR)
    return annotated

# =========================================================
# CAMERA THREAD
# =========================================================
def camera_loop():
    global latest_raw_frame, latest_display_frame, camera_ok
    while True:
        cap = None
        try:
            log(f"camera_loop: opening {RTSP_URL}")
            cap = cv2.VideoCapture(RTSP_URL)
            if not cap.isOpened():
                camera_ok = False
                log("camera_loop: open failed, retry in 2s")
                time.sleep(2); continue

            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            camera_ok = True
            log("camera_loop: connected")
            while True:
                for _ in range(2):
                    cap.grab()
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    camera_ok = False
                    log("camera_loop: read failed, reconnecting..."); break
                with frame_lock:
                    latest_raw_frame = frame.copy()
                    latest_display_frame = frame.copy()
                new_frame_event.set()
        except Exception as e:
            camera_ok = False
            log(f"camera_loop: exception {e}")
        try:
            if cap: cap.release()
        except Exception:
            pass
        time.sleep(2)

# =========================================================
# REALTIME DETECTION THREAD
# =========================================================
def realtime_loop():
    global motion_found, motion_center, motion_box
    global latest_qr_found, latest_qr_text, latest_qr_all

    while True:
        try:
            new_frame_event.wait(timeout=0.5)
            new_frame_event.clear()
            frame = get_latest_raw_frame()
            if frame is None:
                continue

            annotated = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))

            if detect_mode == "motion":
                annotated = detect_black_motion_and_lock(frame, annotated)
                latest_qr_found = False; latest_qr_text = ""; latest_qr_all = []
            elif detect_mode == "qr":
                motion_found = False; motion_center = None; motion_box = None
                annotated = detect_qr_fullframe(frame, annotated)
            elif detect_mode == "ai":
                motion_found = False; motion_center = None; motion_box = None
                latest_qr_found = False; latest_qr_text = ""; latest_qr_all = []
                annotated, _ = run_yolo_on_frame(frame)
            else:
                motion_found = False; motion_center = None; motion_box = None
                latest_qr_found = False; latest_qr_text = ""; latest_qr_all = []

            set_latest_display_frame(annotated)
            time.sleep(0.01)
        except Exception as e:
            log(f"realtime_loop error: {e}")
            time.sleep(0.1)

# =========================================================
# VIDEO STREAM
# =========================================================
def generate_video():
    while True:
        frame = get_latest_display_frame()
        if frame is None:
            time.sleep(0.05); continue
        ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
        if not ok:
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n")

# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/video_feed")
def video_feed():
    return Response(generate_video(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/mode", methods=["POST"])
def set_mode():
    global detect_mode
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "").strip().lower()
    if mode not in ("motion", "qr", "ai"):
        return jsonify({"ok": False, "error": "invalid_mode"}), 200
    detect_mode = mode
    log(f"mode changed -> {detect_mode}")
    return jsonify({"ok": True, "mode": detect_mode}), 200

@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "ok": True, "camera_ok": camera_ok, "mode": detect_mode,
        "motion_found": motion_found, "motion_center": motion_center,
        "locked": locked_target is not None,
        "qr_found": latest_qr_found, "qr": latest_qr_text,
        "all_qr": latest_qr_all, "qr_log": qr_read_log,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }), 200

@app.route("/scan_qr", methods=["GET"])
def scan_qr():
    frame = get_latest_raw_frame()
    if frame is None:
        return jsonify({"ok": False, "image_b64": "", "codes": [], "qr_found": False,
                        "error": "No frame — กล้องยังไม่พร้อม"}), 200
    img = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))
    annotated = img.copy()
    detector = cv2.QRCodeDetector()
    all_texts, all_boxes = [], []
    for _, candidate in preprocess_candidates(img):
        try:
            retval, decoded_info, points_multi, _ = detector.detectAndDecodeMulti(candidate)
            if retval and points_multi is not None:
                for qr_text, pts in zip(decoded_info, points_multi):
                    qr_text = (qr_text or "").strip()
                    if pts is not None and len(pts) > 0:
                        all_boxes.append((pts, qr_text))
                    if qr_text:
                        all_texts.append(qr_text)
        except Exception:
            pass
        try:
            data, points, _ = detector.detectAndDecode(candidate)
            data = (data or "").strip()
            if points is not None and len(points) > 0:
                all_boxes.append((points, data))
            if data:
                all_texts.append(data)
        except Exception:
            pass
    all_texts = dedupe_preserve_order(all_texts)
    for pts, txt in all_boxes:
        pts_int = pts.astype(int).reshape(-1, 2)
        if len(pts_int) >= 4:
            for i in range(len(pts_int)):
                cv2.line(annotated, tuple(pts_int[i]), tuple(pts_int[(i+1)%len(pts_int)]), (0,255,0), 3)
            for pt in pts_int:
                cv2.circle(annotated, tuple(pt), 5, (0,255,0), -1)
            x, y = pts_int[0]
            label = txt if txt else "QR"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            ty = max(th+8, y-8)
            cv2.rectangle(annotated, (x, ty-th-6), (x+tw+8, ty+2), (0,180,0), -1)
            cv2.putText(annotated, label, (x+4, ty-2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,0), 2, cv2.LINE_AA)
    if all_texts:
        add_qr_log("snapshot", all_texts)
    codes = [{"data": t, "type": "QR Code"} for t in all_texts]
    return jsonify({
        "ok": True,
        "image_b64": frame_to_b64(annotated),
        "codes": codes,
        "qr_found": len(all_texts) > 0,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }), 200

@app.route("/export_csv", methods=["GET"])
def route_export_csv():
    count = export_csv()
    if count < 0:
        return jsonify({"ok": False, "error": "Export failed"}), 500
    try:
        with open(CSV_FILE, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        fname = f"qr_log_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        resp = make_response(content)
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
        return resp
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "No data to export"}), 404

@app.route("/clear_log", methods=["POST"])
def route_clear_log():
    global qr_read_log
    qr_read_log = []
    log("[CSV] QR log cleared")
    return jsonify({"ok": True}), 200

# ── AI Routes ──
@app.route("/ai/load", methods=["POST"])
def ai_load():
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or AI_MODEL_PATH).strip()
    ok   = load_yolo_model(path)
    return jsonify({
        "ok":     ok,
        "loaded": yolo_model_loaded,
        "error":  yolo_model_error,
    }), 200

@app.route("/ai/config", methods=["POST"])
def ai_config():
    global yolo_conf, yolo_iou, yolo_imgsz
    body = request.get_json(silent=True) or {}
    if "conf"  in body: yolo_conf  = float(body["conf"])
    if "iou"   in body: yolo_iou   = float(body["iou"])
    if "imgsz" in body: yolo_imgsz = int(body["imgsz"])
    return jsonify({"ok": True}), 200

@app.route("/ai/status", methods=["GET"])
def ai_status():
    with yolo_lock:
        dets     = list(yolo_latest_dets)
        fps      = yolo_fps
        ai_log   = list(yolo_log)
        log_rows = len(yolo_full_log)
    return jsonify({
        "ok":          True,
        "loaded":      yolo_model_loaded,
        "task":        (yolo_model.task if (yolo_model and hasattr(yolo_model,"task")) else "detect"),
        "fps":         fps,
        "obj_count":   len(dets),
        "detections":  dets,
        "mode_active": detect_mode == "ai",
        "camera_ok":   camera_ok,
        "error":       yolo_model_error,
        "ai_log":      ai_log,
        "log_rows":    log_rows,
    }), 200

@app.route("/ai/snapshot", methods=["GET"])
def ai_snapshot():
    frame = get_latest_raw_frame()
    if frame is None:
        return jsonify({"ok": False, "image_b64": "", "detections": [],
                        "error": "No frame"}), 200
    annotated, dets = run_yolo_on_frame(frame)
    return jsonify({
        "ok":         True,
        "image_b64":  frame_to_b64(annotated),
        "detections": dets,
        "count":      len(dets),
        "timestamp":  time.strftime("%Y-%m-%d %H:%M:%S"),
    }), 200

@app.route("/ai/export_csv", methods=["GET"])
def ai_export_csv():
    with yolo_lock:
        rows = list(yolo_full_log)
    if not rows:
        return jsonify({"ok": False, "error": "ยังไม่มีข้อมูล AI detection"}), 404
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["timestamp", "fps", "class", "confidence",
                    "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"],
        lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    csv_content = buf.getvalue()
    fname = f"ai_detection_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    resp  = make_response(csv_content)
    resp.headers["Content-Type"]        = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
    log(f"[AI CSV] export {len(rows)} rows -> {fname}")
    return resp

@app.route("/ai/clear_log", methods=["POST"])
def ai_clear_log():
    global yolo_full_log, yolo_log
    with yolo_lock:
        cleared = len(yolo_full_log)
        yolo_full_log = []
        yolo_log      = []
    log(f"[AI CSV] log cleared ({cleared} rows removed)")
    return jsonify({"ok": True, "cleared": cleared}), 200

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    import os
    log("Starting RTSP-from-Pi Motion / QR / YOLO11 AI server...")
    log(f"RTSP source = {RTSP_URL}")

    if os.path.exists(AI_MODEL_PATH):
        load_yolo_model(AI_MODEL_PATH)
    else:
        log(f"[AI] ไม่พบ {AI_MODEL_PATH} — โหลดได้จากปุ่ม '🤖 AI Detection' ใน UI")

    threading.Thread(target=camera_loop,   daemon=True).start()
    threading.Thread(target=realtime_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)