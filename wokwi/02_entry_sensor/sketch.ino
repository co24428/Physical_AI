// [Test 02] Part-entry sensor (interrupt + debounce filter)
// Stand-in for the photoelectric sensor (#6). Detects parts on two kinds of input.
//   CH-A  D0 : Photoresistor module DO (through-beam style: part blocks the light -> dark -> HIGH)
//   CH-B  D1 : Pushbutton (NPN open-collector style: active LOW with pull-up, bouncing ON)
//   OUT   D2 : Trigger output LED (stands in for camera Line1 trigger), 20ms pulse per detection
//
// Filter logic
//   - Active edge while "armed" -> 1 detection, trigger pulse, disarm
//   - Re-arm only after the input stays inactive for FILTER_MS
//   - Edges while disarmed = chatter/bounce -> counted as rejected
//
// Serial commands
//   p        : print stats
//   r        : reset counts
//   f <ms>   : change filter time (e.g. f 10)

const uint8_t TRIGGER_GPIO = 3;           // D2
const uint32_t TRIGGER_PULSE_MS = 20;
const uint32_t MIN_PITCH_MS = 100;        // 10 parts/s -> parts at least 100ms apart

struct Channel {
  const char* name;
  uint8_t gpio;
  uint8_t activeLevel;
  uint8_t inputMode;

  volatile bool armed;
  volatile bool pending;                  // ISR accepted a detection, loop has not logged it yet
  volatile uint32_t pendingUs;
  volatile uint32_t rejectedEdges;

  uint32_t inactiveSinceMs;
  uint32_t count;
  uint32_t lastDetectUs;
};

Channel channels[] = {
  {"CH-A LDR", 1, HIGH, INPUT},           // D0, module drives the line itself
  {"CH-B BTN", 2, LOW, INPUT_PULLUP},     // D1
};
const uint8_t CHANNEL_COUNT = sizeof(channels) / sizeof(channels[0]);

volatile uint32_t filterMs = 5;
volatile bool triggerOn = false;
volatile uint32_t triggerStartMs = 0;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
String inputLine;

void IRAM_ATTR onEdge(void* arg) {
  Channel* ch = (Channel*)arg;
  if (digitalRead(ch->gpio) != ch->activeLevel) return;  // Only care about the active level

  portENTER_CRITICAL_ISR(&mux);
  if (ch->armed) {
    ch->armed = false;
    ch->pending = true;
    ch->pendingUs = micros();
    digitalWrite(TRIGGER_GPIO, HIGH);     // Fire the trigger right in the ISR (minimum latency)
    triggerOn = true;
    triggerStartMs = millis();
  } else {
    ch->rejectedEdges++;
  }
  portEXIT_CRITICAL_ISR(&mux);
}

// Re-arm a channel once its input has been inactive for filterMs
void updateArming(Channel& ch, uint32_t nowMs) {
  if (ch.armed) return;
  if (digitalRead(ch.gpio) == ch.activeLevel) {
    ch.inactiveSinceMs = 0;
    return;
  }
  if (ch.inactiveSinceMs == 0) {
    ch.inactiveSinceMs = nowMs;
  } else if (nowMs - ch.inactiveSinceMs >= filterMs) {
    ch.inactiveSinceMs = 0;
    ch.armed = true;
  }
}

void logDetection(Channel& ch) {
  bool pending;
  uint32_t detectUs;
  portENTER_CRITICAL(&mux);
  pending = ch.pending;
  detectUs = ch.pendingUs;
  ch.pending = false;
  portEXIT_CRITICAL(&mux);
  if (!pending) return;

  ch.count++;
  if (ch.count == 1) {
    Serial.printf("[%s] #%lu detected | t=%lu us\n", ch.name, (unsigned long)ch.count, (unsigned long)detectUs);
  } else {
    float intervalMs = (detectUs - ch.lastDetectUs) / 1000.0f;
    Serial.printf("[%s] #%lu detected | t=%lu us | interval %.1f ms%s\n", ch.name, (unsigned long)ch.count,
                  (unsigned long)detectUs, intervalMs,
                  intervalMs < MIN_PITCH_MS ? "  <-- WARN: faster than 10 parts/s" : "");
  }
  ch.lastDetectUs = detectUs;
}

void updateTrigger(uint32_t nowMs) {
  if (triggerOn && nowMs - triggerStartMs >= TRIGGER_PULSE_MS) {
    portENTER_CRITICAL(&mux);
    triggerOn = false;
    digitalWrite(TRIGGER_GPIO, LOW);
    portEXIT_CRITICAL(&mux);
  }
}

void printStats() {
  Serial.printf("--- Stats (filter %lu ms) ---\n", (unsigned long)filterMs);
  for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
    Channel& ch = channels[i];
    Serial.printf("  %s : detected %lu, rejected edges %lu, %s\n", ch.name, (unsigned long)ch.count,
                  (unsigned long)ch.rejectedEdges, ch.armed ? "armed" : "waiting");
  }
}

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd == "p") {
    printStats();
  } else if (cmd == "r") {
    for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
      channels[i].count = 0;
      channels[i].rejectedEdges = 0;
    }
    Serial.println("[CMD] counts reset");
  } else if (cmd.startsWith("f ")) {
    long ms = cmd.substring(2).toInt();
    if (ms < 0 || ms > 1000) {
      Serial.println("[CMD] filter range: 0~1000 ms");
      return;
    }
    filterMs = ms;
    Serial.printf("[CMD] filter -> %ld ms\n", ms);
  } else {
    Serial.printf("[CMD] unknown command: '%s' (p, r, f <ms>)\n", cmd.c_str());
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
  pinMode(TRIGGER_GPIO, OUTPUT);
  digitalWrite(TRIGGER_GPIO, LOW);

  for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
    Channel& ch = channels[i];
    pinMode(ch.gpio, ch.inputMode);
    ch.armed = (digitalRead(ch.gpio) != ch.activeLevel);  // Do not count a part already in front of the sensor at boot
    attachInterruptArg(digitalPinToInterrupt(ch.gpio), onEdge, &ch, CHANGE);
  }

  delay(500);
  Serial.println();
  Serial.println("=== [Test 02] Part-entry sensor ===");
  Serial.println("CH-A: drag the LDR lux down to ~5 (part blocks the light) -> detect, raise it back -> re-arm");
  Serial.println("CH-B: click the button or press 'B' -> detect (bounce is filtered)");
  Serial.println("Commands: p (stats) | r (reset) | f <ms> (filter time)");
  printStats();
}

void loop() {
  uint32_t nowMs = millis();
  readSerial();
  for (uint8_t i = 0; i < CHANNEL_COUNT; i++) {
    updateArming(channels[i], nowMs);
    logDetection(channels[i]);
  }
  updateTrigger(nowMs);
}
