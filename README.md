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

```bash
python test_connection.py --ip 192.168.1.190 --channels 16 --duration 5
```

스크립트는 다음 순서로 동작합니다:

1. `<ip>:101` 로 TCP 연결
2. Protocol 명령으로 데이터 포맷을 16bit Little-Endian 바이너리로 설정
3. Stream ON 명령 전송 (TCP/UDP 채널) 후 ACK 확인
4. 들어오는 패킷을 헤더(`00 FF 00`)로 식별해 채널값 디코딩, 지정한 개수/시간만큼 출력
5. Stream OFF 명령 전송 후 연결 종료

## 파일 구성

| 파일 | 설명 |
|---|---|
| `nanodaq_client.py` | 명령 프레임 생성(패리티 포함), 패킷 파싱, `NanoDAQClient` 클래스 |
| `test_connection.py` | CLI 연결 테스트 스크립트 |

## 참고

- 패킷 구조, 명령 코드는 Chell `nanoDAQ-LT User Programming Guide`(900222) 기준입니다.
- `iter_binary_packets()`는 데모용 best-effort 리더입니다. TCP 스트림이 패킷 경계와 어긋나게
  수신될 수 있으므로(문서 4.1.4절), 운영 환경에서는 버퍼에 누적 후 헤더(`00 FF 00`)를 탐색하는
  방식으로 재동기화 로직을 보강하는 것을 권장합니다.
- 장비 시리얼번호, 실제 계약/가격 정보 등 민감정보는 이 저장소에 포함하지 않습니다.
