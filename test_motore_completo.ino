// --- Controllo Motore con Pulsanti ed Endstop (Non-bloccante) ---

// --- CONFIGURAZIONE PIN ---
// Motore
const int PUL_PIN = 2;
const int DIR_PIN = 4;

// Pulsanti
const int UP_BUTTON_PIN = 18;
const int DOWN_BUTTON_PIN = 19;

// NUOVO: Endstop (sullo stesso lato degli altri pin)
const int TOP_ENDSTOP_PIN = 22;   // Endstop in Cima
const int BOTTOM_ENDSTOP_PIN = 23; // Endstop in Basso

// --- PARAMETRI DI MOVIMENTO ---
long pulse_delay_micros = 20;

// Variabili di stato per il motore
unsigned long lastPulseTime = 0;
bool pulseState = LOW;

void setup() {
  // Imposta i pin del motore come uscite
  pinMode(PUL_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);

  // Imposta i pin dei pulsanti come ingressi con pull-up
  pinMode(UP_BUTTON_PIN, INPUT_PULLUP);
  pinMode(DOWN_BUTTON_PIN, INPUT_PULLUP);

  // Imposta i pin degli endstop come ingressi con pull-up
  pinMode(TOP_ENDSTOP_PIN, INPUT_PULLUP);
  pinMode(BOTTOM_ENDSTOP_PIN, INPUT_PULLUP);
}

void loop() {
  bool motorShouldMove = false;

  // 1. LETTURA DEGLI ENDSTOP
  bool isTopEndstopTriggered = (digitalRead(TOP_ENDSTOP_PIN) == LOW);
  bool isBottomEndstopTriggered = (digitalRead(BOTTOM_ENDSTOP_PIN) == LOW);

  // 2. CONTROLLO DEI PULSANTI CON LOGICA DEGLI ENDSTOP
  if (digitalRead(UP_BUTTON_PIN) == LOW && !isTopEndstopTriggered) {
    digitalWrite(DIR_PIN, HIGH);
    motorShouldMove = true;
  }
  else if (digitalRead(DOWN_BUTTON_PIN) == LOW && !isBottomEndstopTriggered) {
    digitalWrite(DIR_PIN, LOW);
    motorShouldMove = true;
  }

  // 3. CONTROLLO DEL MOTORE
  if (motorShouldMove) {
    if (micros() - lastPulseTime >= pulse_delay_micros) {
      lastPulseTime = micros();
      pulseState = !pulseState;
      digitalWrite(PUL_PIN, pulseState);
    }
  }
}