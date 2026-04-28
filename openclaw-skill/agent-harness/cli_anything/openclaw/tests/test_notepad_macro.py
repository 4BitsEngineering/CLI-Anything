"""Tests for the notepad_create_and_save macro.

Two layers:

* TestMacroStructure — load the YAML, verify it parses and has the expected
  shape. Runs anywhere (no GUI, no Notepad++, no Windows). Catches schema
  drift, missing kill_pid step, broken from_step refs, etc.

* TestMacroLive — actually drive Notepad++ via pywinauto. Marked `gui`,
  skipped by default. Run with `pytest --run-gui` (or `RUN_GUI=1`) on a
  Windows desktop with Notepad++ installed.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest


# ── Paths ────────────────────────────────────────────────────────────────────

# tests/test_notepad_macro.py → openclaw/tests/ → openclaw/macro_definitions/notepad/
HERE = Path(__file__).resolve().parent
MACRO_DIR = HERE.parent / "macro_definitions" / "notepad"
MACRO_FILE = MACRO_DIR / "notepad_create_and_save.yaml"


# ── Layer 1: structural (CI-safe) ────────────────────────────────────────────

class TestMacroStructure:
    """Parse the YAML and assert the shape we expect. No subprocess, no GUI."""

    def test_macro_file_exists(self):
        assert MACRO_FILE.is_file(), f"Macro YAML not found: {MACRO_FILE}"

    def test_macro_loads_via_registry(self):
        from cli_anything.openclaw.core.registry import MacroRegistry
        reg = MacroRegistry(str(MACRO_DIR))
        macro = reg.load("notepad_create_and_save")
        assert macro is not None
        assert macro.name == "notepad_create_and_save"

    def test_required_parameters(self):
        from cli_anything.openclaw.core.registry import MacroRegistry
        macro = MacroRegistry(str(MACRO_DIR)).load("notepad_create_and_save")
        # MacroDefinition.parameters is a dict keyed by param name.
        param_names = set(macro.parameters.keys())
        assert "output_path" in param_names
        assert "content" in param_names
        # notepad_exe is optional with a default
        assert "notepad_exe" in param_names

    def test_step_sequence_includes_close(self):
        """The macro must end by killing the spawned PID, otherwise Notepad++
        leaks across runs and we waste hosts of file handles."""
        from cli_anything.openclaw.core.registry import MacroRegistry
        macro = MacroRegistry(str(MACRO_DIR)).load("notepad_create_and_save")
        step_ids = [s.id for s in macro.steps]
        # Must spawn first so we have a pid to kill
        assert "launch" in step_ids
        assert "close_notepad" in step_ids
        assert step_ids.index("launch") < step_ids.index("close_notepad")

    def test_close_step_references_launch(self):
        """close_notepad must use from_step: launch (not a hardcoded pid)."""
        from cli_anything.openclaw.core.registry import MacroRegistry
        macro = MacroRegistry(str(MACRO_DIR)).load("notepad_create_and_save")
        close = next(s for s in macro.steps if s.id == "close_notepad")
        assert close.backend == "native_api"
        assert close.action == "kill_pid"
        assert close.params.get("from_step") == "launch", (
            f"close_notepad must reference launch step, got: {close.params}"
        )

    def test_postconditions_validate_real_outcome(self):
        """Postconditions must verify file content (not just existence) so a
        macro that types into the wrong window fails loudly instead of
        reporting success."""
        from cli_anything.openclaw.core.registry import MacroRegistry
        macro = MacroRegistry(str(MACRO_DIR)).load("notepad_create_and_save")
        post_types = {c.type for c in macro.postconditions}
        assert "file_exists" in post_types
        assert "file_contains" in post_types


# ── Layer 2: live (Windows + Notepad++ required) ─────────────────────────────

NOTEPAD_PATH = r"C:\Program Files\Notepad++\notepad++.exe"


def _platform_supported() -> bool:
    return sys.platform == "win32" and Path(NOTEPAD_PATH).is_file()


@pytest.mark.gui
@pytest.mark.skipif(not _platform_supported(), reason="needs Windows + Notepad++")
class TestMacroLive:
    """Actually run the macro. Skipped unless --run-gui / RUN_GUI=1."""

    def _count_notepad_processes(self) -> int:
        import subprocess
        # tasklist exits 0 even when nothing matches; parse stdout for the exe name.
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq notepad++.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return 0
        # When no matches tasklist prints "INFO: No tasks are running ...".
        return sum(1 for line in proc.stdout.splitlines() if "notepad++.exe" in line.lower())

    def test_create_file_and_close_notepad(self, tmp_path):
        from cli_anything.openclaw.core.registry import MacroRegistry
        from cli_anything.openclaw.core.runtime import MacroRuntime

        # Use Public to avoid path-quoting headaches with the user's profile.
        out = Path(r"C:\Users\Public") / f"notepad_test_{os.getpid()}_{int(time.time())}.txt"
        if out.exists():
            out.unlink()
        content = "hello from notepad-control test"

        before = self._count_notepad_processes()
        rt = MacroRuntime(registry=MacroRegistry(str(MACRO_DIR)))
        try:
            result = rt.execute("notepad_create_and_save", {
                "output_path": str(out),
                "content": content,
            })

            # 1. macro reports success
            assert result.success, f"macro failed: {result.error}"

            # 2. file exists with the exact content the agent typed
            assert out.is_file(), f"output not created: {out}"
            assert out.read_text(encoding="utf-8").strip() == content

            # 3. the close_notepad step ran successfully (cleaned up its instance)
            close_results = [r for r in result.step_results if r.step_id == "close_notepad"]
            assert close_results, "close_notepad step missing from results"
            assert close_results[0].success, f"close_notepad failed: {close_results[0].error}"

            # 4. process count didn't grow (closes whatever it spawned, leaves
            # any other instances the user had open untouched)
            time.sleep(1.0)  # let TerminateProcess settle
            after = self._count_notepad_processes()
            assert after <= before, (
                f"notepad++ leaked: before={before} after={after} — close_notepad didn't kill the spawned pid"
            )
        finally:
            if out.exists():
                out.unlink()
