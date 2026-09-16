"""메인 셸 — 헤더 · 3탭(Setup/Live/Replay) · 푸터.

단일 QTimer(80 ms)가 링 버퍼에서 데이터를 끌어와 현재 보이는 뷰에만 공급한다.
시뮬레이터 스레드에서 UI로 직접 시그널을 보내지 않는다.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog,
                               QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QMainWindow, QMessageBox,
                               QPushButton, QSlider, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from . import profiles, theme
from .logger import SessionLogger
from .ring import RingBuffer
from .sim import SimSource
from .views.heatmap_view import HeatmapView
from .views.polar_view import PolarView
from .views.wake_view import WakeView
from .widgets import Pill

ROOT = Path(__file__).resolve().parent.parent
RING_CAPACITY = 60000  # 100 Hz(실장비 기본) × 10분. 60000×17×8 B ≈ 8 MB
RATE_HZ_SIM = 50.0
DRIFT_LIMIT_PA = 5.0


def _panel(layout=None) -> QFrame:
    f = QFrame()
    f.setProperty("panel", "true")
    if layout is not None:
        f.setLayout(layout)
    return f


class MainWindow(QMainWindow):
    def __init__(self, source=None, log: bool = False):
        """source: 링 버퍼에 프레임을 쓰는 스레드 (SimSource 또는 NanoDAQSource).
        None 이면 시뮬레이터. 두 소스는 같은 인터페이스를 노출한다.
        log: True 면 세션의 모든 샘플·이벤트를 logs/ 에 CSV 로 남긴다 (SessionLogger)."""
        super().__init__()
        self.setWindowTitle("전시 계측 워크벤치 PoC")
        self.resize(1280, 800)

        if source is None:
            self.ring = RingBuffer(RING_CAPACITY, 16)
            self.sim = SimSource(self.ring)
        else:
            self.ring = source.ring
            self.sim = source
        self.is_hw = not isinstance(self.sim, SimSource)
        self.rate_hz = float(getattr(self.sim, "rate_hz", RATE_HZ_SIM))

        self.mode = "downwash"
        self.zero_time = None
        self._zero_seen_t = 0.0  # 마지막으로 반영한 실장비 Zero 결과 시각
        self._log_error_shown = False
        self.markers = []            # 이벤트 마커 (epoch 초)
        self.session_start = time.time()
        self.session_name = f"{'nanodaq' if self.is_hw else 'sim'}_{datetime.now():%Y%m%d_%H%M%S}"

        # 로거는 소스 start() 전에 훅을 걸어야 첫 패킷부터 남는다.
        self.logger = None
        self._log_start_error = ""
        if log:
            meta = {"mode": "nanodaq" if self.is_hw else "sim", "rate_hz": f"{self.rate_hz:g}"}
            if self.is_hw:
                meta["device"] = f"{self.sim.ip}:{self.sim.port}"
            try:
                self.logger = SessionLogger(ROOT / "logs", self.session_name, meta)
            except OSError as exc:
                # UI(토스트)가 아직 없다. 콘솔에 남기고 첫 틱에서 토스트로 알린 뒤 로그 없이 계속한다.
                self._log_start_error = f"로그 시작 실패, 로그 없이 진행: {exc}"
                print(f"[log] {self._log_start_error}", file=sys.stderr)
            else:
                self.session_name = self.logger.session  # 이름이 겹쳐 접미사가 붙었으면 푸터도 그 이름
                self.sim.on_packet = self.logger.on_packet
                if hasattr(self.sim, "on_event"):
                    self.sim.on_event = lambda state, msg: self.logger.event(f"source_{state}", msg)
        self.sim.start()

        self._replay_t = np.empty(0)
        self._replay_v = np.empty((0, 16))
        self._replay_cursor = 0

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        root.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_setup_tab(), "1 · Setup")
        self.tabs.addTab(self._build_live_tab(), "2 · Live")
        self.tabs.addTab(self._build_replay_tab(), "3 · Replay")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        root.addWidget(self._build_footer())
        self.setCentralWidget(central)

        # 스페이스바 = 이벤트 마커
        sc = QShortcut(QKeySequence(Qt.Key_Space), self)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(self.add_marker)

        self._tick_n = 0
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(80)
        self.play_timer.timeout.connect(self._replay_tick)

    # ================================================= 헤더
    def _build_header(self):
        logo = QLabel("EXPO WORKBENCH")
        logo.setProperty("role", "logo")
        self.ident = QLabel("nanoDAQ-LTS-16 · SIMULATOR · 16ch ±2.5 kPa")
        self.ident.setProperty("role", "ident")
        ident = self.ident

        self.pill_sim = Pill("● SIMULATOR", "amber")
        if self.is_hw:
            self.ident.setText(f"nanoDAQ {self.sim.ip}:{self.sim.port} · 접속 중")
            self.pill_sim.setText("● 접속 중")
        self.pill_zero = Pill("ZERO 필요", "amber")

        btn_report = QPushButton("\U0001F4C4 성적서")
        btn_report.clicked.connect(self._open_export_dialog)

        lay = QHBoxLayout()
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(logo)
        lay.addSpacing(14)
        lay.addWidget(ident)
        lay.addStretch(1)
        lay.addWidget(self.pill_sim)
        lay.addWidget(self.pill_zero)
        lay.addSpacing(10)
        lay.addWidget(btn_report)
        return _panel(lay)

    # ================================================= 푸터
    def _build_footer(self):
        self.pill_rec = Pill("● REC 00:00", "red")
        btn_snap = QPushButton("\U0001F4CD 순간 저장")
        btn_snap.clicked.connect(self.add_marker)
        hint = QLabel("스페이스바 = 이벤트 마커")
        hint.setProperty("role", "dim")
        self.toast = QLabel("")
        self.toast.setStyleSheet(f"color: {theme.AMBER};")
        sess = QLabel(f"{self.session_name} · {self.rate_hz:.0f} Hz" + (" · LOG" if self.logger else ""))
        sess.setProperty("role", "ident")

        lay = QHBoxLayout()
        lay.setContentsMargins(12, 6, 12, 6)
        lay.addWidget(self.pill_rec)
        lay.addSpacing(10)
        lay.addWidget(btn_snap)
        lay.addSpacing(10)
        lay.addWidget(hint)
        lay.addSpacing(16)
        lay.addWidget(self.toast, 1)
        lay.addWidget(sess)
        return _panel(lay)

    # ================================================= 탭 1 — Setup
    def _build_setup_tab(self):
        # ---- 좌: 데모 모드 + 데이터 소스
        mode_title = QLabel("데모 모드")
        mode_title.setProperty("role", "dim")
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        mode_lay = QVBoxLayout()
        mode_lay.setContentsMargins(10, 10, 10, 10)
        mode_lay.addWidget(mode_title)
        for key in profiles.MODE_ORDER:
            p = profiles.PROFILES[key]
            b = QPushButton(f"{p['icon']} {p['title']}")
            b.setCheckable(True)
            b.setProperty("mode", "true")
            b.setToolTip(p["subtitle"])
            b.clicked.connect(lambda _=False, k=key: self.set_demo_mode(k))
            self.mode_group.addButton(b)
            self.mode_buttons[key] = b
            mode_lay.addWidget(b)
        self.mode_buttons[self.mode].setChecked(True)

        src_title = QLabel("데이터 소스")
        src_title.setProperty("role", "dim")
        src_lay = QVBoxLayout()
        src_lay.setContentsMargins(10, 10, 10, 10)
        src_lay.addWidget(src_title)
        for name, active in (("nanoDAQ (TCP)", False),
                             ("Simulator", True),
                             ("파일 재생", False)):
            b = QPushButton(("● " if active else "○ ") + name)
            b.setCheckable(True)
            b.setChecked(active)
            b.setEnabled(active)
            if not active:
                b.setToolTip("컨셉 표시용 — PoC에서는 비활성")
            src_lay.addWidget(b)
        src_lay.addStretch(1)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_panel(mode_lay))
        left.addWidget(_panel(src_lay), 1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(240)

        # ---- 중앙: 채널 테이블
        headers = ["색", "CH", "이름", "위치", "현재값 Pa", "영점잔류",
                   "노이즈 RMS", "상태"]
        self.table = QTableWidget(16, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.ch_colors = [QColor.fromHsv(int(360 * i / 16), 150, 225)
                          for i in range(16)]
        for i in range(16):
            sw = QTableWidgetItem()
            sw.setBackground(self.ch_colors[i])
            self.table.setItem(i, 0, sw)
            for c in range(1, len(headers)):
                it = QTableWidgetItem("—")
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setFont(pg.QtGui.QFont(theme.MONO, 9))
                self.table.setItem(i, c, it)
        self._refresh_table_static()

        # ---- 우: Quality Gate
        gate_title = QLabel("Quality Gate")
        gate_title.setProperty("role", "dim")
        self.btn_zero = QPushButton("⊘ Zero All (16ch)")
        self.btn_zero.clicked.connect(lambda: self.do_zero_all(confirm=True))
        self.gate_stat = QLabel("0 /16 OK")
        self.gate_stat.setAlignment(Qt.AlignCenter)
        self.gate_stat.setStyleSheet(
            f"font-family: {theme.MONO}; font-size: 30px; font-weight: 700; "
            f"color: {theme.AMBER}; padding: 8px;")
        self.gate_problems = QLabel("")
        self.gate_problems.setWordWrap(True)
        self.gate_problems.setStyleSheet(f"color: {theme.AMBER};")
        self.btn_start = QPushButton("▶ 시연 시작")
        self.btn_start.setProperty("primary", "true")
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self.go_live)

        gate_lay = QVBoxLayout()
        gate_lay.setContentsMargins(12, 12, 12, 12)
        gate_lay.setSpacing(10)
        gate_lay.addWidget(gate_title)
        gate_lay.addWidget(self.btn_zero)
        gate_lay.addWidget(self.gate_stat)
        gate_lay.addWidget(self.gate_problems)
        gate_lay.addStretch(1)
        gate_lay.addWidget(self.btn_start)
        gw = _panel(gate_lay)
        gw.setFixedWidth(230)

        lay = QHBoxLayout()
        lay.setSpacing(8)
        lay.addWidget(lw)
        lay.addWidget(self.table, 1)
        lay.addWidget(gw)
        w = QWidget()
        w.setLayout(lay)
        return w

    # ================================================= 탭 2 — Live
    def _build_live_tab(self):
        self.live_views = {
            "downwash": HeatmapView(self.sim),
            "edf": PolarView(self.sim),
            "aerobench": WakeView(self.sim),
        }
        self.live_stack = QStackedWidget()
        for key in profiles.MODE_ORDER:
            self.live_stack.addWidget(self.live_views[key])

        # 하단 레코더 스트립: 최근 60 s 평균 압력 + 이벤트 마커
        self.recorder = pg.PlotWidget()
        self.recorder.setFixedHeight(92)
        self.recorder.setMenuEnabled(False)
        self.recorder.setMouseEnabled(False, False)
        self.recorder.setLabel("left", "평균 Pa")
        self.recorder.setXRange(-60.0, 0.0)
        self.rec_curve = self.recorder.plot(
            pen=pg.mkPen(theme.CYAN, width=1.2))
        self._marker_lines = []

        lay = QVBoxLayout()
        lay.setSpacing(8)
        lay.addWidget(self.live_stack, 1)
        lay.addWidget(self.recorder)
        w = QWidget()
        w.setLayout(lay)
        return w

    # ================================================= 탭 3 — Replay
    def _build_replay_tab(self):
        self.session_list = QListWidget()
        self.session_list.addItem("현재 세션 (라이브 버퍼)")
        self.session_list.setCurrentRow(0)
        self.session_list.setFixedWidth(220)

        self.replay_views = {
            "downwash": HeatmapView(self.sim),
            "edf": PolarView(self.sim),
            "aerobench": WakeView(self.sim),
        }
        self.replay_stack = QStackedWidget()
        for key in profiles.MODE_ORDER:
            self.replay_stack.addWidget(self.replay_views[key])

        self.scrubber = QSlider(Qt.Horizontal)
        self.scrubber.setRange(0, 0)
        self.scrubber.valueChanged.connect(self._on_scrub)
        self.btn_play = QPushButton("▶ 재생")
        self.btn_play.clicked.connect(self.replay_play)
        self.btn_pause = QPushButton("⏸")
        self.btn_pause.clicked.connect(self.replay_pause)
        self.btn_loop = QPushButton("Loop")
        self.btn_loop.setCheckable(True)
        loop_hint = QLabel("Loop = 무인 어트랙트 모드")
        loop_hint.setProperty("role", "dim")
        self.replay_time = QLabel("00:00 / 00:00")
        self.replay_time.setProperty("role", "ident")

        tl = QHBoxLayout()
        tl.setContentsMargins(10, 6, 10, 6)
        tl.addWidget(self.btn_play)
        tl.addWidget(self.btn_pause)
        tl.addWidget(self.btn_loop)
        tl.addSpacing(8)
        tl.addWidget(loop_hint)
        tl.addWidget(self.scrubber, 1)
        tl.addWidget(self.replay_time)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.replay_stack, 1)
        right.addWidget(_panel(tl))

        lay = QHBoxLayout()
        lay.setSpacing(8)
        lay.addWidget(self.session_list)
        rl = QWidget()
        rl.setLayout(right)
        lay.addWidget(rl, 1)
        w = QWidget()
        w.setLayout(lay)
        return w

    # ================================================= 공개 동작
    def set_demo_mode(self, key: str):
        self.mode = key
        self.sim.set_mode(key)
        self.mode_buttons[key].setChecked(True)
        idx = profiles.MODE_ORDER.index(key)
        self.live_stack.setCurrentIndex(idx)
        self.replay_stack.setCurrentIndex(idx)
        self._refresh_table_static()

    def go_live(self):
        self.tabs.setCurrentIndex(1)

    def show_replay(self):
        self.tabs.setCurrentIndex(2)

    def do_zero_all(self, confirm: bool = True):
        if confirm:
            ans = QMessageBox.question(
                self, "Zero All", "무풍·무압 상태입니까?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ans != QMessageBox.Yes:
                return
        self.sim.zero_all()
        self._log_event("zero_request", "hw" if self.is_hw else "sim")
        if self.is_hw:
            # 실장비는 비동기 — 결과(done/failed)는 _update_zero_pill 이
            # NanoDAQSource.status()["zero"] 를 보고 반영한다. 여기서 초록으로
            # 바꾸면 장비가 거부했거나 끊겼을 때도 '영점 완료' 로 남는다.
            self.pill_zero.setText("ZERO 요청 중…")
            self.pill_zero.set_kind("amber")
            self._show_toast("장비 Rezero 요청 (16ch)")
            self._update_zero_pill(self.sim.status())
            return
        self.zero_time = datetime.now()
        self.pill_zero.setText(f"ZERO {self.zero_time:%H:%M}")
        self.pill_zero.set_kind("green")
        self._show_toast("영점 조정 완료 (16ch)")
        self._log_event("zero_done", "sim offsets cleared")

    def add_marker(self):
        t = time.time()
        self.markers.append(t)
        line = pg.InfiniteLine(pos=0.0, angle=90,
                               pen=pg.mkPen(theme.AMBER, width=1.5))
        self.recorder.addItem(line)
        self._marker_lines.append((t, line))
        self._log_event("marker", f"#{len(self.markers)}", t=t)
        self._show_toast(f"\U0001F4CD 이벤트 마커 #{len(self.markers)} 저장")

    def replay_play(self):
        if self._replay_t.size == 0:
            self._load_replay_snapshot()
        self.play_timer.start()

    def replay_pause(self):
        self.play_timer.stop()

    def export_png(self) -> str:
        out = ROOT / "reports"
        out.mkdir(exist_ok=True)
        path = out / f"snapshot_{datetime.now():%Y%m%d_%H%M%S}.png"
        if not self.grab().save(str(path)):
            # 저장 실패를 성공으로 기록하면 증거 CSV 가 없는 PNG 를 가리킨다.
            self._log_event("snapshot_failed", path.name)
            self._show_toast(f"스냅샷 저장 실패: {path.name}")
            return ""
        self._log_event("snapshot", path.name)
        self._show_toast(f"스냅샷 저장: {path.name}")
        return str(path)

    def shutdown(self):
        self.timer.stop()
        self.play_timer.stop()
        self.sim.stop()
        self.sim.join(timeout=1.0)
        if self.logger is not None:
            summary = self.logger.close()
            if summary:
                print(f"[log] {summary}")
            if self.logger.error:
                print(f"[log] {self.logger.error}")

    def _log_event(self, kind: str, note: str = "", t: float | None = None):
        if self.logger is not None:
            self.logger.event(kind, note, t)

    def closeEvent(self, ev):
        self.shutdown()
        super().closeEvent(ev)

    # ================================================= 내부
    def _show_toast(self, msg: str):
        self.toast.setText(msg)
        QTimer.singleShot(2500, lambda: self.toast.setText("")
                          if self.toast.text() == msg else None)

    def _on_tab_changed(self, idx):
        if idx == 2:
            self._load_replay_snapshot()
        else:
            self.play_timer.stop()

    # ---------------- Setup: 테이블 + 게이트
    def _refresh_table_static(self):
        chans = profiles.PROFILES[self.mode]["channels"]
        for i, ch in enumerate(chans):
            self.table.item(i, 1).setText(f"CH{i + 1:02d}")
            self.table.item(i, 2).setText(ch["name"])
            self.table.item(i, 3).setText(ch["pos"])

    def _update_setup(self):
        chans = profiles.PROFILES[self.mode]["channels"]
        offs = self.sim.get_offsets()
        ts, vs = self.ring.latest(100)
        cur = vs[-1] if len(vs) else np.zeros(16)
        rms = vs.std(axis=0) if len(vs) > 3 else np.zeros(16)
        n_ok = 0
        problems = []
        for i, ch in enumerate(chans):
            spare = ch["spare"]
            drift = abs(offs[i]) > DRIFT_LIMIT_PA
            self.table.item(i, 4).setText("—" if spare else f"{cur[i]:+8.1f}")
            self.table.item(i, 5).setText("—" if spare else f"{offs[i]:+6.1f}")
            self.table.item(i, 6).setText("—" if spare else f"{rms[i]:6.2f}")
            st = self.table.item(i, 7)
            if spare:
                st.setText("사용 안 함")
                st.setForeground(QColor(theme.DIM))
                n_ok += 1
            elif drift:
                st.setText("● 드리프트")
                st.setForeground(QColor(theme.AMBER))
                problems.append(f"CH{i + 1:02d} 영점잔류 {offs[i]:+.1f} Pa")
            else:
                st.setText("● OK")
                st.setForeground(QColor(theme.OK))
                n_ok += 1
        self.gate_stat.setText(f"{n_ok} /16 OK")
        self.gate_stat.setStyleSheet(
            f"font-family: {theme.MONO}; font-size: 30px; font-weight: 700; "
            f"color: {theme.OK if n_ok == 16 else theme.AMBER}; padding: 8px;")
        self.gate_problems.setText(
            "\n".join(problems) if problems
            else ("게이트 통과 — 시연 시작 가능" if n_ok == 16 else ""))
        self.btn_start.setEnabled(n_ok == 16)

    # ---------------- Live 갱신
    def _update_live(self):
        ts, vs = self.ring.latest(1)
        if len(ts):
            view = self.live_views[self.mode]
            view.update_frame(vs[0], ts[0])

    def _update_recorder(self):
        n = int(60 * self.rate_hz)
        ts, vs = self.ring.latest(n)
        if len(ts) < 5:
            return
        now = ts[-1]
        spare = np.array([c["spare"] for c
                          in profiles.PROFILES[self.mode]["channels"]])
        mean = vs[:, ~spare].mean(axis=1)
        step = max(1, len(ts) // 600)  # 다운샘플
        self.rec_curve.setData(ts[::step] - now, mean[::step])
        for t, line in self._marker_lines:
            line.setPos(t - now)

    # ---------------- Replay
    def _load_replay_snapshot(self):
        self._replay_t, self._replay_v = self.ring.snapshot()
        n = len(self._replay_t)
        self.scrubber.blockSignals(True)
        self.scrubber.setRange(0, max(0, n - 1))
        self.scrubber.blockSignals(False)
        self._replay_cursor = 0
        self._feed_replay_frame()

    def _on_scrub(self, val):
        self._replay_cursor = int(val)
        self._feed_replay_frame()

    def _feed_replay_frame(self):
        n = len(self._replay_t)
        if n == 0:
            return
        i = min(self._replay_cursor, n - 1)
        view = self.replay_views[self.mode]
        view.update_frame(self._replay_v[i], self._replay_t[i])
        cur = (self._replay_t[i] - self._replay_t[0])
        tot = (self._replay_t[-1] - self._replay_t[0])
        self.replay_time.setText(
            f"{int(cur) // 60:02d}:{int(cur) % 60:02d} / "
            f"{int(tot) // 60:02d}:{int(tot) % 60:02d}")

    def _replay_tick(self):
        n = len(self._replay_t)
        if n == 0:
            self.play_timer.stop()
            return
        self._replay_cursor += max(1, round(0.08 * self.rate_hz))  # 80 ms 분량 → 1× 재생
        if self._replay_cursor >= n:
            if self.btn_loop.isChecked():
                self._replay_cursor = 0
            else:
                self._replay_cursor = n - 1
                self.play_timer.stop()
        self.scrubber.blockSignals(True)
        self.scrubber.setValue(self._replay_cursor)
        self.scrubber.blockSignals(False)
        self._feed_replay_frame()

    # ---------------- 메인 타이머
    def _tick(self):
        self._tick_n += 1
        if self._tick_n == 1:
            if self.logger is not None:
                self._show_toast(f"로그 기록 중: logs/{self.logger.path.name}")
            elif self._log_start_error:
                self._show_toast(self._log_start_error)
        if self._tick_n % 12 == 0:  # ~1 s
            if self.logger is not None and self.logger.error and not self._log_error_shown:
                self._log_error_shown = True
                self._show_toast(self.logger.error)
            el = int(time.time() - self.session_start)
            self.pill_rec.setText(f"● REC {el // 60:02d}:{el % 60:02d}")
            if self.is_hw:
                self._update_device_pill()
        idx = self.tabs.currentIndex()
        if idx == 0:
            if self._tick_n % 3 == 0:  # ~5 Hz
                self._update_setup()
        elif idx == 1:
            self._update_live()
            if self._tick_n % 2 == 0:
                self._update_recorder()

    # ---------------- 장비 상태
    def _update_zero_pill(self, st: dict):
        """실장비 Zero All 결과를 알약에 반영한다. 같은 결과는 한 번만 처리."""
        zero = st.get("zero")
        if zero in (None, "pending") or st.get("zero_t", 0.0) == self._zero_seen_t:
            return
        self._zero_seen_t = st["zero_t"]
        self._log_event(f"zero_{zero}", st.get("zero_msg", ""), t=st["zero_t"])
        if zero == "done":
            self.zero_time = datetime.fromtimestamp(st["zero_t"])
            self.pill_zero.setText(f"ZERO {self.zero_time:%H:%M}")
            self.pill_zero.set_kind("green")
        else:
            self.pill_zero.setText("ZERO 실패")
            self.pill_zero.set_kind("red")
        self._show_toast(st.get("zero_msg") or ("장비 Rezero 완료" if zero == "done" else "장비 Rezero 실패"))

    def _update_device_pill(self):
        st = self.sim.status()
        self._update_zero_pill(st)
        state = st["state"]
        if state == "streaming":
            self.pill_sim.setText(f"● nanoDAQ {st['packets']} pkt")
            self.pill_sim.set_kind("green")
            self.ident.setText(st["message"])
        elif state == "stale":
            self.pill_sim.setText("● 데이터 없음")
            self.pill_sim.set_kind("amber")
        elif state == "connecting":
            self.pill_sim.setText("● 접속 중")
            self.pill_sim.set_kind("amber")
        else:
            self.pill_sim.setText("● 연결 오류")
            self.pill_sim.set_kind("red")
            self.ident.setText(st["message"])

    # ---------------- 성적서
    def _open_export_dialog(self):
        dlg = ExportDialog(self)
        dlg.exec()


class ExportDialog(QDialog):
    def __init__(self, win: MainWindow):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("성적서 (PoC)")
        self.setMinimumWidth(420)

        p = profiles.PROFILES[win.mode]
        ts, vs = win.ring.latest(int(2 * win.rate_hz))
        spare = np.array([c["spare"] for c in p["channels"]])
        if len(vs):
            act = vs[:, ~spare]
            peak, mean = act.max(), act.mean()
        else:
            peak = mean = 0.0
        offs = win.sim.get_offsets()
        n_ok = sum(1 for i, c in enumerate(p["channels"])
                   if c["spare"] or abs(offs[i]) <= DRIFT_LIMIT_PA)
        zero_txt = (f"{win.zero_time:%H:%M:%S}" if win.zero_time else "미실시")

        summary = QLabel(
            f"데모      : {p['icon']} {p['title']}\n"
            f"시각      : {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"피크      : {peak:+.1f} Pa (최근 2 s)\n"
            f"평균      : {mean:+.1f} Pa (최근 2 s)\n"
            f"Zero 시각 : {zero_txt}\n"
            f"게이트    : {n_ok} /16 OK\n"
            f"이벤트    : 마커 {len(win.markers)}개")
        summary.setStyleSheet(
            f"font-family: {theme.MONO}; font-size: 12px; "
            f"background: {theme.PANEL}; border: 1px solid {theme.LINE}; "
            f"border-radius: 6px; padding: 12px;")

        btn_png = QPushButton("PNG 스냅샷 저장")
        btn_png.clicked.connect(self._save_png)
        btn_pdf = QPushButton("PDF 성적서")
        btn_pdf.setEnabled(False)
        btn_pdf.setToolTip("P1 — reportlab 예정")
        self.path_label = QLabel("")
        self.path_label.setProperty("role", "dim")
        self.path_label.setWordWrap(True)

        btns = QHBoxLayout()
        btns.addWidget(btn_png)
        btns.addWidget(btn_pdf)
        btns.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(summary)
        lay.addLayout(btns)
        lay.addWidget(self.path_label)

    def _save_png(self):
        path = self.win.export_png()
        self.path_label.setText(f"저장됨: {path}" if path else "저장 실패 — reports/ 폴더 쓰기 권한을 확인")
