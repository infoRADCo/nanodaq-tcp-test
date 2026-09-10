"""익형 표면압력 뷰 — 코드방향 압력계수 분포.

X = 탭 위치, Y = Cp (공력 관행대로 흡입이 위로 오도록 축을 뒤집는다).
update_frame(values, t) 만 외부 인터페이스.

Cp = p / q 인데 이 리그에는 자유류 동압을 재는 피토 채널이 아직 없다. 그래서
q 는 '탭들이 본 최대 양압' 으로 잡는다 — 어느 탭이 정체점에 있을 때 그 탭이
읽는 값이 곧 q 이기 때문이다. 다만 받음각을 바꾸면 정체점이 탭 사이로 비껴가
순간최대값이 실제 q 보다 작아지고, 그러면 |Cp| 가 1 을 넘는 허상이 생긴다.
송풍기 속도가 일정한 동안은 q 도 상수이므로, `q 고정` 으로 스윕 중 관측된
최대값을 붙잡아 두고 쓰는 쪽이 맞다.
"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from .. import profiles, theme
from ..widgets import StatTile

# 이 아래에서는 q 가 노이즈와 구별되지 않는다. 무풍일 때 0.3 Pa 를 0.4 Pa 로
# 나눠 Cp 를 만들어내는 짓을 막는 바닥값.
Q_FLOOR_PA = 20.0
# q 자동 추정은 '순간 최댓값' 이 아니라 '관측된 최댓값' 을 붙잡는다. 순간값을
# 쓰면 정체점이 탭 사이로 비껴간 자세에서 q 가 과소평가되어 |Cp| 가 1 을 훌쩍
# 넘는 허상이 나온다. 송풍기 속도가 일정한 동안 q 는 상수이므로 최댓값을 들고
# 있는 편이 맞고, 바람이 멎으면 이 감쇠로 서서히 잊는다 (12.5 Hz 갱신 기준
# 반감기 약 55 s).
Q_DECAY = 0.999


class CpView(QWidget):
    def __init__(self, sim, parent=None):
        super().__init__(parent)
        self.sim = sim
        self.profile = profiles.AIRFOIL
        self.taps = self.profile["tap_index"]        # 0-based 채널 번호
        self.tap_labels = self.profile["tap_labels"]
        self.x = np.arange(len(self.taps), dtype=float)

        self._q_auto = 0.0
        self._q_latched = None

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

        self.btn_qlatch = QPushButton("q 고정")
        self.btn_qlatch.setCheckable(True)
        self.btn_qlatch.setToolTip(
            "스윕 중 관측된 최대 정체압을 q 로 붙잡아 둡니다.\n"
            "송풍기 속도가 일정한 동안 q 는 상수이므로,\n"
            "정체점이 탭 사이로 비껴가도 Cp 가 왜곡되지 않습니다.")
        self.btn_qlatch.clicked.connect(self._on_qlatch)

        self.tile_q = StatTile("q 기준 Pa", "—", accent=theme.AMBER)
        self.tile_stag = StatTile("정체점 (최대 Cp)", "—", accent=theme.CYAN)
        self.tile_suc = StatTile("흡입 피크 Cp", "—", accent=theme.CYAN)
        self.tile_speed = StatTile("자유류 km/h", "—", accent=theme.DIM)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.alpha_title)
        right.addWidget(self.alpha_slider)
        right.addSpacing(6)
        right.addWidget(self.btn_qlatch)
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

    def _on_qlatch(self, checked):
        if checked:
            self._q_latched = max(self._q_auto, Q_FLOOR_PA)
            self.btn_qlatch.setText(f"q 고정됨 {self._q_latched:.0f} Pa")
        else:
            self._q_latched = None
            self.btn_qlatch.setText("q 고정")

    # ------------------------------------------------ 프레임 갱신
    def update_frame(self, values: np.ndarray, t: float):
        p = np.asarray(values, dtype=float)[self.taps]

        # q 자동 추정: 탭들이 본 최대 양압을 붙잡고 천천히 잊는다.
        peak = float(max(p.max(), 0.0))
        self._q_auto = max(peak, self._q_auto * Q_DECAY)
        # 고정 중이면 그 값을, 아니면 자동값을 쓴다.
        q = self._q_latched if self._q_latched is not None else self._q_auto

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

        if self._q_latched is None:
            self.tile_q.set_value(f"{q:.0f} (관측최대)")
        else:
            self.tile_q.set_value(f"{q:.0f} (고정)")
        i_max = int(np.argmax(cp))
        i_min = int(np.argmin(cp))
        self.tile_stag.set_value(f"{self.tap_labels[i_max]} · {cp[i_max]:+.2f}")
        self.tile_suc.set_value(f"{self.tap_labels[i_min]} · {cp[i_min]:+.2f}")
        # q 는 동압이므로 자유류 속도가 그대로 따라 나온다.
        self.tile_speed.set_value(f"{3.6 * np.sqrt(2.0 * q / profiles.RHO):.0f}")
