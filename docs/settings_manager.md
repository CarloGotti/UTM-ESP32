# settings_manager.py

## Scopo

Persistenza minimale su file JSON (`settings.json`, nella working directory
dell'app) delle impostazioni applicative. Gestisce i carichi di calibrazione
(`cal_loads`) per cella e la configurazione del filtro EMA della cella di
carico (`filter_config`).

## Classi e funzioni principali

- **`SettingsManager`**
  - `__init__(filename="settings.json")`: definisce `default_settings` con
    `cal_loads` precompilato per le celle `1N, 10N, 50N, 100N, 200N` (ognuna
    come `[zero_load_g, cal_load_g]`) e `filter_config` precompilato con
    `{"alpha": 0.5, "rate_sps": 320, "gain": 128}` (default del firmware
    NAU7802, gain 128x coincidente col default interno della libreria).
  - `load_settings()`: se il file esiste lo legge e fa il merge delle chiavi
    mancanti con i default (senza sovrascrivere quelle presenti); se il JSON
    è corrotto, stampa un avviso e ritorna i default **senza però
    riscrivere il file corrotto**; se il file non esiste, lo crea con i
    default e li ritorna.
  - `save_settings(settings)`: scrive il dizionario intero su file con
    indentazione 4, sovrascrivendo il contenuto precedente.

## Dipendenze

- Istanziato una sola volta in `MainWindow.__init__()`. `MainWindow` passa
  `settings['cal_loads']` a `CalibrationWidget` alla costruzione e collega
  `CalibrationWidget.settings_changed` a
  `MainWindow.save_cal_load_settings()`, che aggiorna `self.settings` e
  richiama `save_settings()`.
- `MainWindow` legge anche `settings['filter_config']` per inizializzare
  `current_filter_alpha`/`current_filter_rate_sps`/`current_filter_pga_gain`,
  e li ri-salva tramite `save_settings()` da `show_filter_dialog()` quando
  l'utente conferma una nuova configurazione da "Filter Config".
- Nessuna dipendenza verso altri moduli: usa solo `json` e `os` dalla
  standard library.

## Punti di attenzione

- `save_settings()` fa una scrittura diretta senza file temporaneo né
  gestione di scritture concorrenti: un crash a metà scrittura (es. perdita
  di alimentazione, kill del processo) può corrompere `settings.json`. Dato
  che `load_settings()` in quel caso ritorna silenziosamente i default senza
  avvisare in UI (solo un `print()` su console), l'utente potrebbe non
  accorgersi che i carichi di calibrazione personalizzati sono stati persi.
- Il path del file (`"settings.json"`) è relativo alla working directory da
  cui viene lanciato lo script: se l'app viene avviata da directory diverse
  (es. da un collegamento vs da terminale), può finire per leggere/scrivere
  file `settings.json` diversi senza che sia ovvio all'utente.
- Il merge in `load_settings()` è **solo a livello di chiavi di primo
  livello**: se una chiave come `filter_config` esiste già nel file ma le
  manca una sotto-chiave introdotta successivamente nei `default_settings`
  (es. l'aggiunta di `"gain"` a un `filter_config` salvato prima che
  esistesse), quella sotto-chiave **non viene aggiunta automaticamente** —
  serve un intervento manuale sul file esistente (com'è successo
  nell'introduzione del gain PGA, vedi `CHANGELOG.md`), altrimenti il codice
  che si aspetta quella chiave solleva un `KeyError`. Chi aggiunge nuovi
  campi a impostazioni già esistenti in struttura nidificata deve tenerne
  conto.
- I limiti di sicurezza (`current_force_limit_N` / `current_disp_limit_mm`
  in `main.py`) e il fattore di calibrazione attivo **non** passano da qui
  (a differenza di `filter_config`, che invece è persistito): sono gestiti
  con meccanismi separati o non persistiti affatto (vedi `docs/main.md`,
  `docs/calibration_widget.md` e `TODO.md`) — chi cerca "dove sono salvate
  le impostazioni" in questo file troverà solo una parte della persistenza
  applicativa.