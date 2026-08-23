"""공용 소형 위젯 — 스탯 타일, 알약 라벨 등."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from . import theme


class StatTile(QFrame):
    """제목(dim) + 모노스페이스 큰 숫자 타일."""

    def __init__(self, title: str, value: str = "—", accent: str = theme.INK,
                 parent=None):
        super().__init__(parent)
        self.setProperty("panel", "true")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        self._title = QLabel(title)
        self._title.setProperty("role", "dim")
        self._title.setStyleSheet("font-size: 11px;")
        self._value = QLabel(value)
        self._value.setStyleSheet(
            f"font-family: {theme.MONO}; font-size: 20px; font-weight: 600; "
            f"color: {accent};")
        self._value.setTextFormat(Qt.PlainText)
        lay.addWidget(self._title)
        lay.addWidget(self._value)

    def set_value(self, text: str):
        self._value.setText(text)


class Pill(QLabel):
    """색상 property 기반 알약 라벨. kind ∈ {amber, green, red}."""

    def __init__(self, text: str, kind: str = "amber", parent=None):
        super().__init__(text, parent)
        self.setProperty("pill", kind)
        self.setAlignment(Qt.AlignCenter)

    def set_kind(self, kind: str):
        if self.property("pill") != kind:
            self.setProperty("pill", kind)
            theme.repolish(self)
