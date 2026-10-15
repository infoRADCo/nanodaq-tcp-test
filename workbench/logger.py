"""SessionLogger — 세션 동안 받은 모든 샘플과 이벤트를 CSV 로 남긴다 (임시 로깅, `--log`).

두 파일을 쓴다 (`logs/`, git 무시):
  {session}.csv          샘플 로그. 소스 스레드가 패킷마다 on_packet() 으로 한 줄.
  {session}_events.csv   이벤트 로그. 접속 상태 전이·마커·Zero All·스냅샷 등.

샘플 로그 포맷은 monitor_gui.py 의 CSV 와 같은 골격이라 같은 판다스 리더로 읽힌다:
  첫 줄  '# workbench log key=value ...'   (pandas.read_csv(comment='#'))
  헤더   iso_time, elapsed_s, packet_index, ch1..16_raw, ch1..16_pa
raw 는 장비 ADC 카운트(16 bit) — 시뮬레이터는 raw 가 없어 빈칸. pa 는 링 버퍼에
들어간 값과 동일(Pa, 오프셋 보정 후).

데시메이션 없음: 100 Hz 기준 약 70 MB/h. 검증 단계에서는 전달률·끊김 분석에 전 샘플이
필요하고, 줄이는 것은 나중에 쉽다. flush 는 행마다 하지 않고 FLUSH_INTERVAL 마다.

스레드: on_packet 은 소스 스레드, event 는 UI 스레드에서 불린다. 파일이 다르므로
서로 막지 않지만, close 와 겹칠 수 있어 각각 락을 둔다. 디스크 오류가 나면 조용히
멈추지 않고 error 를 남기고 그 파일 쓰기를 중단한다.
"""

import csv
import threading
import time
from datetime import datetime
from pathlib import Path

FLUSH_INTERVAL = 1.0  # s
N_CH = 16


class SessionLogger:
    def __init__(self, out_dir: Path, session_name: str, meta: dict):
        out_dir = Path(out_dir)
        out_dir.mkdir(exist_ok=True)
        self.t0 = time.time()
        self.rows = 0
        self.events = 0
        self.error: str | None = None
        self._n = 0
        self._closed = False
        self._lock = threading.Lock()
        self._elock = threading.Lock()
        self._last_flush = self.t0

        # 세션명은 초 단위라 같은 초에 두 번 시작하면 이름이 겹친다. 'x' 모드로 열어 기존 증거를
        # 절대 덮어쓰지 않고, 겹치면 _1, _2 … 접미사를 붙인다. 두 파일이 한 쌍으로 같은 이름을 갖도록
        # 이벤트 파일이 겹치면 방금 만든 샘플 파일을 지우고 다음 이름으로 넘어간다.
        # newline="" 는 csv 모듈이 Windows 에서 빈 줄을 끼우지 않게 하는 필수 옵션.
        for k in range(1000):
            name = session_name if k == 0 else f"{session_name}_{k}"
            self.path = out_dir / f"{name}.csv"
            self.events_path = out_dir / f"{name}_events.csv"
            try:
                self._fh = open(self.path, "x", newline="", encoding="utf-8")
            except FileExistsError:
                continue
            try:
                self._efh = open(self.events_path, "x", newline="", encoding="utf-8")
            except FileExistsError:
                self._fh.close()
                self.path.unlink(missing_ok=True)
                continue
            break
        else:
            raise OSError(f"로그 파일 이름을 정하지 못함: {out_dir / session_name}")
        self.session = name
        self._w = csv.writer(self._fh)
        meta = {"session": name, "start": datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"), **meta}
        self._w.writerow(["# workbench log " + " ".join(f"{k}={v}" for k, v in meta.items())])
        header = ["iso_time", "elapsed_s", "packet_index"]
        header += [f"ch{i}_raw" for i in range(1, N_CH + 1)]
        header += [f"ch{i}_pa" for i in range(1, N_CH + 1)]
        self._w.writerow(header)
        self._fh.flush()

        self._ew = csv.writer(self._efh)
        self._ew.writerow(["iso_time", "elapsed_s", "kind", "note"])
        self._efh.flush()

    # ------------------------------------------------ 소스 스레드
    def on_packet(self, t: float, raw, pa) -> None:
        """패킷 1개 = 행 1개. raw: 16 개 int 또는 None(시뮬레이터). pa: 16 개 float."""
        if self._closed or self.error:
            return
        self._n += 1
        # elapsed 는 0.1 ms 해상도: 소스가 뭉친 패킷의 시각을 직전 샘플 +0.1 ms 로 조이므로
        # 3 자리로 반올림하면 같은 값이 되어 "역행/정지" 로 오독된다.
        row = [datetime.fromtimestamp(t).isoformat(timespec="milliseconds"), f"{t - self.t0:.4f}", self._n]
        row += [int(r) for r in raw] if raw is not None else [""] * N_CH
        row += [f"{v:.2f}" for v in pa]
        with self._lock:
            if self._closed:
                return
            try:
                self._w.writerow(row)
                self.rows += 1
                if t - self._last_flush >= FLUSH_INTERVAL:
                    self._fh.flush()
                    self._last_flush = t
            except (OSError, ValueError) as exc:
                self.error = f"샘플 로그 쓰기 실패: {exc}"

    # ------------------------------------------------ UI / 소스 어느 쪽에서든
    def event(self, kind: str, note: str = "", t: float | None = None) -> None:
        if self._closed:
            return
        t = time.time() if t is None else t
        with self._elock:
            if self._closed:
                return
            try:
                self._ew.writerow([datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                                   f"{t - self.t0:.3f}", kind, note])
                self._efh.flush()  # 이벤트는 드물고 중요하니 즉시
                self.events += 1
            except (OSError, ValueError) as exc:
                self.error = self.error or f"이벤트 로그 쓰기 실패: {exc}"

    def close(self) -> str:
        """두 파일을 닫고 한 줄 요약을 돌려준다. 두 번 불려도 안전.

        마지막 flush 가 실패하면 파일 끝이 잘린 것이므로 error 에 남기고 요약에도 붙인다.
        요약의 rows 는 writerow 가 성공한 행 수이지 디스크에 확정된 행 수가 아니다."""
        if self._closed:
            return ""
        self.event("session_end", f"rows={self.rows}")
        with self._lock, self._elock:
            self._closed = True
            for fh in (self._fh, self._efh):
                try:
                    fh.flush()
                    fh.close()
                except (OSError, ValueError) as exc:
                    self.error = self.error or f"로그 닫기 실패({Path(fh.name).name}): 마지막 구간이 잘렸을 수 있음: {exc}"
        summary = f"{self.path.name}: {self.rows} rows, {self.events} events"
        return summary + (f" | ERROR {self.error}" if self.error else "")
