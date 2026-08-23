"""Downwash 히트맵 뷰 — 16점 → 200×200 RBF 보간 (성능 스파이크 겸용).

update_frame(values, t)만 외부 인터페이스. 라이브/리플레이 공용.
"""

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)
from scipy.interpolate import RBFInterpolator

from .. import profiles, theme
from ..widgets import StatTile

GRID = 200
EXTENT = 300.0  # mm
LEVELS = (-40.0, 120.0)  # 고정 스케일

# 발산형 컬러맵: 파랑 → 어두움(0 부근) → 노랑 → 빨강
_CMAP = pg.ColorMap(
    pos=[0.0, 0.25, 0.62, 1.0],
    color=[(79, 127, 224), (16, 22, 30), (232, 176, 75), (226, 92, 74)],
)


class HeatmapView(QWidget):
    def __init__(self, sim, parent=None):
        super().__init__(parent)
        self.sim = sim
        self.profile = profiles.DOWNWASH
        self._baseline = None
        self._delta_on = False
        self._ms_acc = []
        self._last_log = time.perf_counter()
        self.last_frame_ms = 0.0

        pts = self.profile["positions"]
        gx = np.linspace(-EXTENT, EXTENT, GRID)
        X, Y = np.meshgrid(gx, gx)
        grid_pts = np.column_stack([X.ravel(), Y.ravel()])
        # 항등행렬을 데이터로 넣어 평가하면 그대로 가중치 행렬 W (40000×16)
        rbf = RBFInterpolator(pts, np.eye(16), kernel="thin_plate_spline")
        self.W = rbf(grid_pts)

        # ---- 좌: 히트맵
        glw = pg.GraphicsLayoutWidget()
        plot = glw.addPlot()
        plot.setAspectLocked(True)
        plot.setLabel("bottom", "x (mm)")
        plot.setLabel("left", "y (mm)")
        plot.setMenuEnabled(False)
        plot.setMouseEnabled(False, False)
        # 고정 범위 (TextItem으로 인한 오토레인지 폭주 방지)
        plot.setRange(xRange=(-320, 320), yRange=(-320, 320), padding=0)

        self.img = pg.ImageItem()
        # setRect는 이미지 shape이 있어야 올바른 변환을 만든다 → 더미 프레임 선할당
        self.img.setImage(np.zeros((GRID, GRID)), autoLevels=False,
                          levels=LEVELS)
        self.img.setRect(QRectF(-EXTENT, -EXTENT, 2 * EXTENT, 2 * EXTENT))
        plot.addItem(self.img)
        bar = pg.ColorBarItem(values=LEVELS, colorMap=_CMAP,
                              interactive=False, label="Pa", width=18)
        bar.setImageItem(self.img, insert_in=plot)

        self.dots = pg.ScatterPlotItem(
            x=pts[:, 0], y=pts[:, 1], size=11, symbol="o",
            pen=pg.mkPen("#ffffff", width=1.5), brush=None)
        plot.addItem(self.dots)
        self.peak_label = pg.TextItem(color=theme.INK, anchor=(0.5, 1.4))
        self.peak_label.setFont(pg.QtGui.QFont(theme.MONO, 10))
        plot.addItem(self.peak_label, ignoreBounds=True)

        legend = QLabel("○ 실측점 16 · 면=보간 추정")
        legend.setProperty("role", "dim")
        legend.setAlignment(Qt.AlignCenter)

        left = QVBoxLayout()
        left.addWidget(glw, 1)
        left.addWidget(legend)

        # ---- 우: 스탯 타일 + 컨트롤
        self.tile_peak = StatTile("피크 Pa / 채널", accent=theme.CYAN)
        self.tile_mean = StatTile("평균 Pa")
        self.tile_min = StatTile("최소 Pa")

        self.btn_drone = QPushButton("\U0001F681 드론 OFF")
        self.btn_drone.setCheckable(True)
        self.btn_drone.toggled.connect(self._on_drone)

        self.btn_baseline = QPushButton("기준 저장")
        self.btn_baseline.clicked.connect(self._save_baseline)
        self.btn_delta = QPushButton("Δ 표시")
        self.btn_delta.setCheckable(True)
        self.btn_delta.setEnabled(False)
        self.btn_delta.toggled.connect(self._on_delta)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.tile_peak)
        right.addWidget(self.tile_mean)
        right.addWidget(self.tile_min)
        right.addSpacing(10)
        right.addWidget(self.btn_drone)
        right.addWidget(self.btn_baseline)
        right.addWidget(self.btn_delta)
        right.addStretch(1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(left, 1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(190)
        lay.addWidget(rw)

    # ------------------------------------------------ 컨트롤
    def _on_drone(self, on):
        self.sim.set_drone(on)
        self.btn_drone.setText(
            "\U0001F681 드론 ON" if on else "\U0001F681 드론 OFF")

    def set_drone(self, on: bool):
        self.btn_drone.setChecked(on)

    def _save_baseline(self):
        self._baseline = self._last_values.copy() if \
            getattr(self, "_last_values", None) is not None else None
        self.btn_delta.setEnabled(self._baseline is not None)

    def _on_delta(self, on):
        self._delta_on = on

    # ------------------------------------------------ 프레임 갱신
    def update_frame(self, values: np.ndarray, t: float):
        self._last_values = values
        v = values
        if self._delta_on and self._baseline is not None:
            v = values - self._baseline

        t0 = time.perf_counter()
        img = (self.W @ v).reshape(GRID, GRID)
        ms = (time.perf_counter() - t0) * 1e3
        self.last_frame_ms = ms
        self._ms_acc.append(ms)
        now = time.perf_counter()
        if now - self._last_log >= 5.0 and self._ms_acc:
            avg = sum(self._ms_acc) / len(self._ms_acc)
            print(f"[heatmap] frame compute avg {avg:.2f} ms "
                  f"(max {max(self._ms_acc):.2f} ms, n={len(self._ms_acc)})")
            self._ms_acc.clear()
            self._last_log = now

        self.img.setImage(img, autoLevels=False, levels=LEVELS)

        i = int(np.argmax(v))
        pos = self.profile["positions"][i]
        self.peak_label.setPos(pos[0], pos[1])
        self.peak_label.setText(f"{v[i]:+.0f} Pa")
        self.tile_peak.set_value(f"{v[i]:+.0f} / CH{i + 1:02d}")
        self.tile_mean.set_value(f"{v.mean():+.1f}")
        self.tile_min.set_value(f"{v.min():+.1f}")
