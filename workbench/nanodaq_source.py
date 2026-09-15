"""NanoDAQSource — 실장비(nanoDAQ-LT/LTS) TCP 스트림을 링 버퍼에 쓰는 스레드.

SimSource 와 같은 인터페이스(set_mode/set_drone/…/zero_all/get_offsets/stop)를
노출해 main_window·views 코드를 바꾸지 않고 데이터 공급부만 교체한다.
데모 제어(드론/차폐/RPM)는 실장비에서는 의미가 없으므로 no-op.

접속 절차·프로토콜 처리는 실장비(nanoDAQ-LTS-16, FW 2.2.2)로 검증한
monitor_gui.Worker 를 그대로 따른다. 데이터는 링 버퍼로만 흐른다.
"""

import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

# 레포 루트의 nanodaq_client.py 를 패키지 밖에서 가져온다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nanodaq_client import (Channel, Command, DataRate, NanoDAQClient,  # noqa: E402
                            PacketBuffer, Protocol, StatusLevel,
                            scale_differential)

COMMAND_TIMEOUT = 2.0       # 명령 왕복 소켓 타임아웃
STREAM_READ_TIMEOUT = 0.2   # 스트림 폴링 소켓 타임아웃
RECONNECT_DELAY = 2.0       # 연결 실패/끊김 후 재시도 간격
STALE_AFTER = 1.5           # 이 시간 동안 패킷이 없으면 '데이터 없음'

RATE_TABLE = {
    200: DataRate.HZ_200, 150: DataRate.HZ_150, 100: DataRate.HZ_100,
    50: DataRate.HZ_50, 25: DataRate.HZ_25, 20: DataRate.HZ_20,
    10: DataRate.HZ_10, 5: DataRate.HZ_5, 1: DataRate.HZ_1,
}

# nanoDAQ-LTS-16 (FW 2.2.2) 은 Get Status(Full) 응답을 [CAN message] 에서 끊어
# 보내며, 매뉴얼(fig 3.2)에 있는 [Press. units]·[Press. type] 을 아예 싣지 않는다.
# 실장비로 확인: 4 초를 기다려도 그 뒤가 오지 않는다.
#
# 이때 환산 계수를 1.0 으로 두면 psi 값이 Pa 라벨을 달고 표시되어 6,895 배
# 작은 수가 조용히 나간다(620 Pa → 0.09). 그래서 '단위 미보고' 는 psi 로 가정한다:
#   - 데이터시트의 레인지 표기가 psi 이고 [Full scale] 1.0 이 ±1 psi 와 일치
#   - 실측 검증 — 전 포트 대기 개방 상태에서 raw 가 중앙값 32,768 부근에 앉는데,
#     이는 차압 ±FS 스케일링에서만 나오는 값이다 (절대압 해석이면 ~56,575)
# 값을 신뢰할 수 없는 경우는 '모르는 단위 문자열이 온 경우' 뿐이고, 그때만 환산을
# 포기한다. 어느 쪽이든 가정한 사실은 상태줄에 남긴다.
ASSUMED_UNITS = "psi"

# Get Status 의 [Press. units] → Pa 환산 계수. 화면은 항상 Pa 로 표시한다.
UNITS_TO_PA = {
    "pa": 1.0, "hpa": 100.0, "kpa": 1000.0, "mbar": 100.0, "bar": 1e5,
    "psi": 6894.757, "psig": 6894.757, "psid": 6894.757,
    "inh2o": 249.089, "mmh2o": 9.80665, "mmhg": 133.322, "torr": 133.322,
}


class NanoDAQSource(threading.Thread):
    def __init__(self, ring, ip: str, port: int = 101, rate_hz: int = 50):
        super().__init__(daemon=True, name="NanoDAQSource")
        self.ring = ring
        self.ip = ip
        self.port = port
        if rate_hz not in RATE_TABLE:
            raise ValueError(f"지원하지 않는 rate: {rate_hz} (가능: {sorted(RATE_TABLE)})")
        self.rate_hz = float(rate_hz)
        self._rate_code = RATE_TABLE[rate_hz]

        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._rezero_req = threading.Event()

        # UI 가 읽는 상태 (스레드 안전 getter 로만 접근)
        self._state = "connecting"     # connecting | streaming | error
        self._message = f"{ip}:{port} 접속 중"
        self._info = {}                # Get Status(Full) 필드
        self._full_scale_pa = None
        self._last_packet_t = 0.0
        self._packets = 0
        self._last_sample_t = 0.0     # 링 버퍼에 마지막으로 쓴 타임스탬프 (단조 증가 보장용)

        # Zero All 결과. UI 는 요청 즉시 '완료' 로 그리지 않고 이 상태를 본다.
        #   None | "pending" | "done" | "failed"
        self._zero_state = None
        self._zero_t = 0.0            # done/failed 가 확정된 시각
        self._zero_msg = ""

    # ------------------------------------------------ SimSource 호환 인터페이스
    def set_mode(self, key: str):
        pass

    def set_drone(self, on: bool):
        pass

    def set_shield(self, on: bool):
        pass

    def set_rpm(self, rpm: int):
        pass

    def get_rpm(self) -> int:
        return 0

    def get_offsets(self) -> np.ndarray:
        """영점 잔류 = 최근 1 s 평균. 실장비는 오프셋을 알 수 없으므로
        Setup 게이트가 '현재 평균이 0 근처인가' 를 보게 한다."""
        _, vs = self.ring.latest(int(self.rate_hz))
        if len(vs) < 3:
            return np.zeros(16)
        return vs.mean(axis=0)

    def zero_all(self):
        """장비 Rezero 요청. 실제 실행·결과는 스트림 스레드가 status()["zero"] 로 보고한다."""
        with self._lock:
            if self._state != "streaming":
                # 접속 전/끊김 중에는 요청을 쌓아두지 않는다 — 나중에 언제 실행될지
                # 알 수 없는 영점을 UI 가 '완료' 로 오해하는 것을 막는다.
                self._zero_state, self._zero_t = "failed", time.time()
                self._zero_msg = "장비 미접속 — Rezero 를 보내지 않음"
                return
            self._zero_state, self._zero_msg = "pending", ""
        self._rezero_req.set()

    def _finish_zero(self, ok: bool, msg: str):
        with self._lock:
            self._zero_state = "done" if ok else "failed"
            self._zero_t = time.time()
            self._zero_msg = msg

    def stop(self):
        self._stop_evt.set()

    # ------------------------------------------------ 상태 조회 (UI 용)
    def status(self) -> dict:
        with self._lock:
            state = self._state
            if state == "streaming" and time.time() - self._last_packet_t > STALE_AFTER:
                state = "stale"
            return {
                "state": state,
                "message": self._message,
                "info": dict(self._info),
                "full_scale_pa": self._full_scale_pa,
                "packets": self._packets,
                "zero": self._zero_state,
                "zero_t": self._zero_t,
                "zero_msg": self._zero_msg,
            }

    def _set(self, state: str, message: str):
        with self._lock:
            self._state = state
            self._message = message

    # ------------------------------------------------ 메인 루프
    def run(self):
        while not self._stop_evt.is_set():
            try:
                self._session()
            except Exception as exc:  # 세션 중 예외는 상태로만 보고, 스레드는 살린다
                self._set("error", f"{type(exc).__name__}: {exc}")
            # 세션이 끝났는데 Rezero 요청이 남아 있으면 실패로 확정한다.
            if self._rezero_req.is_set():
                self._rezero_req.clear()
                self._finish_zero(False, "연결이 끊겨 Rezero 미실행")
            if self._stop_evt.is_set():
                break
            self._stop_evt.wait(RECONNECT_DELAY)

    def _session(self):
        self._set("connecting", f"{self.ip}:{self.port} 접속 중")
        client = NanoDAQClient(self.ip, self.port, timeout=COMMAND_TIMEOUT)
        try:
            client.connect()
        except OSError as exc:
            self._set("error", f"접속 실패: {exc}")
            return

        try:
            # 이전 세션의 스트림이 살아 있을 수 있음 — 명령 전 정숙화 (Worker.run 과 동일)
            client.flush_input()
            client.send_command(Command.STANDBY, read_ack=False)
            client.flush_input()

            status = client.get_status(StatusLevel.FULL)
            fields = status.fields
            channels = int(fields.get("Active channels") or fields.get("TCP channels") or 16)
            full_scale = float(fields.get("Full scale", "1"))
            units = fields.get("Press. units", "").strip()
            to_pa = UNITS_TO_PA.get(units.lower().replace(" ", ""))
            if to_pa is not None:
                unit_note = ""
            elif not units:
                # 장비가 단위를 아예 안 보냄 (LTS-16 FW 2.2.2) — psi 로 가정한다.
                units = ASSUMED_UNITS
                to_pa = UNITS_TO_PA[ASSUMED_UNITS]
                unit_note = f" · 단위 미보고 → {ASSUMED_UNITS} 가정"
            else:
                # 모르는 단위 문자열: 추측하지 않는다. 환산 없이 내보내되 크게 알린다.
                to_pa = 1.0
                unit_note = f" · 단위 '{units}' 미환산 — 표시값은 Pa 가 아님"
            with self._lock:
                self._info = dict(fields)
                self._full_scale_pa = full_scale * to_pa
            if channels != 16:
                self._set("error", f"채널 수 {channels} — 이 워크벤치는 16ch 전용")
                return

            client.set_protocol(Protocol.BINARY_LE, Channel.TCP_UDP)
            client.set_rate(self._rate_code, Channel.TCP_UDP)
            client.stream_on(Channel.TCP_UDP)

            self._set("streaming",
                      f"{fields.get('Model', 'nanoDAQ')} · FS {full_scale:g} {units}"
                      f" = {self._full_scale_pa:.0f} Pa · {self.rate_hz:.0f} Hz{unit_note}")
            with self._lock:
                self._last_packet_t = time.time()

            self._stream_loop(client, channels, full_scale, to_pa)
        finally:
            try:
                client.set_timeout(COMMAND_TIMEOUT)
                client.stream_off(Channel.TCP_UDP)
            except Exception:
                pass
            client.close()

    def _stream_loop(self, client, channels, full_scale, to_pa):
        buf = PacketBuffer(channels)
        vals = np.zeros(16)
        while not self._stop_evt.is_set():
            if self._rezero_req.is_set():
                self._rezero_req.clear()
                self._do_rezero(client)
                buf = PacketBuffer(channels)

            client.set_timeout(STREAM_READ_TIMEOUT)
            try:
                data = client.read_raw()
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("장비가 연결을 닫음")

            buf.feed(data)
            packets = []
            packet = buf.pop_packet()
            while packet is not None:
                packets.append(packet)
                packet = buf.pop_packet()
            if not packets:
                continue
            # 한 번의 recv 에 여러 패킷이 뭉쳐 올 수 있음(저속 TCP 버퍼링) —
            # 타임스탬프를 샘플 주기로 펼쳐 마지막 패킷이 now 가 되게 한다.
            # 단, 한 버스트가 두 recv 로 쪼개져 연달아 오면 역산한 시각이 직전
            # 묶음과 겹칠 수 있으므로, 직전 샘플보다 항상 뒤가 되도록 조인다
            # (리플레이 경과 시간이 뒤로 가지 않게).
            now = time.time()
            period = 1.0 / self.rate_hz
            last_t = self._last_sample_t
            for k, packet in enumerate(packets):
                for i, raw in enumerate(packet.values[:16]):
                    vals[i] = scale_differential(raw, full_scale) * to_pa
                t = max(now - (len(packets) - 1 - k) * period, last_t + 1e-4)
                self.ring.append(t, vals)
                last_t = t
            self._last_sample_t = last_t
            with self._lock:
                self._packets += len(packets)
                self._last_packet_t = now

    def _do_rezero(self, client):
        """스트림 중단 → Rezero → 재개. 결과는 _finish_zero 로 UI 에 알린다.

        스트리밍 중에는 명령 ack(`**`)가 진행 중인 패킷 사이에 섞여 오므로
        2 바이트 ack 읽기가 실패한다(장비는 실제로 멈췄는데도). 그래서 Stream Off 는
        stream_off_quiesce 로 '회선이 조용해졌는가' 를 성공 기준으로 삼고, 그 뒤의
        Rezero·Stream On 은 깨끗한 회선에서 ack 를 확인한다. 소켓 오류는 세션
        종료로 이어지도록 그대로 올린다(run() 이 failed 로 확정).
        """
        client.set_timeout(COMMAND_TIMEOUT)
        if not client.stream_off_quiesce(Channel.TCP_UDP):
            self._finish_zero(False, "Stream Off 후에도 장비가 계속 송신 — Rezero 생략")
            return
        try:
            ok = client.rezero()
        except (socket.timeout, RuntimeError) as exc:
            ok = False
            err = f"Rezero ack 없음 ({exc})"
        else:
            err = "장비가 Rezero 를 거부 (!!)"
        try:
            resumed = client.stream_on(Channel.TCP_UDP)
        except (socket.timeout, RuntimeError):
            resumed = False
        if ok and resumed:
            self._finish_zero(True, "장비 Rezero 완료 (16ch)")
        elif ok:
            self._finish_zero(True, "Rezero 완료 — 스트림 재개 실패, 재접속 대기")
            raise ConnectionError("Stream On 실패 after rezero")
        else:
            self._finish_zero(False, err)
            if not resumed:
                raise ConnectionError("Stream On 실패 after rezero")
