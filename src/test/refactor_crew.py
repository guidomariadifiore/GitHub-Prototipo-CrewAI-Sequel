import os
import subprocess
import time
import requests
from typing import List, Optional

from crewai import Agent, Crew, Process, Task, LLM, TaskOutput
from crewai.project import CrewBase, agent, crew, task
from crewai.tools import tool, BaseTool
from file_tools import FileUpdateTool
from tools.tools import SonarScanTool

# Assicurati che questi import puntino ai tuoi file corretti o definisci le costanti qui
# Se non hai il file constants.py, modifica DIRECTORY_REPOS con il path assoluto della cartella dei progetti
from constants import DIRECTORY_REPOS

class FilePatchTool(BaseTool):
    name: str = "File Patch Tool"
    description: str = "Replaces a specific range of lines in a file with new code. Requires file_path, new_code, start_line, and end_line (inclusive)."
    
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
        return Task(config=self.tasks_config['task3'], verbose=False, tools=[FilePatchTool()])

    @task
    def task4(self) -> Task:
        return Task(
            config=self.tasks_config['task4'], 
            verbose=False, 
            tools=[SonarScanTool()]
        )

    @task
    def conditional_task5(self) -> Task:
        return Task(
            config=self.tasks_config['conditional_task5'], 
            verbose=False, 
            tools=[FilePatchTool()]
        )

    @task
    def conditional_task6(self) -> Task:
        return Task(
            config=self.tasks_config['conditional_task6'], 
            verbose=False
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )