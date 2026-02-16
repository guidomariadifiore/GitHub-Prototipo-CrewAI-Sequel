import os
import subprocess
import time
import requests
import json
import re
from typing import List, Optional, Type, Tuple
from pydantic import BaseModel, Field

from crewai import Agent, Crew, Process, Task, LLM, TaskOutput
from crewai.project import CrewBase, agent, crew, task
from crewai.tools import tool, BaseTool
from file_tools import FileUpdateTool

# Assicurati che questi import puntino ai tuoi file corretti o definisci le costanti qui
# Se non hai il file constants.py, modifica DIRECTORY_REPOS con il path assoluto della cartella dei progetti
from constants import DIRECTORY_REPOS, JAVA_COLLECTION_RULES, CUSTOM_RULES


class FilePatchToolSchema(BaseModel):
    """Input for FilePatchTool."""

    file_path: str = Field(
        ..., description="The absolute path to the file that needs to be patched."
    )
    new_code: str = Field(
        ..., description="The new code that will replace the specified lines."
    )
    start_line: int = Field(
        ...,
        description="The line number where the replacement should start (inclusive).",
    )
    end_line: int = Field(
        ..., description="The line number where the replacement should end (inclusive)."
    )


class FilePatchTool(BaseTool):
    name: str = "File Patch Tool"
    description: str = (
        "Replaces a specific range of lines in a file with new code. Requires file_path, new_code, start_line, and end_line (inclusive)."
    )
    args_schema: Type[BaseModel] = FilePatchToolSchema

    def _run(
        self, file_path: str, new_code: str, start_line: int, end_line: int
    ) -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Convert 1-based start_line to 0-based index
            start_idx = start_line - 1
            end_idx = end_line  # end_line is inclusive, so slice up to end_line (which is index+1)

            # Ensure bounds
            if start_idx < 0:
                start_idx = 0
            if end_idx > len(lines):
                end_idx = len(lines)

            content_before = lines[:start_idx]
            content_after = lines[end_idx:]

            # Ensure new_code ends with newline to prevent merging with the next line
            if new_code and not new_code.endswith("\n"):
                new_code += "\n"

            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines(content_before)
                f.write(new_code)
                f.writelines(content_after)

            return "File patched successfully."
        except Exception as e:
            return f"Error patching file: {str(e)}"


class SonarScanToolSchema(BaseModel):
    """Input for SonarScanTool."""

    project_key: str = Field(..., description="The key of the project to scan.")
    project_dir: str = Field(
        ...,
        description="The absolute path to the project root directory containing the pom.xml.",
    )


class SonarScanTool(BaseTool):
    name: str = "Sonar Scan Tool"
    description: str = (
        "Executes Maven build and SonarQube scan in the specified project directory. Returns the build output."
    )
    args_schema: Type[BaseModel] = SonarScanToolSchema

    def _run(self, project_key: str, project_dir: str) -> str:
        sonar_token = os.getenv("SONAR_TOKEN")
        if not sonar_token:
            return "Error: SONAR_TOKEN environment variable not set."

        # Command exactly as used in main.py
        cmd = f"mvn clean verify org.sonarsource.scanner.maven:sonar-maven-plugin:sonar -Dsonar.projectKey={project_key} -Dsonar.projectName={project_key} -Dsonar.host.url=http://localhost:9000 -Dsonar.token={sonar_token} -Dmaven.test.failure.ignore=true -DskipTests" #RIAGGIUNGERE TEST

        try:
            # cwd=project_dir ensures we run in the correct folder
            result = subprocess.run(
                cmd, cwd=project_dir, shell=True, capture_output=True, text=True
            )

            # Save full log to file for error summarizer
            log_path = os.path.abspath("maven_build_log.txt")
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
        return Agent(
            config=self.agents_config["query_writer"], verbose=True, llm=self.llm
        )

    @agent
    def code_refactor(self) -> Agent:
        return Agent(
            config=self.agents_config["code_refactor"], verbose=True, llm=self.llm
        )

    @agent
    def sonar_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["sonar_agent"], verbose=True, llm=self.llm
        )

    @agent
    def errors_summarizer(self) -> Agent:
        return Agent(
            config=self.agents_config["errors_summarizer"], verbose=True, llm=self.llm
        )

    @task
    def task1(self) -> Task:
        t = Task(config=self.tasks_config["task1"], verbose=True)
        t.description += "\n\nCONTEXT FROM PREVIOUS ATTEMPT (If any):\n{previous_errors}"
        return t

    @task
    def task2(self) -> Task:
        t = Task(config=self.tasks_config["task2"], verbose=True)
        # promemoria "anti-pigrizia" per l'LLM
        t.description += "\n\nIMPORTANT: Do NOT be lazy. You must output the full file content including all imports and unchanged methods. If you use placeholders like '// ...' the code will be broken."
        t.description += "\n\nCONTEXT FROM PREVIOUS ATTEMPT (If any):\n{previous_errors}"
        return t

    @task
    def conditional_task6(self) -> Task:
        t = Task(config=self.tasks_config["conditional_task6"], verbose=True)
        t.description += (
            "\n\nERROR CONTEXT / FAILURE DETAILS:\n{current_failure_reason}"
        )
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
            verbose=False,
        )

    def run_refactoring_cycle(self, inputs: dict):
        """
        Executes the refactoring flow with retry logic (max 4 attempts).
        Flow: Refactor -> Scan -> (If Fail: Revert -> Summarize -> Retry)
        """
        max_retries = 4
        original_issues = inputs.get("original_issues_list", [])
        project_key = inputs.get("project_key")
        project_dir = inputs.get("project_dir")
        file_path_full = inputs.get("path_class")

        # Initialize previous_errors to ensure the placeholder in Task 1/2 has a value on the first run
        inputs.setdefault("previous_errors", "None (First Attempt - No previous errors)")

        # Store the very original code to revert to if all attempts fail
        initial_code = inputs.get("code_class")

        # Track state for final decision
        last_build_failed = True
        last_issue_count = len(original_issues)
        last_successful_code = initial_code
        last_successful_issue_count = len(original_issues)
        current_issues_list = original_issues

        for attempt in range(max_retries):
            print(f"\n=== Refactoring Attempt {attempt + 1}/{max_retries} ===")

            # FASE 1: Generazione del Codice (Solo Task 1 e Task 2)
            generation_crew = Crew(
                agents=[self.query_writer(), self.code_refactor()],
                tasks=[self.task1(), self.task2()],  # Task 1: Prompt, Task 2: Code
                process=Process.sequential,
                verbose=True,
            )

            # Eseguiamo solo la generazione
            refactored_code_output = generation_crew.kickoff(inputs=inputs)

            # Estrarre la stringa del codice dall'output
            # (CrewAI restituisce un oggetto CrewOutput, convertiamolo in stringa pulita)
            raw_output = str(refactored_code_output).strip()

            # PULIZIA EXTRA: Estrazione robusta del codice dai blocchi Markdown
            # Cerca blocchi di codice delimitati da ```
            code_blocks = re.findall(
                r"```(?:[^\s]+)?\s*(.*?)```", raw_output, re.DOTALL
            )

            if code_blocks:
                # Se ci sono più blocchi, prendiamo il più lungo (probabilmente è il file intero)
                new_code_content = max(code_blocks, key=len).strip()
            else:
                # Se non ci sono backticks, assumiamo che l'intero output sia codice
                new_code_content = raw_output

            print(
                f">>> Salvataggio manuale del file ({len(new_code_content)} caratteri)..."
            )

            # SALVATAGGIO DETERMINISTICO
            try:
                with open(file_path_full, "w", encoding="utf-8") as f:
                    f.write(new_code_content)
                print("✅ File salvato correttamente via Python.")
            except Exception as e:
                print(f"❌ Errore critico nel salvataggio manuale: {e}")
                return False, attempt + 1

            # FASE 2: Scansione e Verifica (Deterministica via Python)
            print(">>> Avvio scansione SonarQube (Deterministica)...")
            
            sonar_token = os.getenv("SONAR_TOKEN")
            result_str = ""

            if not sonar_token:
                result_str = "Error: SONAR_TOKEN environment variable not set."
            else:
                cmd = f"mvn clean verify org.sonarsource.scanner.maven:sonar-maven-plugin:sonar -Dsonar.projectKey={project_key} -Dsonar.projectName={project_key} -Dsonar.host.url=http://localhost:9000 -Dsonar.token={sonar_token} -Dmaven.test.failure.ignore=true -DskipTests" #RIAGGIUNGERE TEST

                try:
                    result = subprocess.run(
                        cmd, cwd=project_dir, shell=True, capture_output=True, text=True
                    )

                    # Save full log to file for error summarizer
                    log_path = os.path.abspath("maven_build_log.txt")
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}")

                    if result.returncode != 0:
                        result_str = f"BUILD FAILURE. Check maven_build_log.txt for details.\n{result.stdout[-1000:]}"
                    else:
                        result_str = f"BUILD SUCCESS:\n{result.stdout}"
                except Exception as e:
                    result_str = f"Execution Error: {str(e)}"

            result_lower = result_str.lower()

            # Check for build failure.
            build_failed = (
                "build failure" in result_lower or "execution error" in result_lower
            )

            last_build_failed = build_failed

            failure_reason = ""
            failure_details = ""

            if build_failed:
                failure_reason = "Build Failed"
                # Retrieve full log from file
                log_path = os.path.abspath("maven_build_log.txt")
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8") as f:
                        failure_details = f.read()
                else:
                    failure_details = result_str
            else:
                # Build Success - Now Verify Issues
                print(">>> Build Successful. Verifying SonarQube issues...")

                # Save successful state
                try:
                    with open(file_path_full, "r", encoding="utf-8") as f:
                        last_successful_code = f.read()
                except Exception as e:
                    print(f"Error reading file state: {e}")

                verification_error, current_count, current_issues_list = (
                    self._verify_refactoring(
                        project_key, project_dir, file_path_full, original_issues
                    )
                )

                if current_count != -1:
                    last_issue_count = current_count
                    last_successful_issue_count = current_count

                    # CRITICAL: Update inputs['errors'] so the next agent sees the ACTUAL current issues, not the old ones
                    inputs["errors"] = (
                        f"{json.dumps(current_issues_list)}\n\n{JAVA_COLLECTION_RULES}\n\n{CUSTOM_RULES}"
                    )

                if not verification_error:
                    print(">>> Refactoring Verified! Issues resolved.")
                    return result_str, attempt + 1
                else:
                    failure_reason = "Verification Failed"
                    failure_details = verification_error

            print(
                f">>> {failure_reason}. Initiating recovery (Attempt {attempt + 1})..."
            )

            inputs["current_failure_reason"] = (
                f"{failure_reason}\n\nDETAILS:\n{failure_details}"
            )
            print(
                f"\n[DEBUG] Context sent to Errors Summarizer:\n{'-'*40}\n{inputs['current_failure_reason']}\n{'-'*40}\n"
            )

            summary = ""

            if build_failed:
                # Case 1: Build Failed -> Keep changes (broken code), update inputs['code_class'] for next attempt
                print(">>> Keeping changes despite build failure.")

                # Only run summarizer (Task 6)
                summarize_crew = Crew(
                    agents=[self.errors_summarizer()],
                    tasks=[self.conditional_task6()],
                    process=Process.sequential,
                    verbose=True,
                )
                summary = summarize_crew.kickoff(inputs=inputs)

                # Update inputs['code_class'] with the current file content so next attempt builds upon this
                try:
                    with open(file_path_full, "r", encoding="utf-8") as f:
                        current_content = f.read()
                    inputs["code_class"] = current_content
                except Exception as e:
                    print(f"Error reading updated file: {e}")

                inputs["previous_code_context"] = (
                    f"Attempt {attempt+1} failed (Build Error). Code KEPT. Fix build errors."
                )
            else:
                # Case 2: Verification Failed -> Keep changes, update inputs['code_class'] for next attempt
                print(
                    ">>> Keeping changes despite verification failure (Build passed)."
                )

                # Only run summarizer (Task 6)
                summarize_crew = Crew(
                    agents=[self.errors_summarizer()],
                    tasks=[self.conditional_task6()],
                    process=Process.sequential,
                    verbose=True,
                )
                summary = summarize_crew.kickoff(inputs=inputs)

                # Update inputs['code_class'] with the current file content so next attempt builds upon this
                try:
                    with open(file_path_full, "r", encoding="utf-8") as f:
                        current_content = f.read()
                    inputs["code_class"] = current_content
                except Exception as e:
                    print(f"Error reading updated file: {e}")

                inputs["previous_code_context"] = (
                    f"Attempt {attempt+1} failed (Verification Error). Code KEPT. Fix new issues."
                )

            # Update inputs for the next iteration
            inputs["previous_errors"] = str(summary)

            # print("\n⏳ API Rate Limit Cooldown: Waiting 60 seconds...")
            # time.sleep(60)

            # print("\n🛑 PAUSED: Press 'U' and Enter to resume next attempt...")
            # while True:
            #    if input().strip().upper() == 'U':
            #        break

        print(">>> Max retries reached. Giving up on this file.")

        code_to_restore = None
        final_issue_count = last_issue_count

        if last_build_failed:
            print(
                ">>> Last attempt failed build. Reverting to latest successful build version."
            )
            code_to_restore = last_successful_code
            final_issue_count = last_successful_issue_count

        if final_issue_count > len(original_issues):
            print(
                f">>> Final version has more issues ({final_issue_count}) than original ({len(original_issues)}). Reverting to ORIGINAL."
            )
            code_to_restore = initial_code
        elif code_to_restore:
            print(f">>> Restoring selected version (Issues: {final_issue_count}).")

        if code_to_restore:
            try:
                with open(file_path_full, "w", encoding="utf-8") as f:
                    f.write(code_to_restore)
                print(">>> Restore complete.")
            except Exception as e:
                print(f"Error restoring code: {e}")

        return False, max_retries

    def _wait_for_processing(self, project_key: str, sonar_token: str, sonar_url: str):
        """Waits for SonarQube Compute Engine to finish processing the submitted report."""
        print(">>> Waiting for SonarQube Compute Engine to finish processing...")
        api_url = f"{sonar_url}/api/ce/component"
        params = {"component": project_key}

        # Wait up to 60 seconds
        for _ in range(30):
            try:
                response = requests.get(api_url, auth=(sonar_token, ""), params=params)
                response.raise_for_status()
                data = response.json()

                # If queue is empty and no current task, processing is done
                if not data.get("queue") and not data.get("current"):
                    print(">>> SonarQube processing complete.")
                    return

                time.sleep(2)
            except Exception as e:
                print(f"Warning: Error checking CE status: {e}")
                time.sleep(2)
        print(
            ">>> Warning: Timed out waiting for SonarQube processing. Results may be stale."
        )

    def _verify_refactoring(
        self,
        project_key: str,
        project_dir: str,
        file_path_full: str,
        original_issues: list,
    ) -> Tuple[Optional[str], int, List[dict]]:
        """
        Verifies if the refactoring resolved original issues and didn't introduce new ones.
        Returns (error_message, current_issue_count, current_issues_list). error_message is None if successful.
        """
        sonar_token = os.getenv("SONAR_TOKEN")
        sonar_url = os.getenv("SONAR_HOST_URL", "http://localhost:9000")

        if not sonar_token:
            return "SONAR_TOKEN not set, cannot verify.", -1, []

        # Ensure the latest analysis is processed before querying
        self._wait_for_processing(project_key, sonar_token, sonar_url)

        # Calculate relative path for SonarQube component key
        try:
            relative_path = os.path.relpath(file_path_full, project_dir).replace(
                "\\", "/"
            )
        except ValueError:
            return (
                f"Could not determine relative path for {file_path_full} relative to {project_dir}",
                -1,
                [],
            )

        component_key = f"{project_key}:{relative_path}"

        api_url = f"{sonar_url}/api/issues/search"
        params = {"componentKeys": component_key, "resolved": "false", "ps": 500}

        try:
            response = requests.get(api_url, auth=(sonar_token, ""), params=params)
            response.raise_for_status()
            data = response.json()
            current_issues = data.get("issues", [])
            current_count = len(current_issues)
        except Exception as e:
            return f"Error querying SonarQube API: {str(e)}", -1, []

        # Check for NEW issues (Regressions) - Corresponds to "New Issues" tab in SonarQube
        try:
            params_new = {
                "componentKeys": component_key,
                "inNewCodePeriod": "true",
                "resolved": "false",
                "ps": 50,
            }
            resp_new = requests.get(api_url, auth=(sonar_token, ""), params=params_new)
            resp_new.raise_for_status()
            new_data = resp_new.json()
            total_new = new_data.get("total", 0)
            if total_new > 0:
                issues = new_data.get("issues", [])
                details = []
                for issue in issues:
                    details.append(
                        f"- Line {issue.get('line', '?')}: [{issue.get('rule', 'Unknown')}] {issue.get('message', '')}"
                    )
                return (
                    f"Refactoring introduced {total_new} NEW issues (regressions):\n"
                    + "\n".join(details),
                    current_count,
                    current_issues,
                )
        except Exception as e:
            print(f"Warning: Could not check new code period issues: {e}")

        original_rules = {issue.get("rule") for issue in original_issues}
        current_rules = {issue.get("rule") for issue in current_issues}

        # Check for new issue types (regressions)
        new_rules = current_rules - original_rules
        if new_rules:
            return (
                f"New issue types introduced: {', '.join(new_rules)}",
                current_count,
                current_issues,
            )

        # Check if total issue count decreased (heuristic for 'resolution')
        # We allow partial resolution, but count must not increase or stay same if we started with issues.
        if len(original_issues) > 0 and len(current_issues) >= len(original_issues):
            details = []
            for issue in current_issues:
                details.append(
                    f"- Line {issue.get('line', '?')}: [{issue.get('rule', 'Unknown')}] {issue.get('message', '')}"
                )
            details_str = "\n".join(details)
            return (
                f"No issues were resolved (Count: {len(original_issues)} -> {len(current_issues)}). The following issues remain:\n{details_str}",
                current_count,
                current_issues,
            )

        return None, current_count, current_issues
