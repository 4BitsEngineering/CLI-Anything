"""SemanticUIBackend — drive applications via accessibility and keyboard shortcuts.

Linux uses xdotool; Windows uses pywinauto (UIA + send_keys); macOS still stubs.

Example macro step:

    - backend: semantic_ui
      action: menu_click
      params:
        menu_path: [File, Export As, PNG]

    - backend: semantic_ui
      action: shortcut
      params:
        keys: ctrl+shift+e

    - backend: semantic_ui
      action: wait_for_window
      params:
        title_contains: Export
        timeout_ms: 5000
"""

from __future__ import annotations

import platform
import re
import time

from cli_anything.openclaw.backends.base import Backend, BackendContext, StepResult
from cli_anything.openclaw.core.macro_model import MacroStep, substitute


# Last window matched by wait_for_window. Used to restore foreground before
# subsequent send_keys actions, since other processes (or our own subprocess
# children) can steal focus between steps.
_LAST_FOREGROUND: dict = {"hwnd": None, "title": ""}


class SemanticUIBackend(Backend):
    """Drive applications through semantic (accessibility) controls."""

    name = "semantic_ui"
    priority = 50

    def execute(self, step: MacroStep, params: dict, context: BackendContext) -> StepResult:
        t0 = time.time()
        action = step.action
        step_params = substitute(step.params, params)

        if context.dry_run:
            return StepResult(
                success=True,
                output={"dry_run": True, "action": action},
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        dispatch = {
            "shortcut": self._shortcut,
            "menu_click": self._menu_click,
            "wait_for_window": self._wait_for_window,
            "button_click": self._button_click,
            "type_text": self._type_text,
        }

        handler = dispatch.get(action)
        if handler is None:
            return StepResult(
                success=False,
                error=f"SemanticUIBackend: unknown action '{action}'.",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

        try:
            output = handler(step_params)
            return StepResult(
                success=True,
                output=output or {},
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )
        except NotImplementedError as exc:
            return StepResult(
                success=False,
                error=str(exc),
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )
        except Exception as exc:
            return StepResult(
                success=False,
                error=f"SemanticUIBackend.{action}: {exc}",
                backend_used=self.name,
                duration_ms=(time.time() - t0) * 1000,
            )

    def is_available(self) -> bool:
        if platform.system() == "Linux":
            try:
                import pyatspi  # noqa: F401
                return True
            except ImportError:
                pass
        elif platform.system() == "Windows":
            try:
                import pywinauto  # noqa: F401
                return True
            except ImportError:
                pass
        elif platform.system() == "Darwin":
            try:
                import ApplicationServices  # noqa: F401
                return True
            except ImportError:
                pass
        return False

    # ── Actions ──────────────────────────────────────────────────────────

    def _shortcut(self, p: dict) -> dict:
        keys: str = p.get("keys", "")
        if not keys:
            raise ValueError("shortcut action requires 'keys' param.")

        sys = platform.system()
        if sys == "Linux":
            return self._shortcut_xdotool(keys)
        elif sys == "Windows":
            return self._shortcut_win32(keys)
        elif sys == "Darwin":
            return self._shortcut_macos(keys)
        raise NotImplementedError(
            f"SemanticUIBackend.shortcut: not yet implemented for platform {sys}. "
            "Consider using native_api backend instead."
        )

    def _shortcut_xdotool(self, keys: str) -> dict:
        import shutil
        if not shutil.which("xdotool"):
            raise NotImplementedError(
                "xdotool not found. Install with: apt install xdotool"
            )
        import subprocess
        xdg_keys = keys.replace("+", " ")
        subprocess.run(["xdotool", "key", xdg_keys], check=True)
        return {"keys": keys, "method": "xdotool"}

    def _shortcut_win32(self, keys: str) -> dict:
        sk = _to_send_keys(keys)
        return _send_to_target_window(sk, pause=0.02, label=f"shortcut:{keys}")

    def _shortcut_macos(self, keys: str) -> dict:
        raise NotImplementedError(
            "SemanticUIBackend.shortcut: macOS requires pyobjc. "
            "pip install pyobjc-framework-Quartz"
        )

    def _menu_click(self, p: dict) -> dict:
        menu_path: list = p.get("menu_path", [])
        if platform.system() == "Windows":
            from pywinauto import Desktop
            title_contains: str = p.get("title_contains", "")
            timeout_ms: int = int(p.get("timeout_ms", 5000))
            t_end = time.time() + (timeout_ms / 1000.0)
            last_exc: Exception | None = None
            while time.time() < t_end:
                try:
                    desk = Desktop(backend="uia")
                    win = desk.window(title_re=f".*{re.escape(title_contains)}.*") if title_contains else desk.window(active_only=True)
                    win.wait("exists ready", timeout=1.0)
                    win.menu_select(" -> ".join(menu_path))
                    return {"menu_path": menu_path, "method": "pywinauto"}
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.3)
            raise RuntimeError(f"menu_click failed: {last_exc}")
        raise NotImplementedError(
            f"SemanticUIBackend.menu_click: not yet implemented on {platform.system()}. "
            f"Menu path: {menu_path}."
        )

    def _wait_for_window(self, p: dict) -> dict:
        title_contains: str = p.get("title_contains", "")
        title_regex: str = p.get("title_regex", "")
        timeout_ms: int = int(p.get("timeout_ms", 5000))
        if not title_contains and not title_regex:
            raise ValueError("wait_for_window requires 'title_contains' or 'title_regex'.")
        if title_regex:
            pattern = re.compile(title_regex, re.IGNORECASE)
            descr = f"regex={title_regex!r}"
        else:
            pattern = re.compile(re.escape(title_contains), re.IGNORECASE)
            descr = f"contains={title_contains!r}"
        if platform.system() == "Windows":
            from pywinauto import Desktop
            t_end = time.time() + (timeout_ms / 1000.0)
            while time.time() < t_end:
                try:
                    for backend in ("uia", "win32"):
                        desk = Desktop(backend=backend)
                        for w in desk.windows():
                            try:
                                title = w.window_text() or ""
                            except Exception:
                                continue
                            if pattern.search(title):
                                hwnd = None
                                try:
                                    hwnd = w.handle
                                except Exception:
                                    pass
                                _bring_to_front(hwnd, title)
                                _LAST_FOREGROUND["hwnd"] = hwnd
                                _LAST_FOREGROUND["title"] = title
                                return {
                                    "title": title,
                                    "backend": backend,
                                    "matched": descr,
                                    "hwnd": hwnd,
                                }
                except Exception:
                    pass
                time.sleep(0.25)
            raise TimeoutError(
                f"wait_for_window: no window matching {descr} appeared within {timeout_ms} ms."
            )
        raise NotImplementedError(
            f"SemanticUIBackend.wait_for_window: not yet implemented on {platform.system()}. "
            f"Looking for: {descr}."
        )

    def _button_click(self, p: dict) -> dict:
        if platform.system() == "Windows":
            from pywinauto import Desktop
            label: str = p.get("label", "")
            window_title_contains: str = p.get("window_title_contains", "")
            timeout_ms: int = int(p.get("timeout_ms", 5000))
            if not label:
                raise ValueError("button_click requires 'label'.")
            t_end = time.time() + (timeout_ms / 1000.0)
            last_exc: Exception | None = None
            while time.time() < t_end:
                try:
                    desk = Desktop(backend="uia")
                    if window_title_contains:
                        win = desk.window(title_re=f".*{re.escape(window_title_contains)}.*")
                    else:
                        win = desk.window(active_only=True)
                    win.wait("exists ready", timeout=1.0)
                    btn = win.child_window(title=label, control_type="Button")
                    btn.wait("exists enabled", timeout=1.0)
                    btn.click_input()
                    return {"label": label, "method": "pywinauto"}
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.3)
            raise RuntimeError(f"button_click failed: {last_exc}")
        raise NotImplementedError(
            f"SemanticUIBackend.button_click: not yet implemented on {platform.system()}."
        )

    def _type_text(self, p: dict) -> dict:
        text: str = p.get("text", "")
        if not text:
            raise ValueError("type_text action requires 'text' param.")
        sys = platform.system()
        if sys == "Linux":
            import shutil
            if not shutil.which("xdotool"):
                raise NotImplementedError("xdotool not found. apt install xdotool")
            import subprocess
            subprocess.run(["xdotool", "type", "--clearmodifiers", text], check=True)
            return {"text_len": len(text), "method": "xdotool"}
        elif sys == "Windows":
            return _send_to_target_window(
                _escape_send_keys(text),
                pause=0.005,
                with_spaces=True,
                with_tabs=True,
                with_newlines=True,
                label=f"text_len:{len(text)}",
            )
        raise NotImplementedError(
            f"SemanticUIBackend.type_text: not yet implemented for {sys}."
        )


# ── pywinauto send_keys helpers ──────────────────────────────────────────────

# Modifier map for "ctrl+alt+s" → "^%s"
_MOD_MAP = {
    "ctrl": "^",
    "control": "^",
    "alt": "%",
    "shift": "+",
    "win": "{VK_LWIN}",
    "cmd": "{VK_LWIN}",
}

# Named key map for shortcuts ("enter", "tab", ...)
_NAMED_KEY_MAP = {
    "enter": "{ENTER}",
    "return": "{ENTER}",
    "tab": "{TAB}",
    "esc": "{ESC}",
    "escape": "{ESC}",
    "space": " ",
    "backspace": "{BACKSPACE}",
    "delete": "{DELETE}",
    "del": "{DELETE}",
    "home": "{HOME}",
    "end": "{END}",
    "pageup": "{PGUP}",
    "pagedown": "{PGDN}",
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
    "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
    "f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
    "f9": "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",
}


def _to_send_keys(spec: str) -> str:
    """Translate shortcut specs like 'ctrl+alt+s' or 'enter' into pywinauto send_keys syntax."""
    parts = [p.strip().lower() for p in spec.split("+")]
    mods = []
    final = ""
    for part in parts:
        if part in _MOD_MAP:
            mods.append(_MOD_MAP[part])
        elif part in _NAMED_KEY_MAP:
            final = _NAMED_KEY_MAP[part]
        else:
            # Single character or unknown — pass through. Escape send_keys metas.
            final = _escape_send_keys(part)
    return "".join(mods) + final


# Characters that have special meaning in send_keys and need to be wrapped in {}
_SK_SPECIALS = set("+^%~(){}[]")


def _escape_send_keys(text: str) -> str:
    out = []
    for ch in text:
        if ch == "\n":
            out.append("{ENTER}")
        elif ch == "\t":
            out.append("{TAB}")
        elif ch in _SK_SPECIALS:
            out.append("{" + ch + "}")
        else:
            out.append(ch)
    return "".join(out)


# ── Foreground window management (Windows) ───────────────────────────────────
#
# Without this, send_keys / type_text deliver to whatever window happens to be
# foregrounded when the call lands — frequently the console that launched
# Python rather than the GUI app the macro is driving. We resurrect the last
# matched window before each keystroke burst.

def _bring_to_front(hwnd, title: str) -> bool:
    if platform.system() != "Windows" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        # Restore if minimised, then bring to front.
        if user32.IsIconic(wintypes.HWND(hwnd)):
            user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        # AttachThreadInput dance is needed because Windows blocks raw
        # SetForegroundWindow calls from background threads / consoles.
        target_thread = user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = False
        if target_thread and target_thread != current_thread:
            attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
        # Tiny settle so the WM has time to repaint focus.
        time.sleep(0.05)
        return True
    except Exception:
        return False


def _restore_last_foreground() -> dict:
    hwnd = _LAST_FOREGROUND.get("hwnd")
    title = _LAST_FOREGROUND.get("title", "")
    if not hwnd:
        return {"restored": False, "reason": "no prior wait_for_window match"}
    ok = _bring_to_front(hwnd, title)
    return {"restored": ok, "title": title, "hwnd": hwnd}


def _send_to_target_window(send_keys_str: str, pause: float, label: str, **kw) -> dict:
    """Send keystrokes to the last matched window via pywinauto, not to the
    global foreground. This avoids the focus-steal class of failures: even when
    Windows refuses our SetForegroundWindow call, pywinauto's set_focus +
    type_keys path actually delivers the input to the target HWND.
    """
    from pywinauto import Application  # noqa
    from pywinauto.keyboard import send_keys as global_send_keys
    hwnd = _LAST_FOREGROUND.get("hwnd")
    title = _LAST_FOREGROUND.get("title", "")
    info: dict = {"label": label, "title": title, "hwnd": hwnd}
    if hwnd:
        try:
            app = Application(backend="win32").connect(handle=hwnd)
            win = app.window(handle=hwnd)
            try:
                win.set_focus()
            except Exception as exc:
                info["set_focus_error"] = str(exc)
            time.sleep(0.05)
            win.type_keys(
                send_keys_str,
                pause=pause,
                with_spaces=kw.get("with_spaces", True),
                with_tabs=kw.get("with_tabs", True),
                with_newlines=kw.get("with_newlines", True),
                set_foreground=True,
            )
            info["delivery"] = "pywinauto.window.type_keys"
            return info
        except Exception as exc:
            info["pywinauto_error"] = str(exc)
            # Fall through to global send_keys.
    # Fallback: bring to front + global send_keys.
    info["delivery"] = "global_send_keys"
    info["foreground"] = _restore_last_foreground() if hwnd else {"restored": False}
    global_send_keys(send_keys_str, pause=pause)
    return info
