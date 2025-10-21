import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QStackedWidget, QComboBox, 
                             QPushButton, QHBoxLayout, QWidget, QStatusBar, QLabel, 
                             QVBoxLayout, QListWidgetItem, QMessageBox)
from PyQt6.QtCore import QThread, QTimer, pyqtSignal

from main_menu_widget import MainMenuWidget
from manual_control_widget import ManualControlWidget
from calibration_widget import CalibrationWidget
from monotonic_test_widget import MonotonicTestWidget
from cyclic_test_widget import CyclicTestWidget
from communication import SerialCommunicator 
from settings_manager import SettingsManager
from custom_widgets import LimitsDialog


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

        self.PULSES_PER_REV = 2000.0; self.GEAR_RATIO = 10.0; self.SCREW_PITCH_MM = 5.0873
        self.PULSES_TO_MM = self.SCREW_PITCH_MM / (self.PULSES_PER_REV * self.GEAR_RATIO)
        
        self.default_force_limit_N = 100.0
        self.current_force_limit_N = self.default_force_limit_N
        self.current_disp_limit_mm = 190.0

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
        connection_bar.addWidget(self.connect_button); connection_bar.addWidget(self.disconnect_button)

        main_layout.addLayout(connection_bar); main_layout.addWidget(self.stacked_widget)
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
        self.main_menu.monotonic_button.clicked.connect(self.show_monotonic_test)
        self.main_menu.cyclic_button.clicked.connect(self.show_cyclic_test)
        
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
        self.communicator.send_command("SET_MODE:POLLING")
        self.data_request_timer.start()
        
    def on_disconnected(self):
        self.data_request_timer.stop()
        self.connect_button.setEnabled(True); self.disconnect_button.setEnabled(False)
        self.refresh_ports_button.setEnabled(True); self.port_selector.setEnabled(True)
        self.statusBar().showMessage("Disconnesso.")

    def handle_data_from_esp32(self, data: str):
        print(f"[ESP32 RAW]: {data}")
        
        # --- BLOCCO UNICO PER LA GESTIONE DEI MESSAGGI ---
        
        # Se è un messaggio di STATO, gestiscilo e termina.
        if data.startswith("STATUS:"):
            status_message = data.replace("STATUS:", "")
            self.statusBar().showMessage(f"Status: {status_message}", 5000)
            # --- GESTIONE MESSAGGI CICLICI ---
            if "CYCLIC_TEST_STARTED" in status_message or "CYCLIC_PREPOSITIONING" in status_message:
                # La UI è già stata aggiornata da on_start_test
                pass # Non fare nulla di speciale qui
            
            elif "BLOCK_COMPLETED" in status_message:
                widget = self.cyclic_test # Riferimento al widget ciclico
                widget.current_block_index += 1 # Passa al blocco successivo

                if widget.current_block_index < len(widget.test_sequence):
                    # C'è un altro blocco, invia il comando
                    next_block = widget.test_sequence[widget.current_block_index]

                    if next_block["type"] == "cyclic":
                        # --- BLOCCO CICLICO (invariato) ---
                        control_mode_base = next_block["base_unit"].upper()
                        if control_mode_base == "MM":
                            control_mode_fw = "DISP"
                            abs_upper_mm = next_block["upper_conv"] + widget.displacement_offset_mm
                            abs_lower_mm = next_block["lower_conv"] + widget.displacement_offset_mm
                            upper_fw = abs_upper_mm; lower_fw = abs_lower_mm
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
                        self.communicator.send_command(command)
                        print(f"DEBUG Main: Avviato Blocco Ciclico {widget.current_block_index + 1}")

                    elif next_block["type"] == "pause":
                        # --- BLOCCO PAUSA (invariato) ---
                        duration_ms = int(next_block["duration"] * 1000)
                        command = f"EXECUTE_PAUSE:{duration_ms}"
                        self.communicator.send_command(command)
                        print(f"DEBUG Main: Avviata Pausa {widget.current_block_index + 1} ({duration_ms} ms)")

                    # --- NUOVO BLOCCO PER GESTIRE LA RAMPA ---
                    elif next_block["type"] == "ramp":
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

                        # Costruisci il nuovo comando per il firmware
                        command = (f"EXECUTE_RAMP:"
                                   f"MODE={control_mode_fw};" # DISP o FORCE
                                   f"TARGET={target_fw:.4f};" # mm o grammi ASSOLUTI
                                   f"SPEED={speed_mms:.3f};"
                                   f"HOLD={hold_ms}")
                        self.communicator.send_command(command)
                        print(f"DEBUG Main: Avviata Rampa {widget.current_block_index + 1}")
                    # --- FINE NUOVO BLOCCO RAMPA ---
                else:
                     # --- INIZIO CORREZIONE ---
                     # Non ci sono altri blocchi. La sequenza è completata.
                     # Chiama manualmente la funzione di stop della UI.
                     print(f"DEBUG Main: Sequenza completata. Tutti i {widget.current_block_index} blocchi eseguiti.")
                     self.communicator.send_command("SET_MODE:POLLING")
                     if self.cyclic_test.is_test_running:
                         self.cyclic_test.on_stop_test(user_initiated=False)
                         QMessageBox.information(self, "Test Ciclico Terminato", "Sequenza di test completata.")
                     # --- FINE CORREZIONE ---


            elif "CYCLIC_TEST_COMPLETED" in status_message or \
                 ("CYCLIC_TEST_STOPPED_BY_USER" in status_message and self.cyclic_test.is_test_running) or \
                 ("TOP_HIT" in status_message and self.cyclic_test.is_test_running) or \
                 ("BOTTOM_HIT" in status_message and self.cyclic_test.is_test_running):
                 
                 # Il test è finito (completato, stoppato, o endstop colpito), aggiorna la UI
                 if self.cyclic_test.is_test_running:
                     self.cyclic_test.on_stop_test(user_initiated=False)
                     # Mostra un popup diverso se è un endstop
                     if "TOP_HIT" in status_message or "BOTTOM_HIT" in status_message:
                         QMessageBox.critical(self, "Endstop Colpito", f"Test interrotto: {status_message}")
                     else:
                         QMessageBox.information(self, "Test Ciclico Terminato", f"Il test si è concluso con stato: {status_message}")
            # Gestione Limiti di Sicurezza
            if "LIMIT_HIT" in status_message:
                QTimer.singleShot(10, lambda: self.show_limit_hit_popup(status_message))
            
            # Gestione Homing (LA SOLUZIONE)
            elif "HOMING_COMPLETED" in status_message or "HOMED" in status_message:
                # Imposta lo stato 'homed' su entrambi i widget
                self.manual_control.is_homed = True
                self.monotonic_test_widget.set_homing_status(True)
                # *** NUOVO: Imposta homing anche per il widget ciclico ***
                self.cyclic_test.set_homing_status(True) 
                # Ripristina la UI del controllo manuale
                self.manual_control.reset_homing_ui()
                # Aggiorna i display per mostrare i valori numerici e rimuovere "Unhomed"
                self.manual_control.update_displays() 

            # Gestione Homing Interrotto dall'utente
            elif "STOPPED_BY_USER" in status_message and self.manual_control.is_homing_active:
                self.manual_control.reset_homing_ui()

            # Gestione Stop del Test Monotonico (da comando o da fine test)
            elif "TEST_COMPLETED" in status_message or \
                 ("TEST_STOPPED_BY_USER" in status_message and self.monotonic_test_widget.is_test_running) or \
                 ("TOP_HIT" in status_message and self.monotonic_test_widget.is_test_running) or \
                 ("BOTTOM_HIT" in status_message and self.monotonic_test_widget.is_test_running):
                 
                if self.monotonic_test_widget.is_test_running:
                    self.monotonic_test_widget.on_stop_test(user_initiated=False)
                    # Mostra un popup diverso se è un endstop
                    if "TOP_HIT" in status_message or "BOTTOM_HIT" in status_message:
                        QMessageBox.critical(self, "Endstop Colpito", f"Test interrotto: {status_message}")
                    else:
                        QMessageBox.information(self, "Test Terminato", f"Il test si è concluso con stato: {status_message}")
            
            
            return # I messaggi di stato non contengono dati di telemetria, quindi usciamo.

        # Se non è un messaggio di stato, allora è un messaggio di DATI.
        load_N = None
        displacement_mm = None
        time_s = 0.0
        cycle_count = 0 # Inizializza
        try:
            # Ora gestiamo SOLO il formato "D:", sia per lo streaming che per il polling
            if data.startswith("D:"):
                payload = data[2:]
                
                # --- QUESTA È LA CORREZIONE DEFINITIVA ---
                parts = payload.split(';') # Divide il payload in una lista
                
                if len(parts) == 4:
                    # Formato STREAMING (load, pulses, time, cycle)
                    load_str, disp_str, time_ms_str, cycle_str = parts
                    cycle_count = int(cycle_str)
                elif len(parts) == 3:
                    # Formato POLLING (load, pulses, time=0)
                    load_str, disp_str, time_ms_str = parts
                    cycle_count = 0 # Imposta il ciclo a 0 di default
                else:
                    # Pacchetto corrotto o numero di valori inatteso
                    raise ValueError(f"Attesi 3 o 4 valori, ricevuti {len(parts)}")
                
                # Parsing comune
                load_grams = float(load_str)
                pulse_count = int(disp_str)
                time_s = float(time_ms_str) / 1000.0
                # --- FINE CORREZIONE ---

                load_N = (load_grams / 1000.0) * 9.81
                displacement_mm = pulse_count * self.PULSES_TO_MM
            else:
                return # Se non è un messaggio 'D:' o 'STATUS:', ignora

        except (ValueError, IndexError) as e:
            print(f"ERRORE PARSING: {e} | Dati: {data}") # Aggiunto debug
            return # Ignora righe dati corrotte o malformate

        # Se il parsing dei dati ha avuto successo, aggiorna l'applicazione
        if load_N is not None and displacement_mm is not None:
            current_widget = self.stacked_widget.currentWidget()

            # --- AGGIORNAMENTO CENTRALIZZATO (Come nella versione STABLE) ---
            # Aggiorna le variabili assolute in TUTTI i widget che le possiedono
            
            # Manual Control
            if hasattr(self.manual_control, 'absolute_load_N'):
                self.manual_control.absolute_load_N = load_N
                self.manual_control.absolute_displacement_mm = displacement_mm
                
            # Monotonic Test
            if hasattr(self.monotonic_test_widget, 'absolute_load_N'):
                self.monotonic_test_widget.absolute_load_N = load_N
                self.monotonic_test_widget.absolute_displacement_mm = displacement_mm
                
            # Cyclic Test (Aggiunto)
            if hasattr(self.cyclic_test, 'absolute_load_N'):
                self.cyclic_test.absolute_load_N = load_N
                self.cyclic_test.absolute_displacement_mm = displacement_mm

            # Calibration Widget (Caso speciale)
            if hasattr(self.calibration_widget, 'abs_load_display'):
                 self.calibration_widget.abs_load_display.set_value(f"{load_N:.3f}")
            # --- FINE AGGIORNAMENTO CENTRALIZZATO ---

            # Invia i dati in streaming al widget corrente (per i grafici)
            if hasattr(current_widget, 'handle_stream_data'):
                # Passa tutti i dati, inclusi tempo e ciclo
                current_widget.handle_stream_data(load_N, displacement_mm, time_s, cycle_count)
            
            # --- AGGIORNAMENTO DISPLAY (COME NELLA VERSIONE STABLE) ---
            # Aggiorna i display del widget corrente (es. i contatori)
            if hasattr(current_widget, 'update_displays'):
                current_widget.update_displays()
      

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

    def show_limits_dialog(self):
        """
        Mostra la finestra di dialogo per impostare i limiti e invia il comando al firmware.
        """
        dialog = LimitsDialog(self.current_force_limit_N, self.current_disp_limit_mm, self)
        
        # Esegui la finestra di dialogo. Se l'utente preme "Save"...
        if dialog.exec():
            # Ottieni i valori inseriti dall'utente
            new_force_N, new_disp_mm = dialog.get_values()
            self.current_force_limit_N = new_force_N
            self.current_disp_limit_mm = new_disp_mm
            
            # 1. Prepara i valori per il firmware
            # Converte la forza da Newton a grammi
            force_grams = (new_force_N / 9.81) * 1000.0
            
            # 2. Costruisci la stringa di comando
            command = f"SET_LIMITS:FORCE_G={force_grams:.2f};DISP_MM={new_disp_mm:.4f}"
            # NUOVA RIGA DI DEBUG
            #print(f"DEBUG GUI: Invio comando '{command}'")
            
            # 3. Invia il comando all'ESP32
            self.communicator.send_command(command)
            
            # Messaggio di conferma per l'utente
            QMessageBox.information(self, "Limiti Impostati", 
                                    f"Nuovi limiti macchina inviati:\n"
                                    f"- Forza Massima: {new_force_N:.3f} N\n"
                                    f"- Spostamento Massimo: {new_disp_mm:.4f} mm")



if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())