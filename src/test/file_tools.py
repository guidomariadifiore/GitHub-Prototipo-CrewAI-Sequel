from crewai_tools import BaseTool
from pydantic import BaseModel, Field
import os

# Definizione degli input per il tool
class FileUpdateInput(BaseModel):
    file_path: str = Field(..., description="Il percorso assoluto o relativo del file locale da sovrascrivere.")
    new_code: str = Field(..., description="Il codice Java completo e rifattorizzato da scrivere nel file.")

class FileUpdateTool(BaseTool):
    name: str = "Overwrite File Tool"
    description: str = (
        "Questo strumento serve per sovrascrivere il contenuto di un file esistente "
        "con nuovo codice. DEVE essere usato per salvare il refactoring. "
        "Richiede il percorso esatto del file e il contenuto completo."
    )
    args_schema: type[BaseModel] = FileUpdateInput

    def _run(self, file_path: str, new_code: str) -> str:
        try:
            # Verifica base del percorso (opzionale, per sicurezza)
            if not os.path.exists(file_path):
                return f"Errore: Il file nel percorso {file_path} non esiste."

            # Scrittura del file con codifica UTF-8 come specificato nella tesi 
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(new_code)
            
            return f"Successo: Il file {file_path} è stato aggiornato correttamente."
        
        except Exception as e:
            return f"Errore durante la scrittura del file: {str(e)}"