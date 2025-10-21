from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QDoubleSpinBox, QGridLayout, QFileDialog, QMessageBox
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont

import pyqtgraph as pg
from collections import deque
import time
from datetime import datetime
from custom_widgets import DisplayWidget, SpeedBarWidget
import numpy as np
from data_saver import DataSaver


class ManualControlWidget(QWidget):
    back_to_menu_requested = pyqtSignal()
    limits_button_requested = pyqtSignal()
    
    def __init__(self, communicator, parent=None):
        super().__init__(parent)
        self.communicator = communicator
        self.is_homing_active = False # NUOVO: Stato per tracciare l'homing

        # --- NUOVE VARIABILI PER GRAFICO E REGISTRAZIONE ---
        self.is_recording = False
        self.recorded_data = []
        
        # Prepara le strutture dati per il grafico a scorrimento
        self.time_window_seconds = 5.0
        # Calcola il numero di punti da visualizzare (5s * 50Hz = 250 punti)
        num_points = int(self.time_window_seconds * 50) 
        self.plot_time_data = deque(maxlen=num_points)
        self.plot_force_data = deque(maxlen=num_points)
        self.plot_start_time = 0
        # --- FINE NUOVE VARIABILI ---

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
        self.limits_button = QPushButton("LIMITS")
        self.limits_button.setStyleSheet("background-color: #F39C12; color: white;")

              # --- CREAZIONE NUOVI WIDGET PER GRAFICO E REC ---
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_widget.showGrid(x=True, y=True)
        self.plot_widget.setLabel('left', 'Load (N)')
        self.plot_widget.setLabel('bottom', 'Time (s)')
        self.plot_curve = self.plot_widget.plot(pen='b')

        self.time_window_spinbox = QDoubleSpinBox()
        self.time_window_spinbox.setSuffix(" s")
        self.time_window_spinbox.setRange(1.0, 60.0)
        self.time_window_spinbox.setValue(self.time_window_seconds)
        self.time_window_spinbox.setFont(general_font) # Usa il font generale

        self.rec_button = QPushButton("REC")
        # --- NUOVO STILE PULSANTE REC ---
        rec_font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        self.rec_button.setFont(rec_font)
        self.rec_button.setStyleSheet("background-color: #E74C3C; color: white;")
        # --- FINE STILE ---
        
        # Aggiungiamo il timer per l'aggiornamento del grafico
        self.plot_update_timer = QTimer(self)
        self.plot_update_timer.setInterval(33) # Aggiorna circa 30 volte al secondo (1000ms / 30fps)

        # Assicurati che il pulsante sia incluso nel ciclo for per lo stile
        for btn in [self.up_button, self.down_button, self.homing_button, self.zero_rel_load_button, self.zero_rel_disp_button, self.back_button, self.limits_button, self.rec_button]: # Aggiunto self.rec_button
            # Applica lo stile base, quello specifico verrà sovrascritto
            if btn != self.rec_button:
                 btn.setFont(button_font)
            btn.setMinimumHeight(40)

        for btn in [self.up_button, self.down_button, self.homing_button, self.zero_rel_load_button, self.zero_rel_disp_button, self.back_button, self.limits_button]:
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
        functions_layout.addStretch(1)
        functions_layout.addWidget(self.limits_button)
        main_layout.addLayout(display_layout, 0, 0, 1, 2)
        # --- NUOVO LAYOUT PER LA SEZIONE GRAFICO ---
        graph_section_layout = QVBoxLayout()
        graph_controls_layout = QHBoxLayout()
        graph_controls_layout.addWidget(QLabel("Time Window:"))
        graph_controls_layout.addWidget(self.time_window_spinbox)
        graph_controls_layout.addStretch(1)
        graph_controls_layout.addWidget(self.rec_button)

        graph_section_layout.addWidget(self.plot_widget)
        graph_section_layout.addLayout(graph_controls_layout)
        
        # Aggiungi il nuovo layout al layout principale
        main_layout.addLayout(graph_section_layout, 1, 0, 1, 2)
        # --- FINE NUOVO LAYOUT --

        main_layout.addLayout(left_vbox, 2, 0)
        main_layout.addLayout(right_vbox, 2, 1); main_layout.addLayout(functions_layout, 3, 0, 1, 2)
        main_layout.addWidget(self.back_button, 4, 0, 1, 2); main_layout.setColumnStretch(0, 2); main_layout.setColumnStretch(1, 1)
        
        self.up_button.pressed.connect(self.start_moving_up); self.up_button.released.connect(self.stop_moving)
        self.down_button.pressed.connect(self.start_moving_down); self.down_button.released.connect(self.stop_moving)
        self.homing_button.clicked.connect(self.toggle_homing) # MODIFICATO
        self.zero_rel_load_button.clicked.connect(self.zero_relative_load)
        self.zero_rel_disp_button.clicked.connect(self.zero_relative_displacement)
        self.speed_spinbox.valueChanged.connect(self.update_speed_controls)
        self.back_button.clicked.connect(self.back_to_menu_requested.emit)
        self.limits_button.clicked.connect(self.limits_button_requested.emit)
        # --- NUOVE CONNESSIONI ---
        self.rec_button.clicked.connect(self.on_rec_button_clicked)
        self.time_window_spinbox.valueChanged.connect(self.on_time_window_changed)
        self.plot_update_timer.timeout.connect(self._update_plot)
        # --- FINE ---
        self.update_displays(); self.update_speed_controls()

    def handle_stream_data(self, load_N, disp_mm, time_s, cycle_count):
             # Se la schermata non è visibile, non fare nulla
        if not self.isVisible():
            self.plot_start_time = 0 # Resetta il tempo se la schermata viene nascosta
            return

        # Inizializza il tempo di partenza al primo dato ricevuto
        if self.plot_start_time == 0:
            self.plot_start_time = time.time()
            # Pulisci i dati vecchi all'inizio di una nuova visualizzazione
            self.plot_time_data.clear()
            self.plot_force_data.clear()

        elapsed_time = time.time() - self.plot_start_time
        
        self.plot_time_data.append(elapsed_time)
        self.plot_force_data.append(load_N)

        # Se la registrazione è attiva, salva tutti i dati
        if self.is_recording:
            relative_disp = disp_mm - self.displacement_offset_mm
            relative_load = load_N - self.load_offset_N
            self.recorded_data.append((elapsed_time, relative_disp, relative_load, disp_mm, load_N))

    # Aggiungi questo nuovo metodo privato alla classe
    def _update_plot(self):
        self.plot_curve.setData(list(self.plot_time_data), list(self.plot_force_data))
        
        # Calcola dinamicamente la finestra di visualizzazione per l'effetto "scorrimento"
        if self.plot_time_data:
            # Prendi il tempo dell'ultimo dato arrivato
            current_time = self.plot_time_data[-1]
            # Calcola l'inizio della finestra visibile
            start_time = max(0, current_time - self.time_window_seconds)
            # Imposta il range visibile dell'asse X, senza spazi aggiuntivi (padding=0)
            self.plot_widget.setXRange(start_time, current_time, padding=0)

    def on_rec_button_clicked(self):
        if not self.is_recording:
            # --- Avvia la registrazione ---
            self.is_recording = True
            self.rec_button.setText("■ STOP REC")
            
            self.recorded_data = []
            
            # Disabilita i controlli che potrebbero interferire
            self.homing_button.setEnabled(False)
            self.back_button.setEnabled(False)
        else:
            # --- Ferma la registrazione ---
            self.is_recording = False
            self.rec_button.setText("REC")

            # Riabilita i controlli
            self.homing_button.setEnabled(True)
            self.back_button.setEnabled(True)
            
            # Chiama la funzione di salvataggio
            self._save_recorded_data()

    def _save_recorded_data(self):
        # 1. Controlla se ci sono dati da salvare
        if not self.recorded_data:
            QMessageBox.information(self, "Info", "Nessun dato registrato da salvare.")
            return

        # 2. Propone un nome di file e apre la finestra di dialogo per il salvataggio
        default_filename = f"ManualRecord_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"
        filepath, _ = QFileDialog.getSaveFileName(
            self, 
            "Salva Registrazione Manuale", 
            default_filename, 
            "Excel Files (*.xlsx)"
        )

        # 3. Se l'utente ha scelto un file (non ha premuto "Annulla")
        if filepath:
            # 4. Crea un "provino fittizio" al volo per essere compatibile con DataSaver.
            # Inseriamo i dati registrati e alcuni valori segnaposto.
            manual_specimen = {
                "gauge_length": np.nan,  # Usa NaN invece di 0
                "area": np.nan,          # Usa NaN invece di 0
                "speed": "N/A",
                "speed_unit": "Manual",
                "stop_criterion_value": "N/A",
                "stop_criterion_unit": "Manual Stop",
                "test_data": self.recorded_data
            }
            
            # DataSaver si aspetta un dizionario di provini, quindi creiamolo
            specimens_to_save = {"Manual Recording": manual_specimen}
            
            # 5. Usa la classe DataSaver per fare il lavoro sporco
            saver = DataSaver()
            success, message = saver.save_batch_to_xlsx(
                specimens_to_save, 
                filepath, 
                self.calib_status_display.value_label.text() # Passiamo l'info di calibrazione
            )

            # 6. Comunica il risultato all'utente
            if success:
                QMessageBox.information(self, "Salvataggio Riuscito", message)
            else:
                QMessageBox.critical(self, "Errore di Salvataggio", message)

    def showEvent(self, event):
        """ Questo metodo viene chiamato automaticamente quando il widget diventa visibile. """
        super().showEvent(event)
        print("DEBUG: ManualControlWidget mostrato, avvio timer del grafico.")
        self.plot_start_time = 0 # Azzera il tempo per far ripartire il grafico
        self.plot_update_timer.start()

    def hideEvent(self, event):
        """ Questo metodo viene chiamato automaticamente quando il widget viene nascosto. """
        super().hideEvent(event)
        print("DEBUG: ManualControlWidget nascosto, fermo timer del grafico.")
        self.plot_update_timer.stop()   

    def on_time_window_changed(self, value):
        self.time_window_seconds = value
        num_points = int(self.time_window_seconds * 50)
        # Crea delle liste NUOVE e VUOTE con la nuova lunghezza massima
        self.plot_time_data = deque(maxlen=num_points)
        self.plot_force_data = deque(maxlen=num_points)

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


