# communication.py

## Scopo

Incapsula tutta la comunicazione seriale con l'ESP32 in un `QObject` pensato
per girare in un `QThread` dedicato, così da non bloccare mai la GUI durante
letture/scritture sulla porta seriale.

## Classi e funzioni principali

- **`SerialCommunicator(QObject)`**
  - Segnali: `data_received(str)` (una riga completa ricevuta), `port_error(str)`,
    `connected()`, `disconnected()`.
  - `connect_to_port(port_name)`: apre la porta a **460800 baud**, `timeout=0`
    (lettura non bloccante), svuota il buffer di ingresso, emette `connected`
    (o `port_error` in caso di `SerialException`).
  - `disconnect_port()` / `stop()`: chiudono la porta; `stop()` imposta anche
    `is_running = False` per terminare il loop di `run()`.
  - `send_command(command)`: mette il comando in una `Queue` FIFO thread-safe
    (`command_queue`) — non scrive direttamente sulla porta.
  - `run()`: loop principale eseguito nel thread dedicato.
    1. Se la coda comandi non è vuota, estrae un comando e lo scrive come
       `f"{command}\n"` (encoding UTF-8), poi fa `flush()`.
    2. Se la porta è aperta, legge tutti i byte disponibili
       (`in_waiting`), li accumula in un `bytearray` e spezza sulle occorrenze
       di `\n`, emettendo `data_received` per ogni riga non vuota.
    3. Se non c'è nulla da leggere, fa uno `sleep(0.002)` per non saturare la
       CPU; se la porta non è aperta, `sleep(0.01)`.
  - `send_emergency_stop()`: scrive **direttamente** `b"!\n"` sulla porta,
    bypassando `command_queue`, per garantire la priorità assoluta dello stop
    di emergenza anche se la coda ha altri comandi in attesa.
  - `list_available_ports()` (staticmethod): wrapper su
    `serial.tools.list_ports.comports()`.

## Dipendenze

- Usato da `MainWindow` (che lo sposta in un `QThread` con
  `moveToThread`) e passato per riferimento a tutti i widget che devono
  inviare comandi (`ManualControlWidget`, `CalibrationWidget`,
  `MonotonicTestWidget`, `CyclicTestWidget`).
- Nessuna dipendenza verso altri moduli applicativi: non conosce il formato
  dei comandi/messaggi, tratta tutto come stringhe opache.

## Punti di attenzione

- Il baud rate `460800` è hardcoded qui e deve corrispondere esattamente a
  `Serial.begin(460800)` nel firmware — non c'è negoziazione automatica (vedi
  `docs/firmware_main.md`).
- `send_command()` e `send_emergency_stop()` scrivono su due canali diversi
  (coda vs scrittura diretta): un comando normale accodato subito prima di
  un emergency stop può essere scritto *dopo* lo stop se il loop `run()` sta
  già processando la coda in quel momento — nella pratica non è un problema
  perché lo stop è a singolo carattere e il firmware lo intercetta comunque
  con priorità massima, ma va tenuto a mente se si aggiungono altri comandi
  "critici".
- Gli errori di scrittura (`SerialException`) durante l'invio di un comando
  in coda vengono solo segnalati con `port_error`, il comando perso non viene
  rimesso in coda né ritentato.
- Il buffer di lettura è un semplice `bytearray` accumulato senza limite
  massimo: se il firmware smette di terminare le righe con `\n` (bug lato
  firmware) il buffer crescerebbe indefinitamente.