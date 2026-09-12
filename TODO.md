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
   Dall'integrazione dell'encoder incrementale esterno (vedi `CHANGELOG.md`)
   il `loop()` ha anche due nuove ISR (`handleEncoderChange()` su A/B,
   `handleEncoderZChange()` su Z) che si aggiungono a quella del timer di
   step: il loro overhead a velocità di traversa elevate non è stato
   ancora misurato, quindi la validazione di `STREAM_INTERVAL_MS` andrebbe
   rifatta tenendone conto, non solo del carico NAU7802. **Idem per il
   polling SPI dell'ADS1220** (`updateADS1220Reading()`, vedi `CLAUDE.md`
   sezione ADS1220): stima teorica di impatto trascurabile (poche decine di
   µs per lettura `RDATA`, rate-limitata al sample rate configurato), ma
   **il jitter di `loop()` a velocità non è ancora mai stato misurato su
   hardware reale**. In una sessione successiva è stata fatta una prima
   verifica fisica del canale ADS1220 (registri confermati corretti via
   `DEBUG_ADS1220`, lettura di un resistore di prova funzionante — vedi
   `CHANGELOG.md`), ma **solo a motore fermo**, via diagnosi seriale
   diretta: non copre questo punto. Da fare: misurare il jitter reale su
   `STREAM_INTERVAL_MS` e sulla cadenza di step motore con il canale
   ADS1220 attivo a piena velocità di traversa (idealmente insieme alla
   misura dell'overhead encoder, unico giro di test), prima di un uso in
   produzione ad alta velocità con quel canale abilitato.
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

## ADS1220: range di resistenza misurabile limitato dal riferimento fisico fisso

**Verificato su hardware reale** (vedi `CHANGELOG.md`): il range massimo di
resistenza misurabile è `R_ref / gain`, indipendente da `IDAC`. Con l'attuale
`R_ref = 989.58 Ω`, il tetto assoluto è **~990 Ω** (a `GAIN=1`, il minimo
disponibile) — oltre quella soglia l'ADC satura a fondo scala e la lettura
resta bloccata allo stesso valore, indistinguibile da un circuito aperto.
Non è configurabile via `SET_ADS1220_CONFIG`: `R_ref` è un resistore fisico
saldato sul circuito di misura.

**Per campioni con resistenza attesa fuori da questo range** (es. l'utente
ha indicato la necessità di coprire 1 Ω–10 kΩ per nanofibre con coating
conduttivo), due strade discusse ma non implementate:

1. **Sostituire il resistore di riferimento** con uno più grande (es.
   ~10-12 kΩ, misurato con precisione e aggiornato in `ADS1220_R_REF_OHM`),
   scelto in base al limite superiore atteso dei campioni. A `GAIN=1` questo
   coprirebbe l'intero range fino al nuovo `R_ref` in un colpo solo; il
   rumore/risoluzione dell'ADC a quel gain resta ampiamente sufficiente per
   risolvere anche il lato basso (1 Ω) del range, ma andrebbe confermato
   sperimentalmente col nuovo resistore installato.
2. **Banco di resistenze di riferimento commutabili** (idea proposta
   dall'utente, per coprire più decadi senza dover scegliere un singolo
   compromesso): un multiplexer analogico (es. CD4051, 8 canali) tra
   `REFP0`/`REFN0` e una serie di resistori (es. 10 Ω/100 Ω/1 kΩ/10 kΩ/100
   kΩ), pilotato da GPIO liberi, con firmware che seleziona/consiglia il
   canale in base all'ultima lettura. Non ancora scoperto un pin plan
   completo: sull'attuale mappa GPIO (vedi `CLAUDE.md`, tabella pinout) solo
   `GPIO15` risulta chiaramente libero e sicuro da riusare; pilotare anche
   solo 3 linee di selezione per un mux ad 8 canali richiederebbe liberare
   altri pin o aggiungere un espansore I2C/SPI. In più, la resistenza ON del
   mux (tipicamente decine di Ω, variabile con temperatura) si somma in
   serie al resistore selezionato e andrebbe caratterizzata/calibrata per
   non introdurre errore sistematico, soprattutto sulle decadi più basse.

Nessuna delle due strade è stata implementata in questa sessione: richiede
una decisione dell'utente su component/pin plan prima di procedere.

## Conteggio giri Z dell'encoder esterno, decodificato ma non esposto

**Stato attuale**: l'integrazione dell'encoder incrementale esterno (vedi
`CHANGELOG.md`) decodifica anche il canale Z (indice, un impulso a giro)
in `encoder_z_turns`, azzerato insieme a `encoder_position` a fine homing,
ma **non è esposto sul protocollo seriale** — nessun campo `D:` lo porta al
PC, nessun comando lo legge. È stato lasciato decodificato "per un
eventuale uso futuro" senza che quell'uso sia stato ancora definito.

**Possibili usi futuri** (non pianificati, solo idee raccolte durante
l'integrazione):
- Verifica di coerenza fra `encoder_z_turns` e lo spostamento stimato
  (giri interi attesi vs. giri effettivamente contati), come controllo
  indipendente di eventuali passi persi dal motore.
- Un homing/zero dedicato per l'encoder che non dipenda dalla sequenza di
  homing esistente basata sugli endstop meccanici.

Se nessuno di questi usi si materializza, valutare se rimuovere del tutto
la decodifica Z (oggi codice morto lato funzionalità, anche se a costo
quasi nullo) per ridurre la superficie del firmware.

## Jog encoder: verifica fisica pendente e possibile scaling per scatto (detent)

**Stato attuale**: pulsanti manuali Up/Down e jog encoder sono stati
integrati in firmware (vedi `CHANGELOG.md`, voce più recente) e il
firmware è stato caricato e verificato passivamente (boot pulito,
`GET_KILLSWITCH_STATE`/`GET_DATA` rispondono correttamente). **Non ancora
verificato con un uso fisico reale dei pulsanti/encoder** (nessun comando
di movimento inviato in questa sessione dopo l'upload, per non muovere la
traversa senza supervisione diretta).

**Da verificare fisicamente prima di un uso in produzione** (vedi
`CLAUDE.md`, sezione "Pulsanti manuali e jog encoder fisici", punto 4 dei
compromessi, per il dettaglio):
1. Verso di rotazione dell'encoder ora corretto (l'inversione era stata
   corretta "a tavolino" invertendo il segno in ISR, non ancora confermata
   girando fisicamente l'encoder).
2. Killswitch premuto mentre un pulsante è fisicamente tenuto premuto o
   l'encoder è a metà di un movimento a step: il motore deve fermarsi
   subito, e il sistema deve restare utilizzabile dopo (nessun blocco
   residuo dei comandi seriali).
3. Taratura dei 3 preset di step (0.05/0.01/0.005 mm nominali) rispetto al
   movimento realmente osservato.

**Possibile scaling per "scatto" (detent) dell'encoder**: il firmware oggi
applica `step_size_corrente` per ogni singolo conteggio di quadratura
grezzo (`jogEncoderCount`), non per scatto meccanico. Se l'encoder fisico
genera più conteggi per scatto (comune sugli encoder economici EC11-style,
tipicamente 4 conteggi/scatto), il movimento per click risulterebbe un
multiplo (es. ×4) del preset nominale. Da verificare al punto 3 sopra; se
confermato, valutare se dividere il delta per un fattore
`JOG_ENCODER_COUNTS_PER_DETENT` (nuova costante) prima di convertirlo in
passi, invece di ritarare solo i tre preset per compensare.

## Altri punti aperti (dai `docs/*.md` e da `CLAUDE.md`)

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
