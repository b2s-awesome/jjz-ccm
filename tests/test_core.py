"""Core tests: vision on synthetic frames, rotation, stats, profiles, and a full engine run
against a scripted fake game (no Roblox, no real input)."""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation.engine import Engine  # noqa: E402
from automation.input import InputBackend  # noqa: E402
from combat.rotation import Rotation  # noqa: E402
from config.settings import Settings  # noqa: E402
from raids.profile import ProfileStore, normalize, validate  # noqa: E402
from stats.tracker import RunRecord, StatsTracker, objective_value, summarize  # noqa: E402
from vision.detector import TemplateLibrary, bar_fill, match_template  # noqa: E402
from vision.screen import Vision, parse_number  # noqa: E402

H, W = 540, 960


def blank():
    return np.full((H, W, 3), 30, np.uint8)


def badge(color, text):
    """A distinctive 60x120 'UI element' used as a template."""
    img = np.full((60, 120, 3), color, np.uint8)
    cv2.putText(img, text, (5, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.rectangle(img, (2, 2), (117, 57), (0, 0, 0), 2)
    return img


def paste(frame, img, x, y):
    frame[y:y + img.shape[0], x:x + img.shape[1]] = img
    return frame


# ---------------------------------------------------------------- vision
def test_template_found_and_located():
    t = badge((40, 120, 200), "HUD")
    f = paste(blank(), t, 400, 20)
    m = match_template(f, t, threshold=0.8)
    assert m.found and m.confidence > 0.95
    assert abs(m.center[0] - 460 / W) < 0.01 and abs(m.center[1] - 50 / H) < 0.01


def test_template_absent_and_region_excludes():
    t = badge((40, 120, 200), "HUD")
    assert not match_template(blank(), t, threshold=0.8)
    f = paste(blank(), t, 400, 20)
    assert not match_template(f, t, region=[0, 0.5, 1, 0.5], threshold=0.8)


def test_template_scaled_resolution():
    """Template captured at 540p still matches a 1080p frame via height scaling."""
    t = badge((40, 120, 200), "HUD")
    big = cv2.resize(paste(blank(), t, 400, 20), (W * 2, H * 2))
    m = match_template(big, t, threshold=0.8, scale=2.0)
    assert m.found


def test_bar_fill():
    f = blank()
    f[10:20, 100:400] = (40, 40, 200)  # BGR red, 300px wide
    assert bar_fill(f, [100 / W, 10 / H, 400 / W, 10 / H], [200, 40, 40]) == pytest.approx(0.75, abs=0.01)


def test_parse_number():
    assert parse_number("+1,250 EXP") == 1250
    assert parse_number("5.6k") == 5600
    assert parse_number("1.4M") == 1_400_000
    assert parse_number("none") is None


# ---------------------------------------------------------------- rotation
def test_rotation_priority_and_cooldown():
    r = Rotation([
        {"name": "M1", "key": "mouse1", "cooldown": 0.3, "priority": 1},
        {"name": "Big", "key": "4", "cooldown": 10, "priority": 9},
        {"name": "Off", "key": "5", "cooldown": 0, "priority": 99, "enabled": False},
    ])
    a = r.next_ready(0.0)
    assert a["name"] == "Big"
    r.mark_used(a, 0.0)
    assert r.next_ready(1.0)["name"] == "M1"
    r.mark_used(r.by_name("M1"), 1.0)
    assert r.next_ready(1.1) is None
    assert r.next_ready(10.0)["name"] == "Big"


# ---------------------------------------------------------------- stats
def test_stats_and_recommendation(tmp_path):
    st = StatsTracker(tmp_path / "h.jsonl")
    for i in range(3):
        st.record(RunRecord("p", "Easy", 0, 60, True, rewards={"player_exp": 100}))
        st.record(RunRecord("p", "Hard", 0, 100, True, rewards={"player_exp": 300}))
    st.record(RunRecord("p", "Nightmare", 0, 100, True, rewards={"player_exp": 9999}))  # too few runs
    s = summarize(st.session_runs)
    assert s["runs"] == 7 and s["success_rate"] == 1
    best, val, scored = st.recommend("p", "player_exp")
    assert best == "Hard" and "Nightmare" not in scored
    assert val == pytest.approx(300 * 36)
    assert objective_value(summarize([RunRecord("p", "E", 0, 3600, False)]), "efficiency") == 0


# ---------------------------------------------------------------- profiles
def test_shipped_profiles_valid():
    profiles = ProfileStore().list()
    assert len(profiles) >= 8
    for p in profiles:
        assert validate(p) == [], p["id"]


def test_validate_catches_problems():
    p = normalize({"name": "x", "difficulty": "Ultra", "entry_steps": [{"action": "teleport"}]})
    errs = " ".join(validate(p))
    assert "Ultra" in errs and "teleport" in errs and "complete" in errs and "in_raid" in errs


# ---------------------------------------------------------------- engine
class FakeWindow:
    def rect(self): return (0, 0, W, H)
    def is_foreground(self): return True
    def focus(self): return True


class RecordingInput(InputBackend):
    def __init__(self, window, game):
        super().__init__(window)
        self.game, self.log = game, []
    def _down(self, key): self.log.append(("down", key)); self.game.on_key(key)
    def _up(self, key): self.log.append(("up", key))
    def _move(self, x, y): self.log.append(("move", x, y)); self.game.on_move(x, y)


TPL = {
    "raid_hud": badge((40, 120, 200), "HUD"),
    "start_button": badge((40, 200, 40), "START"),
    "reward_screen": badge((200, 160, 40), "WIN"),
    "replay_button": badge((160, 40, 160), "AGAIN"),
}


class FakeGame:
    """Lobby -> click START -> raid with a boss that loses HP per skill press -> reward screen
    -> click AGAIN -> raid again."""

    def __init__(self, hp_per_hit=0.25):
        self.screen, self.hp, self.hp_per_hit, self.mouse = "lobby", 1.0, hp_per_hit, (0, 0)

    def on_move(self, x, y): self.mouse = (x, y)

    def _over(self, x, y):
        return x <= self.mouse[0] <= x + 120 and y <= self.mouse[1] <= y + 60

    def on_key(self, key):
        if key == "mouse1" and self.screen == "lobby" and self._over(420, 240):
            self.screen, self.hp = "raid", 1.0
        elif key == "mouse1" and self.screen == "reward" and self._over(420, 400):
            self.screen, self.hp = "raid", 1.0
        elif self.screen == "raid" and key in ("1", "mouse1"):
            self.hp = max(0.0, self.hp - self.hp_per_hit)
            if self.hp == 0:
                self.screen = "reward"

    def grab(self):
        f = blank()
        if self.screen == "lobby":
            paste(f, TPL["start_button"], 420, 240)
        elif self.screen == "raid":
            paste(f, TPL["raid_hud"], 420, 10)
            f[90:100, 288:288 + int(384 * self.hp)] = (40, 40, 200)
        elif self.screen == "reward":
            paste(f, TPL["reward_screen"], 420, 200)
            paste(f, TPL["replay_button"], 420, 400)
        return f


def make_profile(**over):
    p = {
        "name": "Fake Raid", "difficulty": "Easy",
        "entry_steps": [{"action": "click_template", "template": "start_button", "timeout": 3}],
        "restart_steps": [{"action": "click_template", "template": "replay_button", "timeout": 3}],
        "enter_timeout_s": 3, "run_timeout_s": 10,
        "detection": {
            "in_raid": {"template": "raid_hud", "region": [0.3, 0, 0.4, 0.2]},
            "complete": {"template": "reward_screen"},
            "boss_health": {"region": [0.3, 0.16, 0.4, 0.02], "color": [200, 40, 40]},
        },
        "combat": {"abilities": [{"name": "S1", "key": "1", "cooldown": 0.05, "priority": 5}],
                   "tick_interval": 0.02, "no_progress_timeout": 1, "max_recoveries": 1},
        "rewards_per_run": {"player_exp": 1000},
    }
    p.update(over)
    return normalize(p)


@pytest.fixture
def rig(tmp_path):
    for n, img in TPL.items():
        cv2.imwrite(str(tmp_path / f"{n}.png"), img)
    game = FakeGame()
    s = Settings(template_reference_height=H, dry_run=False, retry_delay_seconds=0.1,
                 max_consecutive_failures=2, unknown_state_timeout=2, save_screenshot_on_error=False)
    win = FakeWindow()
    vis = Vision(type("Src", (), {"grab": lambda self: game.grab()})(), TemplateLibrary(tmp_path), H)
    inp = RecordingInput(win, game)
    events = {"finished": None, "runs": []}
    eng = Engine(s, win, vis, inp, StatsTracker(tmp_path / "h.jsonl"), {
        "finished": lambda r: events.__setitem__("finished", r),
        "run": lambda rec: events["runs"].append(rec),
    })
    return eng, game, inp, events


def wait_done(eng, timeout=20):
    eng.thread.join(timeout)
    assert not eng.running, "engine did not finish"


def test_engine_full_cycle(rig):
    eng, game, inp, ev = rig
    eng.start([(make_profile(), 2)])
    wait_done(eng)
    assert ev["finished"] == "queue finished"
    assert [r.success for r in ev["runs"]] == [True, True]
    assert ev["runs"][0].rewards["player_exp"] == 1000
    assert not inp.held, "keys left held down"


def test_engine_stops_after_consecutive_failures(rig):
    eng, game, inp, ev = rig
    game.hp_per_hit = 0.0  # attacks never register -> recovery -> fail
    eng.start([(make_profile(), 5)])
    wait_done(eng)
    assert ev["finished"] == "2 consecutive failures"
    assert all(not r.success for r in ev["runs"])
    assert "not registering" in ev["runs"][0].reason


def test_engine_unknown_state_stops(rig):
    eng, game, inp, ev = rig
    game.screen = "raid"
    orig = game.grab
    game.grab = lambda: blank() if game.hp < 1 else orig()  # screen goes unrecognisable mid-fight
    prof = make_profile()
    prof["combat"]["no_progress_timeout"] = 30
    eng.start([(prof, 1)])
    wait_done(eng)
    assert ev["finished"].startswith("unknown game state")


def test_emergency_stop_is_immediate(rig):
    eng, game, inp, ev = rig
    game.hp_per_hit = 0.0
    eng.start([(make_profile(run_timeout_s=60), 0)])
    time.sleep(0.5)
    t = time.monotonic()
    eng.stop()
    wait_done(eng, 2)
    assert time.monotonic() - t < 1.0
    assert ev["finished"] == "stopped by user"
    assert not inp.held


def test_start_rejects_invalid_profile(rig):
    eng, *_ = rig
    with pytest.raises(ValueError):
        eng.start([(normalize({"name": "bad"}), 1)])


# ---------------------------------------------------------------- vision performance / options
def test_color_option_separates_same_luminance():
    """Red and green badges with equal grayscale value: gray matching confuses them, color
    (the default) doesn't."""
    red, green = badge((0, 0, 255), "GO"), badge((0, 130, 0), "GO")
    f = paste(blank(), green, 400, 200)
    assert match_template(f, red, threshold=0.8, color=False).found
    assert not match_template(f, red, threshold=0.8).found
    assert match_template(f, green, threshold=0.8).found


def test_vision_caches_scale(tmp_path):
    t = badge((40, 120, 200), "HUD")
    cv2.imwrite(str(tmp_path / "hud.png"), t)
    frame = paste(blank(), t, 400, 20)
    vis = Vision(None, TemplateLibrary(tmp_path), H)
    assert vis.check({"template": "hud"}, frame)
    assert vis._scale_cache == {("hud", H): 1.0}
    assert vis.check({"template": "hud"}, frame).confidence > 0.95  # cached single-scale path


def test_scan_speed_1080p():
    """A steady-state scan (5 color detectors at their cached scale, one full-frame, + HP bar) must
    fit the 0.4 s scan interval. Was ~1.7 s before coarse-to-fine matching and the scale cache."""
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
    tpl = rng.integers(0, 255, (60, 180, 3), dtype=np.uint8)
    paste(frame, tpl, 900, 40)
    regions = [[0.25, 0, 0.5, 0.2], [0.2, 0.1, 0.6, 0.5], [0.2, 0.1, 0.6, 0.5], None, [0.25, 0, 0.5, 0.12]]

    def scan():
        for r in regions:
            match_template(frame, tpl, r, 0.8, 1.0, scales=[1.0])
        bar_fill(frame, [0.3, 0.05, 0.4, 0.03], [200, 40, 40])

    scan()
    t0 = time.perf_counter()
    for _ in range(5):
        scan()
    ms = (time.perf_counter() - t0) / 5 * 1000
    print(f"scan: {ms:.0f} ms")
    assert match_template(frame, tpl, None, 0.8, 1.0).center == pytest.approx((990 / 1920, 70 / 1080), abs=1e-3)
    assert ms < 300
