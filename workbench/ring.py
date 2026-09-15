"""스레드 안전 numpy 링 버퍼 — 16채널 + 타임스탬프.

시뮬레이터 스레드가 append(), UI 타이머가 latest()/snapshot()으로 읽는다.
데이터는 오직 이 버퍼를 통해서만 흐른다 (샘플 단위 시그널 없음).
"""

import threading

import numpy as np


class RingBuffer:
    def __init__(self, capacity: int = 16000, channels: int = 16):
        # 기본 16000 샘플 = 50 Hz 기준 약 5.3분
        self._cap = int(capacity)
        self._nch = int(channels)
        self._t = np.zeros(self._cap, dtype=np.float64)
        self._v = np.zeros((self._cap, self._nch), dtype=np.float64)
        self._written = 0  # 총 기록 샘플 수 (단조 증가)
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._cap

    @property
    def count(self) -> int:
        with self._lock:
            return min(self._written, self._cap)

    def append(self, t: float, values) -> None:
        with self._lock:
            i = self._written % self._cap
            self._t[i] = t
            self._v[i, :] = values
            self._written += 1

    def latest(self, n: int):
        """최근 n개 샘플 스냅샷 (오래된 것 → 최신 순). (times, values) 반환."""
        with self._lock:
            m = min(int(n), self._written, self._cap)
            if m <= 0:
                return (np.empty(0, dtype=np.float64),
                        np.empty((0, self._nch), dtype=np.float64))
            end = self._written % self._cap
            idx = np.arange(end - m, end) % self._cap
            return self._t[idx].copy(), self._v[idx].copy()

    def snapshot(self):
        """버퍼에 남은 전체 이력 스냅샷."""
        return self.latest(self._cap)
