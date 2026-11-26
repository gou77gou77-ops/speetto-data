"""동행복권 요청 공통: User-Agent, 요청 간격, 재시도, 차단·형식 오류 구분."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from .output import log as log_line

BASE_URL = "https://www.dhlottery.co.kr"
GITHUB_INTERVAL = 3.0  # 초. GitHub 러너(해외 IP)에서 요청 사이 간격
LOCAL_INTERVAL = 1.0  # 초. 내 PC(self-hosted·로컬)에서 요청 사이 간격. 1초보다 짧게 하지 않는다
TIMEOUT = 20
TIMEOUT_PAUSE = 60.0  # 응답 시간 초과 뒤 쉬는 시간(초)
TIMEOUT_RETRIES = 3  # 시간 초과 뒤 같은 요청을 다시 하는 최대 횟수

BLOCK_STATUS = {401, 403, 407, 429, 451}


class CollectError(Exception):
    """수집 실패. 이 예외가 나면 기존 JSON을 바꾸지 않는다."""


class BlockedError(CollectError):
    """동행복권에 접속할 수 없음 (차단, 연결 실패, 점검 페이지 등)."""

    def __init__(self, message: str, retryable: bool = True, timeout: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.timeout = timeout


class FormatChangedError(CollectError):
    """응답은 왔지만 형식이 예상과 다름."""


UA_BASE = "SpeettoDataCollector/1.0 (com.susuim.speetto"


def user_agent() -> str:
    contact = os.environ.get("COLLECTOR_CONTACT", "").strip()
    return f"{UA_BASE}; {contact})" if contact else f"{UA_BASE})"


def user_agent_summary() -> str:
    """로그용. 연락처 값은 공개 로그에 남기지 않는다."""
    has = bool(os.environ.get("COLLECTOR_CONTACT", "").strip())
    return f"{UA_BASE}; 연락처 {'설정됨' if has else '없음'})"


def default_interval(env: Mapping[str, str] | None = None) -> float:
    """COLLECTOR_INTERVAL(초)이 있으면 그 값, 없으면 GitHub 러너 3초 / 내 PC 1초."""
    env = os.environ if env is None else env
    raw = env.get("COLLECTOR_INTERVAL", "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"COLLECTOR_INTERVAL={raw!r}은 숫자가 아니다") from None
        if value < LOCAL_INTERVAL:
            raise ValueError(f"COLLECTOR_INTERVAL은 {LOCAL_INTERVAL:g}초 이상이어야 한다 (지금 {raw})")
        return value
    return GITHUB_INTERVAL if env.get("RUNNER_ENVIRONMENT") == "github-hosted" else LOCAL_INTERVAL


def _is_timeout(e: BaseException) -> bool:
    reason = e.reason if isinstance(e, urllib.error.URLError) else e
    return isinstance(reason, (socket.timeout, TimeoutError))


def describe_network_error(e: BaseException) -> str:
    """연결 단계 오류를 사람이 읽을 원인 문장으로 바꾼다."""
    reason = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(reason, socket.gaierror):
        return f"DNS 조회 실패 ({reason}). 러너의 네트워크나 DNS 문제"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "응답 시간 초과. 해외 IP를 조용히 막는 경우 이렇게 보인다"
    if isinstance(reason, ConnectionRefusedError):
        return "연결 거부. 접속 차단으로 보인다"
    if isinstance(reason, ConnectionResetError):
        return "서버가 연결을 끊음. 접속 차단으로 보인다"
    return f"연결 실패: {reason!r}"


class Client:
    """동행복권 GET 요청. 모든 요청이 같은 간격 제한을 지난다.

    시간 초과는 TIMEOUT_PAUSE초 쉬고 같은 요청을 최대 TIMEOUT_RETRIES번 다시 한다.
    그 밖의 일시 오류(5xx, 연결 끊김)는 retries번까지 짧게 쉬고 다시 한다.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        min_interval: float | None = None,
        retries: int = 3,
        timeout_retries: int = TIMEOUT_RETRIES,
        timeout_pause: float = TIMEOUT_PAUSE,
        log: Callable[[str], None] = log_line,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url
        self.min_interval = default_interval() if min_interval is None else min_interval
        self.retries = retries
        self.timeout_retries = timeout_retries
        self.timeout_pause = timeout_pause
        self._log = log
        self._open = opener
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.request_count = 0

    def _wait_turn(self) -> None:
        if self._last is not None:
            left = self.min_interval - (self._clock() - self._last)
            if left > 0:
                self._sleep(left)
        self._last = self._clock()

    def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        failures = timeouts = 0
        while True:
            try:
                return self._get_once(url)
            except BlockedError as e:
                if e.timeout:
                    if timeouts >= self.timeout_retries:
                        if timeouts:
                            raise BlockedError(
                                f"{e} ({self.timeout_pause:g}초씩 쉬고 {timeouts}번 다시 했지만 응답 없음)",
                                timeout=True,
                            ) from e
                        raise
                    timeouts += 1
                    self._log(f"  응답 시간 초과 — {self.timeout_pause:g}초 쉬고 같은 요청 다시 "
                              f"({timeouts}/{self.timeout_retries}, 요청 {self.request_count}번째)")
                    self._sleep(self.timeout_pause)
                    continue
                failures += 1
                if not e.retryable or failures >= self.retries:
                    raise
                self._sleep(5 * failures)

    def _get_once(self, url: str) -> dict[str, Any]:
        self._wait_turn()
        self.request_count += 1
        req = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
        )
        try:
            with self._open(req, timeout=TIMEOUT) as res:
                status = res.status
                ctype = res.headers.get("Content-Type", "")
                body = res.read()
        except urllib.error.HTTPError as e:
            if e.code in BLOCK_STATUS:
                raise BlockedError(f"HTTP {e.code}. 접속 차단", retryable=False) from e
            raise BlockedError(f"HTTP {e.code}. 서버 오류") from e
        except (urllib.error.URLError, OSError) as e:
            raise BlockedError(describe_network_error(e), timeout=_is_timeout(e)) from e

        text = body.decode("utf-8", errors="replace")
        if "json" not in ctype.lower():
            if "점검" in text:
                raise BlockedError(
                    f"HTTP {status}, JSON 대신 점검 안내 페이지가 옴. 사이트 점검 중", retryable=False
                )
            raise BlockedError(
                f"HTTP {status}, JSON 대신 {ctype or '알 수 없는 형식'} 응답. 접속 차단 페이지로 보인다",
                retryable=False,
            )
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            raise FormatChangedError(f"JSON을 읽을 수 없음: {e}") from e
        if not isinstance(doc, dict):
            raise FormatChangedError("응답 최상위가 객체가 아님")
        return doc
