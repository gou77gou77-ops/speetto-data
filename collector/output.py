"""JSON 쓰기와 시각. 파일은 다 만든 뒤 한 번에 바꾼다(중간에 실패하면 기존 파일 그대로)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def write_json_atomic(path: str | os.PathLike[str], doc: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: str | os.PathLike[str]) -> Any | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def gh_error(title: str, message: str) -> None:
    """GitHub Actions 로그에 빨간 오류 표시(로컬에서는 그냥 한 줄)."""
    print(f"::error title={title}::{message}", flush=True)


def gh_warning(title: str, message: str) -> None:
    print(f"::warning title={title}::{message}", flush=True)


def log(message: str) -> None:
    """진행 로그. Actions에서도 바로 보이게 줄마다 내보낸다."""
    print(message, flush=True)


def elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}분 {s:02d}초"
