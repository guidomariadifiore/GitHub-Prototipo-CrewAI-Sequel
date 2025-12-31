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
    def select_target_file(self):
        """
        Step 1: Seleziona il file Java target e prepara lo stato.
        """
        print("\n--- STEP 1: Selezione File Target ---")
        
        # IL TUO FILE DI TEST
        # Assicurati che questo path relativo sia corretto dentro la cartella REPOS
        target_relative_path = "scacchi-usofuori/src/main/java/controller/ControllerLoadedGame.java"
        
        full_path = os.path.join(DIRECTORY_REPOS, target_relative_path)
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Il file {full_path} non esiste! Controlla DIRECTORY_REPOS in constants.py")

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Setup dello stato
        self.state.file_path = full_path
        self.state.file_content = content
        
        # Estraiamo la project_key (prima cartella del path)
        parts = target_relative_path.split("/")
        self.state.project_key = parts[0] 
        self.state.iteration = 1
        
        print(f"Target: {target_relative_path}")
        print(f"Project Key: {self.state.project_key}")
        
        # Non ritorniamo più una stringa, l'importante è che il metodo finisca
        return True

    @listen(select_target_file) # <--- MODIFICA CHIAVE: Ascolta direttamente il metodo
    def run_initial_analysis(self):
        """
        Step 2: Esegue scansione SonarQube PREVENTIVA usando Git Bash environment.
        """
        print(f"\n--- STEP 2: Analisi Iniziale (Git Bash Style) ---")
        
        sonar_token = os.getenv("SONAR_TOKEN")
        if not sonar_token:
            print("⚠️ WARNING: SONAR_TOKEN non trovato. Assicurati che sia nel file .env")

        project_dir = os.path.join(DIRECTORY_REPOS, self.state.project_key)
        
        # Usiamo mvn semplice perché sei su Git Bash
        mvn_executable = "mvn" 

        # Comando completo
        cmd = [
            mvn_executable, 
            "clean", 
            "verify", 
            "org.sonarsource.scanner.maven:sonar-maven-plugin:sonar",
            f"-Dsonar.projectKey={self.state.project_key}",
            f"-Dsonar.projectName={self.state.project_key}",
            "-Dsonar.host.url=http://localhost:9000",
            f"-Dsonar.token={sonar_token}",
            "-Dmaven.test.failure.ignore=true"
        ]
        
        cmd_str = " ".join(cmd)

        try:
            print(f"Avvio scansione su: {project_dir}")
            print(f"Comando:\n{cmd_str}")
            
            # Eseguiamo il comando ereditando l'ambiente Git Bash
            result = subprocess.run(
                cmd_str, 
                cwd=project_dir, 
                check=True, 
                capture_output=True, 
                text=True, 
                shell=True 
            )
            
            print("✅ Analisi Maven completata. Attesa server SonarQube...")
            time.sleep(10) # Diamo tempo a Sonar di processare

            # --- RECUPERO ISSUES ---
            api_url = "http://localhost:9000/api/issues/search"
            
            # Calcolo Component Key
            # Rimuove la cartella progetto dal path per ottenere src/main/...
            rel_path = os.path.relpath(self.state.file_path, project_dir)
            clean_rel_path = rel_path.replace("\\", "/") # Assicuriamoci che usi slash normali per Sonar
            component_key = f"{self.state.project_key}:{clean_rel_path}"
            
            print(f"Recupero issues per component: {component_key}")

            params = {
                "componentKeys": component_key,
                "resolved": "false",
                "ps": 500  # Page Size alta per prendere tutte le issues
            }

            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            
            if response.status_code == 200:
                data = response.json()
                issues = data.get("issues", [])
                
                if not issues:
                    msg = "Nessun energy smell rilevato da SonarQube."
                    print(f"✅ {msg}")
                    self.state.error_log = msg
                else:
                    print(f"⚠️ Trovate {len(issues)} issues.")
                    report = "Issues Energetiche rilevate:\n"
                    for issue in issues:
                        line = issue.get("line", "?")
                        msg = issue.get("message", "")
                        rule = issue.get("rule", "")
                        report += f"- [Riga {line}] {rule}: {msg}\n"
                    
                    self.state.error_log = report
            else:
                print(f"❌ Errore API Sonar: {response.status_code}")
                self.state.error_log = "Impossibile recuperare issues iniziali."

        except subprocess.CalledProcessError as e:
            print("❌ Errore durante l'esecuzione del comando Maven.")
            full_error = (e.stdout or "") + "\n" + (e.stderr or "")
            print(f"Dettagli errore:\n{full_error[-1000:]}") # Stampa gli ultimi 1000 caratteri
            self.state.error_log = "Errore build iniziale."        
        except Exception as e:
            print(f"❌ Errore generico: {e}")
            self.state.error_log = f"Errore: {str(e)}"

        return True

    @listen(run_initial_analysis) # <--- MODIFICA CHIAVE: Concatena al metodo precedente
    def run_refactoring_crew(self):
        """
        Step 3: Lancia la Crew.
        """
        print(f"\n--- STEP 3: Avvio Crew (Refactoring) ---")
        
        inputs = {
            "code_class": self.state.file_content,
            "path_class": self.state.file_path,
            "project_key": self.state.project_key,
            "errors": self.state.error_log
        }

        # Lancia la crew
        result = RefactorCrew().crew().kickoff(inputs=inputs)
        print("\n--- Refactoring Completato ---")
        return True

    @listen(run_refactoring_crew) # <--- MODIFICA CHIAVE
    def finish(self):
        print("\n--- STEP 4: Fine Flusso ---")
        print(f"File processato: {self.state.file_path}")

def kickoff():
    refactoring_flow = RefactoringFlow()
    refactoring_flow.kickoff()

if __name__ == "__main__":
    kickoff()