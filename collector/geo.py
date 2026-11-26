"""좌표 검사, 거리, 주소 비교, UTM-K → WGS84 변환 (표준 라이브러리만 사용)."""

from __future__ import annotations

import math
import re

KOREA_LAT = (33.0, 39.0)
KOREA_LNG = (124.0, 132.0)


def valid_coord(lat: object, lng: object) -> bool:
    """좌표가 숫자이고 한국 범위 안이면 True."""
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return False
    return KOREA_LAT[0] <= lat <= KOREA_LAT[1] and KOREA_LNG[0] <= lng <= KOREA_LNG[1]


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_ROAD_RE = re.compile(r"^\S+(로|길)$")
_BUILDING_NO_RE = re.compile(r"^\d+(-\d+)?$")


def road_key(address: str | None) -> str | None:
    """도로명 주소에서 '시군구 … 도로명' 부분만 뽑는다. 시도 표기 차이(서울/서울특별시)는 비교하지 않는다.

    '서울 서초구 바우뫼로7길 18 1층' → '서초구 바우뫼로7길'
    '경기 수원시 장안구 대평로 86 1층 116호' → '수원시 장안구 대평로'
    """
    tokens = (address or "").split()
    for i, t in enumerate(tokens):
        if i >= 1 and _ROAD_RE.match(t):
            return " ".join(tokens[1 : i + 1])
    return None


def road_address_core(address: str | None) -> str | None:
    """상세 주소(층·호)를 뺀 '시도 시군구 도로명 건물번호'. 지오코딩 검색어로 쓴다."""
    tokens = (address or "").split()
    for i, t in enumerate(tokens):
        if i >= 1 and _ROAD_RE.match(t):
            end = i + 2 if i + 1 < len(tokens) and _BUILDING_NO_RE.match(tokens[i + 1]) else i + 1
            return " ".join(tokens[:end])
    return None


# UTM-K (EPSG:5179): GRS80, 원점 위도 38°, 중앙 경선 127.5°, 축척 0.9996, 가산 (1,000,000, 2,000,000)
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_K0 = 0.9996
_LAT0 = math.radians(38.0)
_LON0 = math.radians(127.5)
_FE = 1_000_000.0
_FN = 2_000_000.0


def _meridian_arc(phi: float) -> float:
    e2, e4, e6 = _E2, _E2**2, _E2**3
    return _A * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
        - (35 * e6 / 3072) * math.sin(6 * phi)
    )


_M0 = _meridian_arc(_LAT0)


def wgs84_to_utmk(lat: float, lng: float) -> tuple[float, float]:
    """(x, y) = (동향, 북향). 테스트와 검산용."""
    phi, lam = math.radians(lat), math.radians(lng)
    n = _A / math.sqrt(1 - _E2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = _EP2 * math.cos(phi) ** 2
    a = (lam - _LON0) * math.cos(phi)
    m = _meridian_arc(phi)
    x = _FE + _K0 * n * (
        a + (1 - t + c) * a**3 / 6 + (5 - 18 * t + t**2 + 72 * c - 58 * _EP2) * a**5 / 120
    )
    y = _FN + _K0 * (
        m
        - _M0
        + n
        * math.tan(phi)
        * (a**2 / 2 + (5 - t + 9 * c + 4 * c**2) * a**4 / 24 + (61 - 58 * t + t**2 + 600 * c - 330 * _EP2) * a**6 / 720)
    )
    return x, y


def utmk_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """행안부 좌표제공 API의 entX(동향), entY(북향)를 (위도, 경도)로 바꾼다."""
    m = _M0 + (y - _FN) / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = _EP2 * math.cos(phi1) ** 2
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = (x - _FE) / (n1 * _K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * _EP2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * _EP2 - 3 * c1**2) * d**6 / 720
    )
    lng = _LON0 + (
        d - (1 + 2 * t1 + c1) * d**3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * _EP2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lng)
