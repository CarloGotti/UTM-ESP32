from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QDoubleSpinBox, QGridLayout
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont

from custom_widgets import DisplayWidget, SpeedBarWidget

class ManualControlWidget(QWidget):
    back_to_menu_requested = pyqtSignal()

    def __init__(self, communicator, parent=None):
        super().__init__(parent)
        self.communicator = communicator
        self.is_homing_active = False # NUOVO: Stato per tracciare l'homing

        self.MIN_SPEED, self.MAX_SPEED = 0.01, 25.0
        self.is_homed = False; self.absolute_load_N = 0.0; self.load_offset_N = 0.0
        self.absolute_displacement_mm = 0.0; self.displacement_offset_mm = 0.0

        general_font = QFont("Segoe UI", 12); button_font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        minmax_font = QFont("Segoe UI", 8)

        self.abs_load_display = DisplayWidget("Absolute Load (N)")
        self.rel_load_display = DisplayWidget("Relative Load (N)")
        self.abs_disp_display = DisplayWidget("Absolute Displacement (mm)")
        self.rel_disp_display = DisplayWidget("Relative Displacement (mm)")
        self.calib_status_display = DisplayWidget("Active Calibration")
        self.calib_status_display.set_value("Not Calibrated")

        self.up_button = QPushButton("↑ UP ↑"); self.down_button = QPushButton("↓ DOWN ↓")
        
        self.speed_spinbox = QDoubleSpinBox(); self.speed_spinbox.setFont(QFont("Segoe UI", 12))
        self.speed_spinbox.setDecimals(2); self.speed_spinbox.setRange(self.MIN_SPEED, self.MAX_SPEED)
        self.speed_spinbox.setSingleStep(0.1); self.speed_spinbox.setValue(1.0)
        self.speed_spinbox.setSuffix(" mm/s")
        
        self.speed_bar = SpeedBarWidget()

        self.homing_button = QPushButton("HOMING")
        self.zero_rel_load_button = QPushButton("Zero Relative Load")
        self.zero_rel_disp_button = QPushButton("Zero Relative Displacement")
        self.back_button = QPushButton("Back to Menu")

        for btn in [self.up_button, self.down_button, self.homing_button, self.zero_rel_load_button, self.zero_rel_disp_button, self.back_button]:
            btn.setFont(button_font)
            btn.setMinimumHeight(40)

        main_layout = QGridLayout(self)
        display_layout = QHBoxLayout(); display_layout.addWidget(self.abs_load_display)
        display_layout.addWidget(self.rel_load_display); display_layout.addWidget(self.abs_disp_display)
        display_layout.addWidget(self.rel_disp_display)
        left_vbox = QVBoxLayout(); speed_label_title = QLabel("Jog Speed:"); speed_label_title.setFont(general_font)
        left_vbox.addWidget(self.up_button); left_vbox.addWidget(self.down_button); left_vbox.addSpacing(20)
        left_vbox.addWidget(speed_label_title); left_vbox.addWidget(self.speed_spinbox); left_vbox.addWidget(self.speed_bar)
        min_label = QLabel(f"Min: {self.MIN_SPEED:.2f} mm/s"); min_label.setFont(minmax_font)
        max_label = QLabel(f"Max: {self.MAX_SPEED:.2f} mm/s"); max_label.setFont(minmax_font)
        left_vbox.addWidget(min_label); left_vbox.addWidget(max_label); left_vbox.addStretch(1)
        right_vbox = QVBoxLayout(); right_vbox.addWidget(self.calib_status_display); right_vbox.addStretch(1)
        functions_layout = QHBoxLayout(); functions_layout.addWidget(self.homing_button)
        functions_layout.addWidget(self.zero_rel_disp_button); functions_layout.addWidget(self.zero_rel_load_button)
        main_layout.addLayout(display_layout, 0, 0, 1, 2); main_layout.addLayout(left_vbox, 1, 0)
        main_layout.addLayout(right_vbox, 1, 1); main_layout.addLayout(functions_layout, 2, 0, 1, 2)
        main_layout.addWidget(self.back_button, 3, 0, 1, 2); main_layout.setColumnStretch(0, 2); main_layout.setColumnStretch(1, 1)
        
        self.up_button.pressed.connect(self.start_moving_up); self.up_button.released.connect(self.stop_moving)
        self.down_button.pressed.connect(self.start_moving_down); self.down_button.released.connect(self.stop_moving)
        self.homing_button.clicked.connect(self.toggle_homing) # MODIFICATO
        self.zero_rel_load_button.clicked.connect(self.zero_relative_load)
        self.zero_rel_disp_button.clicked.connect(self.zero_relative_displacement)
        self.speed_spinbox.valueChanged.connect(self.update_speed_controls)
        self.back_button.clicked.connect(self.back_to_menu_requested.emit)
        
        self.update_displays(); self.update_speed_controls()

    def set_calibration_status(self, status_text):
        self.calib_status_display.set_value(status_text)
    
    def send_command(self, command):
        self.communicator.send_command(command)

    def start_moving_up(self): self.set_speed(); self.send_command("JOG_UP")
    def start_moving_down(self): self.set_speed(); self.send_command("JOG_DOWN")
    def stop_moving(self): self.send_command("STOP")
    
    def toggle_homing(self):
        """ NUOVA VERSIONE: gestisce l'avvio e l'arresto dell'homing, inclusa l'interruzione. """
        if not self.is_homing_active:
            # Inizia l'homing
            self.send_command("HOME")
            self.homing_button.setText("STOP Homing")
            # Disabilita gli altri controlli di movimento per sicurezza
            self.up_button.setEnabled(False)
            self.down_button.setEnabled(False)
            self.is_homing_active = True
        else:
            # Interrompi l'homing
            self.send_command("STOP")
            # --- MODIFICA FONDAMENTALE ---
            # Ripristina l'interfaccia immediatamente, senza aspettare una risposta
            self.reset_homing_ui()
            # --- FINE MODIFICA ---

    def reset_homing_ui(self):
        """ NUOVA FUNZIONE: ripristina l'interfaccia dopo l'homing (o l'interruzione). """
        self.homing_button.setText("HOMING")
        self.up_button.setEnabled(True)
        self.down_button.setEnabled(True)
        self.is_homing_active = False
        
    def zero_relative_load(self): self.load_offset_N = self.absolute_load_N; self.update_displays()
    def zero_relative_displacement(self): self.displacement_offset_mm = self.absolute_displacement_mm; self.update_displays()
    def set_speed(self): self.send_command(f"SET_SPEED:{self.speed_spinbox.value():.2f}")
    def update_speed_controls(self):
        self.set_speed()
        speed_value = self.speed_spinbox.value()
        percent = ((speed_value - self.MIN_SPEED) / (self.MAX_SPEED - self.MIN_SPEED)) * 100
        self.speed_bar.setValue(percent)
    
    def update_displays(self):
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