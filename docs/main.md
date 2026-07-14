# main.py

## Scopo

Punto di ingresso dell'applicazione e orchestratore centrale della GUI. Crea la
finestra principale (`MainWindow`), il thread di comunicazione seriale, tutti i
widget delle schermate (menu, controllo manuale, calibrazione, test monotonico,
test ciclico) e li collega tra loro tramite segnali/slot Qt. È anche l'unico
punto in cui arrivano e vengono smistati tutti i messaggi provenienti
dall'ESP32.

## Classi e funzioni principali

- **`MainWindow(QMainWindow)`** — finestra principale, unica classe del file.
  - `__init__()`: crea `SettingsManager`, costanti meccaniche
    (`PULSES_PER_REV`, `GEAR_RATIO`, `SCREW_PITCH_MM`, `PULSES_TO_MM`,
    `ENCODER_COUNTS_PER_REV` = 4800.0 per l'encoder esterno, che riusa
    `SCREW_PITCH_MM` senza bisogno di `GEAR_RATIO`), i
    limiti di sicurezza lato GUI (`current_force_limit_N` = 10 N di default,
    `current_disp_limit_mm` = 190 mm), la configurazione del filtro cella di
    carico (`current_filter_alpha` / `current_filter_rate_sps` /
    `current_filter_pga_gain`, caricati da `self.settings['filter_config']`),
    il thread `SerialCommunicator`, tutti i
    widget e tutte le connessioni segnale/slot. Avvia anche il `QTimer` di
    polling a 100 ms (`data_request_timer`, non ancora avviato qui).
  - `on_connected()` / `on_disconnected()`: gestiscono lo stato dei pulsanti di
    connessione e avviano/fermano `data_request_timer`. `on_connected()` non
    invia più comandi seriali immediatamente: pianifica
    `_send_post_connect_commands()` con `QTimer.singleShot(2000, ...)`, per dare
    tempo al firmware di completare il boot nel caso l'apertura della porta
    abbia causato un reset hardware dell'ESP32 (comune sulle schede con
    USB-seriale CH340/CP210x). `_send_post_connect_commands()` invia
    `SET_MODE:POLLING`, i limiti di sicurezza correnti
    (`send_limits_to_firmware()`) e la configurazione del filtro
    (`send_filter_config_to_firmware()`).
  - `handle_data_from_esp32(data)`: **cuore del dispatch**. Distingue righe
    `STATUS:` da righe `D:`.
    - Per `STATUS:`: interpreta per substring matching (`in`, non `==`) e
      aggiorna stato applicativo — in particolare gestisce l'intera macchina a
      stati della **sequenza a blocchi del test ciclico**: alla ricezione di
      `BLOCK_COMPLETED` incrementa `cyclic_test.current_block_index`, legge il
      blocco successivo da `cyclic_test.test_sequence` e costruisce/invia il
      comando firmware appropriato (`START_CYCLIC_TEST`, `EXECUTE_PAUSE`,
      `EXECUTE_RAMP`). Se non ci sono altri blocchi, invia
      `SET_MODE:POLLING` e chiude il test. Gestisce anche fine test
      monotonico/ciclico, colpi di endstop, `LIMIT_HIT` (via segnale
      thread-safe `limit_hit_signal`), homing. Dopo l'intera catena
      `if/elif`, un controllo aggiuntivo **non esclusivo** (un `if`
      indipendente, non un altro `elif`) chiama
      `clear_goto_busy_state()` su `monotonic_test_widget` e `cyclic_test`
      per qualunque messaggio che contenga `MOVE_COMPLETED`,
      `STOPPED_BY_USER`, `TOP_HIT`, `BOTTOM_HIT` o `LIMIT_HIT`: serve a
      chiudere lo stato "Go To in corso" su quei widget quando il motore si
      ferma per una ragione diversa dal click dell'utente sullo stesso
      pulsante Go To (che si ripristina già da solo, otticamente, appena
      cliccato — vedi `docs/monotonic_test_widget.md`).
    - Per `D:`: fa parsing flessibile a 3/4/5/6 campi (solo 6 usato dal
      firmware attuale), converte grammi→N e passi→mm, aggiorna le variabili
      assolute (`absolute_load_N`, `absolute_displacement_mm`,
      `current_resistance_ohm`) su tutti i widget che le espongono, poi
      chiama `handle_stream_data()` e `update_displays()` sul widget
      attualmente visibile. Il 6° campo (opzionale, `None` se assente o non
      parsabile) è il conteggio grezzo dell'encoder incrementale esterno:
      viene convertito in `encoder_displacement_mm` con
      `(encoder_count / ENCODER_COUNTS_PER_REV) * SCREW_PITCH_MM` e
      propagato come `absolute_encoder_displacement_mm` sui widget, e passato
      come argomento aggiuntivo a `handle_stream_data()`. **Canale di sola
      lettura (Livello 1)**: non entra in nessuna validazione di sicurezza né
      logica di stop, serve solo per confronto/logging (vedi `CHANGELOG.md`).
    - `"CALIBRATION_INVALIDATED" in status_message`: resetta
      `active_calibration_info` a "Not Calibrated" (propagato a
      `manual_control`/`monotonic_test_widget`), chiama
      `calibration_widget.invalidate_calibration()` (azzera il fattore di
      scala noto lì, vedi `docs/calibration_widget.md`) e mostra un popup di
      avviso. Emesso dal firmware quando un `SET_FILTER_CONFIG` cambia
      realmente il guadagno PGA — copre sia il cambio esplicito dal dialog
      "Filter Config" sia il reinvio automatico alla riconnessione, se il
      gain salvato in `settings.json` differisce da quello con cui il
      firmware è ripartito.
    - `"CALIBRATION_DONE" in status_message`: estrae `SCALE=<valore>` e lo
      passa a `calibration_widget.set_calibration_factor()` — è il solo modo
      in cui la GUI viene a conoscenza del fattore di scala reale calcolato
      dal firmware dopo `CALIBRATE:<grammi>`, necessario perché "Save
      Calibration" abbia qualcosa da scrivere (vedi `CHANGELOG.md`).
  - `update_calibration_status(status_text, cell_name)`: propaga lo stato di
    calibrazione ai widget e, se `cell_name` è parsabile come "<numero>N",
    aggiorna `current_force_limit_N` e chiama `send_limits_to_firmware()`.
  - `send_limits_to_firmware()`: costruisce e invia `SET_LIMITS:FORCE_G=..;
    DISP_MM=..` usando i valori correnti di `current_force_limit_N` /
    `current_disp_limit_mm`. Punto unico di invio di questo comando, usato da
    `on_connected()`, `update_calibration_status()` e `show_limits_dialog()`.
  - `show_limits_dialog()`: apre `LimitsDialog`, e se l'utente conferma
    aggiorna i limiti locali e chiama `send_limits_to_firmware()`.
  - `send_filter_config_to_firmware()`: costruisce e invia
    `SET_FILTER_CONFIG:ALPHA=..;RATE=..;GAIN=..` usando i valori correnti di
    `current_filter_alpha` / `current_filter_rate_sps` /
    `current_filter_pga_gain`. Stesso pattern di `send_limits_to_firmware()`,
    usato da `_send_post_connect_commands()` e `show_filter_dialog()`.
  - `show_filter_dialog()`: apre `FilterConfigDialog` (da
    `custom_widgets.py`), e se l'utente conferma aggiorna
    `current_filter_alpha`/`current_filter_rate_sps`/
    `current_filter_pga_gain`, li persiste in `self.settings['filter_config']`
    tramite `SettingsManager.save_settings()` e chiama
    `send_filter_config_to_firmware()`. Non mostra un avviso di
    ricalibrazione da sé: quello arriva in modo asincrono tramite
    `STATUS:CALIBRATION_INVALIDATED` se il firmware conferma un cambio gain
    reale (vedi sopra).
  - `show_limit_hit_popup(status_message)`: mostrato in risposta a
    `limit_hit_signal`, con una bandierina anti-rientranza
    (`is_critical_popup_active`) per evitare popup multipli.

## Dipendenze

- Importa e istanzia direttamente: `MainMenuWidget`, `ManualControlWidget`,
  `CalibrationWidget`, `MonotonicTestWidget`, `CyclicTestWidget`,
  `SerialCommunicator`, `SettingsManager`, `LimitsDialog` e
  `FilterConfigDialog` (da `custom_widgets.py`).
- `MonotonicTestWidget` e `CyclicTestWidget` ricevono un riferimento a
  `MainWindow` (`self`) e leggono `main_window.current_force_limit_N` /
  `current_disp_limit_mm` per le validazioni sui limiti — quindi `main.py` è
  una dipendenza diretta di quei due moduli, non solo viceversa.
- Riceve dati solo tramite i segnali di `SerialCommunicator`
  (`data_received`, `connected`, `disconnected`, `port_error`).

## Punti di attenzione

- La logica multi-blocco del test ciclico (avanzamento `test_sequence`,
  costruzione dei comandi `START_CYCLIC_TEST`/`EXECUTE_PAUSE`/`EXECUTE_RAMP`)
  è **duplicata** tra `handle_data_from_esp32()` (per i blocchi successivi al
  primo) e `CyclicTestWidget.on_start_test()` (per il primo blocco). Qualsiasi
  modifica al formato di un blocco o al comando firmware corrispondente va
  applicata in *entrambi* i punti.
- `current_force_limit_N` / `current_disp_limit_mm` sono l'unica fonte di
  verità lato GUI per i limiti di sicurezza. Vengono inviati al firmware solo
  tramite `send_limits_to_firmware()` — se in futuro si aggiungono altri punti
  che modificano questi due attributi, ricordarsi di chiamare anche questo
  metodo, altrimenti si ricrea il gap di sincronizzazione già corretto (vedi
  `CHANGELOG.md`).
- Le costanti meccaniche (`PULSES_PER_REV`, `GEAR_RATIO`, `SCREW_PITCH_MM`)
  sono duplicate manualmente in `Controllo-Macchina-ESP32/src/main.cpp`.
  Cambiarle qui senza cambiarle anche nel firmware disallinea la conversione
  passi↔mm.
- Il parsing di `STATUS:` è per substring (`"TOP_HIT" in status_message`), non
  per uguaglianza esatta: aggiungere in futuro un messaggio di stato il cui
  testo contiene una sottostringa già gestita (es. un nuovo
  `"PRE_TOP_HIT_WARNING"`) causerebbe branch inattesi.
- A differenza di `current_force_limit_N` / `current_disp_limit_mm` (mai
  persistiti su disco, si perdono alla chiusura dell'app — vedi `TODO.md`),
  `current_filter_alpha` / `current_filter_rate_sps` /
  `current_filter_pga_gain` **sono** persistiti in `settings.json` tramite
  `SettingsManager` fin dall'inizio. Chi tocca questa parte del codice deve
  tenere presente questa asimmetria tra le due categorie di impostazioni
  macchina.
- Il popup "Ricalibrazione Necessaria" (in risposta a
  `CALIBRATION_INVALIDATED`) resetta solo `active_calibration_info`, non
  `current_force_limit_N`: un cambio di gain invalida la calibrazione della
  cella ma non ha relazione diretta col limite di sicurezza assoluto
  impostato dall'utente, che resta quello che era.