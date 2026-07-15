"""Persistent application settings for PDFTOOL."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .version import APP_NAME

DEFAULT_SETTINGS = {
    "default_zoom": 1.35,
    "zoom_step": 0.15,
    "nudge_step": 1.0,
    "nudge_step_shift": 10.0,
    "nudge_step_ctrl": 0.5,
    "fit_on_open": False,
    "default_font": "Arial",
    "max_recent_files": 5,
    "history_max_steps": 60,
    "confirm_replace_all": True,
    "page_margin": 24,
    "check_updates_on_start": False,
}


@dataclass
class AppSettings:
    default_zoom: float = 1.35
    zoom_step: float = 0.15
    nudge_step: float = 1.0
    nudge_step_shift: float = 10.0
    nudge_step_ctrl: float = 0.5
    fit_on_open: bool = False
    default_font: str = "Arial"
    max_recent_files: int = 5
    history_max_steps: int = 60
    confirm_replace_all: bool = True
    page_margin: int = 24
    check_updates_on_start: bool = False

    def clamp(self) -> "AppSettings":
        self.default_zoom = float(max(0.5, min(3.0, self.default_zoom)))
        self.zoom_step = float(max(0.05, min(1.0, self.zoom_step)))
        self.nudge_step = float(max(0.1, min(50.0, self.nudge_step)))
        self.nudge_step_shift = float(max(1.0, min(100.0, self.nudge_step_shift)))
        self.nudge_step_ctrl = float(max(0.1, min(10.0, self.nudge_step_ctrl)))
        self.max_recent_files = int(max(1, min(20, self.max_recent_files)))
        self.history_max_steps = int(max(5, min(200, self.history_max_steps)))
        self.page_margin = int(max(8, min(80, self.page_margin)))
        self.default_font = (self.default_font or "Arial").strip() or "Arial"
        self.fit_on_open = bool(self.fit_on_open)
        self.confirm_replace_all = bool(self.confirm_replace_all)
        self.check_updates_on_start = bool(self.check_updates_on_start)
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "AppSettings":
        raw = deepcopy(DEFAULT_SETTINGS)
        if isinstance(data, dict):
            for key in raw:
                if key in data:
                    raw[key] = data[key]
        # Only known fields
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in raw.items() if k in known}
        return cls(**kwargs).clamp()


def settings_dir() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def load_settings() -> AppSettings:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AppSettings.from_dict(data if isinstance(data, dict) else None)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return AppSettings().clamp()


def save_settings(settings: AppSettings) -> None:
    settings = settings.clamp()
    path = settings_path()
    path.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
