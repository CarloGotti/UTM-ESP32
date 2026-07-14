#include <Arduino.h>
#include <Wire.h>
#include "SparkFun_Qwiic_Scale_NAU7802_Arduino_Library.h"

// --- CONFIGURAZIONE PIN E PARAMETRI ---
const int PUL_PIN = 2, DIR_PIN = 4, UP_BUTTON_PIN = 18, DOWN_BUTTON_PIN = 19;
const int TOP_ENDSTOP_PIN = 22, BOTTOM_ENDSTOP_PIN = 23;
const int LOADCELL_SDA_PIN = 32, LOADCELL_SCL_PIN = 33;
const int ENCODER_PIN_A = 34, ENCODER_PIN_B = 35, ENCODER_PIN_Z = 27;



const float PULSES_PER_REV = 2000.0;
const float GEAR_RATIO = 10.0;
const float SCREW_PITCH_MM = 5.0873;  // allineato
const float PULSES_TO_MM = SCREW_PITCH_MM / (PULSES_PER_REV * GEAR_RATIO);
unsigned long test_start_time = 0;

// --- VARIABILI GLOBALI ---
NAU7802 scale;
volatile long pulse_count = 0;
volatile bool pulse_state = LOW;
volatile bool motor_enabled = false;
volatile bool dir_up = true;
volatile bool move_completed_flag = false;  // evento: movimento a passi contati terminato

// --- LIMITI DI SICUREZZA ASSOLUTI ---
// Inizializzati a valori molto alti (quindi "disabilitati" di default)
volatile long absolute_max_pulse_count = 99999999;
volatile float absolute_max_force_grams = 9999999;
bool limit_hit_notification_sent = false; // <-- NUOVA BANDIERINA


// --- Return-to-start support ---
long start_pulse_count = 0;                  // posizione all'avvio del test (in passi)
volatile long target_steps_remaining = 0;    // passi residui per movimenti a conteggio
const float default_return_speed_mms = 10.0;  // velocità di ritorno al punto iniziale

long pulse_delay_micros = 500; // periodo passi
hw_timer_t * stepTimer = NULL;

enum MotorState { STOPPED, JOG_UP, JOG_DOWN, HOMING, MONOTONIC_TEST, CYCLIC_TEST }; 
MotorState motor_state = STOPPED;
MotorState previous_motor_state = STOPPED;
bool is_hardware_jog_active = false;
enum HomingPhase { HOMING_FAST, HOMING_BACKOFF, HOMING_SLOW, HOMING_FINAL_LIFT };
HomingPhase homing_phase;

// --- ISR TIMER ---
 void IRAM_ATTR onStepTimer() {
  if (!motor_enabled) return;
  pulse_state = !pulse_state;
  digitalWrite(PUL_PIN, pulse_state);
if (pulse_state == LOW) {
  pulse_count += dir_up ? 1 : -1;
  // Gestione movimento a passi contati: decrementa SOLO sul fronte LOW,
  // cioè quando incrementi davvero pulse_count (1 passo logico)
if (target_steps_remaining > 0) {
  target_steps_remaining--;
  if (target_steps_remaining == 0) {
    motor_enabled = false;        // ferma il motore
    move_completed_flag = true;   // <-- segnala al loop
  }
}
}
} 


// --- ENCODER INCREMENTALE ESTERNO (Omron E6B2-CWZ6C, 1200 PPR) ---
// Canale di misura indipendente, montato direttamente sulla vite senza fine
// (nessun GEAR_RATIO di mezzo). Livello 1: sola lettura, non influenza in
// alcun modo il comando motore né i limiti di sicurezza assoluti, che restano
// basati su pulse_count. Decodifica in quadratura 4x (interrupt su A e B) +
// conteggio giri su Z, portata qui invariata dal modulo di validazione
// standalone testato su hardware reale (4800 conteggi/giro confermati).
volatile long encoder_position = 0;
volatile unsigned long encoder_z_turns = 0;
volatile uint8_t encoder_last_state = 0;
volatile int encoder_last_z_state = HIGH;
portMUX_TYPE encoder_mux = portMUX_INITIALIZER_UNLOCKED;

// Tabella di decodifica quadratura: indice = (stato_vecchio<<2)|stato_nuovo,
// ciascuno stato codificato come (A<<1)|B. 0 = transizione non valida
// (entrambi i canali cambiati nello stesso istante: rumore o passo perso).
static const int8_t ENCODER_QUAD_TABLE[16] = {
   0,  +1, -1,  0,
  -1,   0,  0, +1,
  +1,   0,  0, -1,
   0,  -1, +1,  0
};

void IRAM_ATTR handleEncoderChange() {
  uint8_t newState = (digitalRead(ENCODER_PIN_A) << 1) | digitalRead(ENCODER_PIN_B);
  uint8_t index = (encoder_last_state << 2) | newState;
  int8_t delta = ENCODER_QUAD_TABLE[index];

  portENTER_CRITICAL_ISR(&encoder_mux);
  encoder_position += delta;
  portEXIT_CRITICAL_ISR(&encoder_mux);

  encoder_last_state = newState;
}

void IRAM_ATTR handleEncoderZChange() {
  int z = digitalRead(ENCODER_PIN_Z);
  // Solo fronte di discesa (transistor open collector che conduce = impulso indice attivo).
  if (z == LOW && encoder_last_z_state == HIGH) {
    encoder_z_turns++;
  }
  encoder_last_z_state = z;
}

long readEncoderPosition() {
  long value;
  portENTER_CRITICAL(&encoder_mux);
  value = encoder_position;
  portEXIT_CRITICAL(&encoder_mux);
  return value;
}

// Cache lettura carico (in grammi)
volatile float last_load_grams = 0.0f;

// --- FILTRO EMA CELLA DI CARICO (NAU7802) ---
float filter_alpha = 0.5f;         // 0.01-1.0, configurabile via SET_FILTER_CONFIG
int filter_rate_sps = 320;         // sample rate NAU7802 corrente, configurabile via SET_FILTER_CONFIG
int filter_pga_gain = 128;         // guadagno PGA corrente (128x), configurabile via SET_FILTER_CONFIG
float filtered_load_grams = 0.0f;  // stato persistente del filtro EMA
bool filter_seeded = false;        // true dopo il primo campione valido (o dopo un reset esplicito)



// --- ARCHITETTURA A STATI PER LA COMUNICAZIONE ---
enum CommsMode { POLLING, STREAMING };
CommsMode comms_mode = POLLING;
unsigned long last_stream_time = 0;
long STREAM_INTERVAL_MS = 20; // 20 Hz

// Buffer seriale
String serial_buffer;

// --- PROVA MONOTONICA ---
enum StopCriterion { CRITERION_DISP, CRITERION_FORCE };
StopCriterion stop_criterion;
float stop_value; // mm o grammi (assoluti)

// --- PROVA CICLICA ---
enum CyclicPhase { CYCLIC_PREPOSITION, CYCLIC_MOVING_UP, CYCLIC_HOLDING_UPPER, CYCLIC_MOVING_DOWN, CYCLIC_HOLDING_LOWER, CYCLIC_PAUSED, RAMPING, RAMP_HOLDING };
volatile CyclicPhase cyclic_phase;
StopCriterion cyclic_control_type; // Riutilizziamo l'enum: CRITERION_DISP o CRITERION_FORCE
float cyclic_upper_limit = 0; // Limite superiore (in mm o grammi)
float cyclic_lower_limit = 0; // Limite inferiore (in mm o grammi)
float cyclic_speed_mms = 1.0; // Velocità (unica per ora)
unsigned long cyclic_hold_upper_ms = 0; // Pausa al limite sup. (in ms)
unsigned long cyclic_hold_lower_ms = 0; // Pausa al limite inf. (in ms)
int cyclic_target_cycles = 0; // Numero di cicli richiesti
volatile int cyclic_current_cycle = 0; // Contatore cicli attuale
unsigned long hold_start_time = 0; // Per gestire le pause

// --- NUOVE VARIABILI PER LA RAMPA ---
float ramp_target_value = 0; // Target in passi o grammi (assoluto)
volatile long ramp_target_steps = 0;
float ramp_speed_mms = 1.0;  // Velocità della rampa
unsigned long ramp_hold_ms = 0; // Durata hold alla fine della rampa
StopCriterion ramp_control_type; // CRITERION_DISP o CRITERION_FORCE

const int LCR_RX_PIN = 16; // Pin RX dell'ESP32 (collegato a TX dell'LCR)
const int LCR_TX_PIN = 17; // Pin TX dell'ESP32 (collegato a RX dell'LCR)
volatile float last_lcr_resistance = -999.0f; // Ultimo valore valido letto (o errore)
volatile bool lcr_polling_enabled = false; // Flag per attivare/disattivare la lettura
volatile bool lcr_read_in_progress = false; // Flag per evitare richieste multiple
unsigned long last_lcr_request_time = 0; // Per temporizzare le richieste
const long LCR_REQUEST_INTERVAL_MS = 20; // Interroga LCR max 50 volte/sec (100ms) - Regola se necessario

// --- PROTOTIPI ---
void handleHardwareInputs();
void startMotor(bool up);
void stopMotor();
void setMotorSpeed(float speed_mms);
void handleSerialCommands();
void processCommand(const String &command);
void updateMotorState();
void handleDataStreaming();
bool readLoadNonBlocking(float* result);
float averageLoadOverMs(unsigned long duration_ms);
void updateLCRReading();

void setup()
{
  Serial.begin(460800);
  Serial2.begin(115200, SERIAL_8N1, LCR_RX_PIN, LCR_TX_PIN);
  delay(200);
  Serial.println("Porta Seriale 2 per LCR Meter avviata...");
  Serial.println("ESP32 Avviato. Firmware con gestione seriale migliorata.");

  pinMode(PUL_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(TOP_ENDSTOP_PIN, INPUT_PULLUP);
  pinMode(BOTTOM_ENDSTOP_PIN, INPUT_PULLUP);
  pinMode(UP_BUTTON_PIN, INPUT_PULLUP);
  pinMode(DOWN_BUTTON_PIN, INPUT_PULLUP);

  pinMode(ENCODER_PIN_A, INPUT);
  pinMode(ENCODER_PIN_B, INPUT);
  pinMode(ENCODER_PIN_Z, INPUT);
  // Stato iniziale, per non generare un delta spurio alla prima transizione.
  encoder_last_state = (digitalRead(ENCODER_PIN_A) << 1) | digitalRead(ENCODER_PIN_B);
  encoder_last_z_state = digitalRead(ENCODER_PIN_Z);
  attachInterrupt(digitalPinToInterrupt(ENCODER_PIN_A), handleEncoderChange, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENCODER_PIN_B), handleEncoderChange, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENCODER_PIN_Z), handleEncoderZChange, CHANGE);

  Wire.begin(LOADCELL_SDA_PIN, LOADCELL_SCL_PIN);
  scale.begin(Wire);
  scale.setGain(NAU7802_GAIN_128); // Esplicito per chiarezza (coincide col default interno di begin())
  scale.setSampleRate(NAU7802_SPS_320);
  scale.calibrateAFE();
  scale.setCalibrationFactor(1.0);
  // Nessun auto-zero: la cella va sempre ri-tarata dopo il boot (comportamento invariato)

  // Timer hardware: prescaler 80 → 1 tick = 1 µs
  stepTimer = timerBegin(0, 80, true);
  timerAttachInterrupt(stepTimer, &onStepTimer, true);
  timerAlarmWrite(stepTimer, pulse_delay_micros, true);
  timerAlarmEnable(stepTimer);
  Serial.println("ESP32 avviato con timer hardware passi.");
}

void loop()
{
  // --- NUOVO BLOCCO PER GESTIRE IL COMPLETAMENTO DEL MOVIMENTO ---
  if (move_completed_flag) {
    // --- CORREZIONE ---
    // Controlla se il flag deve essere gestito dalla state machine ciclica
    if (motor_state == CYCLIC_TEST && cyclic_phase == CYCLIC_PREPOSITION) {
       // Non fare nulla qui. updateMotorState() lo gestirà.
    } else {
       // Altrimenti, è un movimento generico (es. RETURN_TO_START)
       move_completed_flag = false; // Consuma il flag
       Serial.println("STATUS:MOVE_COMPLETED");
       // Questo controllo è specifico per RETURN_TO_START
       if (pulse_count == start_pulse_count) {
         Serial.println("STATUS:RETURN_COMPLETED");
       }
    }
  }
  handleSerialCommands();
  handleDataStreaming();
  updateMotorState();
  //handleHardwareInputs();  TEMPORANEAMENTE DISABILITATO TASTI FISICI
  updateLCRReading();
}

// --- Gestione seriale non bloccante ---
void handleSerialCommands()
{
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    // STOP immediato con carattere singolo '!'
    if (c == '!') {
      bool was_monotonic = (motor_state == MONOTONIC_TEST);
      bool was_cyclic = (motor_state == CYCLIC_TEST);
      motor_state = STOPPED;
      comms_mode = POLLING;
      stopMotor();  // ferma subito i passi
      target_steps_remaining = 0;  // azzera un eventuale movimento a passi contati in corso (es. GOTO)
      if (was_monotonic) {
          Serial.println("STATUS:TEST_STOPPED_BY_USER");
      } else if (was_cyclic) {
          Serial.println("STATUS:CYCLIC_TEST_STOPPED_BY_USER"); // <-- MESSAGGIO CORRETTO
      } else {
          Serial.println("STATUS:STOPPED_BY_USER");
      }
      serial_buffer = "";
      return; // esci subito
    }

    if (c == '\n') {
      String command = serial_buffer;
      serial_buffer = "";
      command.trim();
      if (command.length() > 0) 
/*       // --- NUOVO BLOCCO DI ANALISI DETTAGLIATA ---
          Serial.println("\n--- INIZIO ANALISI DETTAGLIATA COMANDO ---");
          Serial.println("Comando ricevuto: '" + command + "'");
          Serial.println("Lunghezza: " + String(command.length()));
          for (unsigned int i = 0; i < command.length(); i++) {
              char c_char = command.charAt(i);
              Serial.println("Carattere " + String(i) + ": '" + c_char + "' (ASCII: " + String((int)c_char) + ")");
          }
          Serial.println("--- FINE ANALISI ---\n");
          // --- FINE BLOCCO --- */
      processCommand(command);
    } else {
      if (serial_buffer.length() < 128) serial_buffer += c;
    }
  }
}


// --- Gestione comandi ---
void processCommand(const String &command)
{
  // STOP: priorità assoluta
  if (command == "ENABLE_LCR_POLLING") {
      lcr_polling_enabled = true;
      last_lcr_resistance = -999.0f; // Resetta l'ultimo valore letto
      lcr_read_in_progress = false;  // Resetta lo stato interno
      Serial.println("STATUS:LCR_POLLING_ENABLED");
      return; // Esci subito
  } else if (command == "DISABLE_LCR_POLLING") {
      lcr_polling_enabled = false;
      Serial.println("STATUS:LCR_POLLING_DISABLED");
      return; // Esci subito
  }

  else if (command == "STOP")
  {
    bool was_monotonic = (motor_state == MONOTONIC_TEST);
    bool was_cyclic = (motor_state == CYCLIC_TEST);
    bool was_test = was_monotonic || was_cyclic;
    motor_state = STOPPED;
    stopMotor();  // ferma subito i passi
    target_steps_remaining = 0;  // azzera un eventuale movimento a passi contati in corso (es. GOTO)
    if (was_test) comms_mode = POLLING;
    if (was_monotonic) {
        Serial.println("STATUS:TEST_STOPPED_BY_USER");
    } else if (was_cyclic) {
        Serial.println("STATUS:CYCLIC_TEST_STOPPED_BY_USER"); // <-- MESSAGGIO CORRETTO
    } else {
        Serial.println("STATUS:STOPPED_BY_USER");
    }
    return;
  }
  else if (command == "RESET_TIMER")
  {
    test_start_time = millis();
    cyclic_current_cycle = 0; // Azzera anche i cicli globali
    Serial.println("STATUS:TIMER_RESET");
  }
  if (command.startsWith("SET_MODE:"))
  {
    String mode = command.substring(9);
    if (mode == "POLLING") comms_mode = POLLING;
    else if (mode == "STREAMING") comms_mode = STREAMING;
    Serial.println("STATUS:MODE_SET");
  }
  else if (command == "GET_DATA" && comms_mode == POLLING)
  {
    // Ora rispondiamo sempre con il formato 'D:', aggiungendo un timestamp fittizio di 0
    Serial.print("D:");
    Serial.print(last_load_grams, 1); // 1. Load
    Serial.print(";");
    Serial.print(pulse_count);        // 2. Pulses
    Serial.print(";");
    Serial.print("0");                // 3. Time (ms) - Fittizio
    Serial.print(";");
    Serial.print("0");                // 4. Cycle - Fittizio
    Serial.print(";");
    Serial.print(last_lcr_resistance, 4); // 5. Resistance
    Serial.print(";");
    Serial.println(readEncoderPosition()); // 6. Encoder count (grezzo, sola lettura)
  }
  else if (command == "TARE")
  {
    long offset = 0;
    int count = 0;
    unsigned long start = millis();
    while (millis() - start < 1000)
    {
      if (scale.available())
      {
        offset += scale.getReading();
        count++;
      }
      delay(2);
    }
    if (count > 0)
    {
      offset /= count;
      scale.setZeroOffset(offset);
      filter_seeded = false; // Ri-semina l'EMA: l'offset è cambiato
      Serial.print("STATUS:TARE_DONE;OFFSET=");
      Serial.println(offset);
    }
  }
  else if (command.startsWith("CALIBRATE:"))
  {
    float known_weight_grams = command.substring(10).toFloat();
    if (known_weight_grams > 0)
    {
      long sum = 0;
      int count = 0;
      unsigned long start = millis();
      while (millis() - start < 1000)
      {
        if (scale.available())
        {
          sum += scale.getReading();
          count++;
        }
        delay(2);
      }
      if (count > 0)
      {
        long raw_avg = sum / count;
        long offset = scale.getZeroOffset();
        float new_scale = (raw_avg - offset) / known_weight_grams;
        scale.setCalibrationFactor(new_scale);
        filter_seeded = false; // Ri-semina l'EMA: il fattore di scala è cambiato
        Serial.print("STATUS:CALIBRATION_DONE;SCALE=");
        Serial.println(new_scale, 6);
      }
    }
  }
  else if (command == "GET_SCALE")
  {
    Serial.print("SCALE:");
    Serial.println(scale.getCalibrationFactor(), 4);
  }
  else if (command.startsWith("SET_SCALE:"))
  {
    float new_scale = command.substring(10).toFloat();
    scale.setCalibrationFactor(new_scale);
    filter_seeded = false; // Ri-semina l'EMA: il fattore di scala è cambiato (stesso caso di CALIBRATE)
    Serial.println("STATUS:SCALE_SET");
  }
  else if (command.startsWith("SET_LIMITS:"))
  {
    // --- LOGICA ESSENZIALE (DA TENERE) ---
    String params = command.substring(11);
    int force_idx = params.indexOf("FORCE_G=");
    int disp_idx = params.indexOf("DISP_MM=");

    if (force_idx != -1) {
      String force_str = params.substring(force_idx + 8);
      absolute_max_force_grams = force_str.toFloat();
    }
    
    if (disp_idx != -1) {
      String disp_str = params.substring(disp_idx + 8);
      float disp_mm = disp_str.toFloat();
      absolute_max_pulse_count = (long)(disp_mm / PULSES_TO_MM);
    }
    
    // --- MESSAGGIO DI STATO UFFICIALE (DA TENERE) ---
    // Questo è l'unico output necessario per la comunicazione con la GUI
    Serial.println("STATUS:LIMITS_SET;MAX_FORCE_G=" + String(absolute_max_force_grams) + ";MAX_PULSES=" + String(absolute_max_pulse_count));
  }
  else if (command.startsWith("SET_FILTER_CONFIG:"))
  {
    String params = command.substring(18);
    int alpha_idx = params.indexOf("ALPHA=");
    int rate_idx = params.indexOf("RATE=");
    int gain_idx = params.indexOf("GAIN=");

    bool valid = (alpha_idx != -1 && rate_idx != -1);
    float new_alpha = 0.0f;
    int new_rate = 0;
    int new_gain = filter_pga_gain; // invariato se GAIN assente (retrocompatibilità)
    bool gain_provided = (gain_idx != -1);

    if (valid) {
      new_alpha = params.substring(alpha_idx + 6).toFloat();
      new_rate = params.substring(rate_idx + 5).toInt();
      valid = (new_alpha >= 0.01f && new_alpha <= 1.0f) &&
              (new_rate == 10 || new_rate == 20 || new_rate == 40 || new_rate == 80 || new_rate == 320);
    }

    if (valid && gain_provided) {
      new_gain = params.substring(gain_idx + 5).toInt();
      valid = (new_gain == 1 || new_gain == 2 || new_gain == 4 || new_gain == 8 ||
               new_gain == 16 || new_gain == 32 || new_gain == 64 || new_gain == 128);
    }

    if (valid) {
      uint8_t sps_enum;
      switch (new_rate) {
        case 10:  sps_enum = NAU7802_SPS_10;  break;
        case 20:  sps_enum = NAU7802_SPS_20;  break;
        case 40:  sps_enum = NAU7802_SPS_40;  break;
        case 80:  sps_enum = NAU7802_SPS_80;  break;
        default:  sps_enum = NAU7802_SPS_320; break;
      }
      filter_alpha = new_alpha;
      filter_rate_sps = new_rate;
      scale.setSampleRate(sps_enum);

      if (gain_provided) {
        bool gain_changed = (new_gain != filter_pga_gain);

        uint8_t gain_enum;
        switch (new_gain) {
          case 1:   gain_enum = NAU7802_GAIN_1;   break;
          case 2:   gain_enum = NAU7802_GAIN_2;   break;
          case 4:   gain_enum = NAU7802_GAIN_4;   break;
          case 8:   gain_enum = NAU7802_GAIN_8;   break;
          case 16:  gain_enum = NAU7802_GAIN_16;  break;
          case 32:  gain_enum = NAU7802_GAIN_32;  break;
          case 64:  gain_enum = NAU7802_GAIN_64;  break;
          default:  gain_enum = NAU7802_GAIN_128; break;
        }
        filter_pga_gain = new_gain;
        scale.setGain(gain_enum);

        if (gain_changed) {
          // Offset e fattore di scala erano validi solo al gain precedente:
          // invalidali esplicitamente invece di lasciare letture sbagliate silenziose.
          scale.setZeroOffset(0);
          scale.setCalibrationFactor(1.0);
          Serial.println("STATUS:CALIBRATION_INVALIDATED;REASON=GAIN_CHANGED");
        }
      }

      filter_seeded = false; // Ri-semina l'EMA: alpha/rate/gain sono cambiati
      Serial.println("STATUS:FILTER_CONFIG_SET;ALPHA=" + String(filter_alpha, 3) + ";RATE=" + String(filter_rate_sps) + ";GAIN=" + String(filter_pga_gain));
    } else {
      Serial.println("STATUS:FILTER_CONFIG_REJECTED;REASON=OUT_OF_RANGE");
    }
  }
  else if (command == "GET_FILTER_CONFIG")
  {
    Serial.println("STATUS:FILTER_CONFIG;ALPHA=" + String(filter_alpha, 3) + ";RATE=" + String(filter_rate_sps) + ";GAIN=" + String(filter_pga_gain));
  }
  else if (command == "RETURN_TO_START")
  {
    long delta = start_pulse_count - pulse_count;  // quanti passi servono per tornare
    if (delta == 0) {
      Serial.println("STATUS:RETURN_COMPLETED");
    } else {
      bool up = (delta > 0);
      setMotorSpeed(default_return_speed_mms);     // imposta la velocità di ritorno
      target_steps_remaining = labs(delta);
      dir_up = up;
      digitalWrite(DIR_PIN, up ? HIGH : LOW);
      motor_enabled = true;
      Serial.println("STATUS:RETURNING");
    }
  }
  else if (command.startsWith("START_CYCLIC_TEST:"))
  {
    // Serial.println("\n[DEBUG CYCLIC] Entrato nel blocco START_CYCLIC_TEST."); // <-- DEBUG 1
    target_steps_remaining = 0;
    // Esempio comando: "START_CYCLIC_TEST:MODE=DISP;UPPER=50.0;LOWER=10.0;SPEED=5.0;HOLD_U=1000;HOLD_L=500;CYCLES=100"
    String params = command.substring(18); // Rimuove "START_CYCLIC_TEST:"
    // Serial.println("[DEBUG CYCLIC] Parametri grezzi: " + params); // <-- DEBUG 2
    // --- Estrazione Parametri ---
    String mode_str = params.substring(params.indexOf("MODE=") + 5, params.indexOf(';'));
    float upper_val = params.substring(params.indexOf("UPPER=") + 6, params.indexOf(';', params.indexOf("UPPER="))).toFloat();
    float lower_val = params.substring(params.indexOf("LOWER=") + 6, params.indexOf(';', params.indexOf("LOWER="))).toFloat();
    cyclic_speed_mms = params.substring(params.indexOf("SPEED=") + 6, params.indexOf(';', params.indexOf("SPEED="))).toFloat();
    cyclic_hold_upper_ms = params.substring(params.indexOf("HOLD_U=") + 7, params.indexOf(';', params.indexOf("HOLD_U="))).toInt();
    cyclic_hold_lower_ms = params.substring(params.indexOf("HOLD_L=") + 7, params.indexOf(';', params.indexOf("HOLD_L="))).toInt();
    cyclic_target_cycles = params.substring(params.indexOf("CYCLES=") + 7).toInt();

    // Serial.println("[DEBUG CYCLIC] Parametri estratti: MODE=" + mode_str + ", UPPER=" + String(upper_val) + ", LOWER=" + String(lower_val) + ", SPEED=" + String(cyclic_speed_mms) + ", CYCLES=" + String(cyclic_target_cycles)); // <-- DEBUG 3

    // Determina il tipo di controllo
    if (mode_str == "DISP") {
        cyclic_control_type = CRITERION_DISP;
        // I limiti sono già in mm, convertili in passi
        cyclic_upper_limit = (long)(upper_val / PULSES_TO_MM);
        cyclic_lower_limit = (long)(lower_val / PULSES_TO_MM);
        // Serial.println("[DEBUG CYCLIC] Controllo DISP. Limiti (passi): " + String((long)cyclic_lower_limit) + " - " + String((long)cyclic_upper_limit)); // <-- DEBUG 4a
    } else { // mode_str == "FORCE"
        cyclic_control_type = CRITERION_FORCE;
        // I limiti sono già in grammi
        cyclic_upper_limit = upper_val;
        cyclic_lower_limit = lower_val;
        // Serial.println("[DEBUG CYCLIC] Controllo FORCE. Limiti (grammi): " + String(cyclic_lower_limit) + " - " + String(cyclic_upper_limit)); // <-- DEBUG 4b
    }

    // --- Inizializzazione Test ---
    cyclic_current_cycle = 0;
    setMotorSpeed(cyclic_speed_mms); // Imposta la velocità
    motor_state = CYCLIC_TEST;
    comms_mode = STREAMING;
    // Serial.println("[DEBUG CYCLIC] Stato impostato su CYCLIC_TEST, STREAMING."); // <-- DEBUG 5

    // --- Pre-posizionamento (Versione Semplificata) ---
    long target_start_pos_pulses = 0; 
    if (cyclic_control_type == CRITERION_DISP) {
        target_start_pos_pulses = (long)cyclic_lower_limit;
    } else { // Se controllo Forza, parti da dove sei
        target_start_pos_pulses = pulse_count; 
        
    }

    long delta = target_start_pos_pulses - pulse_count;
    // Serial.println("[DEBUG CYCLIC] Preposizionamento: Delta=" + String(delta)); // <-- DEBUG 6

    if (delta != 0 && cyclic_control_type == CRITERION_DISP) {
        // C'è un pre-posizionamento da fare (solo per controllo DISP)
        cyclic_phase = CYCLIC_PREPOSITION; 
        dir_up = (delta > 0);
        target_steps_remaining = labs(delta);
        digitalWrite(DIR_PIN, dir_up ? HIGH : LOW);
        motor_enabled = true;
        // Serial.println("[DEBUG CYCLIC] Avvio preposizionamento."); // <-- DEBUG 7a
        Serial.println("STATUS:CYCLIC_PREPOSITIONING");
    } else {
        // Nessun pre-posizionamento necessario (o controllo Forza), inizia subito a salire
        cyclic_current_cycle = 1;
        cyclic_phase = CYCLIC_MOVING_UP;
        startMotor(true); // Forza l'avvio del motore verso l'alto
        // Serial.println("[DEBUG CYCLIC] startMotor chiamato."); // <-- DEBUG 8b
        Serial.println("STATUS:CYCLIC_TEST_STARTED"); // <-- Messaggio ufficiale
    }
  //  Serial.println("[DEBUG CYCLIC] Fine blocco START_CYCLIC_TEST.\n"); // <-- DEBUG 9


  }
  else if (command.startsWith("EXECUTE_PAUSE:"))
  {
    // Estrai la durata della pausa in millisecondi
    unsigned long pause_duration_ms = command.substring(14).toInt();

    // Imposta lo stato e memorizza l'inizio della pausa
    motor_state = CYCLIC_TEST; // Rimaniamo nello stato generale ciclico
    cyclic_phase = CYCLIC_PAUSED;
    hold_start_time = millis(); // Riutilizziamo la variabile degli hold
    cyclic_hold_upper_ms = pause_duration_ms; // Memorizziamo la durata qui (o crea una variabile dedicata)

    // Assicurati che il motore sia fermo
    stopMotor();
    comms_mode = STREAMING; //

    Serial.println("STATUS:PAUSE_STARTED");
  }
  else if (command.startsWith("EXECUTE_RAMP:"))
  {
    // Esempio: "EXECUTE_RAMP:MODE=DISP;TARGET=10000;SPEED=5.0;HOLD=500"
    String params = command.substring(13); // Rimuove "EXECUTE_RAMP:"

    // Estrai parametri (simile a START_CYCLIC_TEST)
    String mode_str = params.substring(params.indexOf("MODE=") + 5, params.indexOf(';'));
    ramp_target_value = params.substring(params.indexOf("TARGET=") + 7, params.indexOf(';', params.indexOf("TARGET="))).toFloat();
    ramp_speed_mms = params.substring(params.indexOf("SPEED=") + 6, params.indexOf(';', params.indexOf("SPEED="))).toFloat();
    ramp_hold_ms = params.substring(params.indexOf("HOLD=") + 5).toInt();

    // Determina tipo di controllo
// Determina tipo di controllo e calcola target per il firmware
    if (mode_str == "DISP") {
        ramp_control_type = CRITERION_DISP;
        float target_mm = params.substring(params.indexOf("TARGET=") + 7, params.indexOf(';', params.indexOf("TARGET="))).toFloat();
        ramp_target_steps = (long)(target_mm / PULSES_TO_MM); // Salva PASSI
        // --- NUOVO DEBUG ---
        Serial.print("[DEBUG CMD] Calculated ramp_target_steps: ");
        Serial.println(ramp_target_steps);
        // --- FINE NUOVO DEBUG ---
    } else { // mode_str == "FORCE"
        ramp_control_type = CRITERION_FORCE;
        // target_value è già in grammi assoluti
    }

    // --- Inizializza la Rampa ---
    motor_state = CYCLIC_TEST; // Rimaniamo nello stato generale ciclico
    cyclic_phase = RAMPING;
    comms_mode = STREAMING;
    
    setMotorSpeed(ramp_speed_mms); // Imposta la velocità

    // Determina la direzione
    bool target_reached = false;
        if (ramp_control_type == CRITERION_DISP) {
            // --- INIZIO CORREZIONE ---
            // Confronta passi con passi, non mm con passi
            dir_up = (ramp_target_steps > pulse_count); 

            Serial.print("pulse_count: ");
            Serial.println(pulse_count);  
            
            Serial.print("ramp_target_steps: ");
            Serial.println(ramp_target_steps);  
            target_reached = (pulse_count == ramp_target_steps);
            // --- FINE CORREZIONE ---
        } else { // CRITERION_FORCE
            dir_up = (ramp_target_value > last_load_grams);
            target_reached = (abs(last_load_grams - ramp_target_value) < 1.0);
        }

    // Se non siamo già al target, avvia il motore
    if (!target_reached) {
        startMotor(dir_up);
        Serial.println("STATUS:RAMP_STARTED");
    } else {
        // Siamo già al target, vai direttamente all'hold o a fine blocco
        stopMotor(); // Assicura che sia fermo
        if (ramp_hold_ms > 0) {
            cyclic_phase = RAMP_HOLDING;
            hold_start_time = millis();
            Serial.println("STATUS:RAMP_HOLDING_STARTED");
        } else {
            motor_state = STOPPED;
            comms_mode = POLLING;
            Serial.println("STATUS:BLOCK_COMPLETED"); // Rampa "saltata" perché già al target
        }
    }
  }
  else if (command.startsWith("START_TEST:"))
  {
    target_steps_remaining = 0;
    String params = command.substring(11);
    int speed_idx = params.indexOf("SPEED_MMS=");
    int crit_idx = params.indexOf("CRITERION=");
    int stop_val_idx = params.indexOf("STOP_VAL=");
    if (speed_idx != -1 && crit_idx != -1 && stop_val_idx != -1)
    {
      String speed_str = params.substring(speed_idx + 10, params.indexOf(';', speed_idx));
      String crit_str = params.substring(crit_idx + 10, params.indexOf(';', crit_idx));
      String stop_val_str = params.substring(stop_val_idx + 9);

      float speed_mms = speed_str.toFloat();
      if (speed_mms > 0) {
        setMotorSpeed(speed_mms);  // aggiorna timer
      }

      if (crit_str == "DISP") stop_criterion = CRITERION_DISP;
      else if (crit_str == "FORCE") stop_criterion = CRITERION_FORCE;

      stop_value = stop_val_str.toFloat();
      comms_mode = STREAMING;
      motor_state = MONOTONIC_TEST;
      startMotor(true);  // avvia in direzione "up"
      Serial.println("STATUS:TEST_STARTED");
      start_pulse_count = pulse_count;  // memorizza punto iniziale del test
      test_start_time = millis();

    }
  }
  else if (!is_hardware_jog_active && motor_state == STOPPED)
  {
    limit_hit_notification_sent = false; // <-- ABBASSA LA BANDIERINA QUI

    if (command == "JOG_UP") {
      motor_state = JOG_UP;
      startMotor(true);
    }
    else if (command == "JOG_DOWN") {
      motor_state = JOG_DOWN;
      startMotor(false);
    }
    else if (command == "HOME") {
      motor_state = HOMING;
      homing_phase = HOMING_FAST;
      startMotor(false);  // homing verso il basso
    }
    else if (command.startsWith("SET_SPEED:"))
    {
      float speed_mms = command.substring(10).toFloat();
      if (speed_mms > 0) {
        setMotorSpeed(speed_mms);
      }
    }
    else if (command.startsWith("GOTO:"))
    {
      // Movimento verso una posizione assoluta (mm, sola andata, >= 0 come
      // da richiesta esplicita). Stesso meccanismo a passi contati già
      // usato da RETURN_TO_START: non introduce un nuovo MotorState, resta
      // STOPPED per tutta la durata del movimento (STOP/'!' lo interrompono
      // sempre azzerando target_steps_remaining, vedi sopra).
      float target_mm = command.substring(5).toFloat();
      if (target_mm >= 0) {
        long target_steps = (long)(target_mm / PULSES_TO_MM);
        long delta = target_steps - pulse_count;
        if (delta == 0) {
          Serial.println("STATUS:MOVE_COMPLETED");
        } else {
          dir_up = (delta > 0);
          digitalWrite(DIR_PIN, dir_up ? HIGH : LOW);
          target_steps_remaining = labs(delta);
          motor_enabled = true;
          Serial.println("STATUS:GOTO_STARTED");
        }
      }
    }
  }
}


// --- Lettura pulsanti hardware ---
void handleHardwareInputs()
{
  if (motor_state == MONOTONIC_TEST) return;  // durante il test monotono ignora i jog

  bool up_pressed   = (digitalRead(UP_BUTTON_PIN) == LOW);
  bool down_pressed = (digitalRead(DOWN_BUTTON_PIN) == LOW);

  if (up_pressed)
  {
    motor_state = JOG_UP;
    is_hardware_jog_active = true;
    startMotor(true);   // avvia motore verso l’alto
  }
  else if (down_pressed)
  {
    motor_state = JOG_DOWN;
    is_hardware_jog_active = true;
    startMotor(false);  // avvia motore verso il basso
  }
  else if (is_hardware_jog_active)
  {
    is_hardware_jog_active = false;
    motor_state = STOPPED;
    stopMotor();        // ferma subito i passi
  }
}


// --- Gestione motore ---
void startMotor(bool up) {
  dir_up = up;
  digitalWrite(DIR_PIN, up ? HIGH : LOW);
  motor_enabled = true;
  Serial.println("DEBUG: startMotor() chiamato");
}

void stopMotor() {
  motor_enabled = false;
  //Serial.println("DEBUG: stopMotor() chiamato");
}

void setMotorSpeed(float speed_mms) {
  if (speed_mms > 0) {
    pulse_delay_micros = (1000000.0 * SCREW_PITCH_MM) /
                         (2.0 * PULSES_PER_REV * GEAR_RATIO * speed_mms);
    timerAlarmWrite(stepTimer, pulse_delay_micros, true);
  }
}

void updateLCRReading() {
    // Se la lettura non è abilitata, imposta un valore non valido e esci
    if (!lcr_polling_enabled) {
        last_lcr_resistance = -999.0f; // Valore che indica "disabilitato"
        lcr_read_in_progress = false; // Resetta stato interno
        return;
    }

    unsigned long now = millis();

    // 1. È ora di inviare una nuova richiesta?
    // Invia solo se non c'è già una lettura in corso E se è passato abbastanza tempo
    if (!lcr_read_in_progress && (now - last_lcr_request_time >= LCR_REQUEST_INTERVAL_MS)) {

        // Svuota il buffer di ricezione da eventuali dati vecchi o spuri
        while(Serial2.available()) {
            Serial2.read();
        }

        // Invia la richiesta all'LCR meter
        Serial2.println("FETCh?");
        last_lcr_request_time = now; // Aggiorna il timer dell'ultima richiesta
        lcr_read_in_progress = true; // Imposta il flag: "sto aspettando una risposta"
        //Serial.println("DEBUG LCR: Richiesta inviata"); // Debug opzionale
        return; // Esci e aspetta la risposta nel prossimo ciclo del loop()
    }

    // 2. C'è una risposta in attesa? (Controlla solo se lcr_read_in_progress è true)
    if (lcr_read_in_progress) {
        if (Serial2.available() > 0) {
            // Risposta ricevuta! Leggila.
            String response = Serial2.readStringUntil('\n');
            response.trim(); // Rimuovi spazi/caratteri extra

            // Estrai il primo valore numerico prima della virgola
            int firstComma = response.indexOf(',');
            if (firstComma != -1) {
                String resistanceStr = response.substring(0, firstComma);
                last_lcr_resistance = resistanceStr.toFloat();
                 //Serial.print("DEBUG LCR: Ricevuto "); Serial.println(last_lcr_resistance); // Debug opzionale
            } else {
                // Errore: la risposta non contiene la virgola attesa
                last_lcr_resistance = -2.0f; // Codice errore per parsing fallito
                 //Serial.print("DEBUG LCR: Errore parsing risposta: "); Serial.println(response); // Debug opzionale
            }
            lcr_read_in_progress = false; // Lettura completata (o fallita), resetta il flag
        }
        // 3. È andata in timeout?
        // Se è passato troppo tempo dall'invio della richiesta senza risposta
        // Usiamo un timeout doppio rispetto all'intervallo per sicurezza
        else if (now - last_lcr_request_time > (LCR_REQUEST_INTERVAL_MS * 2)) {
            last_lcr_resistance = -1.0f; // Codice errore per timeout
            lcr_read_in_progress = false; // Annulla l'attesa e preparati per una nuova richiesta
             //Serial.println("DEBUG LCR: Timeout attesa risposta"); // Debug opzionale
        }
        // Altrimenti (nessuna risposta ancora e non in timeout), non fare nulla e aspetta ancora
    }
}

void updateMotorState()
{
  //Serial.println("Entro in update motor state");

  // --- NUOVO: CONTROLLO LIMITI DI SICUREZZA ASSOLUTI ---
  bool should_stop = false;

  // 1. Controllo Limite di Spostamento Assoluto
  if (pulse_count >= absolute_max_pulse_count && dir_up) {
    should_stop = true;
  }
  if (pulse_count <= -absolute_max_pulse_count && !dir_up) {
    should_stop = true;
  }

  if (should_stop) {
    motor_state = STOPPED;
    comms_mode = POLLING;
    stopMotor();
    if (!limit_hit_notification_sent) {
        Serial.println("STATUS:LIMIT_HIT_DISPLACEMENT");
        limit_hit_notification_sent = true;
    }
    return;
  }

  // 2. Controllo Limite di Forza (valore già filtrato via EMA in readLoadNonBlocking)
  if (last_load_grams > absolute_max_force_grams) {
    motor_state = STOPPED;
    comms_mode = POLLING;
    stopMotor();
    if (!limit_hit_notification_sent) {
        Serial.println("STATUS:LIMIT_HIT_FORCE");
        limit_hit_notification_sent = true;
    }
    return;
  }
  // --- FINE BLOCCO CONTROLLO LIMITI ---

  if (target_steps_remaining > 0) {
    digitalWrite(DIR_PIN, dir_up ? HIGH : LOW);
    return;
  }

  previous_motor_state = motor_state;

  // --- Controllo endstop (Logica Corretta) ---
  bool top_hit = (digitalRead(TOP_ENDSTOP_PIN) == LOW);
  bool bottom_hit = (digitalRead(BOTTOM_ENDSTOP_PIN) == LOW);

  bool force_stop_due_to_endstop = false; // Flag specifico per questa logica
  String status_msg = "";

  // Controlla TOP endstop: ferma solo se stiamo salendo (dir_up = true)
  if (top_hit && dir_up) {
      force_stop_due_to_endstop = true;
      status_msg = "STATUS:TOP_HIT";
  }
  // Controlla BOTTOM endstop: ferma solo se stiamo scendendo (dir_up = false)
  // E ignora questo controllo durante l'homing
  else if (bottom_hit && !dir_up && motor_state != HOMING) {
      force_stop_due_to_endstop = true;
      status_msg = "STATUS:BOTTOM_HIT";
  }

  // Applica lo stop forzato SE necessario E se non siamo già fermi
  if (force_stop_due_to_endstop && motor_state != STOPPED)
  {
    motor_state = STOPPED; // Forza lo stato a STOPPED
    comms_mode = POLLING;  // Torna in polling se colpisci un limite manualmente
    stopMotor();          // Ferma il motore
    Serial.println(status_msg); // Invia lo stato
  }
  // --- Fine Controllo endstop ---

  // --- Homing ---
  if (motor_state == HOMING) {
    bool bottom_hit = (digitalRead(BOTTOM_ENDSTOP_PIN) == LOW); // Leggi di nuovo l'endstop

    if (homing_phase == HOMING_FAST && bottom_hit) {
      stopMotor();
      long steps = (long)(3.0 / PULSES_TO_MM);
      setMotorSpeed(3.0); 
      dir_up = true;
      digitalWrite(DIR_PIN, HIGH);
      target_steps_remaining = steps;
      motor_enabled = true;
      homing_phase = HOMING_BACKOFF;
      Serial.println("STATUS:HOMING_BACKOFF");
    }
    else if (homing_phase == HOMING_BACKOFF && move_completed_flag) {
        move_completed_flag = false; 
        setMotorSpeed(1);
        dir_up = false;
        digitalWrite(DIR_PIN, LOW);
        motor_enabled = true;
        homing_phase = HOMING_SLOW;
        Serial.println("STATUS:HOMING_SLOW");
    }
    else if (homing_phase == HOMING_SLOW && bottom_hit) {
        // Endstop colpito durante la discesa lenta
        stopMotor(); // Ferma immediatamente

        // --- INIZIO NUOVA LOGICA: SOLLEVAMENTO FINALE ---
        long steps_to_lift = (long)(5.0 / PULSES_TO_MM); // Calcola passi per 5mm

        // Imposta i parametri per il movimento verso l'alto
        setMotorSpeed(1); // Usa una velocità ragionevole (es. quella di ritorno)
        dir_up = true;
        digitalWrite(DIR_PIN, HIGH);
        target_steps_remaining = steps_to_lift; // Imposta il target di passi
        motor_enabled = true; // Avvia il movimento

        // Passa alla nuova fase di sollevamento
        homing_phase = HOMING_FINAL_LIFT;
        Serial.println("STATUS:HOMING_LIFTING"); // Invia nuovo stato
        // --- FINE NUOVA LOGICA ---

    }
    else if (homing_phase == HOMING_FINAL_LIFT && move_completed_flag) {
        // Il movimento di 5mm verso l'alto è terminato
        move_completed_flag = false; // Consuma il flag
        stopMotor(); // Assicura che il motore sia fermo

        // ORA azzera la posizione
        pulse_count = 0;

        // L'homing esistente diventa anche il punto di zero per il canale
        // encoder esterno (Livello 1): stessa sezione critica già usata da
        // readEncoderPosition() per l'accesso sicuro dal loop() alle ISR.
        portENTER_CRITICAL(&encoder_mux);
        encoder_position = 0;
        encoder_z_turns = 0;
        portEXIT_CRITICAL(&encoder_mux);

        // Finalizza lo stato di homing
        motor_state = STOPPED;
        comms_mode = POLLING; // Torna in polling
        Serial.println("STATUS:HOMING_COMPLETED");
        Serial.println("STATUS:HOMED"); // Segnala che la macchina è pronta

/*       // --- NUOVO: Avvia un movimento di 5mm verso l'alto ---
      long steps_to_move_up = (long)(5.0 / PULSES_TO_MM); // 5mm
      setMotorSpeed(3.0);
      dir_up = true;
      digitalWrite(DIR_PIN, HIGH);
      target_steps_remaining = steps_to_move_up;
      motor_enabled = true; */
    }
  }

  // --- Verifica criterio di stop durante test monotono ---
  if (motor_state == MONOTONIC_TEST) {
    bool criterion_met = false; 

    if (stop_criterion == CRITERION_DISP) {
        float current_disp_mm = pulse_count * PULSES_TO_MM;
        if (current_disp_mm >= stop_value) {
            criterion_met = true;
        }
    } 
    else if (stop_criterion == CRITERION_FORCE) {
        // Valore già filtrato via EMA (vedi readLoadNonBlocking): confronto diretto
        if (last_load_grams >= stop_value) {
            criterion_met = true;
        }
    }

    if (criterion_met) {
      motor_state = STOPPED;
      comms_mode = POLLING;
      stopMotor();
      Serial.println("STATUS:TEST_COMPLETED");
    }
  }

  // --- Gestione direzione e abilitazione motore ---
  if (motor_state == JOG_UP) {
      digitalWrite(DIR_PIN, HIGH);
      startMotor(true);
  }
  else if (motor_state == JOG_DOWN) {
      digitalWrite(DIR_PIN, LOW);
      startMotor(false);
  }
  else if (motor_state == MONOTONIC_TEST) {
      digitalWrite(DIR_PIN, HIGH);
  }
  else if (motor_state == HOMING) {
      // Nessuna azione qui
  }
  else if (motor_state == STOPPED) {
      stopMotor();
  }
  // --- LOGICA TEST CICLICO (MODIFICATA) ---
  else if (motor_state == CYCLIC_TEST) {
        /*
        if (cyclic_current_cycle >= cyclic_target_cycles) {
          Serial.println("Sono in cyclic_current_cycle >= cyclic_target_cycles");
          stopMotor();
          motor_state = STOPPED;
          comms_mode = POLLING;
          Serial.println("STATUS:BLOCK_COMPLETED"); 
          cyclic_current_cycle = 0;
          return;
        }*/

        unsigned long current_time = millis();

        switch (cyclic_phase) {
            case CYCLIC_PREPOSITION:
                if (move_completed_flag) {
                    move_completed_flag = false;
                    cyclic_current_cycle = 1;
                    cyclic_phase = CYCLIC_MOVING_UP;
                    startMotor(true); 
                    Serial.println("STATUS:CYCLIC_TEST_STARTED");
                }
                break;

            case CYCLIC_MOVING_UP:
                { 
                bool limit_reached = false;
                if (cyclic_control_type == CRITERION_DISP) {
                    limit_reached = (pulse_count >= (long)cyclic_upper_limit);
                } else { // CRITERION_FORCE - valore già filtrato via EMA, confronto diretto
                    limit_reached = (last_load_grams >= cyclic_upper_limit);
                }

                if (limit_reached) {
                    stopMotor();
                    if (cyclic_hold_upper_ms > 0) {
                        cyclic_phase = CYCLIC_HOLDING_UPPER;
                        hold_start_time = current_time; 
                    } else {
                        cyclic_phase = CYCLIC_MOVING_DOWN; 
                        startMotor(false);
                    }
                }
                }
                break;

            case CYCLIC_HOLDING_UPPER:
                if (current_time - hold_start_time >= cyclic_hold_upper_ms) {
                    cyclic_phase = CYCLIC_MOVING_DOWN; 
                    startMotor(false); 
                }
                break;

            case CYCLIC_MOVING_DOWN:
                { 
                bool limit_reached = false;
                 if (cyclic_control_type == CRITERION_DISP) {
                    limit_reached = (pulse_count <= (long)cyclic_lower_limit);
                } else { // CRITERION_FORCE - valore già filtrato via EMA, confronto diretto
                    limit_reached = (last_load_grams <= cyclic_lower_limit);
                }

                if (limit_reached) {
                    stopMotor();

                    if (cyclic_current_cycle >= cyclic_target_cycles)
                    {
                      stopMotor();
                      motor_state = STOPPED; 
                      Serial.println("STATUS:BLOCK_COMPLETED"); 
                      cyclic_current_cycle = 0;
                      return;
                    }

                    if (cyclic_hold_lower_ms > 0) {
                        cyclic_phase = CYCLIC_HOLDING_LOWER;
                        hold_start_time = current_time;
                    } else {
                        cyclic_current_cycle++; 
                        cyclic_phase = CYCLIC_MOVING_UP; 
                        startMotor(true);
                    }
                }
                }
                break;

            case CYCLIC_HOLDING_LOWER:
                 if (current_time - hold_start_time >= cyclic_hold_lower_ms) {
                    cyclic_current_cycle++;
                    cyclic_phase = CYCLIC_MOVING_UP; 
                    startMotor(true); 
                }
                break;

            case CYCLIC_PAUSED:
                if (current_time - hold_start_time >= cyclic_hold_upper_ms) { 
                    motor_state = STOPPED; 
                    cyclic_phase = CYCLIC_MOVING_UP; 
                    Serial.println("STATUS:BLOCK_COMPLETED"); 
                }
                break;
            case RAMPING:
                {
                bool target_reached = false;
                // Leggi le variabili volatili UNA SOLA VOLTA all'inizio per il debug
                long current_pulses = pulse_count;
                long target_s = ramp_target_steps; // Legge il target in passi
                bool going_up = dir_up;
                StopCriterion control_t = ramp_control_type; // Legge il tipo

                // --- DEBUG DETTAGLIATO ---
/*                 Serial.print("[RAMP DBG] START | Pulses="); Serial.print(current_pulses);
                Serial.print(" | TargetSteps="); Serial.print(target_s);
                Serial.print(" | DirUp="); Serial.print(going_up);
                Serial.print(" | CtrlType="); Serial.println(control_t == CRITERION_DISP ? "DISP" : "FORCE"); */
                // --- FINE DEBUG ---

                // Controlla se il target è raggiunto
                if (control_t == CRITERION_DISP) {
                    // --- DEBUG DISP ---
                    // Serial.println("[RAMP DBG] In DISP check");
                    // --- FINE DEBUG ---
                    if (going_up) {
                        // --- DEBUG DISP UP ---
                        // Serial.print("[RAMP DBG] Checking UP: ("); Serial.print(current_pulses);
                        // Serial.print(" >= "); Serial.print(target_s); Serial.print(") ? ");
                        // --- FINE DEBUG ---
                        target_reached = (current_pulses >= target_s); // CONFRONTO DIRETTO
                        // --- DEBUG DISP UP RESULT ---
                        // Serial.println(target_reached ? "TRUE" : "FALSE");
                        // if (target_reached) Serial.println("[RAMP DBG] DISP UP TARGET REACHED!");
                        // --- FINE DEBUG ---
                    } else { // Scendendo
                        // --- DEBUG DISP DOWN ---
                        // Serial.print("[RAMP DBG] Checking DOWN: ("); Serial.print(current_pulses);
                        // Serial.print(" <= "); Serial.print(target_s); Serial.print(") ? ");
                        // --- FINE DEBUG ---
                        target_reached = (current_pulses <= target_s); // CONFRONTO DIRETTO
                        // --- DEBUG DISP DOWN RESULT ---
                        // Serial.println(target_reached ? "TRUE" : "FALSE");
                        // if (target_reached) Serial.println("[RAMP DBG] DISP DOWN TARGET REACHED!");
                        // --- FINE DEBUG ---
                    }
                } else { // CRITERION_FORCE - valore già filtrato via EMA, confronto diretto
                    if (dir_up) { target_reached = (last_load_grams >= ramp_target_value); }
                    else { target_reached = (last_load_grams <= ramp_target_value); }
                }

                // --- DEBUG FINALE ---
                // Serial.print("[RAMP DBG] END | target_reached = "); Serial.println(target_reached ? "TRUE" : "FALSE");
                // --- FINE DEBUG ---

                // Se il target è stato raggiunto
                if (target_reached) {
                    // --- DEBUG STOP ---
                    // Serial.println("[RAMP DBG] Condition MET! Stopping motor and completing block.");
                    // --- FINE DEBUG ---
                    stopMotor();

                    if (ramp_hold_ms > 0) {
                        cyclic_phase = RAMP_HOLDING;
                        hold_start_time = millis();
                        Serial.println("STATUS:RAMP_HOLDING_STARTED");
                    } else {
                        motor_state = STOPPED;
                        cyclic_phase = CYCLIC_MOVING_UP; // Resetta fase
                        Serial.println("STATUS:BLOCK_COMPLETED");
                    }
                }
                } // Fine blocco RAMPING
                break;

            // --- NUOVO CASE PER L'HOLD ALLA FINE DELLA RAMPA ---
            case RAMP_HOLDING:
                // Controlla se il tempo di hold è trascorso
                if (current_time - hold_start_time >= ramp_hold_ms) {
                    // Hold finito. Invia il messaggio di completamento blocco.
                    motor_state = STOPPED;
                    cyclic_phase = CYCLIC_MOVING_UP; // Resetta fase per il prossimo blocco
                    Serial.println("STATUS:BLOCK_COMPLETED");
                }
                // Non fare nient'altro durante l'hold
                break;

        } // Fine switch(cyclic_phase)
    } // Fine else if (motor_state == CYCLIC_TEST)
}


// --- Streaming dati ---
// Sostituisci completamente la vecchia funzione con questa
void handleDataStreaming() {
  float current_grams;
  if (readLoadNonBlocking(&current_grams)) {
    last_load_grams = current_grams;
  }

  if (comms_mode == STREAMING) {
    unsigned long now = millis();
    if (now - last_stream_time >= STREAM_INTERVAL_MS) {
      last_stream_time = now;

      //unsigned long elapsed_ms = (motor_state == MONOTONIC_TEST || motor_state == CYCLIC_TEST) ? (now - test_start_time) : 0;
      unsigned long elapsed_ms = now - test_start_time;

      Serial.print("D:");
      Serial.print(last_load_grams, 1);
      Serial.print(";");
      Serial.print(pulse_count);
      Serial.print(";");
      Serial.print(elapsed_ms);
      Serial.print(";");
      Serial.print(cyclic_current_cycle);
      Serial.print(";");
      Serial.print(last_lcr_resistance, 4);
      Serial.print(";");
      Serial.println(readEncoderPosition());
    }
  }
}

// --- Lettura non bloccante NAU7802 con filtro EMA ---
bool readLoadNonBlocking(float* result)
{
  if (scale.available())
  {
    float raw = (scale.getReading() - scale.getZeroOffset()) / scale.getCalibrationFactor();
    if (!filter_seeded) {
      filtered_load_grams = raw;
      filter_seeded = true;
    } else {
      filtered_load_grams = filter_alpha * raw + (1.0f - filter_alpha) * filtered_load_grams;
    }
    *result = filtered_load_grams;
    return true; // Successo! Un nuovo valore filtrato è disponibile.
  }
  return false; // Nessun nuovo dato disponibile dal sensore.
}


// --- Media su N ms per calibrazione ---
float averageLoadOverMs(unsigned long duration_ms)
{
  unsigned long start = millis();
  int count = 0;
  double sum = 0;

  while (millis() - start < duration_ms)
  {
    if (scale.available())
    {
      int32_t raw = scale.getReading();
      float grams = (raw - scale.getZeroOffset()) / scale.getCalibrationFactor();
      sum += grams;
      count++;
    }
    delay(2);
  }

  if (count > 0) return sum / count;
  else return 0.0f;
}
