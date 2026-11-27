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
    INDEX_CAP,
    grade_for,
    index_to_score,
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
        self.assertEqual(sp2000.score, 37.7)  # 100 × (1.442 − 0.5) / 2.5
        self.assertEqual(sp2000.grade, GRADE_BAD)

        # 스피또500 48회
        sp500 = score_round(make_round(rate=62, r1=(3, 5), r2=(45, 100),
                                       amounts=(200_000_000, 1_000_000),
                                       issued=20_000_000, price=500))
        self.assertEqual((sp500.index, sp500.score, sp500.grade), (1.543, 41.7, GRADE_NORMAL))

    def test_unsold_launch_state_is_index_1(self):
        r = score_round(make_round(rate=0, r1=(12, 12), r2=(60, 60)))
        self.assertEqual(r.index, 1.0)
        self.assertEqual(r.score, 20.0)
        self.assertEqual(r.grade, GRADE_BAD)

    def test_index_capped_at_3(self):
        # 출고율 90%에 1·2등이 하나도 안 나갔으면 지수 10 -> 3으로 자르고 100점
        raw = oracle_index(make_round(rate=90, r1=(8, 8), r2=(24, 24)))
        self.assertGreater(raw, INDEX_CAP)
        r = score_round(make_round(rate=90, r1=(8, 8), r2=(24, 24)))
        self.assertEqual((r.index, r.score, r.grade), (3.0, 100.0, GRADE_STRONG))

    def test_score_formula_and_bounds(self):
        self.assertEqual(index_to_score(0.3), 0.0)
        self.assertEqual(index_to_score(0.5), 0.0)
        self.assertEqual(index_to_score(1.0), 20.0)
        self.assertEqual(index_to_score(1.75), 50.0)
        self.assertEqual(index_to_score(3.0), 100.0)
        self.assertEqual(index_to_score(10.0), 100.0)

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

    def test_on_sale_95_or_more_is_endgame_without_score(self):
        for rate in (95, 99, 100):
            with self.subTest(rate=rate):
                r = score_round(make_round(rate=rate, r1=(3, 8)))
                self.assertEqual(r.status, STATUS_ENDGAME)
                self.assertIsNone(r.score)
                self.assertIsNone(r.index)
                self.assertIsNone(r.grade)
                self.assertTrue(r.low_stock)

    def test_99_percent_with_one_first_prize_left(self):
        # 스피또2000 69회(2026-11-27): 출고율 99%, 1등 10장 중 1장. 예전 공식은 100점 "강력 추천"이었다.
        r = score_round(make_round(rate=99, r1=(1, 10), r2=(5, 30)))
        self.assertEqual((r.status, r.score, r.grade), (STATUS_ENDGAME, None, None))

    def test_94_percent_is_still_scored(self):
        r = score_round(make_round(rate=94, r1=(3, 8)))
        self.assertEqual(r.status, STATUS_ACTIVE)
        self.assertIsNotNone(r.score)
        self.assertFalse(r.low_stock)

    def test_rank1_sold_out_is_not_recommended_everywhere(self):
        active = score_round(make_round(rate=90, r1=(0, 8), r2=(24, 24)))
        endgame = score_round(make_round(rate=100, r1=(0, 8)))
        ended = score_round(make_round(on_sale=False, rate=100, r1=(0, 8)))
        for r in (active, endgame, ended):
            self.assertEqual(r.grade, GRADE_NOT_RECOMMENDED)
        # 점수가 있어도 1등이 없으면 구매 비추천
        self.assertIsNotNone(active.score)

    def test_low_stock_from_95_while_on_sale(self):
        self.assertTrue(score_round(make_round(rate=95)).low_stock)
        self.assertTrue(score_round(make_round(rate=99)).low_stock)
        self.assertTrue(score_round(make_round(rate=100)).low_stock)
        self.assertFalse(score_round(make_round(rate=94)).low_stock)
        self.assertFalse(score_round(make_round(on_sale=False, rate=100)).low_stock)


if __name__ == "__main__":
    unittest.main()
