import json
import os

class SettingsManager:
    """
    Gestisce il caricamento e il salvataggio delle impostazioni
    su un file JSON.
    """
    def __init__(self, filename="settings.json"):
        self.filepath = filename
        self.default_settings = {
            "cal_loads": {
                "1N": [0.0, 44.0],
                "10N": [0.0, 398.0],
                "50N": [0.0, 398.0],
                "100N": [0.0, 1398.0],
                "200N": [0.0, 1398.0]
            }
        }

    def load_settings(self):
        """
        Carica le impostazioni dal file JSON.
        Se il file non esiste, crea il file con le impostazioni di default.
        """
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    settings = json.load(f)
                    # Assicurati che tutte le chiavi di default siano presenti
                    for key, value in self.default_settings.items():
                        if key not in settings:
                            settings[key] = value
                    return settings
            except json.JSONDecodeError:
                print(f"Attenzione: file di impostazioni '{self.filepath}' corrotto. Ritorno ai default.")
                return self.default_settings
        else:
            print(f"File di impostazioni non trovato. Creo '{self.filepath}' con i valori di default.")
            self.save_settings(self.default_settings)
            return self.default_settings

    def save_settings(self, settings):
        """Salva il dizionario delle impostazioni nel file JSON."""
        try:
            with open(self.filepath, 'w') as f:
                json.dump(settings, f, indent=4)
        except IOError as e:
            print(f"Errore durante il salvataggio delle impostazioni: {e}")