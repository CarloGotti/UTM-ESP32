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
    persistere), `save_calibration_requested(str)` (path scelto per il
    salvataggio).
  - Stato interno `calibration_state`: `"IDLE" → "WAITING_FOR_ZERO" →
    "WAITING_FOR_WEIGHT" → "IDLE"`, avanzato da `handle_calibration_step()`
    ad ogni click sul pulsante (che cambia testo/etichetta ad ogni fase).
    - `WAITING_FOR_ZERO`: invia `TARE`.
    - `WAITING_FOR_WEIGHT`: invia `CALIBRATE:<cal_weight>` (il peso noto
      preso da `cal_loads[selected_cell][1]`) ed emette
      `calibration_updated("Just Calibrated", selected_cell)`.
  - `show_set_loads_dialog()`: apre `SetLoadsDialog`; se i carichi cambiano
    emette `settings_changed` per farli persistere da `MainWindow`.
  - `save_calibration()`: apre un `QFileDialog` di salvataggio e **si limita
    a emettere** `save_calibration_requested(filePath)` — non scrive nulla
    da sé (vedi Punti di attenzione).
  - `load_calibration()`: apre un file JSON, legge `calibration_factor`,
    invia `SET_SCALE:<factor>` al firmware, ed emette `calibration_updated`
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
- Usa `DisplayWidget` da `custom_widgets.py`.

## Punti di attenzione

- **`save_calibration_requested` non è collegato a nessuno slot in
  `main.py`**: il pulsante "Save Calibration" fa vedere all'utente il file
  dialog completarsi, ma nessun file JSON viene effettivamente scritto. È una
  feature rimasta a metà (probabilmente durante un refactor che ha spostato
  la responsabilità del salvataggio fuori da questo widget senza completare
  il collegamento). Chi vuole implementarla deve creare uno slot in
  `MainWindow` collegato a questo segnale, che scriva su `filePath` il
  fattore di scala corrente (letto da dove? oggi non esiste un comando
  `GET_SCALE` mai chiamato — vedi `docs/firmware_main.md`).
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