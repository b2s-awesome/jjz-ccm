"""Application settings, persisted to config/settings.json between sessions."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.json"
PROFILES_DIR = ROOT / "profiles"
TEMPLATES_DIR = ROOT / "templates"
LOGS_DIR = ROOT / "logs"

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # Window / capture
    window_title: str = "Roblox"
    # Used when no window can be found (non-Windows, or manual mode): [left, top, width, height]
    manual_region: list = field(default_factory=lambda: [0, 0, 1920, 1080])
    use_manual_region: bool = False
    # Window height the templates were captured at; templates are scaled by current_height / this.
    template_reference_height: int = 1080
    default_threshold: float = 0.80

    # Safety
    dry_run: bool = True  # log inputs instead of sending them; turn off once tests pass
    emergency_stop_hotkey: str = "f8"
    pause_hotkey: str = "f7"
    max_runtime_minutes: int = 120
    max_consecutive_failures: int = 3
    retry_delay_seconds: float = 5.0
    unknown_state_timeout: float = 30.0
    require_window_focus: bool = True

    # Diagnostics
    save_screenshot_on_error: bool = True
    tesseract_cmd: str = ""  # path to tesseract.exe if not on PATH

    # Session queue: list of {"profile": id, "runs": n}
    queue: list = field(default_factory=list)
    last_profile: str = ""

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Settings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("Could not read %s (%s); using defaults", path, e)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path = SETTINGS_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
