"""익형 표면압력 뷰 — 코드방향 압력계수 분포.

X = 탭 위치, Y = Cp (공력 관행대로 흡입이 위로 오도록 축을 뒤집는다).
update_frame(values, t) 만 외부 인터페이스.

Cp = p / q. 이 리그에는 전용 피토 채널이 없어서, CH1(앞전 표면탭)을 가상
피토관으로 삼아 q 로 쓴다 — DAQ 기준 포트가 이미 대기압으로 vent 되어 있어
모든 채널이 처음부터 gauge 압력이므로 별도 기준압 차감은 필요 없다.
전시 데모 스코프: 받음각은 고정 장착하거나 손으로만 바꾸고(스윕 자동화 없음),
문헌값과의 정량적 일치를 주장하지 않는다 — 분포가 받음각/풍속에 반응해
움직이는 정성적 거동만 보여준다. CH1 자체가 참 정체점과 정확히 일치한다는
보장은 없다(별도 프로브 대비 최대 40% 편차 관측됨).
"""

from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSlider,
                               QVBoxLayout, QWidget)

from .. import profiles, theme
from ..widgets import StatTile

# 이 아래에서는 q 가 노이즈와 구별되지 않는다. 무풍일 때 0.3 Pa 를 0.4 Pa 로
# 나눠 Cp 를 만들어내는 짓을 막는 바닥값.
Q_FLOOR_PA = 20.0
# CH1(앞전)을 q 로 쓰되 샘플 노이즈를 줄이려고 최근 1초 이동평균을 취한다.
Q_AVG_WINDOW_S = 1.0


class CpView(QWidget):
    def __init__(self, sim, parent=None):
        super().__init__(parent)
        self.sim = sim
        self.profile = profiles.AIRFOIL
        self.taps = self.profile["tap_index"]        # 0-based 채널 번호
        self.tap_labels = self.profile["tap_labels"]
        self.x = np.arange(len(self.taps), dtype=float)

        self._q_hist = deque()   # (t, CH1 Pa) — 최근 Q_AVG_WINDOW_S 초만 유지

        # ---- 좌: Cp 분포
        self.plot = pg.PlotWidget()
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(False, False)
        self.plot.setLabel("bottom", "탭 위치 (앞전 → 뒤)")
        self.plot.setLabel("left", "Cp")
        self.plot.setXRange(-0.4, len(self.taps) - 0.6)
        self.plot.setYRange(-1.6, 1.2)
        self.plot.invertY(True)                       # 공력 관행: 흡입이 위
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.getAxis("bottom").setTicks(
            [[(i, lbl) for i, lbl in enumerate(self.tap_labels)]])

        # Cp = 0 (대기압) 기준선과 Cp = 1 (정체점) 기준선
        for level, style in ((0.0, Qt.SolidLine), (1.0, Qt.DashLine)):
            ln = pg.InfiniteLine(pos=level, angle=0,
                                 pen=pg.mkPen(theme.DIM, width=1, style=style))
            self.plot.addItem(ln)

        self.curve = self.plot.plot(
            pen=pg.mkPen(theme.CYAN, width=2),
            symbol="o", symbolSize=9,
            symbolPen=pg.mkPen("#ffffff", width=1.2), symbolBrush=None)

        legend = QLabel("── 실측   ─ ─ Cp=1 (정체점)   ── Cp=0 (대기압)   "
                        "· 축 반전: 위쪽이 흡입")
        legend.setProperty("role", "dim")
        legend.setAlignment(Qt.AlignCenter)

        left = QVBoxLayout()
        left.addWidget(self.plot, 1)
        left.addWidget(legend)

        # ---- 우: 받음각(시뮬레이터 전용) + q 기준 + 타일
        self.alpha_title = QLabel("받음각 °")
        self.alpha_title.setProperty("role", "dim")
        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setRange(-4, 16)
        self.alpha_slider.setValue(int(getattr(sim, "get_alpha", lambda: 4)()))
        self.alpha_slider.valueChanged.connect(self._on_alpha)
        if not hasattr(sim, "set_alpha") or not callable(getattr(sim, "set_alpha")):
            self.alpha_slider.setEnabled(False)
        # 실장비에서는 받음각을 손으로 바꾼다 — 슬라이더는 시뮬레이터 전용.
        if getattr(sim, "__class__", None).__name__ != "SimSource":
            self.alpha_slider.setEnabled(False)
            self.alpha_slider.setToolTip("실장비에서는 모형을 직접 돌립니다 — 시뮬레이터 전용")

        self.tile_q = StatTile("q 기준 Pa (CH1 1s평균)", "—", accent=theme.AMBER)
        self.tile_stag = StatTile("정체점 (최대 Cp)", "—", accent=theme.CYAN)
        self.tile_suc = StatTile("흡입 피크 Cp", "—", accent=theme.CYAN)
        self.tile_speed = StatTile("자유류 km/h", "—", accent=theme.DIM)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.alpha_title)
        right.addWidget(self.alpha_slider)
        right.addSpacing(6)
        right.addWidget(self.tile_q)
        right.addWidget(self.tile_stag)
        right.addWidget(self.tile_suc)
        right.addWidget(self.tile_speed)
        right.addStretch(1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(left, 1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(230)
        lay.addWidget(rw)

    # ------------------------------------------------ 컨트롤
    def _on_alpha(self, deg):
        if hasattr(self.sim, "set_alpha"):
            self.sim.set_alpha(int(deg))

    # ------------------------------------------------ 프레임 갱신
    def update_frame(self, values: np.ndarray, t: float):
        p = np.asarray(values, dtype=float)[self.taps]

        # q: CH1(앞전, taps[0])을 가상 피토관으로 삼아 최근 1초 이동평균.
        self._q_hist.append((t, float(p[0])))
        while self._q_hist and self._q_hist[0][0] < t - Q_AVG_WINDOW_S:
            self._q_hist.popleft()
        q = max(sum(v for _, v in self._q_hist) / len(self._q_hist), 0.0)

        if q < Q_FLOOR_PA:
            # 무풍 — Cp 는 정의되지 않는다. 0 으로 눕히고 타일을 비운다.
            self.curve.setData(self.x, np.zeros_like(self.x))
            self.tile_q.set_value("— (무풍)")
            self.tile_stag.set_value("—")
            self.tile_suc.set_value("—")
            self.tile_speed.set_value("—")
            return

        cp = p / q
        self.curve.setData(self.x, cp)

        self.tile_q.set_value(f"{q:.0f}")
        i_max = int(np.argmax(cp))
        i_min = int(np.argmin(cp))
        self.tile_stag.set_value(f"{self.tap_labels[i_max]} · {cp[i_max]:+.2f}")
        self.tile_suc.set_value(f"{self.tap_labels[i_min]} · {cp[i_min]:+.2f}")
        # q 는 동압이므로 자유류 속도가 그대로 따라 나온다.
        self.tile_speed.set_value(f"{3.6 * np.sqrt(2.0 * q / profiles.RHO):.0f}")
