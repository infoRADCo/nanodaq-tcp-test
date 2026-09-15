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

PROFILES = {p["key"]: p for p in (DOWNWASH, EDF, AEROBENCH)}
MODE_ORDER = ["downwash", "edf", "aerobench"]


def wake_shape(x):
    """프로펠러 후류 동압 형상 (0…1 정규화). 피크 r/R≈0.78, 허브·팁 밖 ≈0."""
    x = np.asarray(x, dtype=float)
    g = np.exp(-((x - 0.78) ** 2) / (2 * 0.16 ** 2))
    hub = np.clip((x - 0.06) / 0.30, 0.0, 1.0) ** 1.5
    tip = 1.0 / (1.0 + np.exp((x - 1.0) / 0.035))
    return g * hub * tip


def theory_profile(n: int = 120, q_ref: float = 520.0):
    """운동량이론풍 이론 참조 곡선 (정적 배열). (velocity m/s, r/R) 반환."""
    x = np.linspace(0.0, 1.1, n)
    g = np.exp(-((x - 0.75) ** 2) / (2 * 0.20 ** 2))
    hub = np.clip((x - 0.10) / 0.35, 0.0, 1.0)
    tip = 1.0 / (1.0 + np.exp((x - 1.02) / 0.05))
    q = q_ref * g * hub * tip
    v = np.sqrt(2.0 * q / RHO)
    return v, x
