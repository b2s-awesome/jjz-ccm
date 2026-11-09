"""Data-driven raid runner. Raid-specific modules subclass Raid and override hooks.

A run: enter -> fight -> claim -> restart. Every phase is driven by detectors from the
profile; anything the profile cannot recognise raises, it is never guessed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from combat.rotation import Rotation

log = logging.getLogger("raid")


class Stopped(Exception):
    """User pressed stop / emergency stop."""


class RunFailed(Exception):
    """This run failed; the session may retry."""


class UnknownState(Exception):
    """The screen matches nothing the profile knows. The session must stop."""


@dataclass
class RunResult:
    success: bool
    deaths: int = 0
    wait_time: float = 0.0
    rewards: dict = field(default_factory=dict)
    drops: dict = field(default_factory=dict)
    reason: str = ""


class Raid:
    SCAN_INTERVAL = 0.4  # seconds between full screen-state checks during combat

    def __init__(self, profile: dict, rt):
        self.p = profile
        self.rt = rt  # automation.engine.Runtime: vision, input, sleep, checkpoint, state
        self.det = profile["detection"]
        self.combat = profile["combat"]
        self.fmt = {"difficulty": profile["difficulty"]}
        self.rotation = Rotation(self.combat["abilities"])

    # ---- helpers -------------------------------------------------------
    def seen(self, name: str, frame=None):
        return self.rt.vision.check(self.det.get(name), frame, self.fmt)

    def wait_for(self, names: list[str], timeout: float, poll: float = 0.3) -> str | None:
        """Wait until one of the named detectors fires; return its name or None on timeout."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.rt.checkpoint()
            frame = self.rt.grab()
            for n in names:
                if self.seen(n, frame):
                    return n
            self.rt.sleep(poll)
        return None

    def run_steps(self, steps: list[dict], label: str):
        for i, s in enumerate(steps):
            self.rt.checkpoint()
            self.do_step(s, f"{label}[{i}]")

    def do_step(self, s: dict, where: str):
        a, v, inp = s["action"], self.rt.vision, self.rt.input
        if a == "key":
            for _ in range(int(s.get("repeat", 1))):
                inp.tap(s["key"], float(s.get("hold", 0)), self.rt.stopping)
                self.rt.sleep(float(s.get("delay", 0.1)))
        elif a == "click":
            inp.click(*s["pos"], s.get("button", "mouse1"))
        elif a == "wait":
            self.rt.sleep(float(s["seconds"]))
        elif a in ("click_template", "wait_template", "wait_gone"):
            det = {k: s[k] for k in ("template", "region", "threshold") if k in s}
            end = time.monotonic() + float(s.get("timeout", 10))
            while True:
                self.rt.checkpoint()
                m = v.check(det, self.rt.grab(), self.fmt)
                done = (not m) if a == "wait_gone" else bool(m)
                if done:
                    if a == "click_template":
                        dx, dy = s.get("offset", [0, 0])
                        inp.click(m.center[0] + dx, m.center[1] + dy, s.get("button", "mouse1"))
                    break
                if time.monotonic() >= end:
                    if s.get("optional"):
                        log.info("%s: optional %s not satisfied, continuing", where, s["template"])
                        break
                    raise RunFailed(f"{where}: {a} {s['template']!r} timed out (best {m.confidence:.2f})")
                self.rt.sleep(0.25)
        else:
            raise RunFailed(f"{where}: unknown action {a!r}")
        self.rt.sleep(float(s.get("after", 0.2)))

    # ---- phases (override in raid modules as needed) --------------------
    def in_raid(self) -> bool:
        return bool(self.seen("in_raid", self.rt.grab()))

    def enter(self):
        self.rt.set_state("ENTERING")
        self.run_steps(self.p["entry_steps"], "entry_steps")
        self.rt.set_state("LOADING")
        if self.wait_for(["in_raid"], float(self.p["enter_timeout_s"])) is None:
            raise RunFailed("raid HUD not detected after entry steps")

    def approach_boss(self):
        ap = self.combat.get("approach") or {}
        if ap.get("seconds"):
            self.rt.input.tap(ap["key"], float(ap["seconds"]), self.rt.stopping)
        if self.combat.get("lock_on_key"):
            self.rt.input.tap(self.combat["lock_on_key"])

    def handle_death(self, deaths: int) -> bool:
        """Return True if the fight can continue after respawning."""
        if not self.p["respawn_steps"]:
            return False
        self.rt.set_state("RESPAWNING")
        self.run_steps(self.p["respawn_steps"], "respawn_steps")
        if self.wait_for(["in_raid"], float(self.p["enter_timeout_s"])) is None:
            raise RunFailed("did not return to raid after respawn")
        self.rotation.reset()
        self.approach_boss()
        return True

    def fight(self, result: RunResult) -> str:
        """Run the rotation until complete/failed. Returns 'complete' or 'failed'."""
        self.rt.set_state("SEEKING_BOSS")
        c = self.combat
        start = time.monotonic()
        deadline = start + float(self.p["run_timeout_s"])
        boss_engaged = False
        last_hp, last_progress = None, time.monotonic()
        last_known = time.monotonic()
        recoveries = 0
        next_scan = 0.0
        unknown_timeout = self.rt.settings.unknown_state_timeout

        while True:
            self.rt.checkpoint()
            now = time.monotonic()
            if now > deadline:
                raise RunFailed(f"run exceeded {self.p['run_timeout_s']}s")

            if now >= next_scan:
                next_scan = now + self.SCAN_INTERVAL
                frame = self.rt.grab()
                if self.seen("complete", frame):
                    return "complete"
                if self.seen("failed", frame):
                    return "failed"
                if self.seen("death", frame):
                    result.deaths += 1
                    self.rt.event(f"Death #{result.deaths}")
                    if not self.handle_death(result.deaths):
                        return "failed"
                    last_progress = last_known = time.monotonic()
                    continue
                if self.seen("loading", frame):
                    last_known = last_progress = now
                    self.rt.sleep(0.5)
                    continue
                if self.seen("in_raid", frame):
                    last_known = now
                elif now - last_known > unknown_timeout:
                    raise UnknownState(f"no known screen for {unknown_timeout:.0f}s during combat")

                boss_seen = bool(self.seen("boss", frame)) if self.det.get("boss") else True
                hp = self.rt.vision.bar(self.det.get("boss_health"), frame)
                if boss_seen and not boss_engaged:
                    boss_engaged = True
                    self.rt.set_state("COMBAT")
                    self.rt.event("Boss detected")
                    self.approach_boss()
                    last_progress = time.monotonic()
                if hp is not None:
                    self.rt.boss_hp(hp)
                    if last_hp is None or abs(hp - last_hp) > 0.005:
                        last_hp, last_progress = hp, now
                    elif boss_engaged and now - last_progress > float(c["no_progress_timeout"]):
                        recoveries += 1
                        self.rt.event(f"Boss HP not changing; recovery {recoveries}/{c['max_recoveries']}")
                        if recoveries > int(c["max_recoveries"]):
                            raise RunFailed("attacks not registering (boss HP unchanged)")
                        self.run_steps(c["recovery_steps"], "recovery_steps")
                        self.approach_boss()
                        last_progress = time.monotonic()

                php = self.rt.vision.bar(self.det.get("player_health"), frame)
                if php is not None and c.get("low_health_pct") and php * 100 < c["low_health_pct"]:
                    ab = self.rotation.by_name(c.get("low_health_ability", ""))
                    if ab and self.rotation.ready(ab, now):
                        self.rt.event(f"Low HP ({php:.0%}); using {ab['name']}")
                        self.rt.input.tap(ab["key"], float(ab.get("hold", 0)), self.rt.stopping)
                        self.rotation.mark_used(ab, time.monotonic())

            if boss_engaged:
                exclude = (c.get("low_health_ability", ""),)
                ab = self.rotation.next_ready(time.monotonic(), exclude)
                if ab:
                    self.rt.input.tap(ab["key"], float(ab.get("hold", 0)), self.rt.stopping)
                    self.rotation.mark_used(ab, time.monotonic())
            self.rt.sleep(float(c["tick_interval"]))

    def read_rewards(self) -> tuple[dict, dict]:
        rewards = {}
        for key, region in (self.p.get("reward_ocr") or {}).items():
            val = self.rt.vision.read_number(region)
            if val is not None:
                rewards[key] = val
            else:
                self.rt.event(f"OCR could not read {key}; using configured value")
        for key, val in self.p["rewards_per_run"].items():
            rewards.setdefault(key, val)
        return rewards, dict(self.p["drops_per_run"])

    def claim(self, result: RunResult):
        self.rt.set_state("CLAIMING")
        self.rt.grab()
        result.rewards, result.drops = self.read_rewards()
        self.run_steps(self.p["claim_steps"], "claim_steps")

    def restart(self):
        self.rt.set_state("RESTARTING")
        self.run_steps(self.p["restart_steps"], "restart_steps")

    # ---- one full run --------------------------------------------------
    def run_once(self) -> RunResult:
        result = RunResult(success=False)
        t0 = time.monotonic()
        self.rotation.reset()
        if not self.in_raid():
            self.enter()
        result.wait_time = time.monotonic() - t0
        outcome = self.fight(result)
        if outcome == "complete":
            self.rt.event("Raid complete")
            self.claim(result)
            result.success = True
        else:
            result.reason = "raid failed / party wiped"
        self.restart()
        return result
