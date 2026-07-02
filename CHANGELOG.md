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

## Manutenzione di questo changelog

Da qui in avanti, ogni modifica sostanziale al codice (nuova feature, fix
di bug rilevante, refactoring) deve aggiungere una voce concettuale in
questo file — vedi le istruzioni permanenti in `CLAUDE.md`.