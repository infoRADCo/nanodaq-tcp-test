"""EDF 인렛 왜곡 뷰 — 환형(annulus) 극좌표 히트맵 + 왜곡 지수.

update_frame(values, t)만 외부 인터페이스. 라이브/리플레이 공용.
"""

from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPolygonF
from PySide6.QtWidgets import (QGraphicsPathItem, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)
from scipy.interpolate import RBFInterpolator

from .. import profiles, theme
from ..widgets import StatTile

GRID = 200
EXTENT = 1.05          # 정규화 반경 좌표
R_IN, R_OUT = 0.35, 1.0
LEVELS = (-420.0, -110.0)  # 총압 결손 스케일 (고정)

# 깊은 결손(빨강) → 경고(앰버) → 정상(어두운 청록)
_CMAP = pg.ColorMap(
    pos=[0.0, 0.45, 1.0],
    color=[(226, 92, 74), (232, 176, 75), (24, 46, 62)],
)
_LUT = _CMAP.getLookupTable(0.0, 1.0, 256)  # (256, 3)
# 환형 밖은 배경색으로 칠함 (이 환경의 ImageItem은 알파를 무시하므로 RGB로 합성)
_BG_RGB = np.array([int(theme.PANEL[1:3], 16), int(theme.PANEL[3:5], 16),
                    int(theme.PANEL[5:7], 16)], dtype=np.uint8)


def _wedge_path(a0_deg, a1_deg, r0, r1):
    th = np.radians(np.linspace(a0_deg, a1_deg, 40))
    xs = np.concatenate([r1 * np.cos(th), r0 * np.cos(th[::-1])])
    ys = np.concatenate([r1 * np.sin(th), r0 * np.sin(th[::-1])])
    poly = QPolygonF([QPointF(float(x), float(y)) for x, y in zip(xs, ys)])
    path = QPainterPath()
    path.addPolygon(poly)
    path.closeSubpath()
    return path


class PolarView(QWidget):
    def __init__(self, sim, parent=None):
        super().__init__(parent)
        self.sim = sim
        self.profile = profiles.EDF
        self._trend = deque(maxlen=800)  # (t, index) 최근 60 s

        pts = self.profile["positions"]
        gx = np.linspace(-EXTENT, EXTENT, GRID)
        X, Y = np.meshgrid(gx, gx)
        grid_pts = np.column_stack([X.ravel(), Y.ravel()])
        rbf = RBFInterpolator(pts, np.eye(16), kernel="thin_plate_spline")
        self.W = rbf(grid_pts)
        R = np.sqrt(X ** 2 + Y ** 2)
        self.mask_out = ((R < R_IN) | (R > R_OUT)).ravel()  # 환형 밖

        # ---- 좌: 환형 히트맵
        glw = pg.GraphicsLayoutWidget()
        plot = glw.addPlot()
        plot.setAspectLocked(True)
        plot.hideAxis("bottom")
        plot.hideAxis("left")
        plot.setMenuEnabled(False)
        plot.setMouseEnabled(False, False)
        plot.setRange(xRange=(-1.15, 1.15), yRange=(-1.15, 1.15))

        self.img = pg.ImageItem()
        # setRect는 이미지 shape이 있어야 올바른 변환을 만든다 → 더미 프레임 선할당
        blank = np.zeros((GRID, GRID, 3), dtype=np.uint8)
        blank[:, :] = _BG_RGB
        self.img.setImage(blank, autoLevels=False)
        self.img.setRect(pg.QtCore.QRectF(-EXTENT, -EXTENT,
                                          2 * EXTENT, 2 * EXTENT))
        plot.addItem(self.img)

        self.wedge = QGraphicsPathItem()
        self.wedge.setBrush(QColor(232, 176, 75, 60))
        self.wedge.setPen(pg.mkPen(QColor(232, 176, 75, 140), width=1))
        plot.addItem(self.wedge)
        self.wedge.setVisible(False)

        self.dots = pg.ScatterPlotItem(
            x=pts[:, 0], y=pts[:, 1], size=10, symbol="o",
            pen=pg.mkPen("#ffffff", width=1.5), brush=None)
        plot.addItem(self.dots)

        self.center_label = pg.TextItem(anchor=(0.5, 0.5))
        self.center_label.setPos(0, 0)
        plot.addItem(self.center_label, ignoreBounds=True)
        self._set_center(0.0)

        legend = QLabel("○ 총압 프로브 16 · 면=보간 추정 · 음영=최악 60° 섹터")
        legend.setProperty("role", "dim")
        legend.setAlignment(Qt.AlignCenter)

        left = QVBoxLayout()
        left.addWidget(glw, 1)
        left.addWidget(legend)

        # ---- 우: 왜곡 지수 추이 + 컨트롤 + 타일
        trend_title = QLabel("왜곡 지수 추이 (60 s)")
        trend_title.setProperty("role", "dim")
        self.trend_plot = pg.PlotWidget()
        self.trend_plot.setFixedHeight(150)
        self.trend_plot.setMenuEnabled(False)
        self.trend_plot.setMouseEnabled(False, False)
        self.trend_plot.setYRange(0.0, 0.5)
        self.trend_plot.setLabel("bottom", "s")
        self.trend_curve = self.trend_plot.plot(
            pen=pg.mkPen(theme.AMBER, width=2))

        self.btn_shield = QPushButton("차폐 OFF")
        self.btn_shield.setCheckable(True)
        self.btn_shield.toggled.connect(self._on_shield)

        self.tile_deficit = StatTile("최대 결손 Pa", accent=theme.BAD)
        self.tile_throttle = StatTile("스로틀 %", "62")  # 정적 목업

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(trend_title)
        right.addWidget(self.trend_plot)
        right.addWidget(self.btn_shield)
        right.addWidget(self.tile_deficit)
        right.addWidget(self.tile_throttle)
        right.addStretch(1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(left, 1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(230)
        lay.addWidget(rw)

    # ------------------------------------------------ 컨트롤
    def _on_shield(self, on):
        self.sim.set_shield(on)
        self.btn_shield.setText("차폐 ON" if on else "차폐 OFF")

    def set_shield(self, on: bool):
        self.btn_shield.setChecked(on)

    def _set_center(self, idx):
        self.center_label.setHtml(
            f'<div style="text-align:center">'
            f'<span style="color:{theme.AMBER}; font-size:24pt; '
            f'font-family:{theme.MONO}; font-weight:700">{idx:.2f}</span><br>'
            f'<span style="color:{theme.DIM}; font-size:9pt">왜곡 지수</span>'
            f'</div>')

    # ------------------------------------------------ 프레임 갱신
    def update_frame(self, values: np.ndarray, t: float):
        # RGB 수동 합성 (환형 밖 = 배경색)
        flat = self.W @ values
        norm = np.clip((flat - LEVELS[0]) / (LEVELS[1] - LEVELS[0]), 0.0, 1.0)
        idx8 = (norm * 255).astype(np.uint8)
        rgb = _LUT[idx8]
        rgb[self.mask_out] = _BG_RGB
        self.img.setImage(rgb.reshape(GRID, GRID, 3), autoLevels=False)

        # 왜곡 지수: 60° 섹터 평균 결손 vs 전체 평균
        th = self.profile["theta_deg"]
        mean_all = values.mean()
        worst_mean = mean_all
        worst_a0 = 0.0
        for a0 in range(0, 360, 15):
            dang = (th - (a0 + 30.0) + 180.0) % 360.0 - 180.0
            sel = np.abs(dang) <= 30.0
            if sel.any():
                m = values[sel].mean()
                if m < worst_mean:
                    worst_mean = m
                    worst_a0 = a0
        raw = (mean_all - worst_mean) / abs(mean_all) if mean_all != 0 else 0.0
        idx = float(np.clip(0.04 + raw * 0.28, 0.0, 0.60))  # 표시용 스케일 (목업)
        self._set_center(idx)

        if idx > 0.08:
            self.wedge.setPath(_wedge_path(worst_a0, worst_a0 + 60.0,
                                           R_IN, R_OUT))
            self.wedge.setVisible(True)
        else:
            self.wedge.setVisible(False)

        self._trend.append((t, idx))
        ts = np.array([p[0] for p in self._trend])
        ys = np.array([p[1] for p in self._trend])
        keep = ts >= t - 60.0
        self.trend_curve.setData(ts[keep] - t, ys[keep])

        self.tile_deficit.set_value(f"{values.min():+.0f}")
