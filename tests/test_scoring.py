"""실행: 이 저장소(speetto-data) 루트에서 python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.scoring import (  # noqa: E402
    GRADE_BAD,
    GRADE_GOOD,
    GRADE_NORMAL,
    GRADE_NOT_RECOMMENDED,
    GRADE_STRONG,
    STATUS_ACTIVE,
    STATUS_ENDED,
    STATUS_ENDGAME,
    Prize,
    RoundInput,
    grade_for,
    oracle_index,
    parse_amount,
    score_round,
)


def make_round(*, on_sale=True, rate=64, r1=(4, 8), r2=(14, 24),
               amounts=(1_000_000_000, 100_000_000), issued=40_000_000, price=2000):
    return RoundInput(
        on_sale=on_sale,
        stock_rate=rate,
        issued=issued,
        price=price,
        prizes=(
            Prize(rank=1, amount=amounts[0], remaining=r1[0], total=r1[1]),
            Prize(rank=2, amount=amounts[1], remaining=r2[0], total=r2[1]),
        ),
    )


class ParseAmountTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_amount("10억원"), 1_000_000_000)
        self.assertEqual(parse_amount("1천만원"), 10_000_000)
        self.assertEqual(parse_amount("1백만원"), 1_000_000)
        self.assertEqual(parse_amount("2만원"), 20_000)
        self.assertEqual(parse_amount("4천원"), 4_000)
        self.assertEqual(parse_amount("1.5억원"), 150_000_000)

    def test_unknown_unit_fails(self):
        with self.assertRaises(ValueError):
            parse_amount("10조원")
        with self.assertRaises(ValueError):
            parse_amount("")


class OracleIndexTest(unittest.TestCase):
    def test_real_values_2026_11_26(self):
        # 스피또2000 70회 (docs/data-source.md 3절 검산표)
        sp2000 = score_round(make_round())
        self.assertEqual(sp2000.status, STATUS_ACTIVE)
        self.assertAlmostEqual(sp2000.index, 1.442, places=3)
        self.assertEqual(sp2000.score, 72.1)
        self.assertEqual(sp2000.grade, GRADE_GOOD)

        # 스피또500 48회
        sp500 = score_round(make_round(rate=62, r1=(3, 5), r2=(45, 100),
                                       amounts=(200_000_000, 1_000_000),
                                       issued=20_000_000, price=500))
        self.assertEqual(sp500.score, 77.2)

    def test_unsold_launch_state_is_index_1(self):
        r = score_round(make_round(rate=0, r1=(12, 12), r2=(60, 60)))
        self.assertEqual(r.index, 1.0)
        self.assertEqual(r.score, 50.0)
        self.assertEqual(r.grade, GRADE_NORMAL)

    def test_score_capped_at_100(self):
        r = score_round(make_round(rate=90, r1=(8, 8), r2=(24, 24)))
        self.assertEqual(r.score, 100.0)
        self.assertEqual(r.grade, GRADE_STRONG)

    def test_index_refuses_full_stock(self):
        with self.assertRaises(ValueError):
            oracle_index(make_round(rate=100))

    def test_grade_bands(self):
        self.assertEqual(grade_for(80), GRADE_STRONG)
        self.assertEqual(grade_for(79.9), GRADE_GOOD)
        self.assertEqual(grade_for(60), GRADE_GOOD)
        self.assertEqual(grade_for(59.9), GRADE_NORMAL)
        self.assertEqual(grade_for(40), GRADE_NORMAL)
        self.assertEqual(grade_for(39.9), GRADE_BAD)


class StatusRuleTest(unittest.TestCase):
    def test_ended_round_has_no_score(self):
        r = score_round(make_round(on_sale=False, rate=81, r1=(1, 6)))
        self.assertEqual(r.status, STATUS_ENDED)
        self.assertIsNone(r.score)
        self.assertIsNone(r.index)
        self.assertIsNone(r.grade)
        self.assertFalse(r.low_stock)

    def test_on_sale_full_stock_is_endgame_without_score(self):
        r = score_round(make_round(rate=100, r1=(3, 8)))
        self.assertEqual(r.status, STATUS_ENDGAME)
        self.assertIsNone(r.score)
        self.assertIsNone(r.index)
        self.assertIsNone(r.grade)
        self.assertFalse(r.low_stock)

    def test_rank1_sold_out_is_not_recommended_everywhere(self):
        active = score_round(make_round(rate=90, r1=(0, 8), r2=(24, 24)))
        endgame = score_round(make_round(rate=100, r1=(0, 8)))
        ended = score_round(make_round(on_sale=False, rate=100, r1=(0, 8)))
        for r in (active, endgame, ended):
            self.assertEqual(r.grade, GRADE_NOT_RECOMMENDED)
        # 점수가 높아도 1등이 없으면 구매 비추천
        self.assertGreaterEqual(active.score, 80)

    def test_low_stock_badge_only_for_active(self):
        self.assertTrue(score_round(make_round(rate=95)).low_stock)
        self.assertTrue(score_round(make_round(rate=99)).low_stock)
        self.assertFalse(score_round(make_round(rate=94)).low_stock)
        self.assertFalse(score_round(make_round(rate=100)).low_stock)


if __name__ == "__main__":
    unittest.main()
