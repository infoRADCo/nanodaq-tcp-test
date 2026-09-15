"""장비 없이 NanoDAQSource 를 시험하기 위한 nanoDAQ-LT(S) TCP 서버 에뮬레이터.

실장비에서 관찰된 동작만 흉내 낸다 (nanodaq_client.py 주석·README 참고):
- 포트에서 TCP 서버로 대기, 연결 1개
- 명령 프레임 `> cmd param parity <` 수신 → `**` ack (Get Status 는 ack 없이 상태 프레임)
- Get Status(Full): `>` + 2 status bytes + `<` + ASCII `[Field] value,` 나열
- Stream On + Rate 설정 후 `00 FF 00` + 16ch × 16bit LE 패킷을 rate 로 송신
- Rezero: 현재 오프셋을 0 으로

압력 시나리오: CH1~16 에 각각 다른 위상의 저주파 사인파(±300 Pa) + 잡음.
`--ch N --pa P` 로 특정 채널에 고정 압력을 얹어 "포트에 압력 가함" 을 흉내 낸다.

    python scripts/fake_nanodaq_server.py --port 10101 --ch 7 --pa 800
    python -m workbench --ip 127.0.0.1 --port 10101
"""

import argparse
import math
import random
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nanodaq_client import START, END, Command, DataRate  # noqa: E402

RATE_HZ = {int(r): hz for r, hz in (
    (DataRate.HZ_200, 200), (DataRate.HZ_150, 150), (DataRate.HZ_100, 100),
    (DataRate.HZ_50, 50), (DataRate.HZ_25, 25), (DataRate.HZ_20, 20),
    (DataRate.HZ_10, 10), (DataRate.HZ_5, 5), (DataRate.HZ_1, 1))}

FULL_SCALE = 1.0      # PSI (인포라드 보유 nanoDAQ-LTS-16 = FS 1 PSIG)
UNITS = "PSI"
PA_PER_PSI = 6894.757


def status_full() -> bytes:
    fields = [("Model", "nanoDAQ-LTS-16 (fake)"), ("Firmware", "2.2.2"),
              ("Active channels", "16"), ("Full scale", f"{FULL_SCALE:g}"),
              ("Press. units", UNITS), ("Press. type", "Differential")]
    trailer = ",".join(f"[{k}] {v}" for k, v in fields)
    return bytes((START, 0x00, 0x00, END)) + trailer.encode("ascii")


def encode_packet(pa_values, offsets) -> bytes:
    raw = []
    for pa, off in zip(pa_values, offsets):
        psi = (pa - off) / PA_PER_PSI
        r = int(round((psi + FULL_SCALE) / (2 * FULL_SCALE) * 65535))
        raw.append(max(0, min(65535, r)))
    return b"\x00\xff\x00" + struct.pack("<16H", *raw)


def serve(conn, args):
    conn.settimeout(0.0)
    streaming = False
    rate = 0
    offsets = [0.0] * 16
    zero_err = [random.uniform(-15, 15) for _ in range(16)]  # 초기 영점 오차
    inbuf = bytearray()
    t0 = time.time()
    next_t = time.perf_counter()
    n_sent = 0

    while True:
        try:
            data = conn.recv(256)
            if not data:
                return
            inbuf.extend(data)
        except BlockingIOError:
            pass

        # 명령 프레임 처리
        while len(inbuf) >= 5:
            i = inbuf.find(bytes((START,)))
            if i < 0:
                inbuf.clear(); break
            if i > 0:
                del inbuf[:i]
            if len(inbuf) < 5:
                break
            cmd, param = inbuf[1], inbuf[2]
            del inbuf[:5]
            if cmd == Command.GET_STATUS:
                conn.sendall(status_full() if param == 2 else bytes((START, 0, 0, END)))
                continue
            if cmd == Command.STREAM_ON:
                streaming = True
            elif cmd == Command.STREAM_OFF or cmd == Command.STANDBY:
                streaming = False
            elif cmd == Command.RATE:
                rate = RATE_HZ.get(param & 0x0F, 0)
                print(f"[fake] rate -> {rate} Hz (param 0x{param:02x})")
            elif cmd == Command.REZERO:
                offsets = list(zero_err)
                print("[fake] rezero")
            elif cmd == Command.PROTOCOL:
                print(f"[fake] protocol param 0x{param:02x}")
            if cmd != Command.STANDBY:
                conn.sendall(b"**")

        if streaming and rate > 0:
            now = time.perf_counter()
            if now >= next_t:
                ts = time.time() - t0
                vals = [zero_err[i] + 300.0 * math.sin(2 * math.pi * 0.1 * ts + i * 0.4)
                        + random.gauss(0, 2.0) for i in range(16)]
                if args.ch:
                    vals[args.ch - 1] += args.pa
                conn.sendall(encode_packet(vals, offsets))
                n_sent += 1
                next_t += 1.0 / rate
                if n_sent % (rate * 5) == 0:
                    print(f"[fake] {n_sent} packets sent")
            else:
                time.sleep(min(0.005, next_t - now))
        else:
            time.sleep(0.02)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10101)
    ap.add_argument("--ch", type=int, default=0, help="고정 압력을 얹을 채널 (1-16)")
    ap.add_argument("--pa", type=float, default=800.0, help="얹을 압력 Pa")
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(1)
    print(f"[fake] nanoDAQ emulator listening on 127.0.0.1:{args.port}")
    while True:
        conn, addr = srv.accept()
        print(f"[fake] client {addr}")
        try:
            serve(conn, args)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            conn.close()
            print("[fake] client gone")


if __name__ == "__main__":
    main()
