"""행안부 도로명주소 API 지오코딩 (좌표가 비었거나 이상한 곳만).

1. 검색 API(addrLinkApi.do)로 주소 → 행정구역코드·도로명코드·건물번호
2. 좌표제공 API(addrCoordApi.do)로 → entX, entY (UTM-K, EPSG:5179) → WGS84

키: 환경 변수 JUSO_API_KEY (검색). 좌표제공 API 키가 따로면 JUSO_COORD_API_KEY, 없으면 JUSO_API_KEY를 같이 쓴다.
키가 없으면 Geocoder.from_env()가 None을 돌려주고, 호출하는 쪽은 지오코딩만 건너뛴다.
실제 키로는 아직 시험하지 않았다(docs/data-source.md 7절).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from .geo import road_address_core, utmk_to_wgs84, valid_coord

SEARCH_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
COORD_URL = "https://business.juso.go.kr/addrlink/addrCoordApi.do"


class Geocoder:
    def __init__(
        self,
        search_key: str,
        coord_key: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        interval: float = 0.3,
    ) -> None:
        self._search_key = search_key
        self._coord_key = coord_key
        self._open = opener
        self._sleep = sleep
        self._interval = interval
        self.found = 0
        self.missed = 0
        self.errors = 0

    @classmethod
    def from_env(cls) -> "Geocoder | None":
        key = os.environ.get("JUSO_API_KEY", "").strip()
        if not key:
            return None
        coord = os.environ.get("JUSO_COORD_API_KEY", "").strip() or key
        return cls(key, coord)

    def _call(self, url: str, params: dict[str, str]) -> dict[str, Any] | None:
        self._sleep(self._interval)
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}")
        with self._open(req, timeout=15) as res:
            doc = json.loads(res.read().decode("utf-8"))
        results = doc.get("results") or {}
        common = results.get("common") or {}
        if str(common.get("errorCode")) != "0":
            # 키 값이 메시지에 섞여 나오지 않도록 코드와 메시지만 남긴다.
            raise RuntimeError(f"juso {common.get('errorCode')}: {common.get('errorMessage')}")
        rows = results.get("juso") or []
        return rows[0] if rows else None

    def locate(self, address: str | None) -> tuple[float, float] | None:
        """도로명 주소 → (위도, 경도). 못 찾으면 None. 오류도 None(로그에 숫자만 남김)."""
        if self.errors >= 5 and self.found == 0:
            return None  # 키 오류 등으로 계속 실패하면 더 부르지 않는다
        keyword = road_address_core(address)
        if not keyword:
            self.missed += 1
            return None
        try:
            hit = self._call(
                SEARCH_URL,
                {"confmKey": self._search_key, "keyword": keyword, "currentPage": "1",
                 "countPerPage": "1", "resultType": "json"},
            )
            if not hit:
                self.missed += 1
                return None
            coord = self._call(
                COORD_URL,
                {"confmKey": self._coord_key, "admCd": hit["admCd"], "rnMgtSn": hit["rnMgtSn"],
                 "udrtYn": hit["udrtYn"], "buldMnnm": str(hit["buldMnnm"]),
                 "buldSlno": str(hit["buldSlno"]), "resultType": "json"},
            )
            if not coord or not coord.get("entX") or not coord.get("entY"):
                self.missed += 1
                return None
            lat, lng = utmk_to_wgs84(float(coord["entX"]), float(coord["entY"]))
        except Exception as e:  # noqa: BLE001 — 지오코딩 실패는 그 판매점만 좌표 null로 둔다
            self.errors += 1
            print(f"지오코딩 오류: {type(e).__name__}: {e}")
            return None
        if not valid_coord(lat, lng):
            self.missed += 1
            return None
        self.found += 1
        return round(lat, 6), round(lng, 6)
