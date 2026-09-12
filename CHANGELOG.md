# Changelog

Riepilogo concettuale dei cambiamenti architetturali e delle correzioni
rilevanti al progetto. Non è un log riga-per-riga dei commit: per quello si
veda la cronologia git. Ogni voce spiega **cosa** è cambiato e **perché**.

## 2026-07-21

### Feature: ADS1220 (ADC SPI) integrato per la misura di resistenza dei campioni, canale alternativo all'LCR-meter

Cablaggio già fatto e verificato in una sessione di pianificazione precedente
(non toccato qui): regolatore LP5907 per `AVDD`/`DVDD`, circuito di misura
ratiometrico a 4 fili (`AIN0`/`AIN1` su un elettrodo, `AIN2`/`AIN3` sull'altro,
`AIN3` cortocircuitato a `REFP0`, resistenza di riferimento **misurata**
`R_ref = 989.58 Ω` fra `REFP0`/`REFN0`), SPI su `CS`=GPIO5, `SCLK`=GPIO21,
`MOSI`=GPIO26, `MISO`=GPIO36 (pin già riservati in precedenza, ora spostati
dalla sezione "non ancora cablati" a "cablati e integrati" in `CLAUDE.md`).
**Non è un canale di sicurezza**: sola lettura (Livello 1), stesso
trattamento del canale encoder esterno — non tocca limiti assoluti né
criteri di stop.

**Ragionamento sul DRDY (perché non serve un pin dedicato)**: il chip non ha
un GPIO libero per il pin `DRDY` (GPIO4, l'unico "naturale", è già `DIR_PIN`
del motore). Verificato dal datasheet ADS1220 che in modalità di conversione
continua (`CM=1`) i dati possono essere letti in qualunque momento via
`RDATA` senza rischio di corruzione, riflettendo sempre l'ultima conversione
completata — quindi si interroga a intervalli (rate-limitati al sample rate
configurato) invece che via interrupt hardware, senza perdita di
correttezza (al più si rilegge lo stesso campione due volte).

**Correzioni ai valori di registro rispetto a descrizioni imprecise
circolate prima della sessione di implementazione**: i valori di registro
usati (`Reg0=0x38`, `Reg1=0x84`, `Reg2=0x47`, `Reg3=0x20`) sono stati
riverificati bit-per-bit contro il datasheet reale prima di scriverli nel
firmware — in particolare il guadagno PGA di default è **16x, non 1x** come
descritto in una versione precedente non verificata, e il filtro 50/60Hz
(`Reg2` bit 5:4) resta **sempre spento** (obbligatorio per SPS≠20 in Normal
mode, tenuto spento anche a 20 SPS per semplicità, dato che nessun caso
d'uso richiede il filtro attivo).

**Firmware** (`Controllo-Macchina-ESP32/src/main.cpp`):
- Driver SPI a basso livello (`SPIClass` su pin custom via GPIO matrix,
  `ads1220WriteReg()`/`ads1220ReadData()`) e `applyAds1220Config()` che
  traduce i parametri correnti (`ads1220_sps`/`gain`/`pga_bypass`/`idac_ua`)
  nei 4 byte di registro e riavvia le conversioni (`START/SYNC`,
  necessario dopo una scrittura ai registri in continuous conversion mode).
  `PGA_BYPASS` forzato a `false` (`ads1220_pga_bypass_applied`) se
  `gain>=8`, obbligatorio da datasheet, qualunque fosse la richiesta.
- `updateADS1220Reading()`, chiamata da `loop()` come `updateLCRReading()`:
  rate-limitata a `1000/SPS` ms, calcola `R_x = (rawData / (2^23 * gain)) *
  R_ref` (l'IDAC non entra nella formula, si semplifica ratiometricamente)
  e applica una media mobile su buffer circolare (`ads1220_window`
  campioni, default 10, max 20).
- Comandi nuovi: `ENABLE_ADS1220_POLLING`/`DISABLE_ADS1220_POLLING` (stesso
  pattern degli equivalenti LCR); `SET_ADS1220_CONFIG:SPS=..;GAIN=..;
  PGA_BYPASS=..;IDAC=..;WINDOW=..` (validazione atomica come
  `SET_FILTER_CONFIG`, **rifiutato** con
  `STATUS:ADS1220_CONFIG_REJECTED;REASON=POLLING_ACTIVE` se il canale è
  attualmente in polling — evita discontinuità/artefatti su un test in
  corso); `GET_ADS1220_CONFIG`.
- Pacchetto `D:` esteso da 6 a **7 campi**: rinominato concettualmente il
  5° campo in `RES_LCR` (invariato) e aggiunto un 7° campo `RES_ADS` in
  coda (stesso schema di sentinel: `-999`=disabilitato, `-2`=errore
  parsing). Aggiunto sia alla risposta di `GET_DATA` sia allo streaming.

**Python**:
- `main.py`: parsing esteso a 7 campi (con tolleranza retrocompatibile
  alle varianti storiche a 3/4/5/6, come già per l'encoder); propaga
  entrambi i valori grezzi (`current_resistance_lcr_ohm`/
  `current_resistance_ads_ohm`) a tutti i widget; gestisce
  `STATUS:ADS1220_CONFIG_SET`/`_REJECTED`/`ADS1220_CONFIG` (allinea lo
  stato GUI ai valori **realmente applicati** dal firmware, segnalando
  esplicitamente con un popup se `PGA_BYPASS` è stato ignorato per
  `gain>=8x`); traccia centralmente `active_resistance_source` (via nuovo
  segnale `resistance_source_changed` emesso da ciascun widget) al solo
  scopo di disabilitare il dialog "ADS1220 Settings" mentre il canale è
  attivo su una qualunque schermata.
- `manual_control_widget.py`/`monotonic_test_widget.py`/
  `cyclic_test_widget.py`: il checkbox "Enable LCR Reading" è sostituito
  da un combo box "Sorgente Resistenza" ("Off"/"LCR"/"ADS1220"), stesso
  schema nelle tre schermate; il cambio selezione invia sempre la coppia
  `ENABLE_*`/`DISABLE_*` corretta (mai LCR e ADS1220 richiesti attivi
  insieme). Il grafico/display "Resistance (Ω)" esistente (già generico,
  non richiedeva modifiche) è ora alimentato da qualunque dei due campi
  arrivi, in base alla sorgente selezionata — nessun overlay delle due
  curve (a differenza di Motor/Encoder displacement): sono alternative,
  non complementari. Le tuple dati (`current_test_data`/`recorded_data`)
  guadagnano un campo finale con la sorgente attiva ("LCR"/"ADS1220"/
  "OFF"), senza spostare gli indici esistenti (append in coda).
- `custom_widgets.py`: nuovo `ADS1220ConfigDialog` (sul modello di
  `FilterConfigDialog`), aperto da un nuovo pulsante "ADS1220 Settings"
  nel menu principale; `MainWindow` lo apre solo se il canale non è
  attualmente attivo, con un messaggio esplicativo altrimenti (il
  firmware lo rifiuterebbe comunque, ma evitiamo di far compilare un form
  che verrà scartato).
- `data_saver.py`: nuova colonna "Resistance Source" accanto a
  "Resistance (Ohm)" nei file Excel esportati.
- `settings_manager.py`: nuova chiave `ads1220_config` persistita in
  `settings.json` (default allineati ai valori di registro sopra).

**Validazione timing richiesta (jitter `STREAM_INTERVAL_MS` e cadenza di
step motore con canale ADS1220 attivo a piena velocità di traversa):
NON eseguita** — nessun accesso alla macchina fisica in questa sessione.
Stima teorica soltanto (vedi `CLAUDE.md`, sezione ADS1220, e `TODO.md`):
una lettura `RDATA` a 1 MHz SPI dura nell'ordine di poche decine di µs,
rate-limitata a `1000/SPS` ms, quindi impatto atteso trascurabile su
`STREAM_INTERVAL_MS` (20 ms); il timer hardware dei passi motore non è
comunque influenzato da un `loop()` più lento, essendo un interrupt
hardware indipendente. **Da verificare fisicamente prima di un uso in
produzione ad alta velocità con questo canale abilitato**, idealmente
insieme alla misura (anch'essa mai fatta) dell'overhead delle ISR encoder
già segnalata in `TODO.md`.

### Fix: ADS1220 verificato su hardware reale — bug di parsing `SET_ADS1220_CONFIG` e scoperta del limite di range `R_ref/gain`

Seguito della feature sopra, con accesso reale alla macchina in questa
sessione (a differenza della validazione timing, ancora non eseguita — vedi
sotto): firmware compilato ma **non caricato** al primo tentativo (`pio run`
senza `-t upload`, stesso errore già documentato al punto critico 11 di
`CLAUDE.md`), poi effettivamente flashato e testato dal vivo.

**Bug 1 — `SET_ADS1220_CONFIG` sempre rifiutato con `REASON=OUT_OF_RANGE`**:
il parsing usava `command.substring(20)` per isolare i parametri dopo il
prefisso, ma `"SET_ADS1220_CONFIG:"` è lunga **19** caratteri, non 20 — il
primo carattere (`S` di `SPS=`) veniva scartato, `params.indexOf("SPS=")`
falliva sempre (`-1`), e la validazione falliva di conseguenza qualunque
fossero i valori inviati. Riprodotto subito alla prima connessione reale
(la GUI invia `SET_ADS1220_CONFIG` automaticamente dopo la connessione).
Corretto in `command.substring(19)`.

**Bug 2 (falso allarme, diagnosticato con un comando temporaneo) — lettura
"bloccata" a 61.8487 Ω anche scollegando fisicamente il campione**: aggiunto
un comando diagnostico temporaneo `DEBUG_ADS1220` (rilegge i 4 registri via
`RREG` più un campione `RDATA` immediato, a prescindere dal polling) per
verificare se le scritture `WREG` di `applyAds1220Config()` arrivassero
davvero al chip. Risultato: registri **corretti** (`0x38 0x84 0x47 0x20`,
esattamente quelli attesi), ma `RAW=8388607` — cioè `2^23-1`, fondo scala
positivo. Non è un bug: è la conferma di un vincolo fisico della misura
ratiometrica, non documentato esplicitamente prima di questa verifica:

```
R_x,max = R_ref / gain   (indipendente da IDAC)
```

Con `R_ref = 989.58 Ω` e `gain=16` (default), `R_x,max ≈ 61.85 Ω` — che è
esattamente il valore "bloccato" osservato: qualunque resistenza (o un
circuito aperto, resistenza infinita) pari o superiore a quella soglia
satura allo stesso identico codice di fondo scala, quindi rimuovere il
campione non cambia nulla se la lettura era già satura. **Verificato poi
con successo collegando un resistore di valore basso** (ben sotto i 62 Ω):
lettura corretta e non più bloccata. `DEBUG_ADS1220` resta nel firmware
come comando diagnostico temporaneo (non in tabella comandi "ufficiale" di
`CLAUDE.md`, da valutare se rimuovere in seguito).

**Implicazione pratica per l'uso reale**: con l'attuale `R_ref = 989.58 Ω`
il range massimo assoluto (a `GAIN=1`, il minimo disponibile) è **~990 Ω** —
non raggiungibile oltre, qualunque configurazione si scelga. Per campioni
con resistenza attesa fino a 1 Ω–10 kΩ, come discusso con l'utente, servirebbe
un resistore di riferimento fisicamente più grande (es. ~10-12 kΩ, misurato
con precisione e riportato in `ADS1220_R_REF_OHM`) — cambio hardware non
ancora fatto. Discussa anche l'idea di un banco di resistenze di riferimento
commutabili via multiplexer analogico per coprire più decadi dinamicamente,
non implementata (vedi `TODO.md`): richiede GPIO liberi (scarsi sull'attuale
mappa pin) e caratterizzazione della resistenza ON del mux.

**Validazione timing (jitter `STREAM_INTERVAL_MS`/cadenza step a canale
ADS1220 attivo): ancora NON eseguita** — l'accesso alla macchina in questa
sessione è stato usato solo per diagnosi via seriale diretta (letture
ferme, motore non movimentato), non per un test a piena velocità di
traversa. Resta un passo esplicito da fare, vedi `TODO.md`.

### Feature: pulsanti manuali Up/Down fisici e jog encoder a step fini, integrati in firmware (con display GUI del preset)

Seguito della voce "Hardware: pulsanti manuali, jog encoder e killswitch —
cablati e verificati, logica firmware non ancora scritta" (più sotto in
questa stessa data): scritta la logica firmware che legge questo hardware
(il killswitch era già stato integrato in una sessione precedente, non
toccato qui se non per leggerne lo stato). Nessuna nuova logica di
movimento: pulsanti ed encoder riusano deliberatamente le stesse funzioni
(`startMotor()`/`stopMotor()`) già usate da `JOG_UP`/`JOG_DOWN` via seriale.

**Firmware** (`Controllo-Macchina-ESP32/src/main.cpp`):
- Nuova guardia di attivazione condivisa, unico punto di verità,
  `isManualJogAllowed()`: `motor_state == STOPPED` **e**
  `target_steps_remaining == 0` (esclude anche un `GOTO`/`RETURN_TO_START`
  in corso, che lascia `motor_state == STOPPED` — controllo più stringente
  di quello usato oggi da `JOG_UP`/`JOG_DOWN` seriale, introdotto solo per i
  nuovi input fisici) **e** `!killswitch_engaged` (non controlla
  `position_unverified`: in stato "giallo" il jog fisico resta utilizzabile,
  come già per il jog seriale). Usata sia dai pulsanti sia dall'encoder,
  nessuna copia duplicata.
- `handleHardwareInputs()` riabilitata (era commentata/disabilitata,
  `//handleHardwareInputs(); TEMPORANEAMENTE DISABILITATO TASTI FISICI`):
  polling nel `loop()`, debounce software a transizione ~25ms per
  `UP_BUTTON_PIN`/`DOWN_BUTTON_PIN`. Pressione (se la guardia lo permette):
  `motor_state = JOG_UP`/`JOG_DOWN` + `startMotor()`. Rilascio: ferma
  esattamente come il rilascio del jog da GUI (`motor_state = STOPPED`,
  `stopMotor()`, `STATUS:STOPPED_BY_USER`), tranne se il motore era già
  stato fermato nel frattempo da un'altra causa (endstop, limite,
  killswitch), nel qual caso richiude solo la contabilità senza duplicare
  il messaggio. Bandierine di "proprietà" per pulsante
  (`up_button_owns_jog`/`down_button_owns_jog`), necessarie per evitare che
  `is_hardware_jog_active` (già esistente, blocca `JOG_UP`/`JOG_DOWN`/
  `HOME`/`SET_SPEED`/`GOTO` seriali durante un jog fisico) resti bloccata a
  `true` per sempre se il killswitch ferma il motore mentre il pulsante è
  ancora fisicamente premuto — bug potenziale individuato scrivendo questa
  logica, risolto senza toccare la logica del killswitch stessa (vedi
  `CLAUDE.md`, sezione dedicata, per il dettaglio).
- Jog encoder: decodifica quadratura su interrupt `CHANGE` su
  `JOG_ENCODER_A`/`JOG_ENCODER_B` (`handleJogEncoderChange()`, ISR minimale,
  riusa la stessa tabella di decodifica generica `ENCODER_QUAD_TABLE` già
  esistente per l'encoder esterno). **Verso di conteggio invertito** rispetto
  al segno "naturale" della tabella, per correggere l'inversione verificata
  fisicamente in una sessione precedente (sottrazione del delta invece di
  somma, equivalente allo scambio dei pin A/B ma senza toccare il cablaggio).
  Consumo nel `loop()` (`handleJogEncoderMotion()`): converte l'intero delta
  accumulato in un movimento relativo di `delta × step_size_corrente`,
  impostando `motor_state = JOG_UP`/`JOG_DOWN` (**non** il meccanismo a passi
  contati `target_steps_remaining` di `GOTO`/`RETURN_TO_START`, che salta il
  controllo endstop ad ogni giro di loop) — scelta deliberata per ereditare
  esattamente lo stesso controllo di endstop e limiti assoluti del jog
  normale, monitorando un target di `pulse_count` assoluto per fermarsi da
  solo. Se la guardia non è soddisfatta o un movimento precedente è ancora
  in corso, il delta resta accumulato (letto/sottratto atomicamente in
  sezione critica) senza essere perso né duplicato.
  Velocità dedicata fissa e più bassa (`JOG_ENCODER_SPEED_MMS = 0.5 mm/s`,
  costante regolabile) per non perdere passi su spostamenti così piccoli:
  la velocità del timer passi viene salvata prima di ogni step e ripristinata
  subito dopo (anche se il movimento viene interrotto da endstop/limite/
  killswitch), per non lasciare la macchina a velocità ridotta per i jog
  successivi.
- Tre preset di step ciclati dal pulsante integrato dell'encoder
  (`JOG_ENCODER_SW`, debounce dedicato ~50ms, indipendente da quello di
  rotazione): costanti regolabili in testa al file
  (`JOG_STEP_SIZE_FINE_MM=0.05`, `JOG_STEP_SIZE_VERY_FINE_MM=0.01`,
  `JOG_STEP_SIZE_FINEST_MM=0.005`), convertite in passi motore con la
  costante `PULSES_TO_MM` già esistente (nessuna nuova costante meccanica).
  Alla pressione, nuovo messaggio `STATUS:JOG_STEP_SIZE_SET;MM=<valore>`.

**Python**: `MainWindow.handle_data_from_esp32()` inoltra
`STATUS:JOG_STEP_SIZE_SET` a un nuovo
`ManualControlWidget.set_jog_step_size()`, che aggiorna un nuovo
`DisplayWidget` ("Jog Encoder Step (mm)") — puro display informativo,
nessuna azione lato GUI: il preset è gestito interamente dal firmware e
funziona anche a GUI chiusa/PC scollegato, come da richiesta.

**Verifica effettuata**: compilazione firmware (`pio run`, successo, RAM
6.9% / Flash 24.5%, footprint sostanzialmente invariato); sintassi Python
verificata (`py_compile` su `main.py` e `manual_control_widget.py`). Nessun
hardware reale disponibile in questa sessione per verificare fisicamente:
l'inversione A/B corretta, la taratura dei preset di step, e il
comportamento del killswitch premuto durante un jog fisico attivo — tutti
da fare come prossimo passo prima di un uso reale (vedi `CLAUDE.md`,
sezione dedicata, "Compromessi e rischi residui noti", per il dettaglio
completo, incluso un possibile fattore ×4 tra conteggi di quadratura grezzi
e "scatti" meccanici dell'encoder se questo genera 4 conteggi/scatto).

### Diagnosi: "software non funziona" dopo il primo flash — causa non software (scheda mai flashata, poi guasto hardware sul killswitch)

Dopo l'integrazione del killswitch (voce successiva in questo changelog),
l'utente ha segnalato che l'intero software non funzionava più: GUI
apparentemente connessa ma nessun dato in calibrazione, nessun movimento
motore (nemmeno l'homing). Diagnosticato passo per passo, senza modificare
codice all'inizio:

1. Ascolto passivo della porta seriale (nessun comando di movimento
   inviato): **zero byte ricevuti dall'ESP32**, né al boot né in risposta a
   `GET_DATA`/`GET_KILLSWITCH_STATE`. Causa: il firmware con il killswitch
   era stato solo **compilato**, mai **caricato** sulla scheda in questa
   sessione — quello effettivamente in esecuzione non emetteva output
   utilizzabile (stato imprecisato, indipendente dalle modifiche fatte).
2. Verificato con `esptool.py chip_id` (comunica col bootloader ROM,
   nessuna modifica alla flash) che il chip stesso era vivo e rispondeva
   correttamente — escludendo una scheda danneggiata o USB non funzionante.
3. Con conferma esplicita dell'utente, caricato il firmware compilato
   (`pio run -t upload`): da quel momento boot pulito, `GET_DATA` e
   `GET_KILLSWITCH_STATE` rispondono correttamente. Causa risolta.

**Secondo problema, emerso testando lo scenario "boot con killswitch
premuto"**: indicatore rimasto verde anche a killswitch tenuto premuto, e
successivi azionamenti fisici del killswitch completamente ignorati
("sordo"). Diagnosticato aggiungendo un comando seriale temporaneo
(`DEBUG_RAW_KILLSWITCH`, poi rimosso) che legge `digitalRead(KILLSWITCH_SENSE_PIN)`
**direttamente**, bypassando interrupt/debounce/state machine. Interrogato
a polling (4 volte/secondo) per 45s mentre l'utente premeva/rilasciava
fisicamente il killswitch: il pin è risultato **fisso a LOW per l'intera
finestra**, e il timestamp dell'ultimo fronte HIGH mai aggiornato dal
valore di boot — cioè il segnale elettrico non arrivava mai a GPIO39,
indipendentemente dall'azione fisica sul pulsante. Questo esclude un bug
nella logica software (interrupt/debounce/`updateKillswitchState()`,
introdotta nella voce successiva): non c'era nulla su cui quella logica
potesse reagire. Causa isolata a un problema elettrico/di cablaggio a monte
di GPIO39 (connettore, pull-up esterno, o contatto meccanico del pulsante
stesso) — plausibilmente disturbato durante i ripetuti spegni/riaccendi e
maneggiamenti di cavi fatti proprio per testare quello scenario. Dopo
verifica/sistemazione della meccanica da parte dell'utente, il
comportamento è tornato corretto; causa esatta della disconnessione non
identificata con precisione (va tenuta presente se il sintomo dovesse
ripresentarsi: rifare lo stesso test con `DEBUG_RAW_KILLSWITCH` se serve
ridiagnosticare, reintroducendolo temporaneamente).

Nessuna modifica alla logica killswitch è stata necessaria: il codice
descritto nella voce precedente (sensing, debounce, `position_unverified`,
gating comandi) si è comportato correttamente per tutta la diagnosi.

### Feature: killswitch integrato in firmware, protocollo e GUI (sensing, stato "posizione non verificata", abort prova, log eventi)

Seguito della voce precedente ("Hardware: ... killswitch — cablati e
verificati, logica firmware non ancora scritta"): in questa sessione il
killswitch è stato integrato end-to-end — sensing/debounce firmware,
gating dei comandi di movimento, protocollo seriale, indicatori GUI e un
nuovo log eventi di sistema. **Scope esplicitamente escluso da questa
sessione**: la logica di lettura dei pulsanti manuali fisici e del jog
encoder (pianificata per una sessione successiva) — dove il killswitch deve
interagire con quel futuro jog fisico, è stato predisposto il gating
(`killswitch_engaged`, variabile globale già riusabile) senza scrivere la
logica di lettura fisica.

**Firmware** (`Controllo-Macchina-ESP32/src/main.cpp`):
- Sensing su `KILLSWITCH_SENSE_PIN`=GPIO39 tramite interrupt `CHANGE`
  sempre attivo (indipendente da `motor_state`/`comms_mode`), con debounce
  **asimmetrico**: pressione (HIGH) rilevata subito nell'ISR, rilascio
  (LOW) confermato solo dopo ~200ms di stato stabile
  (`KILLSWITCH_RELEASE_DEBOUNCE_MS`), per non scambiare un rimbalzo
  meccanico di rilascio per un rilascio vero. Implementato con un solo
  timestamp (`killswitch_last_high_ms`, aggiornato a ogni fronte HIGH
  anche durante un rimbalzo) confrontato con `millis()` in
  `updateKillswitchState()`, chiamata per prima a ogni giro di `loop()`.
- Nuova variabile di stato `position_unverified`: `true` alla pressione del
  killswitch, o già `true` dal **boot** se il killswitch risulta premuto
  all'accensione (letto in `setup()` prima di attaccare l'interrupt — il
  firmware invia comunque `STATUS:KILLSWITCH_TRIGGERED` in quel caso). Resta
  `true` durante il rilascio: solo un **HOME completato con successo** la
  riporta a `false` (impostato alla fine di `HOMING_FINAL_LIFT`, prima di
  `STATUS:HOMING_COMPLETED`). A un boot pulito (killswitch non premuto)
  parte `false`, coerentemente con il comportamento preesistente del resto
  del sistema (l'homing non era già altrimenti imposto a ogni riavvio).
- Alla pressione (`onKillswitchTriggered()`): stesso path già usato dallo
  stop di emergenza `!` esistente (`motor_state=STOPPED`,
  `comms_mode=POLLING`, `stopMotor()`, `target_steps_remaining=0`),
  applicato qualunque fosse `motor_state` (jog, homing, test monotonico o
  ciclico). Invia sempre `STATUS:KILLSWITCH_TRIGGERED` e, se un test era in
  corso, anche `STATUS:TEST_ABORTED;REASON=KILLSWITCH` (nome scelto per
  coerenza con lo stile esistente a `;CHIAVE=VALORE`, es.
  `CALIBRATION_INVALIDATED;REASON=GAIN_CHANGED`, invece della forma
  `TEST_ABORTED:KILLSWITCH` proposta come esempio nella richiesta). Al
  rilascio confermato: `STATUS:KILLSWITCH_CLEARED`.
- Comandi bloccati mentre `position_unverified == true` (stato "giallo" se
  il killswitch non è premuto ORA, "rosso" se lo è): `START_TEST`/
  `START_CYCLIC_TEST` (→ `STATUS:TEST_START_REJECTED;REASON=POSITION_UNVERIFIED`)
  e `GOTO` (→ `STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED`). `HOME`
  resta **sempre permesso** (unico modo per uscirne). `JOG_UP`/`JOG_DOWN`
  sono gated separatamente su `killswitch_engaged` (non su
  `position_unverified`): permessi in stato giallo, bloccati solo in stato
  rosso (→ `STATUS:JOG_REJECTED;REASON=KILLSWITCH_ENGAGED`) — scelta
  deliberata per riflettere fin da ora la regola richiesta anche per i
  futuri jog fisici, che dovranno riusare la stessa variabile.
- Nuovo comando `GET_KILLSWITCH_STATE` → risponde
  `STATUS:KILLSWITCH_STATE;ENGAGED=0|1;POSITION_UNVERIFIED=0|1`, per
  permettere alla GUI di allinearsi allo stato reale subito dopo la
  connessione anche su schede che non resettano all'apertura porta seriale
  (il reset-on-connect già noto, vedi punto critico 3, copre invece il caso
  più comune tramite l'invio di boot di `KILLSWITCH_TRIGGERED`).

**Python — protocollo/stato** (`main.py`): `MainWindow` traccia
`killswitch_engaged`/`position_unverified`, aggiornati dai nuovi messaggi
`STATUS:` sopra; `GET_KILLSWITCH_STATE` inviato in
`_send_post_connect_commands()` subito dopo la connessione.

**Python — GUI**:
- Indicatore a 3 livelli (`KillswitchIndicatorWidget`, verde/giallo/rosso)
  nella barra superiore di `MainWindow` (fuori da `QStackedWidget`, quindi
  visibile su ogni schermata) e nei dialoghi `LIMITS`/`Filter Config`
  (le uniche altre finestre dell'app), registrati/deregistrati in
  `MainWindow._killswitch_indicators` all'apertura/chiusura per restare
  aggiornati anche a dialog aperto.
- Banner persistente (`KillswitchBannerWidget`), in aggiunta al popup e non
  alternativo: resta visibile finché lo stato non torna verde.
- Popup non bloccante su `KILLSWITCH_TRIGGERED` (`QMessageBox` con
  `WindowModality.NonModal` + `.show()` invece di `.exec()`), per non
  impedire l'uso del resto del software mentre è aperto.
- Gating controlli movimento: avvio prova e GOTO disabilitati in
  `MonotonicTestWidget`/`CyclicTestWidget` mentre `position_unverified`;
  Up/Down (software) disabilitati in questi due widget e in
  `ManualControlWidget` solo mentre `killswitch_engaged`. Homing,
  impostazioni, calibrazione, dati salvati e connessione/disconnessione
  seriale restano sempre utilizzabili, come richiesto. Doppia difesa:
  oltre alla disabilitazione dei pulsanti, gli handler `on_start_test()`/
  `toggle_goto()` ricontrollano esplicitamente lo stato (il firmware lo
  rifiuterebbe comunque).
- Su `STATUS:TEST_ABORTED;REASON=KILLSWITCH`: `on_stop_test(user_initiated=False,
  abort_reason="KILLSWITCH")` su qualunque widget di test avesse
  `is_test_running`, che finalizza/autosalva il provino **mantenendo tutti i
  dati già raccolti** (nessun troncamento), con nome file
  `AUTOSAVE_KILLSWITCH_ABORTED_...`/`AUTOSAVE_CYCLIC_KILLSWITCH_ABORTED_...`
  invece di `AUTOSAVE_...`, e una riga `Test Status: INTERRUPTED —
  KILLSWITCH` in grassetto/rosso scritta da `DataSaver` nella sezione
  parametri del foglio Excel (nuovo campo opzionale `abort_reason` sul
  provino, `None` nel caso normale — non aggiunge nulla al file esistente).
  Scelta deliberata non esplicitamente richiesta: se il provino monotonico
  aveva "Return to start" attivo, l'invio automatico di `RETURN_TO_START`
  viene saltato in questo caso specifico, per non far muovere
  automaticamente il motore subito dopo un'emergenza prima che l'operatore
  rifaccia l'homing (in aggiunta al gating firmware descritto subito sotto).

### Fix: `RETURN_TO_START` non era gated da `position_unverified` a livello firmware

Segnalato dall'utente subito dopo la voce precedente: `RETURN_TO_START` usa
lo stesso meccanismo a passi contati basato su `pulse_count` di `GOTO`
(stesso `target_steps_remaining`), quindi soffre dello stesso problema se la
posizione non è verificata (killswitch attivato in precedenza, homing non
ancora ripetuto) — ma solo `GOTO` aveva la guardia firmware corrispondente;
`RETURN_TO_START` era mitigato solo lato GUI (skip dell'invio automatico
dopo un abort da killswitch, vedi voce precedente), quindi restava invocabile
senza restrizioni se richiamato altrimenti.

Aggiunta in `Controllo-Macchina-ESP32/src/main.cpp` la stessa guardia già
usata per `GOTO`: `RETURN_TO_START` viene rifiutato
(`STATUS:GOTO_REJECTED;REASON=POSITION_UNVERIFIED`) mentre
`position_unverified == true`. Nome del messaggio di rifiuto riusato
deliberatamente (non un nuovo `RETURN_TO_START_REJECTED`): stessa categoria
di comando (movimento assoluto a passi contati basato su `pulse_count`),
stesso motivo di rifiuto — coerente con il precedente già stabilito da
`TEST_START_REJECTED`, condiviso tra `START_TEST` e `START_CYCLIC_TEST`.
Verificato con `pio run` (compilazione riuscita, footprint invariato).

**Python — nuovo log eventi di sistema** (`event_logger.py`, nuovo modulo):
`EventLogger`, log JSON Lines append-only in `event_log.jsonl` (cartella
dell'app, accanto a `settings.json`), **separato dai dati di misura delle
prove** (mai forza/spostamento/tempo dei test, solo eventi discreti con
timestamp). Eventi loggati in questa sessione: `killswitch_triggered`,
`killswitch_cleared`, `homing_completed`, `test_aborted_killswitch` (con
provino e tipo test), `serial_connected`/`serial_disconnected`,
`calibration_invalidated`. Pensato come infrastruttura generale per futuri
eventi di sistema, non solo per il killswitch.

**Verifica effettuata**: compilazione firmware (`pio run`, successo, RAM
6.9% / Flash 24.4%). Nessun hardware reale disponibile in questa sessione:
verificato invece con un banco di test headless lato Python (`QT_QPA_PLATFORM=offscreen`)
che inietta i pacchetti `STATUS:` direttamente in
`handle_data_from_esp32()`, coprendo:
- Trigger/clear/homing da stato **idle**: transizioni verde→rosso→giallo→verde,
  indicatore, banner, e abilitazione controlli (start/goto disabilitati in
  giallo e rosso, Up/Down disabilitati solo in rosso) tutte corrette.
- Trigger **durante un homing attivo** lato GUI: UI di homing resettata
  correttamente (stesso comportamento già esistente per `STOPPED_BY_USER`),
  Up/Down restano disabilitati finché il killswitch resta premuto.
- Trigger **durante una prova monotonica** con dati già accumulati: dopo
  `TEST_ABORTED;REASON=KILLSWITCH`, `is_test_running` torna `False`, i dati
  raccolti restano tutti presenti sul provino, `abort_reason="KILLSWITCH"`
  viene salvato, e viene creato un file con il prefisso atteso — verificato
  anche il contenuto del foglio Excel risultante (`Test Status: INTERRUPTED
  — KILLSWITCH` presente e in grassetto).
- Boot con killswitch già premuto: simulato tramite
  `STATUS:KILLSWITCH_STATE;ENGAGED=1;POSITION_UNVERIFIED=1` (il percorso che
  la GUI userebbe realmente su schede che non resettano alla connessione) —
  stato risultante correttamente rosso con `position_unverified=True`.

Non ancora verificato con la macchina fisica collegata: **prossimo passo
prima di un uso reale** è testare fisicamente i tre scenari (idle, homing,
test in corso) premendo il killswitch reale, e confermare a voce/acusticamente
(come già fatto per la sola verifica hardware) che il motore si ferma
immediatamente in tutti i casi.

**Compromessi e rischi residui noti** (vedi anche `CLAUDE.md`, sezione
Killswitch, per il dettaglio):
1. Lag di rilascio fino a ~200ms (debounce voluto) — nessun impatto sulla
   sicurezza, solo un ritardo nell'aggiornamento di indicatore/banner.
2. Un boot pulito (killswitch non premuto) non forza l'homing:
   `position_unverified` parte `false` come già accadeva implicitamente in
   tutto il resto del sistema — non introdotto né corretto da questa modifica.
3. ✅ **[RISOLTO, stessa giornata]** `RETURN_TO_START` non era gated da
   `position_unverified` a livello firmware — vedi voce successiva in
   questo changelog per il fix.
4. Se il killswitch scatta mentre un dialog modale (`LIMITS`/`Filter
   Config`) è aperto, il popup non modale potrebbe restare dietro o non
   ricevere subito il focus finché il dialog non viene chiuso — limitazione
   nota, non risolta in questa sessione.

### Hardware: pulsanti manuali, jog encoder e killswitch — cablati e verificati, logica firmware non ancora scritta

Aggiunto e cablato fisicamente nuovo hardware di controllo/sicurezza
manuale, verificato con sketch di test standalone isolati (non ancora
integrati in `main.cpp`): un encoder di jog meccanico con pulsante
integrato, un killswitch hardware E-stop sulla linea di potenza +48V, e
verifica fisica dei pulsanti manuali Up/Down già previsti nel firmware.
Questa voce documenta **solo lo stato hardware verificato**: nessuna riga
di `main.cpp` è stata modificata in questa sessione — la logica firmware
che userà questi pin (lettura quadratura del jog encoder, gestione
pulsante, reazione al killswitch) è pianificata per una sessione
successiva. Vedi `CLAUDE.md` (sezione "Pinout ESP32") per il riepilogo
tabellare di tutti i pin, cablati e riservati.

**Pulsanti manuali Up/Down** (`UP_BUTTON_PIN`=18, `DOWN_BUTTON_PIN`=19, pin
già presenti nel firmware): cablaggio fisico verificato, ciascuno tra il
proprio GPIO e GND, `INPUT_PULLUP` software, nessun resistore esterno. Pin
invariati; la funzione firmware che li legge (`handleHardwareInputs()`)
resta disabilitata/commentata come prima (vedi `docs/firmware_main.md`) —
non toccata in questa sessione.

**Jog encoder** (nuovo controllo manuale — da non confondere con l'encoder
esterno di misura spostamento Omron E6B2, già presente su
`ENCODER_PIN_A/B/Z`): encoder meccanico "nudo" 5 pin con pulsante
integrato, cablato su `JOG_ENCODER_A`=GPIO13, `JOG_ENCODER_B`=GPIO14
(comune a GND, entrambi `INPUT_PULLUP`), `JOG_ENCODER_SW`=GPIO25 (pulsante
tra GPIO e GND, `INPUT_PULLUP`). **Verificato fisicamente con sketch di
test: il verso di conteggio risulta invertito rispetto alla rotazione
fisica attesa** — da correggere nel firmware definitivo scambiando A/B
nella definizione dei pin oppure invertendo il segno dell'incremento nella
decodifica quadratura (equivalenti, nessun ricablaggio necessario).

**Killswitch** (nuovo sottosistema di sicurezza hardware): interruttore
E-stop fisico normalmente chiuso (NC), inserito in serie sulla linea di
potenza +48V a monte del driver motore ISV57T090S — l'apertura taglia
realmente l'alimentazione del driver, indipendentemente da qualunque logica
software. Snubber RC (100Ω + 100nF in serie) in parallelo ai contatti
COM/NC, per assorbire il picco induttivo del motore all'apertura. Stato
sentito lato logico tramite optoisolatore 4N25 (anodo da +48V lato driver
via due resistori 2.2kΩ 0.5W in serie, catodo a GND comune, diodo 1N4007 in
antiparallelo per protezione da inversione di polarità; collettore su
`KILLSWITCH_SENSE_PIN`=GPIO39 con pull-up 10kΩ esterno verso 3.3V —
necessario perché GPIO39 è un pin input-only senza pull interni, emettitore
a GND logico). **Logica di stato verificata fisicamente, verso non
intuitivo**: GPIO39=LOW è lo stato di riposo (48V presenti, normale),
GPIO39=HIGH è killswitch premuto (48V realmente tagliati) — confermato non
solo elettricamente ma anche acusticamente sul motore (il ronzio di
holding-current cessa quando il killswitch è premuto e riprende al
rilascio/riarmo).

**Scoperta sulla topologia di massa**, emersa ispezionando il convertitore
buck 48V→12V che alimenta l'encoder esterno di misura: è **non isolato**
(induttore singolo, due MOSFET, controller HY1707, nessun trasformatore —
topologia sincrona non isolata standard). Di conseguenza GND linea potenza
48V, GND buck 12V, GND encoder esterno e GND logico ESP32 sono tutti la
stessa rete elettrica: non esiste alcuna barriera di isolamento galvanico
reale nel sistema attuale. L'optoisolatore 4N25 del killswitch, in questo
contesto, non fornisce isolamento galvanico (la massa è comunque condivisa
altrove) — la sua funzione reale è tradurre in sicurezza il livello 48V
verso 3.3V logico, non isolare elettricamente i due domini. Da tenere
presente come possibile causa nota se in futuro emergesse
rumore/instabilità sulle letture di cella di carico o encoder (massa
condivisa col driver stepper, non necessariamente un difetto software).

**Riservati per lavoro futuro, non ancora cablati fisicamente**: pin scelti
per un futuro ADC esterno ADS1220 via SPI (`CS`=GPIO5, `SCK`=GPIO21,
`MOSI`=GPIO26, `MISO`=GPIO36). Nessun GPIO libero adatto rimasto per
`DRDY`: il piano è leggerlo via polling del registro di status su SPI
invece che via interrupt hardware.

## 2026-07-14

### Fix: il pulsante STOP principale non interrompeva un movimento "Go To"

Segnalato dall'utente subito dopo l'introduzione di "Go To" (vedi voce
successiva): "funziona tutto tranne lo STOP... mi pare che io non possa
nemmeno cliccarlo, sembra disattivato". Il pulsante STOP grande e rosso
(`self.stop_button`, quello già usato per interrompere un test) era
effettivamente **disabilitato** durante un Go To: la sua abilitazione in
`update_ui_for_test_state()` dipendeva solo da `is_test_running`, che un
Go To non imposta mai (di proposito: non è un test). Anche abilitandolo,
`on_stop_test()` sarebbe comunque uscita subito per lo stesso motivo
(`if not self.is_test_running: return`), quindi il click non avrebbe avuto
effetto.

L'unico modo per fermare un Go To era ricliccare il pulsante Go To stesso
(diventato "STOP" durante il movimento, per design) — funzionalmente
corretto, ma sorprendente per l'utente: c'è già un pulsante STOP grande e
rosso in vista, ed è naturale aspettarsi che fermi *qualunque* movimento in
corso, non solo i test.

Risolto in entrambi i widget: `update_ui_for_test_state()` ora abilita
`stop_button` anche quando `is_goto_active` è vero, e `on_stop_test()`
controlla `is_goto_active` come primo passo, chiamando una nuova
`_cancel_goto()` condivisa (la stessa logica già usata dal secondo click
sul pulsante Go To, ora estratta in un metodo unico per evitare
duplicazione) prima di valutare se c'è anche un test in corso da fermare.
Aggiunto anche, per coerenza, che `start_button` resta disabilitato durante
un Go To (prima non lo era): avviare un test mentre il motore sta ancora
eseguendo un movimento a passi contati verso una posizione assoluta non ha
senso e avrebbe potuto produrre un comportamento indefinito lato firmware.

Verificato con una simulazione della UI: click su Go To, poi click sul
pulsante STOP principale (non sul Go To stesso) mentre il movimento è
ancora in corso — conferma che ora invia `!`+`STOP` e ripristina
correttamente lo stato dei pulsanti. Non ancora riverificato con un
movimento reale sulla macchina fisica (il fix precedente di "Go To" lo era
stato, ma non copriva questo specifico pulsante).

### Aggiunta: controllo "Go To" (movimento verso una posizione assoluta) nei test monotonici e ciclici

Richiesto dall'utente: accanto ai controlli manuali Up/Down/Jog Speed già
presenti in alto a destra nelle schermate di test monotonico e ciclico,
serviva un modo per muovere la traversa direttamente a una posizione
assoluta (in mm, sempre >= 0, come da richiesta esplicita), alla velocità
impostata in "Jog Speed", con la garanzia che "STOP" interrompa sempre il
movimento in corso.

**Firmware** (`Controllo-Macchina-ESP32/src/main.cpp`): nuovo comando
`GOTO:<mm>`, accettato con la stessa condizione di
`JOG_UP`/`JOG_DOWN`/`HOME`/`SET_SPEED` (`motor_state == STOPPED`, nessun
jog hardware attivo). Riusa deliberatamente lo stesso meccanismo a passi
contati già usato da `RETURN_TO_START` (`target_steps_remaining` +
`motor_enabled`), senza introdurre un nuovo `MotorState`: `motor_state`
resta `STOPPED` per tutta la durata del movimento, esattamente come già
avviene per `RETURN_TO_START`. Risponde `STATUS:GOTO_STARTED`, o
`STATUS:MOVE_COMPLETED` subito se la posizione richiesta coincide già con
quella attuale. I controlli di sicurezza assoluti
(`absolute_max_pulse_count`/`absolute_max_force_grams`) e gli endstop
continuano ad applicarsi automaticamente, perché vengono valutati in
`updateMotorState()` prima dell'uscita anticipata legata a
`target_steps_remaining > 0`.

**Fix correlato, necessario per la correttezza della richiesta ("se premo
STOP deve fermarsi")**: sia il comando `STOP` sia lo stop di emergenza `!`
ora azzerano esplicitamente `target_steps_remaining` (oltre a fermare
subito i passi con `stopMotor()`, invariato). Prima di questo fix,
interrompere un movimento a passi contati (`GOTO`, ma lo stesso valeva già
per `RETURN_TO_START`) a metà lasciava `target_steps_remaining` diverso da
zero: il comando di movimento *successivo* (es. un `JOG_UP`) avrebbe
rischiato di veder "risucchiati" i passi residui del movimento interrotto
(nella direzione vecchia, non quella del nuovo comando), fermandosi da solo
dopo quel tanto di passi invece di continuare come richiesto. Non era mai
emerso prima perché nessuna funzionalità esistente dava tipicamente motivo
di interrompere un movimento a passi contati e inviarne subito un altro;
"Go To" lo rende un caso d'uso comune, quindi il fix è diventato necessario.

**Python** (`monotonic_test_widget.py`, `cyclic_test_widget.py`,
identici): nuovo `goto_position_spinbox` (mm, range `[0, 190]` — lo stesso
limite fisico macchina già usato in "LIMITS") e `goto_button`, accanto ai
controlli Up/Down/Jog Speed esistenti. Il pulsante segue lo stesso pattern
a due stati già usato da "HOMING" in `manual_control_widget.py`
(`toggle_homing()`): un click invia `SET_SPEED:<jog_speed>` +
`GOTO:<target_mm>` e il pulsante diventa "STOP"; un secondo click invia
`STOP` e ripristina subito lo stato, senza attendere conferma dal firmware
(stessa scelta di design già fatta per l'homing). Se il movimento si ferma
per un'altra ragione (fine naturale, endstop, limite di sicurezza) senza
che l'utente riclicchi il pulsante, `MainWindow.handle_data_from_esp32()`
inoltra l'evento a `clear_goto_busy_state()` sui due widget, che fa lo
stesso ripristino. Up/Down e lo spinbox della posizione restano disabilitati
mentre un Go To è in corso (il pulsante Go To stesso resta invece sempre
cliccabile, perché è lui a fungere da STOP), oltre che durante un test,
riusando la stessa `update_ui_for_test_state()` già esistente.

Verificato con una simulazione della UI (click Go To, verifica dei comandi
seriali inviati, simulazione di `STATUS:MOVE_COMPLETED`/`STOPPED_BY_USER`
in arrivo dal firmware, verifica del ripristino dello stato dei pulsanti) e
compilazione reale del firmware; non ancora verificato con un movimento
reale sulla macchina fisica.

### Fix: il pulsante "Save Calibration" non salvava nulla

Segnalato dall'utente ("quando salvo una calibrazione non mi pare si salvi
nulla, non compare nemmeno in Explorer") — bug già noto e documentato in
`CLAUDE.md` (punto critico 2), rimasto non risolto da un refactor
precedente. `CalibrationWidget.save_calibration()` apriva il file dialog e
si limitava a emettere il segnale `save_calibration_requested(filePath)`,
mai collegato a nessuno slot in `main.py`: l'utente vedeva l'interazione
completarsi (dialog "Salva con nome" chiuso normalmente) ma nessun file
veniva scritto, senza alcun errore visibile.

Analizzando il problema è emerso che mancava anche il dato da salvare: il
widget non aveva mai modo di sapere quale fosse il fattore di scala
corrente della cella di carico. Il firmware calcola il fattore di scala
internamente durante `CALIBRATE:<grammi>` (mediando le letture per 1s) e lo
comunica solo tramite `STATUS:CALIBRATION_DONE;SCALE=<valore>`, un
messaggio che prima veniva mostrato in status bar e scartato senza altra
azione (vedi il commento "Altri messaggi di stato... non richiedono azioni
specifiche" in `main.py`).

Risolto su entrambi i fronti:
- Aggiunta una nuova variabile `CalibrationWidget.current_calibration_factor`
  (`None` finché non è noto), che controlla anche l'abilitazione del
  pulsante "Save Calibration".
- `MainWindow.handle_data_from_esp32()` ora inoltra `STATUS:CALIBRATION_DONE`
  a `calibration_widget.set_calibration_factor(scale_factor)`, popolando la
  nuova variabile dopo ogni calibrazione riuscita. Lo stesso metodo viene
  chiamato anche da `load_calibration()` subito dopo l'invio di
  `SET_SCALE:<factor>` da file, dato che in quel caso il valore è già noto
  localmente e non serve attendere conferma dal firmware.
- `MainWindow.handle_data_from_esp32()` inoltra anche
  `STATUS:CALIBRATION_INVALIDATED` (già gestito per resettare lo stato di
  calibrazione mostrato altrove) a un nuovo
  `calibration_widget.invalidate_calibration()`, che azzera il fattore
  noto qui: un cambio di gain PGA reale invalida offset/scala sul firmware,
  quindi un fattore "vecchio" salvato dopo quel punto sarebbe silenziosamente
  sbagliato.
- `save_calibration()` ora scrive direttamente un JSON
  (`cell_name`, `calibration_factor`, `saved_at`) sul percorso scelto
  dall'utente, senza più passare da un segnale verso `MainWindow`: era
  un'indirezione che non aggiungeva nulla, dato che tutte le informazioni
  necessarie sono già disponibili localmente sul widget. Il segnale
  `save_calibration_requested` (mai collegato) è stato rimosso.
  Se `current_calibration_factor` è ancora `None` (nessuna calibrazione né
  caricamento fatto in questa sessione), il pulsante è disabilitato e non
  si arriva nemmeno ad aprire il file dialog.

Verificato con una simulazione della UI (calibrazione simulata via
`STATUS:CALIBRATION_DONE`, salvataggio su file temporaneo, invalidazione
via `STATUS:CALIBRATION_INVALIDATED`, ricaricamento da file): il file
viene scritto con il contenuto atteso solo quando un fattore è
effettivamente noto, ed è correttamente bloccato altrimenti.

### Aggiunta: indicatore di spostamento relativo dell'encoder e scelta della sorgente X nei grafici (Motor/Encoder)

Con il canale encoder esterno solo in forma assoluta (vedi voce precedente),
non c'era modo di confrontarlo visivamente con lo spostamento relativo già
mostrato per il canale a passi motore, né di usarlo come asse X nei grafici
per confrontare le due misure di spostamento sullo stesso test.

**Indicatore "Relative Enc. Displacement (mm)"**: aggiunto un nuovo
`DisplayWidget` in tutte e tre le schermate che già mostravano l'encoder
assoluto (`manual_control_widget.py`, `monotonic_test_widget.py`,
`cyclic_test_widget.py`), accanto a quello esistente. Introdotta una nuova
variabile `encoder_displacement_offset_mm` (zero relativo dedicato,
analoga a `displacement_offset_mm` già usata per il canale motore) che
`zero_relative_displacement()` azzera **insieme** allo zero dello
spostamento a passi con un solo click sul pulsante esistente "Zero Relative
Displacement" — nessun nuovo pulsante, nessuna logica di zero separata. Se
non è ancora arrivato alcun pacchetto `D:` con il campo encoder (hardware
storico o parsing fallito), l'offset non viene toccato e l'indicatore
mostra "N/A", coerentemente con l'indicatore assoluto già esistente.

**Selezione della sorgente X nei grafici (solo test monotonici e
ciclici)**: quando l'asse X del grafico è impostato su "Relative
Displacement (mm)", compaiono due flag ("Motor"/"Encoder") che permettono
di scegliere quale canale di spostamento relativo usare in ascissa —
quello stimato dai passi motore (invariato, comportamento di default) e/o
quello dell'encoder esterno reso relativo al volo con
`encoder_displacement_offset_mm`. Con **entrambi** i flag attivi, il
grafico mostra in tempo reale **due curve sovrapposte per lo stesso
provino** (stessa forza/carico in ordinata, le due diverse stime di
spostamento in ascissa), per confrontare visivamente le due misure durante
lo stesso test. I flag sono nascosti (e la scelta forzata a "Motor",
comportamento identico a prima) per qualunque altra modalità X (Strain,
Time): la richiesta esplicita era di applicare questa scelta solo al
canale "Relative Displacement", non a tutte le derivate che lo usano
internamente (es. Strain%, Y-Displacement in modalità ciclica) — l'asse Y
resta quindi sempre basato sul canale motore, per non introdurre una
dipendenza incrociata implicita. Non è possibile deselezionare entrambi i
flag insieme (nessuna curva da disegnare): un guard riattiva
automaticamente l'ultimo flag che si tenterebbe di deselezionare.

Per supportare due curve simultanee per lo stesso provino, la struttura
dati dei grafici è cambiata da "una curva per nome provino" a "una curva
per (nome provino, sorgente)": `self.plot_curves` in entrambi i widget è
ora indicizzato da tuple invece che dal solo nome, e in `cyclic_test_widget.py`
la vecchia curva live singola (`self.plot_curve`) è diventata un dizionario
`self.live_curves` (sorgente → curva). Nessun cambiamento al formato dati
salvati su disco (`DataSaver` non è stato toccato in questo giro): lo
spostamento encoder relativo è calcolato solo a runtime per display e
grafico, non persistito come colonna separata.

### Aggiunta: canale di misura encoder incrementale esterno (Livello 1, sola lettura)

Il motore passo-passo (closed loop, iSV57T-090S) garantisce solo che
compia le rotazioni comandate, non dà nessuna informazione sulla posizione
reale della traversa dopo l'accoppiamento meccanico con la vite senza fine
(il suo encoder interno non è accessibile). La stima di spostamento usata
finora (`pulse_count`, conteggio passi comandati) non è quindi una misura
indipendente: se il motore perdesse passi o l'accoppiamento meccanico
avesse un gioco, `pulse_count` non se ne accorgerebbe.

Montato un encoder incrementale ottico Omron E6B2-CWZ6C (1200 PPR)
direttamente in cima alla vite senza fine (1 giro encoder = 1 giro vite,
nessun `GEAR_RATIO` di mezzo), per avere una misura di spostamento
realmente indipendente da confrontare con quella esistente. Integrata nel
firmware (`Controllo-Macchina-ESP32/src/main.cpp`) la decodifica in
quadratura 4x (interrupt su A/B, tabella di transizione, lettura atomica
del contatore via `portENTER_CRITICAL`/`portMUX_TYPE` per la natura
dual-core dell'ESP32) più conteggio giri su Z, portata da uno sketch
standalone di validazione testato su hardware reale (risoluzione 4800
conteggi/giro confermata, lineare fino a 2 giri). Il pacchetto `D:` ha ora
un 6° campo col conteggio encoder grezzo; lato Python
(`main.py::handle_data_from_esp32()`) viene convertito in mm con la
stessa `SCREW_PITCH_MM` già usata per gli altri canali
(`mm = encoder_count / 4800.0 * SCREW_PITCH_MM`), esposto come nuova
variabile su tutti i widget di streaming, mostrato in un nuovo
`DisplayWidget` ("Encoder Displacement (mm)") accanto agli altri, e
salvato come colonna aggiuntiva nei file esportati da `DataSaver` (test
monotonici, ciclici e registrazioni manuali), **accanto** e non al posto
dello spostamento stimato a passi, per poter confrontare i due nei dati
salvati.

**Esplicitamente un canale di sola lettura (Livello 1)**: non influenza in
alcun modo il comando motore (resta tutto open-loop come prima), non
sostituisce né modifica la logica dei limiti di sicurezza assoluti
(`absolute_max_force_grams`/`absolute_max_pulse_count`, ancora basati solo
su `pulse_count`), e non entra in nessuno stop criterion dei test. Verificata
su hardware la convenzione di segno: quando la traversa sale, sia
`pulse_count` sia il conteggio encoder aumentano (stesso segno, nessuna
inversione necessaria).

La logica di **homing** esistente resta anch'essa invariata nella sua
macchina a stati, ma diventa il punto di zero comune per entrambi i canali
di spostamento: nello stesso punto in cui azzera `pulse_count` (fine
`HOMING_FINAL_LIFT`, prima di `STATUS:HOMING_COMPLETED`), il firmware
azzera ora anche il contatore encoder (`encoder_position`) e il conteggio
giri Z (`encoder_z_turns`), dentro la stessa sezione critica
(`portENTER_CRITICAL(&encoder_mux)`) già usata da `readEncoderPosition()`
per l'accesso sicuro dal `loop()` alle ISR. Nessun nuovo comando né logica
di homing separata per l'encoder: si riusa deliberatamente l'unico punto
di riferimento meccanico già affidabile della macchina.

**Compromessi emersi durante l'integrazione**:
- Le due nuove ISR dell'encoder si aggiungono a quella già esistente del
  timer di step nello stesso `loop()` che gestisce anche streaming dati e
  controllo motore. L'overhead per singola interruzione è piccolo, ma
  l'impatto su `STREAM_INTERVAL_MS` sotto carico di lavoro reale (rotazione
  rapida della traversa) non è stato ancora misurato — da validare se si
  osservano rallentamenti o jitter nello streaming a velocità elevate.
- `encoder_position` è in RAM volatile come `pulse_count`: un riavvio
  dell'ESP32 (power-cycle, o il reset indotto dall'apertura della porta
  seriale, vedi punto critico 3) lo azzera. Un ciclo di homing lo azzera
  anch'esso di nuovo (vedi sopra): il confronto fra i due canali nei dati
  salvati ha quindi senso a partire dall'ultimo homing (o dall'ultimo
  riavvio, se più recente), non genericamente "dentro la stessa sessione".
- Il conteggio giri su Z (`encoder_z_turns`) è decodificato nel firmware e
  ora azzerato insieme a `encoder_position` a fine homing, ma resta non
  esposto sul protocollo seriale — riservato a un eventuale uso futuro
  (es. verifica di coerenza tra giri contati e spostamento).
- **Verificato su hardware reale**: dopo un ciclo di homing completo, il
  conteggio encoder non risulta sempre esattamente 0 ma può restare un
  residuo di pochi conteggi (osservato: 8 conteggi ≈ 0.0085 mm, contro i
  4800/giro di risoluzione) — assestamento/vibrazione meccanica del
  motore passo-passo dopo la decelerazione finale, non un difetto della
  logica di reset. Trascurabile rispetto alla risoluzione del canale.

## 2026-07-02

### Fix: crash su avvio test monotonico con stop criterion a Forza

`MonotonicTestWidget.on_start_test()` referenziava
`self.current_force_limit_N`, un attributo che non è mai stato definito sul
widget (esiste solo su `MainWindow`, come `main_window.current_force_limit_N`).
Qualunque avvio di un test monotonico con `Stop Criterion = Force (N)` o
`Stress (MPa)` sollevava un `AttributeError` prima ancora di inviare il
comando al firmware. Il test ciclico non era affetto, perché usa
correttamente `self.main_window.current_force_limit_N` ovunque. Corretto il
singolo riferimento per usare l'attributo giusto, in linea con tutti gli
altri punti dello stesso file.

### Fix: i limiti di sicurezza macchina non venivano mai propagati automaticamente al firmware

Il firmware parte con i limiti assoluti di forza/spostamento
(`absolute_max_force_grams`, `absolute_max_pulse_count`) disabilitati
(valori enormi) e li aggiorna solo in risposta a un comando `SET_LIMITS`
esplicito. Quel comando partiva **solo** quando l'utente apriva
manualmente la finestra "LIMITS" e premeva Save: né la connessione
all'ESP32 né una nuova calibrazione della cella di carico aggiornavano il
firmware, anche se la GUI mostrava già un valore di default (100 N / 190 mm)
che sembrava "attivo". In pratica, dopo ogni power-cycle dell'ESP32 la
macchina operava senza limiti di sicurezza reali finché l'operatore non
apriva esplicitamente la finestra dei limiti.

Estratta la logica di costruzione/invio del comando `SET_LIMITS` in un
metodo unico (`MainWindow.send_limits_to_firmware()`), e richiamato
automaticamente in due nuovi punti oltre a `show_limits_dialog()`:
- alla connessione (`on_connected()`), con i limiti correnti lato GUI;
- dopo ogni ricalibrazione di una cella (`update_calibration_status()`),
  con il nuovo limite di forza dedotto dal nome della cella.

### Modifica: limite di forza di default abbassato da 100 N a 10 N

Il valore di default di `default_force_limit_N` in `main.py` era 100 N,
superiore al fondoscala reale della cella di carico attualmente montata
(rischio di danneggiarla durante i test di verifica in laboratorio con la
macchina fisicamente collegata). Abbassato a 10 N come nuovo default più
prudente; resta comunque modificabile da "LIMITS" in qualunque momento.

### Fix: comandi inviati subito dopo la connessione andavano persi (reset ESP32 su apertura porta)

Testando la propagazione automatica dei limiti alla connessione (vedi voce
precedente), è emerso che i comandi inviati da `on_connected()` non
arrivavano al firmware: probabile causa, l'apertura della porta seriale da
PC innesca un reset hardware dell'ESP32 (comportamento comune sulle schede
con USB-seriale CH340/CP210x, usato normalmente per il flashing automatico).
`SET_MODE:POLLING` e `SET_LIMITS` venivano inviati mentre il firmware era
ancora in fase di boot e quindi non poteva riceverli, con l'effetto che i
limiti di sicurezza restavano disattivati anche dopo il fix precedente,
silenziosamente.

Introdotto un ritardo di 2 secondi (`QTimer.singleShot`) tra l'evento di
connessione e l'invio di questi comandi, spostati in un nuovo metodo
`_send_post_connect_commands()`, per dare al firmware il tempo di
completare il boot prima di riceverli.

### Migrazione sensore cella di carico da HX711 a NAU7802

Sostituito il sensore di lettura della cella di carico nel firmware,
passando dall'HX711 (bit-banging su due pin dedicati) al NAU7802
(convertitore ADC I2C, libreria SparkFun Qwiic Scale NAU7802). La scelta
nasce dalla necessità di un ADC con caratteristiche migliori, riusando lo
stesso cablaggio fisico esistente (pin 32/33, ora usati come SDA/SCL invece
che DOUT/SCK) per evitare modifiche hardware.

L'architettura del firmware (loop non-bloccante, macchine a stati, comandi
seriali) è rimasta invariata: è cambiata solo la libreria del sensore e il
modo in cui viene filtrato il segnale di carico. Il vecchio filtro
"anti-spike" (tre contatori indipendenti che richiedevano N letture HX711
consecutive sopra soglia prima di agire, usati per il limite di sicurezza
assoluto, lo stop criterion del test monotonico e i controlli forza nei
blocchi ciclici/rampa) è stato **rimosso e sostituito da un unico filtro EMA
centralizzato**, applicato una sola volta nel punto di lettura del sensore.
Il valore filtrato sostituisce ovunque il vecchio valore grezzo, incluso
quanto trasmesso al PC per lo streaming/grafici — non esiste più un canale
"raw" separato.

Alpha del filtro e sample rate del NAU7802 sono configurabili a runtime
(nuovi comandi firmware `SET_FILTER_CONFIG`/`GET_FILTER_CONFIG`), invece di
essere costanti fisse, e vengono ora salvati lato GUI in `settings.json`
(nuova chiave `filter_config`) e reinviati automaticamente al firmware alla
connessione — stessa logica già adottata per i limiti di sicurezza, per
evitare che questa configurazione si perda ad ogni riavvio dell'ESP32. La
GUI espone la configurazione tramite un nuovo pulsante "Filter Config" nel
menu principale.

Il comportamento di calibrazione (TARE, CALIBRATE, nessun auto-zero al
boot) resta identico a prima: la cella va sempre ri-tarata dopo ogni
riavvio, come già era con l'HX711. Verificato su hardware reale: TARE,
CALIBRATE, lettura dati continua, superamento del limite di sicurezza, e i
due nuovi comandi di configurazione filtro (inclusi i casi di valori fuori
range).

### Feature: guadagno PGA del NAU7802 configurabile, con invalidazione automatica della calibrazione

Aggiunto il controllo del guadagno PGA del NAU7802 come terzo parametro
configurabile, insieme ad alpha e sample rate, nello stesso comando e
stesso dialog già esistenti (`SET_FILTER_CONFIG:ALPHA=..;RATE=..;GAIN=..`,
dialog "Filter Config"). `GAIN` è opzionale nel comando per retrocompatibilità
con versioni precedenti della GUI. Il default (sia firmware sia GUI) è
**128x**, che coincide col comportamento interno della libreria NAU7802 —
reso comunque esplicito nel codice per non dipendere silenziosamente da un
default di libreria che potrebbe cambiare in futuro.

Durante la verifica di questa feature è emerso un problema di correttezza:
cambiare il gain PGA altera la relazione tra conteggi ADC grezzi e grammi,
quindi l'offset (da TARE) e il fattore di scala (da CALIBRATE) calcolati al
gain precedente diventano silenziosamente sbagliati — senza alcun avviso,
anche il limite di sicurezza assoluto sulla forza si sarebbe basato su
letture non più corrette. Corretto su due fronti:
- il firmware ora invalida esplicitamente offset e fattore di scala
  (tornano ai valori di default "non calibrato") ogni volta che riceve un
  cambio di gain realmente diverso da quello corrente, ed emette
  `STATUS:CALIBRATION_INVALIDATED;REASON=GAIN_CHANGED`;
  nessuna invalidazione se il gain reinviato coincide con quello già attivo
  (es. riconnessioni ripetute con la stessa configurazione);
- la GUI reagisce a quel messaggio (non a un confronto lato client) per
  coprire sia il cambio esplicito dal dialog sia il reinvio automatico alla
  riconnessione, resettando lo stato di calibrazione mostrato e avvisando
  l'utente che deve ripetere Tara e Calibrazione.

Verificato che la libreria NAU7802 non espone alcun meccanismo dedicato per
rilevare la saturazione del PGA (nessun overflow flag): rischio noto e
documentato (vedi `docs/firmware_main.md`), non risolto in questa modifica.

### Fix: peso di calibrazione errato per la cella 50N (398g → 298g)

Il carico di calibrazione registrato per la cella "50N" in `cal_loads`
(`settings.json` e default in `settings_manager.py`) era 398 g, un valore
copiato per errore dalla cella "10N" invece del peso noto realmente usato
per quella cella (298 g). Chiunque calibrasse la cella da 50N usando il
peso indicato dalla GUI otteneva un fattore di scala sistematicamente
errato (~25% di scarto rispetto al peso reale applicato). Corretto il
valore in entrambi i punti in cui è definito.

## Manutenzione di questo changelog

Da qui in avanti, ogni modifica sostanziale al codice (nuova feature, fix
di bug rilevante, refactoring) deve aggiungere una voce concettuale in
questo file — vedi le istruzioni permanenti in `CLAUDE.md`.