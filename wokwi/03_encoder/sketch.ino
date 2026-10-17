// [Test 03] Encoder counting (belt position tracking)
// Stand-in for the incremental rotary encoder (#7). KY-040 = A/B phase, button = Z phase (home).
//   D0 : CLK (A phase)
//   D1 : DT  (B phase)
//   D2 : SW  (stands in for the Z phase -> resets position to 0)
//   D3 : PITCH LED, pulses each time the belt moves forward by one part pitch (50mm)
//
// Decoding (state machine, independent of the release order)
//   Idle = 11 (both HIGH, pulled up)
//   First pin to go LOW sets the direction: CLK first = CW(+1), DT first = CCW(-1)
//   A step counts only after passing 00 and returning to 11. Anything else is a glitch.
//
// Serial commands
//   p        : print status
//   r        : reset position and counters
//   m <mm>   : change belt mm per step (e.g. m 2.5)

const uint8_t CLK_GPIO = 1;     // D0
const uint8_t DT_GPIO = 2;      // D1
const uint8_t SW_GPIO = 3;      // D2
const uint8_t PITCH_GPIO = 4;   // D3

const float PITCH_MM = 50.0f;   // Part spacing on the belt (report section 2.2)
const uint32_t PITCH_PULSE_MS = 50;
const uint32_t SW_LOCKOUT_MS = 50;

volatile int32_t position = 0;          // In steps (detents)
volatile uint32_t glitchCount = 0;
volatile uint32_t stepCount = 0;        // Total steps in either direction
volatile uint8_t lastState = 0b11;
volatile int8_t pendingDir = 0;
volatile bool sawBothLow = false;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

float mmPerStep = 5.0f;                 // 10 steps = 1 pitch (50mm)
int32_t lastLoggedPos = 0;
int32_t lastPitchIndex = 0;
uint32_t pitchCount = 0;
bool pitchLedOn = false;
uint32_t pitchLedStartMs = 0;
bool swWasPressed = false;
uint32_t swLastChangeMs = 0;
String inputLine;

void IRAM_ATTR onEncoderEdge() {
  uint8_t state = (digitalRead(CLK_GPIO) << 1) | digitalRead(DT_GPIO);

  portENTER_CRITICAL_ISR(&mux);
  if (state != lastState) {
    if (lastState == 0b11) {
      // Leaving idle: the first pin to drop sets the direction
      if (state == 0b01) pendingDir = +1;        // CLK low first -> CW
      else if (state == 0b10) pendingDir = -1;   // DT low first  -> CCW
      else { pendingDir = 0; glitchCount++; }    // Both at once -> direction unknown
      sawBothLow = false;
    } else if (state == 0b00) {
      sawBothLow = true;
    } else if (state == 0b11) {
      // Back to idle: commit only a complete cycle
      if (pendingDir != 0 && sawBothLow) {
        position += pendingDir;
        stepCount++;
      } else {
        glitchCount++;
      }
      pendingDir = 0;
      sawBothLow = false;
    }
    lastState = state;
  }
  portEXIT_CRITICAL_ISR(&mux);
}

int32_t readPosition() {
  portENTER_CRITICAL(&mux);
  int32_t p = position;
  portEXIT_CRITICAL(&mux);
  return p;
}

int32_t pitchSteps() {
  int32_t steps = (int32_t)lroundf(PITCH_MM / mmPerStep);
  return steps < 1 ? 1 : steps;
}

// Floor division that also works for negative positions
int32_t floorDiv(int32_t a, int32_t b) {
  int32_t q = a / b;
  return (a % b != 0 && (a < 0) != (b < 0)) ? q - 1 : q;
}

void resetPosition(const char* reason) {
  portENTER_CRITICAL(&mux);
  position = 0;
  portEXIT_CRITICAL(&mux);
  lastLoggedPos = 0;
  lastPitchIndex = 0;
  Serial.printf("[Z] %s -> position 0 (0.0 mm)\n", reason);
}

void updatePosition(uint32_t nowMs) {
  int32_t pos = readPosition();
  if (pos == lastLoggedPos) return;

  int32_t delta = pos - lastLoggedPos;
  Serial.printf("[ENC] %s%ld -> pos %ld step (%.1f mm)\n", delta > 0 ? "+" : "", (long)delta, (long)pos,
                pos * mmPerStep);
  lastLoggedPos = pos;

  int32_t pitchIndex = floorDiv(pos, pitchSteps());
  if (pitchIndex > lastPitchIndex) {
    pitchCount += pitchIndex - lastPitchIndex;
    Serial.printf("[PITCH] pitch #%ld reached (%.1f mm) -> next part slot in place\n", (long)pitchIndex,
                  pitchIndex * PITCH_MM);
    digitalWrite(PITCH_GPIO, HIGH);
    pitchLedOn = true;
    pitchLedStartMs = nowMs;
  } else if (pitchIndex < lastPitchIndex) {
    Serial.printf("[PITCH] moved back to pitch #%ld (reverse, no pulse)\n", (long)pitchIndex);
  }
  lastPitchIndex = pitchIndex;
}

void updatePitchLed(uint32_t nowMs) {
  if (pitchLedOn && nowMs - pitchLedStartMs >= PITCH_PULSE_MS) {
    digitalWrite(PITCH_GPIO, LOW);
    pitchLedOn = false;
  }
}

void updateZButton(uint32_t nowMs) {
  bool pressed = digitalRead(SW_GPIO) == LOW;
  if (pressed == swWasPressed || nowMs - swLastChangeMs < SW_LOCKOUT_MS) return;
  swWasPressed = pressed;
  swLastChangeMs = nowMs;
  if (pressed) resetPosition("Z phase (SW button)");
}

void printStatus() {
  int32_t pos = readPosition();
  portENTER_CRITICAL(&mux);
  uint32_t steps = stepCount;
  uint32_t glitches = glitchCount;
  portEXIT_CRITICAL(&mux);

  Serial.println("--- Encoder status ---");
  Serial.printf("  position     : %ld step (%.1f mm)\n", (long)pos, pos * mmPerStep);
  Serial.printf("  mm per step  : %.2f mm (pitch = %ld steps)\n", mmPerStep, (long)pitchSteps());
  Serial.printf("  total steps  : %lu\n", (unsigned long)steps);
  Serial.printf("  pitch pulses : %lu\n", (unsigned long)pitchCount);
  Serial.printf("  glitches     : %lu\n", (unsigned long)glitches);
}

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd == "p") {
    printStatus();
  } else if (cmd == "r") {
    portENTER_CRITICAL(&mux);
    stepCount = 0;
    glitchCount = 0;
    portEXIT_CRITICAL(&mux);
    pitchCount = 0;
    resetPosition("serial reset");
  } else if (cmd.startsWith("m ")) {
    float mm = cmd.substring(2).toFloat();
    if (mm <= 0 || mm > PITCH_MM) {
      Serial.printf("[CMD] range: 0 < mm <= %.0f\n", PITCH_MM);
      return;
    }
    mmPerStep = mm;
    lastPitchIndex = floorDiv(readPosition(), pitchSteps());
    Serial.printf("[CMD] mm per step -> %.2f (pitch = %ld steps)\n", mmPerStep, (long)pitchSteps());
  } else {
    Serial.printf("[CMD] unknown command: '%s' (p, r, m <mm>)\n", cmd.c_str());
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      handleCommand(inputLine);
      inputLine = "";
    } else {
      inputLine += c;
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(CLK_GPIO, INPUT);       // KY-040 has its own pull-ups
  pinMode(DT_GPIO, INPUT);
  pinMode(SW_GPIO, INPUT_PULLUP);
  pinMode(PITCH_GPIO, OUTPUT);
  digitalWrite(PITCH_GPIO, LOW);

  lastState = (digitalRead(CLK_GPIO) << 1) | digitalRead(DT_GPIO);
  attachInterrupt(digitalPinToInterrupt(CLK_GPIO), onEncoderEdge, CHANGE);
  attachInterrupt(digitalPinToInterrupt(DT_GPIO), onEncoderEdge, CHANGE);

  delay(500);
  Serial.println();
  Serial.println("=== [Test 03] Encoder counting ===");
  Serial.println("Click the encoder, then Right/Up = CW (+), Left/Down = CCW (-), Space = Z reset");
  Serial.println("Commands: p (status) | r (reset) | m <mm> (mm per step)");
  printStatus();
}

void loop() {
  uint32_t nowMs = millis();
  readSerial();
  updateZButton(nowMs);
  updatePosition(nowMs);
  updatePitchLed(nowMs);
}
