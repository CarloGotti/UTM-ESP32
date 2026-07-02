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
    `current_test_data` (lista di tuple a 6 elementi: `time_s, rel_disp,
    rel_load, abs_disp, abs_load, resistance_ohm`), `current_resistance_ohm`.
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
  - `handle_stream_data(load_N, disp_mm, time_s, cycle_count, resistance_ohm)`:
    chiamato da `MainWindow` per ogni pacchetto `D:` mentre il widget è
    quello corrente; aggiorna i valori assoluti, accoda un punto dati, e
    aggiorna la curva live (con conversione opzionale Strain/Stress in base
    ai combo box degli assi).
  - `on_new_specimen()` / `on_modify_specimen()`: creano/aggiornano una voce
    in `specimens`, convertendo velocità e stop criterion nelle unità base e
    **validando che il target assoluto non superi i limiti macchina**
    (`main_window.current_disp_limit_mm` / `current_force_limit_N`) prima di
    permettere il salvataggio del provino.
  - `refresh_plot()`: ridisegna il grafico principale e, se abilitato,
    l'asse secondario per la resistenza LCR (crea/distrugge dinamicamente una
    `pg.ViewBox` secondaria agganciata all'asse destro).
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