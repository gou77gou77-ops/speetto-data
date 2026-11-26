"""회차별 점수 기록 → history/<상품>_<회차>.json (docs/data-contract.md "history").

rounds 수집이 끝날 때마다 회차마다 그날 값 한 건(날짜, 상태, 점수, 입고율, 1·2등 잔여)을 더한다.
같은 날 두 번 수집하면 그날 값을 덮어쓴다. 판매종료 후 30일이 지난 회차는 더 이상 바꾸지 않는다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .contract import ContractError, validate_history
from .output import write_json_atomic

HISTORY_DIR = "history"
FREEZE_DAYS = 30


def history_url(r: dict[str, Any]) -> str:
    """Pages 루트 기준 상대 경로. 앱은 dataBaseUrl 뒤에 붙인다."""
    return f"{HISTORY_DIR}/{r['product']}_{r['round']}.json"


def frozen(r: dict[str, Any], today: date) -> bool:
    """판매종료 후 30일이 지났으면 기록을 더 바꾸지 않는다."""
    if r["status"] != "ended":
        return False
    return today > date.fromisoformat(r["saleEndDate"]) + timedelta(days=FREEZE_DAYS)


def entry(r: dict[str, Any], day: str) -> dict[str, Any]:
    return {
        "date": day,
        "status": r["status"],
        "score": r["score"],
        "stockRate": r["stockRate"],
        "rank1Remaining": r["prizes"][0]["remaining"],
        "rank2Remaining": r["prizes"][1]["remaining"],
    }


def _load(path: Path, r: dict[str, Any], log) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        validate_history(doc)
        if doc["id"] != r["id"]:
            raise ContractError(f"id {doc['id']}가 회차 {r['id']}와 다름")
        return doc["entries"]
    except (ValueError, KeyError, TypeError) as e:
        log(f"{path.name}이 형식 검사를 통과하지 못해 새로 시작한다: {e}")
        return []


def build(r: dict[str, Any], entries: list[dict[str, Any]], day: str, generated_at: str) -> dict[str, Any]:
    by_date = {e["date"]: e for e in entries}
    by_date[day] = entry(r, day)
    doc = {
        "schemaVersion": 1,
        "id": r["id"],
        "product": r["product"],
        "round": r["round"],
        "updatedAt": generated_at,
        "entries": [by_date[d] for d in sorted(by_date)],
    }
    validate_history(doc)
    return doc


def update(doc: dict[str, Any], root: Path, log) -> tuple[int, int]:
    """rounds_latest.json 문서의 회차마다 기록을 더하고 각 회차에 historyUrl을 넣는다.

    root는 rounds_latest.json이 있는 폴더(gh-pages 루트). 바꾼 파일 수와 그대로 둔 회차 수를 돌려준다.
    파일은 모두 형식 검사를 통과한 뒤에 쓴다.
    """
    day = doc["generatedAt"][:10]
    today = date.fromisoformat(day)
    pending: list[tuple[Path, dict[str, Any]]] = []
    kept = 0
    for r in doc["rounds"]:
        rel = history_url(r)
        path = root / rel
        if frozen(r, today):
            r["historyUrl"] = rel if path.exists() else None
            kept += 1
            continue
        pending.append((path, build(r, _load(path, r, log), day, doc["generatedAt"])))
        r["historyUrl"] = rel
    for path, h in pending:
        write_json_atomic(path, h)
    return len(pending), kept
