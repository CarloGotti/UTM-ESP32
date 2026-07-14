# data_saver.py

## Scopo

Unico punto di export dei dati di test verso file Excel (`.xlsx`), condiviso
sia dai test monotonici sia da quelli ciclici (e dalla registrazione manuale
in `manual_control_widget.py`). Un foglio per provino, con parametri di setup,
dati tabellari e grafici Scatter incorporati.

## Classi e funzioni principali

- **`DataSaver`** (nessuno stato, tutti i metodi operano sugli argomenti
  passati)
  - `save_batch_to_xlsx(specimens_dict, filepath, calibration_info="N/A")`:
    crea un `Workbook`, un foglio per ogni provino che ha `test_data` non
    vuoto, salva su `filepath`. Ritorna `(True, msg)` o `(False, msg)`
    catturando qualunque `Exception`.
  - `_create_sheet_for_specimen(...)`: distingue test ciclico da monotonico
    controllando la presenza della chiave `"test_sequence_setup"` nei dati
    del provino (non un campo esplicito tipo `"test_type"`). Scrive i
    parametri di setup, poi la tabella dati con intestazioni fisse:
    `Time, Relative Displacement, Relative Load, Strain, Stress, Absolute
    Displacement, Absolute Load, Resistance, Encoder Displacement`
    (+`Cycle`, `Block` se ciclico). `Encoder Displacement (mm)` è il canale
    di sola lettura dell'encoder incrementale esterno (vedi `CHANGELOG.md`):
    scritto **accanto**, non al posto, di `Absolute/Relative Displacement`
    (che restano la stima a passi motore), per permettere il confronto tra i
    due nei dati salvati. Interpreta le tuple di `test_data` per
    **lunghezza**: 7 elementi per monotonico/registrazione manuale (con
    resistenza ed encoder), 9 per ciclico (con cycle/block/resistenza/
    encoder). Se l'ultimo elemento è `None` (pacchetto storico senza
    encoder, o parsing fallito lato Python), scrive `NaN` nella colonna.
  - `_format_block_description(block, index)`: converte un dizionario-blocco
    (nello stesso formato usato da `cyclic_test_widget.py`) in una riga di
    testo leggibile, per il riepilogo "Test Sequence" scritto nel foglio.
  - `_style_excel_chart(chart, x_title, y_title, legend=None)`: applica uno
    stile comune (assi neri, griglia solo su Y, nessuna legenda di default)
    a tutti i grafici `ScatterChart` creati.

## Dipendenze

- Nessuna dipendenza verso altri moduli applicativi: riceve solo dizionari
  Python semplici (`specimens_dict`) costruiti da chi lo chiama.
- Chiamato da: `MonotonicTestWidget.on_stop_test()` (autosave singolo
  provino) e `on_finish_and_save()` (batch); `CyclicTestWidget` allo stesso
  modo (aggiungendo `test_sequence_setup` ai dati); `ManualControlWidget.
  _save_recorded_data()` (crea un "provino fittizio" con `gauge_length`/`area`
  a `NaN` per riusare lo stesso export).

## Punti di attenzione

- Il **contratto di formato delle tuple** in `test_data` è implicito e basato
  sulla posizione e sulla lunghezza (`len(data_row) == 7` vs `== 9`): se un
  chiamante cambia l'ordine o il numero di campi in una tupla senza
  aggiornare questo file, i dati vengono scritti nelle colonne sbagliate
  senza errori espliciti (l'unico segnale sarebbe
  `resistance`/`cycle`/`block`/`encoder_disp` che restano `NaN`).
- Analogamente, la distinzione ciclico/monotonico basata sulla presenza della
  chiave `"test_sequence_setup"` è fragile: un dizionario provino che la
  contenga per errore (es. copiato da un provino ciclico) verrebbe trattato
  come ciclico anche se i dati sono monotonici.
- `_format_block_description()` assume che ogni dizionario-blocco abbia
  esattamente le chiavi usate da `cyclic_test_widget.py` per quel `type`
  (`control_text`, `lower`, `upper`, `speed`, `speed_unit`, `cycles`,
  `hold_upper`, `hold_lower` per `"cyclic"`; `duration` per `"pause"`;
  `target`, `hold_duration` per `"ramp"`). Un cambiamento di schema nei
  blocchi lato `cyclic_test_widget.py` va rispecchiato qui, altrimenti il
  metodo cade nel ramo `except Exception` e scrive solo un messaggio di
  errore invece della descrizione.
- Gli errori di scrittura file (`IOError`, permessi, file aperto in Excel)
  sono catturati genericamente da `save_batch_to_xlsx` e restituiti come
  stringa: non c'è distinzione tra "file bloccato da Excel" e altri errori,
  il messaggio mostrato all'utente è quello grezzo dell'eccezione Python.