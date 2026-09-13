#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include "SparkFun_Qwiic_Scale_NAU7802_Arduino_Library.h"

// --- CONFIGURAZIONE PIN E PARAMETRI ---
const int PUL_PIN = 2, DIR_PIN = 4, UP_BUTTON_PIN = 18, DOWN_BUTTON_PIN = 19;
const int TOP_ENDSTOP_PIN = 22, BOTTOM_ENDSTOP_PIN = 23;
const int LOADCELL_SDA_PIN = 32, LOADCELL_SCL_PIN = 33;
const int ENCODER_PIN_A = 34, ENCODER_PIN_B = 35, ENCODER_PIN_Z = 27;
const int KILLSWITCH_SENSE_PIN = 39; // "VN", input-only, pull-up esterno 10k verso 3.3V
// Jog encoder (controllo manuale fisico, NON l'encoder esterno di misura sopra):
// encoder meccanico "nudo" 5 pin con pulsante integrato per il preset di step.
const int JOG_ENCODER_A = 13, JOG_ENCODER_B = 14, JOG_ENCODER_SW = 25;



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

// --- KILLSWITCH HARDWARE (GPIO39, "VN") ---
// Topologia e verifica fisica: vedi CLAUDE.md. GPIO39=LOW -> riposo (48V
// presenti). GPIO39=HIGH -> killswitch premuto (48V realmente tagliati).
// Debounce asimmetrico voluto: la pressione (transizione a HIGH) viene
// rilevata subito, senza alcun ritardo; il rilascio (transizione a LOW)
// richiede ~200ms di stato stabile prima di essere considerato reale, per non
// scambiare un rimbalzo meccanico in rilascio per un vero rilascio.
volatile bool killswitch_raw_high = false;
volatile unsigned long killswitch_last_high_ms = 0;
bool killswitch_engaged = false; // stato effettivo (debounced), letto/scritto solo in loop()/ISR-safe via i due volatile sopra
const unsigned long KILLSWITCH_RELEASE_DEBOUNCE_MS = 200;

// True finché non viene completato con successo un HOME successivo a un
// trigger del killswitch (o già true dal boot se il killswitch era premuto
// all'accensione). Blocca l'avvio di prove e GOTO; non blocca mai HOME, che
// resta l'unico modo per uscire da questo stato. In futuro, quando verranno
// aggiunti jog fisici (pulsanti/encoder), la stessa variabile killswitch_engaged
// andrà usata per permetterli in stato "giallo" (unverified ma non premuto) e
// bloccarli solo in stato "rosso" (premuto) — stessa regola già applicata qui
// sotto a JOG_UP/JOG_DOWN via comando seriale.
bool position_unverified = false;

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


// --- ISR KILLSWITCH ---
// Aggiorna solo lo stato grezzo (letto poi da updateKillswitchState() nel
// loop principale): niente logica di sicurezza dentro l'ISR stessa, per
// restare rapidissima e per tenere un solo punto (il loop) che decide
// se/quando fermare il motore.
void IRAM_ATTR handleKillswitchChange() {
  bool state = (digitalRead(KILLSWITCH_SENSE_PIN) == HIGH);
  killswitch_raw_high = state;
  if (state) {
    killswitch_last_high_ms = millis();
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

// --- PULSANTI MANUALI UP/DOWN E JOG ENCODER (controllo manuale fisico) ---
// Da non confondere con l'encoder incrementale esterno di misura sopra
// (ENCODER_PIN_A/B/Z, Omron E6B2): il "jog encoder" qui è un dispositivo
// meccanico separato, dedicato al controllo manuale, con pulsante integrato
// per selezionare la dimensione dello step. Variabili/funzioni relative
// prefissate JOG_/jog_ per evitare confusione tra i due encoder.

// Guardia di attivazione condivisa (unico punto di verità) tra pulsanti
// fisici Up/Down e jog encoder: permessi solo se il motore è realmente
// fermo (nessun test/homing/GOTO/RETURN_TO_START in corso, quindi anche
// target_steps_remaining==0, non solo motor_state==STOPPED) e il killswitch
// non è premuto ORA. Non controlla position_unverified: in stato "giallo"
// (posizione non verificata ma killswitch rilasciato) il jog manuale deve
// restare utilizzabile, esattamente come JOG_UP/JOG_DOWN via seriale.
bool isManualJogAllowed() {
  return (motor_state == STOPPED) && (target_steps_remaining == 0) && (!killswitch_engaged);
}

// --- Pulsanti manuali Up/Down (UP_BUTTON_PIN/DOWN_BUTTON_PIN) ---
const unsigned long BUTTON_DEBOUNCE_MS = 25;
bool up_button_last_reading = false;
bool up_button_stable = false;
unsigned long up_button_last_change_ms = 0;
bool up_button_owns_jog = false;   // true se è stato questo pulsante ad avviare il jog corrente

bool down_button_last_reading = false;
bool down_button_stable = false;
unsigned long down_button_last_change_ms = 0;
bool down_button_owns_jog = false;

// --- Jog encoder: decodifica quadratura (rotazione) ---
// Riusa la stessa tabella di decodifica generica ENCODER_QUAD_TABLE già
// definita sopra per l'encoder esterno (puramente combinatoria, non
// specifica a un device). ISR minimale: solo incremento/decremento di un
// contatore volatile, nessuna chiamata a funzioni di movimento qui dentro.
volatile long jogEncoderCount = 0;
volatile uint8_t jog_encoder_last_state = 0;
portMUX_TYPE jog_encoder_mux = portMUX_INITIALIZER_UNLOCKED;

void IRAM_ATTR handleJogEncoderChange() {
  uint8_t newState = (digitalRead(JOG_ENCODER_A) << 1) | digitalRead(JOG_ENCODER_B);
  uint8_t index = (jog_encoder_last_state << 2) | newState;
  int8_t delta = ENCODER_QUAD_TABLE[index];

  portENTER_CRITICAL_ISR(&jog_encoder_mux);
  jogEncoderCount -= delta; // segno invertito: verificato fisicamente che il
                            // verso di conteggio "naturale" risulta opposto
                            // a quello atteso (vedi CLAUDE.md/CHANGELOG.md)
  portEXIT_CRITICAL_ISR(&jog_encoder_mux);

  jog_encoder_last_state = newState;
}

// --- Jog encoder: preset di dimensione step (ciclati dal pulsante integrato) ---
// Valori di partenza indicativi ("fine"/"molto fine"/"finissima"), pensati
// per essere ritarati su macchina reale: costanti volutamente qui in testa,
// facilmente modificabili.
const float JOG_STEP_SIZE_FINE_MM = 0.05f;
const float JOG_STEP_SIZE_VERY_FINE_MM = 0.01f;
const float JOG_STEP_SIZE_FINEST_MM = 0.005f;
const float JOG_STEP_SIZES_MM[3] = { JOG_STEP_SIZE_FINE_MM, JOG_STEP_SIZE_VERY_FINE_MM, JOG_STEP_SIZE_FINEST_MM };
int jog_step_preset_index = 0; // 0=fine, 1=molto fine, 2=finissima; ciclato da JOG_ENCODER_SW

// Velocità dedicata per i movimenti a step del jog encoder: fissa,
// deliberatamente più bassa della velocità di jog normale (SET_SPEED), per
// non perdere passi motore su spostamenti così piccoli (fino a 0.005mm).
// Valore di partenza prudente, regolabile, da tarare su macchina reale.
const float JOG_ENCODER_SPEED_MMS = 0.5f;

// --- Jog encoder: pulsante integrato (cambio preset), debounce dedicato ---
const unsigned long JOG_STEP_BUTTON_DEBOUNCE_MS = 50;
bool jog_step_button_last_reading = false;
bool jog_step_button_stable = false;
unsigned long jog_step_button_last_change_ms = 0;

// --- Jog encoder: stato del movimento a step contati in corso ---
long jog_step_target_pulse_count = 0;
bool jog_step_move_active = false;
long jog_step_saved_pulse_delay_micros = 0; // velocità di jog da ripristinare a fine step

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

// --- ADS1220 (ADC esterno SPI, 24 bit) — misura resistenza campioni (piezoresistivo) ---
// Cablato e verificato (vedi CLAUDE.md per schema alimentazione e circuito di misura
// ratiometrico a 4 fili). Canale alternativo all'LCR-meter (Serial2 sopra), mai attivi
// insieme per costruzione della GUI: STATUS "RES_LCR"/"RES_ADS" nel pacchetto D:.
// Nessun pin DRDY dedicato collegato (nessun GPIO libero rimasto): in modalità di
// conversione continua (CM=1) il datasheet garantisce che i dati possano essere letti
// in qualunque momento via RDATA senza rischio di corruzione, riflettendo sempre
// l'ultima conversione completata — quindi si interroga a intervalli invece che via
// interrupt hardware (nel peggiore dei casi si rilegge due volte lo stesso campione).
const int ADS1220_CS_PIN = 5, ADS1220_SCLK_PIN = 21, ADS1220_MOSI_PIN = 26, ADS1220_MISO_PIN = 36;
const float ADS1220_R_REF_OHM = 989.58f; // valore MISURATO della resistenza di riferimento (REFP0-REFN0), non un placeholder
SPIClass ads1220SPI(HSPI);
const SPISettings ADS1220_SPI_SETTINGS(1000000, MSBFIRST, SPI_MODE1); // ADS1220: CPOL=0, CPHA=1

const uint8_t ADS1220_CMD_RESET = 0x06;
const uint8_t ADS1220_CMD_START = 0x08;
const uint8_t ADS1220_CMD_RDATA = 0x10;
const uint8_t ADS1220_CMD_WREG_BASE = 0x40; // 0100 rrnn, rr=registro iniziale, nn=num.registri-1

// Configurazione corrente, validata atomicamente da SET_ADS1220_CONFIG (rifiutato se il
// canale è in polling: vedi processCommand()). Default allineati agli esempi di registro
// verificati da datasheet: Reg0=0x38, Reg1=0x84, Reg2=0x47, Reg3=0x20.
int ads1220_sps = 330;
int ads1220_gain = 16;
bool ads1220_pga_bypass = false;         // richiesta utente; ha effetto solo se gain<8
bool ads1220_pga_bypass_applied = false; // valore realmente scritto nel registro (forzato false se gain>=8)
int ads1220_idac_ua = 1500;
int ads1220_window = 10; // campioni della media mobile, max ADS1220_MAX_WINDOW
bool ads1220_polling_enabled = false;

const int ADS1220_MAX_WINDOW = 20;
volatile float last_ads1220_resistance_ohm = -999.0f;
float ads1220_avg_buffer[ADS1220_MAX_WINDOW];
int ads1220_avg_count = 0;
int ads1220_avg_index = 0;
unsigned long ads1220_last_read_time = 0;

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
void updateKillswitchState();
void onKillswitchTriggered();
void onKillswitchCleared();
void handleJogEncoderMotion();
void handleJogStepButton();
void ads1220WriteReg(uint8_t reg, uint8_t value);
uint8_t ads1220ReadReg(uint8_t reg);
int32_t ads1220ReadData();
void applyAds1220Config();
void updateADS1220Reading();

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

  // --- JOG ENCODER: controllo manuale fisico (non l'encoder esterno sopra) ---
  pinMode(JOG_ENCODER_A, INPUT_PULLUP);
  pinMode(JOG_ENCODER_B, INPUT_PULLUP);
  pinMode(JOG_ENCODER_SW, INPUT_PULLUP);
  // Stato iniziale, per non generare un delta spurio alla prima transizione.
  jog_encoder_last_state = (digitalRead(JOG_ENCODER_A) << 1) | digitalRead(JOG_ENCODER_B);
  attachInterrupt(digitalPinToInterrupt(JOG_ENCODER_A), handleJogEncoderChange, CHANGE);
  attachInterrupt(digitalPinToInterrupt(JOG_ENCODER_B), handleJogEncoderChange, CHANGE);

  // --- KILLSWITCH: stato iniziale letto PRIMA di attaccare l'interrupt, per
  // sapere subito (anche a boot) se è già premuto. Nessun INPUT_PULLUP: GPIO39
  // è input-only e privo di pull interni, il pull-up 10k verso 3.3V è esterno.
  pinMode(KILLSWITCH_SENSE_PIN, INPUT);
  bool killswitch_initial_high = (digitalRead(KILLSWITCH_SENSE_PIN) == HIGH);
  killswitch_raw_high = killswitch_initial_high;
  killswitch_last_high_ms = millis();
  killswitch_engaged = killswitch_initial_high;
  // Se il killswitch è già premuto all'avvio (es. riavvio durante
  // un'emergenza), la posizione va considerata non verificata fin da subito,
  // esattamente come dopo un trigger normale a macchina già accesa.
  position_unverified = killswitch_initial_high;
  attachInterrupt(digitalPinToInterrupt(KILLSWITCH_SENSE_PIN), handleKillswitchChange, CHANGE);
  if (killswitch_engaged) {
    Serial.println("STATUS:KILLSWITCH_TRIGGERED");
  }

  Wire.begin(LOADCELL_SDA_PIN, LOADCELL_SCL_PIN);
  scale.begin(Wire);
  scale.setGain(NAU7802_GAIN_128); // Esplicito per chiarezza (coincide col default interno di begin())
  scale.setSampleRate(NAU7802_SPS_320);
  scale.calibrateAFE();
  scale.setCalibrationFactor(1.0);
  // Nessun auto-zero: la cella va sempre ri-tarata dopo il boot (comportamento invariato)

  // --- ADS1220: init SPI (pin custom via GPIO matrix) e reset del chip ---
  pinMode(ADS1220_CS_PIN, OUTPUT);
  digitalWrite(ADS1220_CS_PIN, HIGH); // idle
  ads1220SPI.begin(ADS1220_SCLK_PIN, ADS1220_MISO_PIN, ADS1220_MOSI_PIN, ADS1220_CS_PIN);
  delay(1); // margine su t_STARTUP dopo power-up prima del primo comando
  ads1220SPI.beginTransaction(ADS1220_SPI_SETTINGS);
  digitalWrite(ADS1220_CS_PIN, LOW);
  ads1220SPI.transfer(ADS1220_CMD_RESET);
  digitalWrite(ADS1220_CS_PIN, HIGH);
  ads1220SPI.endTransaction();
  delay(1); // margine su t_RESET
  applyAds1220Config(); // scrive i registri di default; il canale resta comunque
                         // spento (ads1220_polling_enabled=false) finché non
                         // arriva ENABLE_ADS1220_POLLING

  // Timer hardware: prescaler 80 → 1 tick = 1 µs
  stepTimer = timerBegin(0, 80, true);
  timerAttachInterrupt(stepTimer, &onStepTimer, true);
  timerAlarmWrite(stepTimer, pulse_delay_micros, true);
  timerAlarmEnable(stepTimer);
  Serial.println("ESP32 avviato con timer hardware passi.");
}

void loop()
{
  // Killswitch: sempre attivo, indipendente da motor_state/comms_mode,
  // valutato per primo a ogni giro di loop (vedi updateKillswitchState()).
  updateKillswitchState();

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
  handleHardwareInputs();     // pulsanti manuali fisici Up/Down (riabilitato in questa sessione)
  handleJogEncoderMotion();   // consumo del delta accumulato dal jog encoder (movimento a step)
  handleJogStepButton();      // pulsante integrato del jog encoder (cambio preset di step)
  updateLCRReading();
  updateADS1220Reading();
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

  else if (command == "ENABLE_ADS1220_POLLING") {
      applyAds1220Config(); // (ri)applica la config corrente e riavvia le conversioni
      ads1220_polling_enabled = true;
      ads1220_last_read_time = 0;
      Serial.println("STATUS:ADS1220_POLLING_ENABLED");
      return;
  } else if (command == "DISABLE_ADS1220_POLLING") {
      ads1220_polling_enabled = false;
      last_ads1220_resistance_ohm = -999.0f;
      Serial.println("STATUS:ADS1220_POLLING_DISABLED");
      return;
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
    Serial.print(last_lcr_resistance, 4); // 5. Resistance (RES_LCR)
    Serial.print(";");
    Serial.print(readEncoderPosition()); // 6. Encoder count (grezzo, sola lettura)
    Serial.print(";");
    Serial.println(last_ads1220_resistance_ohm, 4); // 7. Resistance (RES_ADS)
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
  else if (command.startsWith("SET_ADS1220_CONFIG:"))
  {
    // Configurabile solo a canale fermo: evita discontinuità/artefatti sui dati
    // di un test in corso (race fra scrittura registri e conversione a metà,
    // cambio del divisore di conversione non sincrono col cambio gain, media
    // mobile con finestra che cambia a buffer pieno).
    if (ads1220_polling_enabled) {
      Serial.println("STATUS:ADS1220_CONFIG_REJECTED;REASON=POLLING_ACTIVE");
    } else {
      String params = command.substring(19); // len("SET_ADS1220_CONFIG:") == 19
      int sps_idx = params.indexOf("SPS=");
      int gain_idx = params.indexOf("GAIN=");
      int bypass_idx = params.indexOf("PGA_BYPASS=");
      int idac_idx = params.indexOf("IDAC=");
      int window_idx = params.indexOf("WINDOW=");

      bool valid = (sps_idx != -1 && gain_idx != -1 && bypass_idx != -1 && idac_idx != -1 && window_idx != -1);
      int new_sps = 0, new_gain = 0, new_idac = 0, new_window = 0;
      bool new_bypass_requested = false;

      if (valid) {
        new_sps = params.substring(sps_idx + 4, params.indexOf(';', sps_idx)).toInt();
        new_gain = params.substring(gain_idx + 5, params.indexOf(';', gain_idx)).toInt();
        new_bypass_requested = (params.substring(bypass_idx + 11, params.indexOf(';', bypass_idx)).toInt() != 0);
        new_idac = params.substring(idac_idx + 5, params.indexOf(';', idac_idx)).toInt();
        new_window = params.substring(window_idx + 7).toInt();

        valid = (new_sps == 20 || new_sps == 45 || new_sps == 90 || new_sps == 175 ||
                 new_sps == 330 || new_sps == 600 || new_sps == 1000) &&
                (new_gain == 1 || new_gain == 2 || new_gain == 4 || new_gain == 8 ||
                 new_gain == 16 || new_gain == 32 || new_gain == 64 || new_gain == 128) &&
                (new_idac == 0 || new_idac == 10 || new_idac == 50 || new_idac == 100 ||
                 new_idac == 250 || new_idac == 500 || new_idac == 1000 || new_idac == 1500) &&
                (new_window >= 1 && new_window <= ADS1220_MAX_WINDOW);
      }

      if (valid) {
        ads1220_sps = new_sps;
        ads1220_gain = new_gain;
        ads1220_pga_bypass = new_bypass_requested;
        ads1220_idac_ua = new_idac;
        ads1220_window = new_window;
        applyAds1220Config(); // scrive i registri (forza PGA attivo se gain>=8), riavvia le conversioni

        // PGA_BYPASS riportato qui è il valore REALMENTE applicato (ads1220_pga_bypass_applied),
        // non necessariamente quello richiesto: se gain>=8 il firmware ignora silenziosamente
        // la richiesta di bypass (PGA sempre attivo per datasheet) e lo segnala così, senza
        // rifiutare il comando per questo.
        Serial.println("STATUS:ADS1220_CONFIG_SET;SPS=" + String(ads1220_sps) +
                        ";GAIN=" + String(ads1220_gain) +
                        ";PGA_BYPASS=" + String(ads1220_pga_bypass_applied ? 1 : 0) +
                        ";IDAC=" + String(ads1220_idac_ua) +
                        ";WINDOW=" + String(ads1220_window));
      } else {
        Serial.println("STATUS:ADS1220_CONFIG_REJECTED;REASON=OUT_OF_RANGE");
      }
    }
  }
  else if (command == "GET_ADS1220_CONFIG")
  {
    Serial.println("STATUS:ADS1220_CONFIG;SPS=" + String(ads1220_sps) +
                    ";GAIN=" + String(ads1220_gain) +
                    ";PGA_BYPASS=" + String(ads1220_pga_bypass_applied ? 1 : 0) +
                    ";IDAC=" + String(ads1220_idac_ua) +
                    ";WINDOW=" + String(ads1220_window));
  }
  else if (command == "DEBUG_ADS1220")
  {
    // Diagnostica temporanea (da rimuovere a valutazione completata, stesso
    // pattern di DEBUG_RAW_KILLSWITCH usato in passato): rilegge i 4
    // registri via RREG (per verificare che le WREG di applyAds1220Config()
    // siano realmente arrivate al chip) e fa una RDATA immediata, a
    // prescindere da ads1220_polling_enabled.
    uint8_t r0 = ads1220ReadReg(0);
    uint8_t r1 = ads1220ReadReg(1);
    uint8_t r2 = ads1220ReadReg(2);
    uint8_t r3 = ads1220ReadReg(3);
    int32_t raw = ads1220ReadData();
    float r_x = ((float)raw / (8388608.0f * (float)ads1220_gain)) * ADS1220_R_REF_OHM;
    Serial.print("STATUS:ADS1220_DEBUG;REG0=0x"); Serial.print(r0, HEX);
    Serial.print(";REG1=0x"); Serial.print(r1, HEX);
    Serial.print(";REG2=0x"); Serial.print(r2, HEX);
    Serial.print(";REG3=0x"); Serial.print(r3, HEX);
    Serial.print(";RAW="); Serial.print(raw);
    Serial.print(";RES="); Serial.println(r_x, 4);
  }
  else if (command == "GET_KILLSWITCH_STATE")
  {
    Serial.println("STATUS:KILLSWITCH_STATE;ENGAGED=" + String(killswitch_engaged ? 1 : 0) +
                    ";POSITION_UNVERIFIED=" + String(position_unverified ? 1 : 0));
  }
  else if (command == "RETURN_TO_START")
  {
    // Stessa guardia di GOTO, per lo stesso motivo: si basa su pulse_count,
    // non più affidabile mentre la posizione non è verificata (stato
    // "giallo" o "rosso"). Un HOME riuscito è l'unico modo per sbloccarlo.
    if (position_unverified) {
      Serial.println("STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED");
    } else {
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
  }
  else if (command.startsWith("START_CYCLIC_TEST:"))
  {
    // Bloccato mentre la posizione non è verificata (stato "giallo" o
    // "rosso"): un HOME riuscito è l'unico modo per sbloccare l'avvio prove.
    if (position_unverified) {
      Serial.println("STATUS:TEST_START_REJECTED;REASON=POSITION_UNVERIFIED");
      return;
    }
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
    // Bloccato mentre la posizione non è verificata (stato "giallo" o
    // "rosso"): un HOME riuscito è l'unico modo per sbloccare l'avvio prove.
    if (position_unverified) {
      Serial.println("STATUS:TEST_START_REJECTED;REASON=POSITION_UNVERIFIED");
      return;
    }
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
      // Permesso in stato "giallo" (position_unverified ma killswitch non
      // premuto ora), bloccato solo in stato "rosso" (killswitch_engaged).
      // Stessa regola da riusare per i futuri JOG fisici (pulsanti/encoder).
      if (!killswitch_engaged) {
        motor_state = JOG_UP;
        startMotor(true);
      } else {
        Serial.println("STATUS:JOG_REJECTED;REASON=KILLSWITCH_ENGAGED");
      }
    }
    else if (command == "JOG_DOWN") {
      if (!killswitch_engaged) {
        motor_state = JOG_DOWN;
        startMotor(false);
      } else {
        Serial.println("STATUS:JOG_REJECTED;REASON=KILLSWITCH_ENGAGED");
      }
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
      // Bloccato mentre la posizione non è verificata (stato "giallo" o
      // "rosso"): l'unico modo per uscirne è un HOME riuscito.
      if (position_unverified) {
        Serial.println("STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED");
      } else {
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
}


// --- Lettura pulsanti manuali fisici Up/Down (con debounce software) ---
// Riusa esattamente la stessa funzione di movimento (startMotor()/stopMotor())
// già usata da JOG_UP/JOG_DOWN via seriale: nessuna nuova logica di
// movimento, solo un nuovo modo di attivarla. Guardia di attivazione
// condivisa con il jog encoder: isManualJogAllowed() (unico punto di
// verità, vedi sopra). Se la guardia non è soddisfatta, il pulsante non fa
// nulla (nessun comando/messaggio inviato al PC).
void handleHardwareInputs()
{
  unsigned long now = millis();

  // --- Pulsante UP ---
  bool up_raw = (digitalRead(UP_BUTTON_PIN) == LOW);
  if (up_raw != up_button_last_reading) {
    up_button_last_reading = up_raw;
    up_button_last_change_ms = now;
  } else if (now - up_button_last_change_ms >= BUTTON_DEBOUNCE_MS && up_raw != up_button_stable) {
    up_button_stable = up_raw;
    if (up_button_stable) {
      if (isManualJogAllowed()) {
        motor_state = JOG_UP;
        is_hardware_jog_active = true;
        up_button_owns_jog = true;
        startMotor(true);
      }
    } else if (up_button_owns_jog) {
      // Rilascio: ferma esattamente come il rilascio del JOG da GUI (che
      // invia il comando STOP). Se il motore è già stato fermato da
      // un'altra causa nel frattempo (endstop, limite, killswitch), ci
      // limitiamo a richiudere la contabilità senza toccarlo di nuovo.
      up_button_owns_jog = false;
      is_hardware_jog_active = false;
      if (motor_state == JOG_UP) {
        motor_state = STOPPED;
        stopMotor();
        Serial.println("STATUS:STOPPED_BY_USER");
      }
    }
  }

  // --- Pulsante DOWN (stessa logica, direzione opposta) ---
  bool down_raw = (digitalRead(DOWN_BUTTON_PIN) == LOW);
  if (down_raw != down_button_last_reading) {
    down_button_last_reading = down_raw;
    down_button_last_change_ms = now;
  } else if (now - down_button_last_change_ms >= BUTTON_DEBOUNCE_MS && down_raw != down_button_stable) {
    down_button_stable = down_raw;
    if (down_button_stable) {
      if (isManualJogAllowed()) {
        motor_state = JOG_DOWN;
        is_hardware_jog_active = true;
        down_button_owns_jog = true;
        startMotor(false);
      }
    } else if (down_button_owns_jog) {
      down_button_owns_jog = false;
      is_hardware_jog_active = false;
      if (motor_state == JOG_DOWN) {
        motor_state = STOPPED;
        stopMotor();
        Serial.println("STATUS:STOPPED_BY_USER");
      }
    }
  }
}

// --- Jog encoder: consumo del delta accumulato (movimento a step contati) ---
// Chiamato ogni giro di loop(). Se un movimento a step è già in corso, ne
// segue solo il completamento (o l'interruzione per un'altra causa:
// endstop, limite assoluto, killswitch — già tutte gestite altrove; qui ci
// limitiamo a richiudere la contabilità). Se non c'è un movimento in corso,
// converte il delta in sospeso (se non zero e se la guardia lo permette) in
// un singolo movimento relativo di (delta * step_size_corrente), usando
// esattamente lo stesso motor_state JOG_UP/JOG_DOWN e la stessa startMotor()
// già usati dal jog normale — eredita così automaticamente lo stesso
// controllo di endstop e limiti assoluti applicato in updateMotorState()
// (a differenza del meccanismo target_steps_remaining usato da GOTO, che
// non viene qui utilizzato proprio per non perdere quel controllo). Se la
// guardia non è soddisfatta o un movimento precedente è ancora in corso, il
// delta resta accumulato nel contatore (non viene consumato né perso) fino
// al turno in cui potrà essere gestito correttamente.
void handleJogEncoderMotion() {
  if (jog_step_move_active) {
    if (motor_state != JOG_UP && motor_state != JOG_DOWN) {
      // Il movimento si è fermato per un'altra ragione (endstop, limite
      // assoluto, killswitch, STOP/'!'): richiudi solo la contabilità, il
      // motore è già fermo.
      jog_step_move_active = false;
      is_hardware_jog_active = false;
      pulse_delay_micros = jog_step_saved_pulse_delay_micros;
      timerAlarmWrite(stepTimer, pulse_delay_micros, true);
    } else if ((dir_up && pulse_count >= jog_step_target_pulse_count) ||
               (!dir_up && pulse_count <= jog_step_target_pulse_count)) {
      motor_state = STOPPED;
      stopMotor();
      jog_step_move_active = false;
      is_hardware_jog_active = false;
      pulse_delay_micros = jog_step_saved_pulse_delay_micros;
      timerAlarmWrite(stepTimer, pulse_delay_micros, true);
    }
    return; // non iniziare un nuovo movimento finché questo non è concluso
  }

  long delta;
  portENTER_CRITICAL(&jog_encoder_mux);
  delta = jogEncoderCount;
  portEXIT_CRITICAL(&jog_encoder_mux);

  if (delta == 0) return;
  if (!isManualJogAllowed()) return; // resta in coda, consumato quando la guardia lo permetterà

  portENTER_CRITICAL(&jog_encoder_mux);
  jogEncoderCount -= delta; // consuma solo il delta effettivamente usato
  portEXIT_CRITICAL(&jog_encoder_mux);

  float step_mm = JOG_STEP_SIZES_MM[jog_step_preset_index];
  long steps = lround((double)labs(delta) * step_mm / PULSES_TO_MM);
  if (steps <= 0) return; // non dovrebbe accadere con i preset attuali

  bool up = (delta > 0);
  jog_step_saved_pulse_delay_micros = pulse_delay_micros; // per ripristinare la velocità di jog normale a fine step
  setMotorSpeed(JOG_ENCODER_SPEED_MMS);
  jog_step_target_pulse_count = pulse_count + (up ? steps : -steps);
  jog_step_move_active = true;
  is_hardware_jog_active = true;
  motor_state = up ? JOG_UP : JOG_DOWN;
  startMotor(up);
}

// --- Jog encoder: pulsante integrato, ciclo dei 3 preset di dimensione step ---
void handleJogStepButton() {
  unsigned long now = millis();
  bool raw_pressed = (digitalRead(JOG_ENCODER_SW) == LOW);

  if (raw_pressed != jog_step_button_last_reading) {
    jog_step_button_last_reading = raw_pressed;
    jog_step_button_last_change_ms = now;
  } else if (now - jog_step_button_last_change_ms >= JOG_STEP_BUTTON_DEBOUNCE_MS &&
             raw_pressed != jog_step_button_stable) {
    jog_step_button_stable = raw_pressed;
    if (jog_step_button_stable) { // fronte di pressione
      jog_step_preset_index = (jog_step_preset_index + 1) % 3;
      Serial.println("STATUS:JOG_STEP_SIZE_SET;MM=" + String(JOG_STEP_SIZES_MM[jog_step_preset_index], 4));
    }
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

// --- KILLSWITCH: gestione stato e reazioni ---
void onKillswitchTriggered() {
  // Stesso path già usato per lo stop di emergenza esistente ('!'): ferma
  // subito qualunque movimento motore in corso, in QUALUNQUE motor_state
  // (jog, homing, test monotonico/ciclico), e riporta in POLLING.
  bool was_monotonic = (motor_state == MONOTONIC_TEST);
  bool was_cyclic = (motor_state == CYCLIC_TEST);
  bool was_test = was_monotonic || was_cyclic;

  motor_state = STOPPED;
  comms_mode = POLLING;
  stopMotor();
  target_steps_remaining = 0; // azzera un eventuale movimento a passi contati (GOTO/RETURN_TO_START)

  position_unverified = true;

  Serial.println("STATUS:KILLSWITCH_TRIGGERED");
  if (was_test) {
    // Pacchetto dedicato (distinto da TEST_STOPPED_BY_USER/CYCLIC_TEST_STOPPED_BY_USER,
    // che implicano uno stop volontario dell'utente): permette al software Python
    // di chiudere il file della prova mantenendo tutti i dati già acquisiti,
    // marcandolo come interrotto da killswitch e non come completato/stoppato normalmente.
    Serial.println("STATUS:TEST_ABORTED;REASON=KILLSWITCH");
  }
}

void onKillswitchCleared() {
  Serial.println("STATUS:KILLSWITCH_CLEARED");
  // position_unverified resta true: solo un HOME completato con successo lo
  // riporta a false (vedi HOMING_FINAL_LIFT in updateMotorState()).
}

void updateKillswitchState() {
  bool raw_high = killswitch_raw_high;
  unsigned long last_high = killswitch_last_high_ms;

  if (raw_high) {
    // Pressione: rilevamento immediato, nessun debounce.
    if (!killswitch_engaged) {
      killswitch_engaged = true;
      onKillswitchTriggered();
    }
  } else if (killswitch_engaged && (millis() - last_high >= KILLSWITCH_RELEASE_DEBOUNCE_MS)) {
    // Rilascio: solo dopo ~200ms di stato stabile a LOW (nessun fronte a
    // HIGH nel frattempo, altrimenti killswitch_last_high_ms si sarebbe
    // aggiornato di nuovo), per non scambiare un rimbalzo meccanico in
    // rilascio per un vero rilascio.
    killswitch_engaged = false;
    onKillswitchCleared();
  }
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

// --- ADS1220: SPI a basso livello ---
void ads1220WriteReg(uint8_t reg, uint8_t value) {
  ads1220SPI.beginTransaction(ADS1220_SPI_SETTINGS);
  digitalWrite(ADS1220_CS_PIN, LOW);
  ads1220SPI.transfer(ADS1220_CMD_WREG_BASE | (reg << 2)); // nn=00 -> scrive 1 solo registro
  ads1220SPI.transfer(value);
  digitalWrite(ADS1220_CS_PIN, HIGH);
  ads1220SPI.endTransaction();
}

// Rilettura di un registro via RREG — usata solo da DEBUG_ADS1220 (diagnostica
// temporanea) per verificare che le scritture WREG di applyAds1220Config()
// siano realmente arrivate al chip.
uint8_t ads1220ReadReg(uint8_t reg) {
  ads1220SPI.beginTransaction(ADS1220_SPI_SETTINGS);
  digitalWrite(ADS1220_CS_PIN, LOW);
  ads1220SPI.transfer(0x20 | (reg << 2)); // RREG, nn=00 -> legge 1 solo registro
  uint8_t value = ads1220SPI.transfer(0x00);
  digitalWrite(ADS1220_CS_PIN, HIGH);
  ads1220SPI.endTransaction();
  return value;
}

// Legge l'ultima conversione via RDATA (sicuro in continuous conversion mode
// anche senza pin DRDY, vedi commento sui globali ADS1220 sopra) e la
// sign-extende da 24 a 32 bit.
int32_t ads1220ReadData() {
  ads1220SPI.beginTransaction(ADS1220_SPI_SETTINGS);
  digitalWrite(ADS1220_CS_PIN, LOW);
  ads1220SPI.transfer(ADS1220_CMD_RDATA);
  uint32_t b0 = ads1220SPI.transfer(0x00);
  uint32_t b1 = ads1220SPI.transfer(0x00);
  uint32_t b2 = ads1220SPI.transfer(0x00);
  digitalWrite(ADS1220_CS_PIN, HIGH);
  ads1220SPI.endTransaction();
  uint32_t raw = (b0 << 16) | (b1 << 8) | b2;
  if (raw & 0x800000) raw |= 0xFF000000; // segno su 24 bit -> sign-extend a 32
  return (int32_t)raw;
}

// Scrive i 4 registri di configurazione a partire dai parametri correnti
// (ads1220_sps/gain/pga_bypass/idac/window) e riavvia le conversioni con
// START/SYNC — necessario in continuous conversion mode dopo aver scritto i
// registri, altrimenti la nuova configurazione non viene applicata alla
// conversione in corso. Chiamata sia da ENABLE_ADS1220_POLLING sia da un
// SET_ADS1220_CONFIG riuscito (quest'ultimo è comunque bloccato mentre il
// canale è già in polling, vedi processCommand()).
void applyAds1220Config() {
  uint8_t gain_bits;
  switch (ads1220_gain) {
    case 1: gain_bits = 0; break; case 2: gain_bits = 1; break;
    case 4: gain_bits = 2; break; case 8: gain_bits = 3; break;
    case 16: gain_bits = 4; break; case 32: gain_bits = 5; break;
    case 64: gain_bits = 6; break; default: gain_bits = 7; break; // 128
  }
  uint8_t dr_bits;
  switch (ads1220_sps) {
    case 20: dr_bits = 0; break; case 45: dr_bits = 1; break;
    case 90: dr_bits = 2; break; case 175: dr_bits = 3; break;
    case 330: dr_bits = 4; break; case 600: dr_bits = 5; break;
    default: dr_bits = 6; break; // 1000
  }
  uint8_t idac_bits;
  switch (ads1220_idac_ua) {
    case 0: idac_bits = 0; break; case 10: idac_bits = 1; break;
    case 50: idac_bits = 2; break; case 100: idac_bits = 3; break;
    case 250: idac_bits = 4; break; case 500: idac_bits = 5; break;
    case 1000: idac_bits = 6; break; default: idac_bits = 7; break; // 1500
  }

  // PGA_BYPASS ha effetto solo per gain<8: per gain>=8 il PGA deve restare
  // sempre attivo (obbligatorio da datasheet), qualunque fosse la richiesta.
  ads1220_pga_bypass_applied = (ads1220_gain < 8) ? ads1220_pga_bypass : false;

  // Reg0: MUX=0011 (AIN1-AIN2 differenziale, fisso) | GAIN (3 bit) | PGA_BYPASS (1 bit)
  uint8_t reg0 = (0b0011 << 4) | (gain_bits << 1) | (ads1220_pga_bypass_applied ? 1 : 0);
  // Reg1: DR (3 bit) | MODE=00 (Normal, fisso) | CM=1 (continuous, fisso) | TS=0 | BCS=0
  uint8_t reg1 = (dr_bits << 5) | (0b00 << 3) | (1 << 2);
  // Reg2: VREF=01 (esterno REFP0/REFN0, fisso) | 50/60=00 (filtro sempre spento, obbligatorio
  // da datasheet per SPS != 20 in Normal mode; tenuto spento anche a 20 SPS) | PSW=0 | IDAC (3 bit)
  uint8_t reg2 = (0b01 << 6) | (0b00 << 4) | idac_bits;
  // Reg3: I1MUX=001 (IDAC1->AIN0/REFP1, fisso) | I2MUX=000 (disabilitato, fisso) | DRDYM=0 | RESERVED=0
  uint8_t reg3 = (0b001 << 5);

  ads1220WriteReg(0, reg0);
  ads1220WriteReg(1, reg1);
  ads1220WriteReg(2, reg2);
  ads1220WriteReg(3, reg3);

  ads1220SPI.beginTransaction(ADS1220_SPI_SETTINGS);
  digitalWrite(ADS1220_CS_PIN, LOW);
  ads1220SPI.transfer(ADS1220_CMD_START);
  digitalWrite(ADS1220_CS_PIN, HIGH);
  ads1220SPI.endTransaction();

  // La finestra della media mobile può essere cambiata solo a canale fermo
  // (vedi guardia in processCommand()): azzerare qui è sempre sicuro.
  ads1220_avg_count = 0;
  ads1220_avg_index = 0;
  ads1220_last_read_time = 0;
}

// Interrogazione a intervalli (RDATA), rate-limitata al sample rate corrente:
// interrogare più veloce di quanto il chip produca nuovi campioni è sicuro in
// continuous conversion mode (si rilegge al più due volte lo stesso valore),
// ma inutile, quindi ci si allinea al periodo di conversione per non sprecare
// cicli di loop() / banda SPI. Media mobile su ads1220_window campioni.
void updateADS1220Reading() {
  if (!ads1220_polling_enabled) {
    last_ads1220_resistance_ohm = -999.0f;
    return;
  }

  unsigned long now = millis();
  unsigned long interval_ms = 1000UL / (unsigned long)ads1220_sps;
  if (interval_ms < 1) interval_ms = 1;
  if (now - ads1220_last_read_time < interval_ms) return;
  ads1220_last_read_time = now;

  int32_t raw = ads1220ReadData();
  // R_x = (rawData / (2^23 * gain)) * R_ref — il valore di IDAC non entra nella
  // formula (si semplifica ratiometricamente, stessa corrente attraversa R_ref
  // e R_x in serie): influisce solo su rumore/autoriscaldamento del campione.
  float r_x = ((float)raw / (8388608.0f * (float)ads1220_gain)) * ADS1220_R_REF_OHM;

  ads1220_avg_buffer[ads1220_avg_index] = r_x;
  ads1220_avg_index = (ads1220_avg_index + 1) % ads1220_window;
  if (ads1220_avg_count < ads1220_window) ads1220_avg_count++;

  float sum = 0.0f;
  for (int i = 0; i < ads1220_avg_count; i++) sum += ads1220_avg_buffer[i];
  last_ads1220_resistance_ohm = sum / (float)ads1220_avg_count;
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
        // Un HOME riuscito è l'unico modo per uscire da position_unverified
        // (impostato true da un trigger del killswitch, o dal boot se era
        // già premuto all'avvio).
        position_unverified = false;
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
      Serial.print(last_lcr_resistance, 4); // RES_LCR
      Serial.print(";");
      Serial.print(readEncoderPosition());
      Serial.print(";");
      Serial.println(last_ads1220_resistance_ohm, 4); // RES_ADS
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
