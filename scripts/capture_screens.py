"""오프스크린 화면 캡처 — docs/poc_screens/*.png 5장 생성.

사용: .venv/Scripts/python.exe scripts/capture_screens.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # offscreen 플랫폼은 폰트 디렉터리를 직접 알려줘야 함
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "poc_screens"


def main():
    import pyqtgraph as pg
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from workbench import theme
    from workbench.main_window import MainWindow

    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True,
                        background=theme.PANEL, foreground=theme.DIM)
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLESHEET)
    win = MainWindow()
    win.show()

    OUT.mkdir(parents=True, exist_ok=True)

    def grab(name):
        path = OUT / f"{name}.png"
        ok = win.grab().save(str(path))
        print(f"[capture] {name}.png -> {'OK' if ok else 'FAIL'}")
        if not ok:
            app.exit(1)

    seq = [
        # Setup: 영점 조정 전 (드리프트 칩이 보이는 상태)
        (1500, lambda: grab("setup")),
        (100,  lambda: win.do_zero_all(confirm=False)),
        (200,  lambda: win.set_demo_mode("downwash")),
        (100,  win.go_live),
        (100,  lambda: win.live_views["downwash"].set_drone(True)),
        (1800, lambda: grab("live_downwash")),
        (100,  lambda: win.set_demo_mode("edf")),
        (100,  lambda: win.live_views["edf"].set_shield(True)),
        (1800, lambda: grab("live_edf")),
        (100,  lambda: win.set_demo_mode("aerobench")),
        (1800, lambda: grab("live_aerobench")),
        (100,  win.show_replay),
        (300,  win.replay_play),
        (1200, lambda: grab("replay")),
    ]
    it = iter(seq)

    def advance():
        try:
            delay, fn = next(it)
        except StopIteration:
            print("CAPTURE DONE")
            app.exit(0)
            return

        def run():
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
                app.exit(1)
                return
            advance()

        QTimer.singleShot(delay, run)

    advance()
    rc = app.exec()
    win.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
