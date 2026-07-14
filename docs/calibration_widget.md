# calibration_widget.py

## Scopo

Schermata di calibrazione della cella di carico: guida l'utente attraverso un
wizard a stati (tara → peso noto → fine) che pilota i comandi `TARE` e
`CALIBRATE:<grammi>` sul firmware, e gestisce salvataggio/caricamento del
fattore di calibrazione su file JSON esterni.

## Classi e funzioni principali

- **`SetLoadsDialog(QDialog)`** — tabella editabile (`QTableWidget`) dei
  carichi di zero/calibrazione per ogni cella nota (`cal_loads`, es.
  `{"10N": [0.0, 398.0]}`). `get_updated_loads()` ritorna il dizionario
  aggiornato, o quello originale se la conversione a float fallisce per
  qualche riga.
- **`CalibrationWidget(QWidget)`**
  - Segnali: `back_to_menu_requested`, `calibration_updated(str, str)` (testo
    di stato, nome cella), `settings_changed(dict)` (nuovi `cal_loads` da
    persistere).
  - `current_calibration_factor`: fattore di scala corrente **noto alla
    GUI**, `None` finché non diventa noto in uno dei due modi seguenti (vedi
    `CHANGELOG.md`, fix del pulsante "Save Calibration"). Controlla anche
    l'abilitazione di `save_cal_button` (disabilitato quando è `None`).
  - Stato interno `calibration_state`: `"IDLE" → "WAITING_FOR_ZERO" →
    "WAITING_FOR_WEIGHT" → "IDLE"`, avanzato da `handle_calibration_step()`
    ad ogni click sul pulsante (che cambia testo/etichetta ad ogni fase).
    - `WAITING_FOR_ZERO`: invia `TARE`.
    - `WAITING_FOR_WEIGHT`: invia `CALIBRATE:<cal_weight>` (il peso noto
      preso da `cal_loads[selected_cell][1]`) ed emette
      `calibration_updated("Just Calibrated", selected_cell)`. Il fattore di
      scala reale **non** è ancora noto a questo punto (il firmware lo
      calcola mediando su una finestra di 1s): arriva in modo asincrono via
      `STATUS:CALIBRATION_DONE;SCALE=..`, che `MainWindow` inoltra a
      `set_calibration_factor()`.
  - `set_calibration_factor(scale_factor)`: chiamato da `MainWindow` sia in
    risposta a `STATUS:CALIBRATION_DONE` sia subito dopo l'invio di
    `SET_SCALE` da `load_calibration()` (in quel caso il valore è già noto
    localmente, nessun bisogno di attendere conferma). Aggiorna
    `current_calibration_factor` e abilita `save_cal_button`.
  - `invalidate_calibration()`: chiamato da `MainWindow` in risposta a
    `STATUS:CALIBRATION_INVALIDATED` (cambio gain PGA reale): azzera
    `current_calibration_factor` a `None` e disabilita `save_cal_button`,
    perché il fattore noto qui non è più valido al nuovo gain.
  - `show_set_loads_dialog()`: apre `SetLoadsDialog`; se i carichi cambiano
    emette `settings_changed` per farli persistere da `MainWindow`.
  - `save_calibration()`: se `current_calibration_factor` è `None` mostra un
    messaggio e non apre nemmeno il file dialog. Altrimenti apre un
    `QFileDialog` di salvataggio e scrive direttamente un JSON
    (`cell_name`, `calibration_factor`, `saved_at`) — nessuna indirezione
    verso `MainWindow` (vedi `CHANGELOG.md`).
  - `load_calibration()`: apre un file JSON, legge `calibration_factor`,
    invia `SET_SCALE:<factor>` al firmware, chiama
    `set_calibration_factor(scale_factor)` ed emette `calibration_updated`
    usando come nome cella la seconda parte del nome file separato da `_`
    (assume una convenzione tipo `cal_<CELLA>_<data>.json`).

## Dipendenze

- Riceve `communicator` (per inviare `TARE`/`CALIBRATE`/`SET_SCALE`) e
  `cal_loads` (dizionario caricato da `SettingsManager` tramite `MainWindow`).
- `calibration_updated` è collegato in `MainWindow` a
  `update_calibration_status()`, che a sua volta aggiorna
  `current_force_limit_N` e propaga i limiti al firmware — quindi il nome
  cella passato qui (`selected_cell` o `cell_name_from_file`) deve avere il
  formato `"<numero>N"` per essere interpretato correttamente a valle.
- `MainWindow.handle_data_from_esp32()` inoltra a questo widget due
  messaggi `STATUS:` che il widget non potrebbe intercettare da sé (non
  legge la porta seriale direttamente): `CALIBRATION_DONE` (→
  `set_calibration_factor()`) e `CALIBRATION_INVALIDATED` (→
  `invalidate_calibration()`).
- Usa `DisplayWidget` da `custom_widgets.py`.

## Punti di attenzione

- `load_calibration()` deduce il nome della cella dal **nome del file**
  (`filename.split('_')[1]`), non da un campo dentro il JSON: se l'utente
  rinomina il file o non segue la convenzione `cal_<CELLA>_...`, il limite di
  forza propagato a valle sarà sbagliato o non aggiornabile
  (`update_calibration_status` fallisce silenziosamente con un
  `print()` se non riesce a fare `float()` sul nome).
- `handle_calibration_step()` non ha alcuna validazione che la cella
  selezionata nel combo non cambi tra una fase e l'altra del wizard: se
  l'utente cambia `cell_selector` mentre è a metà del flusso
  (`WAITING_FOR_ZERO`/`WAITING_FOR_WEIGHT`), il peso di calibrazione
  usato in `WAITING_FOR_WEIGHT` sarà quello della cella *attualmente*
  selezionata, non quella con cui si è iniziata la procedura.