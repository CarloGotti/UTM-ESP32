import sys
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QGridLayout, QFrame, QLineEdit, QDoubleSpinBox, QComboBox,
                             QCheckBox, QListWidget, QListWidgetItem, QInputDialog, QMessageBox)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QDoubleValidator

from custom_widgets import DisplayWidget
import pyqtgraph as pg

class MonotonicTestWidget(QWidget):
    back_to_menu_requested = pyqtSignal()
    # ... (altri segnali se necessari)

    def __init__(self, communicator, parent=None):
        super().__init__(parent)
        self.communicator = communicator
        
        self.is_homed = False
        self.active_calibration_info = "Not Calibrated"
        self.specimens = {}
        self.current_specimen_name = None
        self.is_test_running = False

        general_font = QFont("Segoe UI", 11)
        button_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        title_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        self.only_float_validator = QDoubleValidator()

        main_layout = QVBoxLayout(self)

        # --- 1. SEZIONE SUPERIORE ---
        top_section_layout = QHBoxLayout()
        self.abs_load_display = DisplayWidget("Absolute Load (N)")
        self.rel_load_display = DisplayWidget("Relative Load (N)")
        self.abs_disp_display = DisplayWidget("Absolute Displacement (mm)")
        self.rel_disp_display = DisplayWidget("Relative Displacement (mm)")
        self.calib_status_display = DisplayWidget("Active Calibration")

        jog_controls_layout = QVBoxLayout()
        self.up_button = QPushButton("↑ UP ↑")
        self.down_button = QPushButton("↓ DOWN ↓")
        jog_speed_layout = QHBoxLayout()
        self.jog_speed_spinbox = QDoubleSpinBox()
        self.jog_speed_spinbox.setSuffix(" mm/s")
        self.jog_speed_spinbox.setFixedWidth(100)
        jog_speed_layout.addWidget(QLabel("Jog Speed:"))
        jog_speed_layout.addWidget(self.jog_speed_spinbox)
        jog_controls_layout.addWidget(self.up_button)
        jog_controls_layout.addWidget(self.down_button)
        jog_controls_layout.addLayout(jog_speed_layout)
        
        top_section_layout.addWidget(self.abs_load_display)
        top_section_layout.addWidget(self.rel_load_display)
        top_section_layout.addWidget(self.abs_disp_display)
        top_section_layout.addWidget(self.rel_disp_display)
        top_section_layout.addWidget(self.calib_status_display)
        top_section_layout.addStretch(1)
        top_section_layout.addLayout(jog_controls_layout)

        # --- SEPARATORE 1 ---
        separator1 = QFrame(); separator1.setFrameShape(QFrame.Shape.HLine); separator1.setFrameShadow(QFrame.Shadow.Sunken)

        # --- 2. SEZIONE CENTRALE (Parametri) ---
        params_layout = QGridLayout()
        self.name_edit = QLineEdit()
        self.gauge_length_edit = QLineEdit(); self.gauge_length_edit.setValidator(self.only_float_validator)
        self.automeasure_button = QPushButton("Automeasure"); self.automeasure_button.setEnabled(False)
        self.area_edit = QLineEdit(); self.area_edit.setValidator(self.only_float_validator)
        self.speed_spinbox = QDoubleSpinBox(); self.speed_spinbox.setDecimals(3); self.speed_spinbox.setRange(0.001, 500.0)
        self.speed_unit_combo = QComboBox(); self.speed_unit_combo.addItems(["mm/s", "mm/min", "%/s", "%/min"])
        self.stop_criterion_spinbox = QDoubleSpinBox(); self.stop_criterion_spinbox.setDecimals(2); self.stop_criterion_spinbox.setRange(0.01, 10000.0)
        self.stop_criterion_combo = QComboBox(); self.stop_criterion_combo.addItems(["Displacement (mm)", "Strain (%)", "Force (N)", "Stress (MPa)"])
        self.return_to_start_checkbox = QCheckBox("Return to start point after test")
        
        params_layout.addWidget(QLabel("Name:"), 0, 0); params_layout.addWidget(self.name_edit, 0, 1, 1, 3)
        params_layout.addWidget(QLabel("Gauge Length (mm):"), 1, 0); params_layout.addWidget(self.gauge_length_edit, 1, 1); params_layout.addWidget(self.automeasure_button, 1, 2)
        params_layout.addWidget(QLabel("Area (mm²):"), 2, 0); params_layout.addWidget(self.area_edit, 2, 1)
        params_layout.addWidget(QLabel("Speed:"), 3, 0); params_layout.addWidget(self.speed_spinbox, 3, 1); params_layout.addWidget(self.speed_unit_combo, 3, 2)
        params_layout.addWidget(QLabel("Stop Criterion:"), 4, 0); params_layout.addWidget(self.stop_criterion_spinbox, 4, 1); params_layout.addWidget(self.stop_criterion_combo, 4, 2)
        params_layout.addWidget(self.return_to_start_checkbox, 5, 1, 1, 2)
        
        specimen_mgmt_layout = QHBoxLayout()
        self.new_button = QPushButton("NEW"); self.modify_button = QPushButton("MODIFY"); self.delete_button = QPushButton("DELETE")
        specimen_mgmt_layout.addStretch(1); specimen_mgmt_layout.addWidget(self.new_button)
        specimen_mgmt_layout.addWidget(self.modify_button); specimen_mgmt_layout.addWidget(self.delete_button); specimen_mgmt_layout.addStretch(1)

        # --- SEPARATORE 2 ---
        separator2 = QFrame(); separator2.setFrameShape(QFrame.Shape.HLine); separator2.setFrameShadow(QFrame.Shadow.Sunken)

        # --- 3. SEZIONE INFERIORE (Grafico e Controlli) ---
        test_area_layout = QHBoxLayout()
        
        graph_layout = QVBoxLayout()
        self.plot_widget = pg.PlotWidget(); self.plot_widget.setBackground('w')
        self.plot_curve = self.plot_widget.plot(pen='b'); self.plot_widget.showGrid(x=True, y=True)
        graph_controls_layout = QHBoxLayout()
        self.x_axis_combo = QComboBox(); self.x_axis_combo.addItems(["Relative Displacement (mm)", "Strain (%)"])
        self.y_axis_combo = QComboBox(); self.y_axis_combo.addItems(["Relative Load (N)", "Stress (MPa)"])
        self.overlay_checkbox = QCheckBox("Overlay previous tests")
        graph_controls_layout.addWidget(QLabel("X-Axis:")); graph_controls_layout.addWidget(self.x_axis_combo); graph_controls_layout.addStretch(1)
        graph_controls_layout.addWidget(QLabel("Y-Axis:")); graph_controls_layout.addWidget(self.y_axis_combo); graph_controls_layout.addStretch(2)
        graph_controls_layout.addWidget(self.overlay_checkbox)
        graph_layout.addWidget(self.plot_widget); graph_layout.addLayout(graph_controls_layout)

        right_panel_layout = QVBoxLayout()
        self.specimen_list = QListWidget()
        start_stop_layout = QHBoxLayout()
        self.start_button = QPushButton("▶ START"); self.stop_button = QPushButton("■ STOP")
        self.start_button.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold)); self.stop_button.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.start_button.setStyleSheet("background-color: #2ECC71; color: white;"); self.stop_button.setStyleSheet("background-color: #E74C3C; color: white;")
        start_stop_layout.addWidget(self.start_button); start_stop_layout.addWidget(self.stop_button)
        right_panel_layout.addWidget(QLabel("Test Batch:", font=title_font)); right_panel_layout.addWidget(self.specimen_list); right_panel_layout.addLayout(start_stop_layout)
        
        test_area_layout.addLayout(graph_layout, 3); test_area_layout.addLayout(right_panel_layout, 1)

        # --- 4. SEZIONE FINALE ---
        bottom_buttons_layout = QHBoxLayout()
        self.zero_rel_load_button = QPushButton("Zero Relative Load")
        self.zero_rel_disp_button = QPushButton("Zero Relative Displacement")
        self.limits_button = QPushButton("LIMITS")
        self.finish_save_button = QPushButton("FINISH & SAVE")
        self.back_button = QPushButton("Back to Menu") # CORREZIONE: Aggiunta definizione
        
        bottom_buttons_layout.addWidget(self.zero_rel_load_button)
        bottom_buttons_layout.addWidget(self.zero_rel_disp_button)
        bottom_buttons_layout.addStretch(1)
        bottom_buttons_layout.addWidget(self.limits_button)
        bottom_buttons_layout.addWidget(self.finish_save_button)

        # --- ASSEMBLAGGIO FINALE ---
        main_layout.addLayout(top_section_layout)
        main_layout.addWidget(separator1)
        main_layout.addLayout(params_layout)
        main_layout.addLayout(specimen_mgmt_layout)
        main_layout.addWidget(separator2)
        main_layout.addLayout(test_area_layout)
        main_layout.addLayout(bottom_buttons_layout)
        main_layout.addWidget(self.back_button)

        # --- CONNESSIONI ---
        self.back_button.clicked.connect(self.back_to_menu_requested.emit)
        self.area_edit.textChanged.connect(self.update_stop_criterion_options)
        
        self.update_stop_criterion_options()

    def update_stop_criterion_options(self):
        try:
            is_area_valid = float(self.area_edit.text()) > 0
        except ValueError:
            is_area_valid = False
        
        stress_item_index = self.stop_criterion_combo.findText("Stress (MPa)")
        if stress_item_index != -1:
            item = self.stop_criterion_combo.model().item(stress_item_index)
            item.setEnabled(is_area_valid)
            
        if not is_area_valid and self.stop_criterion_combo.currentText() == "Stress (MPa)":
            self.stop_criterion_combo.setCurrentIndex(0)

    def set_homing_status(self, is_homed):
        self.is_homed = is_homed