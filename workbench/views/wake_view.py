"""AeroBench 후류 프로파일 뷰 — 수직 방향 반경 속도 프로파일.

X = 속도 (m/s), Y = r/R. update_frame(values, t)만 외부 인터페이스.
"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QGraphicsRectItem, QHBoxLayout, QLabel,
                               QSlider, QVBoxLayout, QWidget)

from .. import profiles, theme
from ..widgets import StatTile

RHO = profiles.RHO


class WakeView(QWidget):
    def __init__(self, sim, parent=None):
        super().__init__(parent)
        self.sim = sim
        self.profile = profiles.AEROBENCH
        self.r_over_R = self.profile["r_over_R"]

        # ---- 좌: 프로파일 플롯
        self.plot = pg.PlotWidget()
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(False, False)
        self.plot.setLabel("bottom", "속도 (m/s)")
        self.plot.setLabel("left", "r/R")
        self.plot.setXRange(0.0, 36.0)
        self.plot.setYRange(0.0, 1.15)
        self.plot.showGrid(x=True, y=True, alpha=0.15)

        v_th, x_th = profiles.theory_profile()
        self.plot.plot(v_th, x_th,
                       pen=pg.mkPen(theme.DIM, width=1.5, style=Qt.DashLine),
                       name="이론")

        self.curve = self.plot.plot(
            pen=pg.mkPen(theme.CYAN, width=2),
            symbol="o", symbolSize=8,
            symbolPen=pg.mkPen("#ffffff", width=1.2), symbolBrush=None)

        # 허브 실루엣 (좌측 하단 회색 직사각형)
        hub = QGraphicsRectItem(QRectF(0.0, 0.0, 1.6, 0.13))
        hub.setBrush(QColor(123, 140, 153, 90))
        hub.setPen(pg.mkPen(None))
        self.plot.addItem(hub)

        legend = QLabel("── 실측 (13점)   - - 이론 참조   ▮ 허브")
        legend.setProperty("role", "dim")
        legend.setAlignment(Qt.AlignCenter)

        left = QVBoxLayout()
        left.addWidget(self.plot, 1)
        left.addWidget(legend)

        # ---- 우: RPM 슬라이더 + 타일 + 미니 플롯
        rpm_title = QLabel("RPM")
        rpm_title.setProperty("role", "dim")
        self.rpm_slider = QSlider(Qt.Horizontal)
        self.rpm_slider.setRange(2000, 8000)
        self.rpm_slider.setSingleStep(100)
        self.rpm_slider.setValue(sim.get_rpm())
        self.rpm_slider.valueChanged.connect(self._on_rpm)

        self.tile_rpm = StatTile("RPM", f"{sim.get_rpm()}", accent=theme.AMBER)
        self.tile_peak = StatTile("피크 속도 m/s @ r/R", accent=theme.CYAN)

        mini_title = QLabel("UIUC CT vs J (참조)")
        mini_title.setProperty("role", "dim")
        self.mini = pg.PlotWidget()
        self.mini.setFixedHeight(140)
        self.mini.setMenuEnabled(False)
        self.mini.setMouseEnabled(False, False)
        self.mini.setLabel("bottom", "J")
        self.mini.setLabel("left", "CT")
        self._J = np.linspace(0.2, 1.0, 60)
        self._CT = 0.115 - 0.085 * self._J - 0.02 * self._J ** 2
        self.mini.plot(self._J, self._CT, pen=pg.mkPen(theme.DIM, width=1.5))
        self.mini_dot = pg.ScatterPlotItem(
            size=9, pen=pg.mkPen(None), brush=pg.mkBrush(theme.AMBER))
        self.mini.addItem(self.mini_dot)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(rpm_title)
        right.addWidget(self.rpm_slider)
        right.addWidget(self.tile_rpm)
        right.addWidget(self.tile_peak)
        right.addSpacing(8)
        right.addWidget(mini_title)
        right.addWidget(self.mini)
        right.addStretch(1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(left, 1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(230)
        lay.addWidget(rw)

        self._update_mini_dot(sim.get_rpm())

    # ------------------------------------------------ 컨트롤
    def _on_rpm(self, rpm):
        self.sim.set_rpm(rpm)
        self.tile_rpm.set_value(f"{rpm}")
        self._update_mini_dot(rpm)

    def _update_mini_dot(self, rpm):
        # RPM ↑ → 전진비 J ↓ (고정 풍속 가정, 목업)
        j = float(np.interp(rpm, [2000, 8000], [0.92, 0.30]))
        ct = float(np.interp(j, self._J, self._CT))
        self.mini_dot.setData([j], [ct])

    # ------------------------------------------------ 프레임 갱신
    def update_frame(self, values: np.ndarray, t: float):
        q = np.clip(values[0:13], 0.0, None)
        v = np.sqrt(2.0 * q / RHO)
        self.curve.setData(v, self.r_over_R)
        i = int(np.argmax(v))
        self.tile_peak.set_value(f"{v[i]:.1f} @ {self.r_over_R[i]:.2f}")
