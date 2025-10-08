import sys
from PyQt6.QtWidgets import QApplication, QPushButton, QMessageBox

# Funzione che crea il popup esattamente come abbiamo provato a fare
def show_test_popup():
    print("Tentativo di mostrare il popup...")
    try:
        msg_box = QMessageBox() # Non specifichiamo un genitore per il test
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Test di Stabilità")
        msg_box.setText("Se vedi questo testo, PyQt6 funziona correttamente.")
        msg_box.exec()
        print("Popup mostrato e chiuso con successo.")
    except Exception as e:
        print(f"Errore durante la creazione del popup: {e}")

# Creiamo un'applicazione minima con un solo pulsante
app = QApplication(sys.argv)
main_window = QPushButton("Clicca per Testare il Popup")
main_window.setMinimumSize(300, 100)
main_window.clicked.connect(show_test_popup)
main_window.show()

print("Applicazione di test avviata. Clicca il pulsante.")
sys.exit(app.exec())