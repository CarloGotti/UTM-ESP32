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

## Acquisizione ad alta frequenza (avvicinarsi ai 320 Hz del NAU7802)

**Problema attuale**: dopo la migrazione a NAU7802 (vedi `CHANGELOG.md`), il
sensore può produrre nuovi campioni fino a 320 volte al secondo
(`SET_FILTER_CONFIG:...;RATE=320`), ma questo **non significa che
l'acquisizione registrata sia a 320 Hz**. Il pacchetto `D:` inviato al PC è
governato da `STREAM_INTERVAL_MS` in `Controllo-Macchina-ESP32/src/main.cpp`
(costante fissa, oggi 20 ms → 50 Hz), indipendente dal sample rate del
sensore. Un RATE alto con alpha=1 (filtro disattivato) significa solo
"ultimo campione istantaneo grezzo ogni 20ms", non un'acquisizione a
risoluzione temporale più alta.

**Cosa comporterebbe portare `STREAM_INTERVAL_MS` vicino ai 3 ms (≈320 Hz)**:

1. **Banda seriale**: nessun problema. A 460800 baud (8N1) il throughput è
   ~46 KB/s; un pacchetto `D:` tipico (~35-40 byte) a 320 Hz userebbe circa
   il 28% della banda disponibile (contro il ~4% attuale a 50 Hz). Ampio
   margine.
2. **Timing del `loop()` firmware**: da verificare empiricamente, non
   garantito a priori come la banda seriale. Ogni ciclo di `loop()`
   (lettura I2C del NAU7802, controllo motore, polling LCR) dovrebbe restare
   sotto ~3ms con margine sufficiente. Probabile che regga (le operazioni
   I2C sono nell'ordine delle centinaia di µs), ma va misurato, non assunto.
3. **Il costo reale è lato Python/GUI, non sul firmware o sul cavo**:
   - `handle_data_from_esp32()` → `handle_stream_data()` farebbe ~6.4×
     più `append()` sulla lista dati e più `setData()` su curve pyqtgraph
     al secondo rispetto a oggi (320 vs 50 Hz) — rischio concreto di grafico
     live che rallenta o di backlog sul buffer seriale se Python non sta al
     passo.
   - Un test di 5 minuti passerebbe da ~15.000 a ~96.000 punti campionati:
     `DataSaver` scrive una riga per punto in un foglio Excel via
     `openpyxl` — file molto più pesanti, autosave/export molto più lenti.
   - `STREAM_INTERVAL_MS` andrebbe reso configurabile (nuovo parametro,
     eventualmente dentro lo stesso `SET_FILTER_CONFIG` visto che è
     concettualmente legato al rate di acquisizione) prima ancora di poter
     esporlo dalla GUI.

**Conclusione**: fattibile sul piano elettrico/seriale, ma l'impatto reale
si sposterebbe sul carico di lavoro Python (grafici live, dimensione file
di export) più che sul firmware o sul cavo. Da affrontare solo se serve
davvero una risoluzione temporale più alta per l'analisi dei dati; non è
un prerequisito della migrazione NAU7802 già completata.

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
