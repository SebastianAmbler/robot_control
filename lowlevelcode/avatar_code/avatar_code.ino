#define NUM_CH 6
#define SMOOTH_SIZE 8
#define DEADBAND 7

int analogPins[] = {A1, A2, A3, A4, A5, A6};
int digitalPin = 14;
bool toggle = false;
unsigned long debounceTime = 100;
unsigned long lastMillis = 0;
bool buttonState = LOW;
bool lastFlickerState = LOW;

int buf[NUM_CH][SMOOTH_SIZE] = {0};
int bufIdx = 0;
int prevRaw[NUM_CH] = {-999, -999, -999, -999, -999, -999};

int smoothRead(int ch) {
    buf[ch][bufIdx] = analogRead(analogPins[ch]);
    int sum = 0;
    for (int i = 0; i < SMOOTH_SIZE; i++) sum += buf[ch][i];
    return sum / SMOOTH_SIZE;
}

void setup() {
    Serial.begin(115200);
    pinMode(digitalPin, INPUT_PULLDOWN);
}

void loop() {
    for (int i = 0; i < NUM_CH; i++) {
        int raw = smoothRead(i);
        
        if (abs(raw - prevRaw[i]) >= DEADBAND) {
            prevRaw[i] = raw;
        }
        
        int angle = map(prevRaw[i], 0, 1023, 0, 180);
        Serial.print(angle);
        if (i < NUM_CH - 1) Serial.print(",");
    }
    
    bufIdx = (bufIdx + 1) % SMOOTH_SIZE;
    Serial.print(",");

    bool reading = digitalRead(digitalPin);
    
    if (reading != lastFlickerState) {
        lastMillis = millis();
    }
    
    if ((millis() - lastMillis) > debounceTime) {
        if (reading != buttonState) {
            buttonState = reading;
            if (buttonState == LOW) {
                toggle = !toggle;
            }
        }
    }
    
    lastFlickerState = reading;

    Serial.print(toggle);
    Serial.println();
    delay(50);
}