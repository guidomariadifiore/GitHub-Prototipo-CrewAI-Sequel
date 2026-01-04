from crewai.tools import BaseTool
from pydantic import BaseModel, Field
import os

# Definizione degli input per il tool
class FileUpdateInput(BaseModel):
    file_path: str = Field(..., description="The absolute or relative path of the local file to overwrite.")
    new_code: str = Field(..., description="The complete refactored Java code to write into the file.")

class FileUpdateTool(BaseTool):
    name: str = "Overwrite File Tool"
    description: str = (
        "Use this tool to overwrite an existing file with new code. "
        "This tool MUST be used to save the refactoring results. "
        "Requires the absolute path of the file and the complete new content."
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