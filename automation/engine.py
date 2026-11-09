"""Session engine: runs a queue of raid profiles on a worker thread with safety limits.

Game interaction (vision + input) lives in Runtime; farming logic lives in raids/. The GUI
only calls start/pause/resume/stop and receives callbacks.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from config.settings import LOGS_DIR
from raids import registry
from raids.base import RunFailed, Stopped, UnknownState
from raids.profile import validate
from stats.tracker import RunRecord, StatsTracker

log = logging.getLogger("engine")


class Runtime:
    """What raid modules may use. Every wait goes through sleep/checkpoint so stop is instant."""

    def __init__(self, settings, window, vision, inp, callbacks: dict):
        self.settings, self.window, self.vision, self.input = settings, window, vision, inp
        self.cb = callbacks
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

    def stopping(self) -> bool:
        return self.stop_event.is_set()

    def checkpoint(self):
        if self.pause_event.is_set():
            self.input.release_all()
            self.set_state("PAUSED")
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.05)
            if not self.stop_event.is_set():
                self.set_state("RESUMED")
                if self.settings.require_window_focus:
                    self.window.focus()
        if self.stop_event.is_set():
            raise Stopped()

    def sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while True:
            self.checkpoint()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def grab(self):
        frame = self.vision.frame()
        if frame is None:
            raise RunFailed("game window not found or minimised")
        if self.settings.require_window_focus and not self.window.is_foreground():
            self.input.release_all()
            if not self.window.focus():
                raise RunFailed("game window lost focus and could not be refocused")
        return frame

    def set_state(self, state: str):
        self._emit("state", state)

    def event(self, msg: str):
        log.info(msg)

    def boss_hp(self, frac: float):
        self._emit("boss_hp", frac)

    def _emit(self, name, *args):
        fn = self.cb.get(name)
        if fn:
            try:
                fn(*args)
            except Exception:
                log.exception("callback %s failed", name)


class Engine:
    def __init__(self, settings, window, vision, inp, stats: StatsTracker,
                 callbacks: Optional[dict[str, Callable]] = None):
        self.settings = settings
        self.stats = stats
        self.cb = callbacks or {}
        self.rt = Runtime(settings, window, vision, inp, self.cb)
        self.thread: Optional[threading.Thread] = None
        self.current: dict = {}

    # ---- control (any thread) -----------------------------------------
    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, queue: list[tuple[dict, int]]):
        """queue: [(profile, runs)], runs == 0 means until session limits."""
        if self.running:
            raise RuntimeError("already running")
        for profile, _ in queue:
            errs = validate(profile)
            if errs:
                raise ValueError(f"{profile['name']}: " + "; ".join(errs))
        self.rt.stop_event.clear()
        self.rt.pause_event.clear()
        self.thread = threading.Thread(target=self._run, args=(queue,), name="engine", daemon=True)
        self.thread.start()

    def pause(self):
        self.rt.pause_event.set()

    def resume(self):
        self.rt.pause_event.clear()

    def toggle_pause(self):
        (self.resume if self.rt.pause_event.is_set() else self.pause)()

    def stop(self):
        """Emergency stop: safe from any thread, releases input immediately."""
        self.rt.stop_event.set()
        self.rt.pause_event.clear()
        self.rt.input.release_all()

    def run_single(self, fn: Callable, label: str):
        """Testing mode: run one action (fn(raid_or_rt)) on the worker thread."""
        if self.running:
            raise RuntimeError("already running")
        self.rt.stop_event.clear()

        def body():
            self.rt.set_state(f"TEST: {label}")
            try:
                fn()
                log.info("Test %s finished", label)
            except Stopped:
                log.warning("Test %s stopped", label)
            except Exception as e:
                log.error("Test %s failed: %s", label, e)
                self._screenshot("test_error")
            finally:
                self.rt.input.release_all()
                self.rt.set_state("IDLE")

        self.thread = threading.Thread(target=body, name="engine-test", daemon=True)
        self.thread.start()

    # ---- worker --------------------------------------------------------
    def _screenshot(self, tag):
        if self.settings.save_screenshot_on_error:
            p = self.rt.vision.save_frame(LOGS_DIR / "screens", tag)
            if p:
                log.info("Saved screenshot %s", p)

    def _limit_hit(self, t_start) -> Optional[str]:
        if self.settings.max_runtime_minutes and time.monotonic() - t_start > self.settings.max_runtime_minutes * 60:
            return f"max runtime {self.settings.max_runtime_minutes} min reached"
        return None

    def _run(self, queue):
        t_start = time.monotonic()
        consecutive_failures = 0
        reason = "queue finished"
        log.info("Session started (%s)", "DRY RUN" if self.settings.dry_run else "LIVE INPUT")
        try:
            if self.settings.require_window_focus and not self.rt.window.focus():
                raise UnknownState(f"game window {self.settings.window_title!r} not found")
            for profile, runs in queue:
                raid = registry.create(profile, self.rt)
                done = attempts = 0
                self.current = {"profile": profile["name"], "difficulty": profile["difficulty"]}
                self._emit("profile", profile)
                log.info("Farming %s [%s], %s runs", profile["name"], profile["difficulty"], runs or "unlimited")
                while runs == 0 or done < runs:
                    limit = self._limit_hit(t_start)
                    if limit:
                        reason = limit
                        return
                    if profile["max_attempts"] and attempts >= profile["max_attempts"]:
                        log.warning("%s: max attempts (%d) reached", profile["name"], profile["max_attempts"])
                        break
                    attempts += 1
                    started = time.time()
                    t0 = time.monotonic()
                    try:
                        res = raid.run_once()
                    except RunFailed as e:
                        log.error("Run failed: %s", e)
                        self._screenshot("run_failed")
                        res = None
                        fail_reason = str(e)
                    rec = RunRecord(profile=profile["id"], difficulty=profile["difficulty"], started=started,
                                    duration=time.monotonic() - t0, success=bool(res and res.success))
                    if res:
                        rec.deaths, rec.wait_time, rec.rewards, rec.drops, rec.reason = (
                            res.deaths, res.wait_time, res.rewards, res.drops, res.reason)
                    else:
                        rec.reason = fail_reason
                    self.stats.record(rec)
                    self._emit("run", rec)
                    if rec.success:
                        done += 1
                        consecutive_failures = 0
                        log.info("Run %d complete in %.0fs", done, rec.duration)
                        continue
                    consecutive_failures += 1
                    if consecutive_failures >= self.settings.max_consecutive_failures:
                        reason = f"{consecutive_failures} consecutive failures"
                        return
                    log.info("Recovering: retry in %.0fs", self.settings.retry_delay_seconds)
                    self.rt.input.release_all()
                    self.rt.sleep(self.settings.retry_delay_seconds)
                    try:
                        if raid.in_raid():
                            raid.restart()
                    except RunFailed as e:
                        log.error("Recovery failed: %s", e)
        except Stopped:
            reason = "stopped by user"
        except UnknownState as e:
            reason = f"unknown game state: {e}"
            self._screenshot("unknown_state")
        except Exception as e:  # never die silently
            log.exception("Engine crashed")
            reason = f"error: {e}"
            self._screenshot("crash")
        finally:
            self.rt.input.release_all()
            log.info("Session ended: %s", reason)
            self.rt.set_state("STOPPED")
            self._emit("finished", reason)

    def _emit(self, name, *args):
        self.rt._emit(name, *args)
