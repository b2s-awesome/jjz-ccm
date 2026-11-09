"""Map a profile's "module" field to a Raid class.

"generic" uses raids.base.Raid (fully data-driven). Any other value imports
raids/<module>.py and uses its RAID_CLASS, so a new raid with unusual mechanics is one
new file and never needs engine changes.
"""
from __future__ import annotations

import importlib
import re

from raids.base import Raid


def raid_class(module: str) -> type[Raid]:
    if module in ("", "generic"):
        return Raid
    if not re.fullmatch(r"[a-z0-9_]+", module):
        raise ValueError(f"invalid raid module name {module!r}")
    mod = importlib.import_module(f"raids.{module}")
    cls = getattr(mod, "RAID_CLASS", None)
    if not (isinstance(cls, type) and issubclass(cls, Raid)):
        raise ValueError(f"raids/{module}.py must define RAID_CLASS (a Raid subclass)")
    return cls


def create(profile: dict, rt) -> Raid:
    return raid_class(profile.get("module", "generic"))(profile, rt)
