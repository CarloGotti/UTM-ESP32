import serial
import serial.tools.list_ports
import time
from PyQt6.QtCore import QObject, pyqtSignal
from queue import Queue

class SerialCommunicator(QObject):
    data_received = pyqtSignal(str)
    port_error = pyqtSignal(str)
    connected = pyqtSignal()
    disconnected = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.serial_port = None
        self.is_running = True
        self.command_queue = Queue()

    def connect_to_port(self, port_name):
        try:
            # timeout=0 → lettura non bloccante
            self.serial_port = serial.Serial(port_name, 460800, timeout=0)
            self.serial_port.reset_input_buffer()
            self.connected.emit()
        except serial.SerialException as e:
            self.port_error.emit(f"Errore connessione: {e}")

    def disconnect_port(self):
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except:
                pass
        self.disconnected.emit()

    def stop(self):
        self.is_running = False
        self.disconnect_port()

    def send_command(self, command: str):
        """Accoda un comando da inviare all'ESP32"""
        self.command_queue.put(command)

    def run(self):
        buffer = bytearray()
        while self.is_running:
            # --- Invio comandi in coda ---
            if not self.command_queue.empty():
                command = self.command_queue.get()
                if self.serial_port and self.serial_port.is_open:
                    try:
                        #print(f"DEBUG COMM: scrivo {command}")
                        self.serial_port.write(f"{command}\n".encode("utf-8"))
                        self.serial_port.flush()
                    except serial.SerialException as e:
                        self.port_error.emit(f"Errore invio: {e}")

            # --- Lettura dati ---
            if self.serial_port and self.serial_port.is_open:
                try:
                    n = self.serial_port.in_waiting
                    if n:
                        data = self.serial_port.read(n)
                        buffer.extend(data)

                        # smonta in righe complete
                        while b"\n" in buffer:
                            line_bytes, buffer = buffer.split(b"\n", 1)
                            line_str = line_bytes.decode("utf-8", errors="ignore").strip()
                            if line_str:
                                self.data_received.emit(line_str)
                    else:
                        # piccolo sleep per non saturare la CPU
                        time.sleep(0.002)
                except (serial.SerialException, OSError):
                    self.port_error.emit("Dispositivo disconnesso.")
                    self.disconnect_port()
            else:
                time.sleep(0.01)

    @staticmethod
    def list_available_ports():
        return [port.device for port in serial.tools.list_ports.comports()]
    
    def send_emergency_stop(self):
            """Invia il carattere di stop immediato '!' fuori dalla coda normale."""
            if self.serial_port and self.serial_port.is_open:
                try:
                    self.serial_port.write(b"!\n")
                    self.serial_port.flush()
                except serial.SerialException as e:
                    self.port_error.emit(f"Errore invio emergency stop: {e}")
