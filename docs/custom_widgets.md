# custom_widgets.py

## Scopo

Raccolta di widget Qt riusabili e senza logica applicativa specifica,
condivisi da tutte le schermate: un indicatore numerico standard, una barra
di velocità colorata, il dialog dei limiti di sicurezza macchina e il dialog
di configurazione del filtro della cella di carico.

## Classi e funzioni principali

- **`SpeedBarWidget(QWidget)`** — barra a segmenti colorata verde→giallo→rosso
  in base a una percentuale (`setValue(percent)`), disegnata manualmente in
  `paintEvent()` con un gradiente calcolato punto per punto
  (`get_gradient_color()`). Usata in `manual_control_widget.py` per mostrare
  la velocità di jog impostata rispetto a `MIN_SPEED`/`MAX_SPEED`.
- **`DisplayWidget(QWidget)`** — etichetta + valore in stile "display"
  (font monospaced grande, sfondo grigio, bordo incassato). `set_value(text)`
  aggiorna solo il testo del valore. È il building block usato ovunque per
  mostrare Absolute/Relative Load, Displacement, Resistance, Calibration
  status, Cycle count, ecc., in tutte le schermate.
- **`LimitsDialog(QDialog)`** — form con due `QDoubleSpinBox` (forza massima
  in N, range 0.1–5000; spostamento massimo in mm, range 1–190) pre-popolati
  con i valori correnti passati al costruttore. `get_values()` ritorna la
  tupla `(force_N, disp_mm)` inseriti dall'utente. Non invia nulla da solo:
  è `MainWindow.show_limits_dialog()` a leggere i valori e inviare
  `SET_LIMITS` al firmware.
- **`FilterConfigDialog(QDialog)`** — form con un `QDoubleSpinBox` (alpha
  del filtro EMA, range 0.01–1.00), un `QComboBox` (sample rate NAU7802,
  valori fissi "10/20/40/80/320 SPS") e un secondo `QComboBox` (guadagno
  PGA, valori fissi "1x/2x/4x/8x/16x/32x/64x/128x") pre-popolati con i
  valori correnti passati al costruttore. `get_values()` ritorna la tupla
  `(alpha: float, rate_sps: int, gain: int)`. Stesso pattern di
  `LimitsDialog`: non invia nulla da solo, è `MainWindow.show_filter_dialog()`
  a leggere i valori, salvarli in `settings.json` e inviare
  `SET_FILTER_CONFIG` al firmware. Non mostra alcun avviso se il gain
  cambia — quello arriva separatamente e in modo asincrono dalla GUI
  quando il firmware conferma l'invalidazione della calibrazione (vedi
  `docs/main.md`).

## Dipendenze

- Nessuna dipendenza verso altri moduli applicativi (solo PyQt6). È
  importato da `main.py`, `calibration_widget.py`, `monotonic_test_widget.py`,
  `cyclic_test_widget.py`, `manual_control_widget.py`.

## Punti di attenzione

- Il range dello spostamento massimo in `LimitsDialog`
  (`setRange(1.0, 190.0)`) codifica un **limite fisico della macchina**
  (corsa massima della vite) direttamente nella UI: se la corsa fisica
  cambia (nuova macchina, nuova vite), va aggiornato qui a mano; non è
  derivato da nessuna costante condivisa con `main.py` o col firmware.
- `DisplayWidget` non fa alcuna validazione o formattazione del testo che
  riceve: la responsabilità di formattare i numeri (decimali, unità,
  notazione scientifica per valori estremi) è interamente demandata ai
  chiamanti, che infatti duplicano la stessa logica di formattazione della
  resistenza in tre punti diversi (vedi i rispettivi `docs/*.md` dei widget
  di test/manuale).
- `SpeedBarWidget` ridisegna l'intera barra ad ogni `paintEvent` con un
  ciclo su tutti i segmenti: non è un problema alle dimensioni attuali, ma
  non è pensato per aggiornamenti a frequenza molto alta.
- `FilterConfigDialog.get_values()` estrae sample rate e gain numerici dal
  testo dei combo box (`"320 SPS".split()[0]`, `"128x".rstrip('x')`) invece
  di associare un dato numerico esplicito a ogni voce (es. con `userData`):
  funziona perché le voci sono hardcoded nello stesso metodo `__init__`, ma
  se in futuro le etichette cambiano formato (es. localizzazione, spazi
  diversi) questo parsing va aggiornato in coppia con `addItems()`.