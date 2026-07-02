# firmware: Controllo-Macchina-ESP32/src/main.cpp

> File in un repository separato:
> `c:\Users\carlo\Documents\PlatformIO\Projects\Controllo-Macchina-ESP32\src\main.cpp`
> (progetto PlatformIO/Arduino, ~1220 righe, unico file sorgente).

## Scopo

Firmware dell'ESP32 che pilota fisicamente la macchina: motore passo-passo
(vite senza fine) per il movimento, cella di carico NAU7802 (I2C) per la
forza, endstop meccanici, e opzionalmente un LCR-meter esterno via UART2.
Espone
un protocollo seriale a comandi testuali (vedi `CLAUDE.md`, sezione
"Protocollo di comunicazione seriale") e implementa da solo tutta la logica
di temporizzazione, sicurezza e macchine a stati dei test — la GUI Python
invia comandi ad alto livello e riceve stato/dati, ma non ha visibilità sui
dettagli di esecuzione (passi, ISR, timer hardware).

## Classi e funzioni principali

Non è C++ orientato agli oggetti: stato globale + funzioni. Le funzioni
principali sono:

- **`setup()`**: inizializza seriali (`Serial` a 460800 baud per la GUI,
  `Serial2` a 115200 baud sui pin 16/17 per l'LCR-meter), pin, bus I2C per
  la cella di carico (`Wire.begin(LOADCELL_SDA_PIN, LOADCELL_SCL_PIN)` sui
  pin 32/33 — riuso del cablaggio fisico già esistente per evitare il
  conflitto tra il pin I2C SCL di default dell'ESP32, GPIO22, e
  `TOP_ENDSTOP_PIN`), il sensore NAU7802 (`scale.begin(Wire)`,
  `scale.setGain(NAU7802_GAIN_128)` — esplicito per non dipendere
  silenziosamente dal default interno della libreria, anche se oggi
  coincide con esso —, `scale.setSampleRate(NAU7802_SPS_320)`,
  `scale.calibrateAFE()`, `scale.setCalibrationFactor(1.0)` — **nessun
  auto-zero**: la cella va sempre ri-tarata dopo ogni boot, invariato
  rispetto a prima), e il timer hardware (`stepTimer`) che genera gli
  impulsi di step via interrupt.
- **`loop()`**: gestisce il completamento di movimenti a passi contati
  (`move_completed_flag`), poi chiama in sequenza `handleSerialCommands()`,
  `handleDataStreaming()`, `updateMotorState()`, `updateLCRReading()`.
  `handleHardwareInputs()` (pulsanti fisici) è **disabilitata** (commentata).
- **`onStepTimer()`** (ISR, `IRAM_ATTR`): alterna il pin di step,
  incrementa/decrementa `pulse_count`, e decrementa `target_steps_remaining`
  per i movimenti a conteggio (homing, return-to-start, pre-posizionamento
  ciclico), impostando `move_completed_flag` quando arriva a 0.
- **`handleSerialCommands()`**: legge caratteri non bloccante da `Serial`;
  intercetta `'!'` **immediatamente**, prima di aspettare `'\n'`, per lo stop
  di emergenza; altrimenti accumula in `serial_buffer` (max 128 caratteri)
  fino a `'\n'`, poi chiama `processCommand()`.
- **`processCommand(command)`**: dispatcher a catena di
  `if/else if command == ...` / `command.startsWith(...)`. Gestisce tutti i
  comandi elencati in `CLAUDE.md`, incluso
  `SET_FILTER_CONFIG:ALPHA=<val>;RATE=<sps>;GAIN=<val>` (valida alpha in
  [0.01,1.0], rate in {10,20,40,80,320} SPS e, se presente, gain in
  {1,2,4,8,16,32,64,128}; se non validi ignora l'intero comando e risponde
  `STATUS:FILTER_CONFIG_REJECTED;REASON=OUT_OF_RANGE` senza modificare nulla;
  **`GAIN` è opzionale** — se assente, il gain corrente resta invariato,
  per retrocompatibilità con comandi già inviati prima dell'introduzione di
  questo parametro; su successo aggiorna `filter_alpha`/`filter_rate_sps`/
  `filter_pga_gain`, chiama `scale.setSampleRate()`/`scale.setGain()`,
  ri-semina l'EMA e risponde
  `STATUS:FILTER_CONFIG_SET;ALPHA=..;RATE=..;GAIN=..`. **Se il gain cambia
  realmente rispetto al valore precedente**, invalida esplicitamente
  `zero_offset`/`calibration_factor` (`scale.setZeroOffset(0)`,
  `scale.setCalibrationFactor(1.0)`) ed emette anche
  `STATUS:CALIBRATION_INVALIDATED;REASON=GAIN_CHANGED` — cambiare gain
  altera la relazione tra conteggi ADC grezzi e grammi, quindi offset e
  fattore di scala calcolati al gain precedente non sono più validi) e
  `GET_FILTER_CONFIG` (risponde con la configurazione corrente incluso
  `GAIN`, sullo stesso modello di `GET_SCALE` — vedi Punti di attenzione).
  I comandi con parametri (`START_CYCLIC_TEST`, `EXECUTE_RAMP`,
  `SET_LIMITS`, `START_TEST`, `SET_FILTER_CONFIG`) fanno parsing manuale
  con `indexOf`/`substring` (vedi Punti di attenzione).
- **`updateMotorState()`**: chiamata ad ogni `loop()`. In ordine:
  1. Controllo dei **limiti di sicurezza assoluti** (`absolute_max_pulse_count`,
     `absolute_max_force_grams`, quest'ultimo confrontato direttamente col
     valore già filtrato via EMA — vedi `readLoadNonBlocking()` sotto) → se
     superati, ferma tutto ed emette `STATUS:LIMIT_HIT_DISPLACEMENT`/
     `LIMIT_HIT_FORCE` (una sola volta per evento, tramite
     `limit_hit_notification_sent`).
  2. Controllo endstop meccanici (`TOP_HIT`/`BOTTOM_HIT`), ignorando il
     bottom endstop durante l'homing.
  3. Macchina a stati dell'**homing** (`HOMING_FAST → HOMING_BACKOFF →
     HOMING_SLOW → HOMING_FINAL_LIFT`), che azzera `pulse_count` solo alla
     fine.
  4. Verifica dello stop criterion del **test monotonico**
     (`CRITERION_DISP`/`CRITERION_FORCE`, confronto diretto sul valore
     filtrato per il ramo Forza).
  5. Macchina a stati del **test ciclico** (`switch(cyclic_phase)`):
     `CYCLIC_PREPOSITION → CYCLIC_MOVING_UP → CYCLIC_HOLDING_UPPER →
     CYCLIC_MOVING_DOWN → CYCLIC_HOLDING_LOWER` (ripetuto per
     `cyclic_target_cycles` cicli), più i rami paralleli `CYCLIC_PAUSED`
     (per i blocchi pausa) e `RAMPING → RAMP_HOLDING` (per i blocchi rampa).
     Ogni fine-blocco emette `STATUS:BLOCK_COMPLETED`, che la GUI Python
     interpreta per avanzare alla `main.py::handle_data_from_esp32()`.
- **`readLoadNonBlocking()`**: legge la cella di carico in modo non
  bloccante via `scale.available()`/`scale.getReading()` (polling, nessun
  interrupt su DRDY) e applica un **filtro EMA** al valore convertito in
  grammi: `filtered = alpha*raw + (1-alpha)*filtered_precedente`, con
  `alpha`, sample rate e guadagno PGA configurabili a runtime via
  `SET_FILTER_CONFIG` (di default 0.5 / 320 SPS / 128x — quest'ultimo
  coincide col default interno della libreria NAU7802). Il primo campione
  dopo il boot, dopo `TARE`, dopo `CALIBRATE`/`SET_SCALE` o dopo un
  `SET_FILTER_CONFIG` valido **ri-semina** il filtro (`filter_seeded =
  false`) per evitare un breve transitorio in cui l'EMA insegue un valore
  reso obsoleto dal cambio di offset/scala/alpha/gain.
  Il valore filtrato sostituisce `last_load_grams` ovunque nel firmware:
  controlli di sicurezza, stop criterion, e pacchetti `D:`/`GET_DATA` verso
  il PC — non esiste più un canale "raw" separato.
- **`handleDataStreaming()`**: chiama `readLoadNonBlocking()` e, se
  `comms_mode == STREAMING`, emette un pacchetto `D:` ogni
  `STREAM_INTERVAL_MS` (20 ms → 50 Hz) con il valore di carico già filtrato.
- **`TARE`/`CALIBRATE:<grammi>`**: entrambi mediano su una **finestra di
  tempo fissa di 1000 ms**, non su un numero fisso di campioni — il loop
  interroga `scale.available()` ogni ~2 ms (più spesso di quanto il NAU7802
  produca nuove conversioni a qualunque RATE supportato, quindi non perde
  campioni), quindi il numero di campioni effettivamente mediati scala col
  sample rate configurato (~320 campioni a RATE=320, ~10 a RATE=10).
- **`updateLCRReading()`**: macchina a stati a 2 fasi (invia `FETCh?` su
  `Serial2`, poi aspetta la risposta con un timeout doppio
  dell'intervallo di polling) per non bloccare mai il loop principale in
  attesa dell'LCR-meter.

## Dipendenze

- Libreria SparkFun Qwiic Scale NAU7802 Arduino Library
  (`SparkFun_Qwiic_Scale_NAU7802_Arduino_Library.h`, classe `NAU7802`) per la
  cella di carico, via I2C (`Wire.h`, libreria built-in del framework
  Arduino/ESP32).
- Protocollo seriale condiviso con `communication.py` (baud rate) e con la
  logica applicativa di `main.py`, `monotonic_test_widget.py`,
  `cyclic_test_widget.py` (formato dei comandi/messaggi). Non esiste alcuna
  dipendenza di build tra i due repository: la coerenza è **solo
  concettuale/manuale**.
- Le costanti meccaniche `PULSES_PER_REV`, `GEAR_RATIO`, `SCREW_PITCH_MM`
  sono duplicate identiche in `main.py` (`MainWindow.__init__`).

## Punti di attenzione

- **Parsing comandi non robusto**: tutti i comandi con parametri usano
  `indexOf("CHIAVE=") + N` / `substring(...)` senza validare presenza o
  ordine dei campi. Un campo mancante o in ordine diverso produce
  `indexOf() == -1` e aritmetica su indici invalidi, con comportamento non
  definito su `String` di Arduino. Funziona oggi solo perché il lato Python
  costruisce sempre le stringhe complete e nell'ordine atteso — vedi
  `CLAUDE.md`, punto critico 6.
- **Riuso di variabili di stato per scopi diversi**: `EXECUTE_PAUSE` scrive
  la durata della pausa dentro `cyclic_hold_upper_ms` (la stessa variabile
  usata dai blocchi ciclici per l'hold al limite superiore), con un
  commento esplicito nel codice che segnala l'hack. Aggiungere un nuovo tipo
  di blocco o riordinare le operazioni rischia di leggere/sovrascrivere un
  valore "sporco" lasciato da un blocco precedente.
- **Debug seriale non prefissato e ad alta frequenza**: `startMotor()`
  stampa `"DEBUG: startMotor() chiamato"` ad ogni chiamata, che durante un
  jog manuale (`JOG_UP`/`JOG_DOWN`) può avvenire a frequenza molto alta
  perché richiamata da `updateMotorState()` ad ogni ciclo di `loop()`.
  Occupa banda seriale e CPU nello stesso loop che gestisce streaming dati e
  stato motore; ci sono anche blocchi di debug simili, parzialmente
  commentati, dentro `EXECUTE_RAMP` e nel ramo `RAMPING`.
- **Limiti di sicurezza disabilitati di default**: `absolute_max_pulse_count`
  e `absolute_max_force_grams` partono a valori enormi (di fatto
  "disabilitati") e vengono aggiornati solo da un comando `SET_LIMITS`
  esplicito — non c'è alcun default di sicurezza cablato nel firmware. La
  GUI ora invia `SET_LIMITS` automaticamente alla connessione e ad ogni
  ricalibrazione cella (vedi `CHANGELOG.md`), ma il firmware da solo, appena
  flashato o dopo un riavvio senza la GUI collegata, non applica alcun
  limite.
- **`monitor_speed = 115200` in `platformio.ini`** non corrisponde al baud
  reale (`Serial.begin(460800)`): chi usa `pio device monitor` per debug
  senza forzare `-b 460800` vede solo rumore.
- **`GET_SCALE`/`SCALE:` è implementato ma mai chiamato** da nessun widget
  Python: la GUI non legge mai il fattore di scala corrente dal firmware,
  si fida solo di quanto ha impostato lei stessa. **`GET_FILTER_CONFIG` ha lo
  stesso destino**: implementato per debug/verifica manuale da terminale
  seriale, ma la GUI non lo chiama mai — invia sempre la propria
  configurazione salvata alla connessione (`main.py::
  send_filter_config_to_firmware()`), fidandosi di sé stessa come per i
  limiti di sicurezza.
- **Tempo di assestamento del filtro EMA**: con alpha=0.5 e 320 SPS, un
  salto brusco nel segnale si assesta al 99% in circa 7 campioni (~22 ms) —
  più veloce del vecchio filtro anti-spike a contatore (5 letture
  consecutive, che a seconda del sample rate HX711 configurato in hardware
  poteva arrivare a 62.5-500 ms). Con alpha più bassi o sample rate più
  bassi il tempo di assestamento cresce proporzionalmente: chi cambia questi
  parametri da "Filter Config" deve tenere presente l'impatto sul tempo di
  reazione dei controlli di sicurezza (`absolute_max_force_grams`) e degli
  stop criterion a Forza.
- **Nessun rilevamento di saturazione del PGA**: la libreria NAU7802 non
  espone alcun flag o metodo dedicato per rilevare che il guadagno
  impostato sta saturando/clippando l'ADC per il carico applicato (nessun
  overflow flag, solo lo stato di errore della calibrazione interna via
  `calAFEStatus()`, non correlato). Impostare un gain troppo alto per il
  carico reale può produrre letture bloccate a un valore limite senza
  nessun avviso esplicito, che il sistema di sicurezza tratterebbe come un
  valore valido. L'unica euristica indiretta possibile (non implementata)
  sarebbe controllare se `scale.getReading()` si avvicina agli estremi del
  range a 24 bit con segno (~±8388607).
- **Cambiare il guadagno PGA invalida la calibrazione esistente**: offset
  (`TARE`) e fattore di scala (`CALIBRATE`) sono validi solo al gain con
  cui sono stati determinati, perché i conteggi ADC grezzi per lo stesso
  carico fisico scalano col gain. `SET_FILTER_CONFIG` con un `GAIN` diverso
  da quello corrente azzera automaticamente `zero_offset`/
  `calibration_factor` ed emette `STATUS:CALIBRATION_INVALIDATED` proprio
  per rendere esplicito (non silenzioso) questo requisito — la GUI reagisce
  a questo messaggio richiedendo una nuova Tara/Calibrazione.