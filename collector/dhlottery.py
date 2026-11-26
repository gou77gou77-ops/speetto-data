"""동행복권 요청 공통: User-Agent, 1초 1건 속도 제한, 재시도, 차단·형식 오류 구분."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

BASE_URL = "https://www.dhlottery.co.kr"
MIN_INTERVAL = 1.0  # 초. 동행복권 요청 사이 최소 간격
TIMEOUT = 20

BLOCK_STATUS = {401, 403, 407, 429, 451}


class CollectError(Exception):
    """수집 실패. 이 예외가 나면 기존 JSON을 바꾸지 않는다."""


class BlockedError(CollectError):
    """동행복권에 접속할 수 없음 (차단, 연결 실패, 점검 페이지 등)."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class FormatChangedError(CollectError):
    """응답은 왔지만 형식이 예상과 다름."""


def user_agent() -> str:
    contact = os.environ.get("COLLECTOR_CONTACT", "").strip()
    base = "SpeettoDataCollector/1.0 (com.susuim.speetto"
    return f"{base}; {contact})" if contact else f"{base})"


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
    """동행복권 GET 요청. 모든 요청이 같은 속도 제한을 지난다."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        min_interval: float = MIN_INTERVAL,
        retries: int = 3,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url
        self.min_interval = min_interval
        self.retries = retries
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
        last_error: CollectError | None = None
        for attempt in range(self.retries):
            if attempt:
                self._sleep(5 * attempt)
            try:
                return self._get_once(url)
            except BlockedError as e:
                last_error = e
                if not e.retryable:
                    break
        assert last_error is not None
        raise last_error

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
            raise BlockedError(describe_network_error(e)) from e

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
