"""Windows branding: AppUserModelID + Start Menu shortcut with custom icon."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .icons import app_icon_path, project_root
from .version import APP_NAME, APP_TITLE, APP_VERSION


# Stable ID so Windows does not group this app under python.exe on the taskbar.
APP_USER_MODEL_ID = f"donpv.{APP_NAME}.editor"


def set_windows_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """
    Must run before any Tk window is created.
    Allows Windows to treat PDFTOOL as its own app (custom taskbar icon).
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _pythonw_path() -> Path:
    exe = Path(sys.executable)
    # Prefer pythonw.exe so no console flashes when launching from shortcut.
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.is_file():
            return candidate
    return exe


def launch_target() -> tuple[str, str, str]:
    """
    Returns (target_path, arguments, working_directory) for a shortcut.
    """
    if getattr(sys, "frozen", False):
        target = str(Path(sys.executable).resolve())
        return target, "", str(Path(sys.executable).resolve().parent)

    root = project_root()
    script = root / "pdf_editor.py"
    pythonw = _pythonw_path()
    return str(pythonw), f'"{script}"', str(root)


def ensure_start_menu_shortcut() -> Path | None:
    """
    Create/update Start Menu shortcut with PDFTOOL icon.
    Launching via this shortcut + AUMID keeps the custom taskbar icon.
    """
    if not sys.platform.startswith("win"):
        return None

    ico = app_icon_path()
    if ico is None:
        return None

    try:
        import winreg  # noqa: F401 — presence check on Windows
        from win32com.client import Dispatch  # type: ignore
    except Exception:
        # Fallback without pywin32: write a .url is weak; use WScript via PowerShell-like COM.
        return _ensure_shortcut_via_wscript(ico)

    try:
        programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        programs.mkdir(parents=True, exist_ok=True)
        shortcut_path = programs / f"{APP_NAME}.lnk"
        target, args, cwd = launch_target()

        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(shortcut_path))
        shortcut.Targetpath = target
        shortcut.Arguments = args
        shortcut.WorkingDirectory = cwd
        shortcut.IconLocation = f"{ico},0"
        shortcut.Description = f"{APP_TITLE} v{APP_VERSION}"
        shortcut.Save()
        return shortcut_path
    except Exception:
        return _ensure_shortcut_via_wscript(ico)


def _ensure_shortcut_via_wscript(ico: Path) -> Path | None:
    """Create shortcut using built-in WScript.Shell COM (no pywin32 required)."""
    try:
        import subprocess

        programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        programs.mkdir(parents=True, exist_ok=True)
        shortcut_path = programs / f"{APP_NAME}.lnk"
        target, args, cwd = launch_target()

        # Escape for VBScript strings
        def vbs_escape(value: str) -> str:
            return value.replace('"', '""')

        vbs = f'''
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{vbs_escape(str(shortcut_path))}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{vbs_escape(target)}"
oLink.Arguments = "{vbs_escape(args)}"
oLink.WorkingDirectory = "{vbs_escape(cwd)}"
oLink.IconLocation = "{vbs_escape(str(ico))},0"
oLink.Description = "{vbs_escape(APP_TITLE)}"
oLink.Save
'''
        vbs_file = Path(os.environ.get("TEMP", str(Path.home()))) / "pdftool_make_shortcut.vbs"
        vbs_file.write_text(vbs, encoding="utf-8")
        subprocess.run(["cscript", "//Nologo", str(vbs_file)], check=False, capture_output=True)
        try:
            vbs_file.unlink()
        except OSError:
            pass
        return shortcut_path if shortcut_path.is_file() else None
    except Exception:
        return None
