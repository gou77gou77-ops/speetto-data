"""공개 파일을 올리기 전에 다시 검사한다(docs/data-contract.md).

실행:
    python -m collector.verify .gh-pages/stores.json .gh-pages/rounds_latest.json .gh-pages/config.json

모두 통과하면 0, 하나라도 어긋나면 3. 파일은 바꾸지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .contract import ContractError, validate_rounds, validate_stores
from .output import gh_error, log

VALIDATORS = {"stores.json": validate_stores, "rounds_latest.json": validate_rounds}


def verify(path: Path) -> str:
    doc = json.loads(path.read_text(encoding="utf-8"))
    check = VALIDATORS.get(path.name)
    if check:
        check(doc)
    elif not isinstance(doc, dict):
        raise ContractError("최상위가 객체가 아님")
    if path.name == "stores.json":
        return f"판매점 {doc['total']}곳"
    if path.name == "rounds_latest.json":
        return f"회차 {len(doc['rounds'])}개, 생성 {doc['generatedAt']}"
    return "JSON 정상"


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (sys.argv[1:] if argv is None else argv)]
    code = 0
    for p in paths:
        try:
            log(f"검증 통과: {p.name} — {verify(p)}")
        except FileNotFoundError:
            gh_error("검증 실패", f"{p}가 없습니다")
            code = 3
        except (ContractError, ValueError, KeyError, TypeError) as e:
            gh_error("검증 실패", f"{p.name}: {e}")
            code = 3
    return code


if __name__ == "__main__":
    sys.exit(main())
