"""공개 파일을 올리기 전에 다시 검사한다(docs/data-contract.md).

실행:
    python -m collector.verify .gh-pages/stores.json .gh-pages/rounds_latest.json .gh-pages/config.json
    python -m collector.verify .gh-pages/history     # 폴더면 안의 JSON 모두

모두 통과하면 0, 하나라도 어긋나면 3. 파일은 바꾸지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .contract import ContractError, validate_history, validate_rounds, validate_stores
from .output import gh_error, log

VALIDATORS = {"stores.json": validate_stores, "rounds_latest.json": validate_rounds}


def verify(path: Path) -> str:
    doc = json.loads(path.read_text(encoding="utf-8"))
    check = validate_history if path.parent.name == "history" else VALIDATORS.get(path.name)
    if check:
        check(doc)
    elif not isinstance(doc, dict):
        raise ContractError("최상위가 객체가 아님")
    if path.name == "stores.json":
        return f"판매점 {doc['total']}곳"
    if path.name == "rounds_latest.json":
        return f"회차 {len(doc['rounds'])}개, 생성 {doc['generatedAt']}"
    if check is validate_history:
        return f"기록 {len(doc['entries'])}건"
    return "JSON 정상"


def main(argv: list[str] | None = None) -> int:
    paths: list[Path] = []
    for a in (sys.argv[1:] if argv is None else argv):
        p = Path(a)
        # 폴더(history/)를 주면 안의 JSON을 모두 검사한다
        paths.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    code = 0
    for p in paths:
        try:
            summary = verify(p)
        except FileNotFoundError:
            gh_error("검증 실패", f"{p}가 없습니다")
            code = 3
        except (ContractError, ValueError, KeyError, TypeError) as e:
            gh_error("검증 실패", f"{p.name}: {e}")
            code = 3
        else:
            log(f"검증 통과: {p.name} — {summary}")
    return code


if __name__ == "__main__":
    sys.exit(main())
