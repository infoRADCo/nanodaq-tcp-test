"""3개 데모 모드의 채널 프로파일 (이름 · 기하 · 예비채널)."""

import numpy as np

RHO = 1.204  # 공기 밀도 kg/m^3 (15 °C 부근)


# ---------------------------------------------------------------- Downwash
# 4×4 그리드, x·y ∈ {−225, −75, 75, 225} mm
_GRID_COORDS = (-225.0, -75.0, 75.0, 225.0)
_dw_positions = []
_dw_channels = []
for _r, _y in enumerate(reversed(_GRID_COORDS)):        # 위(+y)부터
    for _c, _x in enumerate(_GRID_COORDS):
        _dw_positions.append((_x, _y))
        _dw_channels.append({
            "name": f"그리드 {chr(65 + _r)}{_c + 1}",
            "pos": f"({_x:+.0f}, {_y:+.0f}) mm",
            "spare": False,
        })

DOWNWASH = {
    "key": "downwash",
    "icon": "\U0001F300",           # 🌀
    "title": "Downwash 압력 맵",
    "subtitle": "드론 하방 유동 · 4×4 그리드",
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
# 익형 표면 정압 탭. 2026-09-10 리그 배선 그대로: 앞전부터 CH1..CH5 가 표면을
# 따라 늘어서고, CH10 이 한참 뒤에 하나 있다. 나머지 10 채널은 배관하지 않았고
# spare 로 두어 품질 게이트·평균에서 빠지게 한다.
#
# 탭의 실제 x/c 는 아직 실측하지 않았다. 아래 값은 도면 없이 사진에서 눈대중한
# **공칭값**이며 계산에 쓰이지 않는다 — 뷰는 탭 순서만 쓴다. 실측하면 여기를
# 채우고 CpView 의 X 축을 x/c 로 바꾸면 진짜 코드방향 분포가 된다.
AIRFOIL_TAPS = [0, 1, 2, 3, 4, 9]                      # 0-based 채널 인덱스
AIRFOIL_TAP_LABELS = ["CH1", "CH2", "CH3", "CH4", "CH5", "CH10"]
AIRFOIL_X_OVER_C_NOMINAL = [0.02, 0.08, 0.16, 0.26, 0.38, 0.85]   # 공칭 — 실측 아님

_af_channels = []
for _i in range(16):
    if _i in AIRFOIL_TAPS:
        _k = AIRFOIL_TAPS.index(_i)
        _af_channels.append({
            "name": f"표면탭 {_k + 1}",
            "pos": f"x/c ~{AIRFOIL_X_OVER_C_NOMINAL[_k]:.2f} (공칭)",
            "spare": False,
        })
    else:
        _af_channels.append({"name": "미사용", "pos": "—", "spare": True})

AIRFOIL = {
    "key": "airfoil",
    "icon": "\U0001F6E9",          # 🛩
    "title": "익형 표면압력",
    "subtitle": "받음각별 Cp 분포 · 표면탭 6점 (x/c 는 공칭)",
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
