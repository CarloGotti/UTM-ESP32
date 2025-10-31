# cyclic_test_widget.py

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGridLayout, QFrame, QMessageBox, QSpinBox, QDoubleSpinBox,
    QListWidget, QListWidgetItem, QComboBox, QTabWidget, QDialog, QDialogButtonBox, QFormLayout, QCheckBox, QInputDialog,QLineEdit, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QLocale, QTimer
from PyQt6.QtGui import QFont
import pyqtgraph as pg
import numpy as np
from collections import deque
from datetime import datetime
import time
from data_saver import DataSaver

from custom_widgets import DisplayWidget # Assicurati che DisplayWidget sia importato

class BlockDialog(QDialog):
    def __init__(self, force_limit, disp_limit, disp_offset, load_offset, parent=None):
        super().__init__(parent)
        self.force_limit = force_limit
        self.disp_limit = disp_limit
        self.disp_offset = disp_offset
        self.load_offset = load_offset
        
        self.setWindowTitle("Define Cyclic Block (Relative Values)")
        self.setMinimumWidth(450)
        locale_c = QLocale("C")
        layout = QFormLayout(self)

        # 1. Tipo di Controllo (Aggiornato)
        self.control_type_combo = QComboBox()
        self.control_type_combo.addItems(["Displacement (mm)", "Strain (%)", "Force (N)", "Stress (MPa)"])
        layout.addRow("Control Type:", self.control_type_combo)

        # 2. Limiti
        self.upper_limit_spinbox = QDoubleSpinBox()
        self.upper_limit_spinbox.setLocale(locale_c)
        self.upper_limit_spinbox.setDecimals(4)
        self.upper_limit_spinbox.setRange(-50000.0, 50000.0) # Range relativo
        layout.addRow("Upper Limit (Relative):", self.upper_limit_spinbox)

        self.lower_limit_spinbox = QDoubleSpinBox()
        self.lower_limit_spinbox.setLocale(locale_c)
        self.lower_limit_spinbox.setDecimals(4)
        self.lower_limit_spinbox.setRange(-50000.0, 50000.0) # Range relativo
        layout.addRow("Lower Limit (Relative):", self.lower_limit_spinbox)

        # 3. Velocità (Aggiornato)
        speed_layout = QHBoxLayout()
        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setLocale(locale_c)
        self.speed_spinbox.setDecimals(3)
        self.speed_spinbox.setRange(0.001, 5000.0)
        self.speed_spinbox.setValue(1.0)
        self.speed_unit_combo = QComboBox()
        self.speed_unit_combo.addItems(["mm/s", "mm/min", "%/s", "%/min"])
        speed_layout.addWidget(self.speed_spinbox, 1)
        speed_layout.addWidget(self.speed_unit_combo, 1)
        layout.addRow("Speed:", speed_layout)

        # 4. Tempi di Mantenimento (Invariato)
        self.hold_upper_spinbox = QDoubleSpinBox()
        self.hold_upper_spinbox.setLocale(locale_c)
        self.hold_upper_spinbox.setSuffix(" s")
        self.hold_upper_spinbox.setDecimals(2)
        self.hold_upper_spinbox.setRange(0.0, 3600.0) 
        layout.addRow("Hold at Upper Limit:", self.hold_upper_spinbox)

        self.hold_lower_spinbox = QDoubleSpinBox()
        self.hold_lower_spinbox.setLocale(locale_c)
        self.hold_lower_spinbox.setSuffix(" s")
        self.hold_lower_spinbox.setDecimals(2)
        self.hold_lower_spinbox.setRange(0.0, 3600.0)
        layout.addRow("Hold at Lower Limit:", self.hold_lower_spinbox)

        # 5. Numero di Cicli (Invariato)
        self.cycles_spinbox = QSpinBox()
        self.cycles_spinbox.setRange(1, 1000000)
        self.cycles_spinbox.setValue(10)
        layout.addRow("Number of Cycles:", self.cycles_spinbox)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.control_type_combo.currentIndexChanged.connect(self._update_units)
        self._update_units()

    def _update_units(self):
        """Aggiorna i suffissi dei limiti in base al tipo di controllo."""
        control_text = self.control_type_combo.currentText()
        if "Displacement" in control_text: unit = " mm"
        elif "Strain" in control_text: unit = " %"
        elif "Force" in control_text: unit = " N"
        elif "Stress" in control_text: unit = " MPa"
        else: unit = ""
        self.upper_limit_spinbox.setSuffix(unit)
        self.lower_limit_spinbox.setSuffix(unit)

    def get_data(self):
        """Ritorna i dati *grezzi* del blocco in un dizionario."""
        control_text = self.control_type_combo.currentText()
        
        if "Displacement" in control_text: control_type_short = "DISP"
        elif "Strain" in control_text: control_type_short = "STRAIN"
        elif "Force" in control_text: control_type_short = "FORCE"
        else: control_type_short = "STRESS"

        data = {
            "type": "cyclic",
            # Dati grezzi per la conversione
            "control_text": control_text, # Es: "Strain (%)"
            "control": control_type_short,    # Es: "STRAIN"
            "upper": self.upper_limit_spinbox.value(),
            "lower": self.lower_limit_spinbox.value(),
            "speed": self.speed_spinbox.value(),
            "speed_unit": self.speed_unit_combo.currentText(),
            # Dati diretti
            "hold_upper": self.hold_upper_spinbox.value(),
            "hold_lower": self.hold_lower_spinbox.value(),
            "cycles": self.cycles_spinbox.value()
        }
        return data

    def accept(self):
        """ Sovrascrive accept per validare i dati prima di chiudere. """
        # La validazione complessa (conversione) verrà fatta in on_add_block
        # Qui facciamo solo un controllo base
        relative_upper = self.upper_limit_spinbox.value()
        relative_lower = self.lower_limit_spinbox.value()
        
        if relative_upper <= relative_lower:
            QMessageBox.warning(self, "Input Error", "Upper limit must be greater than lower limit.")
            return # Non chiudere la finestra

        # La validazione sui limiti macchina (che richiede gauge/area)
        # verrà fatta nel widget principale
        super().accept()

class PauseDialog(QDialog):
    def __init__(self, current_duration=5.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Define Pause Block")
        locale_c = QLocale("C")
        layout = QFormLayout(self)

        self.duration_spinbox = QDoubleSpinBox()
        self.duration_spinbox.setLocale(locale_c)
        self.duration_spinbox.setSuffix(" s")
        self.duration_spinbox.setDecimals(1)
        self.duration_spinbox.setRange(0.1, 3600.0) # Da 0.1s a 1 ora
        self.duration_spinbox.setValue(current_duration)
        layout.addRow("Pause Duration:", self.duration_spinbox)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_duration(self):
        return self.duration_spinbox.value()

class RampDialog(QDialog):
    def __init__(self, parent=None): # Semplificato per ora, aggiungeremo validazione limiti dopo
        super().__init__(parent)
        self.setWindowTitle("Define Ramp Block (GoTo Target)")
        self.setMinimumWidth(450)
        locale_c = QLocale("C")
        layout = QFormLayout(self)

        # 1. Tipo di Controllo Target
        self.control_type_combo = QComboBox()
        self.control_type_combo.addItems(["Displacement (mm)", "Strain (%)", "Force (N)", "Stress (MPa)"])
        layout.addRow("Target Control Type:", self.control_type_combo)

        # 2. Target Value (Relativo)
        self.target_value_spinbox = QDoubleSpinBox()
        self.target_value_spinbox.setLocale(locale_c)
        self.target_value_spinbox.setDecimals(4)
        self.target_value_spinbox.setRange(-50000.0, 50000.0) # Range relativo ampio
        layout.addRow("Target Value (Relative):", self.target_value_spinbox)

        # 3. Velocità
        speed_layout = QHBoxLayout()
        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setLocale(locale_c)
        self.speed_spinbox.setDecimals(3)
        self.speed_spinbox.setRange(0.001, 5000.0) # Range velocità ampio
        self.speed_spinbox.setValue(1.0)
        self.speed_unit_combo = QComboBox()
        self.speed_unit_combo.addItems(["mm/s", "mm/min", "%/s", "%/min"])
        speed_layout.addWidget(self.speed_spinbox, 1)
        speed_layout.addWidget(self.speed_unit_combo, 1)
        layout.addRow("Ramp Speed:", speed_layout)

        # 4. Tempo di Mantenimento (Hold) Opzionale
        self.hold_duration_spinbox = QDoubleSpinBox()
        self.hold_duration_spinbox.setLocale(locale_c)
        self.hold_duration_spinbox.setSuffix(" s")
        self.hold_duration_spinbox.setDecimals(2)
        self.hold_duration_spinbox.setRange(0.0, 3600.0) # Da 0s (no hold) a 1 ora
        self.hold_duration_spinbox.setValue(0.0) # Default no hold
        layout.addRow("Hold Duration at Target:", self.hold_duration_spinbox)

        # Pulsanti OK / Cancel
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept) # Validazione minima qui
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Aggiorna unità target
        self.control_type_combo.currentIndexChanged.connect(self._update_units)
        self._update_units() # Chiama subito

    def _update_units(self):
        """Aggiorna i suffissi del target in base al tipo di controllo."""
        control_text = self.control_type_combo.currentText()
        if "Displacement" in control_text: unit = " mm"
        elif "Strain" in control_text: unit = " %"
        elif "Force" in control_text: unit = " N"
        elif "Stress" in control_text: unit = " MPa"
        else: unit = ""
        self.target_value_spinbox.setSuffix(unit)

    def get_data(self):
        """Ritorna i dati *grezzi* del blocco rampa."""
        control_text = self.control_type_combo.currentText()
        # Mappatura testo -> ID breve (per coerenza con BlockDialog)
        if "Displacement" in control_text: control_type_short = "DISP"
        elif "Strain" in control_text: control_type_short = "STRAIN"
        elif "Force" in control_text: control_type_short = "FORCE"
        else: control_type_short = "STRESS"

        data = {
            "type": "ramp",
            "control_text": control_text,
            "control": control_type_short, # Usiamo lo stesso nome di campo di BlockDialog
            "target": self.target_value_spinbox.value(), # Valore relativo
            "speed": self.speed_spinbox.value(),
            "speed_unit": self.speed_unit_combo.currentText(),
            "hold_duration": self.hold_duration_spinbox.value()
        }
        return data

    def accept(self):
        # Aggiungeremo la validazione sui limiti macchina nel widget principale
        super().accept()

class SpecimenDialog(QDialog):
    """ Finestra di dialogo per creare o modificare i dati di un provino. """
    def __init__(self, current_data=None, existing_names=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Specimen Details")
        self.setMinimumWidth(400)
        locale_c = QLocale("C")
        layout = QFormLayout(self)
        
        self.existing_names = existing_names if existing_names else []
        self.original_name = current_data.get("name", "") if current_data else ""

        # Campi di input
        self.name_edit = QLineEdit()
        self.gauge_length_edit = QDoubleSpinBox()
        self.gauge_length_edit.setLocale(locale_c)
        self.gauge_length_edit.setSuffix(" mm")
        self.gauge_length_edit.setDecimals(3)
        self.gauge_length_edit.setRange(0.001, 10000.0)
        self.area_edit = QDoubleSpinBox()
        self.area_edit.setLocale(locale_c)
        self.area_edit.setSuffix(" mm²")
        self.area_edit.setDecimals(3)
        self.area_edit.setRange(0.001, 10000.0)

        layout.addRow("Name:", self.name_edit)
        layout.addRow("Gauge Length:", self.gauge_length_edit)
        layout.addRow("Area:", self.area_edit)

        # Pre-compila se stiamo modificando
        if current_data:
            self.name_edit.setText(self.original_name)
            self.gauge_length_edit.setValue(current_data.get("gauge_length", 1.0))
            self.area_edit.setValue(current_data.get("area", 1.0))

        # Pulsanti OK / Cancel
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept) # Connette a un metodo custom per validare
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        """ Valida i dati prima di chiudere. """
        name = self.name_edit.text().strip()
        gauge = self.gauge_length_edit.value()
        area = self.area_edit.value()

        if not name:
            QMessageBox.warning(self, "Input Error", "Specimen name cannot be empty.")
            return # Non chiudere
        
        # Controlla se il nome esiste già (solo se è diverso dall'originale)
        if name != self.original_name and name in self.existing_names:
            QMessageBox.warning(self, "Input Error", f"Specimen with name '{name}' already exists.")
            return # Non chiudere
            
        if gauge <= 0 or area <= 0:
            QMessageBox.warning(self, "Input Error", "Gauge length and area must be positive.")
            return # Non chiudere

        # Se tutto ok, procedi con la chiusura
        super().accept()

    def get_data(self):
        """ Ritorna i dati inseriti. """
        return {
            "name": self.name_edit.text().strip(),
            "gauge_length": self.gauge_length_edit.value(),
            "area": self.area_edit.value()
        }

class CyclicTestWidget(QWidget):
    back_to_menu_requested = pyqtSignal()
    limits_button_requested = pyqtSignal() # Segnale per i limiti

    def __init__(self, communicator, main_window, parent=None):
        super().__init__(parent)
        self.communicator = communicator
        self.main_window = main_window

        # --- STATO INTERNO ---
        self.is_test_running = False
        self.is_homed = False
        self.absolute_load_N = 0.0
        self.load_offset_N = 0.0
        self.absolute_displacement_mm = 0.0
        self.displacement_offset_mm = 0.0
        self.current_cycle = 0
        self.elapsed_time_s = 0.0
        self.test_sequence = []
        self.specimens = {} # Aggiunto per gestione batch
        self.current_specimen_name = None # Aggiunto per gestione batch
        self.current_test_data = []
        self.current_resistance_ohm = -999.0 # Per memorizzare l'ultimo valore LCR
        # --- FONT E LOCALE ---
        general_font = QFont("Segoe UI", 11)
        button_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        title_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        locale_c = QLocale("C")

        # --- LAYOUT PRINCIPALE ---
        main_layout = QVBoxLayout(self)

        # --- 1. SEZIONE SUPERIORE (Display) - INVARIATA ---
        top_section_layout = QHBoxLayout()
        self.abs_load_display = DisplayWidget("Absolute Load (N)")
        self.rel_load_display = DisplayWidget("Relative Load (N)")
        self.abs_disp_display = DisplayWidget("Absolute Displacement (mm)")
        self.rel_disp_display = DisplayWidget("Relative Displacement (mm)")
        self.cycle_display = DisplayWidget("Current Cycle")
        self.time_display = DisplayWidget("Elapsed Time (s)")
        self.current_block_display = DisplayWidget("Current Block")
        self.resistance_display = DisplayWidget("Resistance (Ω)") # <-- NUOVO DISPLAY
        self.lcr_enable_checkbox = QCheckBox("Enable LCR Reading")

        top_section_layout.addWidget(self.abs_load_display)
        top_section_layout.addWidget(self.rel_load_display)
        top_section_layout.addWidget(self.abs_disp_display)
        top_section_layout.addWidget(self.rel_disp_display)
        top_section_layout.addWidget(self.cycle_display)
        top_section_layout.addWidget(self.time_display)
        top_section_layout.addWidget(self.current_block_display)
        top_section_layout.addWidget(self.resistance_display)
        top_section_layout.addStretch(1)
        top_section_layout.addWidget(self.lcr_enable_checkbox)

        jog_controls_layout = QVBoxLayout()
        self.up_button = QPushButton("↑ UP ↑"); self.up_button.setFont(button_font)
        self.down_button = QPushButton("↓ DOWN ↓"); self.down_button.setFont(button_font)
        jog_speed_layout = QHBoxLayout()
        self.jog_speed_spinbox = QDoubleSpinBox()
        self.jog_speed_spinbox.setLocale(locale_c)
        self.jog_speed_spinbox.setSuffix(" mm/s")
        self.jog_speed_spinbox.setFixedWidth(100)
        self.jog_speed_spinbox.setRange(0.001, 25.0) # Assumi stessi limiti
        self.jog_speed_spinbox.setValue(10.0) # Valore di default
        jog_speed_layout.addWidget(QLabel("Jog Speed:"))
        jog_speed_layout.addWidget(self.jog_speed_spinbox)
        jog_controls_layout.addWidget(self.up_button)
        jog_controls_layout.addWidget(self.down_button)
        jog_controls_layout.addLayout(jog_speed_layout)
        # Aggiungi il layout dei controlli jog al layout superiore
        top_section_layout.addLayout(jog_controls_layout)


        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine); separator1.setFrameShadow(QFrame.Shadow.Sunken)

        # --- 2. SEZIONE CENTRALE (Grafico e Pannello a Tab) ---
        center_layout = QHBoxLayout()

        # --- Pannello Sinistro: Grafico (Invariato) ---
        graph_layout = QVBoxLayout()
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w'); self.plot_widget.showGrid(x=True, y=True)
        self.plot_widget.setLabel('left', 'Relative Load (N)'); self.plot_widget.setLabel('bottom', 'Relative Displacement (mm)')
        self.plot_curve = self.plot_widget.plot(pen=pg.mkPen('b', width=2))

        self.resistance_axis_viewbox = None # La ViewBox per la resistenza
        self.resistance_axis_item = None    # L'AxisItem a destra
        self.resistance_curve = None

        
        graph_controls_layout = QHBoxLayout()
        self.x_axis_combo = QComboBox(); self.x_axis_combo.addItems(["Relative Displacement (mm)", "Time (s)", "Strain (%)"])
        self.y_axis_combo = QComboBox(); self.y_axis_combo.addItems(["Relative Load (N)", "Stress (MPa)", "Relative Displacement (mm)", "Strain (%)"])
        self.overlay_checkbox = QCheckBox("Overlay previous tests")
        self.reset_zoom_button = QPushButton("Reset Zoom")
        
        graph_controls_layout.addWidget(QLabel("X-Axis:")); graph_controls_layout.addWidget(self.x_axis_combo, 1)
        graph_controls_layout.addWidget(QLabel("Y-Axis:")); graph_controls_layout.addWidget(self.y_axis_combo, 1)
        graph_controls_layout.addStretch(1)
        graph_controls_layout.addWidget(self.overlay_checkbox)
        graph_controls_layout.addWidget(self.reset_zoom_button)
        graph_layout.addWidget(self.plot_widget); graph_layout.addLayout(graph_controls_layout)

        # --- Pannello Destro: Interfaccia a Tab ---
        self.right_tabs = QTabWidget()

        # -- Tab 1: Definizione Sequenza --
        self.sequence_page = QWidget()
        sequence_layout = QVBoxLayout(self.sequence_page)
        sequence_layout.addWidget(QLabel("Test Sequence:", font=title_font))
        self.sequence_list = QListWidget()
        sequence_layout.addWidget(self.sequence_list)

        sequence_buttons_layout = QGridLayout()
        self.add_block_button = QPushButton("Add Cyclic Block")
        self.add_pause_button = QPushButton("Add Pause")
        self.add_ramp_button = QPushButton("Add Ramp Block")
        self.edit_block_button = QPushButton("Edit")
        self.remove_block_button = QPushButton("Remove")
        self.move_up_button = QPushButton("↑ Move Up")   
        self.move_down_button = QPushButton("↓ Move Down") 

        sequence_buttons_layout.addWidget(self.add_block_button, 0, 0)
        sequence_buttons_layout.addWidget(self.add_pause_button, 0, 1)
        sequence_buttons_layout.addWidget(self.add_ramp_button, 0, 2)
        sequence_buttons_layout.addWidget(self.edit_block_button, 1, 0)
        sequence_buttons_layout.addWidget(self.move_up_button, 1, 1)
        sequence_buttons_layout.addWidget(self.move_down_button, 1, 2) 

        sequence_buttons_layout.addWidget(self.remove_block_button, 2, 0)
  

        
 
        sequence_layout.addLayout(sequence_buttons_layout)
       
        self.estimated_duration_label = QLabel("Estimated Duration: N/A")
        sequence_layout.addWidget(self.estimated_duration_label)
        sequence_layout.addStretch(1)

        # -- Tab 2: Gestione Batch & Overlay --
        self.batch_page = QWidget()
        batch_layout = QVBoxLayout(self.batch_page)
        self.specimen_list = QListWidget() # Lista dei provini del batch
        batch_layout.addWidget(QLabel("Test Batch:", font=title_font))
        batch_layout.addWidget(self.specimen_list)
        specimen_buttons_layout = QHBoxLayout()
        self.new_button = QPushButton("NEW"); self.modify_button = QPushButton("MODIFY"); self.delete_button = QPushButton("DELETE")
        specimen_buttons_layout.addWidget(self.new_button); specimen_buttons_layout.addWidget(self.modify_button); specimen_buttons_layout.addWidget(self.delete_button)
        batch_layout.addLayout(specimen_buttons_layout)
        
        self.overlay_list = QListWidget() # Lista per l'overlay con checkbox
        batch_layout.addWidget(QLabel("Overlay Selection:", font=title_font))
        batch_layout.addWidget(self.overlay_list)

        # Aggiungi le pagine al TabWidget
        self.right_tabs.addTab(self.sequence_page, "Sequence Setup")
        self.right_tabs.addTab(self.batch_page, "Batch & Overlay")
        
        center_layout.addLayout(graph_layout, 3)
        center_layout.addWidget(QFrame(frameShape=QFrame.Shape.VLine, frameShadow=QFrame.Shadow.Sunken))
        center_layout.addWidget(self.right_tabs, 1) # Aggiunge il pannello a tab

        # --- 3. SEZIONE INFERIORE (Controlli Test e Generali - Layout Corretto) ---
        bottom_layout = QHBoxLayout() # Layout principale per la riga inferiore

        # Controlli START/STOP (a sinistra)
        self.start_button = QPushButton("▶ START")
        self.start_button.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.start_button.setStyleSheet("background-color: #2ECC71; color: white;")
        # Rimuoviamo altezza minima fissa per START/STOP se non necessaria, altrimenti reinseriscila
        # self.start_button.setMinimumHeight(60) 
        self.stop_button = QPushButton("■ STOP")
        self.stop_button.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.stop_button.setStyleSheet("background-color: #E74C3C; color: white;")
        # self.stop_button.setMinimumHeight(60)

        bottom_layout.addWidget(self.start_button)
        bottom_layout.addWidget(self.stop_button)
        bottom_layout.addStretch(1) # Spazio flessibile tra START/STOP e gli altri

        # Controlli Zero, Limiti, Salva (a destra, in una griglia 2x2)
        general_controls_layout = QGridLayout()
        self.zero_rel_load_button = QPushButton("Zero Relative Load")
        self.zero_rel_disp_button = QPushButton("Zero Relative Displacement")
        self.limits_button = QPushButton("LIMITS")
        self.limits_button.setStyleSheet("background-color: #F39C12; color: white;")
        self.finish_save_button = QPushButton("FINISH & SAVE")

        # Applica font e altezza standard (button_font) a questi 4 pulsanti
        for btn in [self.zero_rel_load_button, self.zero_rel_disp_button, self.limits_button, self.finish_save_button]:
             btn.setFont(button_font) # Usa il font più piccolo definito prima
             btn.setMinimumHeight(35) # Altezza standard (come in Monotonic)

        # Posiziona i pulsanti nella griglia (2x2)
        general_controls_layout.addWidget(self.zero_rel_load_button, 0, 0)
        general_controls_layout.addWidget(self.limits_button, 0, 1)
        general_controls_layout.addWidget(self.zero_rel_disp_button, 1, 0)
        general_controls_layout.addWidget(self.finish_save_button, 1, 1)

        bottom_layout.addLayout(general_controls_layout) # Aggiunge la griglia al layout principale

        # La definizione e aggiunta di self.back_button rimangono più avanti
        self.back_button = QPushButton("Back to Menu")
        self.back_button.setFont(button_font)
        self.back_button.setMinimumHeight(40)

        # --- ASSEMBLAGGIO FINALE ---
        main_layout.addLayout(top_section_layout)
        main_layout.addWidget(separator1)
        main_layout.addLayout(center_layout)
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine); separator2.setFrameShadow(QFrame.Shadow.Sunken)
        # --- ----------------------------- ---
        main_layout.addWidget(separator2)
        main_layout.addLayout(bottom_layout)
        main_layout.addWidget(self.back_button)

        # --- CONNESSIONI ---
        # (Qui collegheremo i nuovi pulsanti ai loro metodi)
        self.back_button.clicked.connect(self.back_to_menu_requested.emit)
        self.limits_button.clicked.connect(self.limits_button_requested.emit)
        self.reset_zoom_button.clicked.connect(lambda: self.plot_widget.enableAutoRange())
        self.zero_rel_load_button.clicked.connect(self.zero_relative_load)
        self.zero_rel_disp_button.clicked.connect(self.zero_relative_displacement)
        self.start_button.clicked.connect(self.on_start_test)
        self.stop_button.clicked.connect(lambda: self.on_stop_test(user_initiated=True))
        self.add_block_button.clicked.connect(self.on_add_block)
        self.add_pause_button.clicked.connect(self.on_add_pause)
        self.add_ramp_button.clicked.connect(self.on_add_ramp)
        self.edit_block_button.clicked.connect(self.on_edit_block)
        self.remove_block_button.clicked.connect(self.on_remove_block)
        self.sequence_list.itemSelectionChanged.connect(self._update_sequence_buttons_state)
        self.move_up_button.clicked.connect(self.on_move_block_up)    
        self.move_down_button.clicked.connect(self.on_move_block_down)
        self.new_button.clicked.connect(self.on_new_specimen)
        self.modify_button.clicked.connect(self.on_modify_specimen)
        self.delete_button.clicked.connect(self.on_delete_specimen)
        self.specimen_list.itemClicked.connect(self.on_specimen_selected)
        self.overlay_list.itemChanged.connect(self.on_overlay_item_changed)
        self.overlay_checkbox.stateChanged.connect(self.refresh_plot) # Collega la checkbox
        self.finish_save_button.clicked.connect(self.on_finish_and_save)
        

        self.up_button.pressed.connect(self.start_moving_up)
        self.up_button.released.connect(self.stop_moving)
        self.down_button.pressed.connect(self.start_moving_down)
        self.down_button.released.connect(self.stop_moving)
        self.jog_speed_spinbox.valueChanged.connect(self.set_speed)
        # Aggiorna il grafico se cambiano gli assi
        self.x_axis_combo.currentIndexChanged.connect(self.refresh_plot)
        self.y_axis_combo.currentIndexChanged.connect(self.refresh_plot)
        self.lcr_enable_checkbox.stateChanged.connect(self._on_lcr_checkbox_changed)

        self.update_ui_for_test_state()
        self.update_displays()
        self._update_sequence_buttons_state()

    # --- Metodi Placeholder per la Logica ---

    def on_start_test(self):

        # --- VALIDAZIONE PROVINO (invariata) ---
        if self.current_specimen_name is None:
            QMessageBox.warning(self, "Attenzione", "Selezionare un provino dalla scheda 'Batch & Overlay' prima di avviare.")
            self.right_tabs.setCurrentWidget(self.batch_page)
            return

        specimen = self.specimens[self.current_specimen_name]

        if specimen.get("test_data") is not None:
            reply = QMessageBox.question(
                self, "Conferma Sovrascrittura",
                f"Il provino '{self.current_specimen_name}' ha già dei dati di test. Sovrascrivere?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return
            else:
                specimen['test_data'] = None
        # --- FINE VALIDAZIONE PROVINO ---

        # --- VALIDAZIONI PRELIMINARI (invariate) ---
        if self.is_test_running or not self.is_homed or not self.test_sequence:
            QMessageBox.warning(self, "Attenzione", "Impossibile avviare il test. Controllare Homing e Sequenza.")
            return
        # --- FINE VALIDAZIONI PRELIMINARI ---

        # Prendi il primo blocco dalla sequenza
        first_block = self.test_sequence[0]
        block_type = first_block["type"]
        command = "" # Inizializza comando

        # --- PREPARA IL COMANDO PER IL FIRMWARE (Logica estesa) ---
        if block_type == "cyclic":
            # --- BLOCCO CICLICO (Come prima) ---
            control_mode_base = first_block["base_unit"].upper()
            if control_mode_base == "MM":
                control_mode_fw = "DISP"
                abs_upper_mm = first_block["upper_conv"] + self.displacement_offset_mm
                abs_lower_mm = first_block["lower_conv"] + self.displacement_offset_mm
                upper_fw = abs_upper_mm; lower_fw = abs_lower_mm
            else: # "N"
                control_mode_fw = "FORCE"
                abs_upper_N = first_block["upper_conv"] + self.load_offset_N
                abs_lower_N = first_block["lower_conv"] + self.load_offset_N
                upper_fw = (abs_upper_N / 9.81) * 1000.0
                lower_fw = (abs_lower_N / 9.81) * 1000.0
            speed_mms = first_block["speed_mms"]
            hold_upper_ms = int(first_block["hold_upper"] * 1000)
            hold_lower_ms = int(first_block["hold_lower"] * 1000)
            cycles = first_block["cycles"]
            command = (
                f"START_CYCLIC_TEST:"
                f"MODE={control_mode_fw};UPPER={upper_fw:.4f};LOWER={lower_fw:.4f};"
                f"SPEED={speed_mms:.3f};HOLD_U={hold_upper_ms};HOLD_L={hold_lower_ms};CYCLES={cycles}"
            )
            print_debug_msg = f"DEBUG: Avviato Blocco Ciclico 1 ({command})"

        # --- NUOVO BLOCCO PER GESTIRE LA RAMPA COME PRIMO BLOCCO ---
        elif block_type == "ramp":
            control_mode_base = first_block["base_unit"].upper() # Sarà "MM" o "N"
            if control_mode_base == "MM":
                control_mode_fw = "DISP"
                abs_target_mm = first_block["target_conv"] + self.displacement_offset_mm
                target_fw = abs_target_mm # Il firmware si aspetta mm
            else: # "N"
                control_mode_fw = "FORCE"
                abs_target_N = first_block["target_conv"] + self.load_offset_N
                target_fw = (abs_target_N / 9.81) * 1000.0 # Converti N assoluti in grammi

            speed_mms = first_block["speed_mms"]
            hold_ms = int(first_block["hold_duration"] * 1000)

            # Costruisci il comando EXECUTE_RAMP (identico a main.py)
            command = (f"EXECUTE_RAMP:"
                       f"MODE={control_mode_fw};" # DISP o FORCE
                       f"TARGET={target_fw:.4f};" # mm o grammi ASSOLUTI
                       f"SPEED={speed_mms:.3f};"
                       f"HOLD={hold_ms}")
            print_debug_msg = f"DEBUG: Avviata Rampa 1 ({command})"
        # --- FINE NUOVO BLOCCO RAMPA ---

        elif block_type == "pause":
             QMessageBox.warning(self, "Errore Sequenza", "La sequenza non può iniziare con una pausa.")
             return # Non inviare comandi

        # --- INVIO COMANDI E AGGIORNAMENTO STATO (se un comando è stato preparato) ---
        if command:
            # Resetta il timer e i cicli nel firmware SOLO all'inizio della sequenza
            self.communicator.send_command("RESET_TIMER")

            self.current_test_data = [] # Svuota i dati del test *imminente*
            self.refresh_plot() # Pulisce grafico e prepara curva live

            self.current_block_index = 0 # Siamo al primo blocco

            self.communicator.send_command(command) # Invia il comando del primo blocco (Ciclo o Rampa)
            self.communicator.send_command("SET_MODE:STREAMING")

            self.is_test_running = True
            self.update_ui_for_test_state()
            self.update_displays()
            print(print_debug_msg) # Stampa il messaggio di debug corretto
        else:
             print("DEBUG: Nessun comando valido da inviare per il primo blocco.") # Debug aggiuntivo
    
    def on_stop_test(self, user_initiated=True):
        print(f"DEBUG Cyclic: on_stop_test chiamato (user_initiated={user_initiated})")

        # Se il test non è in corso, non fare nulla
        if not self.is_test_running:
            return
            
        # Se lo stop è avviato dall'utente (click), invia i comandi di stop.
        if user_initiated:
            print("DEBUG Cyclic: Invio stop emergenza dall'utente")
            self.communicator.send_emergency_stop()      # kill switch immediato
            self.communicator.send_command("STOP")       # Comando di stop logico
            self.communicator.send_command("SET_MODE:POLLING") # Ripristina polling
            
            # La UI si aggiornerà solo quando il firmware risponde
            return 
 
        # --- Questa parte viene eseguita SOLO quando chiamata da MainWindow ---
        
        print("DEBUG Cyclic: Eseguo logica di stop post-conferma firmware")
        self.is_test_running = False
        self.update_ui_for_test_state()
        self.current_cycle = 0 # Azzera il contatore cicli
        self.elapsed_time_s = 0.0 # Azzera il tempo
        self.current_block_index = 0
        
        # --- INIZIO CORREZIONE: SALVATAGGIO DATI ---
        if self.current_specimen_name:
            # Salva i dati del test appena concluso
            self.specimens[self.current_specimen_name]['test_data'] = self.current_test_data
            print(f"DEBUG Cyclic: Dati salvati per {self.current_specimen_name}")

            # --- NUOVO: LOGICA DI AUTOSAVE ---
            try:
                # Prepara i dati del provino, INCLUDENDO la sequenza di test
                specimen_data_to_save = {
                    **self.specimens[self.current_specimen_name],
                    "test_sequence_setup": self.test_sequence 
                }
                specimen_to_save = {self.current_specimen_name: specimen_data_to_save}

                # Crea un nome di file automatico
                filename = f"AUTOSAVE_CYCLIC_{self.current_specimen_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

                saver = DataSaver()
                # Salva il singolo provino usando la stessa logica del batch
                # Passiamo None come info calibrazione per ora (o aggiungila se serve)
                saver.save_batch_to_xlsx(specimen_to_save, filename, "N/A") 
                print(f"DEBUG: Autosave ciclico completato per {self.current_specimen_name} in {filename}")
            except Exception as e:
                print(f"ERRORE AUTOSAVE CICLICO: {e}")
            # --- FINE AUTOSAVE ---
        
        self.update_displays() # Aggiorna i display
        
        # Aggiorna il grafico per mostrare i dati salvati (e l'overlay)
        self.refresh_plot()
        # --- FINE CORREZIONE ---
    
    def handle_stream_data(self, load_N, disp_mm, time_s, cycle_count, resistance_ohm):
        if not self.is_test_running:
            return

        # 1. Aggiorna stato (invariato)
        self.absolute_load_N = load_N
        self.absolute_displacement_mm = disp_mm
        self.elapsed_time_s = time_s
        self.current_cycle = cycle_count
        self.current_resistance_ohm = resistance_ohm
        relative_disp = disp_mm - self.displacement_offset_mm
        relative_load = load_N - self.load_offset_N

        # 2. Aggiunge dati (invariato)
        current_block_num = self.current_block_index + 1
        self.current_test_data.append((time_s, relative_disp, relative_load, disp_mm, load_N, cycle_count, current_block_num, resistance_ohm))

        # 3. Aggiorna il grafico
        specimen = self.specimens.get(self.current_specimen_name, 
                                     {"gauge_length": 1.0, "area": 1.0}) 
        area = specimen.get("area", 1.0)
        gauge = specimen.get("gauge_length", 1.0)
        x_mode = self.x_axis_combo.currentText()
        y_mode = self.y_axis_combo.currentText()

        # Estrai dati (invariato)
        times = [p[0] for p in self.current_test_data]
        x_raw_data = [p[1] for p in self.current_test_data] 
        y_raw_data = [p[2] for p in self.current_test_data]

        # Converte X (invariato)
        if "Strain" in x_mode and gauge > 0: x_data_final = [(d / gauge) * 100 for d in x_raw_data]
        elif "Time" in x_mode: x_data_final = times
        else: x_data_final = x_raw_data

        # Converte Y (invariato)
        if "Stress" in y_mode and area > 0: y_data_final = [(f / area) for f in y_raw_data]
        elif "Strain" in y_mode and gauge > 0: y_data_final = [(d / gauge) * 100 for d in x_raw_data]
        elif "Displacement" in y_mode: y_data_final = x_raw_data
        else: y_data_final = y_raw_data

        # --- MODIFICA (Fix Problema 3) ---
        # Disegna sulla curva principale (self.plot_curve)
        if self.plot_curve: # CONTROLLO DI SICUREZZA
            self.plot_curve.setData(x_data_final, y_data_final)
        # --- FINE MODIFICA ---
        
        # Aggiorna le etichette degli assi (invariato)
        self.plot_widget.setLabel("bottom", x_mode)
        self.plot_widget.setLabel("left", y_mode)
            
        # --- AGGIUNTA PER CURVA RESISTENZA (STREAMING) ---
        if self.resistance_curve: # Controlla se l'asse è attivo
            try:
                # Estrai i dati di resistenza (indice 7)
                r_raw_data = [p[7] for p in self.current_test_data]
                r_data_final = [r if r >= 0 else np.nan for r in r_raw_data]
                self.resistance_curve.setData(x_data_final, r_data_final)
            except Exception as e:
                print(f"Errore aggiornamento curva resistenza live (Cyclic): {e}")
        # --- FINE AGGIUNTA ---

        # 4. Aggiorna i display (invariato)
        self.update_displays()

    def update_displays(self):
        #print(f"DEBUG Cyclic UpdateDisplays: AbsLoad={self.absolute_load_N:.3f}, Offset={self.load_offset_N:.3f}")
        relative_load = self.absolute_load_N - self.load_offset_N
        relative_disp = self.absolute_displacement_mm - self.displacement_offset_mm
        self.abs_load_display.set_value(f"{self.absolute_load_N:.3f}")
        self.rel_load_display.set_value(f"{relative_load:.3f}")
        if self.is_homed:
            self.abs_disp_display.set_value(f"{self.absolute_displacement_mm:.4f}")
            self.rel_disp_display.set_value(f"{relative_disp:.4f}")
        else:
            self.abs_disp_display.set_value("Unhomed")
            self.rel_disp_display.set_value("--")
        self.cycle_display.set_value(str(self.current_cycle))
        self.time_display.set_value(f"{self.elapsed_time_s:.1f}")
        if self.is_test_running:
            total_blocks = len(self.test_sequence)
            # Mostra l'indice + 1 (perché l'indice è 0-based)
            self.current_block_display.set_value(f"{self.current_block_index + 1} / {total_blocks}")
        else:
            self.current_block_display.set_value("N/A")
        # --- NUOVA LOGICA PER DISPLAY RESISTENZA ---
        # Mostra il valore della resistenza (con gestione errori/stato disabilitato)
        res_value = self.current_resistance_ohm
        if res_value <= -900.0: # Codice per "disabilitato" o non ancora letto
            display_text = "N/A"
        elif res_value == -1.0: # Codice errore timeout
            display_text = "Timeout"
        elif res_value == -2.0: # Codice errore parsing
            display_text = "Parse Err"
        elif res_value < 0: # Altri errori imprevisti
            display_text = "ERR"
        else: # Valore valido
            # Formatta con notazione scientifica se molto grande o piccolo, altrimenti normale
            if res_value > 1e6 or (res_value < 1e-3 and res_value != 0):
                display_text = f"{res_value:.3e}" # Es: 1.234e+07
            else:
                display_text = f"{res_value:.4f}" # Es: 123.4567
        self.resistance_display.set_value(display_text)


    def update_ui_for_test_state(self):
        is_running = self.is_test_running
        self.start_button.setEnabled(not is_running and self.is_homed and len(self.test_sequence) > 0)
        self.stop_button.setEnabled(is_running)
        self.back_button.setEnabled(not is_running)
        # Blocca i controlli della sequenza e gli zeri durante il test
        widgets_to_toggle = [
            self.up_button, self.down_button, self.jog_speed_spinbox,self.sequence_list, self.add_block_button, self.add_pause_button,
            self.edit_block_button, self.remove_block_button,
            self.zero_rel_load_button, self.zero_rel_disp_button,
            self.limits_button, self.finish_save_button
        ]
        for widget in widgets_to_toggle:
            widget.setEnabled(not is_running)

    def set_homing_status(self, is_homed):
        self.is_homed = is_homed
        self.update_ui_for_test_state() # Aggiorna stato pulsante START
        self.update_displays()

    def zero_relative_load(self):
        self.load_offset_N = self.absolute_load_N; self.update_displays()

    def zero_relative_displacement(self):
        self.displacement_offset_mm = self.absolute_displacement_mm; self.update_displays()

    def _update_plot_axes(self):
        # Qui aggiorneremo le etichette del grafico quando cambiano i combo box
        x_label = self.x_axis_combo.currentText()
        y_label = self.y_axis_combo.currentText()
        self.plot_widget.setLabel('bottom', x_label)
        self.plot_widget.setLabel('left', y_label)
        # Dovremo anche aggiornare i dati visualizzati (TODO)
        print(f"DEBUG: Assi cambiati a X={x_label}, Y={y_label}")

    # --- Metodi Placeholder per la gestione della sequenza ---
    def on_add_block(self):
        # --- NUOVO: Richiedi un provino prima di aggiungere blocchi ---
        if not self.current_specimen_name:
            QMessageBox.warning(self, "Specimen Required", 
                                "Please create and select a specimen from the 'Batch & Overlay' tab before defining a sequence.")
            self.right_tabs.setCurrentWidget(self.batch_page)
            return
            
        specimen = self.specimens[self.current_specimen_name]
        gauge = specimen.get("gauge_length", 1.0)
        area = specimen.get("area", 1.0)
        
        dialog = BlockDialog(self.main_window.current_force_limit_N, self.main_window.current_disp_limit_mm, 
                             self.displacement_offset_mm, self.load_offset_N, self)
        
        if dialog.exec(): 
            raw_data = dialog.get_data()
            
            # --- NUOVO: Conversione e Validazione (Logica Monotonica) ---
            
            # 1. Converti e valida Velocità
            if gauge <= 0 and raw_data["speed_unit"] in ["%/s", "%/min"]:
                 QMessageBox.warning(self, "Input Error", "Gauge length must be > 0 to use %-based speed.")
                 return
            speed_mms = self.convert_speed(raw_data["speed"], raw_data["speed_unit"], gauge)

            # 2. Converti e valida Limiti
            control_unit = raw_data["control_text"]
            if gauge <= 0 and "Strain" in control_unit:
                 QMessageBox.warning(self, "Input Error", "Gauge length must be > 0 to use Strain (%).")
                 return
            if area <= 0 and "Stress" in control_unit:
                 QMessageBox.warning(self, "Input Error", "Area must be > 0 to use Stress (MPa).")
                 return

            upper_conv, upper_base = self.convert_stop_criterion(raw_data["upper"], control_unit, gauge, area)
            lower_conv, lower_base = self.convert_stop_criterion(raw_data["lower"], control_unit, gauge, area)
            
            # 3. Controlla i limiti macchina (convertiti)
            validation_passed = True
            error_message = ""
            if upper_base == "mm": # Vale anche per Strain
                limit_to_check = self.main_window.current_disp_limit_mm
                abs_upper = upper_conv + self.displacement_offset_mm
                abs_lower = lower_conv + self.displacement_offset_mm
                if abs(abs_upper) > limit_to_check or abs(abs_lower) > limit_to_check:
                    validation_passed = False
                    error_message = (f"Absolute limits ({abs_lower:.2f}/{abs_upper:.2f} mm) "
                                     f"exceed machine limit of ±{limit_to_check:.2f} mm.")
            elif upper_base == "N": # Vale anche per Stress
                limit_to_check = self.main_window.current_force_limit_N
                abs_upper = upper_conv + self.load_offset_N
                if abs_upper > limit_to_check: # Controlla solo il limite superiore di forza
                    validation_passed = False
                    error_message = (f"Absolute upper force ({abs_upper:.2f} N) "
                                     f"exceeds machine limit of {limit_to_check:.2f} N.")

            if not validation_passed:
                QMessageBox.warning(self, "Limit Exceeded", error_message)
                return # Non aggiungere il blocco
            
            # 4. Salva i dati (sia grezzi che convertiti)
            block_data = {
                **raw_data, # Dati grezzi (speed, speed_unit, upper, lower, control_text, etc.)
                "speed_mms": speed_mms, # Velocità convertita
                "upper_conv": upper_conv, # Valore base (mm o N)
                "lower_conv": lower_conv, # Valore base (mm o N)
                "base_unit": upper_base  # "mm" o "N"
            }
            # --- FINE BLOCCO CONVERSIONE ---
            
            self.test_sequence.append(block_data)
            self._update_sequence_list()
            self._calculate_estimated_duration()
            self.update_ui_for_test_state()


    def on_add_pause(self):
        dialog = PauseDialog(parent=self) # Crea la dialog per la pausa
        # (Userà il valore di default 5.0 per la durata)
        # Se volessi un default diverso, faresti: PauseDialog(current_duration=10.0, parent=self)
        if dialog.exec():
            duration = dialog.get_duration()
            pause_data = {
                "type": "pause",
                "duration": duration
            }
            # Aggiungi i dati alla lista interna
            self.test_sequence.append(pause_data)
            # Aggiorna la lista visuale e lo stato dei pulsanti
            self._update_sequence_list()
            self._calculate_estimated_duration()
            self._update_sequence_buttons_state() # Chiama l'aggiornamento pulsanti
            # self._calculate_estimated_duration() # Aggiorna durata (da implementare)
            self.update_ui_for_test_state() # Abilita START se necessario

    def on_add_ramp(self):
        # Verifica che sia selezionato un provino
        if not self.current_specimen_name:
            QMessageBox.warning(self, "Specimen Required",
                                "Please create and select a specimen before defining a sequence.")
            self.right_tabs.setCurrentWidget(self.batch_page)
            return

        specimen = self.specimens[self.current_specimen_name]
        gauge = specimen.get("gauge_length", 1.0)
        area = specimen.get("area", 1.0)

        # Crea e mostra la nuova dialog
        # TODO: Passare limiti e offset per validazione futura
        dialog = RampDialog(parent=self)

        if dialog.exec(): # Se l'utente preme OK
            raw_data = dialog.get_data()

            # --- CONVERSIONE E VALIDAZIONE (MOLTO SIMILE A on_add_block) ---

            # 1. Converti e valida Velocità
            if gauge <= 0 and raw_data["speed_unit"] in ["%/s", "%/min"]:
                 QMessageBox.warning(self, "Input Error", "Gauge length must be > 0 to use %-based speed.")
                 return
            speed_mms = self.convert_speed(raw_data["speed"], raw_data["speed_unit"], gauge)

            # 2. Converti e valida Target
            control_unit = raw_data["control_text"]
            if gauge <= 0 and "Strain" in control_unit:
                 QMessageBox.warning(self, "Input Error", "Gauge length must be > 0 to use Strain (%).")
                 return
            if area <= 0 and "Stress" in control_unit:
                 QMessageBox.warning(self, "Input Error", "Area must be > 0 to use Stress (MPa).")
                 return

            target_conv, target_base = self.convert_stop_criterion(raw_data["target"], control_unit, gauge, area)

            # 3. Controlla i limiti macchina (convertiti)
            validation_passed = True
            error_message = ""
            if target_base == "mm": # Vale anche per Strain
                limit_to_check = self.main_window.current_disp_limit_mm
                abs_target = target_conv + self.displacement_offset_mm
                if abs(abs_target) > limit_to_check:
                    validation_passed = False
                    error_message = (f"Absolute target ({abs_target:.2f} mm) "
                                     f"exceeds machine limit of ±{limit_to_check:.2f} mm.")
            elif target_base == "N": # Vale anche per Stress
                limit_to_check = self.main_window.current_force_limit_N
                abs_target = target_conv + self.load_offset_N
                # Consideriamo solo il limite superiore per la forza in trazione
                if abs_target > limit_to_check:
                    validation_passed = False
                    error_message = (f"Absolute target force ({abs_target:.2f} N) "
                                     f"exceeds machine limit of {limit_to_check:.2f} N.")
                # Aggiungere controllo limite inferiore se necessario

            if not validation_passed:
                QMessageBox.warning(self, "Limit Exceeded", error_message)
                return # Non aggiungere il blocco

            # 4. Salva i dati completi
            block_data = {
                **raw_data,
                "speed_mms": speed_mms,
                "target_conv": target_conv, # Valore base (mm o N)
                "base_unit": target_base    # "mm" o "N"
            }
            # --- FINE BLOCCO CONVERSIONE ---

            self.test_sequence.append(block_data)
            self._update_sequence_list()
            self._calculate_estimated_duration() # Aggiorna la durata stimata
            self.update_ui_for_test_state()

    def on_edit_block(self):
        selected_items = self.sequence_list.selectedItems()
        if not selected_items:
            return

        selected_row = self.sequence_list.row(selected_items[0])
        block_data_to_edit = self.test_sequence[selected_row]
        block_type = block_data_to_edit["type"] # Ottieni il tipo di blocco

        # --- Richiedi provino (necessario per tutti i tipi tranne pausa) ---
        if block_type != "pause":
            if not self.current_specimen_name:
                QMessageBox.warning(self, "Specimen Required",
                                    "Please select the specimen associated with this sequence before editing.")
                return
            specimen = self.specimens[self.current_specimen_name]
            gauge = specimen.get("gauge_length", 1.0)
            area = specimen.get("area", 1.0)

        # --- Logica specifica per tipo ---
        updated_block_data = None # Inizializza a None

        if block_type == "cyclic":
            # --- BLOCCO EDIT CICLICO ---
            original_control_text = block_data_to_edit.get("control_text", "Displacement (mm)")
            original_speed_unit = block_data_to_edit.get("speed_unit", "mm/s")

            dialog = BlockDialog(self.main_window.current_force_limit_N, self.main_window.current_disp_limit_mm,
                                 self.displacement_offset_mm, self.load_offset_N, self)

            # Pre-compila
            index_to_set = dialog.control_type_combo.findText(original_control_text)
            dialog.control_type_combo.setCurrentIndex(index_to_set if index_to_set != -1 else 0)
            dialog.upper_limit_spinbox.setValue(block_data_to_edit["upper"])
            dialog.lower_limit_spinbox.setValue(block_data_to_edit["lower"])
            dialog.speed_spinbox.setValue(block_data_to_edit["speed"])
            speed_index_to_set = dialog.speed_unit_combo.findText(original_speed_unit)
            dialog.speed_unit_combo.setCurrentIndex(speed_index_to_set if speed_index_to_set != -1 else 0)
            dialog.hold_upper_spinbox.setValue(block_data_to_edit["hold_upper"])
            dialog.hold_lower_spinbox.setValue(block_data_to_edit["hold_lower"])
            dialog.cycles_spinbox.setValue(block_data_to_edit["cycles"])

            if dialog.exec():
                raw_data = dialog.get_data()
                # --- BLOCCO CONVERSIONE E VALIDAZIONE CICLICO ---
                try: # Aggiungi try-except per robustezza
                    if gauge <= 0 and raw_data["speed_unit"] in ["%/s", "%/min"]: raise ValueError("Gauge length must be > 0 for % speed.")
                    speed_mms = self.convert_speed(raw_data["speed"], raw_data["speed_unit"], gauge)
                    control_unit = raw_data["control_text"]
                    if gauge <= 0 and "Strain" in control_unit: raise ValueError("Gauge length must be > 0 for Strain.")
                    if area <= 0 and "Stress" in control_unit: raise ValueError("Area must be > 0 for Stress.")
                    upper_conv, upper_base = self.convert_stop_criterion(raw_data["upper"], control_unit, gauge, area)
                    lower_conv, lower_base = self.convert_stop_criterion(raw_data["lower"], control_unit, gauge, area)
                    validation_passed = True; error_message = ""
                    if upper_base == "mm":
                        limit_to_check = self.main_window.current_disp_limit_mm
                        abs_upper = upper_conv + self.displacement_offset_mm
                        abs_lower = lower_conv + self.displacement_offset_mm
                        if abs(abs_upper) > limit_to_check or abs(abs_lower) > limit_to_check:
                            validation_passed = False; error_message = f"Absolute limits ({abs_lower:.2f}/{abs_upper:.2f} mm) exceed machine limit ±{limit_to_check:.2f} mm."
                    elif upper_base == "N":
                        limit_to_check = self.main_window.current_force_limit_N
                        abs_upper = upper_conv + self.load_offset_N
                        abs_lower = lower_conv + self.load_offset_N # Calcola anche lower per info
                        if abs_upper > limit_to_check:
                            validation_passed = False; error_message = f"Absolute upper force ({abs_upper:.2f} N) exceeds machine limit {limit_to_check:.2f} N."
                        # Aggiungere controllo limite inferiore se necessario
                    if not validation_passed: raise ValueError(error_message)
                    updated_block_data = {**raw_data,"speed_mms": speed_mms, "upper_conv": upper_conv, "lower_conv": lower_conv, "base_unit": upper_base}
                except ValueError as e:
                     QMessageBox.warning(self, "Input Error", str(e))
                     return # Non procedere se conversione/validazione fallisce

        elif block_type == "ramp":
            # --- BLOCCO EDIT RAMPA ---
            original_control_text = block_data_to_edit.get("control_text", "Displacement (mm)")
            original_speed_unit = block_data_to_edit.get("speed_unit", "mm/s")

            dialog = RampDialog(parent=self) # TODO: Passare limiti/offset a RampDialog per validazione interna

            # Pre-compila
            index_to_set = dialog.control_type_combo.findText(original_control_text)
            dialog.control_type_combo.setCurrentIndex(index_to_set if index_to_set != -1 else 0)
            dialog.target_value_spinbox.setValue(block_data_to_edit["target"])
            dialog.speed_spinbox.setValue(block_data_to_edit["speed"])
            speed_index_to_set = dialog.speed_unit_combo.findText(original_speed_unit)
            dialog.speed_unit_combo.setCurrentIndex(speed_index_to_set if speed_index_to_set != -1 else 0)
            dialog.hold_duration_spinbox.setValue(block_data_to_edit["hold_duration"])

            if dialog.exec():
                raw_data = dialog.get_data()
                # --- BLOCCO CONVERSIONE E VALIDAZIONE RAMPA ---
                try: # Aggiungi try-except
                    if gauge <= 0 and raw_data["speed_unit"] in ["%/s", "%/min"]: raise ValueError("Gauge length must be > 0 for % speed.")
                    speed_mms = self.convert_speed(raw_data["speed"], raw_data["speed_unit"], gauge)
                    control_unit = raw_data["control_text"]
                    if gauge <= 0 and "Strain" in control_unit: raise ValueError("Gauge length must be > 0 for Strain.")
                    if area <= 0 and "Stress" in control_unit: raise ValueError("Area must be > 0 for Stress.")
                    target_conv, target_base = self.convert_stop_criterion(raw_data["target"], control_unit, gauge, area)
                    validation_passed = True; error_message = ""
                    if target_base == "mm":
                        limit_to_check = self.main_window.current_disp_limit_mm
                        abs_target = target_conv + self.displacement_offset_mm
                        if abs(abs_target) > limit_to_check:
                            validation_passed = False; error_message = f"Absolute target ({abs_target:.2f} mm) exceeds machine limit ±{limit_to_check:.2f} mm."
                    elif target_base == "N":
                        limit_to_check = self.main_window.current_force_limit_N
                        abs_target = target_conv + self.load_offset_N
                        if abs_target > limit_to_check: # Controlla solo limite superiore N
                            validation_passed = False; error_message = f"Absolute target force ({abs_target:.2f} N) exceeds machine limit {limit_to_check:.2f} N."
                        # Aggiungere controllo limite inferiore se necessario
                    if not validation_passed: raise ValueError(error_message)
                    updated_block_data = {**raw_data, "speed_mms": speed_mms, "target_conv": target_conv, "base_unit": target_base}
                except ValueError as e:
                    QMessageBox.warning(self, "Input Error", str(e))
                    return # Non procedere

        elif block_type == "pause":
            # --- BLOCCO EDIT PAUSA ---
            dialog = PauseDialog(block_data_to_edit["duration"], self)
            if dialog.exec():
                new_duration = dialog.get_duration()
                # Per coerenza, creiamo un nuovo dizionario anche qui
                updated_block_data = {**block_data_to_edit, "duration": new_duration}

        # --- Aggiornamento finale (se una modifica è stata fatta) ---
        if updated_block_data is not None:
            self.test_sequence[selected_row] = updated_block_data
            self._update_sequence_list()
            self._calculate_estimated_duration()

    def on_remove_block(self):
            selected_items = self.sequence_list.selectedItems()
            if not selected_items:
                # Se per qualche motivo viene chiamato senza selezione, non fare nulla
                return

            selected_row = self.sequence_list.row(selected_items[0])

            reply = QMessageBox.question(
                self, "Confirm Deletion",
                f"Are you sure you want to remove Block {selected_row + 1}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No # Default a No
            )

            if reply == QMessageBox.StandardButton.Yes:
                # Rimuovi dalla lista interna dei dati usando l'indice
                del self.test_sequence[selected_row]
                # Rimuovi dalla QListWidget visuale usando l'indice
                self.sequence_list.takeItem(selected_row)

                # Aggiorna la numerazione nella lista visuale
                self._update_sequence_list()
                self._calculate_estimated_duration()
                # Aggiorna lo stato dei pulsanti Edit/Remove (ora saranno disabilitati)
                self._update_sequence_buttons_state()
                # self._calculate_estimated_duration() # Aggiorna durata
                self.update_ui_for_test_state() # Aggiorna stato pulsante START

    def _update_sequence_list(self):
        self.sequence_list.clear() 
        for i, block in enumerate(self.test_sequence):
            if block["type"] == "cyclic":
                # --- CORREZIONE: Usa le unità grezze ---
                control_text = block["control_text"]
                if "Displacement" in control_text: unit = "mm"
                elif "Strain" in control_text: unit = "%"
                elif "Force" in control_text: unit = "N"
                else: unit = "MPa"
                
                description = (
                    f"Block {i+1}: {block['control']} Cycle "
                    f"[{block['lower']:.2f} ↔ {block['upper']:.2f} {unit}] "
                    f"@ {block['speed']:.2f} {block['speed_unit']}, "
                    f"{block['cycles']} cycles "
                    f"(Hold U/L: {block['hold_upper']:.1f}s / {block['hold_lower']:.1f}s)"
                )
            elif block["type"] == "pause":
                description = f"Block {i+1}: Pause [{block['duration']:.1f} s]"

            elif block["type"] == "ramp":
                control_text = block["control_text"]
                if "Displacement" in control_text: unit = "mm"
                elif "Strain" in control_text: unit = "%"
                elif "Force" in control_text: unit = "N"
                else: unit = "MPa"
                hold_str = f", Hold {block['hold_duration']:.1f}s" if block['hold_duration'] > 0 else ""
                description = (
                    f"Block {i+1}: Ramp to {block['target']:.2f} {unit} "
                    f"@ {block['speed']:.2f} {block['speed_unit']}{hold_str}"
                )    
            else:
                description = f"Block {i+1}: Unknown Type"
            
            self.sequence_list.addItem(description)


    def _update_sequence_buttons_state(self):
        """Abilita/Disabilita i pulsanti Edit/Remove in base alla selezione nella lista."""
        selected_items = self.sequence_list.selectedItems()
        has_selection = bool(selected_items)
        current_row = -1
        if has_selection:
            current_row = self.sequence_list.row(selected_items[0])
        has_selection = bool(self.sequence_list.selectedItems())
        self.edit_block_button.setEnabled(has_selection)
        self.remove_block_button.setEnabled(has_selection)
        self.move_up_button.setEnabled(has_selection and current_row > 0)
        self.move_down_button.setEnabled(has_selection and current_row < self.sequence_list.count() - 1)

# Aggiungi questo nuovo metodo privato
    def _calculate_estimated_duration(self):
        """
        Calcola la durata totale stimata della sequenza di test, simulando
        la posizione per includere i tempi di riposizionamento.
        """
        total_duration_s = 0.0
        predictable = True
        
        # --- NUOVA LOGICA: Simuliamo la posizione ---
        current_position_mm = 0.0 # Assumiamo di partire da 0 relativo

        for block in self.test_sequence:
            if block["type"] == "pause":
                total_duration_s += block["duration"]
                # La posizione non cambia
                
            elif block["type"] == "cyclic":
                # Controlla se il blocco è basato su spostamento
                if block["base_unit"] == "mm":
                    # Valori relativi (convertiti) del blocco
                    upper_rel = block["upper_conv"]
                    lower_rel = block["lower_conv"]
                    speed_mms = block["speed_mms"]
                    hold_upper = block["hold_upper"]
                    hold_lower = block["hold_lower"]
                    cycles = block["cycles"]

                    # --- 1. Calcolo Riposizionamento ---
                    # Assumiamo che il ciclo parta sempre dal limite inferiore
                    start_position_mm = lower_rel 
                    
                    if abs(current_position_mm - start_position_mm) > 1e-6: # Tolleranza
                        distance = abs(start_position_mm - current_position_mm)
                        if speed_mms > 0:
                            reposition_time = distance / speed_mms
                            total_duration_s += reposition_time
                        else:
                            predictable = False; break
                    
                    current_position_mm = start_position_mm # Ora siamo in posizione

                    # --- 2. Calcolo Cicli (come prima) ---
                    travel_distance = abs(upper_rel - lower_rel)
                    if speed_mms > 0:
                        ramp_time = travel_distance / speed_mms
                    else:
                        ramp_time = 0; predictable = False; break # Evita divisione per zero

                    time_per_cycle = ramp_time + hold_upper + ramp_time + hold_lower
                    total_duration_s += time_per_cycle * cycles
                    
                    # Aggiorna posizione finale: il ciclo finisce al limite inferiore
                    current_position_mm = lower_rel 
                else:
                    # Blocco Forza/Stress: imprevedibile
                    predictable = False
                    break 

            elif block["type"] == "ramp":
                # Controlla se il blocco è basato su spostamento
                if block["base_unit"] == "mm":
                    target_rel = block["target_conv"]
                    speed_mms = block["speed_mms"]

                    # --- 3. Calcolo Rampa (Corretto) ---
                    # Calcola il tempo dalla posizione CORRENTE al target
                    if abs(current_position_mm - target_rel) > 1e-6: # Tolleranza
                        distance = abs(target_rel - current_position_mm)
                        if speed_mms > 0:
                            ramp_time = distance / speed_mms
                            total_duration_s += ramp_time
                        else:
                            predictable = False; break
                    
                    # Aggiungi l'eventuale hold
                    total_duration_s += block["hold_duration"]
                    # Aggiorna la posizione finale
                    current_position_mm = target_rel 
                else:
                    # Blocco Forza/Stress: imprevedibile
                    predictable = False
                    break    
            else:
                predictable = False
                break

        # --- Aggiorna l'etichetta (logica invariata) ---
        if predictable:
            minutes, seconds = divmod(int(total_duration_s), 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                duration_str = f"{hours}h {minutes:02d}m {seconds:02d}s"
            elif minutes > 0:
                 duration_str = f"{minutes}m {seconds:02d}s"
            else:
                 duration_str = f"{seconds}s"
            self.estimated_duration_label.setText(f"Estimated Duration: {duration_str}")
        else:
            self.estimated_duration_label.setText("Estimated Duration: N/A (contains non-displacement blocks)")


    def start_moving_up(self):
            self.set_speed()
            self.communicator.send_command("JOG_UP")

    def start_moving_down(self):
        self.set_speed()
        self.communicator.send_command("JOG_DOWN")

    def stop_moving(self):
        self.communicator.send_command("STOP")

    def set_speed(self):
        # Invia il comando solo se il valore è cambiato significativamente (opzionale)
        self.communicator.send_command(f"SET_SPEED:{self.jog_speed_spinbox.value():.2f}")


    def on_new_specimen(self):
        # Passa la lista dei nomi esistenti per la validazione
        dialog = SpecimenDialog(existing_names=list(self.specimens.keys()), parent=self)
        
        if dialog.exec(): # Se l'utente preme OK (e la validazione passa)
            new_data = dialog.get_data()
            name = new_data["name"]

            # Crea il dizionario completo del provino
            specimen_data = {
                "name": name,
                "gauge_length": new_data["gauge_length"],
                "area": new_data["area"],
                "test_data": None,
                "visible": True
            }

            self.specimens[name] = specimen_data
            self.specimen_list.addItem(name)
            # Aggiungi alla lista overlay
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.overlay_list.addItem(item)

            # Seleziona automaticamente il nuovo provino
            list_items = self.specimen_list.findItems(name, Qt.MatchFlag.MatchExactly)
            if list_items:
                 self.specimen_list.setCurrentItem(list_items[0])
            
            self.refresh_plot()

    def on_delete_specimen(self):
        selected_item = self.specimen_list.currentItem()
        if not selected_item:
            QMessageBox.information(self, "Info", "Please select a specimen to delete.")
            return
        name = selected_item.text()
        reply = QMessageBox.question(
            self, "Confirm Deletion",
            f"Are you sure you want to delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            # Rimuovi dal dizionario
            del self.specimens[name]
            # Rimuovi dalla lista principale
            self.specimen_list.takeItem(self.specimen_list.row(selected_item))
            # Rimuovi anche dalla lista overlay
            for i in range(self.overlay_list.count()):
                item = self.overlay_list.item(i)
                if item.text() == name:
                    self.overlay_list.takeItem(i)
                    break
            self.current_specimen_name = None
            self.refresh_plot()


    def on_modify_specimen(self):
        selected_item = self.specimen_list.currentItem()
        if not selected_item:
            QMessageBox.information(self, "Info", "Please select a specimen to modify.")
            return
        
        original_name = selected_item.text()
        original_data = self.specimens[original_name]

        # Passa i dati attuali e i nomi esistenti
        dialog = SpecimenDialog(current_data=original_data, 
                                existing_names=list(self.specimens.keys()), 
                                parent=self)

        if dialog.exec(): # Se l'utente preme OK (e la validazione passa)
            modified_data = dialog.get_data()
            new_name = modified_data["name"]

            # Aggiorna il dizionario specimens
            # Mantieni i dati del test e la visibilità originali
            final_data = {
                **modified_data, # Prende name, gauge, area dalla dialog
                "test_data": original_data.get("test_data"),
                "visible": original_data.get("visible", True)
            }
            
            # Se il nome è cambiato, gestisci la sostituzione
            if new_name != original_name:
                del self.specimens[original_name]
            self.specimens[new_name] = final_data

            # Aggiorna le liste visuali
            selected_item.setText(new_name)
            for i in range(self.overlay_list.count()):
                item = self.overlay_list.item(i)
                if item.text() == original_name:
                    item.setText(new_name)
                    break
            
            self.current_specimen_name = new_name # Aggiorna il nome corrente se è cambiato
            self.refresh_plot()
            QMessageBox.information(self, "Success", f"Specimen '{new_name}' updated.")



    def on_specimen_selected(self, item):
        if item is None: # Può succedere dopo una cancellazione
            self.current_specimen_name = None
            self.refresh_plot()
            return
            
        name = item.text()
        self.current_specimen_name = name
        # Non ci sono campi da pre-compilare qui
        self.refresh_plot() # Aggiorna il grafico per mostrare/nascondere la curva

    def refresh_plot(self):
        # --- 1. Pulisci il grafico ESISTENTE (MODO SICURO) ---
        plot_item = self.plot_widget.getPlotItem()
        main_viewbox = plot_item.getViewBox()

        if self.resistance_axis_viewbox:
            try:
                # SCOLLEGA I SEGNALI prima di rimuovere (Fix Problema 1)
                try:
                    main_viewbox.sigResized.disconnect(self._update_resistance_views)
                    main_viewbox.sigXRangeChanged.disconnect(self._update_resistance_views)
                except (TypeError, RuntimeError):
                    pass # Ignora se non erano connessi

                plot_item.scene().removeItem(self.resistance_axis_viewbox)
                self.resistance_axis_viewbox = None
                plot_item.getAxis('right').linkToView(None)
                plot_item.showAxis('right', False)
                self.resistance_curve = None
                
                legend = plot_item.legend
                if legend: legend.removeItem("Resistance")
            except Exception as e:
                # L'errore 'weak reference' apparirà qui se la disconnessione fallisce
                print(f"Errore rimozione asse/viewbox secondario (Cyclic): {e}")

        # Pulisci le curve principali
        self.plot_widget.clear() 
        self.plot_widget.addLegend()
        self.plot_curves = {}

        # --- 2. Imposta Assi Principali ---
        x_mode = self.x_axis_combo.currentText()
        y_mode = self.y_axis_combo.currentText()
        plot_item.setLabel("bottom", x_mode)
        plot_item.setLabel("left", y_mode)

        # --- 3. Sotto-funzione convert_data (invariata) ---
        def convert_data(specimen, raw_data):
            area = specimen.get("area", 1.0)
            gauge = specimen.get("gauge_length", 1.0)
            if not raw_data: return [], [], []
            try:
                times = [p[0] for p in raw_data]
                x_raw = [p[1] for p in raw_data]
                y_raw = [p[2] for p in raw_data]
                r_raw = [p[7] for p in raw_data]
            except (IndexError, TypeError) as e:
                print(f"Errore estrazione dati in convert_data (ciclico): {e}")
                return [], [], []
            if "Strain" in x_mode and gauge > 0: x = [(d / gauge) * 100 for d in x_raw]
            elif "Time" in x_mode: x = times
            else: x = x_raw
            if "Stress" in y_mode and area > 0: y = [(f / area) for f in y_raw]
            elif "Strain" in y_mode and gauge > 0: y = [(d / gauge) * 100 for d in x_raw]
            elif "Displacement" in y_mode: y = x_raw
            else: y = y_raw
            r_data = [r if r >= 0 else np.nan for r in r_raw]
            return x, y, r_data

        # --- 4. Logica Overlay (invariata) ---
        show_overlay = self.overlay_checkbox.isChecked()
        if show_overlay:
            for name, specimen in self.specimens.items():
                if specimen.get("test_data") and specimen.get("visible", True):
                    try:
                        x, y, _ = convert_data(specimen, specimen["test_data"])
                        curve = self.plot_widget.plot(x, y, pen=self.get_pen_for_specimen(name), name=name)
                        self.plot_curves[name] = curve
                    except Exception as e: print(f"Errore disegno overlay {name} (Cyclic): {e}")
        else:
            if self.current_specimen_name:
                specimen = self.specimens.get(self.current_specimen_name)
                if specimen and specimen.get("test_data"):
                    try:
                        x, y, _ = convert_data(specimen, specimen["test_data"])
                        curve = self.plot_widget.plot(x, y, pen=self.get_pen_for_specimen(self.current_specimen_name), name=self.current_specimen_name)
                        self.plot_curves[self.current_specimen_name] = curve
                    except Exception as e: print(f"Errore disegno non-overlay {self.current_specimen_name} (Cyclic): {e}")

        # --- 5. Logica Secondo Asse Y (Resistenza) (Metodo ManualControl) ---
        lcr_enabled = self.lcr_enable_checkbox.isChecked()

        if lcr_enabled:
            try:
                plot_item.showAxis('right')
                self.resistance_axis_viewbox = pg.ViewBox()
                self.resistance_axis_viewbox.setZValue(10)
                plot_item.getAxis('right').linkToView(self.resistance_axis_viewbox)
                plot_item.getAxis('right').setLabel('Resistance', units='Ω')
                plot_item.scene().addItem(self.resistance_axis_viewbox)
                self.resistance_axis_viewbox.linkView(pg.ViewBox.XAxis, main_viewbox)
                
                # NON linkare l'asse Y (rimuove il vecchio bug)
                # CANCELLATO: self.resistance_axis_viewbox.linkView(pg.ViewBox.YAxis, main_viewbox)

                resistance_pen = pg.mkPen('orange', width=2, style=Qt.PenStyle.DotLine)
                self.resistance_curve = pg.PlotDataItem(pen=resistance_pen, name="Resistance")
                self.resistance_axis_viewbox.addItem(self.resistance_curve)

                # Collega al nuovo metodo della classe
                main_viewbox.sigResized.connect(self._update_resistance_views)
                main_viewbox.sigXRangeChanged.connect(self._update_resistance_views)
                self._update_resistance_views() # Chiama subito

                # Disegna i dati di resistenza ESISTENTI (Polling)
                if show_overlay:
                     for name, specimen in self.specimens.items():
                        if specimen.get("test_data") and specimen.get("visible", True):
                            try:
                                x, _, r_data = convert_data(specimen, specimen["test_data"])
                                overlay_res_curve = pg.PlotDataItem(pen=pg.mkPen('orange', width=1, style=Qt.PenStyle.DotLine))
                                self.resistance_axis_viewbox.addItem(overlay_res_curve)
                                overlay_res_curve.setData(x, r_data)
                            except Exception as e: print(f"Errore disegno overlay resistenza {name} (Cyclic): {e}")
                else: # Non overlay
                    if self.current_specimen_name:
                        specimen = self.specimens.get(self.current_specimen_name)
                        if specimen and specimen.get("test_data"):
                            try:
                                x, _, r_data = convert_data(specimen, specimen["test_data"])
                                self.resistance_curve.setData(x, r_data)
                            except Exception as e: print(f"Errore disegno non-overlay resistenza {self.current_specimen_name} (Cyclic): {e}")
            
            except Exception as e:
                print(f"Errore creazione asse resistenza (Cyclic - refresh_plot): {e}")

        # --- 6. Grafico Live (Fondamentale per Problema 3) ---
        # QUESTA CURVA DEVE ESSERE CREATA QUI, ALLA FINE,
        # anche se la funzione è crashata prima, questa è la parte che serve a on_start_test
        live_name = "Live: " + (self.current_specimen_name if self.current_specimen_name else "N/A")
        if self.current_specimen_name:
            final_pen = self.get_pen_for_specimen(self.current_specimen_name)
            live_pen = pg.mkPen(final_pen); live_pen.setStyle(Qt.PenStyle.DashLine); live_pen.setWidth(2)
        else:
            live_pen = pg.mkPen('b', width=2, style=Qt.PenStyle.DashLine)
        
        # Questa è la curva live PRINCIPALE (self.plot_curve)
        self.plot_curve = self.plot_widget.plot([], [], pen=live_pen, name=live_name)
        
        # (La sezione "if self.is_test_running..." è rimossa perché 
        # handle_stream_data si occuperà dei dati live)


    def on_overlay_item_changed(self, item):
        name = item.text()
        self.specimens[name]["visible"] = (item.checkState() == Qt.CheckState.Checked)
        self.refresh_plot()

    def get_pen_for_specimen(self, name):
        # Tavolozza di colori ciclica
        colors = ['r', 'b', 'g', 'm', 'c', 'y', 'k']
        index = list(self.specimens.keys()).index(name) % len(colors)
        return pg.mkPen(colors[index], width=2)


    def convert_speed(self, value, unit, gauge_length):
        """Converte la velocità in mm/s a partire dal valore, unità e gauge length."""
        if unit == "mm/s":
            return value
        elif unit == "mm/min":
            return value / 60.0
        elif unit == "%/s":
            if gauge_length <= 0: return 0.0
            return (gauge_length / 100.0) * value
        elif unit == "%/min":
            if gauge_length <= 0: return 0.0
            return (gauge_length / 100.0) * (value / 60.0)
        else:
            return value  # fallback
        

    def convert_stop_criterion(self, value, unit, gauge_length, area):
        """Converte lo stop criterion in unità base (displacement mm o force N)."""
        if unit == "Displacement (mm)":
            return value, "mm"
        elif unit == "Force (N)":
            return value, "N"
        elif unit == "Strain (%)":
            if gauge_length <= 0: return 0.0, "mm"
            displacement = (gauge_length * value) / 100.0
            return displacement, "mm"
        elif unit == "Stress (MPa)":
            if area <= 0: return 0.0, "N"
            force = value * area  # area in mm², stress in MPa = N/mm²
            return force, "N"
        else:
            return value, unit
        
    def on_move_block_up(self):
        selected_items = self.sequence_list.selectedItems()
        if not selected_items: return

        current_row = self.sequence_list.row(selected_items[0])
        if current_row > 0: # Non può salire se è già il primo
            new_row = current_row - 1

            # Scambia nella QListWidget
            item = self.sequence_list.takeItem(current_row)
            self.sequence_list.insertItem(new_row, item)
            self.sequence_list.setCurrentRow(new_row) # Mantieni la selezione

            # Scambia nella lista dati interna
            block_data = self.test_sequence.pop(current_row)
            self.test_sequence.insert(new_row, block_data)

            # Aggiorna la numerazione e la durata
            self._update_sequence_list()
            self._calculate_estimated_duration()

    def on_move_block_down(self):
        selected_items = self.sequence_list.selectedItems()
        if not selected_items: return

        current_row = self.sequence_list.row(selected_items[0])
        if current_row < self.sequence_list.count() - 1: # Non può scendere se è già l'ultimo
            new_row = current_row + 1

            # Scambia nella QListWidget
            item = self.sequence_list.takeItem(current_row)
            self.sequence_list.insertItem(new_row, item)
            self.sequence_list.setCurrentRow(new_row)

            # Scambia nella lista dati interna
            block_data = self.test_sequence.pop(current_row)
            self.test_sequence.insert(new_row, block_data)

            # Aggiorna la numerazione e la durata
            self._update_sequence_list()
            self._calculate_estimated_duration()


    def on_finish_and_save(self):
        if not self.specimens:
            QMessageBox.information(self, "Info", "Nessun provino da salvare.")
            return

        # Propone un nome di file di default
        default_filename = f"Batch_Cyclic_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"

        # Apre la finestra di dialogo per il salvataggio
        filepath, _ = QFileDialog.getSaveFileName(self, "Salva Batch Test Ciclici", default_filename, "Excel Files (*.xlsx)")

        if filepath:
            # --- PREPARA I DATI PER IL SALVATAGGIO ---
            # Dobbiamo aggiungere la sequenza di test a *ogni* provino
            # perché il DataSaver la leggerà da lì
            specimens_to_save = {}
            for name, data in self.specimens.items():
                specimens_to_save[name] = {
                    **data, # gauge_length, area, test_data...
                    "test_sequence_setup": self.test_sequence # Aggiunge la lista dei blocchi
                }

            saver = DataSaver()
            # Passiamo None come info calibrazione (o self.active_calibration_info se ce l'hai)
            success, message = saver.save_batch_to_xlsx(specimens_to_save, filepath, "N/A") 

            if success:
                QMessageBox.information(self, "Successo", message)
            else:
                QMessageBox.critical(self, "Errore", message)

    def _on_lcr_checkbox_changed(self, state):
        """ Invia il comando appropriato all'ESP32 quando il checkbox cambia stato. """
        if state == Qt.CheckState.Checked.value:
            print("DEBUG GUI: Abilitazione LCR Polling")
            self.communicator.send_command("ENABLE_LCR_POLLING")
        else:
            print("DEBUG GUI: Disabilitazione LCR Polling")
            self.communicator.send_command("DISABLE_LCR_POLLING")
            # Resetta subito il display a "N/A"
            self.current_resistance_ohm = -999.0
        
        # --- MODIFICA ---
        # Chiama la nuova funzione di plotting
        self.refresh_plot()
        self.update_displays() # Aggiorna per mostrare il reset
        # --- FINE MODIFICA ---


    def _update_resistance_views(self):
        """ Funzione helper per sincronizzare i ViewBox principale e secondario. """
        main_viewbox = self.plot_widget.getViewBox()
        if self.resistance_axis_viewbox and main_viewbox:
            try:
                # 1. Allinea le aree di disegno
                self.resistance_axis_viewbox.setGeometry(main_viewbox.sceneBoundingRect())
                # 2. Sincronizza l'asse X
                self.resistance_axis_viewbox.linkedViewChanged(main_viewbox, pg.ViewBox.XAxis)
                # 3. Autoscala l'asse Y secondario
                self.resistance_axis_viewbox.enableAutoRange(axis=pg.ViewBox.YAxis)
                
                # --- FIX COSMETICO (Problema 2) ---
                # Se non stiamo facendo un test e l'asse è (0,1), imposta un default
                yrange = self.resistance_axis_viewbox.viewRange()[1]
                if not self.is_test_running and yrange[0] == 0 and yrange[1] == 1:
                    self.resistance_axis_viewbox.setYRange(0, 1000) # Imposta un range visibile
            except Exception as e:
                pass