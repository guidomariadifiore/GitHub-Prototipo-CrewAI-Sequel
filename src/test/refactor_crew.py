import os
import subprocess
import time
import requests
import json
from typing import List, Optional, Type, Tuple
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
            
            # Save full log to file for error summarizer
            log_path = os.path.join(project_dir, "maven_build_log.txt")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}")

            if result.returncode != 0:
                return f"BUILD FAILURE. Check maven_build_log.txt for details.\n{result.stdout[-1000:]}"
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
        t = Task(
            config=self.tasks_config['conditional_task6'], 
            verbose=True
        )
        t.description += "\n\nERROR CONTEXT / FAILURE DETAILS:\n{current_failure_reason}"
        return t

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
        original_issues = inputs.get('original_issues_list', [])
        project_key = inputs.get('project_key')
        project_dir = inputs.get('project_dir')
        file_path_full = inputs.get('path_class')
        
        # Store the very original code to revert to if all attempts fail
        initial_code = inputs.get('code_class')
        
        # Track state for final decision
        last_build_failed = True
        last_issue_count = len(original_issues)

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
            result_str = str(result)
            result_lower = result_str.lower()
            
            # Check for build failure. 
            build_failed = "build failure" in result_lower or "execution error" in result_lower or "error:" in result_lower
            
            last_build_failed = build_failed
            
            failure_reason = ""
            failure_details = ""

            if build_failed:
                failure_reason = "Build Failed"
                # Retrieve full log from file
                log_path = os.path.join(project_dir, "maven_build_log.txt")
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8") as f:
                        failure_details = f.read()
                else:
                    failure_details = result_str
            else:
                # Build Success - Now Verify Issues
                print(">>> Build Successful. Verifying SonarQube issues...")
                verification_error, current_count = self._verify_refactoring(project_key, project_dir, file_path_full, original_issues)
                
                if current_count != -1:
                    last_issue_count = current_count
                
                if not verification_error:
                    print(">>> Refactoring Verified! Issues resolved.")
                    return result
                else:
                    failure_reason = "Verification Failed"
                    failure_details = verification_error
            
            print(f">>> {failure_reason}. Initiating recovery (Attempt {attempt + 1})...")
            
            inputs['current_failure_reason'] = f"{failure_reason}\n\nDETAILS:\n{failure_details}"
            
            summary = ""
            
            if build_failed:
                # Case 1: Build Failed -> Keep changes (broken code), update inputs['code_class'] for next attempt
                print(">>> Keeping changes despite build failure.")
                
                # Only run summarizer (Task 6)
                summarize_crew = Crew(
                    agents=[self.errors_summarizer()],
                    tasks=[self.conditional_task6()],
                    process=Process.sequential,
                    verbose=True
                )
                summary = summarize_crew.kickoff(inputs=inputs)
                
                # Update inputs['code_class'] with the current file content so next attempt builds upon this
                try:
                    with open(file_path_full, 'r', encoding='utf-8') as f:
                        current_content = f.read()
                    inputs['code_class'] = current_content
                except Exception as e:
                    print(f"Error reading updated file: {e}")
                
                inputs['previous_code_context'] = f"Attempt {attempt+1} failed (Build Error). Code KEPT. Fix build errors."
            else:
                # Case 2: Verification Failed -> Keep changes, update inputs['code_class'] for next attempt
                print(">>> Keeping changes despite verification failure (Build passed).")
                
                # Only run summarizer (Task 6)
                summarize_crew = Crew(
                    agents=[self.errors_summarizer()],
                    tasks=[self.conditional_task6()],
                    process=Process.sequential,
                    verbose=True
                )
                summary = summarize_crew.kickoff(inputs=inputs)
                
                # Update inputs['code_class'] with the current file content so next attempt builds upon this
                try:
                    with open(file_path_full, 'r', encoding='utf-8') as f:
                        current_content = f.read()
                    inputs['code_class'] = current_content
                except Exception as e:
                    print(f"Error reading updated file: {e}")
                
                inputs['previous_code_context'] = f"Attempt {attempt+1} failed (Verification Error). Code KEPT. Fix new issues."
            
            # Update inputs for the next iteration
            inputs['previous_errors'] = str(summary)
            
            print("\n⏳ API Rate Limit Cooldown: Waiting 60 seconds...")
            time.sleep(60)
            
            print("\n🛑 PAUSED: Press 'U' and Enter to resume next attempt...")
            while True:
                if input().strip().upper() == 'U':
                    break
            
        print(">>> Max retries reached. Giving up on this file.")
        
        should_revert = False
        if last_build_failed:
            print(">>> Final Decision: Revert. Reason: Last build failed.")
            should_revert = True
        elif last_issue_count > len(original_issues):
            print(f">>> Final Decision: Revert. Reason: Issue count increased ({last_issue_count} > {len(original_issues)}).")
            should_revert = True
        else:
            print(f">>> Final Decision: Keep. Reason: Build passed and issue count did not increase ({last_issue_count} <= {len(original_issues)}).")
            should_revert = False

        if should_revert:
            print(">>> Reverting to ORIGINAL state (before any attempts).")
            try:
                with open(file_path_full, 'w', encoding='utf-8') as f:
                    f.write(initial_code)
                print(">>> Revert complete.")
            except Exception as e:
                print(f"Error reverting to original: {e}")
            
        return "Refactoring failed after 3 attempts."

    def _verify_refactoring(self, project_key: str, project_dir: str, file_path_full: str, original_issues: list) -> Tuple[Optional[str], int]:
        """
        Verifies if the refactoring resolved original issues and didn't introduce new ones.
        Returns (error_message, current_issue_count). error_message is None if successful.
        """
        sonar_token = os.getenv("SONAR_TOKEN")
        sonar_url = os.getenv("SONAR_HOST_URL", "http://localhost:9000")
        
        if not sonar_token:
            return "SONAR_TOKEN not set, cannot verify.", -1

        # Calculate relative path for SonarQube component key
        try:
            relative_path = os.path.relpath(file_path_full, project_dir).replace("\\", "/")
        except ValueError:
            return f"Could not determine relative path for {file_path_full} relative to {project_dir}", -1

        component_key = f"{project_key}:{relative_path}"
        
        api_url = f"{sonar_url}/api/issues/search"
        params = {
            "componentKeys": component_key,
            "resolved": "false",
            "ps": 500
        }
        
        try:
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            response.raise_for_status()
            data = response.json()
            current_issues = data.get("issues", [])
            current_count = len(current_issues)
        except Exception as e:
            return f"Error querying SonarQube API: {str(e)}", -1

        # Check for NEW issues (Regressions) - Corresponds to "New Issues" tab in SonarQube
        try:
            params_new = params.copy()
            params_new["inNewCodePeriod"] = "true"
            params_new["ps"] = 50
            resp_new = requests.get(api_url, auth=(sonar_token, ""), params=params_new)
            resp_new.raise_for_status()
            new_data = resp_new.json()
            total_new = new_data.get("total", 0)
            if total_new > 0:
                issues = new_data.get("issues", [])
                details = []
                for issue in issues:
                    details.append(f"- Line {issue.get('line', '?')}: [{issue.get('rule', 'Unknown')}] {issue.get('message', '')}")
                return f"Refactoring introduced {total_new} NEW issues (regressions):\n" + "\n".join(details), current_count
        except Exception as e:
            print(f"Warning: Could not check new code period issues: {e}")

        # Strict check: Any issue key in current_issues that was not in original_issues is a new issue.
        original_keys = {issue.get("key") for issue in original_issues}
        current_keys = {issue.get("key") for issue in current_issues}
        new_keys = current_keys - original_keys
        
        if new_keys:
            new_issues_list = [i for i in current_issues if i.get("key") in new_keys]
            details = []
            for issue in new_issues_list:
                 details.append(f"- Line {issue.get('line', '?')}: [{issue.get('rule', 'Unknown')}] {issue.get('message', '')}")
            return f"Refactoring introduced {len(new_keys)} NEW issues (Key mismatch):\n" + "\n".join(details), current_count

        original_rules = {issue.get("rule") for issue in original_issues}
        current_rules = {issue.get("rule") for issue in current_issues}
        
        # Check for new issue types (regressions)
        new_rules = current_rules - original_rules
        if new_rules:
            return f"New issue types introduced: {', '.join(new_rules)}", current_count
            
        # Check if total issue count decreased (heuristic for 'resolution')
        # We allow partial resolution, but count must not increase or stay same if we started with issues.
        if len(original_issues) > 0 and len(current_issues) >= len(original_issues):
             return f"No issues were resolved (Count: {len(original_issues)} -> {len(current_issues)}).", current_count

        return None, current_count