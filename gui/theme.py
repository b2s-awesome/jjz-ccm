"""Dark theme with a frog-green accent."""
ACCENT = "#4ade80"
STATE_COLORS = {
    "IDLE": "#64748b", "STOPPED": "#64748b", "PAUSED": "#facc15", "RESUMED": "#4ade80",
    "ENTERING": "#38bdf8", "LOADING": "#38bdf8", "SEEKING_BOSS": "#a78bfa", "COMBAT": "#f87171",
    "CLAIMING": "#4ade80", "RESTARTING": "#38bdf8", "RESPAWNING": "#fb923c",
}

STYLE = f"""
QWidget {{ background: #0f1115; color: #e5e7eb; font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; }}
QListWidget#nav {{ background: #151821; border: none; padding-top: 8px; }}
QListWidget#nav::item {{ padding: 12px 18px; border-left: 3px solid transparent; }}
QListWidget#nav::item:selected {{ background: #1c2130; border-left: 3px solid {ACCENT}; color: {ACCENT}; }}
QFrame#card {{ background: #171a23; border: 1px solid #252a37; border-radius: 10px; }}
QLabel#cardTitle {{ color: #94a3b8; font-size: 11px; text-transform: uppercase; background: transparent; }}
QLabel#cardValue {{ font-size: 20px; font-weight: 600; background: transparent; }}
QLabel#h1 {{ font-size: 20px; font-weight: 700; }}
QLabel#banner {{ border-radius: 6px; padding: 6px 10px; font-weight: 600; }}
QPushButton {{ background: #232838; border: 1px solid #2f3547; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #4b5563; }}
QPushButton#start {{ background: #166534; border-color: #22c55e; font-weight: 700; }}
QPushButton#stop {{ background: #7f1d1d; border-color: #ef4444; font-weight: 700; }}
QPushButton#pause {{ background: #713f12; border-color: #eab308; font-weight: 700; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTableWidget, QListWidget {{
    background: #12141b; border: 1px solid #2a2f3e; border-radius: 6px; padding: 4px; }}
QHeaderView::section {{ background: #1c2130; border: none; padding: 5px; color: #94a3b8; }}
QProgressBar {{ background: #12141b; border: 1px solid #2a2f3e; border-radius: 6px; text-align: center; height: 18px; }}
QProgressBar::chunk {{ background: #ef4444; border-radius: 5px; }}
QTabWidget::pane {{ border: 1px solid #252a37; border-radius: 6px; }}
QTabBar::tab {{ background: #151821; padding: 7px 14px; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QCheckBox::indicator, QTableWidget::indicator {{ width: 14px; height: 14px; border: 1px solid #64748b; border-radius: 3px; background: #12141b; }}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QGroupBox {{ border: 1px solid #252a37; border-radius: 8px; margin-top: 14px; padding-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: #94a3b8; }}
"""
