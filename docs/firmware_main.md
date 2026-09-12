# firmware: Controllo-Macchina-ESP32/src/main.cpp

> File in un repository separato:
> `c:\Users\carlo\Documents\PlatformIO\Projects\Controllo-Macchina-ESP32\src\main.cpp`
> (progetto PlatformIO/Arduino, ~1220 righe, unico file sorgente).

## Scopo

Firmware dell'ESP32 che pilota fisicamente la macchina: motore passo-passo
(vite senza fine) per il movimento, cella di carico NAU7802 (I2C) per la
forza, endstop meccanici, e due canali di resistenza campioni **alternativi**
(mai attivi insieme): un LCR-meter esterno via UART2, e un ADC ADS1220
esterno via SPI (misura ratiometrica a 4 fili, vedi sezione dedicata sotto e
`CLAUDE.md`). Espone
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
  rispetto a prima), il timer hardware (`stepTimer`) che genera gli
  impulsi di step via interrupt, e l'encoder incrementale esterno (pin
  34/35/27, `attachInterrupt` su tutti e tre in modalità `CHANGE`, vedi
  sotto). Inizializza anche l'**ADS1220** (SPI custom via
  `ads1220SPI.begin(SCLK, MISO, MOSI, CS)`, comando `RESET` via SPI, poi
  `applyAds1220Config()` con i valori di default — il canale resta comunque
  disattivo, `ads1220_polling_enabled=false`, finché non arriva
  `ENABLE_ADS1220_POLLING`; vedi sezione dedicata sotto).
- **`loop()`**: gestisce il completamento di movimenti a passi contati
  (`move_completed_flag`), poi chiama in sequenza `handleSerialCommands()`,
  `handleDataStreaming()`, `updateMotorState()`, `handleHardwareInputs()`
  (pulsanti manuali fisici Up/Down, riabilitata in questa sessione — era
  disabilitata/commentata), `handleJogEncoderMotion()` (movimento a step del
  jog encoder), `handleJogStepButton()` (pulsante integrato dell'encoder,
  cambio preset di step), `updateLCRReading()`, `updateADS1220Reading()`.
- **`onStepTimer()`** (ISR, `IRAM_ATTR`): alterna il pin di step,
  incrementa/decrementa `pulse_count`, e decrementa `target_steps_remaining`
  per i movimenti a conteggio (homing, return-to-start, pre-posizionamento
  ciclico, `GOTO`), impostando `move_completed_flag` quando arriva a 0.
- **`handleSerialCommands()`**: legge caratteri non bloccante da `Serial`;
  intercetta `'!'` **immediatamente**, prima di aspettare `'\n'`, per lo stop
  di emergenza; altrimenti accumula in `serial_buffer` (max 128 caratteri)
  fino a `'\n'`, poi chiama `processCommand()`. Sia `'!'` sia il comando
  `STOP` azzerano esplicitamente `target_steps_remaining` (oltre a fermare
  subito i passi con `stopMotor()`): necessario perché un movimento a passi
  contati interrotto a metà (es. `GOTO`) non lasci un residuo che
  interferirebbe con il comando di movimento successivo (vedi
  `CHANGELOG.md`).
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
  `SET_LIMITS`, `START_TEST`, `SET_FILTER_CONFIG`, `SET_ADS1220_CONFIG`) fanno
  parsing manuale con `indexOf`/`substring` (vedi Punti di attenzione).
  `ENABLE_ADS1220_POLLING`/`DISABLE_ADS1220_POLLING` seguono esattamente lo
  stesso pattern dei comandi LCR equivalenti; `SET_ADS1220_CONFIG` è
  rifiutato (`STATUS:ADS1220_CONFIG_REJECTED;REASON=POLLING_ACTIVE`) se il
  canale è già in polling, validato atomicamente come `SET_FILTER_CONFIG`
  (`REASON=OUT_OF_RANGE` se un campo non è valido, nessuna applicazione
  parziale) e altrimenti chiama `applyAds1220Config()`.
  `GOTO:<mm>` (posizione assoluta, sola andata, >= 0) è accettato con la
  stessa condizione di `JOG_UP`/`JOG_DOWN`/`HOME`/`SET_SPEED`
  (`!is_hardware_jog_active && motor_state == STOPPED`): converte mm in
  passi assoluti (`PULSES_TO_MM`), calcola il delta rispetto a `pulse_count`
  e riusa il meccanismo a passi contati già di `RETURN_TO_START`
  (`target_steps_remaining`/`motor_enabled`), **senza introdurre un nuovo
  `MotorState`** — `motor_state` resta `STOPPED` per tutta la durata del
  movimento, esattamente come già avviene per `RETURN_TO_START`. I controlli
  di sicurezza assoluti (`absolute_max_pulse_count`/`absolute_max_force_grams`
  in `updateMotorState()`) e gli endstop si applicano automaticamente anche
  a questo movimento, perché vengono valutati prima dell'uscita anticipata
  legata a `target_steps_remaining > 0` (righe 906-909).
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
     fine. Nello stesso punto (fine `HOMING_FINAL_LIFT`, prima di
     `STATUS:HOMING_COMPLETED`) azzera anche `encoder_position` ed
     `encoder_z_turns`, dentro la stessa sezione critica
     (`portENTER_CRITICAL(&encoder_mux)`) già usata da
     `readEncoderPosition()`: l'homing esistente è quindi il punto di zero
     comune per entrambi i canali di spostamento, senza alcun comando o
     logica di homing separata per l'encoder.
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
  `STREAM_INTERVAL_MS` (20 ms → 50 Hz) con il valore di carico già filtrato,
  incluso il 6° campo (conteggio encoder) e il 7° campo (resistenza
  ADS1220, `RES_ADS`, sentinel `-999.0` se il canale non è abilitato — il 5°
  campo, `RES_LCR`, resta invariato). Lo stesso schema a 7 campi è usato
  anche nella risposta a `GET_DATA`.
- **Encoder incrementale esterno** (Omron E6B2-CWZ6C, 1200 PPR, montato
  direttamente sulla vite senza fine): decodifica in quadratura 4x via due
  ISR (`handleEncoderChange()` su A/B, tabella di transizione
  `ENCODER_QUAD_TABLE`) + conteggio giri su Z (`handleEncoderZChange()`,
  fronte di discesa). Il contatore (`encoder_position`, 4800 conteggi/giro)
  è letto in modo atomico da `readEncoderPosition()` tramite
  `portENTER_CRITICAL`/`portMUX_TYPE` (necessario per la natura dual-core
  dell'ESP32: le ISR possono girare su un core diverso da quello che
  legge). **Canale di sola lettura (Livello 1)**: il valore letto viene
  solo inserito come 6° campo del pacchetto `D:` (sia in streaming sia in
  risposta a `GET_DATA`); non viene mai confrontato con `pulse_count`, non
  influenza `updateMotorState()`, i limiti di sicurezza assoluti, né alcuno
  stop criterion. `encoder_z_turns` (conteggio giri completi da Z) è
  decodificato ma non ancora esposto sul protocollo seriale. **L'unico punto
  in cui l'encoder interagisce con il resto del firmware** è la fine della
  sequenza di homing (`HOMING_FINAL_LIFT`, subito prima di
  `STATUS:HOMING_COMPLETED`): lo stesso punto che azzera `pulse_count` azzera
  ora anche `encoder_position` ed `encoder_z_turns` (dentro la stessa
  sezione critica di `readEncoderPosition()`), rendendo l'homing esistente
  il punto di zero comune per entrambi i canali di spostamento — nessun
  comando o macchina a stati di homing dedicata per l'encoder. Il modulo è
  stato portato invariato
  da uno sketch standalone di validazione, testato su hardware reale prima
  dell'integrazione (risoluzione e linearità confermate fino a 2 giri).
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
- **ADS1220 (resistenza campioni via SPI, canale alternativo all'LCR)**:
  `ads1220WriteReg()`/`ads1220ReadReg()` (WREG/RREG, un registro alla volta),
  `ads1220ReadData()` (RDATA, legge la conversione corrente e fa
  sign-extend da 24 a 32 bit — sicuro senza pin `DRDY` dedicato perché il
  chip resta sempre in **continuous conversion mode**, `CM=1`).
  `applyAds1220Config()` traduce `ads1220_sps`/`gain`/`pga_bypass`/`idac_ua`
  nei 4 byte di registro (vedi `CLAUDE.md` per la mappa bit-per-bit) e li
  scrive via `ads1220WriteReg()`, poi invia `START/SYNC` (obbligatorio dopo
  una scrittura registri in continuous mode) e azzera il buffer della media
  mobile; chiamata sia da `ENABLE_ADS1220_POLLING` sia da un
  `SET_ADS1220_CONFIG` riuscito. `updateADS1220Reading()`, chiamata da
  `loop()`: se il canale non è abilitato imposta il sentinel `-999.0`;
  altrimenti interroga via `RDATA` rate-limitata a `1000/ads1220_sps` ms,
  converte con `R_x = (raw / (2^23 * gain)) * ADS1220_R_REF_OHM` e aggiorna
  una media mobile circolare (`ads1220_avg_buffer`, finestra
  `ads1220_window`, max 20). **Range massimo misurabile: `R_ref / gain`**
  (indipendente da IDAC, verificato su hardware reale — vedi `CLAUDE.md`,
  sezione ADS1220, e `CHANGELOG.md`): oltre quel valore l'ADC satura a
  fondo scala positivo (`raw = 2^23-1`) e la formula restituisce sempre lo
  stesso numero, indistinguibile da un circuito aperto. `DEBUG_ADS1220`
  (comando diagnostico **temporaneo**, non parte del protocollo definitivo)
  rilegge i 4 registri via RREG più un campione RDATA immediato, per
  verificare da terminale che le scritture WREG siano realmente arrivate al
  chip — usato per diagnosticare il bug di parsing descritto sotto.
- **Pulsanti manuali Up/Down e jog encoder** (`UP_BUTTON_PIN`/
  `DOWN_BUTTON_PIN`, `JOG_ENCODER_A`/`B`/`SW` — vedi `CLAUDE.md`, sezione
  dedicata, per il comportamento completo): `isManualJogAllowed()` è la
  guardia di attivazione condivisa (`motor_state == STOPPED &&
  target_steps_remaining == 0 && !killswitch_engaged`), unico punto di
  verità usato sia da `handleHardwareInputs()` (debounce ~25ms, riusa
  `startMotor()`/`stopMotor()` esattamente come `JOG_UP`/`JOG_DOWN`
  seriali) sia da `handleJogEncoderMotion()` (consuma il delta di
  quadratura accumulato da `handleJogEncoderChange()`, ISR su
  `JOG_ENCODER_A`/`B`, in un movimento relativo `delta × step_size`,
  impostando `motor_state = JOG_UP`/`JOG_DOWN` invece del meccanismo a passi
  contati di `GOTO`, per non perdere il controllo endstop). Il preset di
  step (3 valori, costanti in testa al file) è ciclato da
  `handleJogStepButton()` (debounce dedicato ~50ms su `JOG_ENCODER_SW`), che
  emette anche `STATUS:JOG_STEP_SIZE_SET;MM=..`.

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
- **Overhead delle ISR dell'encoder sul timing del `loop()`**: le due nuove
  ISR (`handleEncoderChange()` su A e B, `handleEncoderZChange()` su Z)
  si aggiungono a quella già esistente del timer di step
  (`onStepTimer()`). Ognuna esegue poco lavoro (lettura di due pin,
  lookup in tabella, una sezione critica breve), ma su un ESP32 già
  impegnato con streaming dati, controllo motore e polling LCR
  nello stesso `loop()`, un aumento della frequenza di transizione
  dell'encoder (rotazione molto rapida della traversa) aumenta la
  frequenza di interruzione del `loop()` principale. Non ancora misurato
  l'impatto su `STREAM_INTERVAL_MS` con il nuovo carico di lavoro
  reale — da validare se si osservano rallentamenti o jitter nello
  streaming a velocità di traversa elevate.
- **Conteggio encoder e riavvio dell'ESP32**: `encoder_position` è in RAM
  volatile, non persistito. Un riavvio (reset hardware, power-cycle, o
  anche il reset indotto dall'apertura della porta seriale — vedi
  `CLAUDE.md`) azzera il conteggio, esattamente come già avviene per
  `pulse_count`. Un ciclo di homing lo azzera anch'esso di nuovo
  (vedi sopra): il confronto fra i due canali nei dati salvati ha quindi
  senso a partire dall'ultimo homing (o dall'ultimo riavvio, se più
  recente), non attraverso un riavvio senza homing successivo.
- **Range di resistenza misurabile dall'ADS1220 limitato da `R_ref/gain`**:
  con `ADS1220_R_REF_OHM = 989.58`, il massimo teoricamente misurabile è
  `989.58/gain` Ω (indipendente da `ads1220_idac_ua`), a prescindere dal
  valore di IDAC scelto. Oltre quella soglia l'ADC satura a fondo scala
  positivo (`raw = 2^23-1`) e il valore riportato resta bloccato a quel
  numero — indistinguibile da un ingresso realmente aperto (verificato su
  hardware reale: rimuovere fisicamente il campione non cambia la lettura
  se era già satura). Chi sceglie `GAIN`/`IDAC` da GUI deve tenerne conto
  per il range di resistenza atteso del campione; vedi `CLAUDE.md` per la
  derivazione e `TODO.md` per un'idea di banco di resistenze di riferimento
  commutabili per estendere il range dinamicamente.
- **Bug storico corretto**: `SET_ADS1220_CONFIG` usava
  `command.substring(20)` per isolare i parametri, ma
  `"SET_ADS1220_CONFIG:"` è lunga **19** caratteri, non 20 — il primo
  carattere (`S` di `SPS=`) veniva scartato, `indexOf("SPS=")` falliva
  sempre e il comando era rifiutato con `REASON=OUT_OF_RANGE`
  indipendentemente dai valori inviati. Diagnosticato confrontando la
  richiesta rifiutata con `SET_LIMITS`/`SET_FILTER_CONFIG` (stesso pattern,
  lunghezze del prefisso diverse) e corretto in `command.substring(19)`.
  Promemoria per chi aggiunge un nuovo comando con questo pattern: contare i
  caratteri del prefisso letteralmente, non a occhio.
- **`DEBUG_ADS1220` è un comando diagnostico temporaneo**, aggiunto per
  isolare il bug sopra e per verificare la saturazione del range (stesso
  spirito di `DEBUG_RAW_KILLSWITCH`, già rimosso in passato — vedi
  `CLAUDE.md`): da valutare se rimuovere a validazione ADS1220 completata,
  non è documentato nella tabella comandi "ufficiale" di `CLAUDE.md` per lo
  stesso motivo.
- **Cambiare il guadagno PGA invalida la calibrazione esistente**: offset
  (`TARE`) e fattore di scala (`CALIBRATE`) sono validi solo al gain con
  cui sono stati determinati, perché i conteggi ADC grezzi per lo stesso
  carico fisico scalano col gain. `SET_FILTER_CONFIG` con un `GAIN` diverso
  da quello corrente azzera automaticamente `zero_offset`/
  `calibration_factor` ed emette `STATUS:CALIBRATION_INVALIDATED` proprio
  per rendere esplicito (non silenzioso) questo requisito — la GUI reagisce
  a questo messaggio richiedendo una nuova Tara/Calibrazione.