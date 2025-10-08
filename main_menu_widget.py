from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

class MainMenuWidget(QWidget):
    """La schermata del menu principale."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

        # --- Creazione pulsanti ---
        self.calibrate_button = QPushButton("Calibrate Load Cell")
        self.manual_button = QPushButton("Manual Control")
        self.monotonic_button = QPushButton("Monotonic Test")
        self.cyclic_button = QPushButton("Cyclic Test")
        self.exit_button = QPushButton("Exit and Close")

        button_font = QFont("Segoe UI", 16, QFont.Weight.Bold)

        # --- Ordine dei pulsanti ---
        buttons = [
            self.calibrate_button, 
            self.manual_button, 
            self.monotonic_button, 
            self.cyclic_button, 
            self.exit_button
        ]
        
        for button in buttons:
            button.setMinimumHeight(75) 
            button.setFont(button_font)
            layout.addWidget(button)

        layout.addStretch(1)
        layout.setSpacing(20)
        
        # --- Stato dei pulsanti ---
        self.calibrate_button.setEnabled(True)
        self.monotonic_button.setEnabled(True) # MODIFICA: Abilitato per accedere alla nuova schermata
        self.cyclic_button.setEnabled(False)
        self.exit_button.clicked.connect(QApplication.instance().quit)