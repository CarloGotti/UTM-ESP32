# TODO

Idee e lavori futuri, non ancora pianificati né implementati. A differenza di
`CHANGELOG.md` (cosa è già cambiato) questo file raccoglie cosa **manca
ancora**.

## Indicatore permanente del limite di sicurezza attivo, letto dal firmware

**Problema attuale**: il valore di `current_force_limit_N` /
`current_disp_limit_mm` mostrato in "LIMITS" ([custom_widgets.py::LimitsDialog](custom_widgets.py))
riflette solo lo stato **in memoria lato Python** (`MainWindow`), non quello
realmente attivo sul firmware in questo momento. I due possono divergere
silenziosamente: è già successo durante il collaudo di questa sessione, dove
un comando `SET_LIMITS` inviato troppo presto dopo la connessione (durante il
boot dell'ESP32) veniva perso, lasciando il firmware senza limiti reali pur
con la GUI convinta del contrario. Non c'è oggi alcun modo per l'utente di
accorgersene senza un test fisico (portare la macchina al limite e vedere se
si ferma).

**Cosa serve**:

1. **Un'etichetta sempre visibile** (es. nella barra di stato in basso, o un
   `DisplayWidget` accanto al pulsante "LIMITS" in ogni schermata) che mostri
   il limite di forza/spostamento correntemente attivo.
2. **Il valore mostrato deve venire dal firmware, non dalla sola memoria
   Python** — altrimenti l'indicatore avrebbe lo stesso problema di fiducia
   cieca che ha causato il bug di questa sessione. Serve quindi:
   - un **nuovo comando firmware** `GET_LIMITS` (oggi non esiste; il
     firmware ha solo `SET_LIMITS`, che è solo in scrittura) che risponda con
     qualcosa tipo `LIMITS:FORCE_G=<val>;PULSES=<val>` — sullo stesso modello
     già usato per `GET_SCALE`/`SCALE:` in `Controllo-Macchina-ESP32/src/main.cpp`
     (comando implementato ma oggi non chiamato da nessun widget, vedi
     `docs/firmware_main.md`).
   - lato Python, un `QTimer` che invii `GET_LIMITS` **periodicamente** (ogni
     N secondi, "ogni tot" come richiesto) e aggiorni l'etichetta con la
     risposta, convertendo grammi→N e passi→mm (stessa logica di
     `PULSES_TO_MM` già in `MainWindow`).
3. Da decidere: cosa mostrare se il polling non riceve risposta entro un
   timeout (macchina disconnessa, o firmware che non implementa ancora il
   comando) — probabilmente uno stato esplicito tipo "N/A" o "??" invece di
   mostrare l'ultimo valore noto come se fosse ancora valido.

**Nota di design**: questo andrebbe implementato come fonte di verità
aggiuntiva e non in sostituzione di `current_force_limit_N` /
`current_disp_limit_mm`, che restano necessari per le validazioni istantanee
lato GUI (creazione provini, avvio test) dove non ha senso aspettare un
round-trip seriale prima di ogni controllo.

## Altri punti aperti (dai `docs/*.md` e da `CLAUDE.md`)

- "Save Calibration" non salva nulla (`calibration_widget.py`, segnale
  `save_calibration_requested` non collegato a nessuno slot).
- `current_force_limit_N` / `current_disp_limit_mm` non sono persistiti su
  disco: si perdono alla chiusura dell'app Python (tornano al default
  hardcoded), a differenza di `cal_loads` che è salvato in `settings.json`.
- Debug seriale non prefissato e ad alta frequenza nel firmware
  (`startMotor()` durante il jog) — occupa banda/CPU inutilmente.
- Parsing comandi manuale e fragile nel firmware (`indexOf`/`substring`),
  nessuna validazione su campi mancanti o fuori ordine.
- Riuso di `cyclic_hold_upper_ms` per la durata di `EXECUTE_PAUSE` (hack
  segnalato nel codice stesso).
- `GET_SCALE`/`SCALE:` implementato ma mai chiamato — la GUI non legge mai il
  fattore di scala corrente dal firmware.
- `platformio.ini` ha `monitor_speed = 115200` disallineato dal reale
  `Serial.begin(460800)` del firmware.
