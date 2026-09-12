# manual_control_widget.py

## Scopo

Schermata di controllo manuale della macchina: jog su/giù, homing, azzeramenti
relativi, grafico live carico/tempo a finestra scorrevole, registrazione
manuale dei dati su Excel e selezione della sorgente di resistenza campioni
(LCR-meter esterno o ADS1220, mai attivi insieme). È tipicamente la prima
schermata usata dopo la connessione, perché l'homing eseguito qui sblocca
l'accesso alle schermate di test (`show_monotonic_test`/`show_cyclic_test` in
`main.py` controllano `manual_control.is_homed`).

## Classi e funzioni principali

- **`ManualControlWidget(QWidget)`**
  - Segnali: `back_to_menu_requested`, `limits_button_requested`.
  - Jog: `start_moving_up/down()` (collegati a `pressed` dei pulsanti UP/DOWN)
    inviano `SET_SPEED:<v>` seguito da `JOG_UP`/`JOG_DOWN`; `stop_moving()`
    (collegato a `released`) invia `STOP`.
  - `toggle_homing()`: macchina a stati a 2 fasi pilotata da un solo
    pulsante ("HOMING" ↔ "STOP Homing"); invia `HOME` o `STOP` e aggiorna
    `is_homing_active`. `reset_homing_ui()` ripristina l'interfaccia sia a
    fine homing normale sia se interrotto dall'utente (chiamato anche da
    `MainWindow` in risposta a `STATUS:HOMED`/`HOMING_COMPLETED` o a
    `STOPPED_BY_USER` mentre l'homing è attivo).
  - Grafico live: `handle_stream_data(...)` accumula punti in `deque` a
    lunghezza massima basata su `time_window_seconds * 50` (assume 50 Hz di
    streaming); `_update_plot()`, chiamato da un `QTimer` a ~30 fps
    (`plot_update_timer`, avviato/fermato in `showEvent`/`hideEvent`),
    ridisegna la curva e fa scorrere la finestra temporale. Se il widget non
    è visibile, `handle_stream_data` ignora i dati e resetta
    `plot_start_time` a 0.
  - Registrazione: `on_rec_button_clicked()` accende/spegne `is_recording`;
    mentre attiva, ogni chiamata a `handle_stream_data` accoda una tupla a 8
    elementi (`elapsed_time, rel_disp, rel_load, disp_mm, load_N,
    resistance_ohm, encoder_disp_mm, resistance_source` — gli ultimi due
    sono il canale encoder esterno assoluto e la sorgente resistenza attiva
    in maiuscolo, `"OFF"/"LCR"/"ADS1220"`) in
    `recorded_data`. Allo stop, `_save_recorded_data()` costruisce
    un "provino fittizio" (gauge/area = `NaN`) e lo salva con `DataSaver`,
    riusando l'intera infrastruttura di export pensata per i test.
  - **Canale encoder esterno (sola lettura)**: `encoder_displacement_offset_mm`
    è lo zero relativo dedicato all'encoder, analogo a
    `displacement_offset_mm` per lo spostamento a passi motore.
    `zero_relative_displacement()` azzera **entrambi** con un solo click
    (se `absolute_encoder_displacement_mm` è `None`, cioè nessun pacchetto
    con encoder ancora ricevuto, l'offset encoder resta invariato).
    `update_displays()` mostra il valore risultante in un nuovo
    `DisplayWidget` ("Relative Enc. Displacement (mm)"), accanto a quello
    assoluto già esistente; entrambi mostrano "N/A" finché non arriva un
    pacchetto `D:` a 6 campi.
  - **Resistenza campioni (LCR/ADS1220, canali alternativi)**:
    `resistance_source_combo` (`"Off"/"LCR"/"ADS1220"`, sostituisce il
    vecchio checkbox "Enable LCR Reading") — `_on_resistance_source_changed()`
    invia sempre una coppia `ENABLE_*`/`DISABLE_*` (mai entrambi i canali
    abilitati insieme), azzera i valori grezzi noti
    (`current_resistance_lcr_ohm`/`current_resistance_ads_ohm`) ed emette il
    segnale `resistance_source_changed` (letto da `MainWindow` solo per il
    gating del dialog "ADS1220 Settings", vedi `docs/main.md`).
    `_current_active_resistance_ohm()` sceglie quale dei due valori grezzi
    mostrare/salvare in base al combo corrente; `current_resistance_ohm` è
    quindi il valore della sorgente **attualmente selezionata su questa
    schermata**, non un terzo canale indipendente. `_setup_resistance_axis()`
    crea/distrugge dinamicamente l'asse Y secondario (stessa logica,
    duplicata, presente in `monotonic_test_widget.py` e
    `cyclic_test_widget.py`), gated su `resistance_source_combo.currentText()
    != "Off"` invece che sullo stato del vecchio checkbox.
  - **Jog encoder fisico (a bordo macchina)**: `set_jog_step_size()`
    aggiorna il `DisplayWidget` "Jog Encoder Step (mm)" su
    `STATUS:JOG_STEP_SIZE_SET` — puro display informativo, nessuna azione
    GUI. Pulsanti manuali fisici (Up/Down) e jog encoder sono gestiti
    interamente dal firmware (vedi `CLAUDE.md`, sezione dedicata): questo
    widget non invia né riceve alcun comando per il loro funzionamento di
    base, funzionano anche a GUI chiusa/PC scollegato.

## Dipendenze

- Riceve solo `communicator` (non `main_window`): non legge mai
  direttamente i limiti di sicurezza, dato che il jog manuale è comunque
  vincolato lato firmware dai limiti assoluti e dagli endstop.
- Usa `DisplayWidget`, `SpeedBarWidget` da `custom_widgets.py` e `DataSaver`
  per l'export.
- `is_homed` è impostato dall'esterno da `MainWindow` (in risposta a
  `STATUS:HOMED`); `main.py` legge poi `manual_control.is_homed` per
  decidere se sbloccare le altre schermate.

## Punti di attenzione

- `handle_stream_data()` inizializza `plot_start_time` al primo dato **dopo**
  che il widget diventa visibile: se l'utente passa ripetutamente da questa
  schermata ad un'altra e ritorna durante uno streaming attivo, il grafico si
  resetta ogni volta (comportamento probabilmente voluto, ma da tenere a
  mente se si vuole in futuro mantenere la storia del grafico tra i cambi di
  schermata).
- La logica di creazione/distruzione dell'asse secondario per la resistenza
  (`_setup_resistance_axis` / `_update_resistance_views`) è sostanzialmente
  duplicata in tutti e tre i widget con grafico (`manual_control_widget.py`,
  `monotonic_test_widget.py`, `cyclic_test_widget.py`), ciascuno con piccole
  varianti nella gestione degli errori: un fix a un bug di uno di questi tre
  blocchi va valutato anche negli altri due.
- Il numero di punti del buffer (`deque(maxlen=...)`) è calcolato assumendo
  **50 Hz fissi** di streaming (`time_window_seconds * 50`); il firmware
  invia effettivamente a 50 Hz (`STREAM_INTERVAL_MS = 20`), ma se quella
  costante cambiasse lato firmware questo calcolo andrebbe aggiornato
  manualmente qui per mantenere la finestra temporale accurata.
- `_save_recorded_data()` passa `gauge_length`/`area` come `np.nan`: questo è
  compatibile con `DataSaver` (che gestisce `NaN` per Strain/Stress), ma
  qualunque nuovo consumatore di `DataSaver` che assuma valori numerici
  validi per questi campi andrebbe verificato contro questo caso d'uso.