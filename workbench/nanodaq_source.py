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
        self._rate_req = None          # 대기 중인 레이트 (Hz) — _lock 으로 보호

        # UI 가 읽는 상태 (스레드 안전 getter 로만 접근)
        self._state = "connecting"     # connecting | streaming | error
        self._message = f"{ip}:{port} 접속 중"
        self._info = {}                # Get Status(Full) 필드
        self._full_scale_pa = None
        self._last_packet_t = 0.0
        self._packets = 0

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

    def set_alpha(self, deg: int):
        pass

    def get_alpha(self) -> int:
        return 0

    # ------------------------------------------------ 데이터 레이트
    @staticmethod
    def available_rates():
        """장비가 받는 레이트 (Hz), 빠른 순."""
        return sorted(RATE_TABLE, reverse=True)

    def set_rate_hz(self, hz: int) -> bool:
        """레이트 변경을 요청한다. 실제 적용은 스트림 스레드가 한다.

        소켓은 스트림 스레드만 만지므로(다른 스레드에서 명령을 끼워 넣으면
        스트림 바이트와 ack 가 섞인다) rezero 와 같은 방식으로 요청만 남긴다.
        받아들일 수 있는 값이면 True.
        """
        if hz not in RATE_TABLE:
            return False
        with self._lock:
            self._rate_req = hz
        return True

    def get_offsets(self) -> np.ndarray:
        """영점 잔류 = 최근 1 s 평균. 실장비는 오프셋을 알 수 없으므로
        Setup 게이트가 '현재 평균이 0 근처인가' 를 보게 한다."""
        _, vs = self.ring.latest(int(self.rate_hz))
        if len(vs) < 3:
            return np.zeros(16)
        return vs.mean(axis=0)

    def zero_all(self):
        self._rezero_req.set()

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

            self._msg_head = (f"{fields.get('Model', 'nanoDAQ')} · FS {full_scale:g} {units}"
                              f" = {self._full_scale_pa:.0f} Pa")
            self._msg_tail = unit_note
            self._publish_streaming_msg()
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

    def _publish_streaming_msg(self):
        self._set("streaming",
                  f"{self._msg_head} · {self.rate_hz:.0f} Hz{self._msg_tail}")

    def _stream_loop(self, client, channels, full_scale, to_pa):
        buf = PacketBuffer(channels)
        vals = np.zeros(16)
        while not self._stop_evt.is_set():
            with self._lock:
                new_hz, self._rate_req = self._rate_req, None
            if new_hz is not None and new_hz != self.rate_hz:
                # rezero 와 같은 이유로 스트림을 멈추고 명령을 보낸다.
                client.set_timeout(COMMAND_TIMEOUT)
                client.stream_off(Channel.TCP_UDP)
                client.flush_input()
                ok = client.set_rate(RATE_TABLE[new_hz], Channel.TCP_UDP)
                client.stream_on(Channel.TCP_UDP)
                buf = PacketBuffer(channels)
                if ok is False:
                    # 장비가 거부 — 오버샘플링 설정이 상한을 낮춰둔 경우가 대표적.
                    # 이전 레이트가 그대로 살아 있으므로 상태로만 알린다.
                    self._set("streaming",
                              f"{self._msg_head} · {self.rate_hz:.0f} Hz{self._msg_tail}"
                              f" · {new_hz} Hz 거부됨 (오버샘플링 상한 확인)")
                else:
                    self.rate_hz = float(new_hz)
                    self._rate_code = RATE_TABLE[new_hz]
                    self._publish_streaming_msg()

            if self._rezero_req.is_set():
                self._rezero_req.clear()
                # 스트림 바이트와 ack 가 섞이지 않도록 잠시 멈추고 rezero
                client.set_timeout(COMMAND_TIMEOUT)
                client.stream_off(Channel.TCP_UDP)
                client.flush_input()
                client.rezero()
                client.stream_on(Channel.TCP_UDP)
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
            now = time.time()
            period = 1.0 / self.rate_hz
            for k, packet in enumerate(packets):
                for i, raw in enumerate(packet.values[:16]):
                    vals[i] = scale_differential(raw, full_scale) * to_pa
                self.ring.append(now - (len(packets) - 1 - k) * period, vals)
            with self._lock:
                self._packets += len(packets)
                self._last_packet_t = now
