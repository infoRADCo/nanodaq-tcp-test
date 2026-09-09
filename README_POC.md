# 전시 계측 워크벤치 PoC

16채널 압력 DAQ(nanoDAQ) 전시 워크벤치의 **소프트웨어 컨셉 설명용 목업 UI**입니다.
월요일 회의에서 "전시 데모 소프트웨어가 어떤 모습·흐름인지"를 보여주기 위한 것으로,
**모든 데이터는 시뮬레이션**이며 실장비 I/O는 없습니다.

설계 근거: **infoRADCo/daq Issue #29** 및 **와이어프레임 설계안 v0.1** 참조.

## 실행법

```
cd nanodaq-tcp-test
.venv\Scripts\python.exe -m workbench
```

- 의존성: PySide6, pyqtgraph, numpy, scipy (레포의 `.venv`에 설치되어 있음)
- 자가 점검(오프스크린): `.venv\Scripts\python.exe -m workbench --selftest`

### 실장비(nanoDAQ) 연결

```
.venv\Scripts\python.exe -m workbench --ip 192.168.1.190 [--port 101] [--rate 50]
```

`--ip`를 주면 시뮬레이터 대신 `workbench/nanodaq_source.py`의 **NanoDAQSource**가
장비 스트림을 링 버퍼에 쓴다. 화면 코드는 동일하다 (데이터 공급부만 교체).

- 접속 절차는 `monitor_gui.py`에서 실장비로 검증한 순서 그대로: flush → Standby →
  Get Status(Full) → Protocol 16bit LE → Rate → Stream On
- Get Status의 `Full scale`·`Press. units`로 값을 **Pa로 환산**해 표시 (PSI 1.0 → 6,895 Pa)
- `Zero All` = 스트림 잠시 중단 → 장비 `Rezero` → 재개. Setup 탭의 "영점 잔류"는
  최근 1 s 평균이므로 무풍·무압 상태에서 실행해야 게이트가 통과된다.
- 헤더 우측 알약: `● nanoDAQ N pkt`(수신 중) / `● 데이터 없음`(1.5 s 무패킷) /
  `● 접속 중` / `● 연결 오류`. 끊기면 2 s 간격으로 자동 재접속.
- 드론 ON/차폐 ON/RPM 등 데모 제어 버튼은 실장비 모드에서 아무 동작도 하지 않는다.
- 장비 없이 시험: `scripts/fake_nanodaq_server.py --port 10101 --ch 7 --pa 800`
  (CH7에 +800 Pa 얹은 에뮬레이터) → `-m workbench --ip 127.0.0.1 --port 10101`
- 화면 캡처 생성: `.venv\Scripts\python.exe scripts\capture_screens.py`
  → `docs/poc_screens/*.png` 5장

## 무엇이 목업이고 무엇이 실제인가

| 구성 요소 | 상태 |
|---|---|
| 16ch 데이터 흐름 (50 Hz → 링 버퍼 → UI 타이머) | **실제 구조** — 실장비 연결 시 그대로 사용할 파이프라인 |
| RBF 보간 히트맵 (16점 → 200×200, 프레임당 ~0.3 ms) | **실제 계산** — 성능 스파이크 겸용, 10 FPS 여유 확인됨 |
| 링 버퍼 리플레이 (라이브 버퍼 스크럽/재생/Loop) | **실제 동작** |
| 시뮬레이터 (드론 블롭, EDF 차폐 결손, 프로펠러 후류) | **목업** — 실장비 대신 시나리오 생성 |
| nanoDAQ(TCP) 소스 | **실제 동작** — `--ip`로 실행 (Setup 탭의 소스 버튼은 아직 비활성) |
| 파일 재생 소스 | **목업** — 컨셉 표시용 비활성 버튼 |
| Zero All / 품질 게이트 | 흐름은 실제, 영점 오프셋은 시뮬레이터가 주입한 가상값 |
| 성적서 PNG 스냅샷 (`reports/`) | **실제 저장** |
| 성적서 PDF | **미구현** (P1 — reportlab 예정) |
| 왜곡 지수·스로틀 % 등 일부 수치 | 표시용 스케일링/정적 목업 포함 |

## 회의 시연 순서 제안

1. **품질 게이트 스토리** — Setup 탭: CH03/CH07이 드리프트(앰버)로 걸려 14/16,
   `Zero All` → "무풍·무압 상태입니까?" → 16/16 OK → `시연 시작` 활성화
2. **Downwash** — `드론 ON`: 압력 블롭이 배회, 피크 채널 표시, `기준 저장` + `Δ 표시`
3. **EDF** — `차폐 ON`: 90° 섹터 총압 결손, 왜곡 지수 상승 + 최악 60° 섹터 음영
4. **AeroBench** — RPM 슬라이더 2000→8000: 후류 프로파일이 이론 참조 곡선에 접근
5. **Replay** — 현재 세션 스크럽/재생, `Loop` = 무인 어트랙트 모드
6. **성적서** — 헤더의 `📄 성적서`: 요약 + PNG 스냅샷 저장 (PDF는 P1)

보조 기능: 스페이스바/`📍 순간 저장` = 이벤트 마커(하단 레코더 스트립에 앰버 라인),
`● REC`는 항상 링 버퍼에 기록 중임을 표시.

## 구조

```
workbench/
├── __main__.py     # 진입점 + --selftest
├── theme.py        # 다크 팔레트 + 앱 스타일시트
├── ring.py         # 스레드 안전 링 버퍼 (16ch × ~5분 @ 50 Hz)
├── sim.py          # SimSource: 50 Hz 시나리오 생성 스레드
├── nanodaq_source.py # NanoDAQSource: 실장비 TCP 스트림 → 링 버퍼 (SimSource 와 동일 인터페이스)
├── profiles.py     # 3개 데모 모드 채널 프로파일 (이름·기하)
├── widgets.py      # 공용 소형 위젯 (StatTile, Pill)
├── views/          # heatmap(Downwash) · polar(EDF) · wake(AeroBench)
└── main_window.py  # 셸: 헤더·3탭·푸터, 80 ms 타이머
```

뷰는 `update_frame(values, t)` 인터페이스만 노출하며 라이브/리플레이를 구분하지
않습니다. 데이터는 시뮬레이터 → 링 버퍼 → UI 타이머로만 흐릅니다.
