"""Run history, per-hour rates and difficulty recommendation.

Every finished run is appended to logs/history.jsonl, so stats survive restarts and
recommendations are based on measured results, not guesses.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

MIN_RUNS_FOR_RECOMMENDATION = 3


@dataclass
class RunRecord:
    profile: str
    difficulty: str
    started: float
    duration: float
    success: bool
    deaths: int = 0
    wait_time: float = 0.0             # time in menus/loading, not fighting
    rewards: dict = field(default_factory=dict)   # player_exp, awakened_exp, yen, lumen
    drops: dict = field(default_factory=dict)     # item name -> count
    reason: str = ""                   # failure reason
    session: str = ""


def summarize(runs: list[RunRecord]) -> dict:
    n = len(runs)
    ok = [r for r in runs if r.success]
    total_time = sum(r.duration for r in runs)
    hours = total_time / 3600 if total_time else 0
    rewards: dict[str, float] = {}
    drops: dict[str, float] = {}
    for r in runs:
        for k, v in r.rewards.items():
            rewards[k] = rewards.get(k, 0) + (v or 0)
        for k, v in r.drops.items():
            drops[k] = drops.get(k, 0) + (v or 0)
    return {
        "runs": n,
        "completed": len(ok),
        "failed": n - len(ok),
        "success_rate": len(ok) / n if n else 0.0,
        "deaths": sum(r.deaths for r in runs),
        "avg_completion_s": sum(r.duration for r in ok) / len(ok) if ok else 0.0,
        "wait_time_s": sum(r.wait_time for r in runs),
        "total_time_s": total_time,
        "rewards": rewards,
        "drops": drops,
        "rewards_per_hour": {k: v / hours for k, v in rewards.items()} if hours else {},
        "drops_per_hour": {k: v / hours for k, v in drops.items()} if hours else {},
        "runs_per_hour": n / hours if hours else 0.0,
    }


def objective_value(summary: dict, objective: str, target: str = "") -> float:
    """Per-hour value of the chosen objective. Failed runs already cost time, so they count."""
    rph, dph = summary["rewards_per_hour"], summary["drops_per_hour"]
    if objective in ("player_exp", "awakened_exp"):
        return rph.get(objective, 0.0)
    if objective in ("material", "crate"):
        return dph.get(target, 0.0)
    return summary["runs_per_hour"] * summary["success_rate"]  # efficiency: clears per hour


class StatsTracker:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session = time.strftime("%Y%m%d-%H%M%S")
        self.session_runs: list[RunRecord] = []
        self._lock = threading.Lock()

    def record(self, run: RunRecord):
        run.session = self.session
        with self._lock:
            self.session_runs.append(run)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(run)) + "\n")

    def history(self) -> list[RunRecord]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(RunRecord(**json.loads(line)))
            except (json.JSONDecodeError, TypeError) as e:
                log.warning("Skipping bad history line: %s", e)
        return out

    def session_summary(self) -> dict:
        with self._lock:
            return summarize(list(self.session_runs))

    def recommend(self, profile_id: str, objective: str, target: str = "") -> Optional[tuple[str, float, dict]]:
        """Best difficulty by measured objective/hour. None until each candidate has enough runs."""
        by_diff: dict[str, list[RunRecord]] = {}
        for r in self.history():
            if r.profile == profile_id:
                by_diff.setdefault(r.difficulty, []).append(r)
        scored = {d: objective_value(summarize(rs), objective, target)
                  for d, rs in by_diff.items() if len(rs) >= MIN_RUNS_FOR_RECOMMENDATION}
        if not scored:
            return None
        best = max(scored, key=scored.get)
        return best, scored[best], scored
