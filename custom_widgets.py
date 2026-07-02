from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QComboBox, QMessageBox
from PyQt6.QtCore import Qt, QRectF, QLocale
from PyQt6.QtGui import QFont, QPainter, QColor

class SpeedBarWidget(QWidget):
    """
    Barra visuale per indicare il livello di velocità.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(250, 30)
        self._speed_percent = 0.0
        self._num_segments = 20

    def setValue(self, percent):
        self._speed_percent = max(0.0, min(100.0, percent))
        self.update()

    def get_gradient_color(self, percent_pos):
        g, y, r = QColor("#2ECC71"), QColor("#F1C40F"), QColor("#E74C3C")
        if percent_pos < 0.5:
            interp_factor = percent_pos / 0.5
            red = int(g.red() + interp_factor * (y.red() - g.red()))
            green = int(g.green() + interp_factor * (y.green() - g.green()))
            blue = int(g.blue() + interp_factor * (y.blue() - g.blue()))
        else:
            interp_factor = (percent_pos - 0.5) / 0.5
            red = int(y.red() + interp_factor * (r.red() - y.red()))
            green = int(y.green() + interp_factor * (r.green() - y.green()))
            blue = int(y.blue() + interp_factor * (r.blue() - y.blue()))
        return QColor(red, green, blue)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        num_segments_on = int((self._speed_percent / 100.0) * self._num_segments)
        segment_width = width / self._num_segments
        spacing = segment_width * 0.15
        for i in range(self._num_segments):
            rect = QRectF(i * segment_width + spacing / 2, 0, segment_width - spacing, height)
            if i < num_segments_on:
                percent_pos = (i + 1) / self._num_segments
                color = self.get_gradient_color(percent_pos)
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                painter.setBrush(QColor("white"))
                painter.setPen(QColor("#BBBBBB"))
            painter.drawRect(rect)

class DisplayWidget(QWidget):
    """
    Un display singolo composto da un'etichetta e un valore.
    """
    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Segoe UI", 12))
        self.value_label = QLabel("--")
        display_font = QFont("Consolas", 24, QFont.Weight.Bold)
        self.value_label.setFont(display_font)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setMinimumSize(180, 50)
        self.value_label.setFrameShape(QFrame.Shape.Panel)
        self.value_label.setFrameShadow(QFrame.Shadow.Sunken)
        self.value_label.setLineWidth(2)
        self.value_label.setStyleSheet("background-color: #E8E8E8; color: #2C3E50; border-radius: 5px; padding: 5px;")
        layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.value_label)

    def set_value(self, text):
        self.value_label.setText(text)

class LimitsDialog(QDialog):
    """
    Finestra di dialogo per impostare i limiti massimi di forza e spostamento della macchina.
    """
    def __init__(self, current_force_N, current_disp_mm, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Imposta Limiti Macchina Assoluti")
        self.setMinimumWidth(400)

        # Usa la localizzazione "C" per avere il punto come separatore decimale
        locale_c = QLocale("C")

        layout = QFormLayout(self)

        self.force_limit_spinbox = QDoubleSpinBox()
        self.force_limit_spinbox.setLocale(locale_c)
        self.force_limit_spinbox.setSuffix(" N")
        self.force_limit_spinbox.setDecimals(3)
        self.force_limit_spinbox.setRange(0.1, 5000.0) # Imposta un range ragionevole
        self.force_limit_spinbox.setValue(current_force_N)

        self.disp_limit_spinbox = QDoubleSpinBox()
        self.disp_limit_spinbox.setLocale(locale_c)
        self.disp_limit_spinbox.setSuffix(" mm")
        self.disp_limit_spinbox.setDecimals(4)
        self.disp_limit_spinbox.setRange(1.0, 190.0) # Limite fisico della macchina
        self.disp_limit_spinbox.setValue(current_disp_mm) # Valore di default

        layout.addRow("Forza Massima Assoluta:", self.force_limit_spinbox)
        layout.addRow("Spostamento Massimo Assoluto:", self.disp_limit_spinbox)

        # Pulsanti standard (OK/Cancel)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_values(self):
        """ Ritorna i valori impostati dall'utente. """
        return self.force_limit_spinbox.value(), self.disp_limit_spinbox.value()

class FilterConfigDialog(QDialog):
    """
    Finestra di dialogo per configurare il filtro EMA e il guadagno PGA
    della cella di carico (NAU7802): alpha, sample rate e gain.
    """
    def __init__(self, current_alpha, current_rate_sps, current_gain, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurazione Filtro Cella di Carico")
        self.setMinimumWidth(400)

        locale_c = QLocale("C")
        layout = QFormLayout(self)

        self.alpha_spinbox = QDoubleSpinBox()
        self.alpha_spinbox.setLocale(locale_c)
        self.alpha_spinbox.setDecimals(2)
        self.alpha_spinbox.setRange(0.01, 1.00)
        self.alpha_spinbox.setSingleStep(0.01)
        self.alpha_spinbox.setValue(current_alpha)

        self.rate_combo = QComboBox()
        self.rate_combo.addItems(["10 SPS", "20 SPS", "40 SPS", "80 SPS", "320 SPS"])
        current_rate_text = f"{current_rate_sps} SPS"
        index = self.rate_combo.findText(current_rate_text)
        self.rate_combo.setCurrentIndex(index if index != -1 else self.rate_combo.count() - 1)

        self.gain_combo = QComboBox()
        self.gain_combo.addItems(["1x", "2x", "4x", "8x", "16x", "32x", "64x", "128x"])
        current_gain_text = f"{current_gain}x"
        gain_index = self.gain_combo.findText(current_gain_text)
        self.gain_combo.setCurrentIndex(gain_index if gain_index != -1 else 0)

        layout.addRow("Alpha (EMA):", self.alpha_spinbox)
        layout.addRow("Sample Rate NAU7802:", self.rate_combo)
        layout.addRow("Guadagno PGA:", self.gain_combo)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_values(self):
        """ Ritorna (alpha: float, rate_sps: int, gain: int). """
        rate_sps = int(self.rate_combo.currentText().split()[0])
        gain = int(self.gain_combo.currentText().rstrip('x'))
        return self.alpha_spinbox.value(), rate_sps, gain