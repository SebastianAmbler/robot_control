import cv2

from ultralytics import YOLO



def main():

    # 1. โหลดโมเดล AI ของคุณ (ต้องมั่นใจว่าไฟล์ best.pt อยู่ในโฟลเดอร์เดียวกับไฟล์โค้ดนี้)

    print("Loading the AI ​​model...") 

    model = YOLO("A5.pt") # เปลี่ยนชื่อไฟล์ตรงนี้



    # 2. เปิดกล้องเว็บแคม (เลข 0 คือกล้องตัวแรกของเครื่อง, ถ้าใช้กล้อง USB ต่อแยกอาจจะต้องเปลี่ยนเป็น 1 หรือ 2)

    cap = cv2.VideoCapture(0)



    # ตรวจสอบว่ากล้องเปิดสำเร็จหรือไม่

    if not cap.isOpened():

        print("Error!! The camera cannot be turned on.")

        return



    print("Camera opened successfully! (Press 'Q' or 'ESC' to exit)")



    # 3. เริ่มลูปการทำงานเพื่ออ่านภาพจากกล้องแบบเรียลไทม์

    while True:

        # อ่านภาพทีละเฟรมจากกล้อง

        success, frame = cap.read()

        

        if not success:

            print("Failed to capture image from camera.")

            break



        # 4. ส่งภาพให้ AI ตรวจจับป้าย HazMat

        # (เราตั้ง conf=0.5 คือให้ AI แสดงผลก็ต่อเมื่อมันมั่นใจเกิน 10% ขึ้นไป เพื่อลดความผิดพลาด)

        results = model.predict(source=frame, conf=0.1, show=False)



        # 5. วาดกรอบสี่เหลี่ยมและชื่อคลาสทับลงบนภาพต้นฉบับ

        annotated_frame = results[0].plot()



        # 6. แสดงหน้าต่างภาพที่วาดกรอบแล้ว

        cv2.imshow("HazMat AI Detection (Model A2)", annotated_frame)



        # 7. เช็คการกดปุ่ม 'q', 'Q' หรือ 'ESC' เพื่อออกจากโปรแกรม

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q') or key == ord('Q') or key == 27:

            print("Closing the program...")

            break



    # 8. คืนค่าการใช้กล้องและปิดหน้าต่างโปรแกรมทั้งหมด

    cap.release()

    cv2.destroyAllWindows()



if __name__ == "__main__":

    main()