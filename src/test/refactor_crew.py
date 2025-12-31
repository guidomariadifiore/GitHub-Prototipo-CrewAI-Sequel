import os
import subprocess
import time
import requests
from typing import List, Optional

from crewai import Agent, Crew, Process, Task, LLM, TaskOutput
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.tasks.conditional_task import ConditionalTask
from crewai.tools import tool
from pydantic import BaseModel

# Assicurati che questi import puntino ai tuoi file corretti o definisci le costanti qui
# Se non hai il file constants.py, modifica DIRECTORY_REPOS con il path assoluto della cartella dei progetti
from constants import DIRECTORY_REPOS

# Modello per l'output strutturato
class RefactoringVerificator(BaseModel):
    valid: bool
    errors: Optional[str]
    metric: int # Qui metteremo il numero di energy smells trovati

# -------------------------------------------------------------------------
# TOOL DEFINITIONS (Definiti direttamente qui come nel repository originale)
# -------------------------------------------------------------------------

@tool("sonar-scanner", result_as_answer=True)
def sonar_scanner(path_class: str):
    """
    Esegue Maven e SonarScanner.
    Restituisce True se la build ha successo e cattura gli Energy Smells come issues.
    """
    
    # 1. Setup dei percorsi
    path_class = os.path.normpath(path_class)
    parts = path_class.split(os.sep)
    
    # Adatta questi indici in base alla tua struttura di cartelle reale
    # Esempio: se il path è /home/user/repos/java_project/src/main/..., project_key è java_project
    project_key = parts[-4] # Esempio ipotetico, verifica il tuo path!
    if "src" in parts:
        try:
            index = parts.index("src")
            project_key = parts[index-1]
        except ValueError:
            pass

    directory_pom = ""
    # Cerca il pom.xml risalendo la directory o usando la root definita
    # Per semplicità, assumiamo che il progetto sia nella cartella definita da DIRECTORY_REPOS
    # Se DIRECTORY_REPOS non è settato, usa os.getcwd() o un path fisso per test
    search_root = os.path.join(DIRECTORY_REPOS) if 'DIRECTORY_REPOS' in globals() else os.getcwd()
    
    for root, dirs, files in os.walk(search_root):
        if project_key in root and "pom.xml" in files:
            directory_pom = root
            break
    
    if not directory_pom:
        # Fallback: prova a cercare nella directory corrente o padre
        directory_pom = os.getcwd()

    print(f"--- AVVIO SCANSIONE SU: {directory_pom} ---")

    try:
        # 2. Compilazione (Maven Clean Install)
        # Nota: verify include i test. Se vuoi saltarli aggiungi -DskipTests
        compilation = subprocess.run(
            ["mvn", "clean", "install", "-Dmaven.test.failure.ignore=true"],
            cwd=directory_pom,
            check=True,
            capture_output=True,
            text=True,
            shell=True # Necessario su alcuni OS se mvn non è nel path diretto
        )
        
        # Se la compilazione fallisce, subprocess lancia CalledProcessError (vedi except sotto)

        # 3. Analisi SonarQube
        sonar_token = os.getenv("SONAR_TOKEN") # Assicurati di averlo settato nelle variabili d'ambiente
        sonar_command = [
            "mvn", "sonar:sonar",
            f"-Dsonar.projectKey={project_key}",
            f"-Dsonar.host.url=http://localhost:9000",
            f"-Dsonar.login={sonar_token}"
        ]

        print("Esecuzione SonarQube...")
        subprocess.run(
            sonar_command,
            cwd=directory_pom,
            capture_output=True,
            text=True,
            check=True,
            shell=True
        )

        print("Attesa elaborazione report SonarQube...")
        time.sleep(10) # Tempo tecnico per SonarQube background task

        # 4. Recupero Issues (Energy Smells) tramite API
        # Questa è la parte modificata per il TUO caso specifico
        
        api_url = "http://localhost:9000/api/issues/search"
        params = {
            "componentKeys": project_key,
            "resolved": "false",
            "ps": 100 # Page size
        }
        
        try:
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            response.raise_for_status()
            data = response.json()
            
            total_issues = data.get("total", 0)
            issues_list = data.get("issues", [])
            
            # Costruiamo una stringa con i dettagli degli energy smells per l'agente
            issues_details = ""
            for issue in issues_list:
                msg = issue.get("message", "")
                line = issue.get("line", "?")
                rule = issue.get("rule", "")
                issues_details += f"- [Riga {line}] {rule}: {msg}\n"

            if total_issues == 0:
                print("Nessun Energy Smell trovato!")
                return RefactoringVerificator(valid=True, errors="Nessun issue trovato. Ottimo lavoro!", metric=0)
            else:
                print(f"Trovati {total_issues} issues.")
                return RefactoringVerificator(valid=True, errors=issues_details, metric=total_issues)

        except requests.exceptions.RequestException as e:
            print(f"Errore API Sonar: {e}")
            return RefactoringVerificator(valid=True, errors=f"Errore API: {str(e)}", metric=-1)

    except subprocess.CalledProcessError as e:
        # Cattura errore di compilazione Maven
        print("Errore di Compilazione rilevato.")
        output_err = e.stderr if e.stderr else e.stdout
        # Filtra solo le righe ERROR per non intasare il prompt
        error_lines = [line for line in output_err.splitlines() if "[ERROR]" in line]
        errors_filtered = "\n".join(error_lines[:20]) # Prendi solo le prime 20 righe di errore
        
        return RefactoringVerificator(valid=False, errors=errors_filtered, metric=-1)


@tool("code_replace")
def code_replace(path_class: str, code: str) -> str:
    """
    Sovrascrive il file Java nel percorso specificato con il codice fornito.
    Usa scrittura atomica (tmp + replace) e codifica UTF-8.
    """
    tmp = path_class + ".tmp"
    try:
        # Importante: encoding utf-8 per non rompere caratteri speciali
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(code)
            f.flush()
            os.fsync(f.fileno())
        
        os.replace(tmp, path_class)
        return f"File aggiornato con successo: {path_class}"
    except Exception as e:
        return f"Errore critico nella scrittura del file: {e}"


# -------------------------------------------------------------------------
# CREW DEFINITION
# -------------------------------------------------------------------------

@CrewBase
class RefactorCrew:
    """RefactorCrew crew"""

    refactoring_output: Optional[RefactoringVerificator] = None
    
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    
    def _save_task4_result(self, output: TaskOutput) -> None:
        """Salva l'output del task4 per usarlo nei task condizionali"""
        if getattr(output, "pydantic", None) is not None:
            self.refactoring_output = output.pydantic
            print(f"Stato Refactoring: Valid={self.refactoring_output.valid}, Issues={self.refactoring_output.metric}")

    def build_result(self, output: TaskOutput) -> bool:
        """
        Logica Condizionale:
        Ritorna True (esegui task rollback/errori) se il codice NON è valido (errore compilazione)
        """
        if self.refactoring_output is None:
            return False # Non abbiamo ancora dati, non eseguire rollback a caso
        
        # Se valid è False (compilazione fallita), esegui i task condizionali
        if self.refactoring_output.valid is False:
            return True
        
        return False


    # Definizione LLM (Usa le variabili d'ambiente per le chiavi!)
    llm = LLM(
        model="gemini-2.5-flash-lite", # MODIFICARE DOPO AVER TESTATO
        api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.2
    )

    @agent
    def query_writer(self) -> Agent:
        return Agent(config=self.agents_config['query_writer'], verbose=True, llm=self.llm)

    @agent
    def code_refactor(self) -> Agent:
        return Agent(config=self.agents_config['code_refactor'], verbose=True, llm=self.llm)

    @agent
    def code_replacer(self) -> Agent:
        return Agent(config=self.agents_config['code_replacer'], verbose=False, llm=self.llm)

    @agent
    def sonar_agent(self) -> Agent:
        return Agent(config=self.agents_config['sonar_agent'], verbose=False, llm=self.llm)

    @agent
    def errors_summarizer(self) -> Agent:
        return Agent(config=self.agents_config['errors_summarizer'], verbose=False, llm=self.llm)

    @task
    def task1(self) -> Task:
        return Task(config=self.tasks_config['task1'], verbose=True)

    @task
    def task2(self) -> Task:
        return Task(config=self.tasks_config['task2'], verbose=True)

    @task
    def task3(self) -> Task:
        return Task(config=self.tasks_config['task3'], verbose=False, tools=[code_replace])

    @task
    def task4(self) -> Task:
        return Task(
            config=self.tasks_config['task4'], 
            verbose=False, 
            tools=[sonar_scanner],
            output_pydantic=RefactoringVerificator,
            callback=self._save_task4_result
        )

    @task
    def conditional_task5(self) -> ConditionalTask:
        return ConditionalTask(
            config=self.tasks_config['conditional_task5'], 
            verbose=False, 
            tools=[code_replace],
            condition=self.build_result
        )

    @task
    def conditional_task6(self) -> ConditionalTask:
        return ConditionalTask(
            config=self.tasks_config['conditional_task6'], 
            verbose=False, 
            condition=self.build_result,
            output_pydantic=RefactoringVerificator
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )