# data_saver.py

import openpyxl
from openpyxl.chart import ScatterChart, Reference, Series
from openpyxl.drawing.line import LineProperties
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.axis import ChartLines
from openpyxl.chart import Series
from datetime import datetime

class DataSaver:
    """
    Classe dedicata al salvataggio dei dati dei test in un file Excel (.xlsx).
    """

    def save_batch_to_xlsx(self, specimens_dict, filepath, calibration_info="N/A"):
        """
        Salva un batch di provini in un singolo file Excel, un provino per foglio.
        """
        try:
            workbook = openpyxl.Workbook()
            # Rimuovi il foglio di default creato automaticamente
            workbook.remove(workbook.active) 

            for specimen_name, specimen_data in specimens_dict.items():
                if specimen_data.get("test_data"): # Salva solo se ci sono dati di test
                    self._create_sheet_for_specimen(workbook, specimen_name, specimen_data, calibration_info)
            
            workbook.save(filepath)
            return True, f"Dati salvati con successo in {filepath}"
        except Exception as e:
            return False, f"Errore durante il salvataggio del file: {e}"

    def _create_sheet_for_specimen(self, workbook, specimen_name, specimen_data, calibration_info):
        """
        Metodo privato per creare e popolare un singolo foglio di lavoro.
        """
        # Crea un nome di foglio valido (max 31 caratteri, senza caratteri speciali)
        safe_sheet_name = "".join(c for c in specimen_name if c.isalnum() or c in " _-").strip()[:31]
        sheet = workbook.create_sheet(title=safe_sheet_name)

        # --- Scrittura Parametri di Setup ---
        sheet["A1"] = "Test Parameters"
        sheet["A1"].font = openpyxl.styles.Font(bold=True)
        
        params = {
            "Specimen Name": specimen_name,
            "Test Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Calibration Info": calibration_info, 
            "Gauge Length (mm)": specimen_data.get("gauge_length"),
            "Area (mm²)": specimen_data.get("area"),
            "Test Speed": f"{specimen_data.get('speed')} {specimen_data.get('speed_unit')}",
            "Stop Criterion": f"{specimen_data.get('stop_criterion_value')} {specimen_data.get('stop_criterion_unit')}"
        }

        row = 2
        for key, value in params.items():
            sheet[f"A{row}"] = key
            sheet[f"B{row}"] = value
            row += 1
        
        # --- Preparazione e Scrittura Colonne Dati ---
        header_row = row + 1
        data_start_row = header_row + 1

        headers = [
            "Relative Displacement (mm)", "Relative Load (N)",
            "Strain (%)", "Stress (MPa)",
            "Absolute Displacement (mm)", "Absolute Load (N)"
        ]
        sheet.append(headers)

        test_data = specimen_data.get("test_data", [])
        area = specimen_data.get("area", 1.0)
        gauge = specimen_data.get("gauge_length", 1.0)
        
        # Le colonne A e B conterranno sempre RelDisp e RelLoad, che sono i dati grezzi
        for i, (rel_disp, rel_load, abs_disp, abs_load) in enumerate(test_data):
            # Calcola le altre colonne derivate
            strain = (rel_disp / gauge) * 100 if gauge > 0 else 0
            stress = rel_load / area if area > 0 else 0
            # Nota: i dati assoluti non sono salvati per punto, questa è un'approssimazione
            # Se ti servono, dovrai modificare la struttura dati di `current_test_data`
            
            sheet.cell(row=data_start_row + i, column=1, value=rel_disp)
            sheet.cell(row=data_start_row + i, column=2, value=rel_load)
            sheet.cell(row=data_start_row + i, column=3, value=strain)
            sheet.cell(row=data_start_row + i, column=4, value=stress)
            sheet.cell(row=data_start_row + i, column=5, value=abs_disp)
            sheet.cell(row=data_start_row + i, column=6, value=abs_load)

        num_data_points = len(test_data)
        if num_data_points == 0:
            return # Non creare grafici se non ci sono dati
        
        # --- Creazione Grafico 1: Load vs Displacement ---
        chart1 = ScatterChart()

        chart1.title = "Load vs. Displacement"
        chart1.x_axis.title = "Relative Displacement (mm)"
        chart1.y_axis.title = "Relative Load (N)"
        chart1.x_axis.delete = False
        chart1.y_axis.delete = False
        chart1.y_axis.majorGridlines = ChartLines()
        chart1.x_axis.majorGridlines = None 
        chart1.x_axis.titleOverlay = False
        chart1.y_axis.titleOverlay = False
        
        x_data_ref = Reference(sheet, min_col=1, min_row=header_row + 2, max_row=header_row + num_data_points)
        y_data_ref = Reference(sheet, min_col=2, min_row=header_row + 2, max_row=header_row + num_data_points)

        series1 = Series(y_data_ref, xvalues=x_data_ref, title="Test Data")
        line_props1 = LineProperties(solidFill="4F81BD", w=38100) # w=38100 è circa 3pt
        series1.graphicalProperties = GraphicalProperties(ln=line_props1)
        chart1.series.append(series1)
        chart1.legend = None
        line_props = LineProperties(solidFill="000000") # Linea nera

        # Controlla e imposta le proprietà per l'asse X
        if chart1.x_axis.graphicalProperties is None:
            chart1.x_axis.graphicalProperties = GraphicalProperties()    
        chart1.x_axis.graphicalProperties.ln = line_props
 
        # Controlla e imposta le proprietà per l'asse Y
        if chart1.y_axis.graphicalProperties is None:
            chart1.y_axis.graphicalProperties = GraphicalProperties()
        chart1.y_axis.graphicalProperties.ln = line_props

        sheet.add_chart(chart1, "H2") # Posiziona il grafico nella cella H2

        # --- Creazione Grafico 2: Stress vs Strain ---
        chart2 = ScatterChart()
        chart2.legend = None
        chart2.title = "Stress vs. Strain"
        chart2.x_axis.title = "Strain (%)"
        chart2.y_axis.title = "Stress (MPa)"
        chart2.x_axis.delete = False
        chart2.y_axis.delete = False
        chart2.y_axis.majorGridlines = ChartLines()
        chart2.x_axis.majorGridlines = None 
        chart2.x_axis.titleOverlay = False
        chart2.y_axis.titleOverlay = False

        # Controlla e imposta le proprietà per l'asse X
        if chart2.x_axis.graphicalProperties is None:
            chart2.x_axis.graphicalProperties = GraphicalProperties()    
        chart2.x_axis.graphicalProperties.ln = line_props
 
        # Controlla e imposta le proprietà per l'asse Y
        if chart2.y_axis.graphicalProperties is None:
            chart2.y_axis.graphicalProperties = GraphicalProperties()
        chart2.y_axis.graphicalProperties.ln = line_props

        
        x_data_ref2 = Reference(sheet, min_col=3, min_row=header_row + 2, max_row=header_row + num_data_points)
        y_data_ref2 = Reference(sheet, min_col=4, min_row=header_row + 2, max_row=header_row + num_data_points)
        series2 = Series(y_data_ref2, xvalues=x_data_ref2, title="Test Data")
        series2.graphicalProperties = GraphicalProperties(ln=line_props1)
        chart2.series.append(series2)
        chart2.legend = None
        sheet.add_chart(chart2, "H18") # Posiziona il grafico nella cella H18