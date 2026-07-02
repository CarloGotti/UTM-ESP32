# Changelog

Riepilogo concettuale dei cambiamenti architetturali e delle correzioni
rilevanti al progetto. Non è un log riga-per-riga dei commit: per quello si
veda la cronologia git. Ogni voce spiega **cosa** è cambiato e **perché**.

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