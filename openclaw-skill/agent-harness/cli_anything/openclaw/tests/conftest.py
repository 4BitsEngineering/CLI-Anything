"""Pytest config for the openclaw agent-harness suite.

Registers the `gui` marker used to gate tests that drive a real desktop
(Notepad++, file dialogs, etc.). Those tests need an unlocked Windows
session and the target apps installed — they cannot run in CI.

Run only the headless suite (default):
    pytest

Run everything including GUI tests (local Windows machine):
    pytest --run-gui
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-gui",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.gui (require a real desktop session).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "gui: test drives a real GUI app (skipped unless --run-gui or RUN_GUI=1).",
    )


def pytest_collection_modifyitems(config, items):
    import os
    run_gui = config.getoption("--run-gui") or os.environ.get("RUN_GUI") == "1"
    if run_gui:
        return
    skip_gui = pytest.mark.skip(reason="GUI test (requires desktop session — pass --run-gui or RUN_GUI=1).")
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip_gui)
