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
from constants import DIRECTORY_REPOS, JAVA_COLLECTION_RULES, MAVEN_DEPENDENCIES, CUSTOM_RULES

IGNORE_LIST_FILE = "ignore_list.json"

def load_ignore_list():
    if os.path.exists(IGNORE_LIST_FILE):
        try:
            with open(IGNORE_LIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def add_to_ignore_list(file_path):
    ignored = load_ignore_list()
    if file_path not in ignored:
        ignored.append(file_path)
        with open(IGNORE_LIST_FILE, "w", encoding="utf-8") as f:
            json.dump(ignored, f, indent=4)
    print(f"🚫 Added {file_path} to ignore list.")

# Definiamo lo stato del flusso
class RefactoringState(BaseModel):
    project_key: str = ""
    file_path: str = ""
    file_content: str = ""
    error_log: str = ""
    issues: list = []
    refactoring_valid: bool = False
    iteration: int = 0
    target_files: list = []
    failure_count: int = 0
    success_count: int = 0
    total_attempts: int = 0
    file_results: List[dict] = []
    final_coverage: float = 0.0


class RefactoringFlow(Flow[RefactoringState]):
    llm = LLM(
        model="gemini-2.5-flash", # Changed for better tool execution reliability
        api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.2
    )

    @start()
    def select_project(self):
        """
        Step 1: Seleziona il progetto target e prepara lo stato.
        """
        print("\n--- STEP 1: Selezione Progetto Target ---")

        # Progetto target, CHANGE HERE TO CHANGE TARGET PROJECT
        project_key = "scacchi-afp"

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
        Step 2: Esegue scansione SonarQube e identifica i top 10 file con più issue.
        """
        print(f"\n--- STEP 2: Analisi Hotspots e Selezione File Target ---")

        sonar_token = os.getenv("SONAR_TOKEN")
        if not sonar_token:
            print("⚠️ WARNING: SONAR_TOKEN non trovato.")
            self.state.error_log = "SONAR_TOKEN non configurato."
            return False

        project_dir = os.path.join(DIRECTORY_REPOS, self.state.project_key)
        
        # Comando per la scansione iniziale
        cmd_str = f"mvn clean verify org.sonarsource.scanner.maven:sonar-maven-plugin:sonar -Dsonar.projectKey={self.state.project_key} -Dsonar.projectName={self.state.project_key} -Dsonar.host.url=http://localhost:9000 -Dsonar.token={sonar_token} -Dmaven.test.failure.ignore=true -DskipTests" # RIAGGIUNGERE TEST

        try:
            print(f"Avvio scansione su: {project_dir}")
            subprocess.run(cmd_str, cwd=project_dir, check=True, capture_output=True, text=True, shell=True)
            print("✅ Analisi Maven completata. Attesa server SonarQube...")
            time.sleep(10)

            # Recupero hotspots con facets
            api_url = "http://localhost:9000/api/issues/search"
            params = {"componentKeys": self.state.project_key, "facets": "files", "resolved": "false", "ps": 1, "f.files.limit": 500}
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            response.raise_for_status()
            data = response.json()

            file_facet = next((f for f in data.get("facets", []) if f["property"] == "files"), None)
            if not file_facet or not file_facet.get("values"):
                print("✅ Nessun file con issue trovato.")
                return False # Interrompiamo se non ci sono file

            # Seleziona i top 10 file con PIÙ problemi (ordine decrescente)
            # Filter out ignored files
            ignored_files = load_ignore_list()
            sorted_files = sorted(file_facet["values"], key=lambda x: x["count"], reverse=True)
            
            valid_files = []
            print("🔎 Filtering files (checking ignore list and LOC <= 3500)...")

            for f in sorted_files:
                if len(valid_files) >= 10:
                    break

                f_path = f["val"].split(":", 1)[-1]
                # Extract path relative to project (handle project keys with colons correctly)
                if f["val"].startswith(self.state.project_key + ":"):
                    f_path = f["val"][len(self.state.project_key)+1:]
                else:
                    f_path = f["val"].split(":", 1)[-1]

                if f_path in ignored_files:
                    continue

                # Check LOC via Sonar API
                try:
                    loc_url = "http://localhost:9000/api/measures/component"
                    loc_params = {"component": f["val"], "metricKeys": "lines"}
                    loc_resp = requests.get(loc_url, auth=(sonar_token, ""), params=loc_params)
                    
                    # Fallback if component key from facet gives 404 (mismatch in key format)
                    if loc_resp.status_code == 404:
                        fallback_key = f"{self.state.project_key}:{f_path}"
                        if fallback_key != f["val"]:
                            loc_params["component"] = fallback_key
                            loc_resp = requests.get(loc_url, auth=(sonar_token, ""), params=loc_params)

                    if loc_resp.status_code == 200:
                        measures = loc_resp.json().get("component", {}).get("measures", [])
                        # Default to a high number so we don't accidentally select files with missing metrics
                        lines_val = next((int(m["value"]) for m in measures if m["metric"] == "lines"), 999999)
                        
                        if lines_val == 999999:
                            print(f"⚠️ LOC metric missing for {f_path}. Skipping.")
                        elif lines_val <= 3500:
                            valid_files.append(f)
                            print(f"✅ Added {f_path} (Issues: {f['count']}, LOC: {lines_val})")
                        else:
                            print(f"🚫 Skipping {f_path} (LOC: {lines_val} > 3500)")
                    else:
                        print(f"⚠️ Failed to get LOC for {f_path} (Key: {loc_params['component']}). Status: {loc_resp.status_code}. Skipping.")
                except Exception as e:
                    print(f"⚠️ Error checking LOC for {f_path}: {e}")
            
            self.state.target_files = valid_files
            
            if not self.state.target_files:
                print("✅ Nessun file idoneo trovato (tutti ignorati o nessun problema).")
                return False

            print(f"✅ Trovati {len(self.state.target_files)} file da analizzare in coda.")

        except (subprocess.CalledProcessError, requests.HTTPError) as e:
            error_details = e.stdout + "\n" + e.stderr if isinstance(e, subprocess.CalledProcessError) else e.response.text
            print(f"❌ Errore durante l'analisi: {error_details[-1000:]}")
            self.state.error_log = "Errore durante l'analisi iniziale."
            return False
        
        return True

    @router(run_analysis_and_find_hotspots)
    def route_initial(self):
        if self.state.target_files:
            next_file = self.state.target_files.pop(0)
            self.state.file_path = next_file["val"].split(":", 1)[-1]
            print(f"\n--- INIZIO CICLO PER: {self.state.file_path} ({next_file['count']} issues) ---")
            return "process_file"
        return "completed"

    @listen("process_file")
    def get_issues_for_file(self):
        """
        Step 3: Recupera le issue per il file selezionato.
        """
        if not self.state.file_path:
            print("⚠️ Nessun file selezionato. Salto recupero issues.")
            return False

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
    def ensure_dependencies(self):
        """
        Step 3.5: Checks if 'Avoid Java Collection Framework' issues exist and adds dependencies to pom.xml.
        """
        # Check if we have relevant issues
        has_collection_issue = False
        for issue in self.state.issues:
            msg = issue.get("message", "")
            rule = issue.get("rule", "")
            # Check specifically for the issue type mentioned
            if "Avoid Java Collection Framework" in msg or "AvoidJavaCollectionFramework" in rule: 
                has_collection_issue = True
                break
        
        if not has_collection_issue:
            print("ℹ️ No 'Avoid Java Collection Framework' issues found. Skipping dependency check.")
            return True

        print(f"\n--- STEP 3.5: Checking Maven Dependencies (pom.xml) ---")
        pom_path = os.path.join(DIRECTORY_REPOS, self.state.project_key, "pom.xml")
        
        if not os.path.exists(pom_path):
            print(f"❌ pom.xml not found at: {pom_path}")
            return False

        try:
            with open(pom_path, "r", encoding="utf-8") as f:
                pom_content = f.read()
            
            new_deps = []

            # Check and add Eclipse Collections
            if "org.eclipse.collections" not in pom_content:
                print("➕ Adding dependency: Eclipse Collections")
                new_deps.append(MAVEN_DEPENDENCIES["eclipse_collections"])
            
            # Check and add Apache Commons Collections
            if "commons-collections4" not in pom_content:
                print("➕ Adding dependency: Apache Commons Collections")
                new_deps.append(MAVEN_DEPENDENCIES["commons_collections"])

            if new_deps:
                if "</dependencies>" in pom_content:
                    insertion = "\n".join(new_deps)
                    new_pom_content = pom_content.replace("</dependencies>", f"{insertion}\n    </dependencies>")
                    
                    with open(pom_path, "w", encoding="utf-8") as f:
                        f.write(new_pom_content)
                    print("✅ pom.xml updated successfully.")
                else:
                    print("⚠️ Could not find </dependencies> tag in pom.xml. Skipping update.")
            else:
                print("✅ Dependencies already present.")

        except Exception as e:
            print(f"❌ Error updating pom.xml: {e}")
            return False
            
        return True

    @listen(ensure_dependencies)
    def refactor_file_issues(self):
        """
        Step 4: Esegue il refactoring per il file basandosi su tutte le issue trovate.
        """
        if not self.state.file_path:
            print("⚠️ Nessun file selezionato. Salto refactoring.")
            return False

        print(f"\n--- STEP 4: Avvio Refactoring per {len(self.state.issues)} issues ---")

        full_file_path = os.path.join(DIRECTORY_REPOS, self.state.project_key, self.state.file_path)
        full_file_path = os.path.normpath(full_file_path)

        if not os.path.isfile(full_file_path):
            print(f"❌ Il percorso non è un file valido o non esiste: {full_file_path}")
            return False

        try:
            with open(full_file_path, "r", encoding="utf-8") as f:
                self.state.file_content = f.read()
        except Exception as e:
            print(f"❌ Errore lettura file {full_file_path}: {e}")
            return False # Interrompi se il file non può essere letto

        # Logica Full File: Passiamo l'intero contenuto del file
        total_lines = len(self.state.file_content.splitlines())
        print(f"--- Using Full File Content ({total_lines} lines) ---")

        # Fetch detailed rule descriptions from SonarQube API
        sonar_token = os.getenv("SONAR_TOKEN")
        unique_rules = list(set(issue.get("rule") for issue in self.state.issues if issue.get("rule")))
        rule_details_text = ""

        if unique_rules:
            print(f"Fetching details for {len(unique_rules)} rules from SonarQube...")
            for rule_key in unique_rules:
                try:
                    resp = requests.get("http://localhost:9000/api/rules/show", auth=(sonar_token, ""), params={"key": rule_key})
                    if resp.status_code == 200:
                        rule_data = resp.json().get("rule", {})
                        # Prefer markdown description, fallback to html or generic description
                        desc = rule_data.get("mdDesc") or rule_data.get("htmlDesc") or rule_data.get("description") or ""
                        rule_details_text += f"\n\n--- RULE {rule_key}: {rule_data.get('name', '')} ---\n{desc}"
                except Exception as e:
                    print(f"⚠️ Failed to fetch rule {rule_key}: {e}")

        project_dir = os.path.join(DIRECTORY_REPOS, self.state.project_key)

        inputs = {
            "code_class": self.state.file_content,
            "path_class": full_file_path,
            "project_key": self.state.project_key,
            "project_dir": project_dir,
            "errors": f"{json.dumps(self.state.issues)}\n\n{JAVA_COLLECTION_RULES}\n\n{CUSTOM_RULES}\n\nSONARQUBE RULE DETAILS:{rule_details_text}", # Passa issues + regole + dettagli API
            "original_issues_list": self.state.issues,
            "start_line": 1,
            "line_count": total_lines
        }

        print("Avvio RefactorCrew con tutte le issue...")
        result, attempts = RefactorCrew(llm=self.llm).run_refactoring_cycle(inputs=inputs)
        
        self.state.total_attempts += attempts
        
        if result is False:
            self.state.failure_count += 1
            self.state.file_results.append({
                "file": self.state.file_path,
                "status": "FAILED",
                "attempts": attempts
            })
            print(f"❌ Refactoring failed for {self.state.file_path}. Adding to ignore list.")
            add_to_ignore_list(self.state.file_path)
            return False
        
        self.state.success_count += 1
        self.state.file_results.append({
            "file": self.state.file_path,
            "status": "SUCCESS",
            "attempts": attempts
        })

        print("--- Refactoring completato ---")
        
        return True

    @listen(refactor_file_issues)
    def final_analysis(self):
        """
        Step 5: Esegue una scansione finale per verificare i risultati.
        """
        print("\n--- STEP 5: Verifica Finale ---")
        print("✅ Il ciclo di refactoring è terminato con successo. L'analisi SonarQube è già stata aggiornata dall'agente di validazione.")
        
        return True

    @router(final_analysis)
    def route_loop(self):
        if self.state.target_files:
            next_file = self.state.target_files.pop(0)
            self.state.file_path = next_file["val"].split(":", 1)[-1]
            print(f"\n--- INIZIO CICLO PER: {self.state.file_path} ({next_file['count']} issues) ---")
            return "process_file"
        print("\n✅ Tutti i file in coda sono stati processati.")
        return "final_coverage_scan"

    @listen("final_coverage_scan")
    def run_final_coverage_scan(self):
        """
        Step 6: Esegue scansione finale CON TEST per calcolare la coverage.
        """
        print("\n--- STEP 6: Scansione Finale con Test Coverage ---")
        
        sonar_token = os.getenv("SONAR_TOKEN")
        if not sonar_token:
            print("⚠️ SONAR_TOKEN mancante. Salto scansione coverage.")
            return

        project_dir = os.path.join(DIRECTORY_REPOS, self.state.project_key)
        
        # Optimization: Check if tests exist before running heavy build
        test_dir = os.path.join(project_dir, "src", "test")
        has_tests = False
        if os.path.exists(test_dir):
            for _, _, files in os.walk(test_dir):
                if any(f.endswith(".java") for f in files):
                    has_tests = True
                    break

        if not has_tests:
            print(f"ℹ️ Nessun test (file .java) trovato in {self.state.project_key}.")
            print("   Salto esecuzione test e imposto coverage a 0.0%.")
            self.state.final_coverage = 0.0
            return
        
        # Costruzione path dinamico per l'agente JaCoCo (basato sull'esempio funzionante fornito)
        user_home = os.path.expanduser("~").replace("\\", "/")
        jacoco_agent_jar = f"{user_home}/.m2/repository/org/jacoco/org.jacoco.agent/0.8.14/org.jacoco.agent-0.8.14-runtime.jar"
        arg_line = f"-javaagent:{jacoco_agent_jar}=destfile=target/jacoco.exec -XX:+EnableDynamicAgentLoading"

        # Comando SENZA skipTests, ma con ignore failure per garantire che l'analisi Sonar venga eseguita
        cmd_str = f'mvn clean verify org.jacoco:jacoco-maven-plugin:report org.sonarsource.scanner.maven:sonar-maven-plugin:sonar -Dsonar.projectKey={self.state.project_key} -Dsonar.projectName={self.state.project_key} -Dsonar.host.url=http://localhost:9000 -Dsonar.token={sonar_token} -Dmaven.test.failure.ignore=true -DargLine="{arg_line}"'
        
        print(f"Esecuzione test e analisi SonarQube su: {project_dir}")
        print("Questo passaggio potrebbe richiedere del tempo...")
        
        try:
            subprocess.run(cmd_str, cwd=project_dir, check=True, capture_output=True, text=True, shell=True)
            print("✅ Build e Test completati. Attesa elaborazione SonarQube...")
            time.sleep(15) # Attesa buffer per elaborazione background
            
            # Recupero Coverage
            api_url = "http://localhost:9000/api/measures/component"
            params = {"component": self.state.project_key, "metricKeys": "coverage"}
            
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            if response.status_code == 200:
                measures = response.json().get("component", {}).get("measures", [])
                coverage_val = next((m["value"] for m in measures if m["metric"] == "coverage"), "0.0")
                self.state.final_coverage = float(coverage_val)
                print(f"✅ Final Test Coverage: {self.state.final_coverage}%")
        except Exception as e:
            print(f"❌ Errore durante la scansione finale/recupero coverage: {e}")

def kickoff():
    start_time = time.time()
    refactoring_flow = RefactoringFlow()
    refactoring_flow.kickoff()
    end_time = time.time()

    elapsed_time = end_time - start_time
    hours = int(elapsed_time // 3600)
    minutes = int((elapsed_time % 3600) // 60)

    print(f"\nTotal execution time: {elapsed_time:.2f} seconds")
    print(f"Total execution time: {hours} hours and {minutes} minutes")
    
    # Statistics Logging
    state = refactoring_flow.state
    total_files = len(state.file_results)
    avg_attempts = state.total_attempts / total_files if total_files > 0 else 0

    print("\n--- REFACTORING STATISTICS ---")
    print(f"Total Files Processed: {total_files}")
    print(f"Successful Refactorings: {state.success_count}")
    print(f"Failed Refactorings: {state.failure_count}")
    print(f"Total Attempts: {state.total_attempts}")
    print(f"Average Attempts per File: {avg_attempts:.2f}")
    print(f"Final Test Coverage: {state.final_coverage}%")

    log_data = {
        "project": state.project_key,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "execution_time_seconds": elapsed_time,
        "total_files": total_files,
        "success_count": state.success_count,
        "failure_count": state.failure_count,
        "total_attempts": state.total_attempts,
        "average_attempts": avg_attempts,
        "final_coverage_percent": state.final_coverage,
        "details": state.file_results
    }
    
    stats_file = "refactoring_stats.json"
    existing_data = []

    if os.path.exists(stats_file):
        try:
            with open(stats_file, "r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    existing_data = content
                else:
                    existing_data = [content]
        except Exception:
            pass

    existing_data.append(log_data)
    
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, indent=4)
    print(f"Stats appended to {stats_file}")

if __name__ == "__main__":
    kickoff()
