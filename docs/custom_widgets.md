# custom_widgets.py

## Scopo

Raccolta di widget Qt riusabili e senza logica applicativa specifica,
condivisi da tutte le schermate: un indicatore numerico standard, una barra
di velocità colorata, e il dialog dei limiti di sicurezza macchina.

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