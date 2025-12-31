import os
import subprocess
import time
import requests
from typing import Type
from pydantic import BaseModel, Field
from crewai_tools import BaseTool

# --- 1. TOOL DI SOSTITUZIONE FILE (Per Task 3 - code_replacer) ---

class FileUpdateInput(BaseModel):
    file_path: str = Field(..., description="Il percorso assoluto del file locale da sovrascrivere.")
    new_code: str = Field(..., description="Il codice Java completo e rifattorizzato.")

class FileUpdateTool(BaseTool):
    name: str = "File Update Tool"
    description: str = (
        "Sovrascrive il contenuto di un file con il nuovo codice fornito. "
        "Usa questo tool per salvare il refactoring fisico su disco."
    )
    args_schema: Type[BaseModel] = FileUpdateInput

    def _run(self, file_path: str, new_code: str) -> str:
        try:
            if not os.path.exists(file_path):
                return f"Errore: Il file {file_path} non esiste."

            # Scrittura con codifica UTF-8
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_code)
            
            return f"Successo: File {os.path.basename(file_path)} aggiornato correttamante."
        except Exception as e:
            return f"Errore critico durante la scrittura del file: {str(e)}"


# --- 2. TOOL DI SCANSONE SONAR (Per Task 4 - sonar_agent) ---

class SonarScanInput(BaseModel):
    project_key: str = Field(..., description="La chiave del progetto su SonarQube (es. 'my-java-project').")
    file_path: str = Field(..., description="Il percorso del file analizzato (per filtrare le issues specifiche).")

class SonarScanTool(BaseTool):
    name: str = "Sonar Scan Tool"
    description: str = (
        "Esegue la build Maven e lo scanner SonarQube. "
        "Restituisce 'Build Failure' se c'è un errore di compilazione, "
        "oppure la lista delle issues (energy smells) trovate se la scansione ha successo."
    )
    args_schema: Type[BaseModel] = SonarScanInput

    def _run(self, project_key: str, file_path: str) -> str:
        # 1. Configurazione Parametri (da variabili d'ambiente o hardcoded per test)
        sonar_url = os.getenv("SONAR_HOST_URL", "http://localhost:9000")
        project_dir = os.getcwd() # O la root del tuo progetto target
        
        # 2. Esecuzione Comandi Maven (Clean, Compile, Sonar)
        # Nota: -Dmaven.test.failure.ignore=true permette di continuare anche se i test falliscono
        mvn_command = (
            f"mvn clean verify sonar:sonar "
            f"-Dsonar.projectKey={project_key} "
            f"-Dsonar.host.url={sonar_url} "
            f"-Dsonar.login={sonar_token} "
            f"-Dmaven.test.failure.ignore=true"
        )

        try:
            # Eseguiamo il comando nella directory del progetto
            print(f"Avvio scansione Maven per {project_key}...")
            result = subprocess.run(
                mvn_command, 
                shell=True, 
                cwd=project_dir, 
                capture_output=True, 
                text=True
            )

            # 3. Controllo Errori di Compilazione
            if result.returncode != 0:
                # Se Maven fallisce, restituiamo l'errore (utile per errors_summarizer)
                # Catturiamo le ultime righe di log per capire l'errore
                error_log = result.stderr[-500:] if result.stderr else result.stdout[-500:]
                return f"BUILD FAILURE. Errori di compilazione rilevati:\n{error_log}"

            # 4. Fetch delle Issues da SonarQube (Solo se la build è ok)
            print("Build successo. Attendo elaborazione SonarQube...")
            time.sleep(5) # Attendiamo che SonarQube processi il report appena inviato

            # Cerchiamo le issues specifiche per il file appena modificato
            # Convertiamo il path assoluto in path relativo al progetto se necessario
            relative_path = os.path.relpath(file_path, project_dir).replace("\\", "/")
            
            api_url = f"{sonar_url}/api/issues/search"
            params = {
                "componentKeys": f"{project_key}:{relative_path}",
                "resolved": "false", # Vogliamo solo i problemi aperti
                "ps": 100 # Page size
            }
            
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            
            if response.status_code == 200:
                data = response.json()
                total_issues = data.get("total", 0)
                issues = data.get("issues", [])
                
                if total_issues == 0:
                    return "ANALISI COMPLETATA: Nessun energy smell o issue rilevato. Codice Ottimizzato!"
                
                # Creiamo un riassunto delle issues trovate
                report = f"ANALISI COMPLETATA: Trovate {total_issues} issues (Energy Smells/Bugs).\nDettagli:\n"
                for issue in issues:
                    msg = issue.get("message", "Nessun messaggio")
                    rule = issue.get("rule", "Regola sconosciuta")
                    line = issue.get("line", "?")
                    severity = issue.get("severity", "INFO")
                    report += f"- [Line {line}] {severity} ({rule}): {msg}\n"
                
                return report
            else:
                return f"Build OK, ma errore API SonarQube: {response.status_code} - {response.text}"

        except Exception as e:
            return f"Errore critico durante l'esecuzione del tool: {str(e)}"