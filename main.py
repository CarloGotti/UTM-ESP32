import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QStackedWidget, QComboBox,
                             QPushButton, QHBoxLayout, QWidget, QStatusBar, QLabel,
                             QVBoxLayout, QListWidgetItem, QMessageBox)
from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal

from main_menu_widget import MainMenuWidget
from manual_control_widget import ManualControlWidget
from calibration_widget import CalibrationWidget
from monotonic_test_widget import MonotonicTestWidget
from cyclic_test_widget import CyclicTestWidget
from communication import SerialCommunicator
from settings_manager import SettingsManager
from custom_widgets import LimitsDialog, FilterConfigDialog, ADS1220ConfigDialog, KillswitchIndicatorWidget, KillswitchBannerWidget
from event_logger import EventLogger


class MainWindow(QMainWindow):
    # --- NUOVO SEGNALE THREAD-SAFE ---
    # Questo segnale trasporterà il messaggio di errore dal thread di comunicazione
    # al thread principale della GUI in modo sicuro.
    limit_hit_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Software Controllo Macchina di Trazione")
        self.resize(1200, 800)
        
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.load_settings()

        self.active_calibration_info = "Not Calibrated"
        self.active_cell_name = None # NUOVA VARIABILE
        self.is_critical_popup_active = False # <-- NUOVA BANDIERINA

        # --- STATO KILLSWITCH (vedi CLAUDE.md per protocollo/hardware) ---
        self.killswitch_engaged = False    # True = killswitch premuto ORA (stato "rosso")
        self.position_unverified = False   # True finché non completa un HOME dopo un trigger (stato "giallo" se non rosso)
        self.event_logger = EventLogger()
        # Indicatori a 3 livelli attualmente "vivi": la finestra principale
        # sempre, più quelli dei dialoghi LIMITS/Filter Config finché aperti.
        self._killswitch_indicators = []

        self.PULSES_PER_REV = 2000.0; self.GEAR_RATIO = 10.0; self.SCREW_PITCH_MM = 5.0873
        self.PULSES_TO_MM = self.SCREW_PITCH_MM / (self.PULSES_PER_REV * self.GEAR_RATIO)
        # Encoder incrementale esterno (Omron E6B2-CWZ6C, 1200 PPR x4 = 4800
        # conteggi/giro), montato direttamente sulla vite senza fine: nessun
        # GEAR_RATIO di mezzo. Canale di misura aggiuntivo di sola lettura
        # (Livello 1), non usato per nessuna decisione real-time.
        self.ENCODER_COUNTS_PER_REV = 4800.0
        
        self.default_force_limit_N = 10.0
        self.current_force_limit_N = self.default_force_limit_N
        self.current_disp_limit_mm = 190.0
        self.current_filter_alpha = self.settings['filter_config']['alpha']
        self.current_filter_rate_sps = self.settings['filter_config']['rate_sps']
        self.current_filter_pga_gain = self.settings['filter_config']['gain']

        # --- ADS1220 (resistenza campioni, canale alternativo all'LCR-meter) ---
        ads_cfg = self.settings['ads1220_config']
        self.current_ads1220_sps = ads_cfg['sps']
        self.current_ads1220_gain = ads_cfg['gain']
        self.current_ads1220_pga_bypass = ads_cfg['pga_bypass']
        self.current_ads1220_idac_ua = ads_cfg['idac_ua']
        self.current_ads1220_window = ads_cfg['window']
        # Sorgente resistenza attualmente attiva a livello firmware ("OFF"/"LCR"/"ADS1220"):
        # tracciata centralmente (aggiornata dal segnale emesso da qualunque widget quando
        # l'utente cambia selettore) per poter disabilitare il dialog impostazioni ADS1220
        # mentre il canale è in polling, indipendentemente da quale schermata è visibile.
        self.active_resistance_source = "OFF"

        self.comm_thread = QThread(); self.communicator = SerialCommunicator()
        self.communicator.moveToThread(self.comm_thread)
        self.comm_thread.started.connect(self.communicator.run); self.comm_thread.start()

        self.stacked_widget = QStackedWidget(); main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        
        connection_bar = QHBoxLayout()
        self.port_selector = QComboBox(); self.refresh_ports_button = QPushButton("Refresh")
        self.connect_button = QPushButton("Connect"); self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)
        
        connection_bar.addWidget(QLabel("Porta COM:")); connection_bar.addWidget(self.port_selector)
        connection_bar.addWidget(self.refresh_ports_button); connection_bar.addStretch(1)
        # Indicatore a 3 livelli del killswitch: nella barra superiore, fuori
        # dallo QStackedWidget, quindi visibile in ogni schermata dell'app.
        self.killswitch_indicator = KillswitchIndicatorWidget()
        self._killswitch_indicators.append(self.killswitch_indicator)
        connection_bar.addWidget(self.killswitch_indicator)
        connection_bar.addWidget(self.connect_button); connection_bar.addWidget(self.disconnect_button)

        main_layout.addLayout(connection_bar)
        # Banner persistente (in aggiunta al popup, non alternativo): resta
        # visibile finché lo stato non torna verde, su ogni schermata.
        self.killswitch_banner = KillswitchBannerWidget()
        main_layout.addWidget(self.killswitch_banner)
        main_layout.addWidget(self.stacked_widget)
        self.setCentralWidget(main_widget)
        self.setStatusBar(QStatusBar(self)); self.statusBar().showMessage("Disconnesso.")

        self.main_menu = MainMenuWidget()
        self.manual_control = ManualControlWidget(self.communicator)
        self.calibration_widget = CalibrationWidget(self.communicator, self.settings['cal_loads'])
        self.monotonic_test_widget = MonotonicTestWidget(self.communicator, self)
        self.cyclic_test = CyclicTestWidget(self.communicator, self)
        
        self.stacked_widget.addWidget(self.main_menu); self.stacked_widget.addWidget(self.manual_control)
        self.stacked_widget.addWidget(self.calibration_widget); self.stacked_widget.addWidget(self.monotonic_test_widget); self.stacked_widget.addWidget(self.cyclic_test)
        
        self.main_menu.manual_button.clicked.connect(self.show_manual_control)
        self.main_menu.calibrate_button.clicked.connect(self.show_calibration)
        self.main_menu.filter_button.clicked.connect(self.show_filter_dialog)
        self.main_menu.ads1220_button.clicked.connect(self.show_ads1220_dialog)
        self.main_menu.monotonic_button.clicked.connect(self.show_monotonic_test)
        self.main_menu.cyclic_button.clicked.connect(self.show_cyclic_test)

        # Sorgente resistenza: qualunque schermata la cambi, MainWindow tiene
        # traccia dello stato globale (serve solo per gating del dialog
        # impostazioni ADS1220, vedi self.active_resistance_source sopra).
        self.manual_control.resistance_source_changed.connect(self._on_resistance_source_changed)
        self.monotonic_test_widget.resistance_source_changed.connect(self._on_resistance_source_changed)
        self.cyclic_test.resistance_source_changed.connect(self._on_resistance_source_changed)
        
        self.manual_control.back_to_menu_requested.connect(self.show_main_menu)
        self.calibration_widget.back_to_menu_requested.connect(self.show_main_menu)
        self.monotonic_test_widget.back_to_menu_requested.connect(self.show_main_menu)
        self.cyclic_test.back_to_menu_requested.connect(self.show_main_menu)
        self.cyclic_test.limits_button_requested.connect(self.show_limits_dialog)
        

        self.manual_control.limits_button_requested.connect(self.show_limits_dialog)
        self.monotonic_test_widget.limits_button_requested.connect(self.show_limits_dialog)

        self.calibration_widget.calibration_updated.connect(self.update_calibration_status)
        self.calibration_widget.settings_changed.connect(self.save_cal_load_settings)

        self.refresh_ports_button.clicked.connect(self.populate_ports)
        self.connect_button.clicked.connect(self.connect_device)
        self.disconnect_button.clicked.connect(self.disconnect_device)
        
        self.communicator.data_received.connect(self.handle_data_from_esp32)
        self.communicator.connected.connect(self.on_connected)
        self.communicator.disconnected.connect(self.on_disconnected)
        self.communicator.port_error.connect(lambda msg: self.statusBar().showMessage(msg))

        # --- NUOVA CONNESSIONE PER IL POPUP SICURO ---
        self.limit_hit_signal.connect(self.show_limit_hit_popup)
        # --- FINE NUOVA CONNESSIONE ---

        self.data_request_timer = QTimer(self)
        self.data_request_timer.setInterval(100)
        self.data_request_timer.timeout.connect(lambda: self.communicator.send_command("GET_DATA"))
        
        self.populate_ports()

    def save_cal_load_settings(self, new_cal_loads):
        self.settings['cal_loads'] = new_cal_loads
        self.settings_manager.save_settings(self.settings)

    def populate_ports(self):
        self.port_selector.clear()
        ports = self.communicator.list_available_ports()
        if ports: self.port_selector.addItems(ports)
        else: self.port_selector.addItem("Nessuna porta trovata")

    def connect_device(self):
        port = self.port_selector.currentText()
        if port and "Nessuna porta" not in port:
            self.communicator.connect_to_port(port)

    def disconnect_device(self):
        self.communicator.disconnect_port()

    def on_connected(self):
        self.connect_button.setEnabled(False); self.disconnect_button.setEnabled(True)
        self.refresh_ports_button.setEnabled(False); self.port_selector.setEnabled(False)
        self.statusBar().showMessage(f"Connesso a {self.port_selector.currentText()}")
        self._log_event("serial_connected", {"port": self.port_selector.currentText()})
        # Aprire la porta seriale può causare un reset hardware dell'ESP32 (comune
        # sulle schede con USB-seriale CH340/CP210x): ritardiamo l'invio dei comandi
        # iniziali per dargli il tempo di completare il boot, altrimenti vengono persi.
        QTimer.singleShot(2000, self._send_post_connect_commands)
        self.data_request_timer.start()

    def _send_post_connect_commands(self):
        self.communicator.send_command("SET_MODE:POLLING")
        self.send_limits_to_firmware()
        self.send_filter_config_to_firmware()
        self.send_ads1220_config_to_firmware()
        # Allinea subito l'indicatore killswitch allo stato reale del
        # firmware: copre sia il caso "reset su apertura porta" (il boot
        # invia già KILLSWITCH_TRIGGERED da solo, vedi CLAUDE.md) sia schede
        # che non resettano alla connessione, dove altrimenti l'indicatore
        # resterebbe "verde" per errore fino alla prossima transizione fisica.
        self.communicator.send_command("GET_KILLSWITCH_STATE")

    def on_disconnected(self):
        self.data_request_timer.stop()
        self.connect_button.setEnabled(True); self.disconnect_button.setEnabled(False)
        self.refresh_ports_button.setEnabled(True); self.port_selector.setEnabled(True)
        self.statusBar().showMessage("Disconnesso.")
        self._log_event("serial_disconnected")

    # File: main.py
    # SOSTITUISCI completamente la vecchia funzione handle_data_from_esp32 con questa

    def handle_data_from_esp32(self, data: str):
        print(f"[ESP32 RAW]: {data}") # Stampa dati grezzi

        # --- GESTIONE MESSAGGI DI STATO ---
        if data.startswith("STATUS:"):
            status_message = data.replace("STATUS:", "")
            self.statusBar().showMessage(f"Status: {status_message}", 5000) # Mostra nella status bar

            # Gestione specifica dei messaggi di stato
            widget = self.cyclic_test # Riferimento al widget ciclico (usato sotto)

            # --- KILLSWITCH: gestione stato (priorità alta, valutata per prima) ---
            if "KILLSWITCH_TRIGGERED" in status_message:
                self.killswitch_engaged = True
                self.position_unverified = True
                self._update_killswitch_indicator()
                self._log_event("killswitch_triggered")
                self._show_killswitch_popup()
                # Un homing eventualmente in corso viene interrotto dallo
                # stesso path dello stop di emergenza: ripristina la UI di
                # homing esattamente come già avviene per STOPPED_BY_USER.
                if self.manual_control.is_homing_active:
                    self.manual_control.reset_homing_ui()

            elif "KILLSWITCH_CLEARED" in status_message:
                self.killswitch_engaged = False
                # position_unverified resta invariato (True): serve un HOME
                # riuscito per tornare verde, gestito nel ramo HOMING_COMPLETED.
                self._update_killswitch_indicator()
                self._log_event("killswitch_cleared")

            elif "KILLSWITCH_STATE" in status_message:
                # Risposta a GET_KILLSWITCH_STATE (inviato subito dopo la
                # connessione): allinea lo stato GUI a quello reale del
                # firmware anche se non è arrivato nessun KILLSWITCH_TRIGGERED
                # di boot (schede che non resettano all'apertura porta).
                try:
                    fields = dict(kv.split("=") for kv in status_message.split(";")[1:])
                    self.killswitch_engaged = (fields.get("ENGAGED") == "1")
                    self.position_unverified = (fields.get("POSITION_UNVERIFIED") == "1")
                    self._update_killswitch_indicator()
                except (ValueError, IndexError) as e:
                    print(f"Attenzione: impossibile interpretare KILLSWITCH_STATE da '{status_message}': {e}")

            # Prova interrotta dal killswitch: chiudi il file mantenendo tutti
            # i dati già acquisiti, marcandolo come interrotto (non completato
            # né stoppato dall'utente). Distinto da TEST_STOPPED_BY_USER/
            # CYCLIC_TEST_STOPPED_BY_USER proprio per questo.
            elif "TEST_ABORTED" in status_message and "KILLSWITCH" in status_message:
                if self.monotonic_test_widget.is_test_running:
                    self.monotonic_test_widget.on_stop_test(user_initiated=False, abort_reason="KILLSWITCH")
                    self._log_event("test_aborted_killswitch", {"test_type": "monotonic",
                                                                 "specimen": self.monotonic_test_widget.current_specimen_name})
                if self.cyclic_test.is_test_running:
                    self.cyclic_test.on_stop_test(user_initiated=False, abort_reason="KILLSWITCH")
                    self._log_event("test_aborted_killswitch", {"test_type": "cyclic",
                                                                 "specimen": self.cyclic_test.current_specimen_name})

            elif "CYCLIC_TEST_STARTED" in status_message or "CYCLIC_PREPOSITIONING" in status_message:
                pass # UI già aggiornata da on_start_test

            elif "BLOCK_COMPLETED" in status_message:
                # Questa logica è specifica per il test ciclico
                # Controlla se il widget corrente è quello ciclico prima di procedere
                if self.stacked_widget.currentWidget() == widget and widget.is_test_running:
                    widget.current_block_index += 1 # Passa al blocco successivo

                    if widget.current_block_index < len(widget.test_sequence):
                        # C'è un altro blocco, invia il comando appropriato
                        next_block = widget.test_sequence[widget.current_block_index]
                        command = "" # Inizializza comando

                        if next_block["type"] == "cyclic":
                            # --- ECCO LA LOGICA COMPLETA PER 'cyclic' ---
                            control_mode_base = next_block["base_unit"].upper() # "MM" o "N"
                            if control_mode_base == "MM":
                                control_mode_fw = "DISP"
                                abs_upper_mm = next_block["upper_conv"] + widget.displacement_offset_mm
                                abs_lower_mm = next_block["lower_conv"] + widget.displacement_offset_mm
                                upper_fw = abs_upper_mm
                                lower_fw = abs_lower_mm
                            else: # "N"
                                control_mode_fw = "FORCE"
                                abs_upper_N = next_block["upper_conv"] + widget.load_offset_N
                                abs_lower_N = next_block["lower_conv"] + widget.load_offset_N
                                upper_fw = (abs_upper_N / 9.81) * 1000.0
                                lower_fw = (abs_lower_N / 9.81) * 1000.0
                            
                            speed_mms = next_block["speed_mms"]
                            hold_upper_ms = int(next_block["hold_upper"] * 1000)
                            hold_lower_ms = int(next_block["hold_lower"] * 1000)
                            cycles = next_block["cycles"]
                            
                            command = (f"START_CYCLIC_TEST:"
                                       f"MODE={control_mode_fw};UPPER={upper_fw:.4f};LOWER={lower_fw:.4f};"
                                       f"SPEED={speed_mms:.3f};HOLD_U={hold_upper_ms};HOLD_L={hold_lower_ms};CYCLES={cycles}")
                            print(f"DEBUG Main: Avviato Blocco Ciclico {widget.current_block_index + 1}")
                            # --- FINE LOGICA 'cyclic' ---

                        elif next_block["type"] == "pause":
                            # --- ECCO LA LOGICA COMPLETA PER 'pause' ---
                            duration_ms = int(next_block["duration"] * 1000)
                            command = f"EXECUTE_PAUSE:{duration_ms}"
                            print(f"DEBUG Main: Avviata Pausa {widget.current_block_index + 1} ({duration_ms} ms)")
                            # --- FINE LOGICA 'pause' ---

                        elif next_block["type"] == "ramp":
                            # --- ECCO LA LOGICA COMPLETA PER 'ramp' ---
                            control_mode_base = next_block["base_unit"].upper() # Sarà "MM" o "N"
                            if control_mode_base == "MM":
                                control_mode_fw = "DISP"
                                abs_target_mm = next_block["target_conv"] + widget.displacement_offset_mm
                                target_fw = abs_target_mm # Il firmware si aspetta mm
                            else: # "N"
                                control_mode_fw = "FORCE"
                                abs_target_N = next_block["target_conv"] + widget.load_offset_N
                                target_fw = (abs_target_N / 9.81) * 1000.0 # Converti N assoluti in grammi

                            speed_mms = next_block["speed_mms"]
                            hold_ms = int(next_block["hold_duration"] * 1000)

                            command = (f"EXECUTE_RAMP:"
                                       f"MODE={control_mode_fw};" 
                                       f"TARGET={target_fw:.4f};"
                                       f"SPEED={speed_mms:.3f};"
                                       f"HOLD={hold_ms}")
                            print(f"DEBUG Main: Avviata Rampa {widget.current_block_index + 1}")
                            # --- FINE LOGICA 'ramp' ---

                        if command:
                             self.communicator.send_command(command) # Invia comando del blocco successivo

                    else:
                        # Non ci sono altri blocchi. Sequenza completata.
                        print(f"DEBUG Main: Sequenza completata. Tutti i {widget.current_block_index} blocchi eseguiti.")
                        self.communicator.send_command("SET_MODE:POLLING") # Torna in polling
                        if widget.is_test_running:
                            widget.on_stop_test(user_initiated=False) # Aggiorna UI
                            QMessageBox.information(self, "Test Ciclico Terminato", "Sequenza di test completata.")
                # Se non siamo nel widget ciclico, ignoriamo BLOCK_COMPLETED
                # (potrebbe arrivare da un test precedente interrotto?)

            # Gestione fine test ciclico (completato, stoppato, endstop)
            elif "CYCLIC_TEST_COMPLETED" in status_message or \
                 ("CYCLIC_TEST_STOPPED_BY_USER" in status_message and self.cyclic_test.is_test_running) or \
                 ("TOP_HIT" in status_message and self.cyclic_test.is_test_running) or \
                 ("BOTTOM_HIT" in status_message and self.cyclic_test.is_test_running):
                 if self.cyclic_test.is_test_running:
                     self.cyclic_test.on_stop_test(user_initiated=False)
                     popup_title = "Test Ciclico Terminato"
                     popup_message = f"Il test si è concluso con stato: {status_message}"
                     if "TOP_HIT" in status_message or "BOTTOM_HIT" in status_message:
                         popup_title = "Endstop Colpito"
                         popup_message = f"Test interrotto: {status_message}"
                         QMessageBox.critical(self, popup_title, popup_message)
                     else:
                         QMessageBox.information(self, popup_title, popup_message)

            # Gestione fine test monotonico (completato, stoppato, endstop)
            elif "TEST_COMPLETED" in status_message or \
                 ("TEST_STOPPED_BY_USER" in status_message and self.monotonic_test_widget.is_test_running) or \
                 ("TOP_HIT" in status_message and self.monotonic_test_widget.is_test_running) or \
                 ("BOTTOM_HIT" in status_message and self.monotonic_test_widget.is_test_running):
                 if self.monotonic_test_widget.is_test_running:
                    self.monotonic_test_widget.on_stop_test(user_initiated=False)
                    popup_title = "Test Terminato"
                    popup_message = f"Il test si è concluso con stato: {status_message}"
                    if "TOP_HIT" in status_message or "BOTTOM_HIT" in status_message:
                        popup_title = "Endstop Colpito"
                        popup_message = f"Test interrotto: {status_message}"
                        QMessageBox.critical(self, popup_title, popup_message)
                    else:
                        QMessageBox.information(self, popup_title, popup_message)

            # Gestione Limiti di Sicurezza (Usa il segnale thread-safe)
            elif "LIMIT_HIT" in status_message:
                self.limit_hit_signal.emit(status_message) # Emette il segnale

            # Gestione invalidazione calibrazione (es. cambio gain PGA, sia da dialog
            # sia da reinvio automatico alla connessione con un gain diverso da quello di boot)
            elif "CALIBRATION_INVALIDATED" in status_message:
                self.active_calibration_info = "Not Calibrated"
                self.manual_control.set_calibration_status(self.active_calibration_info)
                self.monotonic_test_widget.set_calibration_status(self.active_calibration_info)
                self.calibration_widget.invalidate_calibration()
                self._log_event("calibration_invalidated", {"reason": status_message})
                QMessageBox.warning(self, "Ricalibrazione Necessaria",
                                    f"Il firmware ha invalidato la calibrazione corrente "
                                    f"({status_message}).\n\n"
                                    f"Esegui Tara e Calibrazione prima di usare la macchina.")

            # Gestione fine calibrazione: il firmware conferma il fattore di scala
            # reale calcolato (risposta a CALIBRATE:<grammi>), che la GUI non conosce
            # finché non arriva questo messaggio (necessario per "Save Calibration")
            elif "CALIBRATION_DONE" in status_message:
                try:
                    scale_factor = float(status_message.split("SCALE=")[1])
                    self.calibration_widget.set_calibration_factor(scale_factor)
                except (IndexError, ValueError):
                    print(f"Attenzione: impossibile interpretare il fattore di scala da '{status_message}'")

            # Gestione Homing
            elif "HOMING_COMPLETED" in status_message or "HOMED" in status_message:
                self.manual_control.is_homed = True
                self.monotonic_test_widget.set_homing_status(True)
                self.cyclic_test.set_homing_status(True)
                self.manual_control.reset_homing_ui()
                self.manual_control.update_displays() # Aggiorna subito i display
                # Un HOME riuscito è l'unico modo per uscire da "posizione non
                # verificata" (killswitch attivato in passato, o già premuto al boot).
                if self.position_unverified:
                    self.position_unverified = False
                    self._update_killswitch_indicator()
                    self._log_event("homing_completed")

            # Gestione Homing Interrotto
            elif "STOPPED_BY_USER" in status_message and self.manual_control.is_homing_active:
                self.manual_control.reset_homing_ui()

            # Gestione risposta a SET_ADS1220_CONFIG / GET_ADS1220_CONFIG: il
            # firmware riporta i valori REALMENTE applicati (non necessariamente
            # quelli richiesti — es. PGA_BYPASS viene ignorato se guadagno>=8x,
            # vedi CLAUDE.md), quindi allineiamo sempre lo stato GUI a questi.
            elif "ADS1220_CONFIG_SET" in status_message or "ADS1220_CONFIG;" in status_message:
                try:
                    fields = dict(kv.split("=") for kv in status_message.split(";")[1:])
                    new_sps = int(fields["SPS"])
                    new_gain = int(fields["GAIN"])
                    new_bypass_applied = (fields["PGA_BYPASS"] == "1")
                    new_idac = int(fields["IDAC"])
                    new_window = int(fields["WINDOW"])
                    bypass_was_ignored = (self.current_ads1220_pga_bypass and not new_bypass_applied
                                           and "ADS1220_CONFIG_SET" in status_message)
                    self.current_ads1220_sps = new_sps
                    self.current_ads1220_gain = new_gain
                    self.current_ads1220_pga_bypass = new_bypass_applied
                    self.current_ads1220_idac_ua = new_idac
                    self.current_ads1220_window = new_window
                    self.settings['ads1220_config'] = {
                        "sps": new_sps, "gain": new_gain, "pga_bypass": new_bypass_applied,
                        "idac_ua": new_idac, "window": new_window
                    }
                    self.settings_manager.save_settings(self.settings)
                    if bypass_was_ignored:
                        QMessageBox.information(self, "Bypass PGA Ignorato",
                                                 f"Il firmware ha ignorato la richiesta di bypass PGA perché "
                                                 f"il guadagno selezionato ({new_gain}x) è >= 8x: il PGA resta "
                                                 f"sempre attivo in questo caso (vincolo del datasheet ADS1220).")
                except (ValueError, IndexError, KeyError) as e:
                    print(f"Attenzione: impossibile interpretare {status_message}: {e}")

            elif "ADS1220_CONFIG_REJECTED" in status_message:
                if "POLLING_ACTIVE" in status_message:
                    QMessageBox.warning(self, "Configurazione ADS1220 Rifiutata",
                                        "Il firmware ha rifiutato la nuova configurazione ADS1220 perché "
                                        "il canale è attualmente in polling: disattivarlo prima di modificarla.")
                else:
                    QMessageBox.warning(self, "Configurazione ADS1220 Rifiutata",
                                        f"Il firmware ha rifiutato la nuova configurazione ADS1220 ({status_message}).")

            # Notifica cambio preset di step del jog encoder fisico (pulsante
            # integrato GPIO25): puramente informativo, solo per aggiornare
            # il display corrispondente in ManualControlWidget.
            elif "JOG_STEP_SIZE_SET" in status_message:
                try:
                    step_mm = float(status_message.split("MM=")[1])
                    self.manual_control.set_jog_step_size(step_mm)
                except (IndexError, ValueError):
                    print(f"Attenzione: impossibile interpretare JOG_STEP_SIZE_SET da '{status_message}'")

            # Altri messaggi di stato (es. TARE_DONE, CALIBRATION_DONE, etc.)
            # Vengono mostrati nella status bar ma non richiedono azioni specifiche qui.

            # Qualunque messaggio che indica che il motore si è comunque
            # fermato (fine movimento "Go To", endstop, limite di sicurezza,
            # killswitch) chiude lo stato "Go To in corso" sui widget di test,
            # se non l'ha già fatto l'utente cliccando lui stesso il pulsante
            # (che ora funge da STOP). Non è un elif: deve scattare in
            # aggiunta alla gestione specifica già eseguita sopra per questi
            # stessi messaggi (es. LIMIT_HIT, TOP_HIT/BOTTOM_HIT, KILLSWITCH_TRIGGERED).
            if any(code in status_message for code in
                   ("MOVE_COMPLETED", "STOPPED_BY_USER", "TOP_HIT", "BOTTOM_HIT", "LIMIT_HIT", "KILLSWITCH_TRIGGERED")):
                self.monotonic_test_widget.clear_goto_busy_state()
                self.cyclic_test.clear_goto_busy_state()

            return # Fine gestione messaggi STATUS:

        # --- GESTIONE MESSAGGI DI DATI ('D:') ---
        # Se non era un messaggio di stato, prova a interpretarlo come dati
        try:
            if data.startswith("D:"):
                payload = data[2:]
                parts = payload.split(';')

                # Inizializza i default QUI, dentro il blocco dove servono
                load_N = None
                displacement_mm = None
                time_s = 0.0
                cycle_count = 0
                resistance_lcr_ohm = -999.0 # Valore default/fallback (canale LCR-meter)
                resistance_ads_ohm = -999.0 # Valore default/fallback (canale ADS1220)
                encoder_count = None # Assente sui pacchetti storici (< 6 campi)

                # Parsing flessibile in base alla lunghezza
                if len(parts) == 7: # Formato con ADS1220 (RES_ADS), oltre a LCR ed encoder
                    load_str, disp_str, time_ms_str, cycle_str, res_lcr_str, enc_str, res_ads_str = parts
                    cycle_count = int(cycle_str)
                    try: resistance_lcr_ohm = float(res_lcr_str)
                    except ValueError: resistance_lcr_ohm = -2.0 # Errore parsing resistenza
                    try: encoder_count = int(enc_str)
                    except ValueError: encoder_count = None # Errore parsing encoder, tratta come assente
                    try: resistance_ads_ohm = float(res_ads_str)
                    except ValueError: resistance_ads_ohm = -2.0
                elif len(parts) == 6: # Formato con encoder esterno (Livello 1), senza ADS1220 (storico)
                    load_str, disp_str, time_ms_str, cycle_str, res_lcr_str, enc_str = parts
                    cycle_count = int(cycle_str)
                    try: resistance_lcr_ohm = float(res_lcr_str)
                    except ValueError: resistance_lcr_ohm = -2.0 # Errore parsing resistenza
                    try: encoder_count = int(enc_str)
                    except ValueError: encoder_count = None # Errore parsing encoder, tratta come assente
                elif len(parts) == 5: # Formato con LCR, senza encoder (storico)
                    load_str, disp_str, time_ms_str, cycle_str, res_lcr_str = parts
                    cycle_count = int(cycle_str)
                    try: resistance_lcr_ohm = float(res_lcr_str)
                    except ValueError: resistance_lcr_ohm = -2.0 # Errore parsing resistenza
                elif len(parts) == 4: # Vecchio formato streaming
                    load_str, disp_str, time_ms_str, cycle_str = parts
                    cycle_count = int(cycle_str)
                    # resistance_lcr_ohm/resistance_ads_ohm rimangono -999.0
                elif len(parts) == 3: # Formato Polling
                    load_str, disp_str, time_ms_str = parts
                    cycle_count = 0
                    # resistance_lcr_ohm/resistance_ads_ohm rimangono -999.0
                else:
                    raise ValueError(f"Pacchetto D: attesi 3, 4, 5, 6 o 7 valori, ricevuti {len(parts)}")

                # Parsing comune
                load_grams = float(load_str)
                pulse_count = int(disp_str)
                time_s = float(time_ms_str) / 1000.0
                load_N = (load_grams / 1000.0) * 9.81
                displacement_mm = pulse_count * self.PULSES_TO_MM
                encoder_displacement_mm = (
                    (encoder_count / self.ENCODER_COUNTS_PER_REV) * self.SCREW_PITCH_MM
                    if encoder_count is not None else None
                )

                # --- AZIONI SPOSTATE QUI DENTRO ---
                # Se siamo arrivati qui, il parsing è OK e tutte le variabili sono definite.

                current_widget = self.stacked_widget.currentWidget()

                # Aggiornamento centralizzato variabili assolute (per tutti i widget)
                widgets_to_update = [self.manual_control, self.monotonic_test_widget, self.cyclic_test]
                for widget in widgets_to_update:
                    if hasattr(widget, 'absolute_load_N'):
                         widget.absolute_load_N = load_N
                    if hasattr(widget, 'absolute_displacement_mm'):
                         widget.absolute_displacement_mm = displacement_mm
                    # Valori grezzi di entrambi i canali di resistenza (LCR ed ADS1220):
                    # ciascun widget sceglie da sé quale mostrare/salvare in base al
                    # proprio selettore locale "Sorgente" (vedi resistance_source_combo).
                    if hasattr(widget, 'current_resistance_lcr_ohm'):
                        widget.current_resistance_lcr_ohm = resistance_lcr_ohm
                    if hasattr(widget, 'current_resistance_ads_ohm'):
                        widget.current_resistance_ads_ohm = resistance_ads_ohm
                    if hasattr(widget, 'absolute_encoder_displacement_mm'):
                        widget.absolute_encoder_displacement_mm = encoder_displacement_mm

                # Calibrazione (caso speciale)
                if hasattr(self.calibration_widget, 'abs_load_display'):
                     self.calibration_widget.abs_load_display.set_value(f"{load_N:.3f}")

                # Chiama handle_stream_data del widget corrente (se esiste)
                if hasattr(current_widget, 'handle_stream_data'):
                    current_widget.handle_stream_data(load_N, displacement_mm, time_s, cycle_count,
                                                        resistance_lcr_ohm, resistance_ads_ohm, encoder_displacement_mm)

                # Aggiorna i display del widget corrente (se esiste)
                if hasattr(current_widget, 'update_displays'):
                    current_widget.update_displays()
                # --- FINE AZIONI SPOSTATE ---

            # Se non inizia con 'D:' (e non era 'STATUS:'), ignora silenziosamente

        except (ValueError, IndexError) as e:
            # Se c'è stato un errore durante il parsing di 'D:'
            print(f"ERRORE PARSING DATI: {e} | Dati: {data}")
            return # Ignora questa riga di dati
      

    def closeEvent(self, event):
        self.data_request_timer.stop()
        self.communicator.stop()
        self.comm_thread.quit()
        self.comm_thread.wait()
        event.accept()

    def update_calibration_status(self, status_text, cell_name):
        self.active_calibration_info = status_text
        self.active_cell_name = cell_name # Salva il nome della cella
        # Propaga l'informazione a tutti i widget interessati
        self.manual_control.set_calibration_status(self.active_calibration_info)
        self.monotonic_test_widget.set_calibration_status(self.active_calibration_info)
        # Se viene calibrata una nuova cella, aggiorna direttamente il limite di forza attivo.
        try:
            self.current_force_limit_N = float(cell_name.upper().replace("N", ""))
            self.send_limits_to_firmware()
        except (ValueError, TypeError):
            print(f"Attenzione: impossibile aggiornare il limite dal nome cella '{cell_name}'")


    def show_manual_control(self):
        self.manual_control.set_calibration_status(self.active_calibration_info)
        self.stacked_widget.setCurrentWidget(self.manual_control)
        
    def show_calibration(self): 
        self.stacked_widget.setCurrentWidget(self.calibration_widget)

    def show_monotonic_test(self):
        if self.manual_control.is_homed:
            # Assicura che la schermata sia aggiornata con lo stato più recente prima di essere mostrata
            self.monotonic_test_widget.set_homing_status(True)
            self.monotonic_test_widget.set_calibration_status(self.active_calibration_info)

            self.stacked_widget.setCurrentWidget(self.monotonic_test_widget)
        else:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setText("Homing Required")
            msg_box.setInformativeText("Please perform the homing procedure in 'Manual Control' before starting a test.")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()

    def show_cyclic_test(self):
        # Controlla se l'homing è stato fatto prima di accedere
        if self.manual_control.is_homed:
            self.cyclic_test.set_homing_status(True) # Informa la schermata
            self.stacked_widget.setCurrentWidget(self.cyclic_test)
        else:
            QMessageBox.warning(self, "Homing Richiesto", "Eseguire la procedura di Homing prima di avviare un test ciclico.")
    
    def show_main_menu(self):
        self.stacked_widget.setCurrentWidget(self.main_menu)

    def show_limit_hit_popup(self, status_message):
        # Se un popup critico è già visibile, non fare nulla.
        if self.is_critical_popup_active:
            return

        def perform_limit_hit_actions():
            # Alza la bandierina prima di mostrare il popup
            self.is_critical_popup_active = True
            
            QMessageBox.critical(self, "Limite di Sicurezza Raggiunto!", 
                                 f"Il motore è stato arrestato automaticamente.\n\n"
                                 f"Causa: {status_message}")
            
            # Abbassa la bandierina DOPO che l'utente ha chiuso il popup
            self.is_critical_popup_active = False
            
            if self.monotonic_test_widget.is_test_running:
                self.monotonic_test_widget.on_stop_test(user_initiated=False)
            
            # --- INIZIO CORREZIONE: Sblocca anche la UI ciclica ---
            if self.cyclic_test.is_test_running:
                self.cyclic_test.on_stop_test(user_initiated=False)
            # --- FINE CORREZIONE ---

        QTimer.singleShot(10, perform_limit_hit_actions)

    # --- KILLSWITCH: stato, indicatori, popup, log eventi ---
    def _log_event(self, event_type, details=None):
        """ Log di sistema persistente (event_log.jsonl), separato dai dati di
        misura delle prove: vedi event_logger.py. """
        self.event_logger.log(event_type, details)

    def _killswitch_visual_state(self):
        if self.killswitch_engaged:
            return "red"
        if self.position_unverified:
            return "yellow"
        return "green"

    def _update_killswitch_indicator(self):
        """ Propaga lo stato corrente del killswitch a: indicatori a 3 livelli
        (finestra principale + eventuali dialoghi LIMITS/Filter Config
        attualmente aperti), banner persistente, e ai widget che devono
        abilitare/disabilitare controlli di movimento in base allo stato. """
        state = self._killswitch_visual_state()
        for indicator in self._killswitch_indicators:
            indicator.set_state(state)

        if state == "red":
            self.killswitch_banner.set_state(
                "red", "KILLSWITCH ATTIVATO — motore arrestato. Rilasciare il killswitch per continuare.")
        elif state == "yellow":
            self.killswitch_banner.set_state(
                "yellow", "Posizione non verificata — eseguire HOMING prima di avviare una prova o un GOTO.")
        else:
            self.killswitch_banner.set_state("green")

        self.manual_control.set_killswitch_state(self.killswitch_engaged, self.position_unverified)
        self.monotonic_test_widget.set_killswitch_state(self.killswitch_engaged, self.position_unverified)
        self.cyclic_test.set_killswitch_state(self.killswitch_engaged, self.position_unverified)

    def _show_killswitch_popup(self):
        """ Popup NON bloccante: un avviso con pulsante di conferma che non
        impedisce di continuare a usare il resto del software mentre è aperto
        (a differenza di QMessageBox.exec(), qui si usa .show() con modalità
        non modale). """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Killswitch Attivato")
        box.setText("Il killswitch di emergenza è stato attivato.\n\n"
                    "Il motore è stato arrestato immediatamente. Rilasciare il killswitch "
                    "ed eseguire nuovamente l'HOMING prima di avviare una prova o un GOTO.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()

    def send_limits_to_firmware(self):
        """
        Costruisce e invia al firmware il comando SET_LIMITS usando i limiti
        assoluti correnti (self.current_force_limit_N / self.current_disp_limit_mm).
        """
        force_grams = (self.current_force_limit_N / 9.81) * 1000.0
        command = f"SET_LIMITS:FORCE_G={force_grams:.2f};DISP_MM={self.current_disp_limit_mm:.4f}"
        self.communicator.send_command(command)

    def show_limits_dialog(self):
        """
        Mostra la finestra di dialogo per impostare i limiti e invia il comando al firmware.
        """
        dialog = LimitsDialog(self.current_force_limit_N, self.current_disp_limit_mm, self)
        # Registra l'indicatore killswitch del dialog: resta una finestra
        # separata dalla principale, quindi deve riflettere live lo stato
        # (il dialog è modale, ma il thread seriale continua a emettere
        # segnali che vengono processati dal loop eventi durante l'exec()).
        dialog.killswitch_indicator.set_state(self._killswitch_visual_state())
        self._killswitch_indicators.append(dialog.killswitch_indicator)
        try:
            # Esegui la finestra di dialogo. Se l'utente preme "Save"...
            if dialog.exec():
                # Ottieni i valori inseriti dall'utente
                new_force_N, new_disp_mm = dialog.get_values()
                self.current_force_limit_N = new_force_N
                self.current_disp_limit_mm = new_disp_mm

                # Costruisci e invia il comando al firmware (logica condivisa)
                self.send_limits_to_firmware()

                # Messaggio di conferma per l'utente
                QMessageBox.information(self, "Limiti Impostati",
                                        f"Nuovi limiti macchina inviati:\n"
                                        f"- Forza Massima: {new_force_N:.3f} N\n"
                                        f"- Spostamento Massimo: {new_disp_mm:.4f} mm")
        finally:
            self._killswitch_indicators.remove(dialog.killswitch_indicator)

    def send_filter_config_to_firmware(self):
        """
        Costruisce e invia al firmware il comando SET_FILTER_CONFIG usando
        la configurazione filtro corrente (self.current_filter_alpha /
        self.current_filter_rate_sps / self.current_filter_pga_gain).
        """
        command = (f"SET_FILTER_CONFIG:ALPHA={self.current_filter_alpha:.3f};"
                   f"RATE={self.current_filter_rate_sps};GAIN={self.current_filter_pga_gain}")
        self.communicator.send_command(command)

    def show_filter_dialog(self):
        """
        Mostra la finestra di dialogo per configurare il filtro EMA della
        cella di carico e invia il comando al firmware.
        """
        dialog = FilterConfigDialog(self.current_filter_alpha, self.current_filter_rate_sps,
                                     self.current_filter_pga_gain, self)
        dialog.killswitch_indicator.set_state(self._killswitch_visual_state())
        self._killswitch_indicators.append(dialog.killswitch_indicator)
        try:
            if dialog.exec():
                new_alpha, new_rate_sps, new_gain = dialog.get_values()
                self.current_filter_alpha = new_alpha
                self.current_filter_rate_sps = new_rate_sps
                self.current_filter_pga_gain = new_gain
                self.settings['filter_config'] = {"alpha": new_alpha, "rate_sps": new_rate_sps, "gain": new_gain}
                self.settings_manager.save_settings(self.settings)
                self.send_filter_config_to_firmware()
                QMessageBox.information(self, "Filtro Configurato",
                                        f"Nuova configurazione filtro inviata:\n"
                                        f"- Alpha: {new_alpha:.2f}\n"
                                        f"- Sample Rate: {new_rate_sps} SPS\n"
                                        f"- Guadagno PGA: {new_gain}x")
        finally:
            self._killswitch_indicators.remove(dialog.killswitch_indicator)

    # --- ADS1220 (resistenza campioni) ---
    def _on_resistance_source_changed(self, source):
        """ Chiamato dal segnale resistance_source_changed di qualunque widget
        (Manual/Monotonic/Cyclic) quando l'utente cambia il selettore
        "Sorgente" ("OFF"/"LCR"/"ADS1220") su quella schermata. Serve solo a
        tracciare centralmente se il canale ADS1220 è attivo, per poter
        disabilitare "ADS1220 Settings" nel menu indipendentemente da quale
        schermata è attualmente visibile (il firmware ha un unico stato
        globale per il canale, non uno stato per schermata). """
        self.active_resistance_source = source

    def send_ads1220_config_to_firmware(self):
        """
        Costruisce e invia al firmware il comando SET_ADS1220_CONFIG usando
        la configurazione ADS1220 corrente. Il firmware la rifiuta
        (STATUS:ADS1220_CONFIG_REJECTED;REASON=POLLING_ACTIVE) se il canale
        è attualmente in polling: qui la inviamo comunque (es. subito dopo la
        connessione), il rifiuto viene gestito in handle_data_from_esp32().
        """
        command = (f"SET_ADS1220_CONFIG:SPS={self.current_ads1220_sps};"
                   f"GAIN={self.current_ads1220_gain};"
                   f"PGA_BYPASS={1 if self.current_ads1220_pga_bypass else 0};"
                   f"IDAC={self.current_ads1220_idac_ua};"
                   f"WINDOW={self.current_ads1220_window}")
        self.communicator.send_command(command)

    def show_ads1220_dialog(self):
        """
        Mostra la finestra di dialogo per configurare l'ADS1220. Disabilitato
        (con messaggio esplicativo) mentre il canale è attivo su una
        qualunque schermata: il firmware lo rifiuterebbe comunque, ma
        evitiamo di far compilare un form che verrà scartato.
        """
        if self.active_resistance_source == "ADS1220":
            QMessageBox.information(self, "Configurazione Non Disponibile",
                                    "Il canale ADS1220 è attualmente attivo (in polling) su una delle "
                                    "schermate. Disattivalo dal selettore 'Sorgente Resistenza' prima di "
                                    "modificarne la configurazione.")
            return

        dialog = ADS1220ConfigDialog(self.current_ads1220_sps, self.current_ads1220_gain,
                                      self.current_ads1220_pga_bypass, self.current_ads1220_idac_ua,
                                      self.current_ads1220_window, self)
        dialog.killswitch_indicator.set_state(self._killswitch_visual_state())
        self._killswitch_indicators.append(dialog.killswitch_indicator)
        try:
            if dialog.exec():
                new_sps, new_gain, new_pga_bypass, new_idac_ua, new_window = dialog.get_values()
                self.current_ads1220_sps = new_sps
                self.current_ads1220_gain = new_gain
                self.current_ads1220_pga_bypass = new_pga_bypass
                self.current_ads1220_idac_ua = new_idac_ua
                self.current_ads1220_window = new_window
                self.send_ads1220_config_to_firmware()
                # Nota: il messaggio di conferma effettivo (coi valori REALMENTE
                # applicati dal firmware, es. PGA_BYPASS forzato se gain>=8x)
                # arriva in modo asincrono su STATUS:ADS1220_CONFIG_SET, gestito
                # in handle_data_from_esp32().
        finally:
            self._killswitch_indicators.remove(dialog.killswitch_indicator)



if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())