"""Single source of truth for Blackfong Workspace colors and the Qt stylesheet.

Panes that build inline HTML should read from ``TOKENS`` instead of hard-coding hex values.
"""
from __future__ import annotations

from typing import Dict

TOKENS: Dict[str, str] = {
    # surfaces, darkest to lightest
    "bg": "#070b10",
    "surface": "#0b1219",
    "surface_raised": "#101a23",
    "surface_high": "#16232f",
    # lines
    "border": "#223344",
    "border_strong": "#2c4357",
    # text
    "text": "#e6edf2",
    "text_muted": "#7c93a8",
    "text_disabled": "#6b7884",
    # cyan accent
    "accent": "#1fb5cc",
    "accent_hover": "#4fe0f5",
    "accent_text": "#9fe9f5",
    "accent_dim": "#17697a",
    "selection": "#1b5f73",
    # status states
    "state_ok": "#35d0a0",
    "state_warn": "#f0b45a",
    "state_bad": "#ff6b6b",
    "state_unknown": "#6b7884",
    # chat bubbles / inline panels
    "bubble_assistant": "#12202b",
    "bubble_user": "#0f1b24",
    "bubble_system": "#0d1720",
    "bubble_system_border": "#27505e",
}

_FONT = '"Segoe UI", "Aptos", sans-serif'


def build_stylesheet(t: Dict[str, str] | None = None) -> str:
    t = t or TOKENS
    return f"""
        QWidget {{
            background: {t['bg']};
            color: {t['text']};
            font-family: {_FONT};
            font-size: 13px;
        }}
        QMainWindow, QTabWidget::pane, QDockWidget, QTextEdit, QLineEdit, QComboBox {{
            background: {t['surface']};
        }}
        QToolBar {{
            spacing: 8px;
            padding: 8px;
            border: 0;
            background: {t['surface_raised']};
        }}
        QTabBar::tab {{
            background: {t['surface_raised']};
            border: 1px solid {t['border']};
            border-bottom: 0;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 10px 14px;
            margin-right: 4px;
            color: {t['text_muted']};
        }}
        QTabBar::tab:selected {{
            background: {t['surface_high']};
            color: {t['accent_text']};
            border-color: {t['accent']};
        }}
        QTabWidget::pane {{
            border: 1px solid {t['border']};
            border-radius: 8px;
            padding: 6px;
        }}
        QTextEdit, QLineEdit, QComboBox {{
            border: 1px solid {t['border_strong']};
            border-radius: 6px;
            padding: 8px;
            selection-background-color: {t['selection']};
        }}
        QPushButton, QToolButton {{
            background: {t['surface_raised']};
            border: 1px solid {t['border_strong']};
            border-radius: 6px;
            padding: 9px 14px;
            color: {t['text']};
        }}
        QPushButton:hover, QToolButton:hover {{
            border-color: {t['accent']};
            background: {t['surface_high']};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {t['text_disabled']};
            border-color: {t['border']};
        }}
        QPushButton[toolbarRole="primary"], QToolButton[toolbarRole="primary"] {{
            background: {t['surface_high']};
            border: 1px solid {t['accent']};
            color: {t['accent_text']};
            font-weight: 600;
            padding: 10px 16px;
        }}
        QPushButton[toolbarRole="primary"]:hover, QToolButton[toolbarRole="primary"]:hover {{
            background: {t['accent_dim']};
            border-color: {t['accent_hover']};
        }}
        QPushButton[toolbarRole="menu"], QToolButton[toolbarRole="menu"] {{
            background: {t['surface_raised']};
            border: 1px solid {t['border_strong']};
            color: {t['text']};
            font-weight: 500;
        }}
        QLabel[toolbarRole="section"] {{
            background: transparent;
            color: {t['text_muted']};
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.18em;
            padding: 0 4px;
        }}
        QWidget[toolbarRole="statusCluster"] {{
            background: {t['surface']};
            border: 1px solid {t['border']};
            border-radius: 8px;
        }}
        QLabel[toolbarRole="statusHero"] {{
            background: {t['surface_high']};
            border: 1px solid {t['accent_dim']};
            border-radius: 6px;
            color: {t['accent_text']};
            font-weight: 700;
            padding: 6px 10px;
        }}
        QLabel[toolbarRole="statusMinor"] {{
            background: {t['surface_raised']};
            border: 1px solid {t['border']};
            border-radius: 6px;
            color: {t['text']};
            padding: 4px 8px;
        }}
        QDockWidget::title {{
            text-align: left;
            background: {t['surface_raised']};
            padding: 8px 10px;
            border-bottom: 1px solid {t['border']};
        }}
        QLabel[variant="kicker"] {{
            color: {t['accent']};
            font-size: 11px;
            letter-spacing: 0.16em;
            text-transform: uppercase;
        }}
        QLabel[variant="status"] {{
            color: {t['accent_text']};
        }}
        """
