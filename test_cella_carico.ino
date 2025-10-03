// --- Sketch di Test per Cella di Carico e HX711 ---

#include "HX711.h"

// 1. Definisci i pin a cui hai collegato l'HX711
const int LOADCELL_DOUT_PIN = 32;
const int LOADCELL_SCK_PIN = 33;

// 2. Inserisci il tuo fattore di calibrazione
//    Usa quello ottenuto dallo sketch di calibrazione.
//    Se non lo hai ancora, inizia con un valore a caso come -430.0 e poi affinalo.
float calibration_factor = -430.0; // <-- VALORE DA MODIFICARE!

// Crea l'oggetto per comunicare con la cella
HX711 scale;

void setup() {
  Serial.begin(115200); // Avvia la comunicazione con il PC
  Serial.println("--- Test Cella di Carico con HX711 ---");

  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.set_scale(calibration_factor);

  Serial.println("Eseguo la tara... Non toccare la cella!");
  scale.tare(); // Azzera la bilancia. Questa operazione richiede qualche secondo.
  Serial.println("Tara completata. Ora puoi applicare un peso.");
}

void loop() {
  // Leggi il valore dalla cella (in grammi)
  // get_units(10) fa una media di 10 letture per un risultato più stabile
  float reading_grams = scale.get_units(10);
  
  // Stampa il valore sul Monitor Seriale
  Serial.print("Lettura: ");
  Serial.print(reading_grams, 2); // Stampa con 2 cifre decimali
  Serial.println(" g");
  
  delay(500); // Attendi mezzo secondo tra una lettura e l'altra
}