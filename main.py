"""JJZ Raid Farmer entry point."""
import logging
import sys
from logging.handlers import RotatingFileHandler

from config.settings import LOGS_DIR


def setup_logging():
    LOGS_DIR.mkdir(exist_ok=True)
    fh = RotatingFileHandler(LOGS_DIR / "macro.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    root.addHandler(sh)


def main():
    setup_logging()
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
