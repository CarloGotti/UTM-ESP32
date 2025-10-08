import json
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QComboBox, QDialog, QTableWidget, 
                             QTableWidgetItem, QDialogButtonBox, QFileDialog, 
                             QHeaderView, QGridLayout)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from custom_widgets import DisplayWidget

class SetLoadsDialog(QDialog):
    # (Questa classe interna rimane invariata)
    def __init__(self, cal_loads, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Imposta Carichi di Calibrazione")
        self.cal_loads = cal_loads
        self.setMinimumSize(450, 300)
        
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setRowCount(len(self.cal_loads))
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Cella", "Zero Load (g)", "Calibrating Load (g)"])
        
        for row, cell_name in enumerate(self.cal_loads):
            values = self.cal_loads[cell_name]
            zero_load = values[0] if len(values) > 0 else 0.0
            cal_load = values[1] if len(values) > 1 else 0.0

            cell_item = QTableWidgetItem(cell_name)
            cell_item.setFlags(cell_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, cell_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(zero_load)))
            self.table.setItem(row, 2, QTableWidgetItem(str(cal_load)))
        
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_updated_loads(self):
        updated_loads = {}
        for row in range(self.table.rowCount()):
            cell_name = self.table.item(row, 0).text()
            try:
                zero_load = float(self.table.item(row, 1).text())
                cal_load = float(self.table.item(row, 2).text())
                updated_loads[cell_name] = [zero_load, cal_load]
            except (ValueError, TypeError):
                return self.cal_loads
        return updated_loads

class CalibrationWidget(QWidget):
    back_to_menu_requested = pyqtSignal()
    calibration_updated = pyqtSignal(str, str)
    settings_changed = pyqtSignal(dict)
    
    # NUOVO: Segnale per richiedere il salvataggio
    save_calibration_requested = pyqtSignal(str)

    def __init__(self, communicator, cal_loads, parent=None):
        super().__init__(parent)
        self.communicator = communicator
        self.cal_loads = cal_loads
        self.calibration_state = "IDLE"
        
        button_font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        label_font = QFont("Segoe UI", 12)
        main_layout = QGridLayout(self)
        
        self.cell_selector = QComboBox()
        self.cell_selector.addItems(self.cal_loads.keys())
        self.cell_selector.setFont(label_font)
        
        self.load_cal_button = QPushButton("Load Calibration")
        self.start_cal_button = QPushButton("Start Calibration")
        self.save_cal_button = QPushButton("Save Calibration")
        self.set_loads_button = QPushButton("Set Calibrating Loads")
        self.back_button = QPushButton("Back to Menu")

        self.save_cal_button.setEnabled(False)

        for btn in [self.load_cal_button, self.start_cal_button, self.save_cal_button, self.set_loads_button, self.back_button]:
            btn.setFont(button_font)
            btn.setMinimumHeight(40)

        self.status_label = QLabel("Pronto per la calibrazione.")
        status_font = QFont("Segoe UI", 11); status_font.setItalic(True)
        self.status_label.setFont(status_font)
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.abs_load_display = DisplayWidget("Absolute Load (N)")
        
        main_layout.setRowStretch(0, 1); main_layout.setRowStretch(2, 1)
        main_layout.setColumnStretch(0, 1); main_layout.setColumnStretch(2, 1)

        center_widget = QWidget(); center_layout = QVBoxLayout(center_widget)
        main_layout.addWidget(center_widget, 1, 1)

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Seleziona Cella:", font=label_font))
        top_layout.addWidget(self.cell_selector); top_layout.addStretch(1)
        top_layout.addWidget(self.abs_load_display)

        buttons_layout = QGridLayout()
        buttons_layout.addWidget(self.load_cal_button, 0, 0)
        buttons_layout.addWidget(self.start_cal_button, 0, 1)
        buttons_layout.addWidget(self.save_cal_button, 1, 0)
        buttons_layout.addWidget(self.set_loads_button, 1, 1)

        center_layout.addLayout(top_layout); center_layout.addStretch(1)
        center_layout.addWidget(self.status_label); center_layout.addStretch(1)
        center_layout.addLayout(buttons_layout); center_layout.addStretch(2)
        center_layout.addWidget(self.back_button)

        self.back_button.clicked.connect(self.back_to_menu_requested.emit)
        self.start_cal_button.clicked.connect(self.handle_calibration_step)
        self.set_loads_button.clicked.connect(self.show_set_loads_dialog)
        self.save_cal_button.clicked.connect(self.save_calibration)
        self.load_cal_button.clicked.connect(self.load_calibration)

    def handle_calibration_step(self):
        if self.calibration_state == "IDLE":
            self.calibration_state = "WAITING_FOR_ZERO"
            self.status_label.setText("Rimuovere clamp e pesi. Clicca 'Continua'.")
            self.start_cal_button.setText("Continua (Tara)")
            self.save_cal_button.setEnabled(False)
        elif self.calibration_state == "WAITING_FOR_ZERO":
            self.communicator.send_command("TARE")
            selected_cell = self.cell_selector.currentText()
            cal_weight = self.cal_loads[selected_cell][1]
            self.calibration_state = "WAITING_FOR_WEIGHT"
            self.status_label.setText(f"Aggiungere peso di calibrazione ({cal_weight}g).\nClicca 'Continua'.")
            self.start_cal_button.setText("Continua (Pesa)")
        elif self.calibration_state == "WAITING_FOR_WEIGHT":
            selected_cell = self.cell_selector.currentText()
            cal_weight = self.cal_loads[selected_cell][1]
            self.communicator.send_command(f"CALIBRATE:{cal_weight}")
            self.calibration_state = "IDLE"
            self.status_label.setText("Calibrazione completata!")
            self.start_cal_button.setText("Start Calibration")
            self.save_cal_button.setEnabled(True)
            self.calibration_updated.emit("Just Calibrated", selected_cell)

    def show_set_loads_dialog(self):
        dialog = SetLoadsDialog(self.cal_loads, self)
        if dialog.exec():
            updated_loads = dialog.get_updated_loads()
            if updated_loads != self.cal_loads:
                self.cal_loads = updated_loads
                self.settings_changed.emit(self.cal_loads)

    def save_calibration(self):
        selected_cell = self.cell_selector.currentText()
        today_date = datetime.now().strftime("%Y-%m-%d")
        default_filename = f"cal_{selected_cell}_{today_date}.json"
        filePath, _ = QFileDialog.getSaveFileName(self, "Salva Calibrazione", default_filename, "Calibration Files (*.json)")
        if filePath:
            # Emette il segnale per dire a MainWindow di gestire il salvataggio
            self.save_calibration_requested.emit(filePath)
    
    def load_calibration(self):
        filePath, _ = QFileDialog.getOpenFileName(self, "Carica Calibrazione", "", "Calibration Files (*.json)")
        if filePath:
            try:
                with open(filePath, 'r') as f:
                    data = json.load(f)
                scale_factor = data.get('calibration_factor')
                if scale_factor is not None:
                    # Invia il comando all'ESP32 per impostare il nuovo fattore di scala
                    self.communicator.send_command(f"SET_SCALE:{scale_factor}")
                    filename = filePath.split('/')[-1]
                    self.status_label.setText(f"Calibrazione caricata da:\n{filename}")
                    self.save_cal_button.setEnabled(True)
                    try:
                        cell_name_from_file = filename.split('_')[1]
                    except IndexError:
                        cell_name_from_file = "N/A" # Non trovato nel nome del file
                    self.calibration_updated.emit(filename, cell_name_from_file)
                else:
                    self.status_label.setText("Errore: file di calibrazione non valido.")
            except (IOError, json.JSONDecodeError) as e:
                self.status_label.setText(f"Errore caricamento file: {e}")