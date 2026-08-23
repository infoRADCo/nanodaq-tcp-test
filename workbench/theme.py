"""다크 테마 팔레트 + 앱 전역 스타일시트."""

BG = "#0f151c"      # 배경
PANEL = "#161f29"   # 패널
LINE = "#26333f"    # 경계선
INK = "#dce8f0"     # 본문 텍스트
DIM = "#7b8c99"     # 흐린 텍스트
CYAN = "#3fd3dc"    # 트레이스 / 강조
AMBER = "#e8b04b"   # 경고 / 마커
OK = "#4ecb8d"      # 정상
BAD = "#e2685c"     # 오류 / REC

MONO = "Consolas"
UI_FONT = "'Malgun Gothic', 'Segoe UI', sans-serif"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {INK};
    font-family: {UI_FONT};
    font-size: 12px;
}}
QLabel {{ background: transparent; }}
QFrame[panel="true"] {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
}}
QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 5px;
    padding: 6px 12px;
    color: {INK};
}}
QPushButton:hover {{ border-color: {CYAN}; }}
QPushButton:checked {{
    background: #14262c;
    border-color: {CYAN};
    color: {CYAN};
}}
QPushButton:disabled {{
    color: {DIM};
    background: {BG};
    border-color: {LINE};
}}
QPushButton[primary="true"]:enabled {{
    background: {CYAN};
    color: #062226;
    font-weight: 700;
    border: none;
}}
QPushButton[mode="true"] {{
    text-align: left;
    padding: 10px 14px;
    font-size: 13px;
}}
QTabWidget::pane {{ border: none; border-top: 1px solid {LINE}; }}
QTabBar {{ background: {BG}; }}
QTabBar::tab {{
    background: transparent;
    color: {DIM};
    padding: 9px 22px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    color: {CYAN};
    border-bottom: 2px solid {CYAN};
}}
QTabBar::tab:hover {{ color: {INK}; }}
QTableWidget {{
    background: {PANEL};
    alternate-background-color: #131b24;
    gridline-color: {LINE};
    border: 1px solid {LINE};
    border-radius: 6px;
}}
QTableWidget::item {{ padding: 2px 6px; }}
QHeaderView::section {{
    background: #121a23;
    color: {DIM};
    border: none;
    border-bottom: 1px solid {LINE};
    padding: 5px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: #121a23; border: none; }}
QListWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 4px;
}}
QListWidget::item {{ padding: 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background: #14262c; color: {CYAN}; }}
QSlider::groove:horizontal {{
    height: 4px;
    background: {LINE};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {CYAN}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px;
    margin: -6px 0;
    background: {CYAN};
    border-radius: 7px;
}}
QToolTip {{
    background: {PANEL};
    color: {INK};
    border: 1px solid {LINE};
}}
QMessageBox {{ background: {PANEL}; }}
QDialog {{ background: {BG}; }}
QScrollBar:vertical {{
    background: {BG}; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {LINE}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QLabel[pill="amber"] {{
    color: {AMBER}; border: 1px solid {AMBER};
    border-radius: 10px; padding: 2px 10px; font-weight: 600;
}}
QLabel[pill="green"] {{
    color: {OK}; border: 1px solid {OK};
    border-radius: 10px; padding: 2px 10px; font-weight: 600;
}}
QLabel[pill="red"] {{
    color: {BAD}; border: 1px solid {BAD};
    border-radius: 10px; padding: 2px 10px; font-weight: 600;
}}
QLabel[role="dim"] {{ color: {DIM}; }}
QLabel[role="mono"] {{ font-family: {MONO}; }}
QLabel[role="logo"] {{
    color: {CYAN}; font-weight: 800; font-size: 15px; letter-spacing: 2px;
}}
QLabel[role="ident"] {{ color: {DIM}; font-family: {MONO}; font-size: 11px; }}
"""


def repolish(widget):
    """동적 property 변경 후 스타일 재적용."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
