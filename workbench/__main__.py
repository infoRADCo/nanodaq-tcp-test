"""진입점: python -m workbench [--ip ADDR [--port N] [--rate HZ]] [--selftest]

--ip       : nanoDAQ 장비 IP. 지정하면 시뮬레이터 대신 실장비 스트림을 표시한다.
--port     : TCP 포트 (기본 101)
--rate     : 장비 데이터 레이트 Hz (기본 50; 1/5/10/20/25/50/100/150/200)
--selftest : 오프스크린 자가 점검 — Setup → Zero All → 3개 Live 모드(각 2 s)
             → Replay → 스냅샷 내보내기 후 종료 코드 0.
"""

import argparse
import sys


def run_selftest(app, win):
    from PySide6.QtCore import QTimer

    seq = [
        (800,  "zero all",        lambda: win.do_zero_all(confirm=False)),
        (300,  "mode downwash",   lambda: win.set_demo_mode("downwash")),
        (200,  "go live",         win.go_live),
        (200,  "drone on",        lambda: win.live_views["downwash"].set_drone(True)),
        (2000, "mode edf",        lambda: win.set_demo_mode("edf")),
        (200,  "shield on",       lambda: win.live_views["edf"].set_shield(True)),
        (2000, "mode aerobench",  lambda: win.set_demo_mode("aerobench")),
        (2000, "replay tab",      win.show_replay),
        (400,  "replay play",     win.replay_play),
        (1200, "event marker",    win.add_marker),
        (300,  "export png",      lambda: print("snapshot:", win.export_png())),
    ]
    it = iter(seq)

    def advance():
        try:
            delay, name, fn = next(it)
        except StopIteration:
            hm = win.live_views["downwash"]
            print(f"[selftest] last heatmap frame: {hm.last_frame_ms:.2f} ms")
            print("SELFTEST OK")
            app.exit(0)
            return

        def run():
            try:
                fn()
            except Exception:
                import traceback
                print(f"[selftest] step FAILED: {name}")
                traceback.print_exc()
                app.exit(1)
                return
            advance()

        QTimer.singleShot(delay, run)

    advance()


def main():
    ap = argparse.ArgumentParser(prog="workbench")
    ap.add_argument("--ip", help="nanoDAQ IP (없으면 시뮬레이터)")
    ap.add_argument("--port", type=int, default=101)
    ap.add_argument("--rate", type=int, default=50, help="장비 데이터 레이트 Hz")
    ap.add_argument("--selftest", action="store_true")
    args, qt_argv = ap.parse_known_args()
    selftest = args.selftest
    argv = [sys.argv[0]] + qt_argv

    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication

    from . import theme

    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True,
                        background=theme.PANEL, foreground=theme.DIM)

    app = QApplication(argv)
    app.setStyleSheet(theme.STYLESHEET)

    from .main_window import MainWindow

    source = None
    if args.ip:
        from .nanodaq_source import NanoDAQSource
        from .ring import RingBuffer
        from .main_window import RING_CAPACITY
        ring = RingBuffer(RING_CAPACITY, 16)
        source = NanoDAQSource(ring, args.ip, args.port, args.rate)

    win = MainWindow(source=source)
    win.show()
    if selftest:
        run_selftest(app, win)
    rc = app.exec()
    win.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
