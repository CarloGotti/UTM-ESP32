# cyclic_test_widget.py

## Scopo

Schermata per test ciclici composti da una **sequenza di blocchi**
eterogenei (ciclo ripetuto, pausa, rampa verso un target), con editor della
sequenza, gestione batch provini (simile al monotonico) e grafico live/overlay.
È il file più grande del progetto (~1800 righe). Avvia solo il primo blocco
della sequenza: il proseguimento ai blocchi successivi è pilotato da
`main.py` in risposta a `STATUS:BLOCK_COMPLETED` (vedi `docs/main.md`).

## Classi e funzioni principali

- **`BlockDialog(QDialog)`** — definisce un blocco ciclico: tipo di
  controllo (Displacement/Strain/Force/Stress), limiti superiore/inferiore
  **relativi**, velocità, tempi di hold ai due limiti, numero di cicli.
  `accept()` valida solo che `upper > lower` (la validazione sui limiti
  macchina assoluti è delegata al widget principale).
- **`PauseDialog(QDialog)`** — un solo campo, durata della pausa in secondi.
- **`RampDialog(QDialog)`** — target **relativo** (in una delle 4 unità),
  velocità, hold opzionale al target.
- **`SpecimenDialog(QDialog)`** — nome/gauge length/area del provino, con
  validazione di unicità del nome e positività dei valori.
- **`CyclicTestWidget(QWidget)`**
  - Segnali: `back_to_menu_requested`, `limits_button_requested`.
  - Stato: `test_sequence` (lista di blocchi, ognuno un dict con almeno
    `"type"` ∈ `{"cyclic","pause","ramp"}`), `current_block_index`,
    `specimens`, `current_specimen_name`, `current_test_data` (tuple a 8
    elementi: `time_s, rel_disp, rel_load, abs_disp, abs_load, cycle_count,
    block_num, resistance_ohm`).
  - `on_add_block()` / `on_add_ramp()`: aprono il dialog corrispondente,
    convertono i valori grezzi in unità base (mm o N) con
    `convert_stop_criterion()`/`convert_speed()`, **validano contro i limiti
    macchina assoluti** (`main_window.current_force_limit_N` /
    `current_disp_limit_mm`, tenendo conto degli offset relativi correnti),
    e solo se valido appendono il blocco a `test_sequence`.
  - `on_edit_block()`: replica la stessa logica di conversione/validazione
    per modificare un blocco esistente, con rami separati per
    `cyclic`/`ramp`/`pause`.
  - `on_remove_block()`, `on_move_block_up/down()`: gestione ordine sequenza,
    con aggiornamento sincronizzato di `test_sequence` e della
    `QListWidget` visuale.
  - `on_start_test()`: prende **solo il primo blocco** di `test_sequence`,
    costruisce il comando firmware appropriato
    (`START_CYCLIC_TEST:...` o `EXECUTE_RAMP:...`; rifiuta se il primo
    blocco è una pausa), invia `RESET_TIMER` seguito dal comando e da
    `SET_MODE:STREAMING`. Da qui in poi i blocchi successivi sono gestiti da
    `main.py`, non da questo metodo.
  - `on_stop_test(user_initiated)`: stessa dinamica two-phase del test
    monotonico (stop immediato lato utente, finalizzazione differita quando
    richiamato da `MainWindow`). In finalizzazione fa autosave in
    `AUTOSAVE_CYCLIC_<nome>_<timestamp>.xlsx`, includendo anche
    `test_sequence` come `"test_sequence_setup"` per la descrizione testuale
    nel file Excel.
  - `handle_stream_data(...)`: aggiorna stato, accoda dati (tupla a 8
    elementi), aggiorna la curva principale e quella di resistenza.
  - `_calculate_estimated_duration()`: **simula** la sequenza (posizione
    corrente, tempi di riposizionamento tra blocchi, tempi di ciclo/rampa)
    per stimare la durata totale, ma solo se **tutti** i blocchi sono basati
    su spostamento (`base_unit == "mm"`); se anche un solo blocco è basato su
    forza/stress, la stima è dichiarata "N/A (contains non-displacement
    blocks)".
  - `convert_speed()` / `convert_stop_criterion()`: identiche (a meno di
    guardie `gauge_length <= 0` / `area <= 0` leggermente più difensive) a
    quelle in `monotonic_test_widget.py`.
  - `refresh_plot()`: come nel monotonico, gestisce anche l'asse secondario
    per la resistenza; in più ricrea sempre una curva live dedicata
    (`self.plot_curve`, con stile tratteggiato) alla fine della funzione,
    perché lo streaming ciclico può durare a lungo attraverso più blocchi.

## Dipendenze

- Riceve `communicator` e `main_window`; legge
  `main_window.current_force_limit_N` / `current_disp_limit_mm` in tutte le
  validazioni di `on_add_block`, `on_add_ramp`, `on_edit_block`.
- **`main.py` dipende a sua volta da questo modulo**: legge/scrive
  `cyclic_test.current_block_index` e `cyclic_test.test_sequence` per
  pilotare i blocchi 2..N della sequenza. Il formato dei dizionari-blocco
  (chiavi `upper_conv`, `lower_conv`, `target_conv`, `base_unit`,
  `speed_mms`, `hold_upper`, `hold_lower`, `cycles`, `hold_duration`) è quindi
  un **contratto implicito** condiviso con `main.py`: rinominare o cambiare
  significato a una di queste chiavi qui rompe silenziosamente il
  proseguimento della sequenza in `handle_data_from_esp32()`.
- Usa `DataSaver` e `DisplayWidget` da `custom_widgets.py`.

## Punti di attenzione

- **Duplicazione della logica "costruisci comando per un blocco"**: esiste
  sia qui (in `on_start_test()`, solo per il primo blocco) sia in
  `main.py::handle_data_from_esp32()` (per i blocchi successivi). Sono due
  implementazioni indipendenti dello stesso mapping blocco→comando
  seriale: se si aggiunge un nuovo tipo di blocco, va aggiunto in *entrambi*
  i punti, con lo stesso formato di stringa.
- `_calculate_estimated_duration()` assume che ogni blocco ciclico parta
  sempre dal limite inferiore (`start_position_mm = lower_rel`): se in futuro
  cambia la logica di pre-posizionamento nel firmware (vedi
  `CYCLIC_PREPOSITION` in `docs/firmware_main.md`), questa stima andrebbe
  disallineata dal comportamento reale.
- Come nel monotonico, `convert_speed`/`convert_stop_criterion` sono
  duplicate: mantenerle sincronizzate manualmente con
  `monotonic_test_widget.py`.
- `RampDialog` non riceve i limiti macchina nel costruttore (TODO esplicito
  nel codice: `"# TODO: Passare limiti e offset per validazione futura"`), la
  validazione avviene solo dopo la chiusura del dialog in
  `on_add_ramp()`/`on_edit_block()` — l'utente può quindi impostare un valore
  fuori limite nel dialog stesso e scoprirlo solo al termine.