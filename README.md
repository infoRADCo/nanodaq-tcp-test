# nanodaq-tcp-test

Chell **nanoDAQ-LT / nanoDAQ-LTS** 압력 스캐너와의 TCP 연결 테스트용 샘플 코드입니다.
Python 표준 라이브러리(`socket`)만 사용하며 추가 의존성이 없습니다.

## 배경

nanoDAQ-LT(S)는 RS232 없이 **이더넷(TCP/UDP)과 CAN만 지원**하며, 로컬 포트 **101**번에서
항상 **TCP 서버**로 대기합니다. 즉 PC 쪽 프로그램이 **TCP 클라이언트**로 접속해야 하고,
동시에 1개의 TCP 연결만 허용합니다.

명령 프로토콜은 다음과 같은 프레임 구조를 사용합니다 (`nanoDAQ-LT User Programming Guide` 참고):

```
'>' (0x3E) + 명령 바이트 + 파라미터 바이트 + 패리티 바이트 + '<' (0x3C)
```

패리티는 시작/명령/파라미터/종료 바이트 전체에 대한 짝수 블록 패리티(XOR)입니다.
정상 수신 시 장비는 `**` 로, 오류 시 `!!` 로 응답합니다.

## 사전 준비

1. nanoDAQ 본체 뒷면 라벨에서 실제 IP 주소를 확인합니다 (예: `192.168.1.190`).
2. PC의 네트워크 어댑터를 nanoDAQ와 같은 서브넷의 고정 IP로 설정합니다
   (예: 어댑터 `192.168.1.191/255.255.255.0`).
3. `ping <nanoDAQ IP>` 로 응답이 오는지 확인합니다.
4. 다른 프로그램(microDAQX, Serial Debug Assistant 등)이 이미 연결을 잡고 있지 않은지 확인합니다
   (장비는 TCP 연결을 1개만 허용).

## 사용법

### CLI 연결 테스트

```bash
python test_connection.py --ip 192.168.1.190 --channels 16 --duration 5
```

스크립트는 다음 순서로 동작합니다:

1. `<ip>:101` 로 TCP 연결
2. Protocol 명령으로 데이터 포맷을 16bit Little-Endian 바이너리로 설정
3. Stream ON 명령 전송 (TCP/UDP 채널) 후 ACK 확인
4. 들어오는 패킷을 헤더(`00 FF 00`)로 식별해 채널값 디코딩, 지정한 개수/시간만큼 출력
5. Stream OFF 명령 전송 후 연결 종료

### 실시간 모니터링 GUI

```bash
python monitor_gui.py
```

또는 `run_monitor.bat`을 더블클릭 (콘솔창 없이 바로 GUI만 뜸).

IP/Port 입력 후 **Connect**를 누르면 자동으로:
1. 접속 → 잔여 스트림 데이터 flush → Standby
2. `Get Status(Full)`로 채널 수 / Full Scale / 압력 타입 자동 인식
3. Protocol(16bit LE) + Rate(100Hz) 설정 → Stream On
4. 압력값 실시간 표시, 온도는 5초 간격으로 raw 카운트 표시(섭씨 변환 아님 - 이유는 아래 참고)

상단 `Stream On/Off`, `Rezero` 버튼으로 수동 제어도 가능합니다.

## 파일 구성

| 파일 | 설명 |
|---|---|
| `nanodaq_client.py` | 명령 프레임 생성(패리티 포함), 패킷 파싱, `NanoDAQClient` 클래스 |
| `test_connection.py` | CLI 연결 테스트 스크립트 |
| `monitor_gui.py` | 실시간 채널 압력/온도 모니터링 GUI (tkinter) |
| `run_monitor.bat` | `monitor_gui.py`를 콘솔창 없이 더블클릭 실행하는 launcher |

## 알려진 이슈 / 트러블슈팅

실제 nanoDAQ-LTS-16 유닛(펌웨어 2.2.2)으로 검증하는 과정에서, 매뉴얼과 다르게 동작하는
부분들을 발견했습니다. 자세한 내용은 이 레포의 **Wiki**를 참고하세요:

- Rate 명령의 채널 선택 값이 매뉴얼(4=TCP/UDP, 8=CAN)과 실제 장비(1=TCP/UDP, 2=CAN)가 다름
- Get Status는 다른 명령과 달리 별도의 `**` ack 없이 바로 상태 프레임으로 응답함
- Data Rate가 "Off"로 설정된 상태에서는 Stream On을 보내도 데이터가 전혀 나가지 않음
- 저속(1~5Hz)에서는 TCP 버퍼링으로 인해 데이터가 몇 초 단위로 뭉쳐서 도착함 (정상 동작)
- 이 유닛은 `Get Status`의 보정된 온도(With temp./Full)가 항상 `0.00`을 반환함 — 원인 불명의
  펌웨어 결함으로 보이며, 대신 raw 값(level 4)을 그대로 사용

## 참고

- 패킷 구조, 명령 코드는 Chell `nanoDAQ-LT User Programming Guide`(900222) 기준입니다.
- `iter_binary_packets()`는 데모용 best-effort 리더입니다. TCP 스트림이 패킷 경계와 어긋나게
  수신될 수 있으므로(문서 4.1.4절), 운영 환경에서는 버퍼에 누적 후 헤더(`00 FF 00`)를 탐색하는
  방식으로 재동기화 로직을 보강하는 것을 권장합니다.
- 장비 시리얼번호, 실제 계약/가격 정보 등 민감정보는 이 저장소에 포함하지 않습니다.
