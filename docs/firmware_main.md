# firmware: Controllo-Macchina-ESP32/src/main.cpp

> File in un repository separato:
> `c:\Users\carlo\Documents\PlatformIO\Projects\Controllo-Macchina-ESP32\src\main.cpp`
> (progetto PlatformIO/Arduino, ~1220 righe, unico file sorgente).

## Scopo

Firmware dell'ESP32 che pilota fisicamente la macchina: motore passo-passo
(vite senza fine) per il movimento, cella di carico HX711 per la forza,
endstop meccanici, e opzionalmente un LCR-meter esterno via UART2. Espone
un protocollo seriale a comandi testuali (vedi `CLAUDE.md`, sezione
"Protocollo di comunicazione seriale") e implementa da solo tutta la logica
di temporizzazione, sicurezza e macchine a stati dei test — la GUI Python
invia comandi ad alto livello e riceve stato/dati, ma non ha visibilità sui
dettagli di esecuzione (passi, ISR, timer hardware).

## Classi e funzioni principali

Non è C++ orientato agli oggetti: stato globale + funzioni. Le funzioni
principali sono:

- **`setup()`**: inizializza seriali (`Serial` a 460800 baud per la GUI,
  `Serial2` a 115200 baud sui pin 16/17 per l'LCR-meter), pin, HX711
  (`scale.set_scale(1.0)`, `scale.tare()`), e il timer hardware
  (`stepTimer`) che genera gli impulsi di step via interrupt.
- **`loop()`**: gestisce il completamento di movimenti a passi contati
  (`move_completed_flag`), poi chiama in sequenza `handleSerialCommands()`,
  `handleDataStreaming()`, `updateMotorState()`, `updateLCRReading()`.
  `handleHardwareInputs()` (pulsanti fisici) è **disabilitata** (commentata).
- **`onStepTimer()`** (ISR, `IRAM_ATTR`): alterna il pin di step,
  incrementa/decrementa `pulse_count`, e decrementa `target_steps_remaining`
  per i movimenti a conteggio (homing, return-to-start, pre-posizionamento
  ciclico), impostando `move_completed_flag` quando arriva a 0.
- **`handleSerialCommands()`**: legge caratteri non bloccante da `Serial`;
  intercetta `'!'` **immediatamente**, prima di aspettare `'\n'`, per lo stop
  di emergenza; altrimenti accumula in `serial_buffer` (max 128 caratteri)
  fino a `'\n'`, poi chiama `processCommand()`.
- **`processCommand(command)`**: dispatcher a catena di
  `if/else if command == ...` / `command.startsWith(...)`. Gestisce tutti i
  comandi elencati in `CLAUDE.md`. I comandi con parametri
  (`START_CYCLIC_TEST`, `EXECUTE_RAMP`, `SET_LIMITS`, `START_TEST`) fanno
  parsing manuale con `indexOf`/`substring` (vedi Punti di attenzione).
- **`updateMotorState()`**: chiamata ad ogni `loop()`. In ordine:
  1. Controllo dei **limiti di sicurezza assoluti** (`absolute_max_pulse_count`,
     `absolute_max_force_grams`, quest'ultimo con filtro anti-spike a 5
     letture consecutive) → se superati, ferma tutto ed emette
     `STATUS:LIMIT_HIT_DISPLACEMENT`/`LIMIT_HIT_FORCE` (una sola volta per
     evento, tramite `limit_hit_notification_sent`).
  2. Controllo endstop meccanici (`TOP_HIT`/`BOTTOM_HIT`), ignorando il
     bottom endstop durante l'homing.
  3. Macchina a stati dell'**homing** (`HOMING_FAST → HOMING_BACKOFF →
     HOMING_SLOW → HOMING_FINAL_LIFT`), che azzera `pulse_count` solo alla
     fine.
  4. Verifica dello stop criterion del **test monotonico**
     (`CRITERION_DISP`/`CRITERION_FORCE`, anche qui con filtro anti-spike per
     la forza).
  5. Macchina a stati del **test ciclico** (`switch(cyclic_phase)`):
     `CYCLIC_PREPOSITION → CYCLIC_MOVING_UP → CYCLIC_HOLDING_UPPER →
     CYCLIC_MOVING_DOWN → CYCLIC_HOLDING_LOWER` (ripetuto per
     `cyclic_target_cycles` cicli), più i rami paralleli `CYCLIC_PAUSED`
     (per i blocchi pausa) e `RAMPING → RAMP_HOLDING` (per i blocchi rampa).
     Ogni fine-blocco emette `STATUS:BLOCK_COMPLETED`, che la GUI Python
     interpreta per avanzare alla `main.py::handle_data_from_esp32()`.
- **`handleDataStreaming()`**: legge la cella di carico in modo non
  bloccante (`readLoadNonBlocking`) e, se `comms_mode == STREAMING`, emette
  un pacchetto `D:` ogni `STREAM_INTERVAL_MS` (20 ms → 50 Hz).
- **`updateLCRReading()`**: macchina a stati a 2 fasi (invia `FETCh?` su
  `Serial2`, poi aspetta la risposta con un timeout doppio
  dell'intervallo di polling) per non bloccare mai il loop principale in
  attesa dell'LCR-meter.

## Dipendenze

- Libreria `HX711.h` per la cella di carico.
- Protocollo seriale condiviso con `communication.py` (baud rate) e con la
  logica applicativa di `main.py`, `monotonic_test_widget.py`,
  `cyclic_test_widget.py` (formato dei comandi/messaggi). Non esiste alcuna
  dipendenza di build tra i due repository: la coerenza è **solo
  concettuale/manuale**.
- Le costanti meccaniche `PULSES_PER_REV`, `GEAR_RATIO`, `SCREW_PITCH_MM`
  sono duplicate identiche in `main.py` (`MainWindow.__init__`).

## Punti di attenzione

- **Parsing comandi non robusto**: tutti i comandi con parametri usano
  `indexOf("CHIAVE=") + N` / `substring(...)` senza validare presenza o
  ordine dei campi. Un campo mancante o in ordine diverso produce
  `indexOf() == -1` e aritmetica su indici invalidi, con comportamento non
  definito su `String` di Arduino. Funziona oggi solo perché il lato Python
  costruisce sempre le stringhe complete e nell'ordine atteso — vedi
  `CLAUDE.md`, punto critico 6.
- **Riuso di variabili di stato per scopi diversi**: `EXECUTE_PAUSE` scrive
  la durata della pausa dentro `cyclic_hold_upper_ms` (la stessa variabile
  usata dai blocchi ciclici per l'hold al limite superiore), con un
  commento esplicito nel codice che segnala l'hack. Aggiungere un nuovo tipo
  di blocco o riordinare le operazioni rischia di leggere/sovrascrivere un
  valore "sporco" lasciato da un blocco precedente.
- **Debug seriale non prefissato e ad alta frequenza**: `startMotor()`
  stampa `"DEBUG: startMotor() chiamato"` ad ogni chiamata, che durante un
  jog manuale (`JOG_UP`/`JOG_DOWN`) può avvenire a frequenza molto alta
  perché richiamata da `updateMotorState()` ad ogni ciclo di `loop()`.
  Occupa banda seriale e CPU nello stesso loop che gestisce streaming dati e
  stato motore; ci sono anche blocchi di debug simili, parzialmente
  commentati, dentro `EXECUTE_RAMP` e nel ramo `RAMPING`.
- **Limiti di sicurezza disabilitati di default**: `absolute_max_pulse_count`
  e `absolute_max_force_grams` partono a valori enormi (di fatto
  "disabilitati") e vengono aggiornati solo da un comando `SET_LIMITS`
  esplicito — non c'è alcun default di sicurezza cablato nel firmware. La
  GUI ora invia `SET_LIMITS` automaticamente alla connessione e ad ogni
  ricalibrazione cella (vedi `CHANGELOG.md`), ma il firmware da solo, appena
  flashato o dopo un riavvio senza la GUI collegata, non applica alcun
  limite.
- **`monitor_speed = 115200` in `platformio.ini`** non corrisponde al baud
  reale (`Serial.begin(460800)`): chi usa `pio device monitor` per debug
  senza forzare `-b 460800` vede solo rumore.
- **`GET_SCALE`/`SCALE:` è implementato ma mai chiamato** da nessun widget
  Python: la GUI non legge mai il fattore di scala corrente dal firmware,
  si fida solo di quanto ha impostato lei stessa.