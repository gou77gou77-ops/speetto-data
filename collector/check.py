"""동행복권 접속 확인. 워크플로의 첫 단계로, 수집 전에 한 번만 요청해 본다.

실행:
    python -m collector.check --target rounds
    python -m collector.check --target stores

정상이면 0, 접속할 수 없으면 2(원인을 "접속 차단"으로 로그에 남김), 응답 형식이 바뀌었으면 3.
이 단계는 파일을 읽거나 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys

from .dhlottery import BASE_URL, BlockedError, Client, FormatChangedError
from .output import gh_error

TARGETS = {
    "rounds": ("/st/selectPblcnDsctn.do",
               {"gdsType": "", "gdsPrice": "", "gdsStatus": "", "pageNum": 1, "recordCountPerPage": 1}),
    "stores": ("/prchsplcsrch/selectLtShp.do",
               {"l645LtNtslYn": "N", "l520LtNtslYn": "N", "st5LtNtslYn": "N", "st10LtNtslYn": "N",
                "st20LtNtslYn": "N", "cpexUsePsbltyYn": "N", "pageNum": 1, "recordCountPerPage": 10,
                "pageCount": 5, "srchCtpvNm": "", "srchSggNm": ""}),
}


def runner_label() -> str:
    env = os.environ.get("RUNNER_ENVIRONMENT")  # github-hosted / self-hosted (Actions에서만)
    name = os.environ.get("RUNNER_NAME")
    return f"{env or '로컬'}{f' ({name})' if name else ''}"


def check(target: str, client: Client) -> int:
    path, params = TARGETS[target]
    where = f"{BASE_URL}{path}"
    print(f"동행복권 접속 확인: {where} — 러너 {runner_label()}", flush=True)
    try:
        doc = client.get_json(path, params)
    except BlockedError as e:
        hint = ""
        if os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted":
            hint = " GitHub 러너는 해외 IP입니다. 계속 막히면 docs/github-setup.md 10절(self-hosted runner)을 보세요."
        gh_error("접속 차단", f"동행복권에 접속할 수 없습니다: {e}. 기존 JSON은 바꾸지 않았습니다.{hint}")
        return 2
    except FormatChangedError as e:
        gh_error("응답 형식 변경", f"동행복권 응답을 읽을 수 없습니다: {e}. 기존 JSON은 바꾸지 않았습니다.")
        return 3
    data = doc.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("total"), int) or not isinstance(data.get("list"), list):
        gh_error("응답 형식 변경", "동행복권 응답에 data.total / data.list가 없습니다. 기존 JSON은 바꾸지 않았습니다.")
        return 3
    print(f"접속 정상: total {data['total']}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="동행복권 접속 확인")
    ap.add_argument("--target", choices=sorted(TARGETS), default="rounds")
    args = ap.parse_args(argv)
    return check(args.target, Client(retries=2))


if __name__ == "__main__":
    sys.exit(main())
