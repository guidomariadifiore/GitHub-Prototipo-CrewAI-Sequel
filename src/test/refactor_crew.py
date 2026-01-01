import os
import subprocess
import time
import requests
from typing import List, Optional

from crewai import Agent, Crew, Process, Task, LLM, TaskOutput
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.conditional_task import ConditionalTask
from crewai.tools import tool
from file_tools import FileUpdateTool, SonarScanTool

# Assicurati che questi import puntino ai tuoi file corretti o definisci le costanti qui
# Se non hai il file constants.py, modifica DIRECTORY_REPOS con il path assoluto della cartella dei progetti
from constants import DIRECTORY_REPOS

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
        return Task(config=self.tasks_config['task3'], verbose=False, tools=[FileUpdateTool()])

    @task
    def task4(self) -> Task:
        return Task(
            config=self.tasks_config['task4'], 
            verbose=False, 
            tools=[SonarScanTool()]
        )

    @task
    def conditional_task5(self) -> ConditionalTask:
        return ConditionalTask(
            config=self.tasks_config['conditional_task5'], 
            verbose=False, 
            tools=[FileUpdateTool()]
        )

    @task
    def conditional_task6(self) -> ConditionalTask:
        return ConditionalTask(
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