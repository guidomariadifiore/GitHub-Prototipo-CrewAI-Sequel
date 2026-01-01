#!/usr/bin/env python
import os
import subprocess
import time
import requests
from dotenv import load_dotenv
from typing import Optional, List
from pydantic import BaseModel
import json

# Carica subito le variabili d'ambiente
load_dotenv()

from crewai.flow.flow import Flow, listen, start, router
from crewai import LLM

# Assicurati che questi import siano corretti rispetto alla tua struttura cartelle
from refactor_crew import RefactorCrew
from constants import DIRECTORY_REPOS


# Definiamo lo stato del flusso
class RefactoringState(BaseModel):
    project_key: str = ""
    file_path: str = ""
    file_content: str = ""
    error_log: str = ""
    issues: list = []
    refactoring_valid: bool = False
    iteration: int = 0


class RefactoringFlow(Flow[RefactoringState]):
    llm = LLM(
        model="gemini-2.5-flash-lite", # MODIFICARE DOPO AVER TESTATO
        api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.2
    )

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
        self.state.file_path = ""
        self.state.file_content = ""
        self.state.issues = []

        print(f"Project Key: {self.state.project_key}")
        return True

    @listen(select_project)
    def run_analysis_and_find_hotspots(self):
        """
        Step 2: Esegue scansione SonarQube, trova i 10 file con più issue e seleziona quello con meno issue.
        """
        print(f"\n--- STEP 2: Analisi Hotspots e Selezione File Target ---")

        sonar_token = os.getenv("SONAR_TOKEN")
        if not sonar_token:
            print("⚠️ WARNING: SONAR_TOKEN non trovato.")
            self.state.error_log = "SONAR_TOKEN non configurato."
            return False

        project_dir = os.path.join(DIRECTORY_REPOS, self.state.project_key)
        
        # Comando per la scansione iniziale
        cmd_str = f"mvn clean verify org.sonarsource.scanner.maven:sonar-maven-plugin:sonar -Dsonar.projectKey={self.state.project_key} -Dsonar.projectName={self.state.project_key} -Dsonar.host.url=http://localhost:9000 -Dsonar.token={sonar_token} -Dmaven.test.failure.ignore=true"

        try:
            print(f"Avvio scansione su: {project_dir}")
            subprocess.run(cmd_str, cwd=project_dir, check=True, capture_output=True, text=True, shell=True)
            print("✅ Analisi Maven completata. Attesa server SonarQube...")
            time.sleep(10)

            # Recupero hotspots con facets
            api_url = "http://localhost:9000/api/issues/search"
            params = {"componentKeys": self.state.project_key, "facets": "files", "resolved": "false", "ps": 1}
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            response.raise_for_status()
            data = response.json()

            file_facet = next((f for f in data.get("facets", []) if f["property"] == "files"), None)
            if not file_facet or not file_facet.get("values"):
                print("✅ Nessun file con issue trovato.")
                return False # Interrompiamo se non ci sono file

            # Seleziona il file con il minor numero di problemi tra i top 10
            top_10_files = sorted(file_facet["values"], key=lambda x: x["count"])
            
            if not top_10_files:
                print("✅ Nessun file con issue nella top 10.")
                return False

            target_file = top_10_files[0]
            self.state.file_path = target_file["val"].split(":", 1)[-1]
            
            print(f"\n--- FILE TARGET SELEZIONATO ---")
            print(f"File: {self.state.file_path} ({target_file['count']} issues)")

        except (subprocess.CalledProcessError, requests.HTTPError) as e:
            error_details = e.stdout + "\n" + e.stderr if isinstance(e, subprocess.CalledProcessError) else e.response.text
            print(f"❌ Errore durante l'analisi: {error_details[-1000:]}")
            self.state.error_log = "Errore durante l'analisi iniziale."
            return False
        
        return True

    @listen(run_analysis_and_find_hotspots)
    def get_issues_for_file(self):
        """
        Step 3: Recupera le issue per il file selezionato.
        """
        print(f"\n--- STEP 3: Recupero Issues per {self.state.file_path} ---")
        sonar_token = os.getenv("SONAR_TOKEN")
        api_url = "http://localhost:9000/api/issues/search"
        
        # Il componentKey per un file è projectKey:filePath
        component_key = f"{self.state.project_key}:{self.state.file_path}"

        params = {"componentKeys": component_key, "resolved": "false", "ps": 500} # Aumenta ps per prenderle tutte
        
        try:
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            response.raise_for_status()
            data = response.json()
            
            self.state.issues = data.get("issues", [])
            print(f"Trovate {len(self.state.issues)} issue per il file.")
            
            if not self.state.issues:
                print("Nessuna issue da risolvere per questo file.")
                return False # Potremmo interrompere qui se non ci sono issue

        except requests.HTTPError as e:
            print(f"❌ Errore API Sonar: {e.response.text}")
            return False
            
        return True

    @listen(get_issues_for_file)
    def refactor_file_issues(self):
        """
        Step 4: Esegue il refactoring per il file basandosi su tutte le issue trovate.
        """
        print(f"\n--- STEP 4: Avvio Refactoring per {len(self.state.issues)} issues ---")

        full_file_path = os.path.join(DIRECTORY_REPOS, self.state.file_path)

        try:
            with open(full_file_path, "r", encoding="utf-8") as f:
                self.state.file_content = f.read()
        except FileNotFoundError:
            print(f"❌ File non trovato: {full_file_path}")
            return False # Interrompi se il file non può essere letto

        # Passiamo tutte le issue in una volta sola
        inputs = {
            "code_class": self.state.file_content,
            "path_class": full_file_path,
            "project_key": self.state.project_key,
            "errors": json.dumps(self.state.issues), # Passa l'intera lista di issue
        }

        print("Avvio RefactorCrew con tutte le issue...")
        RefactorCrew(llm=self.llm).crew().kickoff(inputs=inputs)
        print("--- Refactoring completato ---")
        
        return True

    @listen(refactor_file_issues)
    def final_analysis(self):
        """
        Step 5: Esegue una scansione finale per verificare i risultati.
        """
        print("\n--- STEP 5: Scansione Finale SonarQube ---")
        sonar_token = os.getenv("SONAR_TOKEN")
        project_dir = os.path.join(DIRECTORY_REPOS, self.state.project_key)
        
        cmd_str = f"mvn clean verify org.sonarsource.scanner.maven:sonar-maven-plugin:sonar -Dsonar.projectKey={self.state.project_key} -Dsonar.projectName={self.state.project_key} -Dsonar.host.url=http://localhost:9000 -Dsonar.token={sonar_token} -Dmaven.test.failure.ignore=true"

        try:
            print(f"Avvio scansione finale su: {project_dir}")
            subprocess.run(cmd_str, cwd=project_dir, check=True, capture_output=True, text=True, shell=True)
            print("✅ Scansione finale completata.")
        except subprocess.CalledProcessError as e:
            print(f"❌ Errore durante la scansione finale: {e.stderr}")
        
        return True

def kickoff():
    refactoring_flow = RefactoringFlow()
    refactoring_flow.kickoff()

if __name__ == "__main__":
    kickoff()
