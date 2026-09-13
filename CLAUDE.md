# UTM-ESP32 — Macchina di Trazione Universale

Questo repository (monorepo) contiene due progetti collegati che insieme pilotano una
macchina da trazione/compressione da laboratorio:

- **UTM-ESP32** (radice del repo) — applicazione desktop Python/PyQt6, gira sul PC e
  comunica via seriale USB con l'ESP32.
- **`firmware/`** (sottocartella di questo stesso repo) — firmware C++
  (PlatformIO/Arduino) che gira sull'ESP32, un unico file `firmware/src/main.cpp`
  (~1220 righe). Pilota un motore passo-passo (vite senza fine), legge una cella di
  carico via NAU7802 (ADC I2C, libreria SparkFun Qwiic Scale NAU7802) e opzionalmente
  un LCR-meter esterno via UART2.

**Nota storica**: fino al 2026-09-13 il firmware viveva in un repository git separato
(`Controllo-Macchina-ESP32`, in `c:\Users\carlo\Documents\PlatformIO\Projects\`, fuori
dalla cartella OneDrive di questo progetto). È stato fuso qui con `git subtree`
(mantenendo tutta la sua cronologia commit, visibile in `git log -- firmware/`) per
avere un solo repo da clonare/sincronizzare e poter fare commit atomici che toccano
sia GUI sia firmware. Il vecchio repo `Controllo-Macchina-ESP32` su GitHub è stato
archiviato (sola lettura); la sua cartella locale originale è stata rinominata
`Controllo-Macchina-ESP32_OLD_backup` invece di essere cancellata. Chi apre il
progetto in PlatformIO deve ora puntare a `firmware/` come project root, non più alla
vecchia cartella in `Documents`.

## Architettura software Python

### Moduli e responsabilità

- **`main.py`** — `MainWindow`: finestra principale, orchestratore. Possiede:
  - `SerialCommunicator` spostato in un `QThread` dedicato (comunicazione non bloccante).
  - Uno `QStackedWidget` con le schermate: `MainMenuWidget`, `ManualControlWidget`,
    `CalibrationWidget`, `MonotonicTestWidget`, `CyclicTestWidget`.
  - `handle_data_from_esp32()`: **punto centrale di smistamento** di tutti i messaggi
    seriali in arrivo. Interpreta i prefissi `STATUS:` e `D:`, aggiorna le variabili
    assolute di carico/spostamento su tutti i widget, e — cosa importante — è anche
    il posto dove viene **guidata la sequenza a blocchi del test ciclico**: alla
    ricezione di `STATUS:BLOCK_COMPLETED` costruisce e invia il comando per il blocco
    successivo di `CyclicTestWidget.test_sequence` (vedi sotto). Il firmware non sa
    nulla della sequenza intera: la logica multi-blocco vive tutta qui. Gestisce anche
    `STATUS:CALIBRATION_INVALIDATED` (emesso dal firmware quando un cambio di guadagno
    PGA reale invalida offset/scala correnti): resetta lo stato di calibrazione
    mostrato in GUI e avvisa l'utente che deve ripetere Tara e Calibrazione. Estrae
    anche il 6° campo (opzionale) del pacchetto `D:`, il conteggio grezzo
    dell'encoder incrementale esterno, e lo converte in `encoder_displacement_mm`
    (canale di sola lettura, non usato per nessuna decisione real-time — vedi
    `CHANGELOG.md`). Estrae anche `STATUS:CALIBRATION_DONE;SCALE=..` per
    `calibration_widget.set_calibration_factor()`. Dopo l'intera catena
    `if/elif` dei messaggi `STATUS:`, un controllo aggiuntivo non esclusivo
    chiama `clear_goto_busy_state()` su `monotonic_test_widget`/`cyclic_test`
    per qualunque messaggio che indichi che il motore si è comunque fermato
    (`MOVE_COMPLETED`, `STOPPED_BY_USER`, `TOP_HIT`, `BOTTOM_HIT`,
    `LIMIT_HIT`, `KILLSWITCH_TRIGGERED`), per chiudere lo stato "Go To in
    corso" su quei widget. Possiede anche `killswitch_engaged` /
    `position_unverified` (stato del killswitch, vedi sezione dedicata più
    sotto), propagati a tutti i widget via `set_killswitch_state()` e
    all'indicatore/banner tramite `_update_killswitch_indicator()`; un
    `EventLogger` (`event_logger.py`) per il log eventi di sistema.
  - Timer a 100 ms che invia `GET_DATA` in polling (usato solo quando non si è in
    streaming, cioè fuori da un test).
  - `current_force_limit_N` / `current_disp_limit_mm`: stato lato GUI dei limiti di
    sicurezza assoluti, modificabile da `show_limits_dialog()` e reinviato al firmware
    tramite `send_limits_to_firmware()` (automaticamente alla connessione e dopo ogni
    calibrazione, vedi **Punti critici** più sotto, punto 3, risolto).
  - `current_filter_alpha` / `current_filter_rate_sps` / `current_filter_pga_gain`:
    stato lato GUI della configurazione del filtro EMA della cella di carico NAU7802
    (alpha, sample rate, guadagno PGA), caricati/persistiti in `settings.json` e
    reinviati al firmware con `send_filter_config_to_firmware()` (comando
    `SET_FILTER_CONFIG`, vedi sotto), modificabili da `show_filter_dialog()`.

- **`communication.py`** — `SerialCommunicator(QObject)`: worker che vive nel thread
  seriale. Loop principale (`run()`): consuma una coda (`Queue`) di comandi in uscita
  scrivendoli `+ "\n"`, e legge in modo non bloccante (`timeout=0`) accumulando byte
  in un buffer, spezzando su `\n` ed emettendo `data_received` per ogni riga completa.
  `send_emergency_stop()` scrive `b"!\n"` **bypassando la coda dei comandi**, per dare
  priorità assoluta allo stop.

- **`main_menu_widget.py`** — menu iniziale con i pulsanti verso le altre schermate.

- **`manual_control_widget.py`** — `ManualControlWidget`: jog manuale (pulsanti
  UP/DOWN a pressione, che inviano `JOG_UP`/`JOG_DOWN` su `pressed` e `STOP` su
  `released`), homing, zero relativi, grafico live (carico vs tempo, finestra
  scorrevole), registrazione manuale dei dati su Excel, toggle lettura LCR.
  Mostra anche lo spostamento dell'encoder incrementale esterno, sia assoluto
  sia relativo (`Relative Enc. Displacement (mm)`, azzerato insieme allo
  spostamento a passi motore dallo stesso pulsante "Zero Relative
  Displacement" — vedi `CHANGELOG.md`). `set_killswitch_state()`/
  `update_jog_enabled()`: Up/Down disabilitati solo in stato "rosso"
  (killswitch premuto ora), non in "giallo" — vedi sezione Killswitch.
  `set_jog_step_size()`: aggiorna il display "Jog Encoder Step (mm)" su
  `STATUS:JOG_STEP_SIZE_SET` — puro display informativo per il jog encoder
  fisico (pulsante/encoder a bordo macchina, gestiti interamente dal
  firmware, funzionano anche a GUI chiusa), vedi sezione dedicata.

- **`calibration_widget.py`** — `CalibrationWidget`: wizard a stati
  (`IDLE → WAITING_FOR_ZERO → WAITING_FOR_WEIGHT → IDLE`) che pilota `TARE` e
  `CALIBRATE:<grammi>` sul firmware. Gestisce anche salvataggio/caricamento di un
  fattore di calibrazione su file JSON esterno (diverso da `settings.json`):
  traccia `current_calibration_factor`, popolato da
  `STATUS:CALIBRATION_DONE;SCALE=..` (inoltrato da `MainWindow`) o direttamente
  dopo un caricamento da file, e azzerato da `invalidate_calibration()` su
  `STATUS:CALIBRATION_INVALIDATED` — necessario perché "Save Calibration"
  abbia davvero qualcosa da scrivere (era rotto, vedi punto critico 2).

- **`monotonic_test_widget.py`** — `MonotonicTestWidget`: gestione di un batch di
  "provini" (specimen) con parametri (gauge length, area, velocità, criterio di
  stop), avvio di un singolo test monotonico (`START_TEST:...`), grafico
  carico/spostamento con conversione opzionale in stress/strain, overlay di test
  precedenti, autosave in Excel a fine test. Mostra anche lo spostamento
  dell'encoder esterno (assoluto e relativo) e permette di scegliere, quando
  l'asse X è "Relative Displacement (mm)", se usare come sorgente il canale
  motore e/o quello encoder (overlay di due curve in tempo reale — vedi
  `CHANGELOG.md`). Ha un controllo "Go To" (posizione assoluta, mm >= 0, alla
  velocità di Jog Speed) accanto a Up/Down: il pulsante Go To stesso diventa
  "STOP" durante il movimento, e anche il pulsante STOP principale della
  schermata può interromperlo.

- **`cyclic_test_widget.py`** (file più grande, ~1800 righe) — `CyclicTestWidget`:
  editor di una **sequenza di blocchi** (blocco ciclico, pausa, rampa — dialog
  dedicati `BlockDialog`/`PauseDialog`/`RampDialog`), gestione batch provini simile
  al monotonico. Avvia solo il **primo** blocco della sequenza; i blocchi successivi
  sono pilotati da `main.py` in risposta a `STATUS:BLOCK_COMPLETED` (vedi sopra).
  Stesse aggiunte encoder/"Go To"/selezione sorgente X di
  `monotonic_test_widget.py` (vedi sopra e `CHANGELOG.md`). Entrambi i widget
  hanno `set_killswitch_state()`/`on_stop_test(..., abort_reason=...)` per il
  gating avvio-prova/GOTO e la marcatura dell'autosave — vedi sezione Killswitch.

- **`data_saver.py`** — `DataSaver`: esporta i dati di test (monotonici o ciclici) in
  `.xlsx` con `openpyxl`, un foglio per provino, grafici Scatter incorporati
  (Load-Displacement / Stress-Strain per monotonici, Time-Displacement /
  Time-Load per ciclici). Se `specimen_data["abort_reason"]` è valorizzato
  (es. `"KILLSWITCH"`), aggiunge una riga `Test Status: INTERRUPTED — ...`
  in grassetto/rosso nella sezione parametri — assente (comportamento
  invariato) se `None`, il caso normale.

- **`settings_manager.py`** — persistenza JSON (`settings.json`) dei carichi di
  calibrazione (`cal_loads`) per cella e della configurazione del filtro EMA della
  cella di carico (`filter_config`: alpha, sample rate, guadagno PGA — default
  0.5/320 SPS/128x). Nota: la "calibrazione attiva" (fattore di scala) è invece
  salvata/caricata come file JSON separato scelto dall'utente tramite
  `CalibrationWidget`, non tramite `SettingsManager`. Il merge dei default in
  `load_settings()` è solo a livello di chiavi di primo livello: sotto-chiavi nuove
  aggiunte a una chiave già esistente (com'è successo con `gain` dentro
  `filter_config`) non vengono propagate automaticamente in un `settings.json`
  preesistente.

- **`custom_widgets.py`** — widget riusabili: `DisplayWidget` (etichetta + valore),
  `SpeedBarWidget` (barra colorata verde→giallo→rosso), `LimitsDialog` (form per
  forza/spostamento massimi assoluti), `FilterConfigDialog` (form per alpha, sample
  rate e guadagno PGA del filtro cella di carico), `KillswitchIndicatorWidget`
  (indicatore a 3 livelli verde/giallo/rosso, presente sia in `MainWindow` sia
  nei due dialog sopra) e `KillswitchBannerWidget` (banner persistente,
  visibile finché lo stato non torna verde) — vedi sezione Killswitch.

- **`event_logger.py`** — `EventLogger`: log persistente di eventi di sistema
  (JSON Lines, `event_log.jsonl`), separato dai dati di misura delle prove.
  Vedi sezione Killswitch per l'elenco degli eventi attualmente loggati.

### Flusso dati ad alto livello

```
ESP32 (main.cpp) --seriale 460800 8N1--> SerialCommunicator (QThread)
                                              |  pyqtSignal data_received(str)
                                              v
                                     MainWindow.handle_data_from_esp32()
                                              |  aggiorna variabili assolute
                                              |  su tutti i widget
                                              v
                              widget.handle_stream_data(...) del widget corrente
                                              |
                                              v
                                    widget.update_displays() / grafico live
```

I comandi vanno nella direzione opposta: ogni widget chiama
`self.communicator.send_command(cmd)` (che li accoda) oppure, per lo stop
d'emergenza, `send_emergency_stop()` (bypass coda).

## Protocollo di comunicazione seriale

- **Fisico**: 460800 baud, 8N1, framing a riga (`\n`), incapsulato in ASCII.
  `communication.py` apre la porta a `460800`; `main.cpp` fa `Serial.begin(460800)`.
  I due lati **devono restare sincronizzati manualmente** su questo numero (non c'è
  negoziazione).
- **Stop di emergenza**: il carattere singolo `!` ha **priorità assoluta** ed è
  intercettato carattere-per-carattere nel loop di lettura del firmware
  (`handleSerialCommands()`), *senza aspettare `\n`* — interrompe subito il motore e
  svuota il buffer di ricezione. Il Python lo invia come `b"!\n"`: il `\n` finale
  produce solo un comando vuoto successivo, ignorato.
- **Modalità comunicazione**: `comms_mode` sul firmware è `POLLING` o `STREAMING`.
  - In `POLLING`, il PC deve chiedere esplicitamente `GET_DATA` (fatto da un
    `QTimer` a 100 ms in `main.py` quando si è connessi).
  - In `STREAMING`, il firmware invia autonomamente un pacchetto `D:` ogni
    `STREAM_INTERVAL_MS` (20 ms, cioè 50 Hz) senza bisogno di richieste.
  - Il passaggio tra modalità avviene col comando `SET_MODE:POLLING|STREAMING`,
    inviato esplicitamente dal lato Python all'avvio/fine di ogni test.

### Comandi PC → ESP32 (terminati da `\n`, tranne `!`)

| Comando | Parametri | Note |
|---|---|---|
| `!` | — | Stop immediato, out-of-band |
| `STOP` | — | Stop "normale" (via coda) |
| `RESET_TIMER` | — | Azzera `test_start_time` e contatore cicli globale |
| `SET_MODE:POLLING`/`STREAMING` | — | |
| `GET_DATA` | — | Risponde solo se `comms_mode == POLLING` |
| `TARE` | — | Media 1s di letture, imposta offset scala |
| `CALIBRATE:<grammi>` | peso noto | Media 1s, calcola e imposta `scale.set_scale()` |
| `GET_SCALE` | — | Risponde `SCALE:<valore>` — **non risulta usato da nessun widget Python** |
| `SET_SCALE:<factor>` | — | Usato da `CalibrationWidget.load_calibration()` |
| `SET_LIMITS:FORCE_G=..;DISP_MM=..` | grammi, mm | Imposta `absolute_max_force_grams`/`absolute_max_pulse_count` |
| `SET_FILTER_CONFIG:ALPHA=..;RATE=..;GAIN=..` | alpha∈[0.01,1.0], rate∈{10,20,40,80,320}, gain∈{1,2,4,8,16,32,64,128} | `GAIN` opzionale (retrocompatibilità). Rifiuta l'intero comando (`STATUS:FILTER_CONFIG_REJECTED;REASON=OUT_OF_RANGE`) se un campo è fuori range; nessuna applicazione parziale. Se `GAIN` cambia realmente, invalida offset/scala e riazzera l'EMA |
| `GET_FILTER_CONFIG` | — | Risponde `STATUS:FILTER_CONFIG;ALPHA=..;RATE=..;GAIN=..` |
| `RETURN_TO_START` | — | Torna alla posizione registrata a inizio test. Stessa guardia di `GOTO`: rifiutato (`STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED`, nome riusato deliberatamente — stessa categoria di comando, stesso motivo di rifiuto) se `position_unverified == true`, perché si basa sullo stesso `pulse_count` non più affidabile |
| `START_TEST:SPEED_MMS=..;CRITERION=DISP\|FORCE;STOP_VAL=..` | | Avvia test monotonico. Rifiutato (`STATUS:TEST_START_REJECTED;REASON=POSITION_UNVERIFIED`) se `position_unverified == true` |
| `START_CYCLIC_TEST:MODE=DISP\|FORCE;UPPER=..;LOWER=..;SPEED=..;HOLD_U=..;HOLD_L=..;CYCLES=..` | | Avvia blocco ciclico. Stesso rifiuto di `START_TEST` se `position_unverified == true` |
| `EXECUTE_PAUSE:<ms>` | | Blocco pausa nella sequenza ciclica |
| `EXECUTE_RAMP:MODE=DISP\|FORCE;TARGET=..;SPEED=..;HOLD=..` | | Blocco rampa (vai-a-target) |
| `JOG_UP` / `JOG_DOWN` | — | Solo se `motor_state == STOPPED` e non jog hardware attivo. Rifiutato (`STATUS:JOG_REJECTED;REASON=KILLSWITCH_ENGAGED`) se il killswitch è premuto ORA (`killswitch_engaged`) — permesso invece in stato "giallo" (`position_unverified` senza killswitch premuto) |
| `HOME` | — | Avvia sequenza di homing a stati (fast→backoff→slow→final lift). **Sempre permesso**, anche in stato giallo o rosso: è l'unico modo per uscire da `position_unverified` |
| `SET_SPEED:<mm/s>` | — | Stessa condizione di JOG_* (nessun gating aggiuntivo legato al killswitch: non muove nulla da solo) |
| `GOTO:<mm>` | mm assoluti, >= 0 | Movimento verso una posizione assoluta, alla velocità impostata con `SET_SPEED`. Stessa condizione di JOG_* (`motor_state == STOPPED`, non jog hardware attivo), **più** rifiuto (`STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED`) se `position_unverified == true` (stato giallo o rosso). Usa lo stesso meccanismo a passi contati di `RETURN_TO_START` (nessun nuovo `MotorState`): risponde `STATUS:GOTO_STARTED` o, se già alla posizione target, `STATUS:MOVE_COMPLETED` subito. `STOP`/`!` lo interrompono sempre, azzerando esplicitamente `target_steps_remaining` |
| `ENABLE_LCR_POLLING` / `DISABLE_LCR_POLLING` | — | Attiva/disattiva interrogazione LCR-meter su Serial2 |
| `ENABLE_ADS1220_POLLING` / `DISABLE_ADS1220_POLLING` | — | Attiva/disattiva interrogazione ADS1220 su SPI (canale alternativo all'LCR, mai attivi insieme lato GUI — vedi sezione ADS1220 sotto). L'`ENABLE` riapplica sempre la configurazione corrente e riavvia le conversioni (`START/SYNC`) |
| `SET_ADS1220_CONFIG:SPS=..;GAIN=..;PGA_BYPASS=..;IDAC=..;WINDOW=..` | sps∈{20,45,90,175,330,600,1000}, gain∈{1,2,4,8,16,32,64,128}, pga_bypass∈{0,1}, idac∈{0,10,50,100,250,500,1000,1500} (µA), window∈[1,20] | Validazione atomica come `SET_FILTER_CONFIG` (nessuna applicazione parziale). **Rifiutato** (`STATUS:ADS1220_CONFIG_REJECTED;REASON=POLLING_ACTIVE`) se il canale è attualmente in polling — configurabile solo a canale fermo. `PGA_BYPASS` ha effetto solo se `GAIN<8`; per `GAIN>=8` il firmware ignora silenziosamente la richiesta (PGA sempre attivo, da datasheet) e riporta il valore realmente applicato nella risposta `STATUS:ADS1220_CONFIG_SET;SPS=..;GAIN=..;PGA_BYPASS=..;IDAC=..;WINDOW=..`, senza rifiutare il comando per questo |
| `GET_ADS1220_CONFIG` | — | Risponde `STATUS:ADS1220_CONFIG;SPS=..;GAIN=..;PGA_BYPASS=..;IDAC=..;WINDOW=..` (implementato per completezza del protocollo; oggi non chiamato periodicamente dalla GUI, stesso status di `GET_FILTER_CONFIG`) |
| `GET_KILLSWITCH_STATE` | — | Risponde `STATUS:KILLSWITCH_STATE;ENGAGED=0\|1;POSITION_UNVERIFIED=0\|1`. Inviato dalla GUI subito dopo la connessione, per allinearsi allo stato reale anche su schede che non resettano all'apertura porta (vedi sezione Killswitch) |

Tutti i comandi con parametri usano il formato `CHIAVE=valore;CHIAVE=valore` fatto a
mano con `indexOf`/`substring` lato firmware (parsing fragile, vedi sotto).

**Nota unità**: la GUI lavora sempre in **N** e **mm** (con offset relativi), ma
verso il firmware converte sempre in **grammi** (`(N/9.81)*1000`) e **mm assoluti**
(sommando l'offset corrente). Il firmware converte i mm in passi motore internamente
(`PULSES_TO_MM`) e lavora internamente in grammi per la forza. `PULSES_PER_REV=2000`,
`GEAR_RATIO=10`, `SCREW_PITCH_MM=5.0873` sono **duplicati indipendentemente** sia in
`main.py` (`MainWindow.__init__`) sia in `main.cpp`: oggi coincidono, ma non c'è
alcun meccanismo che li tenga sincronizzati se uno dei due cambia. La conversione
dell'encoder esterno (`encoder_count / 4800.0 * SCREW_PITCH_MM`) riusa la stessa
costante `SCREW_PITCH_MM` già presente in `main.py`, senza bisogno di
`GEAR_RATIO` (l'encoder è montato direttamente sulla vite, non sull'albero motore).

### Messaggi ESP32 → PC

- **`D:<load_g>;<pulse_count>;<elapsed_ms>;<cycle>;<resistance_lcr_ohm>;<encoder_count>;<resistance_ads_ohm>`**
  — pacchetto dati, sempre a **7 campi**, sia in risposta a `GET_DATA` (con
  `time`/`cycle` fittizi a `"0"`) sia in streaming. `main.py` sa ancora parsare
  varianti storiche a 3, 4, 5 o 6 campi (retrocompatibilità morta: il firmware
  attuale non le invia più). Il 5° campo (`RES_LCR`, rinominato qui per
  chiarezza rispetto al nuovo campo ADS1220) è la resistenza letta
  dall'LCR-meter esterno (invariato). Il 6° campo (`encoder_count`) è il
  conteggio grezzo dell'encoder incrementale esterno Omron E6B2-CWZ6C
  (quadratura 4x, 1200 PPR → 4800 conteggi/giro), montato direttamente sulla
  vite senza fine (nessun `GEAR_RATIO` di mezzo). **È un canale di misura
  aggiuntivo, di sola lettura (Livello 1)**: non influenza in alcun modo il
  comando motore né i limiti di sicurezza assoluti, che restano basati su
  `pulse_count` come prima. Conversione lato Python: `mm = (encoder_count /
  4800.0) * SCREW_PITCH_MM`. Convenzione di segno verificata su hardware:
  quando la traversa sale, sia `pulse_count` sia `encoder_count` aumentano
  (stesso segno, nessuna inversione da compensare). La sequenza di
  **homing** esistente è anche il punto di zero comune per entrambi i
  canali: allo stesso punto in cui azzera `pulse_count` (fine
  `HOMING_FINAL_LIFT`), il firmware azzera ora anche il contatore encoder —
  nessun comando o homing separato per l'encoder. Il 7° campo (`RES_ADS`,
  nuovo) è la resistenza letta dall'ADC ADS1220 (canale alternativo
  all'LCR-meter, **Livello 1** come l'encoder: sola lettura, mai usato per
  decisioni di sicurezza/movimento) — vedi la sezione ADS1220 sotto per il
  dettaglio hardware/protocollo. Entrambi i campi resistenza usano lo stesso
  schema di sentinel: `-999` = canale disabilitato, `-1` = timeout (solo
  LCR), `-2` = errore di parsing, valore reale altrimenti. Vedi
  `CHANGELOG.md` per i compromessi emersi durante l'integrazione di
  ciascun canale.
- **`STATUS:<messaggio>`** — testo libero, interpretato in `main.py` per substring
  matching (es. `"TOP_HIT" in status_message`), non per uguaglianza esatta. Esempi:
  `TEST_STARTED`, `TEST_COMPLETED`, `TEST_STOPPED_BY_USER`, `CYCLIC_TEST_STARTED`,
  `CYCLIC_PREPOSITIONING`, `BLOCK_COMPLETED`, `CYCLIC_TEST_STOPPED_BY_USER`,
  `RAMP_STARTED`, `RAMP_HOLDING_STARTED`, `PAUSE_STARTED`, `TOP_HIT`, `BOTTOM_HIT`,
  `LIMIT_HIT_DISPLACEMENT`, `LIMIT_HIT_FORCE`, `HOMING_BACKOFF`, `HOMING_SLOW`,
  `HOMING_LIFTING`, `HOMING_COMPLETED`, `HOMED`, `TARE_DONE;OFFSET=..`,
  `CALIBRATION_DONE;SCALE=..`, `LIMITS_SET;MAX_FORCE_G=..;MAX_PULSES=..`,
  `MOVE_COMPLETED`, `RETURN_COMPLETED`, `RETURNING`, `GOTO_STARTED` (risposta a
  `GOTO:<mm>`; se già alla posizione target arriva `MOVE_COMPLETED` invece),
  `MODE_SET`, `TIMER_RESET`,
  `SCALE_SET`, `LCR_POLLING_ENABLED`/`DISABLED`, `STOPPED_BY_USER`,
  `FILTER_CONFIG_SET;ALPHA=..;RATE=..;GAIN=..`, `FILTER_CONFIG_REJECTED;REASON=..`,
  `FILTER_CONFIG;ALPHA=..;RATE=..;GAIN=..` (risposta a `GET_FILTER_CONFIG`),
  `ADS1220_POLLING_ENABLED`/`DISABLED`,
  `ADS1220_CONFIG_SET;SPS=..;GAIN=..;PGA_BYPASS=..;IDAC=..;WINDOW=..` (risposta a
  `SET_ADS1220_CONFIG`, con i valori **realmente applicati** — puo differire da
  quanto richiesto per `PGA_BYPASS` se `GAIN>=8`, vedi sezione ADS1220 sotto),
  `ADS1220_CONFIG_REJECTED;REASON=POLLING_ACTIVE|OUT_OF_RANGE`,
  `ADS1220_CONFIG;SPS=..;GAIN=..;PGA_BYPASS=..;IDAC=..;WINDOW=..` (risposta a
  `GET_ADS1220_CONFIG`),
  `CALIBRATION_INVALIDATED;REASON=GAIN_CHANGED` (emesso quando un cambio di
  guadagno PGA realmente diverso dal precedente resetta offset/scala),
  `KILLSWITCH_TRIGGERED` (killswitch premuto, rilevamento immediato),
  `KILLSWITCH_CLEARED` (killswitch rilasciato, dopo ~200ms di stato stabile),
  `KILLSWITCH_STATE;ENGAGED=0|1;POSITION_UNVERIFIED=0|1` (risposta a
  `GET_KILLSWITCH_STATE`), `TEST_ABORTED;REASON=KILLSWITCH` (prova monotonica
  o ciclica interrotta dal killswitch: dati già acquisiti preservati, non è
  né `TEST_COMPLETED` né `..._STOPPED_BY_USER`), `JOG_REJECTED;REASON=..`,
  `GOTO_REJECTED;REASON=..`, `TEST_START_REJECTED;REASON=..` (rifiuti espliciti
  dei rispettivi comandi per stato killswitch/posizione non verificata — vedi
  sezione Killswitch sotto per il dettaglio), `JOG_STEP_SIZE_SET;MM=..`
  (cambio preset di step del jog encoder fisico, emesso alla pressione del
  pulsante integrato — vedi sezione "Pulsanti manuali e jog encoder fisici").
- **`SCALE:<valore>`** — risposta a `GET_SCALE`, mai richiesta da Python.
- Righe di **debug non prefissate** (es. `"DEBUG: startMotor() chiamato"`,
  `"[DEBUG CMD] Calculated ramp_target_steps: ..."`, `"pulse_count: .."`) —
  ignorate silenziosamente da `main.py` perché non iniziano né per `D:` né per
  `STATUS:`, ma vedi punto critico sotto.

## Pinout ESP32 (hardware)

Riepilogo dei pin GPIO usati o riservati sul firmware ESP32. Vedi anche
`docs/firmware_main.md` per il comportamento firmware dei pin già cablati e
integrati nel codice. Killswitch, pulsanti manuali Up/Down e jog encoder
sono **tutti ora integrati nel firmware** (vedi sezioni dedicate subito
dopo questa). Vedi `CHANGELOG.md`.

### Pin cablati e verificati, già presenti nel firmware (invariati)

| Funzione | Pin | Note |
|---|---|---|
| Motore passo-passo | `PUL_PIN`=2 (step), `DIR_PIN`=4 (direzione) | Uscite digitali verso il driver ISV57T090S |
| Pulsanti manuali Up/Down | `UP_BUTTON_PIN`=18, `DOWN_BUTTON_PIN`=19 | `INPUT_PULLUP` software, ciascuno tra il proprio GPIO e GND, nessun resistore esterno. `handleHardwareInputs()` che li legge è ora riabilitata (con debounce ~25ms) — vedi sezione "Pulsanti manuali e jog encoder" sotto |
| Endstop meccanici | `TOP_ENDSTOP_PIN`=22, `BOTTOM_ENDSTOP_PIN`=23 | |
| Cella di carico NAU7802 (I2C) | `LOADCELL_SDA_PIN`=32, `LOADCELL_SCL_PIN`=33 | |
| Encoder incrementale esterno di misura spostamento (Omron E6B2-CWZ6C, canale Livello 1, sola lettura — vedi sopra) | `ENCODER_PIN_A`=34, `ENCODER_PIN_B`=35, `ENCODER_PIN_Z`=27 | Da non confondere col "jog encoder" sotto: due encoder fisicamente distinti con scopi diversi |
| LCR-meter esterno (UART2) | `LCR_RX_PIN`=16, `LCR_TX_PIN`=17 | |
| **Killswitch (E-stop hardware)** | `KILLSWITCH_SENSE_PIN`=GPIO39 ("VN", input-only, pull-up esterno 10kΩ) | Interrupt `CHANGE` sempre attivo. Vedi sezione dedicata subito sotto per topologia, logica di stato e comportamento firmware/GUI completo |
| **Jog encoder** (controllo manuale a step fini — non l'encoder esterno sopra) | `JOG_ENCODER_A`=GPIO13, `JOG_ENCODER_B`=GPIO14 (comune a GND, entrambi `INPUT_PULLUP`), `JOG_ENCODER_SW`=GPIO25 (pulsante, `INPUT_PULLUP`) | Interrupt `CHANGE` su A/B per la quadratura, pulsante letto a polling con debounce ~50ms. Vedi sezione dedicata sotto |
| **ADS1220** (ADC esterno, SPI, misura resistenza campioni) | `CS`=GPIO5, `SCLK`=GPIO21, `MOSI`(DIN)=GPIO26, `MISO`(DOUT/DRDY)=GPIO36 | Nessun pin DRDY dedicato collegato (nessun GPIO libero rimasto): letto via polling `RDATA` a intervalli in modalità di conversione continua — vedi sezione dedicata subito sotto per schema di alimentazione/misura, registri e protocollo |

### Pin riservati per lavoro futuro (non ancora cablati fisicamente)

Nessuno al momento: tutti i pin GPIO precedentemente riservati (ADS1220) sono
ora cablati e integrati nel firmware — vedi tabella sopra e sezione dedicata
subito sotto.

## ADS1220: resistenza campioni via SPI (cablato e integrato)

ADC esterno 24 bit (SPI) per misurare la resistenza di campioni di
nanofibre con coating conduttivo durante prove stress-strain (comportamento
piezoresistivo). **Non è un canale di sicurezza**: sola lettura, non tocca
`absolute_max_force_grams`/`absolute_max_pulse_count` né alcun criterio di
stop — stesso trattamento "Livello 1" dell'encoder esterno (vedi sopra). Il
progetto ha già un canale di resistenza via **LCR-meter esterno**
(`ENABLE_LCR_POLLING`/`DISABLE_LCR_POLLING`, UART2, invariato): i due
canali sono **alternativi in uso**, mai attivi insieme (la GUI invia sempre
il `DISABLE_*` dell'uno quando abilita l'altro — vedi sotto), quindi il
pacchetto `D:` porta entrambi i valori mentre solo uno dei due è
tipicamente "vivo" alla volta (l'altro resta al sentinel `-999`).

### Cablaggio fisico (verificato, non modificare)

**Alimentazione**: regolatore LP5907 — ingresso 5V, GND, EN=5V, cap
ceramico 10µF fra IN e GND. Uscita 3.3V (cap ceramico 1µF fra OUT e GND) →
alimenta `AVDD` dell'ADS1220 (cap ceramico fra AVDD e AGND per decoupling).
`DVDD` ponticellato ad `AVDD`. `DGND` a massa comune.

**Circuito di misura (ratiometrico, 4 fili)**:
- `AIN0` — coccodrillo 1, iniezione corrente `IDAC1`
- `AIN1` — coccodrillo 1, sense (verso il campione)
- `AIN2` — coccodrillo 2, sense (verso il campione)
- `AIN3` — coccodrillo 2, uscita corrente, **cortocircuitato a `REFP0`**
- Fra `REFP0` e `REFN0`: resistenza di riferimento, **R_ref = 989.58 Ω**
  (valore MISURATO, costante `ADS1220_R_REF_OHM` in `main.cpp`)
- `REFN0` → GND (chiude il loop di corrente)

**SPI verso ESP32**: `CS`=GPIO5, `SCLK`=GPIO21, `DIN`(MOSI)=GPIO26,
`DOUT/DRDY`(MISO)=GPIO36 — vedi tabella pinout sopra. Il pin `DRDY`
dedicato del chip **non è collegato** (nessun GPIO libero: GPIO4, l'unico
"naturale", è già `DIR_PIN` del motore). Questo non è un problema: in
**modalità di conversione continua** (`CM=1`, sempre attiva qui) il
datasheet ADS1220 garantisce che "i dati possono essere letti in
qualunque momento senza rischio di corruzione, e riflettono sempre
l'ultima conversione completata" — il firmware interroga quindi via SPI
(`RDATA`) a intervalli (`updateADS1220Reading()`, rate-limitato al sample
rate corrente), senza pin DRDY e senza rischio di dati corrotti (al
massimo si rilegge lo stesso valore due volte se si interroga più veloce
del rate configurato).

### Configurazione registri (verificata contro il datasheet reale)

Applicati da `applyAds1220Config()` a ogni `ENABLE_ADS1220_POLLING` e a ogni
`SET_ADS1220_CONFIG` riuscito (che riavvia anche le conversioni con
`START/SYNC`, necessario in continuous conversion mode dopo una scrittura
ai registri). Valori di default (`ads1220_sps=330`, `ads1220_gain=16`,
`ads1220_pga_bypass=false`, `ads1220_idac_ua=1500`, `ads1220_window=10`)
corrispondono esattamente a questo esempio di registri:

- **Registro 0** = `0x38`: `MUX=0011` (AIN1-AIN2 differenziale, fisso)
  `GAIN=100`(→16) `PGA_BYPASS=0` (PGA attivo) — guadagno reale 16, non 1
- **Registro 1** = `0x84`: `DR=100`(→330 SPS, Normal mode) `MODE=00`
  (Normal, fisso) `CM=1` (continuous, fisso) `TS=0` `BCS=0`
- **Registro 2** = `0x47`: `VREF=01` (esterno REFP0/REFN0, fisso) `50/60=00`
  (**filtro sempre spento** — obbligatorio da datasheet per SPS≠20 in
  Normal mode, tenuto spento anche a 20 SPS) `PSW=0` `IDAC=111`(→1500µA)
- **Registro 3** = `0x20`: `I1MUX=001` (IDAC1→AIN0/REFP1, fisso)
  `I2MUX=000` (disabilitato, fisso) `DRDYM=0` `RESERVED=0`
- Formula di conversione: `R_x = (rawData / (2^23 * gain)) * R_ref` — il
  valore di IDAC **non entra nella formula** (si semplifica
  ratiometricamente, la stessa corrente attraversa `R_ref` e `R_x` in
  serie): influisce solo su rumore/autoriscaldamento del campione, non
  sulla correttezza del dato.
- Media mobile software su finestra configurabile (`ads1220_window`,
  default 10 campioni, max 20), implementata come buffer circolare in
  `updateADS1220Reading()`.

`GAIN` e `PGA_BYPASS` sono configurabili via `SET_ADS1220_CONFIG` (vedi
tabella comandi sopra); `MUX` e `I1MUX`/`I2MUX` restano fissi (circuito di
misura cablato per una configurazione sola). `PGA_BYPASS` ha effetto solo
per `GAIN<8`: per `GAIN>=8` il firmware forza `PGA_BYPASS=0`
(`ads1220_pga_bypass_applied`) qualunque fosse la richiesta, obbligatorio
da datasheet, e lo riporta nella risposta invece di rifiutare il comando.

### Range di resistenza misurabile e verifica su hardware reale

**Verificato su hardware reale** (sessione successiva a quella di prima
integrazione, con la macchina fisicamente collegata): registri applicati
correttamente (`0x38 0x84 0x47 0x20`, confermati rileggendoli via `RREG`
con il comando diagnostico temporaneo `DEBUG_ADS1220`) e lettura corretta
di un resistore di prova a basso valore. Durante questa verifica è emerso
(non un bug, un vincolo fisico della misura ratiometrica non reso esplicito
prima):

```
R_x,max = R_ref / gain        (indipendente da IDAC)
```

Oltre `R_x,max` l'ADC satura a fondo scala positivo (`raw = 2^23-1 =
8388607`) e la formula restituisce sempre lo stesso numero — **un circuito
aperto (resistenza infinita) satura allo stesso identico codice di una
resistenza semplicemente troppo alta per il guadagno corrente**, quindi i
due casi sono indistinguibili dal solo valore letto. Con
`R_ref = 989.58 Ω`:

| `GAIN` | `R_x,max` |
|---|---|
| 1 (minimo disponibile) | ≈ 989.58 Ω |
| 16 (default) | ≈ 61.85 Ω |
| 128 (massimo) | ≈ 7.73 Ω |

**Implicazione pratica**: con l'attuale resistenza di riferimento, il
range assoluto massimo misurabile è **~990 Ω** (a `GAIN=1`), qualunque
combinazione di `GAIN`/`IDAC` si scelga — `IDAC` non entra nella formula,
influisce solo su rumore/autoriscaldamento del campione (vedi sopra), non
sul range. Per misurare campioni con resistenza attesa superiore serve un
resistore di riferimento fisicamente più grande (va poi rimisurato con
precisione e riportato in `ADS1220_R_REF_OHM`, sostituendo `989.58`) — non
è un cambio configurabile via `SET_ADS1220_CONFIG`, è un cambio hardware.
Discussa con l'utente anche l'idea di un banco di resistenze di riferimento
commutabili via multiplexer analogico (per coprire più decadi senza
ricablare fisicamente), non implementata — vedi `TODO.md` per i vincoli
principali (GPIO liberi scarsi sulla mappa pin attuale, caratterizzazione
della resistenza ON del mux).

**`DEBUG_ADS1220`** (comando diagnostico **temporaneo**, non in tabella
comandi sopra): rilegge i 4 registri via `RREG` e fa un campione `RDATA`
immediato, a prescindere da `ads1220_polling_enabled`, rispondendo
`STATUS:ADS1220_DEBUG;REG0=0x..;REG1=0x..;REG2=0x..;REG3=0x..;RAW=..;RES=..`.
Aggiunto per diagnosticare il bug di parsing sotto e per la verifica del
range sopra; da valutare se rimuovere a validazione completata (stesso
spirito di `DEBUG_RAW_KILLSWITCH`, già rimosso in passato).

**Bug storico corretto — `SET_ADS1220_CONFIG` sempre rifiutato con
`REASON=OUT_OF_RANGE`**: il parsing usava `command.substring(20)`, ma
`"SET_ADS1220_CONFIG:"` è lunga **19** caratteri — il primo carattere (`S`
di `SPS=`) veniva scartato e `indexOf("SPS=")` falliva sempre. Riprodotto
alla prima connessione reale (la GUI invia questo comando automaticamente
dopo la connessione) e corretto in `command.substring(19)`. Vedi
`CHANGELOG.md` per il dettaglio della diagnosi.

### Comportamento GUI (selettore Sorgente Resistenza)

In `manual_control_widget.py`/`monotonic_test_widget.py`/
`cyclic_test_widget.py`, il vecchio checkbox "Enable LCR Reading" è
sostituito da un combo box `resistance_source_combo` ("Off"/"LCR"/
"ADS1220"), stesso schema in tutte e tre le schermate (come già l'LCR).
Al cambio selezione invia sempre una coppia `ENABLE_*`/`DISABLE_*` (mai
`ENABLE_LCR_POLLING` e `ENABLE_ADS1220_POLLING` insieme). **Stato non
sincronizzato fra schermate** (stessa limitazione preesistente del
checkbox LCR: cambiare sorgente su una schermata non aggiorna il combo
delle altre, anche se il comando agisce comunque sullo stato firmware
globale) — `MainWindow` traccia comunque centralmente
`active_resistance_source` (via segnale `resistance_source_changed` emesso
da ciascun widget) al solo scopo di disabilitare il dialog "ADS1220
Settings" (nel menu principale, sul modello di "Filter Config") mentre il
canale è attivo su una qualunque schermata. Un unico grafico/display
"Resistance (Ω)" per schermata, alimentato da qualunque dei due campi
(`RES_LCR`/`RES_ADS`) arrivi, in base alla sorgente selezionata su quella
stessa schermata — nessun overlay delle due curve (a differenza di
Motor/Encoder displacement): sono alternative, non complementari.

I dati salvati (`data_saver.py`) includono una colonna "Resistance
Source" ("LCR"/"ADS1220"/"OFF") accanto a "Resistance (Ohm)", per sapere
sempre quale sorgente era attiva durante ciascun punto della prova.

### Validazione timing (jitter `loop()`) — **NON ancora misurata su hardware reale**

Da non confondere con la verifica hardware della sezione precedente
(registri/range, quella sì eseguita fisicamente): qui si parla
specificamente del jitter di `STREAM_INTERVAL_MS`/cadenza step a piena
velocità di traversa col canale ADS1220 attivo, mai misurato — l'accesso
alla macchina avuto finora è servito solo per diagnosi via seriale diretta
a motore fermo, non per un test di movimento a velocità.

Il polling SPI (`updateADS1220Reading()`) si aggiunge allo stesso `loop()`
che gestisce lo streaming dati (`STREAM_INTERVAL_MS`) e la cadenza dei
comandi motore, esattamente come l'overhead delle ISR encoder già segnalato
come non misurato in `TODO.md`. Stima teorica (non verificata fisicamente):
una singola transazione `RDATA` (comando + 3 byte dati) a 1 MHz SPI dura
nell'ordine di poche decine di µs, rate-limitata a un'esecuzione ogni
`1000/SPS` ms (1 ms nel caso peggiore a 1000 SPS) — quindi un impatto atteso
trascurabile rispetto a `STREAM_INTERVAL_MS` (20 ms). Il timer hardware dei
passi motore (ISR `onStepTimer()`) non è comunque influenzato da un
`loop()` più lento, essendo un interrupt hardware indipendente; il rischio
reale è solo un aumento del jitter di invio del pacchetto `D:`. **Questa
è una stima, non una misura**: non è stato possibile eseguire la
validazione fisica richiesta (jitter reale su `STREAM_INTERVAL_MS` e sulla
cadenza di step a piena velocità di traversa, con canale ADS1220 attivo) in
questa sessione — nessun accesso alla macchina fisica. Resta un passo
esplicito da fare prima di un uso in produzione ad alta velocità, vedi
`TODO.md`.

## Killswitch: comportamento firmware e GUI

Sottosistema di sicurezza hardware (E-stop reale sulla linea di potenza
+48V), integrato nel firmware in questa sessione. Cablaggio, topologia
elettrica e verifica fisica invariati da quanto verificato con sketch di
test standalone (vedi `CHANGELOG.md`, voce 2026-07-21):

- Interruttore NC in serie sulla linea +48V a monte del driver ISV57T090S —
  l'apertura taglia realmente l'alimentazione, indipendentemente da qualunque
  logica software. Sensing tramite optoisolatore 4N25 su `KILLSWITCH_SENSE_PIN`
  = GPIO39 ("VN", input-only, pull-up esterno 10kΩ verso 3.3V).
- Logica di stato (verificata fisicamente, verso non intuitivo): GPIO39 =
  **LOW** → riposo (48V presenti); GPIO39 = **HIGH** → premuto (48V tagliati
  realmente).
- Nota sulla topologia di massa: nessun isolamento galvanico reale nel
  sistema (GND 48V/12V/encoder/logico ESP32 sono la stessa rete) — vedi
  `CHANGELOG.md` per il dettaglio completo; rilevante se in futuro emerge
  rumore su cella di carico/encoder.

### Sensing e debounce (firmware, `main.cpp`)

Interrupt `CHANGE` su GPIO39, **sempre attivo**, indipendente da `motor_state`
o `comms_mode`: funziona in qualunque condizione della macchina, inclusa una
prova in corso. Debounce **asimmetrico** voluto:

- **Pressione** (transizione a HIGH): rilevata immediatamente in
  `updateKillswitchState()` (chiamata per prima a ogni giro di `loop()`),
  nessun ritardo.
- **Rilascio** (transizione stabile a LOW): richiede **~200ms** di stato
  stabile (`KILLSWITCH_RELEASE_DEBOUNCE_MS`) prima di essere considerato
  reale, per non scambiare un rimbalzo meccanico in rilascio per un vero
  rilascio. Implementato con un solo timestamp (`killswitch_last_high_ms`,
  aggiornato dall'ISR a ogni fronte HIGH, anche durante un rimbalzo): il
  rilascio è dichiarato solo quando sono passati ≥200ms dall'ULTIMO fronte
  HIGH visto, quindi un rimbalzo a metà finestra riavvia l'attesa.

### Stato `position_unverified`

Flag che indica se la posizione della macchina (`pulse_count`) è considerata
affidabile:

- Impostato `true` alla pressione del killswitch (`onKillswitchTriggered()`),
  o già `true` dal **boot** se il killswitch risulta premuto all'accensione
  (letto in `setup()` prima di attaccare l'interrupt — copre anche il caso di
  un riavvio dell'ESP32 durante un'emergenza già in corso; in quel caso il
  firmware invia comunque `STATUS:KILLSWITCH_TRIGGERED` durante `setup()`).
- Resta `true` durante il rilascio (`STATUS:KILLSWITCH_CLEARED` non lo
  tocca): un rilascio da solo NON basta, serve un **HOME completato con
  successo** (`position_unverified = false`, impostato in
  `updateMotorState()` alla fine di `HOMING_FINAL_LIFT`, appena prima di
  `STATUS:HOMING_COMPLETED`).
- A un **boot pulito** (killswitch non premuto), `position_unverified` parte
  `false`, coerentemente con il comportamento preesistente del resto del
  sistema (l'homing non è già altrimenti imposto a ogni riavvio) — vedi
  "Compromessi noti" sotto.

Comandi bloccati mentre `position_unverified == true` (stato "giallo" se il
killswitch non è premuto ORA, "rosso" se lo è): avvio di qualunque prova
(`START_TEST`/`START_CYCLIC_TEST` → `STATUS:TEST_START_REJECTED`) e `GOTO`
(→ `STATUS:GOTO_REJECTED`). **`HOME` resta sempre permesso**: è l'unico modo
di uscirne. `JOG_UP`/`JOG_DOWN` sono gated separatamente su
`killswitch_engaged` (non su `position_unverified`): permessi in stato
giallo, bloccati solo in stato rosso (→ `STATUS:JOG_REJECTED` se rifiutati) —
predisposto così perché i futuri jog fisici (pulsanti/encoder, non
implementati in questa sessione) riusino la stessa variabile globale.

### Reazione al trigger (`onKillswitchTriggered()`)

Alla transizione a HIGH: stesso path già usato dallo stop di emergenza `!`
esistente — `motor_state = STOPPED`, `comms_mode = POLLING`, `stopMotor()`,
`target_steps_remaining = 0` — applicato qualunque fosse `motor_state`
(jog, homing, test monotonico/ciclico). Poi: `STATUS:KILLSWITCH_TRIGGERED`
sempre, e **in aggiunta** `STATUS:TEST_ABORTED;REASON=KILLSWITCH` se un test
era in corso (distinto da `TEST_STOPPED_BY_USER`/`CYCLIC_TEST_STOPPED_BY_USER`,
che implicano uno stop volontario). Lato Python, questo pacchetto fa
finalizzare/autosalvare il provino corrente **mantenendo tutti i dati già
raccolti**, con una marcatura esplicita:
- `MonotonicTestWidget`/`CyclicTestWidget.on_stop_test(user_initiated=False, abort_reason="KILLSWITCH")`.
- Nome file autosave con prefisso `AUTOSAVE_KILLSWITCH_ABORTED_...`/
  `AUTOSAVE_CYCLIC_KILLSWITCH_ABORTED_...` invece di `AUTOSAVE_...`.
- `DataSaver` scrive una riga `Test Status: INTERRUPTED — KILLSWITCH` in
  grassetto/rosso nella sezione parametri del foglio Excel.
- **Scelta deliberata lato GUI, non esplicitamente richiesta**: se il
  provino monotonico aveva "Return to start" attivo, l'invio automatico di
  `RETURN_TO_START` a fine test viene comunque **saltato** in questo caso
  (in `on_stop_test`), per non inviare nemmeno il comando dopo
  un'emergenza, prima che l'operatore rifaccia l'HOME — anche se ora
  `RETURN_TO_START` è gated da `position_unverified` a livello firmware
  esattamente come `GOTO` (il firmware lo rifiuterebbe comunque con
  `STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED`), evitare l'invio resta
  più pulito che affidarsi al rifiuto.

### Comportamento a boot con killswitch già premuto

Verificato: `setup()` legge lo stato del pin **prima** di attaccare
l'interrupt, e se risulta HIGH imposta subito `killswitch_engaged = true` e
`position_unverified = true`, oltre a inviare `STATUS:KILLSWITCH_TRIGGERED`.
Poiché l'apertura della porta seriale da PC causa tipicamente un reset
hardware dell'ESP32 (CH340/CP210x, vedi punto critico 3), questo messaggio di
boot arriva quasi sempre **dopo** che la GUI si è già connessa, quindi viene
recepito normalmente. Per le schede che non resettano all'apertura porta (o
per un riavvio del solo processo Python con ESP32 già acceso), la GUI invia
comunque `GET_KILLSWITCH_STATE` subito dopo la connessione
(`_send_post_connect_commands()`), a cui il firmware risponde con lo stato
reale corrente — copre quindi entrambi i casi.

### GUI Python

- **Indicatore a 3 livelli** (`KillswitchIndicatorWidget`,
  `custom_widgets.py`): verde (`killswitch_engaged=False`,
  `position_unverified=False`), giallo/ambra (`position_unverified=True`,
  non premuto ORA — "HOMING REQUIRED"), rosso (`killswitch_engaged=True` —
  "KILLSWITCH"). Presente nella finestra principale (barra in alto, fuori
  da `QStackedWidget`: visibile su ogni schermata) e nei dialoghi `LIMITS`/
  `Filter Config` (registrati/deregistrati in `MainWindow._killswitch_indicators`
  all'apertura/chiusura, per restare live anche mentre il dialog è aperto).
- **Banner persistente** (`KillswitchBannerWidget`): in aggiunta al popup,
  non alternativo — resta visibile finché lo stato non torna verde, così
  l'informazione non si perde se il popup viene chiuso.
- **Popup non bloccante** su `KILLSWITCH_TRIGGERED`
  (`MainWindow._show_killswitch_popup()`): `QMessageBox` con
  `WindowModality.NonModal` + `.show()` invece di `.exec()`, per non
  impedire l'uso del resto del software mentre è aperto.
- **Gating controlli movimento**: `set_killswitch_state(engaged, position_unverified)`
  su `ManualControlWidget`, `MonotonicTestWidget`, `CyclicTestWidget` —
  disabilita avvio prova/GOTO se `position_unverified`, Up/Down (software)
  se `killswitch_engaged`. Homing, impostazioni, calibrazione,
  visualizzazione/esportazione dati e connessione/disconnessione seriale
  restano sempre utilizzabili. Doppia difesa: oltre alla disabilitazione dei
  pulsanti, `on_start_test()`/`toggle_goto()` ricontrollano esplicitamente
  `position_unverified` (il firmware la rifiuterebbe comunque).

### Log eventi di sistema (`event_logger.py`)

Nuovo modulo `EventLogger`, log JSON Lines append-only in
`event_log.jsonl` (cartella dell'app, accanto a `settings.json`),
**separato dai dati di misura delle prove** (mai forza/spostamento/tempo
delle prove stesse — solo eventi discreti con timestamp). Eventi loggati:
`killswitch_triggered`, `killswitch_cleared`, `homing_completed`,
`test_aborted_killswitch` (con provino e tipo test), `serial_connected`/
`serial_disconnected`, `calibration_invalidated`. Pensato come
infrastruttura generale: il killswitch è solo il primo evento che la usa,
altri eventi di sistema futuri possono aggiungersi con la stessa `EventLogger.log(tipo, dettagli)`.

### Compromessi e rischi residui noti

1. **Lag di rilascio**: fino a ~200ms tra il rilascio fisico del killswitch e
   `STATUS:KILLSWITCH_CLEARED` (debounce voluto, vedi sopra) — nessun impatto
   sulla sicurezza (il motore resta comunque fermo finché non arriva
   esplicitamente un nuovo comando di movimento), solo un ritardo
   nell'aggiornamento dell'indicatore/banner.
2. **Boot pulito non forza l'homing**: `position_unverified` parte `false` a
   un riavvio normale (killswitch non premuto), quindi `pulse_count = 0` di
   un boot pulito non passa dallo stato giallo — comportamento preesistente
   dell'intero sistema (mai stato diverso), non introdotto né corretto da
   questa modifica. Se in futuro si vuole imporre l'homing a ogni singolo
   boot (non solo dopo un killswitch), va discusso separatamente.
3. ✅ **[RISOLTO]** `RETURN_TO_START` è ora gated a livello firmware da
   `position_unverified` esattamente come `GOTO` (stessa guardia, stesso
   messaggio di rifiuto `STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED`,
   riusato deliberatamente perché stessa categoria di comando e stesso
   motivo — si basa sullo stesso `pulse_count` non più affidabile). In
   aggiunta, la GUI continua a non inviarlo affatto dopo un abort da
   killswitch (vedi sopra).
4. **Dialoghi modali (`LIMITS`/`Filter Config`) e popup non modale**: se il
   killswitch scatta mentre uno di questi dialoghi è aperto, l'indicatore al
   suo interno si aggiorna comunque (il thread seriale continua a emettere
   segnali durante `exec()`), ma il popup non modale potrebbe restare dietro
   o non ricevere subito il focus finché il dialog modale non viene chiuso —
   limitazione nota, non risolta (richiederebbe rendere non modali anche
   quei dialoghi, fuori scope di questa sessione).
5. ✅ **[VERIFICATO SU HARDWARE REALE, 2026-07-21]** Scenari idle, homing e
   test-in-corso confermati dall'utente sulla macchina fisica. Lo scenario
   "boot con killswitch già premuto" ha invece rivelato un guasto hardware
   (non software) nel cablaggio verso GPIO39, diagnosticato e poi risolto
   dall'utente — vedi `CHANGELOG.md`, voce "Diagnosi: software non
   funziona...", per il dettaglio completo (incluso il comando diagnostico
   temporaneo usato per isolare il guasto). Non risulta una riverifica
   esplicita post-fix dello scenario di boot specifico; se serve
   ridiagnosticare in futuro, il metodo (lettura diretta del pin via un
   comando seriale temporaneo tipo `DEBUG_RAW_KILLSWITCH`, poi rimosso) è
   documentato nello stesso punto del changelog.

## Pulsanti manuali e jog encoder fisici: comportamento firmware e GUI

Seguito della voce "Hardware: pulsanti manuali, jog encoder e killswitch —
cablati e verificati" (`CHANGELOG.md`): in questa sessione è stata scritta
la logica firmware che legge questo hardware, riusando deliberatamente le
stesse funzioni di movimento (`startMotor()`/`stopMotor()`) già usate da
`JOG_UP`/`JOG_DOWN` via seriale — nessuna nuova logica di movimento, solo
nuovi modi di attivarla.

### Guardia di attivazione condivisa (`isManualJogAllowed()`)

Unico punto di verità in `main.cpp`, usato sia dai pulsanti Up/Down sia dal
jog encoder (nessuna copia duplicata della condizione):

```cpp
bool isManualJogAllowed() {
  return (motor_state == STOPPED) && (target_steps_remaining == 0) && (!killswitch_engaged);
}
```

- `motor_state == STOPPED`: esclude qualunque test (monotonico/ciclico),
  homing o jog già in corso.
- `target_steps_remaining == 0`: esclude anche un `GOTO`/`RETURN_TO_START`
  in corso — questi lasciano `motor_state == STOPPED` ma muovono comunque il
  motore a passi contati, quindi vanno esclusi esplicitamente (controllo più
  stringente di quello usato oggi da `JOG_UP`/`JOG_DOWN` via seriale, che
  non lo verifica: introdotto solo per i nuovi input fisici, senza toccare
  il comportamento seriale esistente).
- `!killswitch_engaged`: **non** controlla `position_unverified` — in stato
  "giallo" (posizione non verificata ma killswitch rilasciato) il jog
  manuale fisico deve restare utilizzabile, esattamente come già avviene per
  `JOG_UP`/`JOG_DOWN` via seriale (vedi sezione Killswitch sopra).

Se la guardia non è soddisfatta, pulsanti ed encoder non fanno nulla
silenziosamente: nessun comando/messaggio inviato al PC.

### Pulsanti manuali Up/Down (`handleHardwareInputs()`)

Polling nel `loop()` principale (non interrupt), debounce software a
transizione ~25ms (`BUTTON_DEBOUNCE_MS`) per canale, `INPUT_PULLUP`
(nessuna resistenza esterna, cablaggio invariato). Alla pressione (se la
guardia lo permette): imposta `motor_state = JOG_UP`/`JOG_DOWN` e chiama
`startMotor(true/false)` — **la stessa identica funzione** usata dal comando
seriale `JOG_UP`/`JOG_DOWN`, quindi eredita automaticamente lo stesso
controllo di endstop e limiti assoluti applicato in `updateMotorState()`.
Al rilascio: ferma il movimento esattamente come fa oggi il rilascio del
jog da GUI (che invia il comando seriale `STOP`) — `motor_state = STOPPED`,
`stopMotor()`, ed emette lo stesso `STATUS:STOPPED_BY_USER` (se il motore
non è già stato fermato nel frattempo da un'altra causa, es. endstop o
killswitch: in quel caso il rilascio richiude solo la contabilità interna
senza inviare nulla, per non duplicare il messaggio).

Una bandierina di "proprietà" per pulsante (`up_button_owns_jog`/
`down_button_owns_jog`), distinta dal generico `is_hardware_jog_active` già
esistente (che continua a bloccare `JOG_UP`/`JOG_DOWN`/`HOME`/`SET_SPEED`/
`GOTO` via seriale finché un jog fisico è attivo, comportamento
preesistente invariato), garantisce che il rilascio del pulsante corretto
richiuda sempre la contabilità anche se il motore è già stato fermato da
un'altra causa nel frattempo (es. killswitch) — altrimenti la bandierina
`is_hardware_jog_active` resterebbe bloccata a `true` per sempre, impedendo
qualunque comando seriale successivo. Vedi "Compromessi e rischi residui"
sotto per il dettaglio di questo caso.

### Jog encoder (movimento fine a step contati)

Decodifica quadratura su interrupt `attachInterrupt(..., CHANGE)` su
`JOG_ENCODER_A`/`JOG_ENCODER_B`, ISR minimale (`handleJogEncoderChange()`):
solo incremento/decremento di un contatore volatile (`jogEncoderCount`),
nessuna chiamata a funzioni di movimento dentro l'ISR. Riusa la stessa
tabella di decodifica generica `ENCODER_QUAD_TABLE` già esistente per
l'encoder esterno (puramente combinatoria, non specifica a un device).
**Verso di conteggio invertito rispetto al segno "naturale" della tabella**,
per allinearlo alla rotazione fisica attesa (verificato su hardware con
sketch standalone in una sessione precedente, vedi `CHANGELOG.md`): la ISR
sottrae il delta invece di sommarlo, invece di scambiare i pin `A`/`B` —
scelta equivalente, nessun ricablaggio necessario.

Consumo nel `loop()` principale (`handleJogEncoderMotion()`): se non c'è già
un movimento a step in corso, e la guardia condivisa lo permette, converte
l'intero delta accumulato in un singolo movimento relativo di
`delta × step_size_corrente`, impostando `motor_state = JOG_UP`/`JOG_DOWN` e
chiamando `startMotor()` — **non** usa il meccanismo a passi contati
(`target_steps_remaining`) già usato da `GOTO`/`RETURN_TO_START`, proprio
perché quel meccanismo salta il controllo endstop ad ogni giro di loop
(vedi `updateMotorState()`): usando invece lo stesso `motor_state` del jog
normale, il movimento del jog encoder resta soggetto esattamente agli stessi
controlli di endstop e limiti assoluti del jog normale, monitorando un
target di `pulse_count` assoluto per sapere quando fermarsi da solo. Se la
guardia non è soddisfatta, o un movimento precedente è ancora in corso, il
delta resta accumulato nel contatore (letto e sottratto atomicamente in
sezione critica) senza essere perso né duplicato, e viene consumato al
turno in cui la condizione si sblocca.

Usa una **velocità dedicata fissa** (`JOG_ENCODER_SPEED_MMS = 0.5 mm/s`),
deliberatamente più bassa della velocità di jog normale (quella impostata
con `SET_SPEED`), per non perdere passi motore su spostamenti così piccoli:
la velocità del timer passi (`pulse_delay_micros`) viene salvata prima di
ogni step e ripristinata subito dopo (sia a fine step normale, sia se il
movimento viene interrotto da endstop/limite/killswitch), per non lasciare
la macchina a questa velocità ridotta per i jog successivi (pulsante o
seriale).

**Tre preset di step**, ciclati dal pulsante integrato dell'encoder
(`JOG_ENCODER_SW`, debounce dedicato ~50ms via `handleJogStepButton()`,
indipendente dal debounce di rotazione): costanti regolabili in testa a
`main.cpp` (`JOG_STEP_SIZE_FINE_MM=0.05`, `JOG_STEP_SIZE_VERY_FINE_MM=0.01`,
`JOG_STEP_SIZE_FINEST_MM=0.005`), convertite in passi motore con la stessa
costante `PULSES_TO_MM` già esistente (nessuna nuova costante meccanica
introdotta). Alla pressione del pulsante, il firmware invia
`STATUS:JOG_STEP_SIZE_SET;MM=<valore>` (stile coerente col resto del
protocollo, es. `LIMITS_SET`, `FILTER_CONFIG_SET`); lato Python,
`MainWindow.handle_data_from_esp32()` inoltra il valore a
`ManualControlWidget.set_jog_step_size()`, che aggiorna un nuovo
`DisplayWidget` ("Jog Encoder Step (mm)") — puro display informativo,
nessuna azione lato GUI (il preset è gestito interamente dal firmware,
funziona anche a GUI chiusa/PC scollegato).

### Compromessi e rischi residui noti

1. **Risoluzione minima quantizzata**: 1 passo motore corrisponde a
   `PULSES_TO_MM ≈ 0.000254 mm`. Il preset più fine richiesto (0.005mm)
   corrisponde quindi a `round(0.005 / 0.000254) = 20` passi reali
   (`≈0.00509mm`), con un errore di quantizzazione di circa l'1.8%
   rispetto al valore nominale — trascurabile, ma da tenere presente in
   fase di taratura su macchina reale insieme ai due preset più larghi
   (39 passi ≈0.00992mm per 0.01mm, 197 passi ≈0.05011mm per 0.05mm).
2. **Semantica del "delta" per la conversione mm/click**: il firmware
   applica `step_size_corrente` per ogni singolo conteggio di quadratura
   grezzo (`jogEncoderCount`), non per "scatto/detent" meccanico
   dell'encoder. Se l'encoder fisico genera più conteggi di quadratura per
   scatto (comune sugli encoder economici EC11-style, tipicamente 4
   conteggi/scatto), il movimento risultante per click potrebbe essere un
   multiplo del preset nominale — da verificare e tarare su macchina reale
   (i preset sono costanti facilmente modificabili proprio per questo).
3. **Pulsante + encoder premuti/girati contemporaneamente**: la guardia
   condivisa risolve l'ambiguità per costruzione — se un pulsante ha già
   avviato un jog (`motor_state != STOPPED`), l'encoder non può avviare un
   movimento fino al rilascio del pulsante (il suo delta resta accumulato
   in coda), e viceversa un pulsante premuto mentre un movimento a step
   dell'encoder è in corso non fa nulla finché quello non si conclude.
   Nessuno dei due può "interrompere" l'altro: il primo a partire ha
   sempre priorità fino al proprio rilascio/completamento naturale.
4. **Killswitch premuto durante un jog fisico attivo (pulsante o encoder)**:
   comportamento verificato leggendo il codice (non ancora su hardware
   reale in questa sessione) — `onKillswitchTriggered()` (logica invariata,
   non toccata) ferma il motore incondizionatamente
   (`motor_state = STOPPED`, `stopMotor()`) qualunque fosse la causa del
   movimento, jog fisico incluso. La contabilità dei nuovi flag
   (`is_hardware_jog_active`, `up_button_owns_jog`/`down_button_owns_jog`,
   `jog_step_move_active`) si richiude correttamente al giro di `loop()`
   successivo (`handleHardwareInputs()`/`handleJogEncoderMotion()` rilevano
   che `motor_state` non è più `JOG_UP`/`JOG_DOWN` e ripuliscono lo stato,
   incluso il ripristino della velocità di jog per l'encoder) senza
   richiedere alcuna modifica alla logica del killswitch stessa. **Non
   ancora verificato fisicamente premendo il killswitch reale mentre un
   pulsante è fisicamente tenuto premuto o l'encoder in movimento** — da
   fare come prossimo passo prima di un uso reale, insieme alla verifica
   dell'inversione A/B e della taratura dei preset di step.
5. **Debug spam ereditato (vedi punto critico 4 sotto)**: come il jog
   software esistente, anche i movimenti avviati da pulsanti/encoder
   passano per `startMotor()`, che stampa una riga di debug non prefissata
   ad ogni chiamata — per il jog encoder l'impatto è trascurabile (i
   movimenti sono pochi passi, quasi istantanei), per i pulsanti tenuti
   premuti a lungo si somma al problema preesistente, non introdotto né
   risolto da questa modifica.

## Punti critici / fragili (confermati leggendo il codice, non modificati)

1. ✅ **[RISOLTO 2026-07-02]** `MonotonicTestWidget.on_start_test()` andava in
   crash su stop criterion a Forza. In [monotonic_test_widget.py:339](monotonic_test_widget.py#L339)
   il codice faceva `if target_force_abs_N > self.current_force_limit_N:` ma
   `MonotonicTestWidget` **non definiva mai `self.current_force_limit_N`** — esiste
   solo come `self.main_window.current_force_limit_N` (usato correttamente altrove,
   es. righe 342, 601, 610, 730, 737). Qualunque avvio di test monotonico con
   `Stop Criterion = Force (N)` o `Stress (MPa)` sollevava `AttributeError` a
   runtime. `CyclicTestWidget` non aveva questo problema: usa sempre
   `self.main_window.current_force_limit_N`. Corretto il riferimento a
   `self.main_window.current_force_limit_N`; verificato su hardware reale (vedi
   `CHANGELOG.md`).

2. ✅ **[RISOLTO 2026-07-14]** Il pulsante "Save Calibration" non salvava
   nulla. `CalibrationWidget.save_calibration()` apriva un file dialog e poi
   si limitava a `self.save_calibration_requested.emit(filePath)`, un
   segnale mai collegato a nessuno slot in `main.py`: l'utente sceglieva un
   percorso, vedeva l'interazione completarsi, ma nessun file JSON veniva
   scritto — e il widget non aveva comunque modo di sapere quale fosse il
   fattore di scala corrente da salvare (nessun comando/risposta lo
   comunicava mai alla GUI). Risolto tracciando `current_calibration_factor`
   sul widget, popolato da `STATUS:CALIBRATION_DONE;SCALE=..` (inoltrato da
   `MainWindow`) dopo una calibrazione, o direttamente dopo un
   `load_calibration()` da file; `save_calibration()` ora scrive il JSON
   direttamente (rimossa l'indirezione morta verso `MainWindow`), ed è
   disabilitato se non c'è ancora un fattore noto. Il segnale
   `save_calibration_requested` è stato rimosso. Vedi `CHANGELOG.md` e
   `docs/calibration_widget.md`.

3. ✅ **[RISOLTO 2026-07-02]** I limiti di sicurezza assoluti del firmware non
   erano mai sincronizzati automaticamente. `absolute_max_force_grams`/
   `absolute_max_pulse_count` in `main.cpp` partono a valori enormi
   (`9999999`/`99999999`, cioè di fatto disabilitati) e vengono aggiornati
   **solo** quando arriva un comando `SET_LIMITS:...`. Prima del fix, quel
   comando partiva solo da `MainWindow.show_limits_dialog()` quando l'utente
   apriva manualmente "LIMITS" e cliccava Save; né `on_connected()` né
   `update_calibration_status()` lo inviavano mai in automatico. Conseguenza
   pratica: dopo un power-cycle dell'ESP32 la macchina non aveva limiti
   realmente attivi finché l'operatore non apriva esplicitamente "LIMITS",
   anche se la GUI mostrava già un valore di default (10 N / 190 mm) che
   sembrava "impostato".

   Estratta la logica di invio in `send_limits_to_firmware()`
   ([main.py:445-452](main.py#L445-L452)), richiamata automaticamente sia da
   `on_connected()` sia da `update_calibration_status()` (oltre che da
   `show_limits_dialog()`). Durante il collaudo su hardware reale è emerso un
   secondo problema correlato: i comandi inviati **subito** dopo la
   connessione venivano persi, perché l'apertura della porta seriale causa
   spesso un reset hardware dell'ESP32 (comune sulle schede con USB-seriale
   CH340/CP210x), e il firmware non è ancora pronto a riceverli durante il
   boot. Risolto ritardando l'invio di 2 secondi tramite
   `_send_post_connect_commands()` ([main.py:126-135](main.py#L126-L135)).
   Entrambi i fix sono verificati su macchina fisica (vedi `CHANGELOG.md`).

4. **Firmware: spam di `Serial.println()` di debug non prefissati, ad alta
   frequenza, sul link dati.** `startMotor()` ([firmware/src/main.cpp:642-647](firmware/src/main.cpp#L642-L647))
   stampa `"DEBUG: startMotor() chiamato"` **ogni volta che viene chiamata** — e in
   `updateMotorState()` viene richiamata ad ogni ciclo di `loop()` quando
   `motor_state == JOG_UP` (o `JOG_DOWN`), cioè potenzialmente migliaia di volte al
   secondo durante un jog manuale. Questo non rompe il parsing Python (le righe non
   iniziano per `D:`/`STATUS:` e vengono scartate), ma occupa banda seriale e CPU
   dell'ESP32 in un percorso temporalmente sensibile (lo stesso loop che gestisce lo
   streaming dati e lo stato del motore). C'è anche un blocco di debug simile,
   parzialmente commentato con `//`, dentro `EXECUTE_RAMP` (righe 499-502, 522-528).

5. **Riuso di variabili di stato ciclico per scopi diversi (hack fragile).**
   Il comando `EXECUTE_PAUSE` ([firmware/src/main.cpp:465-481](firmware/src/main.cpp#L465-L481)) memorizza la
   durata della pausa dentro `cyclic_hold_upper_ms` — la stessa variabile usata dai
   blocchi ciclici per il tempo di hold al limite superiore — con un commento
   esplicito nel codice: *"Riutilizziamo la variabile degli hold"* /
   *"Memorizziamo la durata qui (o crea una variabile dedicata)"*. Funziona perché
   ogni blocco configura le proprie variabili prima di partire, ma è un accoppiamento
   implicito: chiunque aggiunga un nuovo tipo di blocco o cambi l'ordine delle
   operazioni rischia di leggere/sovrascrivere un valore "sporco" lasciato da un
   blocco precedente.

6. **Parsing dei comandi nel firmware è manuale e non robusto.** Tutti i comandi con
   parametri (`START_CYCLIC_TEST`, `EXECUTE_RAMP`, `SET_LIMITS`, `START_TEST`, ecc.)
   sono spacchettati con `command.indexOf("CHIAVE=") + N` e `substring(...)` senza
   validare che i campi siano presenti o nell'ordine atteso. Se un campo manca o è
   nell'ordine sbagliato, `indexOf` ritorna `-1` e l'aritmetica sugli indici produce
   `substring` su range invalidi/negativi — comportamento non definito lato Arduino
   `String` (nella migliore delle ipotesi una stringa vuota, nella peggiore un
   comportamento inatteso). Il Python costruisce sempre le stringhe con tutti i campi
   nell'ordine corretto, quindi oggi funziona, ma **non c'è margine per modifiche
   incrementali al protocollo senza aggiornare entrambi i lati in modo coordinato**.

7. **`platformio.ini` ha `monitor_speed = 115200`** ma il firmware apre
   `Serial.begin(460800)`. Chi usa `pio device monitor` per debug vedrà solo rumore
   a meno di forzare manualmente `-b 460800`; non影响 il funzionamento con l'app
   Python (che apre la porta a 460800 esplicitamente in `communication.py`), ma è
   una trappola comune per chi tocca il firmware.

8. **`GET_SCALE`/`SCALE:` è codice morto lato protocollo**: il firmware lo implementa
   ma nessun widget Python lo invia mai — la GUI non legge mai il fattore di scala
   corrente dal firmware, si fida solo del valore che ha impostato lei stessa
   (`SET_SCALE`) o del file di calibrazione caricato da disco.

9. **Nessun rilevamento di saturazione del guadagno PGA del NAU7802.** Verificato
   (leggendo l'API pubblica della libreria SparkFun Qwiic Scale NAU7802) che non
   esiste alcun flag/metodo dedicato per rilevare quando l'ADC satura con un
   guadagno troppo alto per il segnale in ingresso — solo un'idea di euristica
   non implementata (controllare se `getReading()` si avvicina agli estremi
   ±8388607 del range a 24 bit con segno). Con un guadagno alto (default 128x) e
   un carico vicino al fondoscala della cella, una lettura saturata non verrebbe
   segnalata come tale: si tradurrebbe in un valore di forza filtrato
   silenziosamente scorretto (troncato), potenzialmente sotto-stimando il carico
   reale proprio vicino al limite di sicurezza. Rischio noto, non mitigato.

10. ✅ **[RISOLTO 2026-07-14]** Il pulsante STOP principale di
    `MonotonicTestWidget`/`CyclicTestWidget` non riusciva a interrompere un
    movimento "Go To" (segnalato dall'utente: "sembra disattivato"). La sua
    abilitazione in `update_ui_for_test_state()` dipendeva solo da
    `is_test_running`, che un Go To non imposta mai di proposito (non è un
    test); anche abilitandolo, `on_stop_test()` usciva comunque subito per
    lo stesso motivo. L'unico modo per fermare un Go To era ricliccare il
    pulsante Go To stesso (diventato "STOP" durante il movimento by design)
    — funzionalmente corretto ma sorprendente, con un pulsante STOP grande
    e rosso già in vista che sembrava non fare nulla. Risolto abilitando
    `stop_button` anche con `is_goto_active`, e facendo controllare a
    `on_stop_test()` prima questo stato (chiamando la nuova `_cancel_goto()`
    condivisa) prima di valutare se c'è anche un test da fermare. Vedi
    `CHANGELOG.md` e `docs/monotonic_test_widget.md`/`docs/cyclic_test_widget.md`.

11. **`pio run` compila ma NON carica il firmware sulla scheda** — serve
    esplicitamente `pio run --target upload` (o `pio run -t upload`).
    Trappola in cui è facile cadere quando si modifica il firmware in una
    sessione e si verifica solo con `pio run` (che riporta "SUCCESS" a
    compilazione riuscita, senza alcun avviso che la scheda fisica non è
    stata toccata): il codice sorgente e il comportamento realmente in
    esecuzione sull'ESP32 possono divergere silenziosamente. Causa
    accertata di un'intera sessione di debug (2026-07-21, vedi
    `CHANGELOG.md`, voce "Diagnosi: software non funziona..."): il
    killswitch era stato integrato e compilato con successo, ma mai
    caricato, e la scheda continuava a eseguire un firmware precedente
    apparentemente non funzionante (causa non accertata con precisione).
    Verificare sempre, dopo una modifica firmware che deve essere provata
    sulla macchina reale, che sia stato effettivamente eseguito l'upload
    (l'output di `pio run -t upload` mostra esplicitamente righe come
    `Uploading...` e `Hard resetting via RTS pin...`), non solo la
    compilazione.

## Inconsistenze note tra Python e firmware (riepilogo)

| Aspetto | Python si aspetta | Firmware fa | Stato |
|---|---|---|---|
| Baud rate | 460800 | 460800 | ✅ coerente (ma duplicato, nessuna negoziazione) |
| Formato `D:` | fino a 6 campi, tollera 3/4/5 (storico) | sempre 6 campi | ✅ ma retrocompatibilità Python inutile |
| Limiti sicurezza | GUI mostra 10N/190mm come "attivi" di default | Firmware disabilitato di default, ora ricevuto automaticamente alla connessione (con ritardo di boot) e dopo calibrazione | ✅ **risolto**, vedi punto 3 |
| Force stop criterion (monotonico) | invia `START_TEST` dopo un controllo di sicurezza sul limite | il controllo funziona correttamente, nessun crash | ✅ **risolto**, vedi punto 1 |
| Salvataggio calibrazione su file | utente si aspetta che "Save Calibration" scriva un file | scrive un JSON con il fattore di scala noto, disabilitato se non ancora noto | ✅ **risolto**, vedi punto 2 |
| `GET_SCALE` | mai chiamato | implementato e funzionante | codice morto lato protocollo |
| Costanti meccaniche (`PULSES_PER_REV`, `GEAR_RATIO`, `SCREW_PITCH_MM`) | copia locale in `main.py` | copia locale in `main.cpp` | coerenti oggi, nessun single source of truth |
| Filtro cella di carico | si aspetta filtro EMA configurabile (alpha/rate/gain), non contatori anti-spike | EMA centralizzato, sostituisce i tre vecchi contatori anti-spike HX711 | ✅ **migrato**, vedi `CHANGELOG.md` (migrazione NAU7802) |
| Guadagno PGA e calibrazione | si aspetta che un cambio gain invalidi automaticamente offset/scala | firmware invalida esplicitamente e notifica `STATUS:CALIBRATION_INVALIDATED` su cambio gain reale; GUI reagisce al messaggio (copre anche il reinvio automatico alla riconnessione) | ✅ **risolto**, vedi `CHANGELOG.md` (feature guadagno PGA) |
| Saturazione del guadagno PGA | — | nessun rilevamento disponibile in libreria | ⚠️ **rischio noto, non mitigato**, vedi punto 9 |

## Manutenzione della documentazione

Dopo ogni modifica sostanziale al codice (nuova feature, fix di bug
rilevante, refactoring), aggiorna:
- il file in `docs/` relativo al modulo toccato, se la modifica ne cambia
  il comportamento o le responsabilità
- `CHANGELOG.md`, con una voce concettuale (non tecnica riga-per-riga)
  che descriva cosa è cambiato e perché

Fallo proattivamente senza aspettare che l'utente lo richieda
esplicitamente, a meno che la modifica sia puramente cosmetica/minore.
