"""3개 데모 모드의 채널 프로파일 (이름 · 기하 · 예비채널)."""

import numpy as np

RHO = 1.204  # 공기 밀도 kg/m^3 (15 °C 부근)


# ---------------------------------------------------------------- Downwash
# 방사형 16홀 — PVC 500×500×5T 플레이트, 2026-10-06 확정 (휠베이스 245mm 기준).
# 이전의 4×4 격자는 8월 말 PoC 더미값이라 실제 설계와 달랐다 (이슈 #6).
#
# ⚠️ 아래 CH1~16 순서는 실제 배선 순서가 아니라 설계 도면 작성 순서를 그대로
# 따른 제안값이다. 실제 배선 시 이 순서대로 연결하거나, 배선 순서에 맞춰
# 이 배열을 다시 정렬해야 한다.
_dw_positions = [
    (0.0, 0.0),          # CH1  center
    (42.4, 42.4),        # CH2  inner1
    (42.4, -42.4),       # CH3  inner2
    (-42.4, 42.4),       # CH4  inner3
    (-42.4, -42.4),      # CH5  inner4
    (86.6, 86.6),        # CH6  motor1
    (86.6, -86.6),       # CH7  motor2
    (-86.6, 86.6),       # CH8  motor3
    (-86.6, -86.6),      # CH9  motor4
    (131.5, 131.5),      # CH10 tip1
    (131.5, -131.5),     # CH11 tip2
    (-131.5, 131.5),     # CH12 tip3
    (-131.5, -131.5),    # CH13 tip4
    (0.0, 122.5),        # CH14 btw +Y
    (0.0, -122.5),       # CH15 btw -Y
    (-122.5, 0.0),       # CH16 btw -X
]
_dw_names = ["중앙", "내측1", "내측2", "내측3", "내측4",
            "모터1", "모터2", "모터3", "모터4",
            "팁1", "팁2", "팁3", "팁4",
            "간섭 +Y", "간섭 -Y", "간섭 -X"]
_dw_channels = [
    {"name": _dw_names[_i], "pos": f"({_x:+.1f}, {_y:+.1f}) mm", "spare": False}
    for _i, (_x, _y) in enumerate(_dw_positions)
]

DOWNWASH = {
    "key": "downwash",
    "icon": "\U0001F300",           # 🌀
    "title": "Downwash 압력 맵",
    "subtitle": "드론 하방 유동 · 방사형 16홀 (휠베이스 245mm 기준)",
    "positions": np.array(_dw_positions),   # (16, 2) mm
    "channels": _dw_channels,
}


# ---------------------------------------------------------------- EDF annulus
# 8개 단일 링(45도 간격), 덕트 내경 95mm — 팬 마운트 스트럿(90°/270°)을 피하려고
# 전체 배치를 22.5도 회전함. 각도는 "맨 위 0°, 시계방향" 기준(infoRADCo/daq
# 이슈 #34, 2026-10-21 확정). CH9~16은 배관 안 함(spare).
#
# 각도 → (x,y) 변환은 나침반식(0°=위, 시계방향)이라 일반 수학 각도(0°=+X,
# 반시계)와 다름: x = R·sin(θ), y = R·cos(θ).
EDF_TAPS = [0, 1, 2, 3, 4, 5, 6, 7]                      # 0-based 채널 인덱스 (CH1~8)
EDF_THETA_DEG = [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
_EDF_R = 0.95  # 덕트 벽(r/R=1.0) 바로 안쪽 — 탭이 벽면에 붙어있음

_edf_positions = [
    (_EDF_R * np.sin(np.radians(_th)), _EDF_R * np.cos(np.radians(_th)))
    for _th in EDF_THETA_DEG
]
_edf_tap_channels = [
    {"name": f"총압 {_th:.1f}°", "pos": f"r/R {_EDF_R:.2f} · {_th:.1f}°", "spare": False}
    for _th in EDF_THETA_DEG
]
_edf_channels = [
    _edf_tap_channels[EDF_TAPS.index(_i)] if _i in EDF_TAPS
    else {"name": "미사용", "pos": "—", "spare": True}
    for _i in range(16)
]

EDF = {
    "key": "edf",
    "icon": "\U0001F535",           # 🔵
    "title": "EDF 인렛 왜곡",
    "subtitle": "덕트팬 입구 총압 · 8점 단일 링(45° 간격, 22.5° 회전)",
    "tap_index": EDF_TAPS,
    "positions": np.array(_edf_positions),  # (8, 2) 정규화 좌표
    "theta_deg": np.array(EDF_THETA_DEG),   # (8,)
    "channels": _edf_channels,              # (16,) Setup 테이블용 — spare 포함
}


# ---------------------------------------------------------------- AeroBench
# 13 반경 프로브 r/R = 0.0 … 1.1 + 정압기준/자유류기준/예비
AERO_R = np.linspace(0.0, 1.1, 13)
_ab_channels = [{"name": f"후류 r/R {_r:.2f}", "pos": f"r/R {_r:.2f}", "spare": False}
                for _r in AERO_R]
_ab_channels.append({"name": "정압 기준", "pos": "터널 벽면", "spare": False})
_ab_channels.append({"name": "자유류 기준", "pos": "피토 정압관", "spare": False})
_ab_channels.append({"name": "예비", "pos": "—", "spare": True})

AEROBENCH = {
    "key": "aerobench",
    "icon": "\U0001F4C8",           # 📈
    "title": "AeroBench 후류 프로파일",
    "subtitle": "프로펠러 후류 · 13점 반경 레이크",
    "r_over_R": AERO_R,
    "channels": _ab_channels,
}

# ---------------------------------------------------------------- Airfoil taps
# 익형 표면 정압 탭. v0.4 NACA4412(시위 65mm) 정식 배치 — Disc #42(2026-09-15)
# 채널 배치표 기준: CH1 앞전 · CH2~10 윗면 x/c .03/.06/.10/.15/.20/.30/.40/.50/.62
# · CH11~15 아랫면 x/c .06/.12/.20/.32/.45. CH16(아랫면 x/c .60)은 구멍이 없어
# 실제로 뚫려있지 않고(자유 피토 프로브로 임시 전용 중) spare 로 둔다.
#
# CH2·CH8·CH15 는 2026-10-26 기준 결선 제외 — daq#45 10/14 분석에서 CH8은
# nanoDAQ·microDAQ3 두 장비에 걸쳐 재현된 확정 하드웨어 결함, CH2는 느린 영점
# 복귀(튜빙 결함)로 확정. CH15는 10/22 로그에서 새로 의심되는 독립 드리프트
# 발견(물리 점검 전) — 세 채널 다 현장에서 연결 자체를 해제한 상태라 여기서도
# 그래프 대상에서 뺀다.
#
# 표시 순서는 앞전 → 윗면(앞→뒤) → 아랫면(뒤→앞)으로 단면을 한 바퀴 도는 순서다
# (실제 Cp 그림처럼 한 선으로 쭉 이어지게). x/c 는 설계값(Disc #42)이며, 실제
# 출력물 실측치는 아니다.
AIRFOIL_TAPS = [0, 2, 3, 4, 5, 6, 8, 9, 13, 12, 11, 10]  # 0-based 채널 인덱스 (CH2·8·15 제외)
AIRFOIL_TAP_LABELS = ["앞전", "윗.06", "윗.10", "윗.15", "윗.20", "윗.30",
                      "윗.50", "윗.62",
                      "아.32", "아.20", "아.12", "아.06"]
AIRFOIL_X_OVER_C_NOMINAL = [0.00, 0.06, 0.10, 0.15, 0.20, 0.30, 0.50, 0.62,
                            0.32, 0.20, 0.12, 0.06]   # 설계값(Disc #42) — 실측 아님

# 0-based 채널 인덱스 → 제외 사유 (Setup 탭 게이트 테이블 표시용)
AIRFOIL_EXCLUDED_REASON = {
    1: "CH2 제외 — 느린 영점 복귀(튜빙 결함, daq#45)",
    7: "CH8 제외 — 하드웨어 결함 확정(2개 DAQ 재현, daq#45)",
    14: "CH15 제외 — 드리프트 의심, 점검 전(10/22 로그)",
    15: "미사용(CH16, 구멍 없음)",
}

_af_channels = []
for _i in range(16):
    if _i in AIRFOIL_TAPS:
        _k = AIRFOIL_TAPS.index(_i)
        _af_channels.append({
            "name": f"표면탭 {AIRFOIL_TAP_LABELS[_k]}",
            "pos": f"x/c ~{AIRFOIL_X_OVER_C_NOMINAL[_k]:.2f} (설계값)",
            "spare": False,
        })
    else:
        _af_channels.append({"name": AIRFOIL_EXCLUDED_REASON[_i], "pos": "—", "spare": True})

AIRFOIL = {
    "key": "airfoil",
    "icon": "\U0001F6E9",          # 🛩
    "title": "익형 표면압력",
    "subtitle": "받음각별 Cp 분포 · 표면탭 12점 (CH2·8·15 결함 제외, x/c 는 설계값)",
    "tap_index": AIRFOIL_TAPS,
    "tap_labels": AIRFOIL_TAP_LABELS,
    "x_over_c_nominal": AIRFOIL_X_OVER_C_NOMINAL,
    "channels": _af_channels,
}


PROFILES = {p["key"]: p for p in (DOWNWASH, EDF, AEROBENCH, AIRFOIL)}
MODE_ORDER = ["downwash", "edf", "aerobench", "airfoil"]


def wake_shape(x):
    """프로펠러 후류 동압 형상 (0…1 정규화). 피크 r/R≈0.78, 허브·팁 밖 ≈0."""
    x = np.asarray(x, dtype=float)
    g = np.exp(-((x - 0.78) ** 2) / (2 * 0.16 ** 2))
    hub = np.clip((x - 0.06) / 0.30, 0.0, 1.0) ** 1.5
    tip = 1.0 / (1.0 + np.exp((x - 1.0) / 0.035))
    return g * hub * tip


def airfoil_cp(alpha_deg: float):
    """받음각 alpha 에서 AIRFOIL_TAPS 순서대로의 Cp — 목업용 형상 함수.

    CpView 가 앞전 탭(CH1)을 q 기준으로 쓰므로 앞전은 항상 정체점 근처(Cp≈1)로
    둔다. 받음각은 윗면 흡입·아랫면 양압의 차이(얇은 익형 이론풍
    √((1-x)/x) 하중)로 나타난다. 실제 익형 해석이 아니라 데모용 곡선이다.
    """
    x = np.asarray(AIRFOIL_X_OVER_C_NOMINAL, dtype=float)
    a = np.radians(alpha_deg + 4.0)  # NACA4412 무양력각 ≈ −4°
    load = a * np.sqrt((1.0 - x) / np.maximum(x, 0.01))  # 앞전 x=0 은 아래에서 덮어씀
    upper = np.array([lbl.startswith("윗") for lbl in AIRFOIL_TAP_LABELS])
    cp = np.where(upper, -0.2 - load, -0.2 + load)   # -0.2 = 두께로 인한 기본 흡입
    cp[0] = np.cos(np.radians(alpha_deg)) ** 2       # 앞전: 받음각이 크면 정체점이 살짝 비켜남
    # CpView 는 q=CH1 로 다시 나누므로 큰 받음각에서 값이 커진다 — 그래프 범위(-1.6) 안에 들게 여유를 둠
    return np.clip(cp, -1.45, 1.0)


def theory_profile(n: int = 120, q_ref: float = 520.0):
    """운동량이론풍 이론 참조 곡선 (정적 배열). (velocity m/s, r/R) 반환."""
    x = np.linspace(0.0, 1.1, n)
    g = np.exp(-((x - 0.75) ** 2) / (2 * 0.20 ** 2))
    hub = np.clip((x - 0.10) / 0.35, 0.0, 1.0)
    tip = 1.0 / (1.0 + np.exp((x - 1.02) / 0.05))
    q = q_ref * g * hub * tip
    v = np.sqrt(2.0 * q / RHO)
    return v, x
