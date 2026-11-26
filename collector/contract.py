"""docs/data-contract.md 형식 검사. 파일을 쓰기 직전에 부르고, 어긋나면 쓰지 않는다."""

from __future__ import annotations

import re
from typing import Any

from .scoring import (
    GRADE_BAD,
    GRADE_GOOD,
    GRADE_NORMAL,
    GRADE_NOT_RECOMMENDED,
    GRADE_STRONG,
    STATUS_ACTIVE,
    STATUS_ENDED,
    STATUS_ENDGAME,
)

PRODUCTS = {"SP2000": 2000, "SP1000": 1000, "SP500": 500}
STATUSES = {STATUS_ACTIVE, STATUS_ENDGAME, STATUS_ENDED}
GRADES = {GRADE_STRONG, GRADE_GOOD, GRADE_NORMAL, GRADE_BAD, GRADE_NOT_RECOMMENDED, None}
COORD_SOURCES = {"dhlottery", "juso", None}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")


class ContractError(ValueError):
    pass


def _check(cond: bool, where: str, msg: str) -> None:
    if not cond:
        raise ContractError(f"{where}: {msg}")


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _coord_pair(where: str, lat: Any, lng: Any) -> None:
    _check((lat is None) == (lng is None), where, "lat/lng는 둘 다 있거나 둘 다 null")
    if lat is not None:
        _check(_num(lat) and 33 <= lat <= 39 and _num(lng) and 124 <= lng <= 132, where, "한국 범위 밖 좌표")


def validate_rounds(doc: dict[str, Any]) -> None:
    _check(doc.get("schemaVersion") == 1, "rounds", "schemaVersion은 1")
    _check(bool(_DATETIME.match(str(doc.get("generatedAt")))), "rounds", "generatedAt 형식")
    _check(bool(_DATE.match(str(doc.get("stockDate")))), "rounds", "stockDate 형식")
    rounds = doc.get("rounds")
    _check(isinstance(rounds, list) and len(rounds) > 0, "rounds", "회차가 없음")
    ids = set()
    for r in rounds:
        w = f"round {r.get('id')}"
        _check(_int(r.get("id")) and r["id"] not in ids, w, "id 중복 또는 정수 아님")
        ids.add(r["id"])
        _check(r.get("product") in PRODUCTS, w, "product")
        _check(r.get("price") == PRODUCTS[r["product"]], w, "price가 상품 가격과 다름")
        _check(_int(r.get("round")) and r["round"] > 0, w, "round")
        _check(r.get("status") in STATUSES, w, "status")
        _check(_int(r.get("stockRate")) and 0 <= r["stockRate"] <= 100, w, "stockRate")
        _check(_int(r.get("issued")) and r["issued"] > 0, w, "issued")
        for k in ("stockDate", "saleStartDate", "saleEndDate", "payEndDate"):
            _check(bool(_DATE.match(str(r.get(k)))), w, f"{k} 형식")
        prizes = r.get("prizes")
        _check(isinstance(prizes, list) and [p.get("rank") for p in prizes] == [1, 2], w, "prizes는 1·2등")
        for p in prizes:
            _check(_int(p.get("amount")) and p["amount"] > 0, w, "amount")
            _check(_int(p.get("total")) and _int(p.get("remaining")) and 0 <= p["remaining"] <= p["total"], w, "수량")
            _check(bool(_DATE.match(str(p.get("asOf")))), w, "asOf 형식")
        active = r["status"] == STATUS_ACTIVE
        _check((r.get("score") is not None) == active and (r.get("index") is not None) == active, w,
               "score/index는 active일 때만")
        if active:
            _check(_num(r["score"]) and 0 <= r["score"] <= 100, w, "score 범위")
        _check(r.get("grade") in GRADES, w, "grade")
        if prizes[0]["remaining"] == 0:
            _check(r["grade"] == GRADE_NOT_RECOMMENDED, w, "1등 0이면 notRecommended")
        _check(isinstance(r.get("lowStock"), bool), w, "lowStock")
        winners = r.get("winners")
        _check(isinstance(winners, list), w, "winners")
        for x in winners:
            _check(isinstance(x.get("inStoreList"), bool), w, "winners.inStoreList")
            _check((x.get("storeId") is not None) == x["inStoreList"], w, "storeId는 inStoreList일 때만")
            _check(isinstance(x.get("name"), str) and bool(x["name"]), w, "winners.name")
            _check(isinstance(x.get("address"), str), w, "winners.address")
            _coord_pair(w, x.get("lat"), x.get("lng"))


def validate_stores(doc: dict[str, Any]) -> None:
    _check(doc.get("schemaVersion") == 1, "stores", "schemaVersion은 1")
    _check(bool(_DATETIME.match(str(doc.get("generatedAt")))), "stores", "generatedAt 형식")
    stores = doc.get("stores")
    _check(isinstance(stores, list) and len(stores) > 0, "stores", "판매점이 없음")
    _check(doc.get("total") == len(stores), "stores", "total과 개수가 다름")
    ids = set()
    for s in stores:
        w = f"store {s.get('id')}"
        _check(isinstance(s.get("id"), str) and s["id"] not in ids, w, "id 중복 또는 문자열 아님")
        ids.add(s["id"])
        _check(isinstance(s.get("name"), str) and bool(s["name"]), w, "name")
        _check(isinstance(s.get("address"), str), w, "address")
        _check(s.get("tel") is None or isinstance(s["tel"], str), w, "tel")
        _coord_pair(w, s.get("lat"), s.get("lng"))
        _check(s.get("coordSource") in COORD_SOURCES, w, "coordSource")
        _check((s.get("coordSource") is None) == (s.get("lat") is None), w, "coordSource와 좌표가 맞지 않음")
        products = s.get("products")
        _check(isinstance(products, list) and len(products) > 0 and set(products) <= set(PRODUCTS), w, "products")
