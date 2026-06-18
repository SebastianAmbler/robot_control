import cv2
import numpy as np
from pyzbar.pyzbar import decode
from ultralytics import YOLO

# ==========================================
# ⚙️ ตัวแปรปรับแต่ง
# ==========================================
MODEL_PATH = 'QR1.pt'
PADDING_SIZE = 25           # ขยายพื้นที่ Quiet Zone รอบ QR Code (ช่วยให้ PyZbar อ่านง่ายขึ้น)

# ตัวแปรสำหรับจูน AI ตรวจจับ:
CONFIDENCE = 0.2           
IOU_THRESH = 0.45           
IMG_SIZE = 640              

# ตัวแปรสำหรับการหมุนภาพ:
ROTATION_ANGLES = [0, 15, -15, 30, -30 ,45 ,-45 ,60,-60 ,75 ,-75 ,90 ,-90] 
# ==========================================

model = YOLO(MODEL_PATH)
cap = cv2.VideoCapture(0)
WINDOW_NAME = 'Clean Frame QR Scanner'

def rotate_image_safe(image, angle):
    """ฟังก์ชันหมุนภาพต้นฉบับที่สะอาด"""
    if angle == 0: return image
    (h, w) = image.shape[:2]
    (cX, cY) = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D((cX, cY), angle, 1.0)
    cos, sin = np.abs(M[0, 0]), np.abs(M[0, 1])
    nW, nH = int((h * sin) + (w * cos)), int((h * cos) + (w * sin))
    M[0, 2] += (nW / 2) - cX
    M[1, 2] += (nH / 2) - cY
    return cv2.warpAffine(image, M, (nW, nH), borderValue=(255, 255, 255))

while True:
    ret, frame = cap.read() # นี่คือเฟรมต้นฉบับที่ "สะอาด"
    if not ret: break

    height, width = frame.shape[:2]

    # 🌟 ขั้นตอนสำคัญ: ก๊อปปี้เฟรมออกมาเพื่อเอาไว้วาดกรอบแสดงผล "เท่านั้น"
    display_frame = frame.copy() 
    
    # 1. ให้ AI ตรวจจับตำแหน่งบนเฟรมที่ "สะอาด"
    # (หรือจะตรวจบน display_frame ก็ได้ ผลลัพธ์ coordinates จะเหมือนกัน)
    results = model(frame, conf=CONFIDENCE, iou=IOU_THRESH, imgsz=IMG_SIZE, verbose=False)
    
    for result in results:
        for box in result.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            box_w = x2 - x1
            
            # 🌟 วาดกรอบสีแดงแจ้งสถานะลงบนเฟรมสำหรับ "แสดงผล" เท่านั้น
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            
            # --- เตรียมภาพสำหรับการถอดรหัส ---
            
            # คำนวณพื้นที่ตัดภาพ
            crop_y1 = max(0, y1 - PADDING_SIZE)
            crop_y2 = min(height, y2 + PADDING_SIZE)
            crop_x1 = max(0, x1 - PADDING_SIZE)
            crop_x2 = min(width, x2 + PADDING_SIZE)
            
            # 🌟 ตัดภาพ (Crop) จากเฟรม "สะอาด" ตัวต้นฉบับ ไม่ใช่เฟรมที่มีกรอบสี
            crop_img_clean = frame[crop_y1:crop_y2, crop_x1:crop_x2]
            
            if crop_img_clean.size == 0: continue

            # 2. ระบบหมุนภาพและลองอ่าน โดยใช้ภาพที่สะอาด
            qr_text = None
            for angle in ROTATION_ANGLES:
                # หมุนภาพจากต้นฉบับที่สะอาด
                rotated_img_clean = rotate_image_safe(crop_img_clean, angle)
                
                # ลองถอดรหัส (แนะนำให้แปลงเป็น Grayscale เพื่อความแม่นยำพื้นฐาน)
                gray = cv2.cvtColor(rotated_img_clean, cv2.COLOR_BGR2GRAY)
                decoded_objects = decode(gray)
                
                if decoded_objects:
                    for obj in decoded_objects:
                        qr_text = obj.data.decode('utf-8')
                    break 
            
            # 3. แสดงผลเมื่ออ่านสำเร็จ ลงบนเฟรมสำหรับ "แสดงผล"
            if qr_text:
                print(f"สแกนสำเร็จ (มุมเอียง {angle} องศา): {qr_text}")
                
                # เปลี่ยนกรอบสถานะเป็นสีเขียว พร้อมโชว์ข้อความบนภาพที่สะอาด
                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                cv2.rectangle(display_frame, (x1, y1 - 35), (x1 + max(200, box_w), y1), (0, 255, 0), -1)
                cv2.putText(display_frame, qr_text, (x1 + 5, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

    # แสดงผลเฟรมที่มีกรอบและข้อความทับแล้ว (แต่กระบวนการอ่านใช้เฟรม CLEAN)
    cv2.imshow(WINDOW_NAME, display_frame)
    
    # ระบบปิดหน้าต่าง (กด ESC หรือ กากบาท)
    key = cv2.waitKey(1) & 0xFF 
    if key == 27 or cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
        print("กำลังปิดโปรแกรม...")
        break

cap.release()
cv2.destroyAllWindows()