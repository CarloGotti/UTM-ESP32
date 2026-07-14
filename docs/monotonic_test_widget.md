# monotonic_test_widget.py

## Scopo

Schermata per test monotonici (trazione/compressione a singola rampa fino a
uno stop criterion): gestisce un batch di "provini" (specimen) con i loro
parametri, avvia/ferma il test sul firmware, mostra il grafico
carico/spostamento (con conversione opzionale in stress/strain) in tempo
reale e in overlay con test precedenti, e fa autosave in Excel a fine test.

## Classi e funzioni principali

- **`MonotonicTestWidget(QWidget)`**
  - Segnali: `back_to_menu_requested`, `limits_button_requested`.
  - Stato interno principale: `specimens` (dict nome→dati provino),
    `current_specimen_name`, `is_test_running`, `absolute_load_N` /
    `load_offset_N`, `absolute_displacement_mm` / `displacement_offset_mm`,
    `current_test_data` (lista di tuple a 7 elementi: `time_s, rel_disp,
    rel_load, abs_disp, abs_load, resistance_ohm, encoder_disp_mm` — l'ultimo
    è l'encoder **assoluto**, non relativo), `current_resistance_ohm`,
    `absolute_encoder_displacement_mm` (canale dell'encoder incrementale
    esterno, sola lettura — `None` se il pacchetto `D:` non lo include, per
    retrocompatibilità), `encoder_displacement_offset_mm` (zero relativo
    dedicato all'encoder, azzerato insieme a `displacement_offset_mm` da
    `zero_relative_displacement()`; usato per calcolare al volo lo
    spostamento encoder relativo sia nel `DisplayWidget` dedicato sia nel
    grafico, non è mai salvato nella tupla), `is_goto_active` (True mentre
    un movimento "Go To" è in corso).
  - **"Go To" (posizione assoluta)**: `goto_position_spinbox` (mm, range
    `[0, 190]`, coerente con il limite fisico macchina già usato altrove) +
    `goto_button`, accanto ai controlli Up/Down/Jog Speed esistenti.
    `toggle_goto()` funge da singolo pulsante a due stati (stesso pattern di
    `toggle_homing()` in `manual_control_widget.py`): il primo click invia
    `SET_SPEED:<jog_speed>` seguito da `GOTO:<target_mm>` (comando nuovo lato
    firmware, vedi `CLAUDE.md`/`docs/firmware_main.md`), imposta
    `is_goto_active = True` e cambia il testo del pulsante in "STOP"; un
    secondo click invia `STOP`+`!` e ripristina subito lo stato (senza
    attendere conferma dal firmware, come l'homing) tramite `_cancel_goto()`.
    `clear_goto_busy_state()` fa lo stesso ripristino (senza inviare nulla)
    quando è `MainWindow` a segnalare che il motore si è comunque fermato
    **senza** che l'utente abbia agito su nessuno dei due pulsanti (fine
    movimento naturale, endstop, limite di sicurezza — vedi `docs/main.md`).
    `update_ui_for_test_state()` disabilita anche Up/Down e lo spinbox (non
    il pulsante Go To stesso, che deve restare cliccabile per fungere da
    STOP) mentre `is_goto_active` è vero, oltre che durante un test.
  - **Il pulsante STOP principale (`self.stop_button`) interrompe anche un
    Go To**, non solo un test: era il bug segnalato dall'utente (il
    pulsante restava disabilitato — `setEnabled(is_running)` non teneva
    conto di `is_goto_active` — e comunque `on_stop_test()` usciva subito se
    nessun test era in corso). Ora `update_ui_for_test_state()` abilita
    `stop_button` anche con `is_goto_active`, e `on_stop_test()` chiama
    `_cancel_goto()` come primo passo se un Go To è attivo, prima di
    valutare se c'è anche un test da fermare (vedi `CHANGELOG.md`).
    `start_button` è invece disabilitato anche durante un Go To, per
    evitare di avviare un test mentre il motore sta ancora eseguendo un
    movimento a passi contati verso una posizione assoluta.
  - `on_start_test()`: legge i parametri del provino selezionato
    (velocità già convertita in mm/s, stop criterion già convertito in
    unità base mm o N), calcola il target assoluto tenendo conto degli
    offset relativi, e se lo stop criterion è in forza **controlla il
    limite di sicurezza** (`self.main_window.current_force_limit_N`, dopo il
    fix del bug che referenziava un attributo inesistente su `self`, vedi
    `CHANGELOG.md`) prima di chiedere conferma ed inviare
    `START_TEST:SPEED_MMS=..;CRITERION=DISP|FORCE;STOP_VAL=..` seguito da
    `SET_MODE:STREAMING`.
  - `on_stop_test(user_initiated)`: se avviato dall'utente, invia
    `send_emergency_stop()` + `STOP` + `SET_MODE:POLLING` e ritorna subito
    (l'aggiornamento reale dello stato avviene solo quando `MainWindow`
    richiama questo stesso metodo con `user_initiated=False` in risposta al
    messaggio `STATUS:` del firmware). In quel percorso: salva
    `current_test_data` nel provino, fa autosave automatico in
    `AUTOSAVE_<nome>_<timestamp>.xlsx` tramite `DataSaver`, e se il provino
    ha `return_to_start=True` invia `RETURN_TO_START`.
  - `handle_stream_data(load_N, disp_mm, time_s, cycle_count, resistance_ohm,
    encoder_disp_mm=None)`: chiamato da `MainWindow` per ogni pacchetto `D:`
    mentre il widget è quello corrente; aggiorna i valori assoluti, accoda un
    punto dati, e aggiorna la curva live (con conversione opzionale
    Strain/Stress in base ai combo box degli assi). `encoder_disp_mm` non
    entra mai in nessuna validazione di sicurezza; entra invece nel grafico
    come sorgente X alternativa (vedi sotto), oltre che nel
    `DisplayWidget` "Relative Enc. Displacement (mm)".
  - **Sorgente X del grafico (Motor/Encoder)**: due checkbox
    (`x_source_motor_checkbox`, `x_source_encoder_checkbox`, visibili solo
    quando `x_axis_combo` è su "Relative Displacement (mm)", gestite da
    `_update_x_source_controls_visibility()`) permettono di scegliere se
    l'asse X del grafico usi lo spostamento stimato a passi motore
    (`rel_disp`, invariato), l'encoder esterno reso relativo al volo con
    `encoder_displacement_offset_mm`, o **entrambi** selezionati insieme
    (overlay in tempo reale di due curve per lo stesso provino, stile
    tratteggiato per la curva encoder). `_active_x_sources()` centralizza
    questa scelta (sempre `["motor"]` fuori dalla modalità Relative
    Displacement); `_on_x_source_changed()` impedisce di deselezionare
    entrambe le sorgenti insieme. `self.plot_curves` è quindi indicizzato da
    tuple `(nome_provino, sorgente)`, non più dal solo nome, in tutti i punti
    che lo popolano (`on_start_test`, `handle_stream_data`, `refresh_plot`).
  - `on_new_specimen()` / `on_modify_specimen()`: creano/aggiornano una voce
    in `specimens`, convertendo velocità e stop criterion nelle unità base e
    **validando che il target assoluto non superi i limiti macchina**
    (`main_window.current_disp_limit_mm` / `current_force_limit_N`) prima di
    permettere il salvataggio del provino.
  - `refresh_plot()`: ridisegna il grafico principale (una curva per ogni
    sorgente X attiva, per provino se in overlay) e, se abilitato, l'asse
    secondario per la resistenza LCR (crea/distrugge dinamicamente una
    `pg.ViewBox` secondaria agganciata all'asse destro; la curva resistenza
    resta ancorata alla sorgente X "motor" quando entrambe sono attive).
  - `convert_speed()` / `convert_stop_criterion()`: conversioni pure
    mm/s↔%/s↔%/min e mm|N↔Strain(%)|Stress(MPa), usando `gauge_length` e
    `area` del provino. **Duplicate quasi identiche** in
    `cyclic_test_widget.py`.
  - `on_finish_and_save()`: salva l'intero batch di provini in un unico
    `.xlsx` tramite `DataSaver.save_batch_to_xlsx()`.

## Dipendenze

- Riceve `communicator` e `main_window` nel costruttore. Legge
  `main_window.current_force_limit_N` / `current_disp_limit_mm` per tutte le
  validazioni sui limiti (in 5 punti: creazione/modifica provino, avvio
  test).
- Usa `DataSaver` per l'export Excel e `DisplayWidget` da
  `custom_widgets.py`.
- Riceve dati solo tramite `handle_stream_data()` chiamato da
  `MainWindow.handle_data_from_esp32()`; non legge mai direttamente dalla
  porta seriale.

## Punti di attenzione

- **Bug storico corretto**: `on_start_test()` referenziava
  `self.current_force_limit_N` (attributo mai definito sul widget) invece di
  `self.main_window.current_force_limit_N` nel messaggio del popup di alert
  di sicurezza — qualunque avvio con `Stop Criterion = Force (N)` o
  `Stress (MPa)` sollevava `AttributeError` prima del fix (vedi
  `CHANGELOG.md`). Se si copia questo pattern di controllo limiti altrove,
  assicurarsi di usare sempre `self.main_window.current_force_limit_N`.
- La logica di conversione unità (`convert_speed`, `convert_stop_criterion`)
  è duplicata parola per parola in `cyclic_test_widget.py`: un bug o una
  nuova unità di misura va corretta/aggiunta in entrambi i file.
- Le funzioni `zero_relative_load()` / `zero_relative_displacement()` sono
  definite **due volte** nella classe (righe iniziali e più avanti,
  identiche): la seconda definizione sovrascrive silenziosamente la prima,
  nessun impatto funzionale ma è codice morto da pulire con cautela (occhio
  a non lasciare la copia sbagliata se si modifica solo una delle due).
- `update_stop_criterion_options()` è anch'essa definita due volte
  (identica), stesso discorso.
- `refresh_plot()` ricrea sempre l'asse/viewbox della resistenza da zero
  (distrugge e ricrea), con gestione errori tramite `try/except` generici che
  stampano solo su console: un errore qui non blocca la UI ma può lasciare lo
  stato dell'asse secondario inconsistente silenziosamente.