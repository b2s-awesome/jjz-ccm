"""Main window: Dashboard, Raids, Statistics, Logs, Testing, Settings."""
from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import fields

import cv2
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget)

from automation.engine import Engine
from automation.hotkeys import Hotkeys
from automation.input import make_input
from automation.window import make_window
from config.settings import LOGS_DIR, TEMPLATES_DIR, Settings
from gui.capture import CaptureDialog, to_pixmap
from gui.theme import ACCENT, STATE_COLORS, STYLE
from raids import registry
from raids.profile import OBJECTIVES, REWARD_KEYS, ProfileStore, normalize, validate
from stats.tracker import StatsTracker, summarize
from vision.detector import TemplateLibrary
from vision.screen import ScreenSource, Vision

log = logging.getLogger("gui")
ADVANCED_KEYS = ["entry_steps", "claim_steps", "restart_steps", "respawn_steps", "detection",
                 "reward_ocr", "drops_per_run", "target_rewards", "difficulties"]


class Bridge(QObject):
    """Engine thread -> GUI thread (queued signals)."""
    state = Signal(str)
    boss_hp = Signal(float)
    run = Signal(object)
    finished = Signal(str)
    profile = Signal(object)
    log = Signal(str, int)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: Bridge):
        super().__init__()
        self.bridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(name)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.bridge.log.emit(self.format(record), record.levelno)


def card(title: str):
    f = QFrame(objectName="card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 10, 14, 10)
    t = QLabel(title, objectName="cardTitle")
    v = QLabel("-", objectName="cardValue")
    lay.addWidget(t)
    lay.addWidget(v)
    return f, v


def fmt_num(v: float) -> str:
    v = float(v or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"{v / div:.2f}{suf}"
    return f"{v:.0f}"


def fmt_dur(s: float) -> str:
    s = int(s)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JJZ Raid Farmer")
        self.resize(1180, 760)
        self.setStyleSheet(STYLE)
        self.settings = Settings.load()
        self.store = ProfileStore()
        self.stats = StatsTracker(LOGS_DIR / "history.jsonl")
        self.templates = TemplateLibrary(TEMPLATES_DIR)
        self.bridge = Bridge()
        self.engine: Engine | None = None
        self.session_start: float | None = None
        self.current_profile: dict | None = None

        handler = QtLogHandler(self.bridge)
        logging.getLogger().addHandler(handler)
        self.bridge.log.connect(self._append_log)
        self.bridge.state.connect(self._on_state)
        self.bridge.boss_hp.connect(lambda v: self.boss_bar.setValue(int(v * 100)))
        self.bridge.run.connect(self._on_run)
        self.bridge.finished.connect(self._on_finished)
        self.bridge.profile.connect(self._on_profile)

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.nav = QListWidget(objectName="nav")
        self.nav.setFixedWidth(170)
        self.pages = QStackedWidget()
        for name, page in [("Dashboard", self._dashboard()), ("Raids", self._raids_page()),
                           ("Statistics", self._stats_page()), ("Logs", self._logs_page()),
                           ("Testing", self._testing_page()), ("Settings", self._settings_page())]:
            self.nav.addItem(name)
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self._nav_changed)
        self.nav.setCurrentRow(0)
        h.addWidget(self.nav)
        h.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.hotkeys = Hotkeys({self.settings.emergency_stop_hotkey: self._hotkey_stop,
                                self.settings.pause_hotkey: self._hotkey_pause})
        self.hotkeys_ok = self.hotkeys.start()
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()
        self._refresh_profiles()
        self._update_banner()
        self._on_state("IDLE")
        log.info("Ready. Emergency stop: %s, pause: %s%s", self.settings.emergency_stop_hotkey.upper(),
                 self.settings.pause_hotkey.upper(), "" if self.hotkeys_ok else " (hotkeys unavailable)")

    # =============================================================== engine
    def _build_engine(self) -> Engine:
        window = make_window(self.settings)
        self.templates.reload()
        vision = Vision(ScreenSource(window), self.templates, self.settings.template_reference_height,
                        self.settings.default_threshold, self.settings.tesseract_cmd)
        inp = make_input(self.settings, window)
        b = self.bridge
        return Engine(self.settings, window, vision, inp, self.stats, {
            "state": b.state.emit, "boss_hp": b.boss_hp.emit, "run": b.run.emit,
            "finished": b.finished.emit, "profile": b.profile.emit})

    def _busy(self) -> bool:
        if self.engine and self.engine.running:
            QMessageBox.warning(self, "Busy", "Stop the current session or test first.")
            return True
        return False

    def _hotkey_stop(self):
        if self.engine:
            self.engine.stop()  # thread-safe; GUI updates arrive via signals

    def _hotkey_pause(self):
        if self.engine and self.engine.running:
            self.engine.toggle_pause()

    def closeEvent(self, e):
        if self.engine:
            self.engine.stop()
        self.hotkeys.stop()
        self._save_queue()
        self.settings.save()
        super().closeEvent(e)

    # =============================================================== dashboard
    def _dashboard(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 18, 20, 18)
        top = QHBoxLayout()
        top.addWidget(QLabel("Dashboard", objectName="h1"))
        top.addStretch()
        self.banner = QLabel(objectName="banner")
        top.addWidget(self.banner)
        self.state_pill = QLabel(objectName="banner")
        top.addWidget(self.state_pill)
        v.addLayout(top)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.cards = {}
        for i, (key, title) in enumerate([
                ("raid", "Current raid"), ("difficulty", "Difficulty"), ("objective", "Objective"),
                ("time", "Session time"), ("runs", "Runs (ok / failed)"), ("rate", "Success rate"),
                ("player_exp", "Player EXP"), ("awakened_exp", "Awakened EXP"), ("yen", "Yen"),
                ("lumen", "Lumen"), ("avg", "Avg clear time"), ("deaths", "Deaths")]):
            f, val = card(title)
            self.cards[key] = val
            grid.addWidget(f, i // 4, i % 4)
        v.addLayout(grid)

        hp = QHBoxLayout()
        hp.addWidget(QLabel("Boss HP"))
        self.boss_bar = QProgressBar(maximum=100, value=0)
        hp.addWidget(self.boss_bar, 1)
        v.addLayout(hp)

        q = QGroupBox("Session queue: tick raids to farm in order; runs 0 = until limits")
        ql = QVBoxLayout(q)
        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(["Farm", "Raid", "Difficulty", "Runs"])
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queue_table.verticalHeader().hide()
        ql.addWidget(self.queue_table)
        v.addWidget(q, 1)

        btns = QHBoxLayout()
        self.start_btn = QPushButton("▶  Start", objectName="start", clicked=self._start)
        self.pause_btn = QPushButton("❚❚  Pause", objectName="pause", clicked=self._pause)
        self.stop_btn = QPushButton("■  Stop", objectName="stop", clicked=self._stop)
        for b in (self.start_btn, self.pause_btn, self.stop_btn):
            b.setMinimumHeight(42)
            btns.addWidget(b)
        v.addLayout(btns)
        return w

    def _update_banner(self):
        if self.settings.dry_run:
            self.banner.setText("DRY RUN: inputs are logged, not sent")
            self.banner.setStyleSheet("background:#713f12;color:#fde68a;")
        else:
            self.banner.setText("LIVE INPUT")
            self.banner.setStyleSheet("background:#7f1d1d;color:#fecaca;")

    def _fill_queue(self):
        saved = {q["profile"]: q for q in self.settings.queue}
        self.queue_table.setRowCount(0)
        for p in self.profiles:
            r = self.queue_table.rowCount()
            self.queue_table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if p["id"] in saved else Qt.Unchecked)
            chk.setData(Qt.UserRole, p["id"])
            self.queue_table.setItem(r, 0, chk)
            name = QTableWidgetItem(p["name"] + ("" if p["verified"] else "  (unverified)"))
            name.setFlags(Qt.ItemIsEnabled)
            self.queue_table.setItem(r, 1, name)
            diff = QTableWidgetItem(p["difficulty"])
            diff.setFlags(Qt.ItemIsEnabled)
            self.queue_table.setItem(r, 2, diff)
            spin = QSpinBox(maximum=100000)
            spin.setValue(int(saved.get(p["id"], {}).get("runs", 0)))
            self.queue_table.setCellWidget(r, 3, spin)

    def _save_queue(self):
        q = []
        for r in range(self.queue_table.rowCount()):
            it = self.queue_table.item(r, 0)
            if it.checkState() == Qt.Checked:
                q.append({"profile": it.data(Qt.UserRole), "runs": self.queue_table.cellWidget(r, 3).value()})
        self.settings.queue = q
        return q

    def _start(self):
        if self._busy():
            return
        q = self._save_queue()
        if not q:
            QMessageBox.information(self, "Nothing queued", "Tick at least one raid in the session queue.")
            return
        queue = [(self.store.get(i["profile"]), i["runs"]) for i in q]
        queue = [(p, n) for p, n in queue if p]
        unverified = [p["name"] for p, _ in queue if not p["verified"]]
        if not self.settings.dry_run and unverified and QMessageBox.question(
                self, "Unverified profiles",
                "These profiles are not marked verified:\n  " + "\n  ".join(unverified) +
                "\n\nTest them in the Testing page first. Start anyway?") != QMessageBox.Yes:
            return
        self.settings.save()
        self.engine = self._build_engine()
        try:
            self.engine.start(queue)
        except ValueError as e:
            QMessageBox.critical(self, "Profile invalid", str(e))
            return
        self.stats.session_runs.clear()
        self.session_start = time.monotonic()
        self._refresh_dashboard()

    def _pause(self):
        if self.engine and self.engine.running:
            self.engine.toggle_pause()
            paused = self.engine.rt.pause_event.is_set()
            self.pause_btn.setText("▶  Resume" if paused else "❚❚  Pause")
            if paused:
                self._on_state("PAUSED")

    def _stop(self):
        if self.engine:
            self.engine.stop()

    def _on_state(self, s: str):
        color = STATE_COLORS.get(s.split(":")[0], ACCENT)
        self.state_pill.setText(s)
        self.state_pill.setStyleSheet(f"background:{color};color:#0f1115;")
        running = bool(self.engine and self.engine.running) and s != "STOPPED"
        self.start_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running)
        if s in ("RESUMED", "STOPPED"):
            self.pause_btn.setText("❚❚  Pause")

    def _on_profile(self, p):
        self.current_profile = p
        self.cards["raid"].setText(p["name"])
        self.cards["difficulty"].setText(p["difficulty"])
        obj = p["objective"] + (f": {p['objective_target']}" if p["objective_target"] else "")
        self.cards["objective"].setText(obj)

    def _on_run(self, rec):
        self._refresh_dashboard()
        self._refresh_stats()

    def _on_finished(self, reason):
        self.session_start = None
        self._on_state("STOPPED")
        self.boss_bar.setValue(0)
        if not reason.startswith(("queue finished", "stopped by user")):
            QMessageBox.warning(self, "Session stopped", reason)

    def _tick(self):
        if self.session_start is not None:
            self.cards["time"].setText(fmt_dur(time.monotonic() - self.session_start))

    def _refresh_dashboard(self):
        s = self.stats.session_summary()
        self.cards["runs"].setText(f"{s['completed']} / {s['failed']}")
        self.cards["rate"].setText(f"{s['success_rate']:.0%}" if s["runs"] else "-")
        self.cards["avg"].setText(fmt_dur(s["avg_completion_s"]) if s["completed"] else "-")
        self.cards["deaths"].setText(str(s["deaths"]))
        for k in REWARD_KEYS:
            ph = s["rewards_per_hour"].get(k)
            self.cards[k].setText(fmt_num(s["rewards"].get(k, 0)) + (f"  ({fmt_num(ph)}/h)" if ph else ""))

    # =============================================================== raids
    def _raids_page(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.addWidget(QLabel("Raid profiles", objectName="h1"))
        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        self.profile_list = QListWidget()
        self.profile_list.currentRowChanged.connect(self._load_profile_form)
        lv.addWidget(self.profile_list)
        for text, fn in [("New", self._new_profile), ("Duplicate", self._dup_profile), ("Delete", self._del_profile)]:
            lv.addWidget(QPushButton(text, clicked=fn))
        split.addWidget(left)

        tabs = QTabWidget()
        # --- general
        gen = QWidget()
        f = QFormLayout(gen)
        self.f_name = QLineEdit()
        self.f_module = QLineEdit()
        self.f_verified = QCheckBox("Tested end-to-end in the current game version")
        self.f_diff = QComboBox()
        self.f_obj = QComboBox()
        self.f_obj.addItems(OBJECTIVES)
        self.f_target = QLineEdit()
        self.f_target.setPlaceholderText("material or crate name (for those objectives)")
        self.f_level = QSpinBox(maximum=1_000_000)
        self.f_attempts = QSpinBox(maximum=100000)
        self.f_run_to = QSpinBox(maximum=7200, suffix=" s")
        self.f_enter_to = QSpinBox(maximum=600, suffix=" s")
        self.f_rewards = {k: QSpinBox(maximum=2_000_000_000) for k in REWARD_KEYS}
        self.f_notes = QPlainTextEdit()
        self.f_notes.setMaximumHeight(110)
        for label, wid in [("Name", self.f_name), ("Module", self.f_module), ("", self.f_verified),
                           ("Difficulty", self.f_diff), ("Objective", self.f_obj), ("Target item", self.f_target),
                           ("Required level (0 = unknown)", self.f_level), ("Max attempts (0 = unlimited)", self.f_attempts),
                           ("Run timeout", self.f_run_to), ("Entry timeout", self.f_enter_to)]:
            f.addRow(label, wid)
        for k, sp in self.f_rewards.items():
            f.addRow(f"{k} per run (if no OCR)", sp)
        f.addRow("Notes", self.f_notes)
        rec = QHBoxLayout()
        self.rec_label = QLabel("")
        rec.addWidget(QPushButton("Recommend difficulty", clicked=self._recommend))
        rec.addWidget(self.rec_label, 1)
        f.addRow(rec)
        tabs.addTab(gen, "General")

        # --- combat
        cw = QWidget()
        cv = QVBoxLayout(cw)
        self.ab_table = QTableWidget(0, 6)
        self.ab_table.setHorizontalHeaderLabels(["Name", "Key", "Cooldown s", "Priority", "Hold s", "Enabled"])
        self.ab_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        cv.addWidget(QLabel("Abilities: highest priority ready ability fires each tick. Keys: a-z, 0-9, "
                            "space, shift, f1..f12, mouse1, mouse2."))
        cv.addWidget(self.ab_table)
        ab_btns = QHBoxLayout()
        ab_btns.addWidget(QPushButton("Add ability", clicked=lambda: self._add_ability_row({})))
        ab_btns.addWidget(QPushButton("Remove selected", clicked=lambda: self.ab_table.removeRow(self.ab_table.currentRow())))
        cv.addLayout(ab_btns)
        cf = QFormLayout()
        self.c_tick = QDoubleSpinBox(decimals=2, minimum=0.02, maximum=5, singleStep=0.05, suffix=" s")
        self.c_app_key = QLineEdit()
        self.c_app_s = QDoubleSpinBox(decimals=2, maximum=30, suffix=" s")
        self.c_lock = QLineEdit()
        self.c_lowhp = QSpinBox(maximum=99, suffix=" %")
        self.c_lowhp_ab = QLineEdit()
        self.c_noprog = QSpinBox(minimum=3, maximum=600, suffix=" s")
        self.c_maxrec = QSpinBox(maximum=50)
        for label, wid in [("Attack interval (tick)", self.c_tick), ("Approach key", self.c_app_key),
                           ("Approach hold", self.c_app_s), ("Lock-on key", self.c_lock),
                           ("Low-HP threshold (0 = off)", self.c_lowhp), ("Low-HP ability name", self.c_lowhp_ab),
                           ("No-progress timeout", self.c_noprog), ("Max recoveries", self.c_maxrec)]:
            cf.addRow(label, wid)
        cv.addLayout(cf)
        self.c_recovery = QPlainTextEdit()
        self.c_recovery.setMaximumHeight(80)
        cv.addWidget(QLabel("Recovery steps (JSON list), run when boss HP stops changing:"))
        cv.addWidget(self.c_recovery)
        tabs.addTab(cw, "Combat")

        # --- advanced JSON
        aw = QWidget()
        av = QVBoxLayout(aw)
        av.addWidget(QLabel("Entry/claim/restart steps, detectors, OCR regions, drops. "
                            "Schema: docs/ADDING_RAIDS.md"))
        self.adv = QPlainTextEdit()
        self.adv.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        av.addWidget(self.adv)
        tabs.addTab(aw, "Advanced (JSON)")

        rv = QWidget()
        rl = QVBoxLayout(rv)
        rl.addWidget(tabs, 1)
        self.val_label = QLabel("")
        self.val_label.setWordWrap(True)
        rl.addWidget(self.val_label)
        rl.addWidget(QPushButton("Save profile", objectName="start", clicked=self._save_profile))
        split.addWidget(rv)
        split.setSizes([230, 800])
        outer.addWidget(split, 1)
        return w

    def _refresh_profiles(self, select: str | None = None):
        self.profiles = self.store.list()
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for p in self.profiles:
            it = QListWidgetItem(("✔ " if p["verified"] else "• ") + p["name"])
            it.setData(Qt.UserRole, p["id"])
            self.profile_list.addItem(it)
        self.profile_list.blockSignals(False)
        ids = [p["id"] for p in self.profiles]
        sel = select or self.settings.last_profile
        self.profile_list.setCurrentRow(ids.index(sel) if sel in ids else 0)
        self._load_profile_form(self.profile_list.currentRow())
        self._fill_queue()
        if hasattr(self, "test_profile"):
            cur = self.test_profile.currentData()
            self.test_profile.clear()
            for p in self.profiles:
                self.test_profile.addItem(p["name"], p["id"])
            if cur in ids:
                self.test_profile.setCurrentIndex(ids.index(cur))

    def _add_ability_row(self, a: dict):
        r = self.ab_table.rowCount()
        self.ab_table.insertRow(r)
        for c, k in enumerate(["name", "key", "cooldown", "priority", "hold"]):
            self.ab_table.setItem(r, c, QTableWidgetItem(str(a.get(k, {"cooldown": 1, "priority": 1, "hold": 0}.get(k, "")))))
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if a.get("enabled", True) else Qt.Unchecked)
        self.ab_table.setItem(r, 5, chk)

    def _load_profile_form(self, row: int):
        if row < 0 or row >= len(self.profiles):
            return
        p = self.profiles[row]
        self.editing = copy.deepcopy(p)
        self.settings.last_profile = p["id"]
        self.f_name.setText(p["name"])
        self.f_module.setText(p["module"])
        self.f_verified.setChecked(bool(p["verified"]))
        self.f_diff.clear()
        self.f_diff.addItems(p["difficulties"])
        self.f_diff.setCurrentText(p["difficulty"])
        self.f_obj.setCurrentText(p["objective"])
        self.f_target.setText(p["objective_target"])
        self.f_level.setValue(int(p["required_level"] or 0))
        self.f_attempts.setValue(int(p["max_attempts"]))
        self.f_run_to.setValue(int(p["run_timeout_s"]))
        self.f_enter_to.setValue(int(p["enter_timeout_s"]))
        for k, sp in self.f_rewards.items():
            sp.setValue(int(p["rewards_per_run"].get(k, 0)))
        self.f_notes.setPlainText(p["notes"])
        c = p["combat"]
        self.ab_table.setRowCount(0)
        for a in c["abilities"]:
            self._add_ability_row(a)
        self.c_tick.setValue(float(c["tick_interval"]))
        self.c_app_key.setText(c["approach"].get("key", ""))
        self.c_app_s.setValue(float(c["approach"].get("seconds", 0)))
        self.c_lock.setText(c.get("lock_on_key", ""))
        self.c_lowhp.setValue(int(c.get("low_health_pct", 0)))
        self.c_lowhp_ab.setText(c.get("low_health_ability", ""))
        self.c_noprog.setValue(int(c["no_progress_timeout"]))
        self.c_maxrec.setValue(int(c["max_recoveries"]))
        self.c_recovery.setPlainText(json.dumps(c["recovery_steps"], indent=1))
        self.adv.setPlainText(json.dumps({k: p[k] for k in ADVANCED_KEYS}, indent=2))
        self.val_label.setText("")
        self.rec_label.setText("")

    def _form_to_profile(self) -> dict:
        p = copy.deepcopy(self.editing)
        adv = json.loads(self.adv.toPlainText())  # raises ValueError on bad JSON
        for k in ADVANCED_KEYS:
            if k in adv:
                p[k] = adv[k]
        p.update(name=self.f_name.text().strip(), module=self.f_module.text().strip() or "generic",
                 verified=self.f_verified.isChecked(), difficulty=self.f_diff.currentText(),
                 objective=self.f_obj.currentText(), objective_target=self.f_target.text().strip(),
                 required_level=self.f_level.value() or None, max_attempts=self.f_attempts.value(),
                 run_timeout_s=self.f_run_to.value(), enter_timeout_s=self.f_enter_to.value(),
                 notes=self.f_notes.toPlainText())
        p["rewards_per_run"] = {k: sp.value() for k, sp in self.f_rewards.items()}
        abilities = []
        for r in range(self.ab_table.rowCount()):
            cell = lambda c: (self.ab_table.item(r, c).text() if self.ab_table.item(r, c) else "").strip()
            abilities.append({"name": cell(0) or f"Ability {r + 1}", "key": cell(1),
                              "cooldown": float(cell(2) or 0), "priority": int(float(cell(3) or 0)),
                              "hold": float(cell(4) or 0),
                              "enabled": self.ab_table.item(r, 5).checkState() == Qt.Checked})
        c = p["combat"]
        c.update(abilities=abilities, tick_interval=self.c_tick.value(),
                 approach={"key": self.c_app_key.text().strip(), "seconds": self.c_app_s.value()},
                 lock_on_key=self.c_lock.text().strip(), low_health_pct=self.c_lowhp.value(),
                 low_health_ability=self.c_lowhp_ab.text().strip(), no_progress_timeout=self.c_noprog.value(),
                 max_recoveries=self.c_maxrec.value(), recovery_steps=json.loads(self.c_recovery.toPlainText() or "[]"))
        return normalize(p)

    def _save_profile(self):
        try:
            p = self._form_to_profile()
            registry.raid_class(p["module"])
        except (ValueError, TypeError, ImportError) as e:
            self.val_label.setText(f"<span style='color:#f87171'>Not saved: {e}</span>")
            return
        errs = validate(p)
        self.store.save(p)
        self._refresh_profiles(select=p["id"])  # reloads the form, so set the message after
        self.val_label.setText("<span style='color:#4ade80'>Saved.</span>" if not errs else
                               "<span style='color:#facc15'>Saved with problems (cannot run until fixed):<br>"
                               + "<br>".join(errs) + "</span>")

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New profile", "Raid name:")
        if ok and name.strip():
            p = normalize({"name": name.strip()})
            self.store.save(p)
            self._refresh_profiles(select=p["id"])

    def _dup_profile(self):
        if not self.profiles:
            return
        p = copy.deepcopy(self.profiles[self.profile_list.currentRow()])
        p["name"] += " (copy)"
        p["id"] = ""
        p["verified"] = False
        p = normalize(p)
        self.store.save(p)
        self._refresh_profiles(select=p["id"])

    def _del_profile(self):
        if not self.profiles:
            return
        p = self.profiles[self.profile_list.currentRow()]
        if QMessageBox.question(self, "Delete", f"Delete profile {p['name']!r}? This cannot be undone.") == QMessageBox.Yes:
            self.store.delete(p["id"])
            self._refresh_profiles()

    def _recommend(self):
        p = self.editing
        r = self.stats.recommend(p["id"], p["objective"], p["objective_target"])
        if not r:
            self.rec_label.setText("Need ≥3 recorded runs on a difficulty to recommend.")
            return
        best, val, scored = r
        detail = ", ".join(f"{d}: {fmt_num(v)}/h" for d, v in sorted(scored.items(), key=lambda kv: -kv[1]))
        self.rec_label.setText(f"Best for {p['objective']}: <b>{best}</b> ({detail})")

    # =============================================================== stats
    def _stats_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 18, 20, 18)
        top = QHBoxLayout()
        top.addWidget(QLabel("Statistics", objectName="h1"))
        top.addStretch()
        top.addWidget(QPushButton("Refresh", clicked=self._refresh_stats))
        v.addLayout(top)
        v.addWidget(QLabel("All-time by raid and difficulty (from logs/history.jsonl)"))
        self.sum_table = QTableWidget(0, 10)
        self.sum_table.setHorizontalHeaderLabels(["Raid", "Difficulty", "Runs", "Success", "Avg clear",
                                                  "Runs/h", "EXP/h", "Awakened/h", "Yen/h", "Drops/h"])
        self.sum_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.sum_table, 1)
        v.addWidget(QLabel("Run history (latest first)"))
        self.hist_table = QTableWidget(0, 8)
        self.hist_table.setHorizontalHeaderLabels(["Time", "Raid", "Difficulty", "Result", "Duration",
                                                   "Deaths", "Rewards / drops", "Reason"])
        self.hist_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        v.addWidget(self.hist_table, 1)
        for t in (self.sum_table, self.hist_table):
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.verticalHeader().hide()
        return w

    def _refresh_stats(self):
        hist = self.stats.history()
        names = {p["id"]: p["name"] for p in self.profiles}
        groups: dict = {}
        for r in hist:
            groups.setdefault((r.profile, r.difficulty), []).append(r)
        self.sum_table.setRowCount(0)
        for (pid, diff), runs in sorted(groups.items()):
            s = summarize(runs)
            rph = s["rewards_per_hour"]
            row = [names.get(pid, pid), diff, str(s["runs"]), f"{s['success_rate']:.0%}",
                   fmt_dur(s["avg_completion_s"]), f"{s['runs_per_hour']:.1f}", fmt_num(rph.get("player_exp", 0)),
                   fmt_num(rph.get("awakened_exp", 0)), fmt_num(rph.get("yen", 0)),
                   ", ".join(f"{k} {v:.1f}" for k, v in s["drops_per_hour"].items()) or "-"]
            self._table_row(self.sum_table, row)
        self.hist_table.setRowCount(0)
        for r in reversed(hist[-300:]):
            got = {**{k: fmt_num(v) for k, v in r.rewards.items() if v}, **r.drops}
            self._table_row(self.hist_table, [
                time.strftime("%m-%d %H:%M", time.localtime(r.started)), names.get(r.profile, r.profile),
                r.difficulty, "✔" if r.success else "✘", fmt_dur(r.duration), str(r.deaths),
                ", ".join(f"{k}: {v}" for k, v in got.items()), r.reason])

    @staticmethod
    def _table_row(table, values):
        r = table.rowCount()
        table.insertRow(r)
        for c, val in enumerate(values):
            table.setItem(r, c, QTableWidgetItem(val))

    # =============================================================== logs
    def _logs_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 18, 20, 18)
        top = QHBoxLayout()
        top.addWidget(QLabel("Logs", objectName="h1"))
        top.addStretch()
        self.log_level = QComboBox()
        self.log_level.addItems(["INFO", "DEBUG", "WARNING", "ERROR"])
        top.addWidget(QLabel("Show"))
        top.addWidget(self.log_level)
        top.addWidget(QPushButton("Clear", clicked=lambda: self.log_view.clear()))
        v.addLayout(top)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        v.addWidget(self.log_view)
        v.addWidget(QLabel(f"Full log file: {LOGS_DIR / 'macro.log'}"))
        return w

    def _append_log(self, text: str, level: int):
        if level < logging.getLevelName(self.log_level.currentText()):
            return
        color = {logging.ERROR: "#f87171", logging.WARNING: "#facc15", logging.DEBUG: "#64748b"}.get(level, "#e5e7eb")
        self.log_view.appendHtml(f"<span style='color:{color}'>{text.replace('<', '&lt;')}</span>")

    # =============================================================== testing
    def _testing_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 18, 20, 18)
        v.addWidget(QLabel("Testing mode", objectName="h1"))
        v.addWidget(QLabel("Verify each piece before farming. Tests honour dry run; F8 / Stop aborts any test."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Profile"))
        self.test_profile = QComboBox()
        row.addWidget(self.test_profile, 1)
        v.addLayout(row)

        g = QGroupBox("Vision")
        gl = QHBoxLayout(g)
        gl.addWidget(QPushButton("Capture screenshot", clicked=self._t_screenshot))
        gl.addWidget(QPushButton("Capture template…", clicked=self._t_template))
        gl.addWidget(QPushButton("Test all detectors", clicked=self._t_detectors))
        v.addWidget(g)

        g2 = QGroupBox("Input & actions (run on the worker thread; focus switches to Roblox)")
        gl2 = QGridLayout(g2)
        self.test_key = QLineEdit("1")
        self.test_key.setMaximumWidth(90)
        gl2.addWidget(QLabel("Key"), 0, 0)
        gl2.addWidget(self.test_key, 0, 1)
        gl2.addWidget(QPushButton("Press key", clicked=self._t_key), 0, 2)
        self.test_secs = QSpinBox(minimum=1, maximum=300, value=10, suffix=" s")
        gl2.addWidget(QLabel("Combat"), 0, 3)
        gl2.addWidget(self.test_secs, 0, 4)
        gl2.addWidget(QPushButton("Run rotation", clicked=self._t_rotation), 0, 5)
        for i, (label, fn) in enumerate([("Entry steps", self._t_steps("entry_steps")),
                                         ("Claim steps", self._t_steps("claim_steps")),
                                         ("Restart steps", self._t_steps("restart_steps")),
                                         ("Read rewards (OCR)", self._t_ocr),
                                         ("One full raid", self._t_full),
                                         ("■ Stop test", self._stop)]):
            gl2.addWidget(QPushButton(label, clicked=fn), 1, i)
        v.addWidget(g2)

        split = QSplitter()
        self.det_table = QTableWidget(0, 3)
        self.det_table.setHorizontalHeaderLabels(["Detector", "Result", "Confidence / value"])
        self.det_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.det_table.verticalHeader().hide()
        split.addWidget(self.det_table)
        self.preview = QLabel("No screenshot yet")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumWidth(420)
        split.addWidget(self.preview)
        v.addWidget(split, 1)
        return w

    def _test_vision(self) -> Vision:
        window = make_window(self.settings)
        self.templates.reload()
        return Vision(ScreenSource(window), self.templates, self.settings.template_reference_height,
                      self.settings.default_threshold, self.settings.tesseract_cmd)

    def _grab_or_warn(self):
        if self._busy():
            return None, None
        vis = self._test_vision()
        try:
            frame = vis.frame()
        except Exception as e:
            frame = None
            log.error("Capture failed: %s", e)
        if frame is None:
            QMessageBox.warning(self, "Capture failed",
                                f"Window {self.settings.window_title!r} not found. Open Roblox (windowed or "
                                "fullscreen, not minimised) or set a manual region in Settings.")
            return None, None
        self.preview.setPixmap(to_pixmap(frame).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return vis, frame

    def _t_screenshot(self):
        vis, frame = self._grab_or_warn()
        if frame is not None:
            p = vis.save_frame(LOGS_DIR / "screens", "manual")
            log.info("Screenshot %dx%d saved to %s", frame.shape[1], frame.shape[0], p)

    def _t_template(self):
        vis, frame = self._grab_or_warn()
        if frame is None:
            return
        dlg = CaptureDialog(frame, self)
        if dlg.exec() and dlg.result_data:
            name, crop, frac = dlg.result_data
            path = TEMPLATES_DIR / f"{name}.png"
            if path.exists() and QMessageBox.question(self, "Overwrite", f"{path.name} exists. Replace it?") != QMessageBox.Yes:
                return
            cv2.imwrite(str(path), crop)
            QGuiApplication.clipboard().setText(json.dumps(frac))
            log.info("Saved template %s (%dx%d). Region %s copied to clipboard. Captured at height %d; "
                     "reference height setting is %d.", path.name, crop.shape[1], crop.shape[0], frac,
                     frame.shape[0], self.settings.template_reference_height)

    def _selected_test_profile(self):
        pid = self.test_profile.currentData()
        return self.store.get(pid) if pid else None

    def _t_detectors(self):
        p = self._selected_test_profile()
        vis, frame = self._grab_or_warn()
        if frame is None or not p:
            return
        self.det_table.setRowCount(0)
        fmt = {"difficulty": p["difficulty"]}
        for name, det in p["detection"].items():
            if not det:
                self._table_row(self.det_table, [name, "not configured", ""])
            elif "template" in det:
                tname = det["template"].format(**fmt)
                if self.templates.get(tname) is None:
                    self._table_row(self.det_table, [name, "MISSING TEMPLATE", f"templates/{tname}.png"])
                    continue
                m = vis.check(det, frame, fmt)
                self._table_row(self.det_table, [name, "FOUND" if m else "no", f"{m.confidence:.3f} (need {det.get('threshold', self.settings.default_threshold)})"])
            else:
                val = vis.bar(det, frame)
                self._table_row(self.det_table, [name, "bar", "-" if val is None else f"{val:.1%}"])
        steps = [s for k in ("entry_steps", "claim_steps", "restart_steps", "respawn_steps") for s in p[k]]
        for s in steps:
            if "template" in s:
                tname = s["template"].format(**fmt)
                if self.templates.get(tname) is None:
                    self._table_row(self.det_table, [f"step: {tname}", "MISSING TEMPLATE", f"templates/{tname}.png"])
                else:
                    m = vis.check({k: s[k] for k in ("template", "region", "threshold", "color") if k in s}, frame, fmt)
                    self._table_row(self.det_table, [f"step: {tname}", "FOUND" if m else "no (may be another screen)",
                                                     f"{m.confidence:.3f}"])

    def _run_test(self, label, fn_of_raid):
        if self._busy():
            return
        p = self._selected_test_profile()
        if not p:
            return
        self.settings.save()
        self.engine = self._build_engine()
        raid = registry.create(p, self.engine.rt)
        self.engine.run_single(lambda: fn_of_raid(raid), label)

    def _t_key(self):
        key = self.test_key.text().strip()

        def go(raid):
            raid.rt.window.focus()
            raid.rt.sleep(0.5)
            raid.rt.input.tap(key)
            log.info("Pressed %s", key)
        self._run_test(f"press {key}", go)

    def _t_rotation(self):
        secs = self.test_secs.value()

        def go(raid):
            raid.rt.window.focus()
            end = time.monotonic() + secs
            while time.monotonic() < end:
                ab = raid.rotation.next_ready(time.monotonic())
                if ab:
                    raid.rt.input.tap(ab["key"], float(ab.get("hold", 0)), raid.rt.stopping)
                    raid.rotation.mark_used(ab, time.monotonic())
                    log.info("Used %s", ab["name"])
                raid.rt.sleep(float(raid.combat["tick_interval"]))
        self._run_test("rotation", go)

    def _t_steps(self, group):
        return lambda: self._run_test(group, lambda raid: raid.run_steps(raid.p[group], group))

    def _t_ocr(self):
        def go(raid):
            raid.rt.grab()
            rewards, drops = raid.read_rewards()
            log.info("Rewards read: %s, drops %s", rewards, drops)
        self._run_test("read rewards", go)

    def _t_full(self):
        def go(raid):
            res = raid.run_once()
            log.info("Full run result: %s", res)
        self._run_test("one full raid", go)

    # =============================================================== settings
    def _settings_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 18, 20, 18)
        v.addWidget(QLabel("Settings", objectName="h1"))
        form = QFormLayout()
        self.s_widgets = {}
        labels = {
            "window_title": "Roblox window title", "manual_region": "Manual region [left, top, w, h]",
            "use_manual_region": "Use manual region instead of window", "template_reference_height":
            "Template reference height (px)", "default_threshold": "Default match threshold",
            "dry_run": "Dry run (log inputs, send nothing)", "emergency_stop_hotkey": "Emergency stop hotkey",
            "pause_hotkey": "Pause hotkey", "max_runtime_minutes": "Max runtime (min, 0 = none)",
            "max_consecutive_failures": "Max consecutive failures", "retry_delay_seconds": "Retry delay (s)",
            "unknown_state_timeout": "Unknown-state timeout (s)", "require_window_focus": "Require Roblox focus",
            "save_screenshot_on_error": "Save screenshot on error", "tesseract_cmd": "Tesseract path (optional)"}
        for f in fields(Settings):
            if f.name not in labels:
                continue
            val = getattr(self.settings, f.name)
            if isinstance(val, bool):
                wid = QCheckBox()
                wid.setChecked(val)
            elif isinstance(val, int):
                wid = QSpinBox(maximum=100000)
                wid.setValue(val)
            elif isinstance(val, float):
                wid = QDoubleSpinBox(decimals=2, maximum=100000, singleStep=0.05)
                wid.setValue(val)
            else:
                wid = QLineEdit(json.dumps(val) if isinstance(val, list) else str(val))
            self.s_widgets[f.name] = wid
            form.addRow(labels[f.name], wid)
        v.addLayout(form)
        v.addWidget(QLabel("Hotkey changes apply after restarting the app."))
        v.addWidget(QPushButton("Save settings", objectName="start", clicked=self._save_settings))
        v.addStretch()
        return w

    def _save_settings(self):
        if self._busy():
            return
        for name, wid in self.s_widgets.items():
            cur = getattr(self.settings, name)
            if isinstance(wid, QCheckBox):
                val = wid.isChecked()
            elif isinstance(wid, (QSpinBox, QDoubleSpinBox)):
                val = wid.value()
            elif isinstance(cur, list):
                try:
                    val = json.loads(wid.text())
                    assert isinstance(val, list) and len(val) == 4
                except (ValueError, AssertionError):
                    QMessageBox.warning(self, "Invalid", f"{name} must be a JSON list of 4 numbers.")
                    return
            else:
                val = wid.text().strip()
            setattr(self.settings, name, val)
        if not self.settings.dry_run and QMessageBox.question(
                self, "Live input", "Dry run is off: the macro will send real keyboard and mouse input. "
                "Keep your hand near the emergency stop key. Continue?") != QMessageBox.Yes:
            self.settings.dry_run = True
            self.s_widgets["dry_run"].setChecked(True)
        self.settings.save()
        self._update_banner()
        log.info("Settings saved")

    def _nav_changed(self, i):
        self.pages.setCurrentIndex(i)
        if self.nav.item(i).text() == "Statistics":
            self._refresh_stats()
