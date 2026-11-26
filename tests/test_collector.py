"""수집기 테스트. 동행복권·행안부를 부르지 않고 가짜 응답으로 확인한다.

실행: 이 저장소(speetto-data) 루트에서 python -m unittest discover -s tests
"""

import io
import json
import socket
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import check, rounds, stores  # noqa: E402
from collector.contract import ContractError, validate_rounds, validate_stores  # noqa: E402
from collector.dhlottery import BlockedError, Client, CollectError, FormatChangedError  # noqa: E402
from collector.geo import road_address_core, road_key, utmk_to_wgs84, valid_coord, wgs84_to_utmk  # noqa: E402
from collector.juso import Geocoder  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


# ---------- 가짜 HTTP ----------

class _Res:
    def __init__(self, body, ctype="application/json;charset=UTF-8", status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(*responses):
    """요청마다 다음 응답을 준다. 예외 객체면 그 예외를 던진다."""
    seq = list(responses)
    calls = []

    def _open(req, timeout=None):
        calls.append(req.full_url)
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(r, BaseException):
            raise r
        return r

    _open.calls = calls
    return _open


def fake_client(*responses, **kw):
    return Client(opener=opener_returning(*responses), sleep=lambda s: None, **kw)


class RoutedClient:
    """경로·파라미터별 가짜 동행복권."""

    def __init__(self, routes):
        self.routes = routes
        self.request_count = 0

    def get_json(self, path, params):
        self.request_count += 1
        r = self.routes(path, params)
        if isinstance(r, BaseException):
            raise r
        return r


def detail_70(**over):
    d = {
        "stGmTypeNm": "스피또2000", "stEpsd": 70, "ntslStatus": "판매중", "stNtslAmt": 2000,
        "stNtslBgngDt": "2026-09-23", "stNtslEndDt": "2026-12-31", "stGiveEndDt": "2027-12-31",
        "stSpmtRt": 64, "pblcnQty": 40000000,
        "stRnk1GdsLstcCharCn": "10억원", "stRnk1WnQty": 8, "stIvtRnk1Qty": 4,
        "stRnk2GdsLstcCharCn": "1억원", "stRnk2WnQty": 24, "stIvtRnk2Qty": 14,
        "dataChgDt": "26-11-20", "fstDt": "26-11-20", "sndDt": "26-11-11",
    }
    d.update(over)
    return d


ITEM_70 = {"ntslWnSn": 5808, "stGmTypeCd": "SP2000", "stEpsd": 70, "ntslStatus": "판매중"}

STORE = {"id": "11141322", "name": "CU우면한라점", "address": "서울 서초구 바우뫼로7길 18 1층",
         "tel": None, "lat": 37.471226, "lng": 127.02492, "coordSource": "dhlottery", "products": ["SP2000"]}


def winner_row(**over):
    r = {"ltShpId": "74000001", "shpNm": "우면복권", "shpAddr": "서울 서초구 바우뫼로7길 18 1층",
         "rdnm": "서울 서초구 바우뫼로7길 18", "shpLat": 37.47124, "shpLot": 127.02490, "wnShpRnk": 1}
    r.update(over)
    return r


def store_row(**over):
    r = {"ltShpId": "11141322", "conmNm": "CU우면한라점", "bplcRdnmDaddr": "서울 서초구 바우뫼로7길 18 1층",
         "shpTelno": "02-857-4342", "shpLat": 37.471226, "shpLot": 127.02492,
         "st20LtNtslYn": "Y", "st10LtNtslYn": "N", "st5LtNtslYn": "N"}
    r.update(over)
    return r


# ---------- 테스트 ----------

class GeoTest(unittest.TestCase):
    def test_road_key_ignores_sido_and_detail(self):
        self.assertEqual(road_key("서울 서초구 바우뫼로7길 18 1층"), "서초구 바우뫼로7길")
        self.assertEqual(road_key("서울특별시 서초구 바우뫼로7길 18"), "서초구 바우뫼로7길")
        self.assertEqual(road_key("경기 수원시 장안구 대평로 86 1층 116호"), "수원시 장안구 대평로")
        self.assertIsNone(road_key("서울 서초구 방배동 464-11"))

    def test_road_address_core(self):
        self.assertEqual(road_address_core("경기 수원시 장안구 대평로 86 1층 116호"), "경기 수원시 장안구 대평로 86")
        self.assertEqual(road_address_core("서울 서초구 동작대로 54-1 1층 103호"), "서울 서초구 동작대로 54-1")

    def test_valid_coord(self):
        self.assertTrue(valid_coord(37.47, 127.02))
        self.assertFalse(valid_coord(0.0, 0.0))
        self.assertFalse(valid_coord(None, 127.0))
        self.assertFalse(valid_coord("37.4", 127.0))

    def test_utmk_matches_pyproj(self):
        # pyproj EPSG:4326 → EPSG:5179 결과 (2026-11-26 계산)
        cases = [
            ((37.5665, 126.978), (953901.165, 1952032.081)),
            ((35.1796, 129.0756), (1143467.380, 1688281.982)),
            ((33.4996, 126.5312), (910010.546, 1501279.789)),
        ]
        for (lat, lng), (x, y) in cases:
            gx, gy = wgs84_to_utmk(lat, lng)
            self.assertAlmostEqual(gx, x, delta=0.01)
            self.assertAlmostEqual(gy, y, delta=0.01)
            blat, blng = utmk_to_wgs84(x, y)
            self.assertAlmostEqual(blat, lat, places=6)
            self.assertAlmostEqual(blng, lng, places=6)


class ClientTest(unittest.TestCase):
    def test_waits_one_second_between_requests(self):
        now = [100.0]
        slept = []

        def sleep(s):
            slept.append(s)
            now[0] += s

        c = Client(opener=opener_returning(_Res("{}")), sleep=sleep, clock=lambda: now[0])
        c.get_json("/a", {})
        now[0] += 0.3
        c.get_json("/a", {})
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 0.7)

    def test_403_is_block_without_retry(self):
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        op = opener_returning(err)
        c = Client(opener=op, sleep=lambda s: None)
        with self.assertRaises(BlockedError) as cm:
            c.get_json("/a", {})
        self.assertIn("접속 차단", str(cm.exception))
        self.assertEqual(len(op.calls), 1)

    def test_timeout_is_block_after_retries(self):
        op = opener_returning(urllib.error.URLError(socket.timeout("timed out")))
        c = Client(opener=op, sleep=lambda s: None, retries=3)
        with self.assertRaises(BlockedError) as cm:
            c.get_json("/a", {})
        self.assertIn("시간 초과", str(cm.exception))
        self.assertEqual(len(op.calls), 3)

    def test_transient_error_then_success(self):
        c = fake_client(urllib.error.URLError(ConnectionResetError()), _Res('{"data": 1}'))
        self.assertEqual(c.get_json("/a", {}), {"data": 1})

    def test_html_instead_of_json_is_block(self):
        c = fake_client(_Res("<html>접근이 제한되었습니다</html>", ctype="text/html"))
        with self.assertRaises(BlockedError) as cm:
            c.get_json("/a", {})
        self.assertIn("차단", str(cm.exception))

    def test_maintenance_page(self):
        c = fake_client(_Res("<html>시스템 점검 중입니다</html>", ctype="text/html"))
        with self.assertRaises(BlockedError) as cm:
            c.get_json("/a", {})
        self.assertIn("점검", str(cm.exception))

    def test_bad_json_is_format_change(self):
        with self.assertRaises(FormatChangedError):
            fake_client(_Res("[1, 2]")).get_json("/a", {})

    def test_user_agent_has_contact_from_env(self):
        op = opener_returning(_Res("{}"))
        seen = {}

        def capture(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            return op(req, timeout)

        with mock.patch.dict("os.environ", {"COLLECTOR_CONTACT": "contact@example.org"}):
            Client(opener=capture, sleep=lambda s: None).get_json("/a", {})
        self.assertEqual(seen["ua"], "SpeettoDataCollector/1.0 (com.susuim.speetto; contact@example.org)")


class CheckTest(unittest.TestCase):
    def run_check(self, client, env=None):
        buf = io.StringIO()
        with mock.patch.dict("os.environ", env or {}, clear=False), redirect_stdout(buf):
            code = check.check("rounds", client)
        return code, buf.getvalue()

    def test_ok(self):
        code, out = self.run_check(fake_client(_Res('{"data": {"total": 36, "list": []}}')))
        self.assertEqual(code, 0)
        self.assertIn("접속 정상", out)

    def test_blocked_on_github_runner(self):
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        code, out = self.run_check(fake_client(err), {"RUNNER_ENVIRONMENT": "github-hosted"})
        self.assertEqual(code, 2)
        self.assertIn("::error title=접속 차단::", out)
        self.assertIn("기존 JSON은 바꾸지 않았습니다", out)
        self.assertIn("self-hosted", out)

    def test_format_changed(self):
        code, out = self.run_check(fake_client(_Res('{"data": null}')))
        self.assertEqual(code, 3)
        self.assertIn("응답 형식 변경", out)


class GeocoderTest(unittest.TestCase):
    def juso(self, *responses):
        return Geocoder("k1", "k2", opener=opener_returning(*responses), sleep=lambda s: None)

    def test_no_key_means_none(self):
        with mock.patch.dict("os.environ", {"JUSO_API_KEY": ""}):
            self.assertIsNone(Geocoder.from_env())

    def test_coord_key_falls_back(self):
        with mock.patch.dict("os.environ", {"JUSO_API_KEY": "a", "JUSO_COORD_API_KEY": ""}):
            g = Geocoder.from_env()
        self.assertEqual((g._search_key, g._coord_key), ("a", "a"))

    def test_search_then_coord(self):
        search = {"results": {"common": {"errorCode": "0"}, "juso": [
            {"admCd": "4111110100", "rnMgtSn": "411113186007", "udrtYn": "0", "buldMnnm": 86, "buldSlno": 0}]}}
        coord = {"results": {"common": {"errorCode": "0"}, "juso": [{"entX": "955089.522", "entY": "1922013.862"}]}}
        g = self.juso(_Res(json.dumps(search)), _Res(json.dumps(coord)))
        lat, lng = g.locate("경기 수원시 장안구 대평로 86 1층 116호")
        self.assertAlmostEqual(lat, 37.295992, places=5)
        self.assertAlmostEqual(lng, 126.993283, places=5)
        self.assertIn("keyword=%EA%B2%BD%EA%B8%B0", g._open.calls[0])
        self.assertIn("confmKey=k2", g._open.calls[1])

    def test_not_found_and_error(self):
        empty = {"results": {"common": {"errorCode": "0"}, "juso": []}}
        self.assertIsNone(self.juso(_Res(json.dumps(empty))).locate("서울 서초구 바우뫼로7길 18"))
        bad = {"results": {"common": {"errorCode": "E0001", "errorMessage": "승인되지 않은 KEY"}}}
        g = self.juso(_Res(json.dumps(bad)))
        with redirect_stdout(io.StringIO()) as buf:
            self.assertIsNone(g.locate("서울 서초구 바우뫼로7길 18"))
        self.assertEqual(g.errors, 1)
        self.assertNotIn("k1", buf.getvalue())


class StoresTest(unittest.TestCase):
    def test_build_store_filters_and_coords(self):
        self.assertIsNone(stores.build_store(store_row(st20LtNtslYn="N"), None))
        s = stores.build_store(store_row(st10LtNtslYn="Y", shpTelno=" "), None)
        self.assertEqual(s["products"], ["SP2000", "SP1000"])
        self.assertIsNone(s["tel"])
        self.assertEqual(s["coordSource"], "dhlottery")

        missing = stores.build_store(store_row(shpLat=None, shpLot=None), None)
        self.assertEqual((missing["lat"], missing["lng"], missing["coordSource"]), (None, None, None))

        geo = mock.Mock()
        geo.locate.return_value = (37.5, 127.0)
        fixed = stores.build_store(store_row(shpLat=0.0, shpLot=0.0), geo)
        self.assertEqual((fixed["lat"], fixed["lng"], fixed["coordSource"]), (37.5, 127.0, "juso"))

    def pages(self, total, per_page_rows):
        def routes(path, params):
            rows = per_page_rows(params["pageNum"])
            return {"data": {"total": total, "list": rows}}
        return RoutedClient(routes)

    def test_fetch_all_pages(self):
        c = self.pages(23, lambda p: [store_row(ltShpId=f"{p}-{i}") for i in range(10 if p < 3 else 3)])
        rows = stores.fetch_all(c, log=lambda *a: None)
        self.assertEqual(len(rows), 23)
        self.assertEqual(c.request_count, 3)

    def test_fetch_all_incomplete_fails(self):
        c = self.pages(30, lambda p: [store_row(ltShpId=f"{p}-{i}") for i in range(10)] if p < 3 else [])
        with self.assertRaises(CollectError):
            stores.fetch_all(c, log=lambda *a: None)

    def run_main(self, argv, client):
        with mock.patch.object(stores, "Client", return_value=client), \
                mock.patch.dict("os.environ", {"JUSO_API_KEY": ""}), redirect_stdout(io.StringIO()) as buf:
            return stores.main(argv), buf.getvalue()

    def test_failure_keeps_last_week_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "stores.json"
            out.write_text('{"old": true}', encoding="utf-8")
            calls = {"n": 0}

            def routes(path, params):
                calls["n"] += 1
                if params["pageNum"] == 2:
                    return BlockedError("응답 시간 초과")
                return {"data": {"total": 20, "list": [store_row(ltShpId=f"a{i}") for i in range(10)]}}

            code, log = self.run_main(["--out", str(out)], RoutedClient(routes))
            self.assertEqual(code, 2)
            self.assertIn("접속 차단", log)
            self.assertEqual(out.read_text(encoding="utf-8"), '{"old": true}')

    def test_big_shrink_keeps_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "stores.json"
            out.write_text(json.dumps({"total": 1000}), encoding="utf-8")
            client = RoutedClient(lambda p, q: {"data": {"total": 1, "list": [store_row()]}})
            code, log = self.run_main(["--out", str(out)], client)
            self.assertEqual(code, 3)
            self.assertIn("줄었다", log)

    def test_success_writes_contract(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "stores.json"
            client = RoutedClient(lambda p, q: {"data": {"total": 2, "list": [
                store_row(), store_row(ltShpId="2", st20LtNtslYn="N")]}})
            code, _ = self.run_main(["--out", str(out)], client)
            self.assertEqual(code, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            validate_stores(doc)
            self.assertEqual(doc["total"], 1)

    def test_sample_cannot_overwrite_stores_json(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            stores.main(["--sido", "서울", "--sigungu", "서초구", "--out", "out/stores.json"])


class RoundsTest(unittest.TestCase):
    def test_build_round_real_values(self):
        r = rounds.build_round(ITEM_70, detail_70())
        self.assertEqual((r["status"], r["score"], r["grade"], r["index"]), ("active", 72.1, "good", 1.442))
        self.assertEqual(r["stockDate"], "2026-11-20")
        self.assertEqual([p["asOf"] for p in r["prizes"]], ["2026-11-20", "2026-11-11"])
        self.assertEqual(r["prizes"][0]["amount"], 1_000_000_000)

    def test_statuses(self):
        ended = rounds.build_round(ITEM_70, detail_70(ntslStatus="판매종료", stSpmtRt=100))
        self.assertEqual((ended["status"], ended["score"]), ("ended", None))
        endgame = rounds.build_round(ITEM_70, detail_70(stSpmtRt=100))
        self.assertEqual((endgame["status"], endgame["score"], endgame["grade"]), ("endgame", None, None))
        sold_out = rounds.build_round(ITEM_70, detail_70(stSpmtRt=100, stIvtRnk1Qty=0))
        self.assertEqual(sold_out["grade"], "notRecommended")

    def test_as_of_falls_back_to_stock_date(self):
        r = rounds.build_round(ITEM_70, detail_70(sndDt=None))
        self.assertEqual(r["prizes"][1]["asOf"], "2026-11-20")

    def test_format_changes(self):
        for over in ({"ntslStatus": "판매예정"}, {"stRnk1GdsLstcCharCn": "10조원"}, {"dataChgDt": "2026/11/20"},
                     {"stIvtRnk1Qty": 9}, {"pblcnQty": None}, {"stNtslAmt": 1000}):
            with self.subTest(over=over), self.assertRaises(FormatChangedError):
                rounds.build_round(ITEM_70, detail_70(**over))

    def test_winner_matching(self):
        index = rounds.StoreIndex([STORE])
        matched = rounds.build_winner(winner_row(), index, None)
        self.assertEqual((matched["storeId"], matched["inStoreList"], matched["name"]), ("11141322", True, "CU우면한라점"))
        self.assertEqual((matched["lat"], matched["lng"]), (STORE["lat"], STORE["lng"]))

        far = rounds.build_winner(winner_row(shpLat=37.4716), index, None)  # 약 40 m
        self.assertEqual((far["storeId"], far["inStoreList"], far["lat"]), (None, False, 37.4716))

        other_road = rounds.build_winner(winner_row(rdnm="서울 서초구 양재대로 1"), index, None)
        self.assertFalse(other_road["inStoreList"])

        no_coord = rounds.build_winner(winner_row(shpLat=None, shpLot=None), index, None)
        self.assertEqual((no_coord["lat"], no_coord["inStoreList"]), (None, False))

        geo = mock.Mock()
        geo.locate.return_value = (37.471226, 127.02492)
        geocoded = rounds.build_winner(winner_row(shpLat=None, shpLot=None), index, geo)
        self.assertTrue(geocoded["inStoreList"])

    def routes(self, blocked_at=None):
        def r(path, params):
            if blocked_at and path == blocked_at:
                return BlockedError("HTTP 403. 접속 차단", retryable=False)
            if path == rounds.LIST_PATH:
                return {"data": {"total": 1, "list": [ITEM_70]}}
            if path == rounds.DETAIL_PATH:
                return {"data": {"result": detail_70()}}
            return {"data": {"list": [winner_row(), winner_row(wnShpRnk=2), winner_row(ltShpId="7", rdnm="대구 달서구 대명천로 220",
                                                                                     shpAddr="대구 달서구 대명천로 220 1층",
                                                                                     shpNm="일등복권편의점", shpLat=35.842203, shpLot=128.536192)]}}
        return RoutedClient(r)

    def run_main(self, d, client, stores_doc):
        out = Path(d) / "rounds_latest.json"
        sp = Path(d) / "stores.json"
        if stores_doc is not None:
            sp.write_text(json.dumps(stores_doc), encoding="utf-8")
        with mock.patch.object(rounds, "Client", return_value=client), \
                mock.patch.dict("os.environ", {"JUSO_API_KEY": ""}), redirect_stdout(io.StringIO()) as buf:
            code = rounds.main(["--out", str(out), "--stores", str(sp)])
        return code, out, buf.getvalue()

    def test_collect_writes_contract(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, log = self.run_main(d, self.routes(), {"total": 1, "stores": [STORE]})
            self.assertEqual(code, 0, log)
            doc = json.loads(out.read_text(encoding="utf-8"))
            validate_rounds(doc)
            ws = doc["rounds"][0]["winners"]
            self.assertEqual([w["inStoreList"] for w in ws], [True, False])  # 2등 줄은 빠짐
            self.assertEqual(doc["stockDate"], "2026-11-20")

    def test_blocked_keeps_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "rounds_latest.json"
            out.write_text("old", encoding="utf-8")
            code, out, log = self.run_main(d, self.routes(blocked_at=rounds.WINNERS_PATH), {"total": 1, "stores": [STORE]})
            self.assertEqual(code, 2)
            self.assertIn("접속 차단", log)
            self.assertEqual(out.read_text(encoding="utf-8"), "old")

    def test_missing_stores_json(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, log = self.run_main(d, self.routes(), None)
            self.assertEqual(code, 4)
            self.assertIn("stores 워크플로를 먼저", log)
            self.assertFalse(out.exists())


class ContractTest(unittest.TestCase):
    def test_real_samples_pass(self):
        validate_rounds(json.loads((FIXTURES / "rounds_latest.sample.json").read_text(encoding="utf-8")))
        validate_stores(json.loads((FIXTURES / "stores.sample.json").read_text(encoding="utf-8")))

    def test_violations(self):
        good = json.loads((FIXTURES / "rounds_latest.sample.json").read_text(encoding="utf-8"))
        bad_cases = [
            lambda d: d["rounds"][0].update(score=None),
            lambda d: d["rounds"][0]["prizes"].pop(),
            lambda d: d.update(generatedAt="2026-11-26T00:00:00Z"),
            lambda d: d["rounds"][0]["winners"].append(
                {"storeId": "1", "name": "x", "address": "", "lat": None, "lng": None, "inStoreList": False}),
        ]
        for mutate in bad_cases:
            d = json.loads(json.dumps(good))
            mutate(d)
            with self.assertRaises(ContractError):
                validate_rounds(d)


if __name__ == "__main__":
    unittest.main()
