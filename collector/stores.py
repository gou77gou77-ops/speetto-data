"""전국 판매점 → stores.json (docs/data-contract.md, docs/data-source.md 5절).

실행:
    python -m collector.stores --out out/stores.json            # 전국 (약 1,448번 요청. 1초 간격 약 25분, 3초 간격 약 75분)
    python -m collector.stores --sido 서울 --sigungu 서초구       # 시군구 하나만 시험 → out/stores.sample.json

끝까지 다 받고 형식 검사를 통과했을 때만 파일을 바꾼다. 실패하면 기존 파일을 그대로 두고 0이 아닌 값으로 끝난다.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Any

from .contract import validate_stores
from .dhlottery import BlockedError, Client, CollectError, FormatChangedError, user_agent_summary
from .geo import valid_coord
from .juso import Geocoder
from .output import elapsed, gh_error, gh_warning, log, now_kst, read_json, write_json_atomic

PATH = "/prchsplcsrch/selectLtShp.do"
PAGE_SIZE = 10  # 서버가 10으로 고정
COMPLETE_RATIO = 0.995  # 수집 중 목록이 조금 바뀌는 것만 허용
SHRINK_LIMIT = 0.9  # 지난 파일보다 10% 넘게 줄면 이상으로 본다
PROGRESS_EVERY = 10  # 쪽마다 진행 로그
PRODUCT_FLAGS = (("SP2000", "st20LtNtslYn"), ("SP1000", "st10LtNtslYn"), ("SP500", "st5LtNtslYn"))


def _params(page: int, sido: str, sigungu: str) -> dict[str, Any]:
    return {
        "l645LtNtslYn": "N", "l520LtNtslYn": "N", "st5LtNtslYn": "N", "st10LtNtslYn": "N",
        "st20LtNtslYn": "N", "cpexUsePsbltyYn": "N", "pageNum": page,
        "recordCountPerPage": PAGE_SIZE, "pageCount": 5, "srchCtpvNm": sido, "srchSggNm": sigungu,
    }


def _page(client: Client, page: int, sido: str, sigungu: str) -> tuple[int, list[dict[str, Any]]]:
    doc = client.get_json(PATH, _params(page, sido, sigungu))
    data = doc.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("list"), list) or not isinstance(data.get("total"), int):
        raise FormatChangedError(f"판매점 {page}쪽: data.total / data.list가 없음")
    return data["total"], data["list"]


def fetch_all(client: Client, sido: str = "", sigungu: str = "", log=log,
              clock=time.monotonic) -> list[dict[str, Any]]:
    start = clock()
    page, pages = 1, "?"
    try:
        total, rows = _page(client, 1, sido, sigungu)
        pages = math.ceil(total / PAGE_SIZE)
        interval = getattr(client, "min_interval", 0)
        log(f"판매점 total {total}, {pages}쪽, 요청 간격 {interval:g}초"
            + (f", 예상 약 {math.ceil(pages * interval / 60)}분" if interval else ""))
        for page in range(2, pages + 1):
            _, more = _page(client, page, sido, sigungu)
            if not more:
                raise CollectError(f"{page}/{pages}쪽이 비어 있음. 목록이 끝까지 오지 않았다")
            rows.extend(more)
            if page % PROGRESS_EVERY == 0 or page == pages:
                log(f"  {page}/{pages}쪽, 경과 {elapsed(clock() - start)}")
    except CollectError as e:
        where = f"{page}/{pages}쪽, {client.request_count}번째 요청, 시작 {elapsed(clock() - start)} 뒤"
        log(f"실패 지점: {where}")
        e.args = (f"{where}: {e.args[0] if e.args else e}",) + e.args[1:]
        raise
    unique = {str(r.get("ltShpId")): r for r in rows if r.get("ltShpId")}
    if len(unique) < total * COMPLETE_RATIO:
        raise CollectError(f"판매점 {len(unique)}곳만 받음 (total {total}). 끝까지 받지 못했다")
    return list(unique.values())


def _text(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _address(r: dict[str, Any]) -> str:
    road = _text(r.get("bplcRdnmDaddr"))
    if road:
        return road
    parts = [r.get(k) for k in ("tm1BplcLctnAddr", "tm2BplcLctnAddr", "tm3BplcLctnAddr", "tm4BplcLctnAddr", "bplcLctnDaddr")]
    return " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def build_store(r: dict[str, Any], geocoder: Geocoder | None) -> dict[str, Any] | None:
    """스피또를 하나도 안 파는 곳은 None."""
    products = [code for code, flag in PRODUCT_FLAGS if r.get(flag) == "Y"]
    if not products:
        return None
    name = _text(r.get("conmNm"))
    if not name:
        raise FormatChangedError(f"판매점 {r.get('ltShpId')}: 상호(conmNm)가 없음")
    address = _address(r)
    lat, lng, source = r.get("shpLat"), r.get("shpLot"), "dhlottery"
    if not valid_coord(lat, lng):
        lat = lng = source = None
        if geocoder:
            hit = geocoder.locate(address)
            if hit:
                (lat, lng), source = hit, "juso"
    return {
        "id": str(r["ltShpId"]),
        "name": name,
        "address": address,
        "tel": _text(r.get("shpTelno")),
        "lat": lat,
        "lng": lng,
        "coordSource": source,
        "products": products,
    }


def build_doc(rows: list[dict[str, Any]], geocoder: Geocoder | None) -> dict[str, Any]:
    stores = [s for s in (build_store(r, geocoder) for r in rows) if s]
    stores.sort(key=lambda s: s["id"])
    doc = {"schemaVersion": 1, "generatedAt": now_kst(), "total": len(stores), "stores": stores}
    validate_stores(doc)
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="전국 판매점 → stores.json")
    ap.add_argument("--out", help="출력 파일 (기본: out/stores.json, 시군구 시험은 out/stores.sample.json)")
    ap.add_argument("--sido", default="", help="시험용: 시도 약칭 (서울, 경기 …)")
    ap.add_argument("--sigungu", default="", help="시험용: 시군구 (서초구 …). --sido와 같이 쓴다")
    args = ap.parse_args(argv)

    sample = bool(args.sido or args.sigungu)
    if args.sigungu and not args.sido:
        ap.error("--sigungu는 --sido와 같이 쓴다")
    out = Path(args.out or ("out/stores.sample.json" if sample else "out/stores.json"))
    if sample and out.name == "stores.json":
        ap.error("시군구 시험 결과로 stores.json을 덮어쓰지 않는다. --out을 다른 이름으로 준다")

    geocoder = Geocoder.from_env()
    if geocoder is None:
        gh_warning("지오코딩 건너뜀", "JUSO_API_KEY가 없어 좌표 없는 판매점은 좌표 null로 둡니다")

    client = Client()
    log(f"User-Agent: {user_agent_summary()}, 요청 간격 {client.min_interval:g}초")
    try:
        rows = fetch_all(client, args.sido, args.sigungu)
        doc = build_doc(rows, geocoder)
        if not sample:
            prev = read_json(out)
            if prev and doc["total"] < prev.get("total", 0) * SHRINK_LIMIT:
                raise CollectError(f"판매점이 {prev['total']} → {doc['total']}곳으로 10% 넘게 줄었다. 확인 필요")
    except BlockedError as e:
        gh_error("접속 차단", f"판매점 수집 중 동행복권 접속 실패: {e}. {out}은 바꾸지 않았습니다")
        return 2
    except (CollectError, ValueError) as e:
        gh_error("판매점 수집 실패", f"{e}. {out}은 바꾸지 않았습니다")
        return 3

    write_json_atomic(out, doc)
    no_coord = sum(1 for s in doc["stores"] if s["lat"] is None)
    juso = sum(1 for s in doc["stores"] if s["coordSource"] == "juso")
    log(f"저장: {out} — 스피또 판매점 {doc['total']}곳 (받은 판매점 {len(rows)}곳), "
          f"행안부 좌표 {juso}곳, 좌표 없음 {no_coord}곳, 요청 {client.request_count}번")
    return 0


if __name__ == "__main__":
    sys.exit(main())
