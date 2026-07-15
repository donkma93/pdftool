"""Ensure only one PDFTOOL process runs at a time."""

from __future__ import annotations

import atexit
import os
import sys
import time
from pathlib import Path

from .version import APP_NAME

LOCK_FILE_NAME = "pdftool.instance.lock"
MUTEX_NAME = "Local\\PDFTOOL_SingleInstance_Mutex_v1"


def lock_file_path() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base / LOCK_FILE_NAME


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return int(raw.splitlines()[0].strip())
    except (OSError, ValueError):
        return None


def _write_lock_pid(path: Path, pid: int) -> None:
    path.write_text(f"{pid}\n", encoding="utf-8")


def _terminate_pid(pid: int, timeout_seconds: float = 5.0) -> bool:
    """Close another process; force-kill on Windows if it hangs."""
    if pid <= 0 or pid == os.getpid() or not _pid_is_running(pid):
        return True

    if os.name == "nt":
        import subprocess

        subprocess.run(
            ["taskkill", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not _pid_is_running(pid):
                return True
            time.sleep(0.15)
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not _pid_is_running(pid):
                return True
            time.sleep(0.1)
        return not _pid_is_running(pid)

    try:
        import signal

        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _pid_is_running(pid)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.15)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return not _pid_is_running(pid)


class SingleInstanceGuard:
    """
    Single-instance guard.

    On Windows: named mutex (kernel-level, works across processes).
    PID file under %APPDATA%/PDFTOOL is used to identify/close the other process.
    """

    def __init__(self):
        self.path = lock_file_path()
        self.owned = False
        self._mutex = None
        self._lock_handle = None

    def existing_pid(self) -> int | None:
        pid = _read_lock_pid(self.path)
        if pid is None or pid == os.getpid():
            return None
        if not _pid_is_running(pid):
            return None
        return pid

    def try_acquire(self) -> bool:
        if not self._acquire_mutex():
            return False
        self._write_pid_file()
        self.owned = True
        atexit.register(self.release)
        return True

    def acquire_replacing(self, other_pid: int) -> bool:
        if other_pid and _pid_is_running(other_pid):
            if not _terminate_pid(other_pid):
                return False
        # Old process should release mutex on exit; wait briefly then take it.
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if self.try_acquire():
                return True
            time.sleep(0.2)
        return False

    def _acquire_mutex(self) -> bool:
        if os.name == "nt":
            return self._acquire_windows_mutex()
        return self._acquire_file_lock()

    def _acquire_windows_mutex(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        ERROR_ALREADY_EXISTS = 183
        # Request initial ownership of a new mutex.
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if not handle:
            return False
        already = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        if already:
            # Mutex exists — another instance (or abandoned). Try non-blocking wait.
            WAIT_OBJECT_0 = 0
            WAIT_ABANDONED = 0x00000080
            result = kernel32.WaitForSingleObject(handle, 0)
            if result not in (WAIT_OBJECT_0, WAIT_ABANDONED):
                kernel32.CloseHandle(handle)
                return False
        self._mutex = handle
        return True

    def _acquire_file_lock(self) -> bool:
        """Fallback for non-Windows: exclusive PID file with flock-like msvcrt/fcntl."""
        other = self.existing_pid()
        if other is not None:
            return False
        try:
            self._lock_handle = open(self.path, "a+", encoding="utf-8")
            if sys.platform == "win32":
                import msvcrt

                self._lock_handle.seek(0)
                msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._close_lock_handle()
            return False

    def _write_pid_file(self):
        try:
            _write_lock_pid(self.path, os.getpid())
        except OSError:
            pass

    def _close_lock_handle(self):
        if self._lock_handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                try:
                    self._lock_handle.seek(0)
                    msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            self._lock_handle.close()
        except OSError:
            pass
        self._lock_handle = None

    def release(self):
        if not self.owned and self._mutex is None and self._lock_handle is None:
            return
        self._close_lock_handle()
        if self._mutex is not None and os.name == "nt":
            import ctypes

            try:
                ctypes.windll.kernel32.ReleaseMutex(self._mutex)
            except Exception:
                pass
            try:
                ctypes.windll.kernel32.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None
        if self.owned:
            try:
                current = _read_lock_pid(self.path)
                if current in (None, os.getpid()) and self.path.exists():
                    self.path.unlink()
            except OSError:
                pass
        self.owned = False


def ensure_single_instance() -> SingleInstanceGuard | None:
    """
    Acquire the single-instance lock.

    If another live instance exists, ask whether to close it and open this one.
    Returns the guard on success, or None if this process should exit.
    """
    guard = SingleInstanceGuard()
    if guard.try_acquire():
        return guard

    other_pid = guard.existing_pid()
    if other_pid is None:
        # Mutex held but PID file missing/stale — still treat as busy.
        choice = _ask_replace_instance(None)
        if choice is True:
            # Cannot target a PID; user must close manually.
            _show_error(
                "Phát hiện chương trình khác đang giữ phiên bản duy nhất,\n"
                "nhưng không xác định được PID.\n\n"
                "Hãy đóng cửa sổ PDFTOOL đang mở (hoặc kết thúc trong Task Manager)\n"
                "rồi mở lại."
            )
            return None
        if choice is False:
            _show_info("PDF Visual Text Editor đang chạy.\nChỉ được mở một cửa sổ tại một thời điểm.")
        return None

    choice = _ask_replace_instance(other_pid)
    if choice is True:
        if guard.acquire_replacing(other_pid):
            return guard
        _show_error(
            f"Không đóng được chương trình cũ (PID {other_pid}).\n"
            "Hãy đóng thủ công trong Task Manager rồi mở lại."
        )
        return None
    if choice is False:
        _show_info(
            f"PDF Visual Text Editor đang chạy (PID {other_pid}).\n"
            "Chỉ được mở một cửa sổ tại một thời điểm."
        )
    return None


def _ask_replace_instance(other_pid: int | None) -> bool | None:
    """
    Returns:
      True  -> close old instance and continue
      False -> keep old instance, abort new launch
      None  -> user dismissed dialog
    """
    pid_text = f" (PID {other_pid})" if other_pid else ""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        print(
            f"[{APP_NAME}] Đang có instance khác chạy{pid_text}. "
            "Đóng instance cũ trước khi mở mới.",
            file=sys.stderr,
        )
        return False

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        if other_pid:
            result = messagebox.askyesnocancel(
                f"{APP_NAME} — đã có chương trình đang chạy",
                f"Đang có một cửa sổ {APP_NAME} khác đang mở{pid_text}.\n\n"
                "Không thể chạy song song 2 chương trình.\n\n"
                "• Yes: đóng chương trình cũ và mở cái mới\n"
                "• No: giữ chương trình cũ, không mở thêm\n"
                "• Cancel: hủy",
                icon=messagebox.WARNING,
                default=messagebox.NO,
            )
        else:
            result = messagebox.askokcancel(
                f"{APP_NAME} — đã có chương trình đang chạy",
                f"Đang có một cửa sổ {APP_NAME} khác đang mở.\n\n"
                "Không thể chạy song song 2 chương trình.\n"
                "Hãy đóng cửa sổ cũ trước.",
                icon=messagebox.WARNING,
            )
            # Map OK/Cancel to False/None so we don't try a blind kill.
            return False if result else None
        return result
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def _show_info(message: str):
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(f"{APP_NAME}", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def _show_error(message: str):
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(f"{APP_NAME}", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)
