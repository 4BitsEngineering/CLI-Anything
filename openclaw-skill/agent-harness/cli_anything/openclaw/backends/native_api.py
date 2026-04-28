"""NativeAPIBackend — executes macro steps via subprocess.

Supports these action types (configured in macro step params):

    action: run_command
    params:
      command: [inkscape, --export-filename, /tmp/out.png, input.svg]
      cwd: /optional/working/dir      # optional
      env: {KEY: value}               # optional extra env vars
      capture_stdout: true            # store stdout in output.stdout

    action: find_executable
    params:
      name: inkscape
      candidates: [inkscape, inkscape-1.0, /usr/bin/inkscape]
      install_hint: "apt install inkscape"
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any

from cli_anything.openclaw.backends.base import Backend, BackendContext, StepResult
from cli_anything.openclaw.core.macro_model import MacroStep, substitute


class NativeAPIBackend(Backend):
    """Execute a macro step by running an external command."""

    name = "native_api"
    priority = 100

    def execute(self, step: MacroStep, params: dict, context: BackendContext) -> StepResult:
        t0 = time.time()
        action = step.action

        if action == "find_executable":
            return self._find_executable(step, params, context, t0)
        elif action == "run_command":
            return self._run_command(step, params, context, t0)
        elif action == "spawn":
            return self._spawn(step, params, context, t0)
        elif action == "kill_pid":
            return self._kill_pid(step, params, context, t0)
        elif action == "sleep":
            return self._sleep(step, params, context, t0)
        else:
            return StepResult(
                success=False,
                error=f"NativeAPIBackend: unknown action '{action}'.",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

    # ── Actions ──────────────────────────────────────────────────────────

    def _find_executable(
        self, step: MacroStep, params: dict, context: BackendContext, t0: float
    ) -> StepResult:
        """Check that an executable exists; return its path."""
        step_params = substitute(step.params, params)
        exe_name = step_params.get("name", "")
        candidates: list[str] = step_params.get("candidates", [exe_name] if exe_name else [])
        install_hint: str = step_params.get("install_hint", f"Install {exe_name}")

        for candidate in candidates:
            found = shutil.which(candidate)
            if found:
                return StepResult(
                    success=True,
                    output={"executable": found, "name": candidate},
                    backend_used=self.name,
                    duration_ms=(time.time() - t0) * 1000,
                )

        return StepResult(
            success=False,
            error=(
                f"Executable not found: {exe_name}. "
                f"Tried: {candidates}. "
                f"Install with: {install_hint}"
            ),
            backend_used=self.name,
            duration_ms=(time.time() - t0) * 1000,
        )

    def _run_command(
        self, step: MacroStep, params: dict, context: BackendContext, t0: float
    ) -> StepResult:
        """Run an external command."""
        step_params = substitute(step.params, params)
        command: list[str] = step_params.get("command", [])
        if not command:
            return StepResult(
                success=False,
                error="NativeAPIBackend.run_command: 'command' param is required.",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        if isinstance(command, str):
            import shlex
            command = shlex.split(command)
        command = [str(c) for c in command]

        cwd: str = step_params.get("cwd", "")
        extra_env: dict = step_params.get("env", {})
        capture_stdout: bool = step_params.get("capture_stdout", False)

        env = os.environ.copy()
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})

        timeout_s = context.timeout_ms / 1000.0

        if context.dry_run:
            return StepResult(
                success=True,
                output={"dry_run": True, "command": command},
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=cwd or None,
                env=env,
            )
        except FileNotFoundError as exc:
            return StepResult(
                success=False,
                error=f"Command not found: {command[0]}. {exc}",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                success=False,
                error=f"Command timed out after {timeout_s:.0f}s: {command}",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        duration = (time.time() - t0) * 1000
        if result.returncode != 0:
            return StepResult(
                success=False,
                error=(
                    f"Command failed (exit {result.returncode}): {command}\n"
                    f"stderr: {result.stderr.strip()}"
                ),
                output={"returncode": result.returncode, "stderr": result.stderr},
                backend_used=self.name,
                duration_ms=duration,
            )

        output: dict[str, Any] = {"returncode": 0}
        if capture_stdout:
            output["stdout"] = result.stdout
        return StepResult(
            success=True,
            output=output,
            backend_used=self.name,
            duration_ms=duration,
        )

    def _spawn(
        self, step: MacroStep, params: dict, context: BackendContext, t0: float
    ) -> StepResult:
        """Launch a process detached and return immediately. Use when the target
        is a long-running GUI app the macro will then drive via semantic_ui.
        """
        step_params = substitute(step.params, params)
        command: list[str] = step_params.get("command", [])
        if not command:
            return StepResult(
                success=False,
                error="NativeAPIBackend.spawn: 'command' param is required.",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )
        if isinstance(command, str):
            import shlex
            command = shlex.split(command)
        command = [str(c) for c in command]

        cwd: str = step_params.get("cwd", "")
        extra_env: dict = step_params.get("env", {})
        env = os.environ.copy()
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})

        if context.dry_run:
            return StepResult(
                success=True,
                output={"dry_run": True, "command": command},
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        try:
            popen_kwargs: dict[str, Any] = {
                "cwd": cwd or None,
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
            else:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(command, **popen_kwargs)
        except FileNotFoundError as exc:
            return StepResult(
                success=False,
                error=f"Command not found: {command[0]}. {exc}",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )
        except OSError as exc:
            return StepResult(
                success=False,
                error=f"Spawn failed for {command}: {exc}",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        return StepResult(
            success=True,
            output={"pid": proc.pid, "command": command},
            backend_used=self.name,
            duration_ms=(time.time() - t0) * 1000,
        )

    def _kill_pid(
        self, step: MacroStep, params: dict, context: BackendContext, t0: float
    ) -> StepResult:
        """Terminate a process by PID. Idempotent — already-gone is success.

        Accepts either:
          - pid: <int>            literal pid to kill
          - from_step: <step_id>  read pid from a previous step's output['pid']
        """
        step_params = substitute(step.params, params)
        pid = step_params.get("pid")
        from_step = step_params.get("from_step")

        if pid is None and from_step:
            for prev in context.previous_results:
                if getattr(prev, "step_id", "") == from_step:
                    pid = (prev.output or {}).get("pid")
                    break
            if pid is None:
                return StepResult(
                    success=False,
                    error=f"NativeAPIBackend.kill_pid: no pid found for from_step='{from_step}'.",
                    backend_used=self.name,
                    duration_ms=(time.time() - t0) * 1000,
                )

        if pid is None:
            return StepResult(
                success=False,
                error="NativeAPIBackend.kill_pid: 'pid' or 'from_step' required.",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return StepResult(
                success=False,
                error=f"NativeAPIBackend.kill_pid: invalid pid '{pid}'.",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        if context.dry_run:
            return StepResult(
                success=True,
                output={"dry_run": True, "pid": pid},
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        try:
            if os.name == "nt":
                # /T also kills child processes; /F forces.
                proc = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                )
                if proc.returncode != 0:
                    err = (proc.stderr or proc.stdout or b"").decode(errors="ignore")
                    # 128 = process not found → idempotent success.
                    if proc.returncode == 128 or "not found" in err.lower():
                        return StepResult(
                            success=True,
                            output={"pid": pid, "already_exited": True},
                            backend_used=self.name,
                            duration_ms=(time.time() - t0) * 1000,
                        )
                    return StepResult(
                        success=False,
                        error=f"taskkill rc={proc.returncode}: {err.strip()}",
                        backend_used=self.name,
                        duration_ms=(time.time() - t0) * 1000,
                    )
            else:
                import signal as _signal
                try:
                    os.kill(pid, _signal.SIGTERM)
                except ProcessLookupError:
                    return StepResult(
                        success=True,
                        output={"pid": pid, "already_exited": True},
                        backend_used=self.name,
                        duration_ms=(time.time() - t0) * 1000,
                    )
        except subprocess.TimeoutExpired:
            return StepResult(
                success=False,
                error="taskkill timed out after 5s",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )
        except Exception as exc:
            return StepResult(
                success=False,
                error=f"NativeAPIBackend.kill_pid: {exc}",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        return StepResult(
            success=True,
            output={"pid": pid, "killed": True},
            backend_used=self.name,
            duration_ms=(time.time() - t0) * 1000,
        )

    def _sleep(
        self, step: MacroStep, params: dict, context: BackendContext, t0: float
    ) -> StepResult:
        """In-process sleep — does not spawn a child, so it never steals focus."""
        step_params = substitute(step.params, params)
        ms = int(step_params.get("ms", 500))
        if context.dry_run:
            return StepResult(
                success=True,
                output={"dry_run": True, "ms": ms},
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )
        time.sleep(ms / 1000.0)
        return StepResult(
            success=True,
            output={"slept_ms": ms},
            backend_used=self.name,
            duration_ms=(time.time() - t0) * 1000,
        )
