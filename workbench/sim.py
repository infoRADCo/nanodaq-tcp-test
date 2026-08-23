"""SimSource — 50 Hz로 16채널 프레임을 생성해 링 버퍼에 쓰는 스레드.

실제 장비 I/O 없음. 데모 모드 + 제어 상태(스레드 안전 setter)에 따라
시나리오가 달라진다. 데이터는 링 버퍼로만 흐른다.
"""

import threading
import time

import numpy as np

from . import profiles

RATE_HZ = 50.0
RHO = profiles.RHO


class SimSource(threading.Thread):
    def __init__(self, ring):
        super().__init__(daemon=True, name="SimSource")
        self.ring = ring
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._rng = np.random.default_rng(20260823)

        self._mode = "downwash"
        self._drone = False
        self._shield = False
        self._rpm = 4000

        # 품질 게이트 스토리용 초기 영점 오프셋: CH3 +12 Pa, CH7 −9 Pa (1-based)
        off = np.zeros(16)
        off[2] = 12.0
        off[6] = -9.0
        self._offsets = off
        self._t0 = time.time()

    # ------------------------------------------------ 스레드 안전 제어
    def set_mode(self, key: str):
        with self._lock:
            self._mode = key

    def set_drone(self, on: bool):
        with self._lock:
            self._drone = bool(on)

    def set_shield(self, on: bool):
        with self._lock:
            self._shield = bool(on)

    def set_rpm(self, rpm: int):
        with self._lock:
            self._rpm = int(rpm)

    def get_rpm(self) -> int:
        with self._lock:
            return self._rpm

    def get_offsets(self) -> np.ndarray:
        with self._lock:
            return self._offsets.copy()

    def zero_all(self):
        with self._lock:
            self._offsets = np.zeros(16)

    def stop(self):
        self._stop_evt.set()

    # ------------------------------------------------ 메인 루프
    def run(self):
        period = 1.0 / RATE_HZ
        next_t = time.perf_counter()
        while not self._stop_evt.is_set():
            with self._lock:
                mode = self._mode
                drone = self._drone
                shield = self._shield
                rpm = self._rpm
                off = self._offsets
            ts = time.time() - self._t0
            if mode == "downwash":
                vals = self._gen_downwash(ts, drone)
            elif mode == "edf":
                vals = self._gen_edf(ts, shield)
            else:
                vals = self._gen_aerobench(ts, rpm)
            self.ring.append(time.time(), vals + off)

            next_t += period
            dt = next_t - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
            else:  # 밀린 경우 리셋
                next_t = time.perf_counter()

    # ------------------------------------------------ 시나리오
    def _gen_downwash(self, ts, drone):
        pos = profiles.DOWNWASH["positions"]  # (16,2) mm
        vals = self._rng.normal(0.0, 0.8, 16)
        if drone:
            # 중심이 사인파로 ±40 mm 배회하는 가우시안 압력 블롭 (피크 ~+120 Pa)
            cx = 40.0 * np.sin(2 * np.pi * 0.05 * ts)
            cy = 40.0 * np.sin(2 * np.pi * 0.037 * ts + 1.3)
            d2 = (pos[:, 0] - cx) ** 2 + (pos[:, 1] - cy) ** 2
            vals += 120.0 * np.exp(-d2 / (2 * 110.0 ** 2))
            # 가장자리 약한 음압 링 (~−12 Pa)
            r = np.sqrt(d2)
            vals += -12.0 * np.exp(-((r - 265.0) / 70.0) ** 2)
        return vals

    def _gen_edf(self, ts, shield):
        vals = -150.0 + self._rng.normal(0.0, 3.0, 16)
        if shield:
            # 90° 섹터 내 프로브에 최대 −240 Pa 추가 결손 (코사인 감쇠)
            th = profiles.EDF["theta_deg"]
            center = 90.0 + 8.0 * np.sin(2 * np.pi * 0.06 * ts)
            dang = (th - center + 180.0) % 360.0 - 180.0
            fall = np.maximum(np.cos(np.pi * dang / 90.0), 0.0)
            fall[np.abs(dang) > 45.0] = 0.0
            # 외측 링이 조금 더 깊게 결손
            ring_w = np.where(profiles.EDF["positions"][:, 0] ** 2
                              + profiles.EDF["positions"][:, 1] ** 2
                              > (0.7 ** 2), 1.0, 0.85)
            vals += -240.0 * (fall ** 1.5) * ring_w
        return vals

    def _gen_aerobench(self, ts, rpm):
        x = profiles.AERO_R  # (13,)
        qmax = 600.0 * (rpm / 8000.0) ** 2
        flutter = 1.0 + 0.03 * np.sin(2 * np.pi * 0.9 * ts + x * 4.0)
        q = qmax * profiles.wake_shape(x) * flutter
        vals = np.zeros(16)
        vals[0:13] = q + self._rng.normal(0.0, 1.5, 13)
        vals[13] = self._rng.normal(0.0, 0.5)              # 정압 기준
        vals[14] = qmax * 0.15 + self._rng.normal(0.0, 0.8)  # 자유류 기준
        vals[15] = self._rng.normal(0.0, 0.3)              # 예비
        return vals
