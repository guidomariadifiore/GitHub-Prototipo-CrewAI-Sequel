#!/usr/bin/env python
import os
import subprocess
import time
import requests
from dotenv import load_dotenv
from typing import Optional, List
from pydantic import BaseModel

# Carica subito le variabili d'ambiente
load_dotenv()

from crewai.flow.flow import Flow, listen, start, router

# Assicurati che questi import siano corretti rispetto alla tua struttura cartelle
from refactor_crew import RefactorCrew
from constants import DIRECTORY_REPOS


# Definiamo lo stato del flusso
class RefactoringState(BaseModel):
    project_key: str = ""
    file_path: str = ""
    file_content: str = ""
    error_log: str = ""
    refactoring_valid: bool = False
    iteration: int = 0


class RefactoringFlow(Flow[RefactoringState]):

    @start()
    def select_project(self):
        """
        Step 1: Seleziona il progetto target e prepara lo stato.
        """
        print("\n--- STEP 1: Selezione Progetto Target ---")

        # Progetto target
        project_key = "scacchi-usofuori"

        # Setup dello stato
        self.state.project_key = project_key
        self.state.iteration = 1
        
        # Resettiamo i valori legati al file, non più usati in questa fase
        self.state.file_path = ""
        self.state.file_content = ""

        print(f"Project Key: {self.state.project_key}")
        return True

    @listen(select_project)  # <--- MODIFICA CHIAVE: Ascolta la selezione del progetto
    def run_analysis_and_find_hotspots(self):
        """
        Step 2: Esegue scansione SonarQube, trova i 10 file con più issue usando i facets.
        """
        print(f"\n--- STEP 2: Analisi Hotspots con Facets ---")

        sonar_token = os.getenv("SONAR_TOKEN")
        if not sonar_token:
            print(
                "⚠️ WARNING: SONAR_TOKEN non trovato. Assicurati che sia nel file .env"
            )
            self.state.error_log = "SONAR_TOKEN non configurato."
            return False

        project_dir = os.path.join(DIRECTORY_REPOS, self.state.project_key)

        # Usiamo mvn semplice perché sei su Git Bash
        mvn_executable = "mvn"

        # Comando completo per la scansione
        cmd = [
            mvn_executable,
            "clean",
            "verify",
            "org.sonarsource.scanner.maven:sonar-maven-plugin:sonar",
            f"-Dsonar.projectKey={self.state.project_key}",
            f"-Dsonar.projectName={self.state.project_key}",
            "-Dsonar.host.url=http://localhost:9000",
            f"-Dsonar.token={sonar_token}",
            "-Dmaven.test.failure.ignore=true",
        ]
        cmd_str = " ".join(cmd)

        try:
            print(f"Avvio scansione su: {project_dir}")
            print("Comando: mvn clean verify sonar...")
            
            subprocess.run(
                cmd_str,
                cwd=project_dir,
                check=True,
                capture_output=True,
                text=True,
                shell=True,
            )

            print("✅ Analisi Maven completata. Attesa server SonarQube...")
            time.sleep(10)

            # --- RECUPERO HOTSPOTS CON FACETS ---
            print("Recupero hotspots con facets per l'intero progetto...")
            api_url = "http://localhost:9000/api/issues/search"

            params = {
                "componentKeys": self.state.project_key,
                "facets": "files",  # Chiediamo a SonarQube di aggregare per file
                "resolved": "false",
                "ps": 1,  # Non ci interessano le singole issues, solo l'aggregato
            }

            response = requests.get(api_url, auth=(sonar_token, ""), params=params)

            if response.status_code != 200:
                error_message = f"❌ Errore API Sonar: {response.status_code} - {response.text}"
                print(error_message)
                self.state.error_log = error_message
                return False

            data = response.json()
            
            # La risposta con facets ha una struttura diversa
            file_facet = next((f for f in data.get('facets', []) if f['property'] == 'files'), None)

            if not file_facet or not file_facet.get('values'):
                msg = "✅ Nessun file con issue trovato tramite facets."
                print(msg)
                self.state.error_log = msg
                return True
            
            # L'API di SonarQube di solito restituisce i valori ordinati per 'count' decrescente.
            # Prendiamo i primi 10.
            top_10_files = file_facet['values'][:10]

            report = "Top 10 file con più errori energetici (via Facets):\n\n"
            for i, item in enumerate(top_10_files):
                # 'val' contiene il component key del file
                file_path = item['val'].split(':', 1)[-1]
                count = item['count']
                report += f"{i+1}. {file_path}  ({count} issues)\n"
            
            print("\n--- RISULTATI ANALISI HOTSPOTS ---")
            print(report)
            self.state.error_log = report

        except subprocess.CalledProcessError as e:
            error_details = (e.stdout or "") + "\n" + (e.stderr or "")
            print("❌ Errore durante l'esecuzione del comando Maven.")
            print(f"Dettagli errore:\n{error_details[-1000:]}")
            self.state.error_log = "Errore build iniziale."
        except Exception as e:
            print(f"❌ Errore generico: {e}")
            self.state.error_log = f"Errore: {str(e)}"

        return True

'''    @listen(
        run_initial_analysis
    )  # <--- MODIFICA CHIAVE: Concatena al metodo precedente
    def run_refactoring_crew(self):
        """
        Step 3: Lancia la Crew.
        """
        print(f"\n--- STEP 3: Avvio Crew (Refactoring) ---")

        inputs = {
            "code_class": self.state.file_content,
            "path_class": self.state.file_path,
            "project_key": self.state.project_key,
            "errors": self.state.error_log,
        }

        # Lancia la crew
        result = RefactorCrew().crew().kickoff(inputs=inputs)
        print("\n--- Refactoring Completato ---")
        return True

    @listen(run_refactoring_crew)  # <--- MODIFICA CHIAVE
    def finish(self):
        print("\n--- STEP 4: Fine Flusso ---")
        print(f"File processato: {self.state.file_path}")
'''

def kickoff():
    refactoring_flow = RefactoringFlow()
    refactoring_flow.kickoff()


if __name__ == "__main__":
    kickoff()
