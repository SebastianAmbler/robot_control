#include <PWMServo.h>

// --- Pin Definitions ---
const int PIN_FRONT = 10;
const int PIN_BACK  = 9;
const int PIN_ARM1  = 7;   // IK Shoulder (q1)
const int PIN_ARM2  = 6;   // IK Elbow    (q2)
const int PIN_ARM3  = 5;   // IK Wrist    (q3)
const int PIN_ARM4  = 4;
const int PIN_ARM5  = 3;
const int PIN_MOTOR = 2;

// --- Servo Objects ---
PWMServo servoFront;
PWMServo servoBack;
PWMServo servoArm1;
PWMServo servoArm2;
PWMServo servoArm3;
PWMServo servoArm4;
PWMServo servoArm5;
PWMServo servoMotor;

// Safe starting angle for initialization
const int DEFAULT_ANGLE = 90; 

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000); // Wait up to 3 seconds for Serial

  Serial.println("--- 6-DOF Arm & Servo Tester ---");
  Serial.println("Send commands in format: <PIN> <ANGLE>");
  Serial.println("Example: '7 45' moves PIN_ARM1 to 45 degrees.");
  Serial.println("--------------------------------");

  // Attach servos to pins
  servoFront.attach(PIN_FRONT);
  servoBack.attach(PIN_BACK);
  servoArm1.attach(PIN_ARM1);
  servoArm2.attach(PIN_ARM2);
  servoArm3.attach(PIN_ARM3);
  servoArm4.attach(PIN_ARM4);
  servoArm5.attach(PIN_ARM5);
  servoMotor.attach(PIN_MOTOR);

  // Initialize to a safe center position to prevent sudden jerks
  setAllServos(DEFAULT_ANGLE);
  Serial.println("All servos initialized to 90 degrees.");
}

void loop() {
  // Check if data is available in the Serial buffer
  if (Serial.available() > 0) {
    // Read the first integer (Pin Number)
    int targetPin = Serial.parseInt();
    
    // Read the second integer (Target Angle)
    int targetAngle = Serial.parseInt();

    // Clear the rest of the buffer (newlines, carriage returns)
    while (Serial.available() > 0) {
      Serial.read();
    }

    // Validate the angle constraint (0 to 180 degrees)
    if (targetAngle >= 0 && targetAngle <= 180) {
      moveServo(targetPin, targetAngle);
    } else {
      Serial.println("Error: Angle must be between 0 and 180.");
    }
  }
}

// Function to route the angle command to the correct servo
void moveServo(int pin, int angle) {
  switch (pin) {
    case PIN_FRONT: servoFront.write(angle); break;
    case PIN_BACK:  servoBack.write(angle);  break;
    case PIN_ARM1:  servoArm1.write(angle);  break;
    case PIN_ARM2:  servoArm2.write(angle);  break;
    case PIN_ARM3:  servoArm3.write(angle);  break;
    case PIN_ARM4:  servoArm4.write(angle);  break;
    case PIN_ARM5:  servoArm5.write(angle);  break;
    case PIN_MOTOR: servoMotor.write(angle); break;
    default:
      Serial.print("Error: Pin ");
      Serial.print(pin);
      Serial.println(" is not mapped to a servo.");
      return;
  }
  
  Serial.print("Moved Pin ");
  Serial.print(pin);
  Serial.print(" to ");
  Serial.print(angle);
  Serial.println(" degrees.");
}

// Function to set all servos to a specific angle
void setAllServos(int angle) {
  servoFront.write(angle);
  servoBack.write(angle);
  servoArm1.write(angle);
  servoArm2.write(angle);
  servoArm3.write(angle);
  servoArm4.write(angle);
  servoArm5.write(angle);
  servoMotor.write(angle);
}