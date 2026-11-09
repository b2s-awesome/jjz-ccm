"""Priority + cooldown skill rotation. Time is passed in, so it is deterministic to test."""
from __future__ import annotations

from typing import Optional


class Rotation:
    def __init__(self, abilities: list[dict]):
        self.abilities = [a for a in abilities if a.get("enabled", True) and a.get("key")]
        self.last_used: dict[str, float] = {}

    def ready(self, ability: dict, now: float) -> bool:
        last = self.last_used.get(ability["name"])
        return last is None or now - last >= float(ability.get("cooldown", 0))

    def next_ready(self, now: float, exclude: tuple = ()) -> Optional[dict]:
        """Highest-priority ability off cooldown; ties go to the one unused longest."""
        best = None
        for a in self.abilities:
            if a["name"] in exclude or not self.ready(a, now):
                continue
            key = (a.get("priority", 0), -self.last_used.get(a["name"], -1e9))
            if best is None or key > best[0]:
                best = (key, a)
        return best[1] if best else None

    def by_name(self, name: str) -> Optional[dict]:
        return next((a for a in self.abilities if a["name"] == name), None)

    def mark_used(self, ability: dict, now: float):
        self.last_used[ability["name"]] = now

    def reset(self):
        self.last_used.clear()
