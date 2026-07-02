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
    (`PULSES_PER_REV`, `GEAR_RATIO`, `SCREW_PITCH_MM`, `PULSES_TO_MM`), i
    limiti di sicurezza lato GUI (`current_force_limit_N` = 100 N di default,
    `current_disp_limit_mm` = 190 mm), il thread `SerialCommunicator`, tutti i
    widget e tutte le connessioni segnale/slot. Avvia anche il `QTimer` di
    polling a 100 ms (`data_request_timer`, non ancora avviato qui).
  - `on_connected()` / `on_disconnected()`: gestiscono lo stato dei pulsanti di
    connessione e avviano/fermano `data_request_timer`. `on_connected()` invia
    anche `SET_MODE:POLLING` e, tramite `send_limits_to_firmware()`, i limiti
    di sicurezza correnti.
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
      thread-safe `limit_hit_signal`), homing.
    - Per `D:`: fa parsing flessibile a 3/4/5 campi (solo 5 usato dal firmware
      attuale), converte grammi→N e passi→mm, aggiorna le variabili assolute
      (`absolute_load_N`, `absolute_displacement_mm`, `current_resistance_ohm`)
      su tutti i widget che le espongono, poi chiama `handle_stream_data()` e
      `update_displays()` sul widget attualmente visibile.
  - `update_calibration_status(status_text, cell_name)`: propaga lo stato di
    calibrazione ai widget e, se `cell_name` è parsabile come "<numero>N",
    aggiorna `current_force_limit_N` e chiama `send_limits_to_firmware()`.
  - `send_limits_to_firmware()`: costruisce e invia `SET_LIMITS:FORCE_G=..;
    DISP_MM=..` usando i valori correnti di `current_force_limit_N` /
    `current_disp_limit_mm`. Punto unico di invio di questo comando, usato da
    `on_connected()`, `update_calibration_status()` e `show_limits_dialog()`.
  - `show_limits_dialog()`: apre `LimitsDialog`, e se l'utente conferma
    aggiorna i limiti locali e chiama `send_limits_to_firmware()`.
  - `show_limit_hit_popup(status_message)`: mostrato in risposta a
    `limit_hit_signal`, con una bandierina anti-rientranza
    (`is_critical_popup_active`) per evitare popup multipli.

## Dipendenze

- Importa e istanzia direttamente: `MainMenuWidget`, `ManualControlWidget`,
  `CalibrationWidget`, `MonotonicTestWidget`, `CyclicTestWidget`,
  `SerialCommunicator`, `SettingsManager`, `LimitsDialog` (da
  `custom_widgets.py`).
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