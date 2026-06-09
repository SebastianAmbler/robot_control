
# from flask import Flask, jsonify, Response, render_template_string, request
# import csv
# from flask_cors import CORS   # pip install flask-cors
# import cv2
# import base64
# import time
# import threading
# from urllib.parse import quote
# import numpy as np

# app = Flask(__name__)
# CORS(app)   # อนุญาต Browser ต่างต้นทางเรียก API ได้

# # =========================================================
# # CONFIG
# # =========================================================
# USER = "admin"
# PASSWORD = "admin"
# IP = "192.168.0.101"
# PATH = "live"

# pw = quote(PASSWORD)
# RTSP_URL = f"rtsp://{USER}:{pw}@{IP}:554/{PATH}"
# VIEW_WIDTH  = 960
# # VIEW_HEIGHT = 720

# # ===== motion config =====
# BLACK_V_MAX   = 70
# BLACK_S_MAX   = 90
# MIN_BLOB_AREA = 120
# MAX_BLOB_AREA = 12000

# # ===== target lock config =====
# LOCK_MAX_DIST  = 35.0
# LOCK_LOST_LIMIT = 8
# ROI_PAD        = 20
# QR_ROI_MIN_SIZE = 60
# CENTER_X_TOL   = 220
# CENTER_AREA_WEIGHT  = 0.002
# LOCK_CENTER_WEIGHT  = 0.25

# # ===== qr config =====
# DEBOUNCE_SEC = 2
# QR_LOG_MAX   = 20

# # ===== csv config =====
# CSV_FILE = "qr_log.csv"   # ← เปลี่ยน path ได้

# # =========================================================
# # GLOBAL STATE
# # =========================================================
# latest_raw_frame     = None
# latest_display_frame = None
# frame_lock = threading.Lock()

# camera_ok   = False
# detect_mode = "motion"   # motion | qr | both

# # qr state
# last_qr       = ""
# last_qr_time  = 0
# latest_qr_found = False
# latest_qr_text  = ""
# latest_qr_all   = []
# qr_realtime_enabled = False   # True เฉพาะตอนกด SCAN

# # qr read log
# qr_read_log = []

# # motion state
# motion_found  = False
# motion_center = None
# motion_box    = None

# # target lock state
# locked_target  = None
# lock_lost_count = 0

# # =========================================================
# # UI (ไม่เปลี่ยน)
# # =========================================================
# INDEX_HTML = """
# <!doctype html>
# <html>
# <head>
#   <meta charset="utf-8">
#   <title>Motion Lock + QR ROI UI</title>
#   <style>
#     /* ── layout ── */
#     html,body{height:100%;margin:0;padding:0;font-family:Arial,sans-serif;background:#111;color:#eee;overflow:hidden}
#     .wrap{height:100vh;display:flex;flex-direction:column;padding:10px 14px;box-sizing:border-box;gap:0;}
#     .title{font-size:18px;font-weight:700;margin-bottom:8px;flex-shrink:0;}
#     .row{display:grid;grid-template-columns:1.2fr 0.8fr;gap:12px;flex:1;min-height:0;overflow:hidden;}
#     .card{background:#1b1b1b;border:1px solid #333;border-radius:10px;padding:10px;
#           display:flex;flex-direction:column;min-height:0;overflow:hidden;}
#     /* ── video ── */
#     .imgbox{width:100%;flex:1;min-height:0;background:#000;border-radius:8px;overflow:hidden;}
#     img{width:100%;height:100%;object-fit:contain;display:block}
#     /* ── buttons ── */
#     .buttons{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;flex-shrink:0;}
#     button{border:none;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;cursor:pointer}
#     .primary{background:#28a745;color:white}
#     .warn{background:#ff9800;color:#111}
#     .danger{background:#e53935;color:white}
#     .secondary{background:#2f2f2f;color:#fff}
#     /* ── status info ── */
#     .info{line-height:1.6;font-size:12px;flex-shrink:0;margin-bottom:6px;}
#     .label{color:#aaa;display:inline-block;min-width:110px;vertical-align:top}
#     .ok{color:#57d957;font-weight:700}
#     .bad{color:#ff6b6b;font-weight:700}
#     .warntext{color:#ffc14d;font-weight:700}
#     .qrtext{font-size:15px;font-weight:700;color:#57d957;word-break:break-word}
#     /* ── QR log — กินพื้นที่ที่เหลือทั้งหมด ── */
#     h3{margin:4px 0;font-size:12px;flex-shrink:0;}
#     .log-wrap{flex:1;min-height:0;display:flex;flex-direction:column;}
#     .log{background:#0d0d0d;border:1px solid #2c2c2c;border-radius:8px;padding:8px;
#          flex:1;min-height:0;overflow-y:auto;
#          white-space:pre-wrap;font-family:Consolas,monospace;font-size:12px}
#     ul{margin:4px 0 0 16px;padding:0}
#     li{margin-bottom:3px;font-size:12px;}
#   </style>
# </head>
# <body>
# <div class="wrap">
#   <div class="title">Motion Lock + QR ROI UI</div>
#   <!-- Live feed — แสดงเฉพาะ Motion mode -->
#   <div id="liveFeedWrap" style="display:none;flex-shrink:0;height:240px;
#        background:#000;border-radius:10px;overflow:hidden;margin-bottom:8px;border:1px solid #333;">
#     <img src="/video_feed" style="width:100%;height:100%;object-fit:contain;">
#   </div>

#   <div class="row" style="grid-template-columns:1fr;">
#     <div class="card">
#       <div class="buttons">
#         <button class="warn"      onclick="setMode('motion')">Motion Mode</button>
#         <button class="primary"   onclick="setMode('qr')">QR Mode</button>
#         <button class="secondary" onclick="setMode('both')" style="background:#1a3355;color:#7dd3ff;">&#9641; QR Realtime</button>
#         <button class="secondary" onclick="ping()">Refresh Status</button>
#         <button onclick="scanQR()" id="scanBtn"
#             style="background:#005533;color:#57ff9a;border:none;border-radius:8px;
#                    padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer;">
#             &#9641; SCAN QR
#         </button>
#         <button class="secondary" onclick="exportCSV()" style="background:#1a3a5c;color:#57d9ff;font-weight:700;">&#11015; Export CSV</button>
#         <button class="danger"    onclick="clearLog()">&#10005; Clear Log</button>
#       </div>
#       <!-- Snapshot result -->
#       <div id="snapBox" style="display:none;margin-bottom:8px;flex-shrink:0;">
#         <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
#           <span style="color:#57ff9a;font-weight:700;font-size:12px;">&#9641; SNAPSHOT</span>
#           <span id="snapResultLabel" style="font-size:11px;color:#aaa;"></span>
#           <button onclick="clearSnap()"
#               style="margin-left:auto;background:none;border:1px solid #444;color:#aaa;
#                      border-radius:6px;padding:2px 8px;font-size:10px;cursor:pointer;">✕</button>
#         </div>
#         <img id="snapImg" style="width:100%;max-height:420px;object-fit:contain;
#              border-radius:6px;border:1px solid #333;background:#000;">
#         <div id="snapCodes" style="margin-top:4px;font-size:11px;color:#57ff9a;
#              font-family:Consolas,monospace;word-break:break-all;"></div>
#       </div>
#       <div class="info">
#         <div><span class="label">Camera:</span>       <span id="cameraStatus">-</span></div>
#         <div><span class="label">Mode:</span>         <span id="modeStatus">-</span></div>
#         <div><span class="label">Motion:</span>       <span id="motionStatus">-</span></div>
#         <div><span class="label">Locked Target:</span><span id="lockStatus">-</span></div>
#         <div><span class="label">Motion Center:</span><span id="motionCenter">-</span></div>
#         <div><span class="label">QR Found:</span>     <span id="qrFoundStatus">-</span></div>
#         <div><span class="label">Main QR:</span>      <span id="qrText" class="qrtext">-</span></div>
#         <div><span class="label">All QR:</span>       <span id="allQrText">-</span></div>
#         <div><span class="label">Timestamp:</span>    <span id="timeStatus">-</span></div>
#       </div>
#       <h3>QR Read Log</h3>
#       <div class="log-wrap">
#         <div id="logBox" class="log"></div>
#       </div>
#     </div>
#   </div>
# </div>
# <script>
# function renderAllQr(items){
#   const box = document.getElementById("allQrText");
#   if(!items||items.length===0){box.innerHTML="-";return;}
#   box.innerHTML="<ul>"+items.map(x=>"<li>"+String(x)+"</li>").join("")+"</ul>";
# }
# function renderQrLog(items){
#   const box=document.getElementById("logBox");
#   if(!items||items.length===0){box.textContent="No QR log yet";return;}
#   box.textContent=items.map(item=>{
#     const values=(item.values||[]).join(", ");
#     return `[${item.time}] (${item.mode}) ${values}`;
#   }).join("\\n");
# }
# async function ping(){
#   try{
#     const r=await fetch("/status");
#     const j=await r.json();
#     document.getElementById("cameraStatus").textContent=j.camera_ok?"CONNECTED":"NOT READY";
#     document.getElementById("cameraStatus").className=j.camera_ok?"ok":"bad";
#     document.getElementById("modeStatus").textContent=(j.mode||"-").toUpperCase();
#     document.getElementById("motionStatus").textContent=j.motion_found?"TRACKING":"NO MOTION";
#     document.getElementById("motionStatus").className=j.motion_found?"ok":"warntext";
#     document.getElementById("lockStatus").textContent=j.locked?"LOCKED":"UNLOCKED";
#     document.getElementById("lockStatus").className=j.locked?"ok":"bad";
#     document.getElementById("motionCenter").textContent=j.motion_center?`${j.motion_center[0]}, ${j.motion_center[1]}`:"-";
#     document.getElementById("qrFoundStatus").textContent=j.qr_found?"YES":"NO";
#     document.getElementById("qrFoundStatus").className=j.qr_found?"ok":"bad";
#     document.getElementById("qrText").textContent=j.qr||"-";
#     renderAllQr(j.all_qr||[]);
#     renderQrLog(j.qr_log||[]);
#     document.getElementById("timeStatus").textContent=j.timestamp||"-";
#     // sync live feed visibility
#     const feed = document.getElementById('liveFeedWrap');
#     if (feed) {
#       const m = (j.mode||"").toLowerCase();
#       feed.style.display = (m==='motion'||m==='both') ? 'block' : 'none';
#     }
#   }catch(e){console.log("Status error:",e);}
# }
# async function setMode(mode){
#   try{
#     const r=await fetch("/mode",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:mode})});
#     const j=await r.json();
#     console.log("Mode ->",j.mode);
#     // แสดง live feed เฉพาะ motion / both
#     const feed = document.getElementById('liveFeedWrap');
#     if (feed) feed.style.display = (mode==='motion'||mode==='both') ? 'block' : 'none';
#     ping();
#   }catch(e){console.log("Set mode error:",e);}
# }
# setInterval(ping,250);
# window.onload=function(){ping();}

# async function scanQR() {
#   const btn = document.getElementById('scanBtn');
#   if (btn) { btn.textContent = '⏳ Scanning...'; btn.disabled = true; }
#   try {
#     const r = await fetch('/scan_qr');
#     const d = await r.json();

#     // แสดงภาพ
#     const box = document.getElementById('snapBox');
#     const img = document.getElementById('snapImg');
#     const lbl = document.getElementById('snapResultLabel');
#     const codes = document.getElementById('snapCodes');
#     if (box) box.style.display = 'block';
#     if (img && d.image_b64) img.src = 'data:image/jpeg;base64,' + d.image_b64;

#     // แสดงผล QR
#     const codeList = (d.codes || []).map(c => typeof c === 'object' ? c.data : c).filter(Boolean);
#     if (codeList.length > 0) {
#       if (lbl)   lbl.textContent = codeList.length + ' code(s) found';
#       if (codes) codes.innerHTML = codeList.map(c => '• ' + c).join('<br>');
#     } else {
#       if (lbl)   lbl.textContent = 'No QR found';
#       if (codes) codes.textContent = '';
#     }
#     ping();
#   } catch(e) {
#     alert('Scan error: ' + e);
#   } finally {
#     if (btn) { btn.textContent = '⬡ SCAN QR'; btn.disabled = false; }
#   }
# }

# function clearSnap() {
#   const box = document.getElementById('snapBox');
#   if (box) box.style.display = 'none';
# }

# async function exportCSV(){
#   try{
#     const r = await fetch("/export_csv");
#     if(!r.ok){
#       const j = await r.json().catch(()=>({}));
#       alert("Export failed: " + (j.error || r.status));
#       return;
#     }
#     const blob = await r.blob();
#     const url  = URL.createObjectURL(blob);
#     const a    = document.createElement("a");
#     a.href     = url;
#     // filename จาก Content-Disposition header
#     const cd   = r.headers.get("Content-Disposition") || "";
#     const m    = cd.match(/filename=([^;]+)/);
#     a.download = m ? m[1] : "qr_log.csv";
#     document.body.appendChild(a);
#     a.click();
#     document.body.removeChild(a);
#     URL.revokeObjectURL(url);
#   }catch(e){ alert("Export error: "+e); }
# }

# async function clearLog(){
#   if(!confirm("Clear QR log?")) return;
#   try{
#     const r = await fetch("/clear_log",{method:"POST"});
#     const j = await r.json();
#     if(j.ok){ document.getElementById("logBox").textContent="No QR log yet"; ping(); }
#     else alert("Error: "+j.error);
#   }catch(e){ alert("Error: "+e); }
# }
# </script>
# </body>
# </html>
# """

# # =========================================================
# # HELPERS
# # =========================================================
# def log(msg):
#     print(f"[{time.strftime('%H:%M:%S')}] {msg}")


# def add_qr_log(mode, qr_list):
#     global qr_read_log
#     if not qr_list:
#         return
#     entry = {
#         "time":   time.strftime("%H:%M:%S"),
#         "mode":   mode,
#         "values": qr_list,
#     }
#     qr_read_log.insert(0, entry)
#     qr_read_log = qr_read_log[:QR_LOG_MAX]


# def export_csv():
#     """Export QR log ทั้งหมดเป็น CSV file บนเครื่อง Windows"""
#     try:
#         with open(CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
#             writer = csv.writer(f)
#             writer.writerow(["timestamp", "qr_data"])
#             for entry in reversed(qr_read_log):   # เรียงเวลาจากเก่าไปใหม่
#                 ts = entry.get("time", "")
#                 for val in entry.get("values", []):
#                     writer.writerow([ts, val])
#         count = sum(len(e.get("values",[])) for e in qr_read_log)
#         log(f"[CSV] exported {count} row(s) → {CSV_FILE}")
#         return count
#     except Exception as e:
#         log(f"[CSV] export error: {e}")
#         return -1


# def get_latest_raw_frame():
#     with frame_lock:
#         if latest_raw_frame is None:
#             return None
#         return latest_raw_frame.copy()


# def set_latest_display_frame(frame):
#     global latest_display_frame
#     with frame_lock:
#         latest_display_frame = frame.copy()


# def get_latest_display_frame():
#     with frame_lock:
#         if latest_display_frame is not None:
#             return latest_display_frame.copy()
#         if latest_raw_frame is not None:
#             return latest_raw_frame.copy()
#         return None


# def dedupe_preserve_order(items):
#     out, seen = [], set()
#     for x in items:
#         key = (x or "").strip()
#         if not key:
#             continue
#         if key not in seen:
#             seen.add(key)
#             out.append(key)
#     return out


# def polygon_area(pts):
#     if pts is None:
#         return 0.0
#     p = pts.astype(np.float32).reshape(-1, 2)
#     return abs(cv2.contourArea(p))


# def draw_qr_box_and_text(img, points, qr_text, color=(0, 255, 0)):
#     if points is None:
#         return img
#     pts = points.astype(int).reshape(-1, 2)
#     if len(pts) >= 4:
#         for i in range(len(pts)):
#             p1 = tuple(pts[i])
#             p2 = tuple(pts[(i + 1) % len(pts)])
#             cv2.line(img, p1, p2, color, 2)
#         x, y = pts[0]
#         label  = f"QR: {qr_text}" if qr_text else "QR"
#         text_y = max(30, y - 10)
#         cv2.putText(img, label, (x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
#     return img


# def distance(p1, p2):
#     a = np.array(p1, dtype=np.float32)
#     b = np.array(p2, dtype=np.float32)
#     return float(np.linalg.norm(a - b))


# def frame_to_b64(frame):
#     """แปลง OpenCV frame เป็น base64 JPEG string"""
#     ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
#     if not ok:
#         return ""
#     return base64.b64encode(buf.tobytes()).decode("utf-8")


# # =========================================================
# # QR DETECTION IN ROI ONLY
# # =========================================================
# def preprocess_candidates(img):
#     candidates = [("color", img)]
#     gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
#     candidates.append(("gray", gray))
#     blur  = cv2.GaussianBlur(gray, (0, 0), 2)
#     sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
#     candidates.append(("sharp", sharp))
#     return candidates


# def detect_qr_in_roi(frame, roi_rect, annotated):
#     global latest_qr_found, latest_qr_text, latest_qr_all, last_qr, last_qr_time

#     resized = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))
#     H, W = resized.shape[:2]
#     x, y, w, h = roi_rect

#     x1 = max(0, x - ROI_PAD);  y1 = max(0, y - ROI_PAD)
#     x2 = min(W, x + w + ROI_PAD); y2 = min(H, y + h + ROI_PAD)

#     roi = resized[y1:y2, x1:x2].copy()
#     if roi.size == 0 or roi.shape[0] < QR_ROI_MIN_SIZE or roi.shape[1] < QR_ROI_MIN_SIZE:
#         latest_qr_found = False; latest_qr_text = ""; latest_qr_all = []
#         return annotated

#     detector  = cv2.QRCodeDetector()
#     all_texts = []
#     all_boxes = []

#     for _, candidate in preprocess_candidates(roi):
#         try:
#             retval, decoded_info, points_multi, _ = detector.detectAndDecodeMulti(candidate)
#             if retval and points_multi is not None and len(points_multi) > 0:
#                 for qr_text, pts in zip(decoded_info, points_multi):
#                     qr_text = (qr_text or "").strip()
#                     if pts is not None and len(pts) > 0:
#                         pts2 = pts.copy()
#                         pts2[..., 0] += x1; pts2[..., 1] += y1
#                         all_boxes.append((pts2, qr_text))
#                     if qr_text:
#                         all_texts.append(qr_text)
#         except Exception:
#             pass
#         try:
#             data, points, _ = detector.detectAndDecode(candidate)
#             data = (data or "").strip()
#             if points is not None and len(points) > 0:
#                 pts2 = points.copy()
#                 pts2[..., 0] += x1; pts2[..., 1] += y1
#                 all_boxes.append((pts2, data))
#             if data:
#                 all_texts.append(data)
#         except Exception:
#             pass

#     all_texts = dedupe_preserve_order(all_texts)

#     best_text = ""; best_area = -1
#     for pts, txt in all_boxes:
#         annotated = draw_qr_box_and_text(annotated, pts, txt if txt else "QR", color=(0, 255, 0))
#         area = polygon_area(pts)
#         if txt and area > best_area:
#             best_area = area; best_text = txt

#     latest_qr_found = len(all_texts) > 0
#     latest_qr_text  = best_text if best_text else (all_texts[0] if all_texts else "")
#     latest_qr_all   = all_texts

#     if latest_qr_text:
#         now = time.time()
#         duplicated = (latest_qr_text == last_qr and (now - last_qr_time) < DEBOUNCE_SEC)
#         if not duplicated:
#             last_qr = latest_qr_text; last_qr_time = now
#             add_qr_log(detect_mode, all_texts)

#     return annotated


# # =========================================================
# # MOTION LOCK TARGET
# # =========================================================
# def choose_locked_target(candidates):
#     global locked_target, lock_lost_count

#     if not candidates:
#         lock_lost_count += 1
#         if lock_lost_count > LOCK_LOST_LIMIT:
#             locked_target = None
#         return None

#     frame_center = (VIEW_WIDTH / 2.0, VIEW_HEIGHT / 2.0)

#     if locked_target is None:
#         lock_lost_count = 0
#         best = None; best_score = 1e9
#         for c in candidates:
#             d_center = distance(c["center"], frame_center)
#             score = d_center - (c["area"] * CENTER_AREA_WEIGHT)
#             if score < best_score:
#                 best_score = score; best = c
#         locked_target = best
#         return locked_target

#     old_center = locked_target["center"]
#     nearest = None; best_score = 1e9
#     for c in candidates:
#         d_old    = distance(old_center, c["center"])
#         d_center = distance(c["center"], frame_center)
#         score    = d_old + (LOCK_CENTER_WEIGHT * d_center)
#         if score < best_score:
#             best_score = score; nearest = c

#     if nearest is not None and distance(old_center, nearest["center"]) <= LOCK_MAX_DIST:
#         locked_target = nearest; lock_lost_count = 0
#         return locked_target

#     lock_lost_count += 1
#     if lock_lost_count > LOCK_LOST_LIMIT:
#         locked_target = None; return None
#     return locked_target


# def detect_black_motion_and_lock(frame, annotated):
#     global motion_found, motion_center, motion_box, locked_target

#     img = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))
#     hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

#     lower = np.array([0, 0, 0],              dtype=np.uint8)
#     upper = np.array([180, BLACK_S_MAX, BLACK_V_MAX], dtype=np.uint8)

#     mask = cv2.inRange(hsv, lower, upper)
#     mask = cv2.GaussianBlur(mask, (5, 5), 0)
#     _, mask = cv2.threshold(mask, 40, 255, cv2.THRESH_BINARY)
#     mask = cv2.erode(mask, None, iterations=1)
#     mask = cv2.dilate(mask, None, iterations=2)

#     contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

#     candidates      = []
#     frame_center_x  = VIEW_WIDTH / 2.0

#     for c in contours:
#         area = cv2.contourArea(c)
#         if area < MIN_BLOB_AREA or area > MAX_BLOB_AREA:
#             continue
#         x, y, w, h = cv2.boundingRect(c)
#         cx = x + w / 2.0; cy = y + h / 2.0
#         if abs(cx - frame_center_x) > CENTER_X_TOL:
#             continue
#         candidates.append({"area": area, "rect": (x, y, w, h), "center": (cx, cy)})

#     if not candidates:
#         for c in contours:
#             area = cv2.contourArea(c)
#             if area < MIN_BLOB_AREA or area > MAX_BLOB_AREA:
#                 continue
#             x, y, w, h = cv2.boundingRect(c)
#             cx = x + w / 2.0; cy = y + h / 2.0
#             candidates.append({"area": area, "rect": (x, y, w, h), "center": (cx, cy)})

#     target = choose_locked_target(candidates)

#     if target is None:
#         motion_found = False; motion_center = None; motion_box = None
#         return annotated, None

#     x, y, w, h = target["rect"]; cx, cy = target["center"]
#     motion_found  = True
#     motion_center = (int(cx), int(cy))
#     motion_box    = (x, y, w, h)

#     cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
#     cv2.circle(annotated, (int(cx), int(cy)), 4, (0, 255, 0), -1)
#     cv2.putText(annotated, "LOCKED TARGET", (x, max(30, y - 10)),
#                 cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)

#     return annotated, motion_box


# # =========================================================
# # CAMERA THREAD
# # =========================================================
# def camera_loop():
#     global latest_raw_frame, camera_ok

#     while True:
#         cap = None
#         try:
#             log(f"camera_loop: opening {RTSP_URL}")
#             cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
#             try:
#                 cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
#             except Exception:
#                 pass

#             if not cap.isOpened():
#                 camera_ok = False
#                 log("camera_loop: open failed, retry in 2s")
#                 time.sleep(2)
#                 continue

#             camera_ok = True
#             log("camera_loop: connected")

#             while True:
#                 ok, frame = cap.read()
#                 if not ok or frame is None:
#                     camera_ok = False
#                     log("camera_loop: read failed, reconnecting...")
#                     break
#                 with frame_lock:
#                     latest_raw_frame = frame.copy()
#                 camera_ok = True
#                 time.sleep(0.01)

#         except Exception as e:
#             camera_ok = False
#             log(f"camera_loop: exception {e}")

#         try:
#             if cap is not None:
#                 cap.release()
#         except Exception:
#             pass
#         time.sleep(2)


# # =========================================================
# # REALTIME DETECTION THREAD
# # =========================================================
# def realtime_loop():
#     global motion_found, motion_center, motion_box
#     global latest_qr_found, latest_qr_text, latest_qr_all

#     while True:
#         try:
#             frame = get_latest_raw_frame()
#             if frame is None:
#                 time.sleep(0.03); continue

#             annotated = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))
#             roi_rect  = None

#             if detect_mode == "motion":
#                 # motion only — track target, no QR
#                 annotated, roi_rect = detect_black_motion_and_lock(frame, annotated)
#                 latest_qr_found = False; latest_qr_text = ""; latest_qr_all = []

#             elif detect_mode == "qr":
#                 # QR mode — detect เฉพาะตอนกด SCAN
#                 motion_found = False; motion_center = None; motion_box = None
#                 if qr_realtime_enabled:
#                     full_rect = (0, 0, VIEW_WIDTH, VIEW_HEIGHT)
#                     annotated = detect_qr_in_roi(frame, full_rect, annotated)
#                 else:
#                     latest_qr_found = False; latest_qr_text = ""; latest_qr_all = []

#             elif detect_mode == "both":
#                 # both = QR realtime เต็มเฟรม (ไม่ใช้ motion)
#                 motion_found = False; motion_center = None; motion_box = None
#                 full_rect = (0, 0, VIEW_WIDTH, VIEW_HEIGHT)
#                 annotated = detect_qr_in_roi(frame, full_rect, annotated)

#             else:
#                 motion_found = False; motion_center = None; motion_box = None
#                 latest_qr_found = False; latest_qr_text = ""; latest_qr_all = []

#             set_latest_display_frame(annotated)
#             time.sleep(0.03)

#         except Exception as e:
#             log(f"realtime_loop error: {e}")
#             time.sleep(0.1)


# # =========================================================
# # VIDEO STREAM
# # =========================================================
# def generate_video():
#     while True:
#         frame = get_latest_display_frame()
#         if frame is None:
#             time.sleep(0.03); continue
#         ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
#         if not ok:
#             continue
#         yield (
#             b"--frame\r\n"
#             b"Content-Type: image/jpeg\r\n\r\n" +
#             jpeg.tobytes() +
#             b"\r\n"
#         )


# # =========================================================
# # ROUTES
# # =========================================================
# @app.route("/")
# def index():
#     return render_template_string(INDEX_HTML)


# @app.route("/video_feed")
# def video_feed():
#     return Response(
#         generate_video(),
#         mimetype="multipart/x-mixed-replace; boundary=frame"
#     )


# @app.route("/mode", methods=["POST"])
# def set_mode():
#     global detect_mode
#     body = request.get_json(silent=True) or {}
#     mode = (body.get("mode") or "").strip().lower()
#     if mode not in ("motion", "qr", "both"):
#         return jsonify({"ok": False, "error": "invalid_mode"}), 200
#     detect_mode = mode
#     log(f"mode changed -> {detect_mode}")
#     return jsonify({"ok": True, "mode": detect_mode}), 200


# @app.route("/status", methods=["GET"])
# def status():
#     return jsonify({
#         "ok":           True,
#         "camera_ok":    camera_ok,
#         "mode":         detect_mode,
#         "motion_found": motion_found,
#         "motion_center": motion_center,
#         "locked":       locked_target is not None,
#         "qr_found":     latest_qr_found,
#         "qr":           latest_qr_text,
#         "all_qr":       latest_qr_all,
#         "qr_log":       qr_read_log,
#         "timestamp":    time.strftime("%Y-%m-%d %H:%M:%S"),
#     }), 200


# # =========================================================
# # /export_csv — Export QR log เป็น CSV แล้ว download
# # =========================================================
# @app.route("/export_csv", methods=["GET"])
# def route_export_csv():
#     count = export_csv()
#     if count < 0:
#         return jsonify({"ok": False, "error": "Export failed"}), 500

#     # ส่งไฟล์ให้ Browser download
#     try:
#         with open(CSV_FILE, 'r', encoding='utf-8-sig') as f:
#             content = f.read()
#         from flask import make_response
#         fname = f"qr_log_{time.strftime('%Y%m%d_%H%M%S')}.csv"
#         resp  = make_response(content)
#         resp.headers["Content-Type"]        = "text/csv; charset=utf-8"
#         resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
#         return resp
#     except FileNotFoundError:
#         return jsonify({"ok": False, "error": "No data to export"}), 404


# @app.route("/clear_log", methods=["POST"])
# def route_clear_log():
#     global qr_read_log
#     qr_read_log = []
#     log("[CSV] QR log cleared")
#     return jsonify({"ok": True}), 200


# # =========================================================
# # /scan_qr# =========================================================
# # /scan_qr  — endpoint ใหม่สำหรับ Browser Control Panel
# # =========================================================
# @app.route("/scan_qr", methods=["GET"])
# def scan_qr():
#     """
#     ถ่าย snapshot จากกล้องปัจจุบัน
#     detect QR ในภาพทันที → วาดกรอบเขียว + ข้อความ
#     return: image_b64, codes, qr_found
#     """
#     global qr_realtime_enabled
#     qr_realtime_enabled = True   # เปิด detect ชั่วคราว

#     # ดึง raw frame ล่าสุด
#     frame = get_latest_raw_frame()
#     if frame is None:
#         return jsonify({
#             "ok": False,
#             "image_b64": "",
#             "codes": [],
#             "qr_found": False,
#             "error": "No frame — กล้องยังไม่พร้อม",
#         }), 200

#     # resize ให้เหมาะสม
#     img = cv2.resize(frame, (VIEW_WIDTH, VIEW_HEIGHT))
#     annotated = img.copy()

#     # ── detect QR ในทุกโซน ──────────────────────────────
#     detector  = cv2.QRCodeDetector()
#     all_texts = []
#     all_boxes = []

#     for _, candidate in preprocess_candidates(img):
#         try:
#             retval, decoded_info, points_multi, _ = detector.detectAndDecodeMulti(candidate)
#             if retval and points_multi is not None:
#                 for qr_text, pts in zip(decoded_info, points_multi):
#                     qr_text = (qr_text or "").strip()
#                     if pts is not None and len(pts) > 0:
#                         all_boxes.append((pts, qr_text))
#                     if qr_text:
#                         all_texts.append(qr_text)
#         except Exception:
#             pass
#         try:
#             data, points, _ = detector.detectAndDecode(candidate)
#             data = (data or "").strip()
#             if points is not None and len(points) > 0:
#                 all_boxes.append((points, data))
#             if data:
#                 all_texts.append(data)
#         except Exception:
#             pass

#     all_texts = dedupe_preserve_order(all_texts)

#     # ── วาดกรอบเขียว + ข้อความ ──────────────────────────
#     for pts, txt in all_boxes:
#         pts_int = pts.astype(int).reshape(-1, 2)
#         if len(pts_int) >= 4:
#             # วาดกรอบ
#             for i in range(len(pts_int)):
#                 p1 = tuple(pts_int[i])
#                 p2 = tuple(pts_int[(i+1) % len(pts_int)])
#                 cv2.line(annotated, p1, p2, (0, 255, 0), 3)
#             # วาดจุดมุม
#             for pt in pts_int:
#                 cv2.circle(annotated, tuple(pt), 5, (0, 255, 0), -1)
#             # ข้อความบนกรอบ
#             x, y = pts_int[0]
#             label = txt if txt else "QR"
#             # กล่องพื้นหลัง
#             (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
#             ty = max(th + 8, y - 8)
#             cv2.rectangle(annotated, (x, ty - th - 6), (x + tw + 8, ty + 2), (0, 180, 0), -1)
#             cv2.putText(annotated, label, (x + 4, ty - 2),
#                         cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)

#     # ── บันทึก log ถ้าพบ ──────────────────────────────────
#     if all_texts:
#         add_qr_log("snapshot", all_texts)

#     # ── encode เป็น base64 ────────────────────────────────
#     img_b64 = frame_to_b64(annotated)

#     codes = [{"data": t, "type": "QR Code"} for t in all_texts]

#     qr_realtime_enabled = False   # ปิด detect หลัง scan เสร็จ

#     return jsonify({
#         "ok":        True,
#         "image_b64": img_b64,
#         "codes":     codes,
#         "qr_found":  len(all_texts) > 0,
#         "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
#     }), 200


# # =========================================================
# # MAIN
# # =========================================================
# if __name__ == "__main__":
#     log("Starting Motion Lock + QR ROI server...")
#     log(f"RTSP_URL = {RTSP_URL}")

#     threading.Thread(target=camera_loop,   daemon=True).start()
#     threading.Thread(target=realtime_loop, daemon=True).start()
#     app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)