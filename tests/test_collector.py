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

from collector import check, dhlottery, history, rounds, stores, verify  # noqa: E402
from collector.contract import ContractError, validate_history, validate_rounds, validate_stores  # noqa: E402
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
        self.min_interval = 0

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

        c = Client(opener=opener_returning(_Res("{}")), sleep=sleep, clock=lambda: now[0], min_interval=1.0)
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

    def test_timeout_pauses_60s_and_retries_same_request_3_times(self):
        op = opener_returning(urllib.error.URLError(socket.timeout("timed out")))
        slept, logs = [], []
        c = Client(opener=op, sleep=slept.append, min_interval=0, log=logs.append)
        with self.assertRaises(BlockedError) as cm:
            c.get_json("/a", {"pageNum": 7})
        self.assertIn("시간 초과", str(cm.exception))
        self.assertIn("60초씩 쉬고 3번 다시", str(cm.exception))
        self.assertTrue(cm.exception.timeout)
        self.assertEqual(len(op.calls), 4)
        self.assertEqual(len(set(op.calls)), 1)
        self.assertEqual(slept, [60.0, 60.0, 60.0])
        self.assertEqual(len(logs), 3)

    def test_timeout_then_success(self):
        op = opener_returning(urllib.error.URLError(socket.timeout("timed out")), _Res('{"ok": 1}'))
        slept = []
        c = Client(opener=op, sleep=slept.append, min_interval=0, log=lambda m: None)
        self.assertEqual(c.get_json("/a", {}), {"ok": 1})
        self.assertEqual(slept, [60.0])

    def test_server_error_retries_short(self):
        err = urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None)
        op = opener_returning(err)
        slept = []
        c = Client(opener=op, sleep=slept.append, min_interval=0, retries=3)
        with self.assertRaises(BlockedError):
            c.get_json("/a", {})
        self.assertEqual(len(op.calls), 3)
        self.assertEqual(slept, [5, 10])

    def test_default_interval(self):
        self.assertEqual(dhlottery.default_interval({"RUNNER_ENVIRONMENT": "github-hosted"}), 3.0)
        self.assertEqual(dhlottery.default_interval({"RUNNER_ENVIRONMENT": "self-hosted"}), 1.0)
        self.assertEqual(dhlottery.default_interval({}), 1.0)
        self.assertEqual(dhlottery.default_interval(
            {"RUNNER_ENVIRONMENT": "github-hosted", "COLLECTOR_INTERVAL": "5"}), 5.0)
        with self.assertRaises(ValueError):
            dhlottery.default_interval({"COLLECTOR_INTERVAL": "0.5"})
        with self.assertRaises(ValueError):
            dhlottery.default_interval({"COLLECTOR_INTERVAL": "abc"})

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

    def test_user_agent_summary_hides_contact(self):
        with mock.patch.dict("os.environ", {"COLLECTOR_CONTACT": "contact@example.org"}):
            s = dhlottery.user_agent_summary()
        self.assertNotIn("contact@example.org", s)
        self.assertIn("연락처 설정됨", s)
        with mock.patch.dict("os.environ", {"COLLECTOR_CONTACT": ""}):
            self.assertIn("연락처 없음", dhlottery.user_agent_summary())


class CheckTest(unittest.TestCase):
    def run_check(self, client, env=None):
        buf = io.StringIO()
        with mock.patch.dict("os.environ", env or {}, clear=False), redirect_stdout(buf):
            code = check.check("rounds", client)
        return code, buf.getvalue()

    def test_ok(self):
        code, out = self.run_check(fake_client(_Res('{"data": {"total": 36, "list": []}}')))
        self.assertEqual(code, 0)

    def test_log_hides_contact(self):
        code, out = self.run_check(fake_client(_Res('{"data": {"total": 36, "list": []}}')),
                                   {"COLLECTOR_CONTACT": "contact@example.org"})
        self.assertEqual(code, 0)
        self.assertNotIn("contact@example.org", out)
        self.assertIn("연락처 설정됨", out)
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

    def test_fetch_all_logs_every_10_pages(self):
        c = self.pages(250, lambda p: [store_row(ltShpId=f"{p}-{i}") for i in range(10)])
        logs, now = [], [0.0]

        def clock():
            now[0] += 3
            return now[0]

        stores.fetch_all(c, log=logs.append, clock=clock)
        progress = [m for m in logs if "/25쪽" in m]
        self.assertEqual([m.split("쪽")[0].strip() for m in progress], ["10/25", "20/25", "25/25"])
        self.assertIn("10/25쪽, 경과 0분 ", progress[0])

    def test_fetch_all_failure_says_where(self):
        def routes(path, params):
            if params["pageNum"] == 3:
                return BlockedError("응답 시간 초과", timeout=True)
            return {"data": {"total": 50, "list": [store_row(ltShpId=f"{params['pageNum']}-{i}") for i in range(10)]}}

        logs = []
        with self.assertRaises(BlockedError) as cm:
            stores.fetch_all(RoutedClient(routes), log=logs.append)
        self.assertIn("3/5쪽, 3번째 요청", str(cm.exception))
        self.assertIn("응답 시간 초과", str(cm.exception))
        self.assertTrue(any(m.startswith("실패 지점: 3/5쪽") for m in logs))

    def test_main_log_hides_contact(self):
        c = self.pages(1, lambda p: [store_row()])
        with tempfile.TemporaryDirectory() as d,                 mock.patch.dict("os.environ", {"COLLECTOR_CONTACT": "contact@example.org"}):
            code, log = self.run_main(["--out", str(Path(d) / "stores.json")], c)
        self.assertEqual(code, 0)
        self.assertNotIn("contact@example.org", log)
        self.assertIn("연락처 설정됨", log)

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

    def incremental_client(self):
        """회차 2개: 5808(판매중), 5700(판매종료). 요청 경로·키를 기록한다."""
        ended_item = dict(ITEM_70, ntslWnSn=5700, stEpsd=60, ntslStatus="판매종료")
        seen = []

        def r(path, params):
            seen.append((path, params.get("ntslWnSn") or params.get("srchLtEpsd")))
            if path == rounds.LIST_PATH:
                return {"data": {"total": 2, "list": [ITEM_70, ended_item]}}
            if path == rounds.DETAIL_PATH:
                if params["ntslWnSn"] == 5700:
                    return {"data": {"result": detail_70(ntslStatus="판매종료", stSpmtRt=100, stEpsd=60)}}
                return {"data": {"result": detail_70()}}
            return {"data": {"list": [winner_row()]}}
        client = RoutedClient(r)
        client.seen = seen
        return client

    def first_run(self):
        c = self.incremental_client()
        doc = rounds.collect(c, [STORE], None, log=lambda m: None)
        return doc, c

    def test_first_run_fetches_everything(self):
        doc, c = self.first_run()
        self.assertEqual(c.request_count, 1 + 2 + 2)  # 목록 + 상세 2 + 배출점 2
        self.assertEqual({r["id"]: r["status"] for r in doc["rounds"]}, {5808: "active", 5700: "ended"})

    def test_reuses_ended_rounds_from_previous_file(self):
        prev, _ = self.first_run()
        prev = json.loads(json.dumps(prev))
        ended = next(r for r in prev["rounds"] if r["id"] == 5700)
        ended["winners"][0]["name"] = "지난 파일 값"
        c = self.incremental_client()
        logs = []
        doc = rounds.collect(c, [STORE], None, log=logs.append, prev=prev)
        self.assertEqual(c.request_count, 1 + 1 + 1)  # 목록 + 판매중 상세 + 판매중 배출점
        self.assertNotIn((rounds.DETAIL_PATH, 5700), c.seen)
        self.assertNotIn((rounds.WINNERS_PATH, 60), c.seen)
        reused = next(r for r in doc["rounds"] if r["id"] == 5700)
        self.assertEqual(reused["winners"][0]["name"], "지난 파일 값")
        self.assertTrue(any("판매종료 1개는 지난 파일 값 재사용, 1개 새로 받음" in m for m in logs))
        validate_rounds(doc)

    def test_newly_ended_round_is_fetched(self):
        prev, _ = self.first_run()
        prev = json.loads(json.dumps(prev))
        for r in prev["rounds"]:
            if r["id"] == 5700:  # 지난번엔 아직 판매중이었다
                r.update(status="active", score=50.0, index=1.0, grade="normal")
        c = self.incremental_client()
        rounds.collect(c, [STORE], None, log=lambda m: None, prev=prev)
        self.assertIn((rounds.DETAIL_PATH, 5700), c.seen)
        self.assertEqual(c.request_count, 5)

    def test_reused_winner_leaves_store_list(self):
        prev, _ = self.first_run()
        prev = json.loads(json.dumps(prev))
        other_store = dict(STORE, id="99999999")
        doc = rounds.collect(self.incremental_client(), [other_store], None, log=lambda m: None, prev=prev)
        w = next(r for r in doc["rounds"] if r["id"] == 5700)["winners"][0]
        self.assertEqual((w["storeId"], w["inStoreList"]), (None, False))
        self.assertEqual(w["name"], "CU우면한라점")

    def test_broken_previous_file_means_full_fetch(self):
        c = self.incremental_client()
        logs = []
        rounds.collect(c, [STORE], None, log=logs.append, prev={"rounds": "x"})
        self.assertEqual(c.request_count, 5)
        self.assertTrue(any("전부 새로 받는다" in m for m in logs))

    def test_main_uses_out_file_as_previous(self):
        with tempfile.TemporaryDirectory() as d:
            stores_doc = {"total": 1, "stores": [STORE]}
            code, out, _ = self.run_main(d, self.incremental_client(), stores_doc)
            self.assertEqual(code, 0)
            c = self.incremental_client()
            code, out, log = self.run_main(d, c, stores_doc)
            self.assertEqual(code, 0, log)
            self.assertEqual(c.request_count, 3)
            self.assertIn("걸린 시간", log)

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


class HistoryTest(unittest.TestCase):
    def doc(self, day="2026-11-27", hour="09", **detail_over):
        r = rounds.build_round(ITEM_70, detail_70(**detail_over))
        return {"schemaVersion": 1, "generatedAt": f"{day}T{hour}:00:00+09:00", "stockDate": r["stockDate"], "rounds": [r]}

    def entries(self, root):
        doc = json.loads((Path(root) / "history" / "SP2000_70.json").read_text(encoding="utf-8"))
        validate_history(doc)
        return doc["entries"]

    def test_first_run_writes_one_entry_and_url(self):
        with tempfile.TemporaryDirectory() as d:
            doc = self.doc()
            self.assertEqual(history.update(doc, Path(d), log=lambda m: None), (1, 0))
            self.assertEqual(doc["rounds"][0]["historyUrl"], "history/SP2000_70.json")
            validate_rounds(doc)
            self.assertEqual(self.entries(d), [{"date": "2026-11-27", "status": "active", "score": 72.1, "stockRate": 64,
                                                "rank1Remaining": 4, "rank2Remaining": 14}])

    def test_same_day_overwrites_next_day_appends(self):
        with tempfile.TemporaryDirectory() as d:
            history.update(self.doc(hour="09"), Path(d), log=lambda m: None)
            history.update(self.doc(hour="18", stIvtRnk1Qty=3), Path(d), log=lambda m: None)
            self.assertEqual([(e["date"], e["rank1Remaining"]) for e in self.entries(d)], [("2026-11-27", 3)])
            history.update(self.doc(day="2026-11-28", stSpmtRt=70), Path(d), log=lambda m: None)
            self.assertEqual([(e["date"], e["stockRate"]) for e in self.entries(d)],
                             [("2026-11-27", 64), ("2026-11-28", 70)])

    def test_ended_within_30_days_is_updated_after_that_frozen(self):
        ended = dict(ntslStatus="판매종료", stSpmtRt=100, stNtslEndDt="2026-11-01")
        with tempfile.TemporaryDirectory() as d:
            # 판매종료 30일째(12/01)까지는 기록한다
            self.assertEqual(history.update(self.doc(day="2026-12-01", **ended), Path(d), log=lambda m: None), (1, 0))
            before = (Path(d) / "history" / "SP2000_70.json").read_text(encoding="utf-8")
            doc = self.doc(day="2026-12-02", **ended)
            self.assertEqual(history.update(doc, Path(d), log=lambda m: None), (0, 1))
            self.assertEqual(doc["rounds"][0]["historyUrl"], "history/SP2000_70.json")
            self.assertEqual((Path(d) / "history" / "SP2000_70.json").read_text(encoding="utf-8"), before)

    def test_frozen_without_file_has_no_url(self):
        with tempfile.TemporaryDirectory() as d:
            doc = self.doc(ntslStatus="판매종료", stSpmtRt=100, stNtslEndDt="2026-09-30")
            history.update(doc, Path(d), log=lambda m: None)
            self.assertIsNone(doc["rounds"][0]["historyUrl"])
            self.assertFalse((Path(d) / "history").exists())
            validate_rounds(doc)

    def test_broken_file_starts_over(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "history").mkdir()
            (Path(d) / "history" / "SP2000_70.json").write_text("{broken", encoding="utf-8")
            logs = []
            history.update(self.doc(), Path(d), log=logs.append)
            self.assertEqual(len(self.entries(d)), 1)
            self.assertTrue(any("새로 시작" in m for m in logs))

    def test_contract(self):
        doc = self.doc()
        doc["rounds"][0]["historyUrl"] = "history/SP1000_70.json"
        with self.assertRaises(ContractError):
            validate_rounds(doc)
        good = {"schemaVersion": 1, "id": 1, "product": "SP500", "round": 1, "updatedAt": "2026-11-27T09:00:00+09:00",
                "entries": [{"date": "2026-11-27", "status": "active", "score": 50.0, "stockRate": 1,
                             "rank1Remaining": 1, "rank2Remaining": 1}]}
        validate_history(good)
        for mutate in (lambda h: h["entries"].append(dict(h["entries"][0])),            # 같은 날 두 건
                       lambda h: h["entries"][0].update(status="ended"),                 # ended인데 점수
                       lambda h: h["entries"].clear()):
            h = json.loads(json.dumps(good))
            mutate(h)
            with self.assertRaises(ContractError):
                validate_history(h)

    def test_main_writes_history_next_to_out_and_verify_checks_folder(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, log = RoundsTest().run_main(d, RoundsTest().routes(), {"total": 1, "stores": [STORE]})
            self.assertEqual(code, 0, log)
            self.assertIn("점수 기록: 1개 회차 갱신", log)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["rounds"][0]["historyUrl"], "history/SP2000_70.json")
            with redirect_stdout(io.StringIO()) as buf:
                self.assertEqual(verify.main([str(out), str(Path(d) / "history")]), 0)
            self.assertIn("기록 1건", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
