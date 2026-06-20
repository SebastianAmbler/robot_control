/*
 * AMG8833 (GY-AMG8833 / Grid-EYE) -> USB serial streamer
 * Target: Teensy 3.x / 4.x
 *
 * Reads the 8x8 thermal grid and prints 64 comma-separated
 * temperature values (deg C) per line over USB serial.
 * Interpolation + display is handled on the PC side.
 *
 * Libraries (Arduino Library Manager):
 *   - Adafruit AMG88xx
 *   - Adafruit BusIO   (dependency)
 *
 * ---- Wiring (I2C) ----
 *   AMG8833        Teensy
 *     VIN  -> 3.3V
 *     GND  -> GND
 *     SDA  -> 18  (SDA0)
 *     SCL  -> 19  (SCL0)   // same on Teensy LC / 3.x / 4.0 / 4.1
 *
 * Default I2C address is 0x69. If jumpered to 0x68, use amg.begin(0x68).
 */

#include <Wire.h>
#include <Adafruit_AMG88xx.h>

Adafruit_AMG88xx amg;
float pixels[AMG88xx_PIXEL_ARRAY_SIZE];   // 64 floats

void setup() {
  Serial.begin(115200);
  Wire.begin();

  if (!amg.begin()) {            // default address 0x69
    while (1) {                  // halt if sensor not found
      Serial.println("AMG8833 not found - check wiring / address");
      delay(1000);
    }
  }
}

void loop() {
  amg.readPixels(pixels);

  for (int i = 0; i < AMG88xx_PIXEL_ARRAY_SIZE; i++) {
    Serial.print(pixels[i], 2);                       // 2 decimal places
    if (i < AMG88xx_PIXEL_ARRAY_SIZE - 1) Serial.print(',');
  }
  Serial.println();

  delay(100);   // sensor outputs at 10 fps
}
