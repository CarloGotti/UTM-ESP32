import json
from datetime import datetime


class EventLogger:
    """
    Log persistente di eventi di sistema (transizioni killswitch, homing,
    prove interrotte, connessione/disconnessione seriale, errori di
    calibrazione, ecc.), SEPARATO dai dati di misura delle prove: non deve
    mai contenere forza/spostamento/tempo dei test, solo eventi discreti.

    Formato JSON Lines (un oggetto JSON per riga, append-only) in
    event_log.jsonl nella cartella dell'app, accanto a settings.json.
    """

    def __init__(self, filename="event_log.jsonl"):
        self.filepath = filename

    def log(self, event_type, details=None):
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event_type,
            "details": details or {},
        }
        try:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except IOError as e:
            print(f"Errore durante la scrittura del log eventi: {e}")
