import os
import subprocess
import time
import requests
from typing import List, Optional, Type
from pydantic import BaseModel, Field

from crewai import Agent, Crew, Process, Task, LLM, TaskOutput
from crewai.project import CrewBase, agent, crew, task
from crewai.tools import tool, BaseTool
from file_tools import FileUpdateTool

# Assicurati che questi import puntino ai tuoi file corretti o definisci le costanti qui
# Se non hai il file constants.py, modifica DIRECTORY_REPOS con il path assoluto della cartella dei progetti
from constants import DIRECTORY_REPOS

class FilePatchToolSchema(BaseModel):
    """Input for FilePatchTool."""
    file_path: str = Field(..., description="The absolute path to the file that needs to be patched.")
    new_code: str = Field(..., description="The new code that will replace the specified lines.")
    start_line: int = Field(..., description="The line number where the replacement should start (inclusive).")
    end_line: int = Field(..., description="The line number where the replacement should end (inclusive).")


class FilePatchTool(BaseTool):
    name: str = "File Patch Tool"
    description: str = "Replaces a specific range of lines in a file with new code. Requires file_path, new_code, start_line, and end_line (inclusive)."
    args_schema: Type[BaseModel] = FilePatchToolSchema
    
    def _run(self, file_path: str, new_code: str, start_line: int, end_line: int) -> str:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Convert 1-based start_line to 0-based index
            start_idx = start_line - 1
            end_idx = end_line  # end_line is inclusive, so slice up to end_line (which is index+1)
            
            # Ensure bounds
            if start_idx < 0: start_idx = 0
            if end_idx > len(lines): end_idx = len(lines)
            
            content_before = lines[:start_idx]
            content_after = lines[end_idx:]
            
            # Ensure new_code ends with newline to prevent merging with the next line
            if new_code and not new_code.endswith('\n'):
                new_code += '\n'
                
            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(content_before)
                f.write(new_code)
                f.writelines(content_after)
                
            return "File patched successfully."
        except Exception as e:
            return f"Error patching file: {str(e)}"

class SonarScanToolSchema(BaseModel):
    """Input for SonarScanTool."""
    project_key: str = Field(..., description="The key of the project to scan.")
    project_dir: str = Field(..., description="The absolute path to the project root directory containing the pom.xml.")

class SonarScanTool(BaseTool):
    name: str = "Sonar Scan Tool"
    description: str = "Executes Maven build and SonarQube scan in the specified project directory. Returns the build output."
    args_schema: Type[BaseModel] = SonarScanToolSchema

    def _run(self, project_key: str, project_dir: str) -> str:
        sonar_token = os.getenv("SONAR_TOKEN")
        if not sonar_token:
            return "Error: SONAR_TOKEN environment variable not set."

        # Command exactly as used in main.py
        cmd = f"mvn clean verify org.sonarsource.scanner.maven:sonar-maven-plugin:sonar -Dsonar.projectKey={project_key} -Dsonar.projectName={project_key} -Dsonar.host.url=http://localhost:9000 -Dsonar.token={sonar_token} -Dmaven.test.failure.ignore=true -Dmaven.compiler.failOnError=false"
        
        try:
            # cwd=project_dir ensures we run in the correct folder
            result = subprocess.run(cmd, cwd=project_dir, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                return f"BUILD FAILURE:\n{result.stdout}\n{result.stderr}"
            return f"BUILD SUCCESS:\n{result.stdout}"
        except Exception as e:
            return f"Execution Error: {str(e)}"

# -------------------------------------------------------------------------
# CREW DEFINITION
# -------------------------------------------------------------------------

@CrewBase
class RefactorCrew:
    """RefactorCrew crew"""
    
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, llm):
        self.llm = llm

    @agent
    def query_writer(self) -> Agent:
        return Agent(config=self.agents_config['query_writer'], verbose=True, llm=self.llm)

    @agent
    def code_refactor(self) -> Agent:
        return Agent(config=self.agents_config['code_refactor'], verbose=True, llm=self.llm)

    @agent
    def code_replacer(self) -> Agent:
        return Agent(config=self.agents_config['code_replacer'], verbose=True, llm=self.llm)

    @agent
    def sonar_agent(self) -> Agent:
        return Agent(config=self.agents_config['sonar_agent'], verbose=True, llm=self.llm)

    @agent
    def errors_summarizer(self) -> Agent:
        return Agent(config=self.agents_config['errors_summarizer'], verbose=True, llm=self.llm)

    @task
    def task1(self) -> Task:
        return Task(config=self.tasks_config['task1'], verbose=True)

    @task
    def task2(self) -> Task:
        return Task(config=self.tasks_config['task2'], verbose=True)

    @task
    def task3(self) -> Task:
        t = Task(config=self.tasks_config['task3'], verbose=True, tools=[FileUpdateTool()])
        # Force strict tool usage instruction
        t.description += "\n\nCRITICAL: You MUST execute the 'Overwrite File Tool' to apply the changes. \n1. Pass the COMPLETE file content to the 'new_code' argument.\n2. Do NOT truncate the code. Do NOT use placeholders like '// ... rest of code'.\n3. Ensure the string is properly escaped if it contains quotes."
        return t

    @task
    def task4(self) -> Task:
        return Task(
            config=self.tasks_config['task4'], 
            verbose=True, 
            tools=[SonarScanTool()]
        )

    @task
    def conditional_task5(self) -> Task:
        return Task(
            config=self.tasks_config['conditional_task5'], 
            verbose=True, 
            tools=[FileUpdateTool()]
        )

    @task
    def conditional_task6(self) -> Task:
        return Task(
            config=self.tasks_config['conditional_task6'], 
            verbose=True
        )

    @crew
    def crew(self) -> Crew:
        """
        Defines the basic crew. Note: For the conditional retry logic, 
        use run_refactoring_cycle() instead of crew().kickoff().
        """
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False
        )

    def run_refactoring_cycle(self, inputs: dict):
        """
        Executes the refactoring flow with retry logic (max 3 attempts).
        Flow: Refactor -> Scan -> (If Fail: Revert -> Summarize -> Retry)
        """
        max_retries = 3
        for attempt in range(max_retries):
            print(f"\n=== Refactoring Attempt {attempt + 1}/{max_retries} ===")
            
            # Phase 1: Refactor, Patch, and Scan
            # We explicitly select tasks 1-4
            refactor_crew = Crew(
                agents=[self.query_writer(), self.code_refactor(), self.code_replacer(), self.sonar_agent()],
                tasks=[self.task1(), self.task2(), self.task3(), self.task4()],
                process=Process.sequential,
                verbose=True
            )
            
            result = refactor_crew.kickoff(inputs=inputs)
            result_str = str(result).lower()
            
            # Check for build failure. 
            # NOTE: Adjust this condition based on the actual output format of your SonarScanTool/Task4.
            if "build failure" not in result_str and "execution error" not in result_str and "error:" not in result_str:
                print(">>> Refactoring Successful!")
                return result
            
            print(f">>> Build Failed. Initiating recovery (Attempt {attempt + 1})...")
            
            # Phase 2: Revert and Summarize
            # We explicitly select tasks 5-6
            recovery_crew = Crew(
                agents=[self.code_replacer(), self.errors_summarizer()],
                tasks=[self.conditional_task5(), self.conditional_task6()],
                process=Process.sequential,
                verbose=True
            )
            
            summary = recovery_crew.kickoff(inputs=inputs)
            
            # Update inputs for the next iteration
            inputs['previous_errors'] = str(summary)
            inputs['previous_code_context'] = "Code was reverted due to build failure. Please fix the errors."
            
        print(">>> Max retries reached. Giving up on this file.")
        return "Refactoring failed after 3 attempts."