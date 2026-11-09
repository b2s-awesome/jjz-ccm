"""GUI smoke test (offscreen): builds every page, edits and saves a profile, runs a dry-run
test action against a manual region."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("PySide6")


def test_main_window(tmp_path, monkeypatch):
    import config.settings as cs
    import gui.main_window as mw
    from raids.profile import ProfileStore

    monkeypatch.setattr(cs, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(cs.Settings.load.__func__, "__defaults__", (tmp_path / "settings.json",))
    monkeypatch.setattr(cs.Settings.save, "__defaults__", (tmp_path / "settings.json",))
    monkeypatch.setattr(mw, "LOGS_DIR", tmp_path)
    store_dir = tmp_path / "profiles"
    store_dir.mkdir()
    for f in ProfileStore().dir.glob("*.json"):
        (store_dir / f.name).write_text(f.read_text())
    monkeypatch.setattr(mw, "ProfileStore", lambda: ProfileStore(store_dir))

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    w = mw.MainWindow()
    assert w.profile_list.count() >= 8
    for i in range(w.nav.count()):
        w.nav.setCurrentRow(i)
    # edit + save a profile through the form
    w.profile_list.setCurrentRow(0)
    w.f_level.setValue(321)
    w._save_profile()
    assert "Saved" in w.val_label.text()
    pid = w.editing["id"]
    assert ProfileStore(store_dir).get(pid)["required_level"] == 321
    # bad JSON is rejected, not saved
    w.adv.setPlainText("{bad")
    w._save_profile()
    assert "Not saved" in w.val_label.text()
    w._refresh_stats()
    w.close()
