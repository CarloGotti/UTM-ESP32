from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGridLayout, QFrame, QLineEdit, QDoubleSpinBox, QComboBox,
    QCheckBox, QListWidget,  QListWidgetItem, QMessageBox, QDialogButtonBox, QDialog, QFormLayout, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QLocale
from PyQt6.QtGui import QFont, QDoubleValidator
from data_saver import DataSaver
from datetime import datetime

import pyqtgraph as pg
import numpy as np

from custom_widgets import DisplayWidget



class MonotonicTestWidget(QWidget):
    back_to_menu_requested = pyqtSignal()
    limits_button_requested = pyqtSignal() # <-- NUOVO SEGNALE

    def __init__(self, communicator, main_window, parent=None):
        super().__init__(parent)
        self.communicator = communicator
        self.main_window = main_window

        # --- STATO INTERNO ---
        self.is_homed = False
        self.active_calibration_info = "Not Calibrated"

        # --- FINE NUOVE VARIABILI ---
        self.specimens = {}
        self.current_specimen_name = None
        self.is_test_running = False
        self.absolute_load_N = 0.0
        self.load_offset_N = 0.0
        self.absolute_displacement_mm = 0.0
        self.displacement_offset_mm = 0.0
        self.current_test_data = []
        self.current_resistance_ohm = -999.0 # Per memorizzare l'ultimo valore LCR
        # --- FONT E VALIDATORI ---
        general_font = QFont("Segoe UI", 11)
        button_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        title_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        self.only_float_validator = QDoubleValidator()
        self.only_float_validator.setDecimals(3)
        self.only_float_validator.setNotation(QDoubleValidator.Notation.StandardNotation)

        # --- LAYOUT PRINCIPALE ---
        main_layout = QVBoxLayout(self)

        # --- 1. SEZIONE SUPERIORE ---
        top_section_layout = QHBoxLayout()
        self.abs_load_display = DisplayWidget("Absolute Load (N)")
        self.rel_load_display = DisplayWidget("Relative Load (N)")
        self.abs_disp_display = DisplayWidget("Absolute Displacement (mm)")
        self.rel_disp_display = DisplayWidget("Relative Displacement (mm)")
        self.calib_status_display = DisplayWidget("Active Calibration")
        self.resistance_display = DisplayWidget("Resistance (Ω)") # <-- ASSICURATI CHE QUESTA RIGA CI SIA
        self.lcr_enable_checkbox = QCheckBox("Enable LCR Reading")
        
        locale_c = QLocale("C")  # forza separatore decimale con punto

        jog_controls_layout = QVBoxLayout()
        self.up_button = QPushButton("↑ UP ↑"); self.up_button.setFont(button_font)
        self.down_button = QPushButton("↓ DOWN ↓"); self.down_button.setFont(button_font)
        jog_speed_layout = QHBoxLayout()

        self.jog_speed_spinbox = QDoubleSpinBox()
        self.jog_speed_spinbox.setLocale(locale_c)
        self.jog_speed_spinbox.setSuffix(" mm/s")
        self.jog_speed_spinbox.setFixedWidth(100)
        self.jog_speed_spinbox.setRange(0.001, 25.0)
        self.jog_speed_spinbox.setValue(10.0)
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
        top_section_layout.addWidget(self.resistance_display)
        top_section_layout.addStretch(1)
        jog_controls_layout.addWidget(self.lcr_enable_checkbox)
        top_section_layout.addLayout(jog_controls_layout)

        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)

        # --- 2. SEZIONE CENTRALE (Parametri) ---
        params_layout = QGridLayout()
        params_layout.setColumnMinimumWidth(0, 100)  # etichette
        params_layout.setColumnMinimumWidth(1, 120)  # campi
        params_layout.setColumnStretch(0, 0)
        params_layout.setColumnStretch(1, 0)
        params_layout.setColumnStretch(2, 0)  
        params_layout.setColumnStretch(3, 1)  

        self.name_edit = QLineEdit()

        self.gauge_length_edit = QDoubleSpinBox()
        self.gauge_length_edit.setLocale(locale_c)
        self.gauge_length_edit.setDecimals(3)
        self.gauge_length_edit.setRange(0.001, 10000.0)
        self.gauge_length_edit.setSingleStep(0.1)
        self.gauge_length_edit.setSpecialValueText("")
        self.gauge_length_edit.setFixedWidth(120)
        self.gauge_length_edit.setAlignment(Qt.AlignmentFlag.AlignLeft) 

        self.automeasure_button = QPushButton("Automeasure"); self.automeasure_button.setEnabled(False)
        self.automeasure_button.setFixedWidth(200)

        self.area_edit = QDoubleSpinBox()
        self.area_edit.setLocale(locale_c)
        self.area_edit.setDecimals(3)
        self.area_edit.setRange(0.001, 10000.0)
        self.area_edit.setSingleStep(0.1)
        self.area_edit.setSpecialValueText("")
        self.area_edit.setFixedWidth(120)
        self.area_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)  


        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setLocale(locale_c)
        self.speed_spinbox.setDecimals(3)
        self.speed_spinbox.setRange(0.001, 500.0)
        self.speed_spinbox.setSpecialValueText("")
        self.speed_spinbox.setFixedWidth(120)
        self.speed_spinbox.setAlignment(Qt.AlignmentFlag.AlignLeft)


        self.speed_unit_combo = QComboBox(); self.speed_unit_combo.addItems(["mm/s", "mm/min", "%/s", "%/min"])
        self.speed_unit_combo.setFixedWidth(200)

        self.stop_criterion_spinbox = QDoubleSpinBox()
        self.stop_criterion_spinbox.setLocale(locale_c)
        self.stop_criterion_spinbox.setDecimals(3)
        self.stop_criterion_spinbox.setRange(0.001, 10000.0)
        self.stop_criterion_spinbox.setSpecialValueText("")
        self.stop_criterion_spinbox.setFixedWidth(120)
        self.stop_criterion_spinbox.setAlignment(Qt.AlignmentFlag.AlignLeft)


        self.stop_criterion_combo = QComboBox()
        self.stop_criterion_combo.addItems(["Displacement (mm)", "Strain (%)", "Force (N)", "Stress (MPa)"])
        self.stop_criterion_combo.setFixedWidth(200)
        self.return_to_start_checkbox = QCheckBox("Return to start point after test")

        params_layout.addWidget(QLabel("Name:"), 0, 0); params_layout.addWidget(self.name_edit, 0, 1, 1, 3)
        params_layout.addWidget(QLabel("Gauge Length (mm):"), 1, 0); params_layout.addWidget(self.gauge_length_edit, 1, 1); params_layout.addWidget(self.automeasure_button, 1, 2)
        params_layout.addWidget(QLabel("Area (mm²):"), 2, 0); params_layout.addWidget(self.area_edit, 2, 1)
        params_layout.addWidget(QLabel("Speed:"), 3, 0); params_layout.addWidget(self.speed_spinbox, 3, 1); params_layout.addWidget(self.speed_unit_combo, 3, 2)
        params_layout.addWidget(QLabel("Stop Criterion:"), 4, 0); params_layout.addWidget(self.stop_criterion_spinbox, 4, 1); params_layout.addWidget(self.stop_criterion_combo, 4, 2)
        params_layout.addWidget(self.return_to_start_checkbox, 5, 1, 1, 2)

        specimen_mgmt_layout = QHBoxLayout()
        self.new_button = QPushButton("NEW"); self.new_button.setFont(button_font)
        self.modify_button = QPushButton("MODIFY"); self.modify_button.setFont(button_font)
        self.delete_button = QPushButton("DELETE"); self.delete_button.setFont(button_font)
        specimen_mgmt_layout.addStretch(1)
        specimen_mgmt_layout.addWidget(self.new_button)
        specimen_mgmt_layout.addWidget(self.modify_button)
        specimen_mgmt_layout.addWidget(self.delete_button)
        specimen_mgmt_layout.addStretch(1)

        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)

        # --- 3. SEZIONE INFERIORE (Grafico e Controlli) ---
        test_area_layout = QHBoxLayout()

        graph_layout = QVBoxLayout()
        self.plot_widget = pg.PlotWidget(); self.plot_widget.setBackground('w')


        self.plot_curve = self.plot_widget.plot(pen='b'); self.plot_widget.showGrid(x=True, y=True)
        graph_controls_layout = QHBoxLayout()
        self.x_axis_combo = QComboBox(); self.x_axis_combo.addItems(["Relative Displacement (mm)", "Strain (%)"])
        self.y_axis_combo = QComboBox(); self.y_axis_combo.addItems(["Relative Load (N)", "Stress (MPa)"])
        self.x_axis_combo.currentIndexChanged.connect(self.refresh_plot)
        self.y_axis_combo.currentIndexChanged.connect(self.refresh_plot)

        self.overlay_checkbox = QCheckBox("Overlay previous tests")
        self.overlay_checkbox.stateChanged.connect(self.refresh_plot)
        graph_controls_layout.addWidget(QLabel("X-Axis:")); graph_controls_layout.addWidget(self.x_axis_combo); graph_controls_layout.addStretch(1)
        graph_controls_layout.addWidget(QLabel("Y-Axis:")); graph_controls_layout.addWidget(self.y_axis_combo); graph_controls_layout.addStretch(2)
        graph_controls_layout.addWidget(self.overlay_checkbox)
        self.reset_zoom_button = QPushButton("Reset Zoom")
        self.reset_zoom_button.clicked.connect(lambda: self.plot_widget.enableAutoRange())
        graph_controls_layout.addWidget(self.reset_zoom_button)
        graph_layout.addWidget(self.plot_widget); graph_layout.addLayout(graph_controls_layout)

        right_panel_layout = QVBoxLayout()
        self.specimen_list = QListWidget()
        start_stop_layout = QHBoxLayout()
        self.start_button = QPushButton("▶ START"); self.stop_button = QPushButton("■ STOP")
        self.start_button.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.stop_button.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.start_button.setStyleSheet("background-color: #2ECC71; color: white;")
        self.stop_button.setStyleSheet("background-color: #E74C3C; color: white;")
        start_stop_layout.addWidget(self.start_button); start_stop_layout.addWidget(self.stop_button)
        right_panel_layout.addWidget(QLabel("Test Batch:", font=title_font))
        right_panel_layout.addWidget(self.specimen_list)
        # Nuova lista con checkbox per overlay
        self.overlay_list = QListWidget()
        self.overlay_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        right_panel_layout.addWidget(QLabel("Overlay selection:", font=title_font))
        right_panel_layout.addWidget(self.overlay_list)

        # Connessione segnale → quando cambi check, aggiorna grafico
        self.overlay_list.itemChanged.connect(self.on_overlay_item_changed)


        right_panel_layout.addLayout(start_stop_layout)
        self.plot_curves = {}   # dict: specimen_name -> plot curve object
        self.plot_widget.addLegend()  # aggiunge la legenda

        self.resistance_axis_viewbox = None # La ViewBox per la resistenza
        self.resistance_axis_item = None    # L'AxisItem a destra
        self.resistance_curve = None




        test_area_layout.addLayout(graph_layout, 3)
        test_area_layout.addLayout(right_panel_layout, 1)

        # --- 4. SEZIONE FINALE ---
        bottom_buttons_layout = QHBoxLayout()
        self.zero_rel_load_button = QPushButton("Zero Relative Load"); self.zero_rel_load_button.setFont(button_font)
        self.zero_rel_disp_button = QPushButton("Zero Relative Displacement"); self.zero_rel_disp_button.setFont(button_font)

        self.limits_button = QPushButton("LIMITS"); self.limits_button.setFont(button_font)
        self.limits_button.setStyleSheet("background-color: #F39C12; color: white;") # Colore per evidenziarlo
        self.finish_save_button = QPushButton("FINISH & SAVE"); self.finish_save_button.setFont(button_font)
        self.back_button = QPushButton("Back to Menu"); self.back_button.setFont(button_font)

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
        self.zero_rel_load_button.clicked.connect(self.zero_relative_load)
        self.zero_rel_disp_button.clicked.connect(self.zero_relative_displacement)
        self.up_button.pressed.connect(self.start_moving_up)
        self.up_button.released.connect(self.stop_moving)
        self.down_button.pressed.connect(self.start_moving_down)
        self.down_button.released.connect(self.stop_moving)
        self.jog_speed_spinbox.valueChanged.connect(self.set_speed)
        self.new_button.clicked.connect(self.on_new_specimen)
        self.delete_button.clicked.connect(self.on_delete_specimen)
        self.modify_button.clicked.connect(self.on_modify_specimen)
        self.specimen_list.itemClicked.connect(self.on_specimen_selected)
        self.start_button.clicked.connect(self.on_start_test)
        self.finish_save_button.clicked.connect(self.on_finish_and_save)
        self.lcr_enable_checkbox.stateChanged.connect(self._on_lcr_checkbox_changed)
       
        #self.stop_button.clicked.connect(self.on_stop_test)
        # collegamento di debug temporaneo
        self.stop_button.clicked.connect(lambda: self.on_stop_test(user_initiated=True))
        print("DEBUG: collegato stop_button a on_stop_test")
        self.stop_button.clicked.connect(lambda: print("DEBUG: click catturato"))

        self.limits_button.clicked.connect(self.limits_button_requested.emit)

        self.update_stop_criterion_options()
        self.update_displays()
        self.update_ui_for_test_state()

    # --- LOGICA TEST ---
    def on_start_test(self):
        if self.current_specimen_name is None:
            QMessageBox.warning(self, "Warning", "Please select a specimen from the list before starting.")
            return

        specimen = self.specimens[self.current_specimen_name]

        if specimen.get("test_data") is not None:
            reply = QMessageBox.question(
                self, "Confirm Overwrite",
                f"Specimen '{self.current_specimen_name}' already has test data. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # ✅ velocità sempre in mm/s (La tua logica qui è corretta e rimane invariata)
        speed_mms = specimen.get("speed_mm_s")
        if speed_mms is None:
            speed_mms = self.convert_speed(specimen["speed"], specimen["speed_unit"], specimen["gauge_length"])

        # ✅ stop criterion sempre convertito (La tua logica qui è corretta e rimane invariata)
        stop_val = specimen.get("stop_criterion_converted")
        stop_base_unit = specimen.get("stop_criterion_base_unit")

        if stop_val is None or stop_base_unit is None:
            stop_val, stop_base_unit = self.convert_stop_criterion(
                specimen["stop_criterion_value"],
                specimen["stop_criterion_unit"],
                specimen["gauge_length"],
                specimen["area"]
            )
        
        # Inizializziamo le variabili per il comando finale
        criterion_str = ""
        stop_val_for_fw = 0.0

        if stop_base_unit == "mm":
            criterion_str = "DISP"
            # Calcola il target assoluto in mm tenendo conto dell'offset
            stop_val_for_fw = stop_val + self.displacement_offset_mm

        elif stop_base_unit == "N":
            criterion_str = "FORCE"
            # Calcola il target assoluto in N tenendo conto dell'offset
            target_force_abs_N = stop_val + self.load_offset_N

            # --- NUOVO: ALERT DI SICUREZZA ---
            if target_force_abs_N > self.current_force_limit_N:
                reply = QMessageBox.warning(
                    self, "Limite di Sicurezza Superato",
                    f"La forza target ... supera il limite ... ({self.main_window.current_force_limit_N:.2f} N).\n\n" 
                    "Continuare ...?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    return # Annulla l'avvio del test
            # --- FINE ALERT ---

            # --- NUOVO: CONVERSIONE IN GRAMMI PER IL FIRMWARE ---
            # Il firmware si aspetta il valore di stop in grammi
            stop_val_for_fw = (target_force_abs_N / 9.81) * 1000.0
            # --- FINE CONVERSIONE ---

        else:
            QMessageBox.critical(self, "Error", f"Stop criterion '{specimen['stop_criterion_unit']}' not yet implemented.")
            return

        self.current_test_data = []
        # Svuota solo la curva del test corrente, non tutte
        if self.current_specimen_name in self.plot_curves:
            self.plot_curves[self.current_specimen_name].setData([], [])
        else:
            # Se la curva non esiste ancora (es. primo test), creala
            curve = self.plot_widget.plot([], [], pen=self.get_pen_for_specimen(self.current_specimen_name), name=self.current_specimen_name)
            self.plot_curves[self.current_specimen_name] = curve
        if self.resistance_curve:
            self.resistance_curve.setData([], [])

        # ✅ invio sempre valori convertiti e corretti per il firmware
        command = f"START_TEST:SPEED_MMS={speed_mms:.3f};CRITERION={criterion_str};STOP_VAL={stop_val_for_fw:.3f}"
        self.send_command(command)
        self.send_command("SET_MODE:STREAMING")

        self.is_test_running = True
        print("DEBUG: avvio test, is_test_running =", self.is_test_running)
        self.update_ui_for_test_state()



    def on_stop_test(self, user_initiated=True):
        print(f"DEBUG: on_stop_test chiamato (user_initiated={user_initiated})")
        # Se il test non è in corso, non fare nulla
        if not self.is_test_running:
            return
        # Se lo stop è avviato dall'utente (click), invia SOLO lo stop di emergenza.
        # Sarà la risposta del firmware ("STATUS:TEST_STOPPED_BY_USER") a fare il resto.
        if user_initiated:
            # STOP con priorità assoluta; ripristina POLLING

            self.communicator.send_emergency_stop()      # kill switch immediato
            print("DEBUG GUI: inviato !")
            self.send_command("STOP")
            print("DEBUG GUI: inviato STOP")
            self.send_command("SET_MODE:POLLING")
            print("DEBUG GUI: inviato SET_MODE:POLLING")
        # Questa parte viene eseguita solo quando chiamata da MainWindow (user_initiated=False)
        self.is_test_running = False
        self.update_ui_for_test_state()

        if self.current_specimen_name:
            self.specimens[self.current_specimen_name]['test_data'] = self.current_test_data

                # --- NUOVO: LOGICA DI AUTOSAVE ---
            try:
                specimen_to_save = {self.current_specimen_name: self.specimens[self.current_specimen_name]}
                # Crea un nome di file automatico
                filename = f"AUTOSAVE_{self.current_specimen_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

                saver = DataSaver()
                # Salva il singolo provino usando la stessa logica del batch
                saver.save_batch_to_xlsx(specimen_to_save, filename, self.active_calibration_info)
                print(f"DEBUG: Autosave completato per {self.current_specimen_name} in {filename}")
            except Exception as e:
                print(f"ERRORE AUTOSAVE: {e}")
            # --- FINE AUTOSAVE ---

            specimen = self.specimens[self.current_specimen_name]
            if specimen.get("return_to_start", False):
                self.send_command("RETURN_TO_START")
        # Aggiorna il grafico in base al provino selezionato e all'overlay
        self.refresh_plot()    

    def handle_stream_data(self, load_N, disp_mm, time_s, cycle_count, resistance_ohm):
        if not self.is_test_running:
            return

        # Aggiorna i valori assoluti con lo stream
        self.absolute_load_N = load_N
        self.absolute_displacement_mm = disp_mm
        self.current_resistance_ohm = resistance_ohm
        relative_disp = disp_mm - self.displacement_offset_mm
        relative_load = load_N - self.load_offset_N

        # Salva la tupla a 5 elementi, con il tempo all'indice 0
        self.current_test_data.append((time_s, relative_disp, relative_load, disp_mm, load_N, resistance_ohm))

        # Aggiorna la curva del grafico in tempo reale
        if self.current_specimen_name in self.plot_curves:
            specimen = self.specimens[self.current_specimen_name]
            area = specimen.get("area", 1.0)
            gauge = specimen.get("gauge_length", 1.0)

            x_mode = self.x_axis_combo.currentText()
            y_mode = self.y_axis_combo.currentText()

            # Estrai i dati per il grafico usando gli indici corretti
            # Spostamento Relativo è all'indice 1, Carico Relativo è all'indice 2
            x_raw_data = [p[1] for p in self.current_test_data] 
            y_raw_data = [p[2] for p in self.current_test_data]

            if "Strain" in x_mode and gauge > 0:
                x_data_final = [(d / gauge) * 100 for d in x_raw_data]
            else:
                x_data_final = x_raw_data

            if "Stress" in y_mode and area > 0:
                y_data_final = [(f / area) for f in y_raw_data]
            else:
                y_data_final = y_raw_data

            self.plot_curves[self.current_specimen_name].setData(x_data_final, y_data_final)
            self.plot_widget.setLabel("bottom", x_mode)
            self.plot_widget.setLabel("left", y_mode)
            if self.resistance_curve and self.lcr_enable_checkbox.isChecked():
                try:
                    # Estrai i dati di resistenza (indice 5)
                    r_raw_data = [p[5] for p in self.current_test_data]
                    # Applica il filtro per i valori negativi
                    r_data_final = [r if r >= 0 else np.nan for r in r_raw_data]
                    
                    # Usa gli stessi dati X della curva principale
                    self.resistance_curve.setData(x_data_final, r_data_final)
                except Exception as e:
                    print(f"Errore aggiornamento curva resistenza live (Mono): {e}")
        
        # Aggiorna i display numerici (i contatori)
        self.update_displays()
        


    # --- UI STATE ---
    def update_ui_for_test_state(self):
        is_running = self.is_test_running
        print("DEBUG: update_ui_for_test_state → isrunning =", is_running)
        self.start_button.setEnabled(not is_running)
        self.stop_button.setEnabled(is_running)

        widgets_to_toggle = [
            self.up_button, self.down_button, self.jog_speed_spinbox,
            self.new_button, self.modify_button, self.delete_button,
            self.specimen_list, self.back_button, self.name_edit,
            self.gauge_length_edit, self.area_edit, self.speed_spinbox,
            self.speed_unit_combo, self.stop_criterion_spinbox,
            self.stop_criterion_combo, self.return_to_start_checkbox, self.zero_rel_load_button,  self.zero_rel_disp_button, self.finish_save_button, self.limits_button
        ]
        for widget in widgets_to_toggle:
            widget.setEnabled(not is_running)

    # --- MOVIMENTO MANUALE ---
    def start_moving_up(self):
        self.set_speed()
        self.send_command("JOG_UP")

    def start_moving_down(self):
        self.set_speed()
        self.send_command("JOG_DOWN")

    def stop_moving(self):
        self.send_command("STOP")

    def set_speed(self):
        self.send_command(f"SET_SPEED:{self.jog_speed_spinbox.value():.2f}")

    # --- ZERI RELATIVI ---
    def zero_relative_load(self):
        self.load_offset_N = self.absolute_load_N
        self.update_displays()

    def zero_relative_displacement(self):
        self.displacement_offset_mm = self.absolute_displacement_mm
        self.update_displays()

    # --- DISPLAY ---
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

        # --- NUOVA LOGICA PER DISPLAY RESISTENZA (copiata da cyclic) ---
        res_value = self.current_resistance_ohm
        if res_value <= -900.0: display_text = "N/A"
        elif res_value == -1.0: display_text = "Timeout"
        elif res_value == -2.0: display_text = "Parse Err"
        elif res_value < 0: display_text = "ERR"
        else:
            if res_value > 1e6 or (res_value < 1e-3 and res_value != 0):
                display_text = f"{res_value:.1e}"
            else:
                display_text = f"{res_value:.2f}"
        #print(f"DEBUG GUI UpdateDisp ({type(self).__name__}): self.current_resistance_ohm = {self.current_resistance_ohm}, display_text = '{display_text}'")
        #print(f"MONOTONIC DEBUG UpdateDisp: R_value={res_value}, Text='{display_text}'")
        self.resistance_display.set_value(display_text)

    def update_stop_criterion_options(self):
        try:
            is_area_valid = self.area_edit.value() > 0
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
        self.update_displays()

    def set_calibration_status(self, status_text):
        self.active_calibration_info = status_text
        self.calib_status_display.set_value(status_text)

    # --- SPECIMEN MANAGEMENT ---
    def on_new_specimen(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Input Error", "Specimen name cannot be empty.")
            return
        if name in self.specimens:
            QMessageBox.warning(self, "Input Error", f"Specimen with name '{name}' already exists.")
            return
        try:
            gauge_length = self.gauge_length_edit.value()
            area = self.area_edit.value()
            speed_value = self.speed_spinbox.value()
            speed_unit = self.speed_unit_combo.currentText()
            stop_value = self.stop_criterion_spinbox.value()
            stop_unit = self.stop_criterion_combo.currentText()

            if gauge_length <= 0 or area <= 0:
                raise ValueError("Gauge length and area must be positive.")

            # conversioni
            speed_mm_s = self.convert_speed(speed_value, speed_unit, gauge_length)
            stop_converted, stop_base_unit = self.convert_stop_criterion(stop_value, stop_unit, gauge_length, area)

# --- NUOVO: VALIDAZIONE LIMITI ---
            validation_passed = True
            error_message = ""
            if stop_base_unit == "mm":
                limit_to_check = self.main_window.current_disp_limit_mm
                # Controlla se il target di spostamento (assoluto) supera il limite
                # Nota: stop_converted è relativo, dobbiamo considerare l'offset attuale
                target_absolute_disp = stop_converted + self.displacement_offset_mm
                if abs(target_absolute_disp) > limit_to_check:
                    validation_passed = False
                    error_message = (f"The calculated absolute stop displacement ({target_absolute_disp:.2f} mm) "
                                     f"exceeds the machine limit of ±{limit_to_check:.2f} mm.")
            elif stop_base_unit == "N":
                limit_to_check = self.main_window.current_force_limit_N
                # Controlla se il target di forza (assoluto) supera il limite
                target_absolute_force = stop_converted + self.load_offset_N
                if target_absolute_force > limit_to_check:
                     validation_passed = False
                     error_message = (f"The calculated absolute stop force ({target_absolute_force:.2f} N) "
                                      f"exceeds the machine limit of {limit_to_check:.2f} N.")

            if not validation_passed:
                QMessageBox.warning(self, "Limit Exceeded", error_message)
                return # Non creare il provino

            specimen_data = {
                "name": name,
                "gauge_length": gauge_length,
                "area": area,
                "speed": speed_value,
                "speed_unit": speed_unit,
                "speed_mm_s": speed_mm_s,  # ✅ aggiunto
                "stop_criterion_value": stop_value,
                "stop_criterion_unit": stop_unit,
                "stop_criterion_converted": stop_converted,  # ✅ aggiunto
                "stop_criterion_base_unit": stop_base_unit,  # ✅ aggiunto
                "return_to_start": self.return_to_start_checkbox.isChecked(),
                "test_data": None,
                "visible": True
            }
        except (ValueError, TypeError):
            QMessageBox.warning(self, "Input Error", "Invalid or incomplete numeric input.")
            return

        self.specimens[name] = specimen_data
        self.specimen_list.addItem(name)
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.overlay_list.addItem(item)
        self.name_edit.clear()

        # Aggiorna il grafico in base al provino selezionato e all'overlay
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

            # --- SOLUZIONE: AZZERA LO STATO ATTUALE ---
            self.current_specimen_name = None
            # Svuota anche i campi di input per coerenza visiva
            self.name_edit.clear()
            self.gauge_length_edit.setValue(0)
            self.area_edit.setValue(0)
            self.speed_spinbox.setValue(0)
            self.stop_criterion_spinbox.setValue(0)
            # --- FINE SOLUZIONE ---
    
            # Aggiorna il grafico
            self.refresh_plot()


    def on_modify_specimen(self):
        selected_item = self.specimen_list.currentItem()
        if not selected_item:
            QMessageBox.information(self, "Info", "Please select a specimen to modify.")
            return
        name_to_modify = selected_item.text()
        try:
            new_name = self.name_edit.text().strip()
            if not new_name:
                raise ValueError("Name cannot be empty.")

            original = self.specimens[name_to_modify]
            already_tested = bool(original.get("test_data"))

            new_gauge_length = self.gauge_length_edit.value()
            new_area = self.area_edit.value()

            modified_data = {
                "name": new_name,
                "gauge_length": new_gauge_length,
                "area": new_area,
                "return_to_start": self.return_to_start_checkbox.isChecked(),
                "test_data": original.get("test_data")
            }

            if not already_tested:
                # speed
                speed_value = self.speed_spinbox.value()
                speed_unit = self.speed_unit_combo.currentText()
                speed_mm_s = self.convert_speed(speed_value, speed_unit, new_gauge_length)

                # stop criterion
                stop_value = self.stop_criterion_spinbox.value()
                stop_unit = self.stop_criterion_combo.currentText()
                stop_converted, stop_base_unit = self.convert_stop_criterion(stop_value, stop_unit, new_gauge_length, new_area)

# --- NUOVO: VALIDAZIONE LIMITI (IDENTICA A on_new_specimen) ---
                validation_passed = True
                error_message = ""
                if stop_base_unit == "mm":
                    limit_to_check = self.main_window.current_disp_limit_mm
                    target_absolute_disp = stop_converted + self.displacement_offset_mm
                    if abs(target_absolute_disp) > limit_to_check:
                        validation_passed = False
                        error_message = (f"The calculated absolute stop displacement ({target_absolute_disp:.2f} mm) "
                                        f"exceeds the machine limit of ±{limit_to_check:.2f} mm.")
                elif stop_base_unit == "N":
                    limit_to_check = self.main_window.current_force_limit_N
                    target_absolute_force = stop_converted + self.load_offset_N
                    if target_absolute_force > limit_to_check:
                        validation_passed = False
                        error_message = (f"The calculated absolute stop force ({target_absolute_force:.2f} N) "
                                        f"exceeds the machine limit of {limit_to_check:.2f} N.")

                if not validation_passed:
                    QMessageBox.warning(self, "Limit Exceeded", error_message)
                    return # Non modificare il provino

                modified_data.update({
                    "speed": speed_value,
                    "speed_unit": speed_unit,
                    "speed_mm_s": speed_mm_s,
                    "stop_criterion_value": stop_value,
                    "stop_criterion_unit": stop_unit,
                    "stop_criterion_converted": stop_converted,
                    "stop_criterion_base_unit": stop_base_unit,
                })
            else:
                # mantieni i valori originali ma ricalcola se dipendono da gauge/area
                speed_value = original["speed"]
                speed_unit = original["speed_unit"]
                speed_mm_s = self.convert_speed(speed_value, speed_unit, new_gauge_length)

                stop_value = original["stop_criterion_value"]
                stop_unit = original["stop_criterion_unit"]
                stop_converted, stop_base_unit = self.convert_stop_criterion(stop_value, stop_unit, new_gauge_length, new_area)

                modified_data.update({
                    "speed": speed_value,
                    "speed_unit": speed_unit,
                    "speed_mm_s": speed_mm_s,
                    "stop_criterion_value": stop_value,
                    "stop_criterion_unit": stop_unit,
                    "stop_criterion_converted": stop_converted,
                    "stop_criterion_base_unit": stop_base_unit,
                })

                # aggiorna GUI se dipende da gauge/area
                if speed_unit in ["%/s", "%/min"]:
                    self.speed_spinbox.setValue(speed_value)
                    self.speed_unit_combo.setCurrentText(speed_unit)
                if stop_unit in ["Strain (%)", "Stress (MPa)"]:
                    self.stop_criterion_spinbox.setValue(stop_value)
                    self.stop_criterion_combo.setCurrentText(stop_unit)

            if new_gauge_length <= 0 or new_area <= 0:
                raise ValueError("Gauge length and area must be positive.")

            if new_name != name_to_modify:
                if new_name in self.specimens:
                    QMessageBox.warning(self, "Input Error", f"Specimen with name '{new_name}' already exists.")
                    return
                del self.specimens[name_to_modify]
                selected_item.setText(new_name)

            self.specimens[new_name] = modified_data
            QMessageBox.information(self, "Success", f"Specimen '{new_name}' updated successfully.")
            self.refresh_plot()
        except (ValueError, TypeError):
            QMessageBox.warning(self, "Input Error", "Invalid or incomplete numeric input for modification.")
            return

        # Aggiorna overlay e grafico
        for i in range(self.overlay_list.count()):
            item = self.overlay_list.item(i)
            if item.text() == name_to_modify:
                item.setText(new_name)
                break
        self.refresh_plot()



    def on_specimen_selected(self, item):
        name = item.text()
        self.current_specimen_name = name
        data = self.specimens[name]
        self.name_edit.setText(data["name"])
        self.gauge_length_edit.setValue(float(data["gauge_length"]))
        self.area_edit.setValue(float(data["area"]))
        self.speed_spinbox.setValue(data["speed"])
        self.speed_unit_combo.setCurrentText(data["speed_unit"])
        self.stop_criterion_spinbox.setValue(data["stop_criterion_value"])
        self.stop_criterion_combo.setCurrentText(data["stop_criterion_unit"])
        self.return_to_start_checkbox.setChecked(data["return_to_start"])
        # blocca speed/criterion se già testato
        already_tested = bool(data.get("test_data"))
        self.speed_spinbox.setEnabled(not already_tested)
        self.speed_unit_combo.setEnabled(not already_tested)
        self.stop_criterion_spinbox.setEnabled(not already_tested)
        self.stop_criterion_combo.setEnabled(not already_tested)

        
        test_data = data.get("test_data")
        if test_data:
            x_data = [p[0] for p in test_data]
            y_data = [p[1] for p in test_data]
            self.plot_curve.setData(x_data, y_data)
        else:
            self.plot_curve.clear() 
        # Aggiorna il grafico in base al provino selezionato e all'overlay
        self.refresh_plot()      

    def send_command(self, command):
        self.communicator.send_command(command)

    def zero_relative_load(self): 
        self.load_offset_N = self.absolute_load_N
        self.update_displays()

    def zero_relative_displacement(self): 
        self.displacement_offset_mm = self.absolute_displacement_mm
        self.update_displays()

    def update_stop_criterion_options(self):
        try:
            is_area_valid = self.area_edit.value() > 0
        except ValueError:
            is_area_valid = False
        stress_item_index = self.stop_criterion_combo.findText("Stress (MPa)")
        if stress_item_index != -1:
            item = self.stop_criterion_combo.model().item(stress_item_index)
            item.setEnabled(is_area_valid)
            if not is_area_valid and self.stop_criterion_combo.currentText() == "Stress (MPa)":
                self.stop_criterion_combo.setCurrentIndex(0)

# File: monotonic_test_widget.py
# SOSTITUISCI l'intera funzione refresh_plot

    def refresh_plot(self):
        # --- 1. Pulisci il grafico ESISTENTE ---
        plot_item = self.plot_widget.getPlotItem()
        main_viewbox = plot_item.getViewBox()

        # Rimuovi eventuali assi/viewbox secondari aggiunti in precedenza
        if self.resistance_axis_viewbox:
            try:
                try:
                    main_viewbox.sigResized.disconnect(self._update_resistance_views)
                    main_viewbox.sigXRangeChanged.disconnect(self._update_resistance_views)
                except (TypeError, RuntimeError):
                    pass # Ignora se non erano connessi
                
                # Rimuovi la ViewBox dalla scena
                plot_item.scene().removeItem(self.resistance_axis_viewbox)
                self.resistance_axis_viewbox = None
                # Scollega l'asse destro (non rimuoverlo)
                plot_item.getAxis('right').linkToView(None)
                # Nascondi l'asse destro
                plot_item.showAxis('right', False)
                self.resistance_curve = None # Azzera riferimento
                
                legend = plot_item.legend
                if legend: legend.removeItem("Resistance") # Rimuovi dalla legenda
            except Exception as e:
                print(f"Errore rimozione asse/viewbox secondario (Mono): {e}")

        # Pulisci le curve principali
        self.plot_widget.clear() 
        self.plot_widget.addLegend() # Ri-aggiungi la legenda pulita
        self.plot_curves = {}        # Azzera dizionario curve salvate

        # --- 2. Imposta Assi Principali ---
        x_mode = self.x_axis_combo.currentText()
        y_mode = self.y_axis_combo.currentText()
        plot_item.setLabel("bottom", x_mode)
        plot_item.setLabel("left", y_mode)
        
        # --- 3. Sotto-funzione convert_data (è corretta, resta invariata) ---
        def convert_data(specimen, raw_data):
            area = specimen.get("area", 1.0)
            gauge = specimen.get("gauge_length", 1.0)
            if not raw_data: return [], [], []
            try:
                x_raw = [p[1] for p in raw_data] # rel_disp
                y_raw = [p[2] for p in raw_data] # rel_load
                r_raw = [p[5] for p in raw_data] # resistance
            except (IndexError, TypeError) as e:
                print(f"Errore estrazione dati in convert_data (monotonico): {e}")
                return [], [], []
            if "Strain" in x_mode and gauge > 0: x = [(d / gauge) * 100 for d in x_raw]
            else: x = x_raw 
            if "Stress" in y_mode and area > 0: y = [(f / area) for f in y_raw]
            else: y = y_raw 
            r_data = [r if r >= 0 else np.nan for r in r_raw]
            return x, y, r_data

        # --- 4. Logica Overlay/Disegno Curve Principali (resta invariata) ---
        show_overlay = self.overlay_checkbox.isChecked()
        if show_overlay:
            for name, specimen in self.specimens.items():
                if specimen.get("test_data") and specimen.get("visible", True):
                    try:
                        x, y, _ = convert_data(specimen, specimen["test_data"])
                        curve = self.plot_widget.plot(x, y, pen=self.get_pen_for_specimen(name), name=name)
                        self.plot_curves[name] = curve
                    except Exception as e: print(f"Errore disegno overlay {name} (Mono): {e}")
        else:
            if self.current_specimen_name:
                specimen = self.specimens.get(self.current_specimen_name)
                if specimen and specimen.get("test_data"):
                    try:
                        x, y, _ = convert_data(specimen, specimen["test_data"])
                        curve = self.plot_widget.plot(x, y, pen=self.get_pen_for_specimen(self.current_specimen_name), name=self.current_specimen_name)
                        self.plot_curves[self.current_specimen_name] = curve
                    except Exception as e: print(f"Errore disegno non-overlay {self.current_specimen_name} (Mono): {e}")

        # --- 5. Logica Secondo Asse Y (Resistenza) ---
        lcr_enabled = self.lcr_enable_checkbox.isChecked()

        if lcr_enabled:
            try:
                # 1. CREA E MOSTRA L'ASSE DESTRO (gestito dal layout)
                plot_item.showAxis('right')
                
                # 2. Crea la ViewBox secondaria
                self.resistance_axis_viewbox = pg.ViewBox()
                self.resistance_axis_viewbox.setZValue(10)

                # 3. COLLEGA l'asse alla ViewBox
                plot_item.getAxis('right').linkToView(self.resistance_axis_viewbox)
                plot_item.getAxis('right').setLabel('Resistance', units='Ω')

                # 4. Aggiungi la ViewBox alla SCENA
                plot_item.scene().addItem(self.resistance_axis_viewbox)

                # 5. Linka l'asse X (scorrimento e zoom)
                self.resistance_axis_viewbox.linkView(pg.ViewBox.XAxis, main_viewbox)

                # 6. Crea la curva per la resistenza
                resistance_pen = pg.mkPen('orange', width=2, style=Qt.PenStyle.DotLine)
                # Questa self.resistance_curve sarà usata per il LIVE STREAMING
                self.resistance_curve = pg.PlotDataItem(pen=resistance_pen, name="Resistance")
                
                # 7. Aggiungi la curva alla ViewBox secondaria
                self.resistance_axis_viewbox.addItem(self.resistance_curve)

                # 8. Sincronizza le viste (fondamentale)
                main_viewbox.sigResized.connect(self._update_resistance_views)
                main_viewbox.sigXRangeChanged.connect(self._update_resistance_views)
                self._update_resistance_views() # Chiama subito

                # 9. Disegna i dati di resistenza ESISTENTI (Polling)
                if show_overlay:
                     for name, specimen in self.specimens.items():
                        if specimen.get("test_data") and specimen.get("visible", True):
                            try:
                                x, _, r_data = convert_data(specimen, specimen["test_data"])
                                # Crea una NUOVA curva (NON self.resistance_curve)
                                overlay_res_curve = pg.PlotDataItem(pen=pg.mkPen('orange', width=1, style=Qt.PenStyle.DotLine))
                                self.resistance_axis_viewbox.addItem(overlay_res_curve)
                                overlay_res_curve.setData(x, r_data)
                            except Exception as e: print(f"Errore disegno overlay resistenza {name} (Mono): {e}")
                else: # Non overlay
                    if self.current_specimen_name:
                        specimen = self.specimens.get(self.current_specimen_name)
                        if specimen and specimen.get("test_data"):
                            try:
                                x, _, r_data = convert_data(specimen, specimen["test_data"])
                                # Usa la curva self.resistance_curve per i dati salvati
                                self.resistance_curve.setData(x, r_data)
                            except Exception as e: print(f"Errore disegno non-overlay resistenza {self.current_specimen_name} (Mono): {e}")
            
            except Exception as e:
                print(f"Errore creazione asse resistenza (Mono - refresh_plot): {e}")

        # --- 6. Grafico Live (Questa sezione è confusa e la semplifichiamo) ---
        # La logica di aggiornamento live è gestita da handle_stream_data.
        # Dobbiamo solo assicurare che le curve esistano se il test è in corso.
        
        if self.is_test_running and self.current_test_data:
            try:
                specimen = self.specimens.get(self.current_specimen_name, {"gauge_length": 1.0, "area": 1.0})
                x_live, y_live, r_live = convert_data(specimen, self.current_test_data)
                
                # Aggiorna curva live principale
                main_live_curve = self.plot_curves.get(self.current_specimen_name)
                if not main_live_curve: # Se non esiste (strano, ma per sicurezza)
                     main_live_curve = self.plot_widget.plot([], [], pen=self.get_pen_for_specimen(self.current_specimen_name), name=self.current_specimen_name)
                     self.plot_curves[self.current_specimen_name] = main_live_curve
                main_live_curve.setData(x_live, y_live)
                 
                # Aggiorna curva live resistenza (SOLO SE ESISTE)
                if self.resistance_curve:
                    self.resistance_curve.setData(x_live, r_live)
            except Exception as e:
                print(f"Errore aggiornamento dati live (Mono - refresh_plot): {e}")

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
            return (gauge_length / 100.0) * value
        elif unit == "%/min":
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
            displacement = (gauge_length * value) / 100.0
            return displacement, "mm"
        elif unit == "Stress (MPa)":
            force = value * area  # area in mm², stress in MPa = N/mm²
            return force, "N"
        else:
            return value, unit



    def on_finish_and_save(self):
        if not self.specimens:
            QMessageBox.information(self, "Info", "Nessun provino da salvare.")
            return

        # Propone un nome di file di default
        default_filename = f"Batch_{datetime.now().strftime('%Y-%m-%d_%H%M')}.xlsx"

        # Apre la finestra di dialogo per il salvataggio
        filepath, _ = QFileDialog.getSaveFileName(self, "Salva Batch di Test", default_filename, "Excel Files (*.xlsx)")

        if filepath:
            saver = DataSaver()
            success, message = saver.save_batch_to_xlsx(self.specimens, filepath, self.active_calibration_info)
            if success:
                QMessageBox.information(self, "Successo", message)
            else:
                QMessageBox.critical(self, "Errore", message)


    def _on_lcr_checkbox_changed(self, state):
        """ Invia il comando appropriato all'ESP32 quando il checkbox cambia stato. """
        if state == Qt.CheckState.Checked.value:
            print("DEBUG GUI (Mono): Abilitazione LCR Polling")
            self.send_command("ENABLE_LCR_POLLING") # Usa self.send_command
        else:
            print("DEBUG GUI (Mono): Disabilitazione LCR Polling")
            self.send_command("DISABLE_LCR_POLLING") # Usa self.send_command
            # Resetta subito il display a "N/A" o "--"
            self.current_resistance_ohm = -999.0
        self.refresh_plot()
        self.update_displays() # Aggiorna per mostrare il reset



    def _update_resistance_views(self):
        """ Funzione helper per sincronizzare i ViewBox principale e secondario. """
        main_viewbox = self.plot_widget.getViewBox()
        if self.resistance_axis_viewbox and main_viewbox:
            try:
                # Questo codice è lo stesso che era dentro 'update_views'
                self.resistance_axis_viewbox.setGeometry(main_viewbox.sceneBoundingRect())
                self.resistance_axis_viewbox.linkedViewChanged(main_viewbox, pg.ViewBox.XAxis)
                self.resistance_axis_viewbox.enableAutoRange(axis=pg.ViewBox.YAxis)
            except Exception as e:
                # Può fallire in modo innocuo se un oggetto è in fase di distruzione
                pass