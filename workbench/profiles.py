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
# 16 프로브 = 8 스포크 × 2 링 (총압 레이크), 반경은 덕트 반경 R 정규화값
EDF_RINGS = (0.55, 0.85)
_edf_positions = []
_edf_theta = []
_edf_channels = []
for _k in range(8):
    _th = 45.0 * _k
    for _ri, _rr in enumerate(EDF_RINGS):
        _edf_theta.append(_th)
        _edf_positions.append((_rr * np.cos(np.radians(_th)),
                               _rr * np.sin(np.radians(_th))))
        _edf_channels.append({
            "name": f"총압 {_th:.0f}° {'내측' if _ri == 0 else '외측'}",
            "pos": f"r/R {_rr:.2f} · {_th:.0f}°",
            "spare": False,
        })

EDF = {
    "key": "edf",
    "icon": "\U0001F535",           # 🔵
    "title": "EDF 인렛 왜곡",
    "subtitle": "덕트팬 입구 총압 · 8스포크 × 2링",
    "positions": np.array(_edf_positions),  # (16, 2) 정규화 좌표
    "theta_deg": np.array(_edf_theta),      # (16,)
    "channels": _edf_channels,
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
# 표시 순서는 앞전 → 윗면(앞→뒤) → 아랫면(뒤→앞)으로 단면을 한 바퀴 도는 순서다
# (실제 Cp 그림처럼 한 선으로 쭉 이어지게). x/c 는 설계값(Disc #42)이며, 실제
# 출력물 실측치는 아니다.
AIRFOIL_TAPS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 14, 13, 12, 11, 10]  # 0-based 채널 인덱스
AIRFOIL_TAP_LABELS = ["앞전", "윗.03", "윗.06", "윗.10", "윗.15", "윗.20",
                      "윗.30", "윗.40", "윗.50", "윗.62",
                      "아.45", "아.32", "아.20", "아.12", "아.06"]
AIRFOIL_X_OVER_C_NOMINAL = [0.00, 0.03, 0.06, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.62,
                            0.45, 0.32, 0.20, 0.12, 0.06]   # 설계값(Disc #42) — 실측 아님

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
        _af_channels.append({"name": "미사용(CH16, 구멍 없음)", "pos": "—", "spare": True})

AIRFOIL = {
    "key": "airfoil",
    "icon": "\U0001F6E9",          # 🛩
    "title": "익형 표면압력",
    "subtitle": "받음각별 Cp 분포 · 표면탭 15점 (x/c 는 설계값)",
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


def airfoil_cp(s, s_stag: float):
    """탭 위치 s(0..1, 앞전→뒤)에서의 Cp — 목업용 형상 함수.

    정체점 s_stag 에서 Cp=1 이고, 뒤로 갈수록 유동이 가속해 흡입으로 떨어졌다가
    후연을 향해 압력이 회복된다. 실제 익형 해석이 아니라 데모용 곡선이다.
    """
    s = np.asarray(s, dtype=float)
    d = s - s_stag
    # 정체점에서 멀어질수록 흡입이 붙는다 (앞뒤 모두).
    suction = 2.05 * (1.0 - np.exp(-(d / 0.16) ** 2))
    # 후연을 향한 압력 회복.
    recovery = np.clip((s - s_stag - 0.12) / 0.85, 0.0, 1.0) ** 0.8
    return 1.0 - suction * (1.0 - 0.72 * recovery)


def theory_profile(n: int = 120, q_ref: float = 520.0):
    """운동량이론풍 이론 참조 곡선 (정적 배열). (velocity m/s, r/R) 반환."""
    x = np.linspace(0.0, 1.1, n)
    g = np.exp(-((x - 0.75) ** 2) / (2 * 0.20 ** 2))
    hub = np.clip((x - 0.10) / 0.35, 0.0, 1.0)
    tip = 1.0 / (1.0 + np.exp((x - 1.02) / 0.05))
    q = q_ref * g * hub * tip
    v = np.sqrt(2.0 * q / RHO)
    return v, x
