# UTM-ESP32 — Macchina di Trazione Universale

Questo workspace contiene due progetti collegati che insieme pilotano una macchina da
trazione/compressione da laboratorio:

- **UTM-ESP32** (questa cartella) — applicazione desktop Python/PyQt6, gira sul PC e
  comunica via seriale USB con l'ESP32.
- **Controllo-Macchina-ESP32** (`c:\Users\carlo\Documents\PlatformIO\Projects\Controllo-Macchina-ESP32`,
  cartella secondaria del workspace) — firmware C++ (PlatformIO/Arduino) che gira
  sull'ESP32, un unico file `src/main.cpp` (~1220 righe). Pilota un motore passo-passo
  (vite senza fine), legge una cella di carico via HX711 e opzionalmente un LCR-meter
  esterno via UART2.

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
    nulla della sequenza intera: la logica multi-blocco vive tutta qui.
  - Timer a 100 ms che invia `GET_DATA` in polling (usato solo quando non si è in
    streaming, cioè fuori da un test).
  - `current_force_limit_N` / `current_disp_limit_mm`: stato lato GUI dei limiti di
    sicurezza assoluti, modificabile da `show_limits_dialog()` (vedi **Punti critici**
    più sotto: questi valori NON sono automaticamente sincronizzati col firmware).

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

- **`calibration_widget.py`** — `CalibrationWidget`: wizard a stati
  (`IDLE → WAITING_FOR_ZERO → WAITING_FOR_WEIGHT → IDLE`) che pilota `TARE` e
  `CALIBRATE:<grammi>` sul firmware. Gestisce anche salvataggio/caricamento di un
  fattore di calibrazione su file JSON esterno (diverso da `settings.json`).

- **`monotonic_test_widget.py`** — `MonotonicTestWidget`: gestione di un batch di
  "provini" (specimen) con parametri (gauge length, area, velocità, criterio di
  stop), avvio di un singolo test monotonico (`START_TEST:...`), grafico
  carico/spostamento con conversione opzionale in stress/strain, overlay di test
  precedenti, autosave in Excel a fine test.

- **`cyclic_test_widget.py`** (file più grande, ~1800 righe) — `CyclicTestWidget`:
  editor di una **sequenza di blocchi** (blocco ciclico, pausa, rampa — dialog
  dedicati `BlockDialog`/`PauseDialog`/`RampDialog`), gestione batch provini simile
  al monotonico. Avvia solo il **primo** blocco della sequenza; i blocchi successivi
  sono pilotati da `main.py` in risposta a `STATUS:BLOCK_COMPLETED` (vedi sopra).

- **`data_saver.py`** — `DataSaver`: esporta i dati di test (monotonici o ciclici) in
  `.xlsx` con `openpyxl`, un foglio per provino, grafici Scatter incorporati
  (Load-Displacement / Stress-Strain per monotonici, Time-Displacement /
  Time-Load per ciclici).

- **`settings_manager.py`** — persistenza JSON (`settings.json`) dei soli carichi di
  calibrazione (`cal_loads`) per cella. Nota: la "calibrazione attiva" (fattore di
  scala) è invece salvata/caricata come file JSON separato scelto dall'utente tramite
  `CalibrationWidget`, non tramite `SettingsManager`.

- **`custom_widgets.py`** — widget riusabili: `DisplayWidget` (etichetta + valore),
  `SpeedBarWidget` (barra colorata verde→giallo→rosso), `LimitsDialog` (form per
  forza/spostamento massimi assoluti).

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
| `RETURN_TO_START` | — | Torna alla posizione registrata a inizio test |
| `START_TEST:SPEED_MMS=..;CRITERION=DISP\|FORCE;STOP_VAL=..` | | Avvia test monotonico |
| `START_CYCLIC_TEST:MODE=DISP\|FORCE;UPPER=..;LOWER=..;SPEED=..;HOLD_U=..;HOLD_L=..;CYCLES=..` | | Avvia blocco ciclico |
| `EXECUTE_PAUSE:<ms>` | | Blocco pausa nella sequenza ciclica |
| `EXECUTE_RAMP:MODE=DISP\|FORCE;TARGET=..;SPEED=..;HOLD=..` | | Blocco rampa (vai-a-target) |
| `JOG_UP` / `JOG_DOWN` | — | Solo se `motor_state == STOPPED` e non jog hardware attivo |
| `HOME` | — | Avvia sequenza di homing a stati (fast→backoff→slow→final lift) |
| `SET_SPEED:<mm/s>` | — | Stessa condizione di JOG_* |
| `ENABLE_LCR_POLLING` / `DISABLE_LCR_POLLING` | — | Attiva/disattiva interrogazione LCR-meter su Serial2 |

Tutti i comandi con parametri usano il formato `CHIAVE=valore;CHIAVE=valore` fatto a
mano con `indexOf`/`substring` lato firmware (parsing fragile, vedi sotto).

**Nota unità**: la GUI lavora sempre in **N** e **mm** (con offset relativi), ma
verso il firmware converte sempre in **grammi** (`(N/9.81)*1000`) e **mm assoluti**
(sommando l'offset corrente). Il firmware converte i mm in passi motore internamente
(`PULSES_TO_MM`) e lavora internamente in grammi per la forza. `PULSES_PER_REV=2000`,
`GEAR_RATIO=10`, `SCREW_PITCH_MM=5.0873` sono **duplicati indipendentemente** sia in
`main.py` (`MainWindow.__init__`) sia in `main.cpp`: oggi coincidono, ma non c'è
alcun meccanismo che li tenga sincronizzati se uno dei due cambia.

### Messaggi ESP32 → PC

- **`D:<load_g>;<pulse_count>;<elapsed_ms>;<cycle>;<resistance_ohm>`** — pacchetto
  dati, sempre a **5 campi**, sia in risposta a `GET_DATA` (con `time`/`cycle`
  fittizi a `"0"`) sia in streaming. `main.py` sa ancora parsare varianti storiche a
  3 o 4 campi (retrocompatibilità morta: il firmware attuale non le invia più).
- **`STATUS:<messaggio>`** — testo libero, interpretato in `main.py` per substring
  matching (es. `"TOP_HIT" in status_message`), non per uguaglianza esatta. Esempi:
  `TEST_STARTED`, `TEST_COMPLETED`, `TEST_STOPPED_BY_USER`, `CYCLIC_TEST_STARTED`,
  `CYCLIC_PREPOSITIONING`, `BLOCK_COMPLETED`, `CYCLIC_TEST_STOPPED_BY_USER`,
  `RAMP_STARTED`, `RAMP_HOLDING_STARTED`, `PAUSE_STARTED`, `TOP_HIT`, `BOTTOM_HIT`,
  `LIMIT_HIT_DISPLACEMENT`, `LIMIT_HIT_FORCE`, `HOMING_BACKOFF`, `HOMING_SLOW`,
  `HOMING_LIFTING`, `HOMING_COMPLETED`, `HOMED`, `TARE_DONE;OFFSET=..`,
  `CALIBRATION_DONE;SCALE=..`, `LIMITS_SET;MAX_FORCE_G=..;MAX_PULSES=..`,
  `MOVE_COMPLETED`, `RETURN_COMPLETED`, `RETURNING`, `MODE_SET`, `TIMER_RESET`,
  `SCALE_SET`, `LCR_POLLING_ENABLED`/`DISABLED`, `STOPPED_BY_USER`.
- **`SCALE:<valore>`** — risposta a `GET_SCALE`, mai richiesta da Python.
- Righe di **debug non prefissate** (es. `"DEBUG: startMotor() chiamato"`,
  `"[DEBUG CMD] Calculated ramp_target_steps: ..."`, `"pulse_count: .."`) —
  ignorate silenziosamente da `main.py` perché non iniziano né per `D:` né per
  `STATUS:`, ma vedi punto critico sotto.

## Punti critici / fragili (confermati leggendo il codice, non modificati)

1. **`MonotonicTestWidget.on_start_test()` va quasi certamente in crash su stop
   criterion a Forza.** In [monotonic_test_widget.py:339](monotonic_test_widget.py#L339)
   il codice fa `if target_force_abs_N > self.current_force_limit_N:` ma
   `MonotonicTestWidget` **non definisce mai `self.current_force_limit_N`** — esiste
   solo come `self.main_window.current_force_limit_N` (usato correttamente altrove,
   es. righe 342, 601, 610, 730, 737). Qualunque avvio di test monotonico con
   `Stop Criterion = Force (N)` o `Stress (MPa)` solleva `AttributeError` a runtime.
   `CyclicTestWidget` non ha questo problema: usa sempre `self.main_window.current_force_limit_N`.

2. **Il pulsante "Save Calibration" non salva nulla.**
   `CalibrationWidget.save_calibration()` ([calibration_widget.py:157-164](calibration_widget.py#L157-L164))
   apre un file dialog e poi fa solo `self.save_calibration_requested.emit(filePath)`.
   Quel segnale è definito ma **non è collegato a nessuno slot** in `main.py` (sono
   collegati solo `calibration_updated` e `settings_changed`). Risultato: l'utente
   sceglie un percorso, vede l'interazione completarsi, ma nessun file JSON viene
   scritto — probabilmente una feature rimasta a metà durante un refactor.

3. **I limiti di sicurezza assoluti del firmware non sono mai sincronizzati
   automaticamente.** `absolute_max_force_grams`/`absolute_max_pulse_count` in
   `main.cpp` partono a valori enormi (`9999999`/`99999999`, cioè di fatto
   disabilitati) e vengono aggiornati **solo** quando arriva un comando
   `SET_LIMITS:...`, che parte **solo** da `MainWindow.show_limits_dialog()` quando
   l'utente apre manualmente la finestra "LIMITS" e clicca Save
   ([main.py:443-472](main.py#L443-L472)). `on_connected()` ([main.py:126-131](main.py#L126-L131))
   non invia mai `SET_LIMITS` alla connessione, e `update_calibration_status()`
   ([main.py:371-381](main.py#L371-L381)) aggiorna `self.current_force_limit_N` lato
   GUI in base al nome della cella calibrata (es. "10N" → 10.0) ma **non lo propaga
   al firmware**. Conseguenza pratica: dopo un power-cycle dell'ESP32 (o una nuova
   connessione), la macchina **non ha limiti di forza/spostamento realmente attivi**
   finché l'operatore non apre esplicitamente "LIMITS" e salva — anche se la GUI
   mostra già un valore di default (100 N / 190 mm) che sembra "impostato".

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

## Inconsistenze note tra Python e firmware (riepilogo)

| Aspetto | Python si aspetta | Firmware fa | Stato |
|---|---|---|---|
| Baud rate | 460800 | 460800 | ✅ coerente (ma duplicato, nessuna negoziazione) |
| Formato `D:` | fino a 5 campi, tollera 3/4 (storico) | sempre 5 campi | ✅ ma retrocompatibilità Python inutile |
| Limiti sicurezza | GUI mostra 100N/190mm come "attivi" di default | Firmware disabilitato di default, attivato solo da `SET_LIMITS` esplicito | ⚠️ **gap di sicurezza**, vedi punto 3 |
| Force stop criterion (monotonico) | invia `START_TEST` dopo un controllo di sicurezza sul limite | il controllo stesso crasha prima di inviare nulla | ⚠️ **bug bloccante**, vedi punto 1 |
| Salvataggio calibrazione su file | utente si aspetta che "Save Calibration" scriva un file | segnale emesso ma non collegato | ⚠️ **funzione silenziosamente rotta**, vedi punto 2 |
| `GET_SCALE` | mai chiamato | implementato e funzionante | codice morto lato protocollo |
| Costanti meccaniche (`PULSES_PER_REV`, `GEAR_RATIO`, `SCREW_PITCH_MM`) | copia locale in `main.py` | copia locale in `main.cpp` | coerenti oggi, nessun single source of truth |

## Manutenzione della documentazione

Dopo ogni modifica sostanziale al codice (nuova feature, fix di bug
rilevante, refactoring), aggiorna:
- il file in `docs/` relativo al modulo toccato, se la modifica ne cambia
  il comportamento o le responsabilità
- `CHANGELOG.md`, con una voce concettuale (non tecnica riga-per-riga)
  che descriva cosa è cambiato e perché

Fallo proattivamente senza aspettare che l'utente lo richieda
esplicitamente, a meno che la modifica sia puramente cosmetica/minore.
