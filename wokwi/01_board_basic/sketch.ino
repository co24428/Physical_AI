// [Test 01] XIAO ESP32-S3 board basics
// - Prints board information (chip, clock, flash, PSRAM)
// - Built-in LED (GPIO21, active-low) heartbeat blink
// - Sweeps D0~D10 in order to check that pin numbers match the wiring
// - Serial commands for manual checks
//
// Serial commands (type in the Serial Monitor, then Enter)
//   p      : print the pin map
//   s      : pause / resume the sweep
//   a / o  : all LEDs ON / OFF (sweep pauses)
//   D0~D10 : toggle that pin (sweep pauses)

struct PinInfo {
  const char* label;
  uint8_t gpio;
};

// XIAO ESP32-S3 silkscreen labels -> actual GPIO numbers (from the Wokwi board.json)
const PinInfo PINS[] = {
  {"D0", 1},  {"D1", 2},  {"D2", 3},  {"D3", 4},  {"D4", 5},  {"D5", 6},
  {"D6", 43}, {"D7", 44}, {"D8", 7},  {"D9", 8},  {"D10", 9},
};
const uint8_t PIN_COUNT = sizeof(PINS) / sizeof(PINS[0]);

const uint8_t BUILTIN_LED_GPIO = 21;  // Anode on 3V3, so LOW = ON

const uint32_t HEARTBEAT_MS = 500;
const uint32_t SWEEP_STEP_MS = 300;

bool pinState[PIN_COUNT] = {false};
bool sweepRunning = true;
int8_t sweepIndex = -1;
uint32_t lastHeartbeat = 0;
uint32_t lastSweepStep = 0;
bool heartbeatOn = false;
String inputLine;

void setPin(uint8_t i, bool on) {
  pinState[i] = on;
  digitalWrite(PINS[i].gpio, on ? HIGH : LOW);
}

void setAll(bool on) {
  for (uint8_t i = 0; i < PIN_COUNT; i++) setPin(i, on);
}

void printBoardInfo() {
  Serial.println();
  Serial.println("=== [Test 01] XIAO ESP32-S3 board basics ===");
  Serial.printf("Chip      : %s rev %d, %d cores\n", ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores());
  Serial.printf("CPU clock : %lu MHz\n", (unsigned long)ESP.getCpuFreqMHz());
  Serial.printf("Flash     : %lu MB\n", (unsigned long)(ESP.getFlashChipSize() / (1024 * 1024)));
  Serial.printf("PSRAM     : %lu KB\n", (unsigned long)(ESP.getPsramSize() / 1024));
  Serial.printf("Free heap : %lu KB\n", (unsigned long)(ESP.getFreeHeap() / 1024));
}

void printPinMap() {
  Serial.println("--- Pin map (label -> GPIO, state) ---");
  for (uint8_t i = 0; i < PIN_COUNT; i++) {
    Serial.printf("  %-3s -> GPIO%-2d : %s\n", PINS[i].label, PINS[i].gpio, pinState[i] ? "ON" : "OFF");
  }
  Serial.println("  LED -> GPIO21 (built-in, active-low)");
}

int8_t findPin(const String& label) {
  for (uint8_t i = 0; i < PIN_COUNT; i++) {
    if (label.equalsIgnoreCase(PINS[i].label)) return i;
  }
  return -1;
}

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd == "p") {
    printPinMap();
  } else if (cmd == "s") {
    sweepRunning = !sweepRunning;
    if (sweepRunning) setAll(false);
    Serial.printf("[CMD] sweep %s\n", sweepRunning ? "resumed" : "paused");
  } else if (cmd == "a" || cmd == "o") {
    sweepRunning = false;
    setAll(cmd == "a");
    Serial.printf("[CMD] all LEDs %s (sweep paused)\n", cmd == "a" ? "ON" : "OFF");
  } else {
    int8_t i = findPin(cmd);
    if (i < 0) {
      Serial.printf("[CMD] unknown command: '%s' (p, s, a, o, D0~D10)\n", cmd.c_str());
      return;
    }
    sweepRunning = false;
    setPin(i, !pinState[i]);
    Serial.printf("[CMD] %s (GPIO%d) -> %s (sweep paused)\n", PINS[i].label, PINS[i].gpio, pinState[i] ? "ON" : "OFF");
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

void updateHeartbeat(uint32_t now) {
  if (now - lastHeartbeat < HEARTBEAT_MS) return;
  lastHeartbeat = now;
  heartbeatOn = !heartbeatOn;
  digitalWrite(BUILTIN_LED_GPIO, heartbeatOn ? LOW : HIGH);
}

void updateSweep(uint32_t now) {
  if (!sweepRunning || now - lastSweepStep < SWEEP_STEP_MS) return;
  lastSweepStep = now;

  if (sweepIndex >= 0) setPin(sweepIndex, false);
  sweepIndex = (sweepIndex + 1) % PIN_COUNT;
  setPin(sweepIndex, true);
  Serial.printf("[SWEEP] %-3s (GPIO%d) ON  | uptime %lu ms\n", PINS[sweepIndex].label, PINS[sweepIndex].gpio, (unsigned long)now);
}

void setup() {
  Serial.begin(115200);

  pinMode(BUILTIN_LED_GPIO, OUTPUT);
  digitalWrite(BUILTIN_LED_GPIO, HIGH);
  for (uint8_t i = 0; i < PIN_COUNT; i++) {
    pinMode(PINS[i].gpio, OUTPUT);
    setPin(i, false);
  }

  delay(500);  // Wait for the USB serial connection
  printBoardInfo();
  printPinMap();
  Serial.println("Commands: p (pin map) | s (sweep pause/resume) | a/o (all ON/OFF) | D0~D10 (toggle)");
}

void loop() {
  uint32_t now = millis();
  readSerial();
  updateHeartbeat(now);
  updateSweep(now);
}
