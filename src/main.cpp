#include <Arduino.h>
#include "HX711.h"

// --- CONFIGURAZIONE PIN E PARAMETRI ---
const int PUL_PIN = 2, DIR_PIN = 4, UP_BUTTON_PIN = 18, DOWN_BUTTON_PIN = 19;
const int TOP_ENDSTOP_PIN = 22, BOTTOM_ENDSTOP_PIN = 23;
const int LOADCELL_DOUT_PIN = 32, LOADCELL_SCK_PIN = 33;



const float PULSES_PER_REV = 2000.0;
const float GEAR_RATIO = 10.0;
const float SCREW_PITCH_MM = 5.0873;  // allineato
const float PULSES_TO_MM = SCREW_PITCH_MM / (PULSES_PER_REV * GEAR_RATIO);
unsigned long test_start_time = 0;

// --- VARIABILI GLOBALI ---
HX711 scale;
volatile long pulse_count = 0;
volatile bool pulse_state = LOW;
volatile bool motor_enabled = false;
volatile bool dir_up = true;
volatile bool move_completed_flag = false;  // evento: movimento a passi contati terminato

// --- LIMITI DI SICUREZZA ASSOLUTI ---
// Inizializzati a valori molto alti (quindi "disabilitati" di default)
volatile long absolute_max_pulse_count = 99999999;
volatile float absolute_max_force_grams = 9999999;
// --- GESTIONE SPIKE DI FORZA ---
volatile int over_force_limit_counter = 0;
const int SPIKE_FILTER_COUNT = 5; // Richiede 5 letture consecutive sopra il limite per attivare lo stop
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
enum HomingPhase { HOMING_FAST, HOMING_BACKOFF, HOMING_SLOW };
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


// Cache lettura carico (in grammi)
volatile float last_load_grams = 0.0f;



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
int stop_criterion_force_counter = 0;
const int STOP_CRITERION_SPIKE_COUNT = 5;

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
int cyclic_force_counter = 0; // Contatore anti-spike per il controllo di forza
volatile bool new_load_data_available = false;

// --- NUOVE VARIABILI PER LA RAMPA ---
float ramp_target_value = 0; // Target in passi o grammi (assoluto)
volatile long ramp_target_steps = 0;
float ramp_speed_mms = 1.0;  // Velocità della rampa
unsigned long ramp_hold_ms = 0; // Durata hold alla fine della rampa
StopCriterion ramp_control_type; // CRITERION_DISP o CRITERION_FORCE

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

void setup()
{
  Serial.begin(460800);
  Serial.println("ESP32 Avviato. Firmware con gestione seriale migliorata.");

  pinMode(PUL_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(TOP_ENDSTOP_PIN, INPUT_PULLUP);
  pinMode(BOTTOM_ENDSTOP_PIN, INPUT_PULLUP);
  pinMode(UP_BUTTON_PIN, INPUT_PULLUP);
  pinMode(DOWN_BUTTON_PIN, INPUT_PULLUP);

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.set_scale(1.0);
  scale.tare();

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
  handleHardwareInputs();
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
  if (command == "STOP")
  {
    bool was_monotonic = (motor_state == MONOTONIC_TEST);
    bool was_cyclic = (motor_state == CYCLIC_TEST);
    bool was_test = was_monotonic || was_cyclic;
    motor_state = STOPPED;
    stopMotor();  // ferma subito i passi
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
    Serial.print(last_load_grams, 1);
    Serial.print(";");
    Serial.print(pulse_count);
    Serial.print(";");
    Serial.println("0");
  }
  else if (command == "TARE")
  {
    long offset = 0;
    int count = 0;
    unsigned long start = millis();
    while (millis() - start < 1000)
    {
      if (scale.is_ready())
      {
        offset += scale.read();
        count++;
      }
      delay(2);
    }
    if (count > 0)
    {
      offset /= count;
      scale.set_offset(offset);
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
        if (scale.is_ready())
        {
          sum += scale.read();
          count++;
        }
        delay(2);
      }
      if (count > 0)
      {
        long raw_avg = sum / count;
        long offset = scale.get_offset();
        float new_scale = (raw_avg - offset) / known_weight_grams;
        scale.set_scale(new_scale);
        Serial.print("STATUS:CALIBRATION_DONE;SCALE=");
        Serial.println(new_scale, 6);
      }
    }
  }
  else if (command == "GET_SCALE")
  {
    Serial.print("SCALE:");
    Serial.println(scale.get_scale(), 4);
  }
  else if (command.startsWith("SET_SCALE:"))
  {
    float new_scale = command.substring(10).toFloat();
    scale.set_scale(new_scale);
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
    cyclic_force_counter = 0; // Resetta contatore anti-spike
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
      stop_criterion_force_counter = 0;
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

void updateMotorState()
{
  //Serial.println("Entro in update motor state");
  // --- INIZIO CORREZIONE: Snapshot del flag "new data" ---
  // Leggiamo il flag una sola volta all'inizio della funzione
  bool is_fresh_reading = new_load_data_available;
  // --- FINE CORREZIONE ---

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

  // 2. Controllo Limite di Forza con Filtro Anti-Spike
  if (last_load_grams > absolute_max_force_grams) {
    // --- CORREZIONE: Usa il flag anche per i limiti di sicurezza ---
    if (is_fresh_reading) {
        over_force_limit_counter++; // Incrementa solo se è un *nuovo* dato
    }
  } else {
    over_force_limit_counter = 0; 
  }

  if (over_force_limit_counter >= SPIKE_FILTER_COUNT) {
    motor_state = STOPPED;
    comms_mode = POLLING;
    stopMotor();
    if (!limit_hit_notification_sent) { 
        Serial.println("STATUS:LIMIT_HIT_FORCE");
        limit_hit_notification_sent = true; 
    }    over_force_limit_counter = 0; 
    return;
  }
  // --- FINE BLOCCO CONTROLLO LIMITI ---

  if (target_steps_remaining > 0) {
    digitalWrite(DIR_PIN, dir_up ? HIGH : LOW);
    return;
  }

  previous_motor_state = motor_state;

  // --- Controllo endstop ---
  bool top_hit = (digitalRead(TOP_ENDSTOP_PIN) == LOW);
  bool bottom_hit = (digitalRead(BOTTOM_ENDSTOP_PIN) == LOW);
  
  should_stop = false; // Azzera la variabile
  String status_msg = "";

  if (top_hit) {
      should_stop = true;
      status_msg = "STATUS:TOP_HIT";
  } 
  else if (bottom_hit) { 
      if (motor_state != HOMING) {
          should_stop = true;
          status_msg = "STATUS:BOTTOM_HIT";
      }
  }

  if (should_stop && motor_state != STOPPED)
  {
    motor_state = STOPPED;
    comms_mode = POLLING;
    stopMotor();
    Serial.println(status_msg);
  }

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
        setMotorSpeed(0.3);
        dir_up = false;
        digitalWrite(DIR_PIN, LOW);
        motor_enabled = true;
        homing_phase = HOMING_SLOW;
        Serial.println("STATUS:HOMING_SLOW");
    }
    else if (homing_phase == HOMING_SLOW && bottom_hit) {
      stopMotor();
      pulse_count = 0;
      motor_state = STOPPED;
      comms_mode = POLLING;
      Serial.println("STATUS:HOMING_COMPLETED");
      Serial.println("STATUS:HOMED");

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
        if (last_load_grams >= stop_value) {
            // --- CORREZIONE: Usa il flag anche per lo stop monotonico ---
            if (is_fresh_reading) {
                stop_criterion_force_counter++;
            }
        } else {
            stop_criterion_force_counter = 0;
        }

        if (stop_criterion_force_counter >= STOP_CRITERION_SPIKE_COUNT) {
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
                } else { // CRITERION_FORCE
                    // --- INIZIO CORREZIONE: Logica contatore protetta dal flag ---
                    if (last_load_grams >= cyclic_upper_limit) {
                        if (is_fresh_reading) { // Incrementa solo se è un *nuovo* dato
                            cyclic_force_counter++;
                        }
                    } else {
                        cyclic_force_counter = 0; // Resetta sempre
                    }
                    limit_reached = (cyclic_force_counter >= STOP_CRITERION_SPIKE_COUNT);
                    // --- FINE CORREZIONE ---
                }

                if (limit_reached) {
                    stopMotor(); 
                    cyclic_force_counter = 0; 
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
                } else { // CRITERION_FORCE
                    // --- INIZIO CORREZIONE: Logica contatore protetta dal flag ---
                    if (last_load_grams <= cyclic_lower_limit) { 
                        if (is_fresh_reading) { // Incrementa solo se è un *nuovo* dato
                            cyclic_force_counter++;
                        }
                    } else {
                        cyclic_force_counter = 0; // Resetta sempre
                    }
                    limit_reached = (cyclic_force_counter >= STOP_CRITERION_SPIKE_COUNT);
                    // --- FINE CORREZIONE ---
                }

                if (limit_reached) {
                    stopMotor();
                    cyclic_force_counter = 0;
                    

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
                } else { // CRITERION_FORCE
                   // ... (Logica forza invariata, eventualmente aggiungere debug simile se serve) ...
                    bool condition_met = false;
                    if (dir_up) { condition_met = (last_load_grams >= ramp_target_value); }
                    else { condition_met = (last_load_grams <= ramp_target_value); }
                    if (condition_met) { if (is_fresh_reading) { cyclic_force_counter++; } }
                    else { cyclic_force_counter = 0; }
                    target_reached = (cyclic_force_counter >= STOP_CRITERION_SPIKE_COUNT);
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
                    cyclic_force_counter = 0;

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

  // --- INIZIO CORREZIONE: Consuma il flag ---
  // Alla fine della funzione, abbassa la bandierina
  if (is_fresh_reading) {
      new_load_data_available = false;
  }
  // --- FINE CORREZIONE ---
}


// --- Streaming dati ---
// Sostituisci completamente la vecchia funzione con questa
void handleDataStreaming() {
  float current_grams;
  if (readLoadNonBlocking(&current_grams)) {
    last_load_grams = current_grams;
    new_load_data_available = true;
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
      Serial.println(cyclic_current_cycle);
    }
  }
}

// --- Lettura non bloccante HX711 ---
bool use_filter = false;

bool readLoadNonBlocking(float* result)
{
  if (scale.is_ready())
  {
    long raw = scale.read();
    // Non usiamo più il filtro qui, la lettura deve essere grezza e veloce
    *result = (raw - scale.get_offset()) / scale.get_scale();
    return true; // Successo! Un nuovo valore è disponibile.
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
    if (scale.is_ready())
    {
      long raw = scale.read();
      float grams = (raw - scale.get_offset()) / scale.get_scale();
      sum += grams;
      count++;
    }
    delay(2);
  }

  if (count > 0) return sum / count;
  else return 0.0f;
}
