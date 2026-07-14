# UTM-ESP32 — Macchina di Trazione Universale

Questo workspace contiene due progetti collegati che insieme pilotano una macchina da
trazione/compressione da laboratorio:

- **UTM-ESP32** (questa cartella) — applicazione desktop Python/PyQt6, gira sul PC e
  comunica via seriale USB con l'ESP32.
- **Controllo-Macchina-ESP32** (`c:\Users\carlo\Documents\PlatformIO\Projects\Controllo-Macchina-ESP32`,
  cartella secondaria del workspace) — firmware C++ (PlatformIO/Arduino) che gira
  sull'ESP32, un unico file `src/main.cpp` (~1220 righe). Pilota un motore passo-passo
  (vite senza fine), legge una cella di carico via NAU7802 (ADC I2C, libreria SparkFun
  Qwiic Scale NAU7802) e opzionalmente un LCR-meter esterno via UART2.

Sono due repository git separati e indipendenti.

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
    `LIMIT_HIT`), per chiudere lo stato "Go To in corso" su quei widget.
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
  Displacement" — vedi `CHANGELOG.md`).

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
  `monotonic_test_widget.py` (vedi sopra e `CHANGELOG.md`).

- **`data_saver.py`** — `DataSaver`: esporta i dati di test (monotonici o ciclici) in
  `.xlsx` con `openpyxl`, un foglio per provino, grafici Scatter incorporati
  (Load-Displacement / Stress-Strain per monotonici, Time-Displacement /
  Time-Load per ciclici).

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
  rate e guadagno PGA del filtro cella di carico).

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
| `RETURN_TO_START` | — | Torna alla posizione registrata a inizio test |
| `START_TEST:SPEED_MMS=..;CRITERION=DISP\|FORCE;STOP_VAL=..` | | Avvia test monotonico |
| `START_CYCLIC_TEST:MODE=DISP\|FORCE;UPPER=..;LOWER=..;SPEED=..;HOLD_U=..;HOLD_L=..;CYCLES=..` | | Avvia blocco ciclico |
| `EXECUTE_PAUSE:<ms>` | | Blocco pausa nella sequenza ciclica |
| `EXECUTE_RAMP:MODE=DISP\|FORCE;TARGET=..;SPEED=..;HOLD=..` | | Blocco rampa (vai-a-target) |
| `JOG_UP` / `JOG_DOWN` | — | Solo se `motor_state == STOPPED` e non jog hardware attivo |
| `HOME` | — | Avvia sequenza di homing a stati (fast→backoff→slow→final lift) |
| `SET_SPEED:<mm/s>` | — | Stessa condizione di JOG_* |
| `GOTO:<mm>` | mm assoluti, >= 0 | Movimento verso una posizione assoluta, alla velocità impostata con `SET_SPEED`. Stessa condizione di JOG_* (`motor_state == STOPPED`, non jog hardware attivo). Usa lo stesso meccanismo a passi contati di `RETURN_TO_START` (nessun nuovo `MotorState`): risponde `STATUS:GOTO_STARTED` o, se già alla posizione target, `STATUS:MOVE_COMPLETED` subito. `STOP`/`!` lo interrompono sempre, azzerando esplicitamente `target_steps_remaining` |
| `ENABLE_LCR_POLLING` / `DISABLE_LCR_POLLING` | — | Attiva/disattiva interrogazione LCR-meter su Serial2 |

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

- **`D:<load_g>;<pulse_count>;<elapsed_ms>;<cycle>;<resistance_ohm>;<encoder_count>`**
  — pacchetto dati, sempre a **6 campi**, sia in risposta a `GET_DATA` (con
  `time`/`cycle` fittizi a `"0"`) sia in streaming. `main.py` sa ancora parsare
  varianti storiche a 3, 4 o 5 campi (retrocompatibilità morta: il firmware
  attuale non le invia più). Il 6° campo (`encoder_count`) è il conteggio
  grezzo dell'encoder incrementale esterno Omron E6B2-CWZ6C (quadratura 4x,
  1200 PPR → 4800 conteggi/giro), montato direttamente sulla vite senza fine
  (nessun `GEAR_RATIO` di mezzo). **È un canale di misura aggiuntivo, di sola
  lettura (Livello 1)**: non influenza in alcun modo il comando motore né i
  limiti di sicurezza assoluti, che restano basati su `pulse_count` come
  prima. Conversione lato Python: `mm = (encoder_count / 4800.0) *
  SCREW_PITCH_MM`. Convenzione di segno verificata su hardware: quando la
  traversa sale, sia `pulse_count` sia `encoder_count` aumentano (stesso
  segno, nessuna inversione da compensare). La sequenza di **homing**
  esistente è anche il punto di zero comune per entrambi i canali: allo
  stesso punto in cui azzera `pulse_count` (fine `HOMING_FINAL_LIFT`), il
  firmware azzera ora anche il contatore encoder — nessun comando o
  homing separato per l'encoder. Vedi `CHANGELOG.md` per i
  compromessi emersi durante l'integrazione.
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
  `CALIBRATION_INVALIDATED;REASON=GAIN_CHANGED` (emesso quando un cambio di
  guadagno PGA realmente diverso dal precedente resetta offset/scala).
- **`SCALE:<valore>`** — risposta a `GET_SCALE`, mai richiesta da Python.
- Righe di **debug non prefissate** (es. `"DEBUG: startMotor() chiamato"`,
  `"[DEBUG CMD] Calculated ramp_target_steps: ..."`, `"pulse_count: .."`) —
  ignorate silenziosamente da `main.py` perché non iniziano né per `D:` né per
  `STATUS:`, ma vedi punto critico sotto.

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
   frequenza, sul link dati.** `startMotor()` ([main.cpp:642-647](main.cpp#L642-L647))
   stampa `"DEBUG: startMotor() chiamato"` **ogni volta che viene chiamata** — e in
   `updateMotorState()` viene richiamata ad ogni ciclo di `loop()` quando
   `motor_state == JOG_UP` (o `JOG_DOWN`), cioè potenzialmente migliaia di volte al
   secondo durante un jog manuale. Questo non rompe il parsing Python (le righe non
   iniziano per `D:`/`STATUS:` e vengono scartate), ma occupa banda seriale e CPU
   dell'ESP32 in un percorso temporalmente sensibile (lo stesso loop che gestisce lo
   streaming dati e lo stato del motore). C'è anche un blocco di debug simile,
   parzialmente commentato con `//`, dentro `EXECUTE_RAMP` (righe 499-502, 522-528).

5. **Riuso di variabili di stato ciclico per scopi diversi (hack fragile).**
   Il comando `EXECUTE_PAUSE` ([main.cpp:465-481](main.cpp#L465-L481)) memorizza la
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
