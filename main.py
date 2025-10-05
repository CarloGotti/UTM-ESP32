import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QStackedWidget, QComboBox, 
                             QPushButton, QHBoxLayout, QWidget, QStatusBar, QLabel, 
                             QVBoxLayout, QMessageBox)
from PyQt6.QtCore import QThread, QTimer

from main_menu_widget import MainMenuWidget
from manual_control_widget import ManualControlWidget
from calibration_widget import CalibrationWidget
from monotonic_test_widget import MonotonicTestWidget
from communication import SerialCommunicator 
from settings_manager import SettingsManager

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Software Controllo Macchina di Trazione")
        self.resize(1200, 800)
        
        self.settings_manager = SettingsManager()
        self.settings = self.settings_manager.load_settings()

        self.active_calibration_info = "Not Calibrated"
        self.PULSES_PER_REV = 2000.0; self.GEAR_RATIO = 10.0; self.SCREW_PITCH_MM = 5.0873
        self.PULSES_TO_MM = self.SCREW_PITCH_MM / (self.PULSES_PER_REV * self.GEAR_RATIO)
        
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

        self.calibration_widget.calibration_updated.connect(self.update_calibration_status)
        self.calibration_widget.settings_changed.connect(self.save_cal_load_settings)

        self.refresh_ports_button.clicked.connect(self.populate_ports)
        self.connect_button.clicked.connect(self.connect_device)
        self.disconnect_button.clicked.connect(self.disconnect_device)
        
        self.communicator.data_received.connect(self.handle_data_from_esp32)
        self.communicator.connected.connect(self.on_connected)
        self.communicator.disconnected.connect(self.on_disconnected)
        self.communicator.port_error.connect(lambda msg: self.statusBar().showMessage(msg))

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
        if data.startswith("DATA:"):
            try:
                payload = data.replace("DATA:", ""); parts = payload.split(';')
                data_dict = {p.split('=')[0]: float(p.split('=')[1]) for p in parts}
                
                if 'LOAD' in data_dict:
                    load_grams = data_dict['LOAD']; load_N = (load_grams / 1000.0) * 9.81
                    self.manual_control.absolute_load_N = load_N
                    self.calibration_widget.abs_load_display.set_value(f"{load_N:.3f}")

                if 'DISP' in data_dict:
                    pulse_count = data_dict['DISP']
                    self.manual_control.absolute_displacement_mm = pulse_count * self.PULSES_TO_MM
                
                current_widget = self.stacked_widget.currentWidget()
                if hasattr(current_widget, 'update_displays'): current_widget.update_displays()
            except (ValueError, IndexError): pass
        
        elif data.startswith("STATUS:"):
            status_message = data.replace("STATUS:", "")
            self.statusBar().showMessage(f"Status: {status_message}", 5000)
            if "HOMING_DONE" in status_message:
                self.manual_control.is_homed = True
                self.manual_control.reset_homing_ui()
                self.manual_control.update_displays()
            elif "HIT" in status_message and self.manual_control.is_homing_active:
                self.manual_control.reset_homing_ui()

    def closeEvent(self, event):
        self.data_request_timer.stop()
        self.communicator.stop()
        self.comm_thread.quit()
        self.comm_thread.wait()
        event.accept()

    def update_calibration_status(self, status_text):
        self.active_calibration_info = status_text
        self.manual_control.set_calibration_status(self.active_calibration_info)

    def show_manual_control(self):
        self.manual_control.set_calibration_status(self.active_calibration_info)
        self.stacked_widget.setCurrentWidget(self.manual_control)
        
    def show_calibration(self): self.stacked_widget.setCurrentWidget(self.calibration_widget)

    def show_monotonic_test(self):
        if self.manual_control.is_homed:
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

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())