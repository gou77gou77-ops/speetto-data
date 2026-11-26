"""회차 목록 + 상세 + 회차별 1등 배출점 → rounds_latest.json (docs/data-contract.md, docs/data-source.md 1·2·6절).

실행:
    python -m collector.rounds --stores out/stores.json --out out/rounds_latest.json

점수·상태·라벨은 scoring.py가 정한다. 1등 배출점은 --stores 판매점 목록과 짝을 짓는다.
모든 회차를 받고 형식 검사를 통과했을 때만 파일을 바꾼다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .contract import validate_rounds
from .dhlottery import BlockedError, Client, CollectError, FormatChangedError, user_agent_summary
from .geo import distance_m, road_key, valid_coord
from .juso import Geocoder
from .output import gh_error, gh_warning, log, now_kst, read_json, write_json_atomic
from .scoring import Prize, RoundInput, parse_amount, score_round

LIST_PATH = "/st/selectPblcnDsctn.do"
DETAIL_PATH = "/st/selectPblcnDsctnDtl.do"
WINNERS_PATH = "/wnprchsplcsrch/selectStWnShp.do"

PRODUCTS = {"SP2000": 2000, "SP1000": 1000, "SP500": 500}
WINNER_CODES = {"SP2000": "LP35", "SP1000": "LP34", "SP500": "LP33"}
ON_SALE = {"판매중": True, "판매종료": False}
MATCH_RADIUS_M = 30.0


# ---------- 날짜 ----------

def short_date(v: Any, where: str) -> str:
    """'26-11-20' → '2026-11-20'."""
    s = str(v or "")
    parts = s.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts) or len(parts[0]) != 2:
        raise FormatChangedError(f"{where}: 날짜 형식이 다름 {s!r}")
    return f"20{parts[0]}-{parts[1]}-{parts[2]}"


def full_date(v: Any, where: str) -> str:
    s = str(v or "")
    parts = s.split("-")
    if len(parts) != 3 or len(parts[0]) != 4 or not all(p.isdigit() for p in parts):
        raise FormatChangedError(f"{where}: 날짜 형식이 다름 {s!r}")
    return s


# ---------- 회차 ----------

def fetch_list(client: Client) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        doc = client.get_json(LIST_PATH, {"gdsType": "", "gdsPrice": "", "gdsStatus": "",
                                          "pageNum": page, "recordCountPerPage": 100})
        data = doc.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list) or not isinstance(data.get("total"), int):
            raise FormatChangedError("회차 목록: data.total / data.list가 없음")
        rows.extend(data["list"])
        if len(rows) >= data["total"] or not data["list"]:
            break
        page += 1
    if len(rows) != data["total"]:
        raise CollectError(f"회차 목록 {len(rows)}건만 받음 (total {data['total']})")
    if not rows:
        raise FormatChangedError("회차 목록이 비어 있음")
    return rows


def fetch_detail(client: Client, ntsl_wn_sn: int) -> dict[str, Any]:
    doc = client.get_json(DETAIL_PATH, {"ntslWnSn": ntsl_wn_sn})
    result = (doc.get("data") or {}).get("result")
    if not isinstance(result, dict):
        raise FormatChangedError(f"회차 상세 {ntsl_wn_sn}: data.result가 없음")
    return result


def _qty(d: dict[str, Any], key: str, where: str) -> int:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise FormatChangedError(f"{where}: {key} 값이 이상함 {v!r}")
    return v


def build_round(item: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    rid = item.get("ntslWnSn")
    where = f"회차 {item.get('stGmTypeCd')} {item.get('stEpsd')}회 ({rid})"
    product = item.get("stGmTypeCd")
    if product not in PRODUCTS:
        raise FormatChangedError(f"{where}: 모르는 상품 코드 {product!r}")
    status_text = detail.get("ntslStatus") or item.get("ntslStatus")
    if status_text not in ON_SALE:
        raise FormatChangedError(f"{where}: 모르는 판매 상태 {status_text!r}")
    price = detail.get("stNtslAmt")
    if price != PRODUCTS[product]:
        raise FormatChangedError(f"{where}: 가격 {price!r}이 상품과 다름")
    stock_rate = _qty(detail, "stSpmtRt", where)
    if stock_rate > 100:
        raise FormatChangedError(f"{where}: 입고율 {stock_rate}%")
    issued = _qty(detail, "pblcnQty", where)
    if issued == 0:
        raise FormatChangedError(f"{where}: 발행량 0")

    stock_date = short_date(detail.get("dataChgDt"), where)
    as_of = {1: detail.get("fstDt"), 2: detail.get("sndDt")}
    prizes = []
    for k in (1, 2):
        try:
            amount = parse_amount(detail.get(f"stRnk{k}GdsLstcCharCn"))
        except ValueError as e:
            raise FormatChangedError(f"{where}: {e}") from e
        total = _qty(detail, f"stRnk{k}WnQty", where)
        remaining = _qty(detail, f"stIvtRnk{k}Qty", where)
        if remaining > total:
            raise FormatChangedError(f"{where}: {k}등 잔여 {remaining} > 전체 {total}")
        prizes.append({
            "rank": k, "amount": amount, "total": total, "remaining": remaining,
            "asOf": short_date(as_of[k], where) if as_of[k] else stock_date,
        })

    scored = score_round(RoundInput(
        on_sale=ON_SALE[status_text], stock_rate=stock_rate, issued=issued, price=price,
        prizes=tuple(Prize(p["rank"], p["amount"], p["total"], p["remaining"]) for p in prizes),
    ))
    return {
        "id": rid,
        "product": product,
        "round": item.get("stEpsd"),
        "price": price,
        "status": scored.status,
        "stockRate": stock_rate,
        "stockDate": stock_date,
        "issued": issued,
        "prizes": prizes,
        "index": scored.index,
        "score": scored.score,
        "grade": scored.grade,
        "lowStock": scored.low_stock,
        "saleStartDate": full_date(detail.get("stNtslBgngDt"), where),
        "saleEndDate": full_date(detail.get("stNtslEndDt"), where),
        "payEndDate": full_date(detail.get("stGiveEndDt"), where),
        "winners": [],
    }


# ---------- 1등 배출점 ----------

def fetch_winners(client: Client, product: str, round_no: int) -> list[dict[str, Any]]:
    doc = client.get_json(WINNERS_PATH, {"srchLtGdsCd": WINNER_CODES[product], "srchLtEpsd": round_no,
                                         "srchWnShpRnk": 1, "srchShpLctn": ""})
    data = doc.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise FormatChangedError(f"1등 배출점 {product} {round_no}회: data.list가 없음")
    return [r for r in data["list"] if str(r.get("wnShpRnk")) == "1"]


class StoreIndex:
    """판매점 좌표를 약 100 m 칸으로 나눠 30 m 이내만 빨리 찾는다."""

    CELL = 0.001

    def __init__(self, stores: list[dict[str, Any]]) -> None:
        self._cells: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for s in stores:
            if s.get("lat") is not None:
                self._cells.setdefault(self._cell(s["lat"], s["lng"]), []).append(s)

    def _cell(self, lat: float, lng: float) -> tuple[int, int]:
        return int(lat // self.CELL), int(lng // self.CELL)

    def match(self, lat: float, lng: float, address: str | None) -> dict[str, Any] | None:
        key = road_key(address)
        if key is None:
            return None
        ci, cj = self._cell(lat, lng)
        best, best_d = None, MATCH_RADIUS_M
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for s in self._cells.get((ci + di, cj + dj), ()):
                    d = distance_m(lat, lng, s["lat"], s["lng"])
                    if d <= best_d and road_key(s["address"]) == key:
                        best, best_d = s, d
        return best


def build_winner(row: dict[str, Any], index: StoreIndex, geocoder: Geocoder | None) -> dict[str, Any]:
    address = (row.get("shpAddr") or row.get("rdnm") or "").strip()
    lat, lng = row.get("shpLat"), row.get("shpLot")
    if not valid_coord(lat, lng):
        lat = lng = None
        if geocoder:
            hit = geocoder.locate(row.get("rdnm") or address)
            if hit:
                lat, lng = hit
    store = index.match(lat, lng, row.get("rdnm") or address) if lat is not None else None
    if store:
        return {"storeId": store["id"], "name": store["name"], "address": store["address"],
                "lat": store["lat"], "lng": store["lng"], "inStoreList": True}
    name = (row.get("shpNm") or "").strip()
    if not name:
        raise FormatChangedError(f"1등 배출점 {row.get('ltShpId')}: 상호가 없음")
    return {"storeId": None, "name": name, "address": address, "lat": lat, "lng": lng, "inStoreList": False}


# ---------- 전체 ----------

def collect(client: Client, stores: list[dict[str, Any]], geocoder: Geocoder | None, log=log) -> dict[str, Any]:
    items = fetch_list(client)
    log(f"회차 {len(items)}개")
    rounds = [build_round(item, fetch_detail(client, item["ntslWnSn"])) for item in items]

    log(f"회차 상세 {len(rounds)}개 받음, 요청 {client.request_count}번")
    index = StoreIndex(stores)
    for r in rounds:
        r["winners"] = [build_winner(row, index, geocoder) for row in fetch_winners(client, r["product"], r["round"])]

    rounds.sort(key=lambda r: (list(PRODUCTS).index(r["product"]), -r["round"]))
    on_sale = [r["stockDate"] for r in rounds if r["status"] != "ended"]
    doc = {
        "schemaVersion": 1,
        "generatedAt": now_kst(),
        "stockDate": max(on_sale or [r["stockDate"] for r in rounds]),
        "rounds": rounds,
    }
    validate_rounds(doc)
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="회차 + 1등 배출점 → rounds_latest.json")
    ap.add_argument("--out", default="out/rounds_latest.json")
    ap.add_argument("--stores", default="out/stores.json", help="짝지을 판매점 목록 (공개된 stores.json)")
    args = ap.parse_args(argv)
    out = Path(args.out)

    stores_doc = read_json(args.stores)
    if not stores_doc or not stores_doc.get("stores"):
        gh_error("판매점 목록 없음",
                 f"{args.stores}가 없습니다. 1등 배출점을 짝지을 수 없어 수집하지 않습니다. "
                 f"stores 워크플로를 먼저 실행하세요. {out}은 바꾸지 않았습니다")
        return 4

    geocoder = Geocoder.from_env()
    if geocoder is None:
        gh_warning("지오코딩 건너뜀", "JUSO_API_KEY가 없어 좌표 없는 1등 배출점은 좌표 null로 둡니다")

    client = Client()
    log(f"User-Agent: {user_agent_summary()}, 요청 간격 {client.min_interval:g}초")
    try:
        doc = collect(client, stores_doc["stores"], geocoder)
    except BlockedError as e:
        gh_error("접속 차단", f"회차 수집 중 동행복권 접속 실패({client.request_count}번째 요청): {e}. {out}은 바꾸지 않았습니다")
        return 2
    except (CollectError, ValueError) as e:
        gh_error("회차 수집 실패", f"{e}. {out}은 바꾸지 않았습니다")
        return 3

    write_json_atomic(out, doc)
    winners = [w for r in doc["rounds"] for w in r["winners"]]
    log(f"저장: {out} — 회차 {len(doc['rounds'])}개, 1등 배출 {len(winners)}건 "
          f"(판매점 목록에 있음 {sum(w['inStoreList'] for w in winners)}, 없음 {sum(not w['inStoreList'] for w in winners)}, "
          f"좌표 없음 {sum(w['lat'] is None for w in winners)}), 기준 판매점 {stores_doc.get('total')}곳, "
          f"출고율 기준 {doc['stockDate']}, 요청 {client.request_count}번")
    return 0


if __name__ == "__main__":
    sys.exit(main())
