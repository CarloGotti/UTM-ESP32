# Changelog

Riepilogo concettuale dei cambiamenti architetturali e delle correzioni
rilevanti al progetto. Non è un log riga-per-riga dei commit: per quello si
veda la cronologia git. Ogni voce spiega **cosa** è cambiato e **perché**.

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