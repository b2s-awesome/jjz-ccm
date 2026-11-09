"""Raid farming profiles: JSON files in profiles/, merged over defaults and validated.

Profiles are plain dicts so the GUI's JSON editor and new game updates need no code
changes. See docs/ADDING_RAIDS.md for the full schema.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path

from config.settings import PROFILES_DIR

log = logging.getLogger(__name__)

OBJECTIVES = ["player_exp", "awakened_exp", "material", "crate", "efficiency"]
STEP_ACTIONS = {"key", "click", "click_template", "wait_template", "wait_gone", "wait"}
REWARD_KEYS = ["player_exp", "awakened_exp", "yen", "lumen"]

DEFAULT_PROFILE: dict = {
    "id": "",
    "name": "",
    "module": "generic",          # raids/<module>.py; "generic" is fully data-driven
    "verified": False,            # True only once tested end-to-end in the current game version
    "notes": "",
    "required_level": None,
    "difficulties": ["Easy", "Normal", "Hard", "Nightmare"],
    "difficulty": "Easy",
    "objective": "efficiency",    # one of OBJECTIVES
    "objective_target": "",       # material/crate name when objective is material/crate
    "target_rewards": [],
    "max_attempts": 0,            # 0 = unlimited (bounded by session limits)
    "run_timeout_s": 600,
    "enter_timeout_s": 60,
    # Ordered UI actions; "{difficulty}" in a template name is replaced by the selected difficulty.
    "entry_steps": [],
    "claim_steps": [],
    "restart_steps": [],
    "respawn_steps": [],
    # Detectors: {"template": name, "region": [x,y,w,h] fractions, "threshold": float}
    "detection": {
        "in_raid": None,
        "loading": None,
        "boss": None,
        "complete": None,
        "failed": None,
        "death": None,
        # Bar readers: {"region": [x,y,w,h], "color": [r,g,b], "tolerance": int}
        "boss_health": None,
        "player_health": None,
    },
    "combat": {
        "abilities": [
            {"name": "M1", "key": "mouse1", "cooldown": 0.3, "priority": 10, "hold": 0.0, "enabled": True},
        ],
        "tick_interval": 0.1,
        "approach": {"key": "w", "seconds": 0.0},  # walk toward boss after it is detected
        "lock_on_key": "",
        "low_health_pct": 0,          # >0: press heal/escape ability below this player HP %
        "low_health_ability": "",
        "no_progress_timeout": 25,    # seconds without boss HP change before recovery
        "recovery_steps": [],         # e.g. re-approach, re-lock
        "max_recoveries": 3,
    },
    # Optional OCR of the reward screen: {"player_exp": [x,y,w,h], ...}
    "reward_ocr": {},
    # Fallback / expected rewards credited per successful run when OCR is not configured.
    "rewards_per_run": {k: 0 for k in REWARD_KEYS},
    "drops_per_run": {},              # e.g. {"Cursed Womb Shard": 1}
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize(data: dict) -> dict:
    p = _merge(DEFAULT_PROFILE, data)
    if not p["id"]:
        p["id"] = re.sub(r"[^a-z0-9]+", "_", (p["name"] or "profile").lower()).strip("_")
    return p


def validate(p: dict) -> list[str]:
    """Return a list of problems; empty means usable."""
    errs = []
    if not p["name"]:
        errs.append("name is required")
    if p["difficulty"] not in p["difficulties"]:
        errs.append(f"difficulty {p['difficulty']!r} not in {p['difficulties']}")
    if p["objective"] not in OBJECTIVES:
        errs.append(f"objective must be one of {OBJECTIVES}")
    for group in ("entry_steps", "claim_steps", "restart_steps", "respawn_steps"):
        for i, s in enumerate(p[group]):
            if s.get("action") not in STEP_ACTIONS:
                errs.append(f"{group}[{i}]: unknown action {s.get('action')!r}")
    for i, s in enumerate(p["combat"]["recovery_steps"]):
        if s.get("action") not in STEP_ACTIONS:
            errs.append(f"combat.recovery_steps[{i}]: unknown action {s.get('action')!r}")
    for name, det in p["detection"].items():
        if det is None:
            continue
        r = det.get("region")
        if r is not None and (len(r) != 4 or not all(0 <= v <= 1 for v in r)):
            errs.append(f"detection.{name}.region must be 4 fractions in [0,1]")
    if not p["detection"].get("in_raid"):
        errs.append("detection.in_raid is required: the engine must know it is inside the raid")
    if not p["detection"].get("complete"):
        errs.append("detection.complete is required: the engine never guesses a raid finished")
    for a in p["combat"]["abilities"]:
        if not a.get("key"):
            errs.append(f"ability {a.get('name')!r} has no key")
    return errs


class ProfileStore:
    def __init__(self, directory: Path = PROFILES_DIR):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                out.append(normalize(json.loads(f.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError) as e:
                log.error("Skipping bad profile %s: %s", f.name, e)
        return out

    def get(self, pid: str) -> dict | None:
        f = self.dir / f"{pid}.json"
        if not f.exists():
            return None
        return normalize(json.loads(f.read_text(encoding="utf-8")))

    def save(self, profile: dict) -> Path:
        p = normalize(profile)
        f = self.dir / f"{p['id']}.json"
        f.write_text(json.dumps(p, indent=2), encoding="utf-8")
        return f

    def delete(self, pid: str) -> None:
        (self.dir / f"{pid}.json").unlink(missing_ok=True)
