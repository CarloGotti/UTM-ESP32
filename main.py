import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QStackedWidget, QComboBox, 
                             QPushButton, QHBoxLayout, QWidget, QStatusBar, QLabel, 
                             QVBoxLayout, QListWidgetItem, QMessageBox)
from PyQt6.QtCore import QThread, QTimer, pyqtSignal

from main_menu_widget import MainMenuWidget
from manual_control_widget import ManualControlWidget
from calibration_widget import CalibrationWidget
from monotonic_test_widget import MonotonicTestWidget
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
        self.monotonic_test_widget = MonotonicTestWidget(self.communicator)
        
        self.stacked_widget.addWidget(self.main_menu); self.stacked_widget.addWidget(self.manual_control)
        self.stacked_widget.addWidget(self.calibration_widget); self.stacked_widget.addWidget(self.monotonic_test_widget)
        
        self.main_menu.manual_button.clicked.connect(self.show_manual_control)
        self.main_menu.calibrate_button.clicked.connect(self.show_calibration)
        self.main_menu.monotonic_button.clicked.connect(self.show_monotonic_test)
        
        self.manual_control.back_to_menu_requested.connect(self.show_main_menu)
        self.calibration_widget.back_to_menu_requested.connect(self.show_main_menu)
        self.monotonic_test_widget.back_to_menu_requested.connect(self.show_main_menu)

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
        print(f"[ESP32 RAW]: {data}") # --- NUOVA RIGA DI DEBUG ---
        if data.startswith("DATA:"):
            try:
                payload = data.replace("DATA:", ""); parts = payload.split(';')
                data_dict = {p.split('=')[0]: float(p.split('=')[1]) for p in parts}
                
                load_N = 0.0
                displacement_mm = self.manual_control.absolute_displacement_mm # Mantiene l'ultimo valore se non arriva
                
                if 'LOAD' in data_dict:
                    load_grams = data_dict['LOAD']; load_N = (load_grams / 1000.0) * 9.81
                    self.manual_control.absolute_load_N = load_N
                    self.monotonic_test_widget.absolute_load_N = load_N
                    self.calibration_widget.abs_load_display.set_value(f"{load_N:.3f}")

                if 'DISP' in data_dict:
                    pulse_count = data_dict['DISP']
                    displacement_mm = pulse_count * self.PULSES_TO_MM
                    self.manual_control.absolute_displacement_mm = displacement_mm
                    self.monotonic_test_widget.absolute_displacement_mm = displacement_mm
                
                # Se il test è in corso, invia i dati per il grafico
                if self.monotonic_test_widget.is_test_running:
                    self.monotonic_test_widget.handle_stream_data(load_N, displacement_mm)

                # Aggiorna i display solo del widget visibile
                current_widget = self.stacked_widget.currentWidget()
                if hasattr(current_widget, 'update_displays'): 
                    current_widget.update_displays()

            except (ValueError, IndexError): 
                pass 
        


        elif data.startswith("D:"):
        # nuovo formato snellito (streaming)
            try:
                payload = data[2:]  # rimuove "D:"
                load_str, disp_str = payload.split(';')
                load_grams = float(load_str)
                pulse_count = int(disp_str)

                load_N = (load_grams / 1000.0) * 9.81
                displacement_mm = pulse_count * self.PULSES_TO_MM

                self.manual_control.absolute_load_N = load_N
                self.monotonic_test_widget.absolute_load_N = load_N
                self.manual_control.absolute_displacement_mm = displacement_mm
                self.monotonic_test_widget.absolute_displacement_mm = displacement_mm

                if self.monotonic_test_widget.is_test_running:
                    self.monotonic_test_widget.handle_stream_data(load_N, displacement_mm)

                current_widget = self.stacked_widget.currentWidget()
                if hasattr(current_widget, 'update_displays'):
                    current_widget.update_displays()

            except Exception:
                pass

        elif data.startswith("STATUS:"):
            status_message = data.replace("STATUS:", "")
            self.statusBar().showMessage(f"Status: {status_message}", 5000)

            # --- NUOVO BLOCCO PER GESTIONE LIMITI ---
            # Controlla se il messaggio di stato indica che un limite è stato raggiunto.
            # L'uso di "in" cattura sia LIMIT_HIT_FORCE che LIMIT_HIT_DISPLACEMENT.
            if "LIMIT_HIT" in status_message:
                # Non creare il popup qui!
                # Emetti il segnale per chiedere al thread della GUI di farlo.
                self.limit_hit_signal.emit(status_message)
            # --- FINE NUOVO BLOCCO ---
            
            if "HOMING_DONE" in status_message:
                self.manual_control.is_homed = True
                self.manual_control.reset_homing_ui()
                self.manual_control.update_displays()
            elif "HIT" in status_message and self.manual_control.is_homing_active:
                self.manual_control.reset_homing_ui()
            # NUOVA GESTIONE STATO TEST

            elif "TEST_STARTED" in status_message:
                # conferma che il test è partito davvero
                self.monotonic_test_widget.is_test_running = True
                self.monotonic_test_widget.update_ui_for_test_state()  
            elif "TEST_COMPLETED" in status_message or "TEST_STOPPED_BY_USER" in status_message or "TOP_HIT" in status_message:
                print(f"DEBUG MAIN: stop test triggerato da status = {status_message}")
                if self.monotonic_test_widget.is_test_running:
                    self.monotonic_test_widget.on_stop_test(user_initiated=False)
                    QMessageBox.information(self, "Test Finished", f"The test ended with status: {status_message}")
      

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
            self.monotonic_test_widget.set_current_force_limit(self.current_force_limit_N)
            self.stacked_widget.setCurrentWidget(self.monotonic_test_widget)
        else:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setText("Homing Required")
            msg_box.setInformativeText("Please perform the homing procedure in 'Manual Control' before starting a test.")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()

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