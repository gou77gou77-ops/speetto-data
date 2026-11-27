"""오라클 지수 계산과 회차 상태 판정.

입력은 동행복권 상세 응답(docs/data-source.md 2절)에서 뽑은 값이고,
출력은 rounds_latest.json의 회차 항목(docs/data-contract.md)에 들어간다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATUS_ACTIVE = "active"
STATUS_ENDGAME = "endgame"
STATUS_ENDED = "ended"

GRADE_STRONG = "strong"
GRADE_GOOD = "good"
GRADE_NORMAL = "normal"
GRADE_BAD = "bad"
GRADE_NOT_RECOMMENDED = "notRecommended"

# 입고율이 이 값 이상이면(판매중) 점수를 매기지 않고 막바지(endgame)로 둔다.
# 남은 발행분이 너무 적어 분모(미판매분)가 작아지고 지수가 폭등하기 때문이다.
ENDGAME_STOCK_RATE = 95
LOW_STOCK_RATE = ENDGAME_STOCK_RATE
# 지수 상한과 점수 환산: 점수 = 100 × (Index − 0.5) / 2.5, 0~100으로 자른다.
INDEX_CAP = 3.0
INDEX_FLOOR_FOR_ZERO = 0.5

_UNITS = {
    "억원": 100_000_000,
    "천만원": 10_000_000,
    "백만원": 1_000_000,
    "만원": 10_000,
    "천원": 1_000,
    "백원": 100,
    "원": 1,
}
_AMOUNT_RE = re.compile(r"^\s*([\d.,]+)\s*([가-힣]+)\s*$")


def parse_amount(text: str) -> int:
    """'10억원', '1천만원', '1.5억원' 같은 당첨금 문자열을 원 단위 정수로 바꾼다."""
    m = _AMOUNT_RE.match(text or "")
    if not m or m.group(2) not in _UNITS:
        raise ValueError(f"알 수 없는 당첨금 표기: {text!r}")
    number = float(m.group(1).replace(",", ""))
    return round(number * _UNITS[m.group(2)])


@dataclass(frozen=True)
class Prize:
    rank: int
    amount: int
    total: int
    remaining: int


@dataclass(frozen=True)
class RoundInput:
    on_sale: bool
    stock_rate: int  # 판매점 입고율(%), 0~100
    issued: int  # 발행량(매)
    price: int  # 가격(원)
    prizes: tuple[Prize, ...]  # 최소 1·2등

    def prize(self, rank: int) -> Prize:
        for p in self.prizes:
            if p.rank == rank:
                return p
        raise ValueError(f"{rank}등 정보가 없습니다")


@dataclass(frozen=True)
class RoundScore:
    status: str
    index: float | None
    score: float | None
    grade: str | None
    low_stock: bool


def oracle_index(r: RoundInput) -> float:
    """Index = R / R0 (상한 없음). 입고율 100%면 정의되지 않으므로 호출하면 안 된다."""
    if r.stock_rate >= 100:
        raise ValueError("입고율 100% 회차는 점수를 계산하지 않습니다")
    top = [r.prize(1), r.prize(2)]
    unsold = r.issued * (1 - r.stock_rate / 100) * r.price
    ratio_now = sum(p.amount * p.remaining for p in top) / unsold
    ratio_launch = sum(p.amount * p.total for p in top) / (r.issued * r.price)
    return ratio_now / ratio_launch


def index_to_score(index: float) -> float:
    """지수(상한 3.0)를 0~100 점수로."""
    capped = min(index, INDEX_CAP)
    return min(100.0, max(0.0, 100 * (capped - INDEX_FLOOR_FOR_ZERO) / (INDEX_CAP - INDEX_FLOOR_FOR_ZERO)))


def grade_for(score: float) -> str:
    if score >= 80:
        return GRADE_STRONG
    if score >= 60:
        return GRADE_GOOD
    if score >= 40:
        return GRADE_NORMAL
    return GRADE_BAD


def score_round(r: RoundInput) -> RoundScore:
    """회차 상태, 점수, 라벨을 정한다.

    - 판매종료: status=ended, 점수 없음 (추천 목록에서 숨김)
    - 판매중 + 입고율 95% 이상: status=endgame, 점수 없음 ("막바지 회차" 섹션)
    - 판매중 + 입고율 95% 미만: status=active, 지수(상한 3.0)로 점수 계산
    - 1등 잔여 0이면 상태와 상관없이 grade=notRecommended ("구매 비추천")
    """
    rank1_left = r.prize(1).remaining

    if not r.on_sale:
        status, index, score = STATUS_ENDED, None, None
    elif r.stock_rate >= ENDGAME_STOCK_RATE:
        status, index, score = STATUS_ENDGAME, None, None
    else:
        status = STATUS_ACTIVE
        index = min(oracle_index(r), INDEX_CAP)
        score = index_to_score(index)

    if rank1_left == 0:
        grade = GRADE_NOT_RECOMMENDED
    elif score is not None:
        grade = grade_for(score)
    else:
        grade = None

    return RoundScore(
        status=status,
        index=None if index is None else round(index, 3),
        score=None if score is None else round(score, 1),
        grade=grade,
        low_stock=r.on_sale and r.stock_rate >= LOW_STOCK_RATE,
    )
