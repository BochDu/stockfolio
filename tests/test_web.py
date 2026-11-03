"""Web 层测试：密码、会话、限流、payload、登录与接口鉴权（不联网）。"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from stockfolio import auth, market, web

EXAMPLE = Path(__file__).resolve().parent.parent / "example_book.json"
ITERATIONS = 1000


def make_record(password: str = "secret123") -> dict:
    return auth.hash_password(password, iterations=ITERATIONS)


class PasswordTest(unittest.TestCase):
    def test_hash_and_verify(self):
        record = make_record()
        self.assertTrue(auth.verify_password("secret123", record))
        self.assertFalse(auth.verify_password("wrong", record))
        self.assertFalse(auth.verify_password("secret123", {}))
        self.assertFalse(auth.verify_password("secret123", {"algo": "plain"}))

    def test_set_password_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = auth.set_password(
                Path(directory) / "auth.json", password="abcdef", iterations=ITERATIONS
            )
            record = auth.load_auth(path)
            self.assertIsNotNone(record)
            self.assertTrue(auth.verify_password("abcdef", record))
            self.assertFalse(auth.verify_password("abcdefg", record))
            with self.assertRaises(ValueError):
                auth.set_password(path, password="123", iterations=ITERATIONS)

    def test_load_auth_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            for bad in ("{not json", '["x"]', '{"algo": "pbkdf2_sha256"}'):
                path.write_text(bad, encoding="utf-8")
                with self.assertRaises(ValueError):
                    auth.load_auth(path)

    def test_serve_rejects_corrupt_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                web.serve(EXAMPLE, host="127.0.0.1", port=0, auth_file=path)

    def test_clear_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            auth.set_password(path, password="abcdef", iterations=ITERATIONS)
            auth.clear_password(path)
            self.assertFalse(path.exists())
            self.assertIsNone(auth.load_auth(path))
            auth.clear_password(path)  # 幂等：文件不存在也成功


class SessionTest(unittest.TestCase):
    def test_sliding_and_expiry(self):
        sessions = auth.Sessions(ttl=0.02)
        token = sessions.create()
        self.assertTrue(sessions.valid(token))
        time.sleep(0.03)
        self.assertFalse(sessions.valid(token))
        self.assertFalse(sessions.valid(None))
        self.assertFalse(sessions.valid("nope"))

    def test_destroy(self):
        sessions = auth.Sessions()
        token = sessions.create()
        sessions.destroy(token)
        self.assertFalse(sessions.valid(token))


class LimiterTest(unittest.TestCase):
    def test_blocks_after_failures(self):
        limiter = auth.LoginLimiter(max_failures=2, window=60)
        self.assertFalse(limiter.blocked("1.2.3.4"))
        limiter.fail("1.2.3.4")
        limiter.fail("1.2.3.4")
        self.assertTrue(limiter.blocked("1.2.3.4"))
        self.assertFalse(limiter.blocked("5.6.7.8"))
        limiter.reset("1.2.3.4")
        self.assertFalse(limiter.blocked("1.2.3.4"))


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.app = web.App(EXAMPLE, make_record())

    def test_offline_zh(self):
        payload = self.app.payload("zh")
        self.assertEqual(payload["lang"], "zh")
        self.assertEqual(payload["unit"], "w")
        self.assertEqual(payload["labels"]["刷新"], "刷新")
        self.assertEqual(payload["status"]["key"], "initial")
        self.assertIsNotNone(payload["summary"])
        self.assertEqual(payload["summary"]["目标总仓"], 100)
        self.assertEqual(payload["labels"]["目标总仓"], "目标总仓")
        cat = payload["summary"]["大类"][0]
        for key in ("目标仓", "当前仓", "当前占比", "平衡"):
            self.assertIn(key, cat)
        self.assertEqual(len(payload["book"]["大类"]), 3)
        self.assertTrue(payload["enriched"])
        self.assertEqual(len(payload["view"]["参考行"]), 3)
        self.assertFalse(payload["refreshing"])

    def test_offline_en_keeps_logic(self):
        payload = self.app.payload("en")
        self.assertEqual(payload["unit"], "k")
        self.assertEqual(payload["labels"]["刷新"], "Refresh")
        self.assertEqual(payload["labels"]["目标总仓"], "Target Portfolio")
        self.assertEqual(payload["labels"]["平衡"], "Balance")
        names = [row[0] for row in payload["view"]["参考行"]]
        self.assertIn("A-Shares", names)

    def test_default_lang_from_book(self):
        self.assertEqual(self.app.resolve_lang(None), "zh")
        self.assertEqual(self.app.resolve_lang("en"), "en")

    def test_auth_flag(self):
        self.assertTrue(self.app.payload("zh")["auth"])
        self.assertFalse(web.App(EXAMPLE, None).payload("zh")["auth"])

    def test_refresh_watch_only_book_reports_watch(self):
        """没有持仓（数量全为 0）时不算取价失败，状态是 watch。"""
        book = {
            "标题": "watch",
            "本位币": "CNY",
            "大类": [{"名称": "A", "比例": 1.0}],
            "估值": json.loads(EXAMPLE.read_text(encoding="utf-8"))["估值"],
            "持仓": [
                {
                    "标的": "观察",
                    "代码": "600000.SS",
                    "大类": "A",
                    "货币": "CNY",
                    "数量": 0,
                    "成本价": 0,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.json"
            path.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
            app = web.App(path, None)

            def fake_fetch_all(book, opener=None, base=None):
                return {h["代码"]: 9.5 for h in book["持仓"]}, {"CNY": 1.0}

            def fake_holdings(enriched, cfg, opener=None):
                return {
                    "明细": [],
                    "温度": None,
                    "窗口": 250,
                    "代码热度": {"600000.SS": 42.0},
                    "大类热度": [],
                    "样本": "0/0",
                    "跳过": [],
                }

            with mock.patch.object(web, "fetch_all", fake_fetch_all), mock.patch.object(
                web, "benchmark_heats", lambda cfg, opener=None: []
            ), mock.patch.object(web, "holding_heats", fake_holdings):
                self.assertTrue(app.refresh())

            payload = app.payload("zh")
            self.assertEqual(payload["status"]["key"], "watch")
            self.assertIn("无持仓", payload["status"]["text"])
            self.assertIsNone(payload["summary"])
            self.assertEqual(payload["view"]["代码热度"]["600000.SS"], 42.0)

            en = app.payload("en")
            self.assertEqual(en["status"]["key"], "watch")
            self.assertIn("No positions", en["status"]["text"])

    def test_refresh_positions_without_quotes_stays_noquote(self):
        """有持仓但一只都没取到价，仍然是 noquote，不能误报成 watch。"""
        book = {
            "标题": "noquote",
            "本位币": "CNY",
            "大类": [{"名称": "A", "比例": 1.0}],
            "估值": json.loads(EXAMPLE.read_text(encoding="utf-8"))["估值"],
            "持仓": [
                {
                    "标的": "持仓",
                    "代码": "600000.SS",
                    "大类": "A",
                    "货币": "CNY",
                    "数量": 100,
                    "成本价": 8.0,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.json"
            path.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
            app = web.App(path, None)

            def fake_fetch_all(book, opener=None, base=None):
                return {h["代码"]: None for h in book["持仓"]}, {"CNY": 1.0}

            with mock.patch.object(
                web, "fetch_all", fake_fetch_all
            ), mock.patch.object(
                web, "benchmark_heats", lambda cfg, opener=None: []
            ), mock.patch.object(
                web,
                "holding_heats",
                lambda enriched, cfg, opener=None: {
                    "明细": [],
                    "温度": None,
                    "窗口": None,
                    "代码热度": {},
                    "大类热度": [],
                    "样本": "0/1",
                    "跳过": [],
                },
            ):
                self.assertTrue(app.refresh())

            self.assertEqual(app.payload("zh")["status"]["key"], "noquote")

    def test_refresh_pipeline_without_network(self):
        calls = {"fetch": 0}

        def fake_fetch_all(book, opener=None, base=None):
            calls["fetch"] += 1
            quotes = {holding["代码"]: 10.0 for holding in book["持仓"]}
            rates = {currency: 1.0 for currency in market.SUPPORTED_CURRENCIES}
            return quotes, rates

        def fake_benchmarks(cfg, opener=None):
            return [
                {
                    "名称": entry.get("名称"),
                    "代码": entry.get("代码"),
                    "热度": 40.0,
                    "可用": True,
                }
                for entry in cfg["参考基准"]
            ]

        def fake_holdings(enriched, cfg, opener=None):
            return {
                "明细": [],
                "温度": 55.0,
                "窗口": 250,
                "代码热度": {},
                "大类热度": [],
                "样本": "0/0",
                "跳过": [],
            }

        with mock.patch.object(web, "fetch_all", fake_fetch_all), mock.patch.object(
            web, "benchmark_heats", fake_benchmarks
        ), mock.patch.object(web, "holding_heats", fake_holdings):
            self.assertTrue(self.app.refresh())
            zh = self.app.payload("zh")
            en = self.app.payload("en")

        self.assertEqual(calls["fetch"], 1)  # 切语言不得重新联网
        self.assertEqual(zh["status"]["key"], "ok")
        self.assertIn("报价", zh["status"]["text"])
        self.assertIsNotNone(zh["view"]["建议"])
        self.assertNotEqual(zh["heat_reading"], "—")
        self.assertEqual(en["labels"]["现金建议"], "Cash Target")


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = web.App(EXAMPLE, make_record())
        cls.server = web.StockfolioServer(("127.0.0.1", 0), cls.app)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def login(self, password="secret123"):
        body = urlencode({"password": password})
        status, headers, _ = self.request(
            "POST",
            "/login",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        return status, headers.get("Set-Cookie", "").split(";")[0]

    def test_api_requires_login(self):
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(status, 401)
        self.assertIn("error", json.loads(body))

    def test_root_redirects_to_login(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/login")

    def test_root_redirect_keeps_explicit_language(self):
        status, headers, _ = self.request("GET", "/?lang=en")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/login?lang=en")
        status, headers, _ = self.request("GET", "/?lang=fr")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/login")

    def test_login_redirect_keeps_language_when_authorized(self):
        _, cookie = self.login()
        status, headers, _ = self.request(
            "GET", "/login?lang=en", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/?lang=en")
        status, headers, _ = self.request("GET", "/login", headers={"Cookie": cookie})
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/")

    def test_login_page_and_static(self):
        status, headers, body = self.request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn(b"login-card", body)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

        status, headers, _ = self.request("GET", "/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", ""))

        status, _, body = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"renderSummary", body)

    def test_bad_login_redirects_with_error(self):
        status, headers, _ = self.request(
            "POST",
            "/login",
            urlencode({"password": "wrong"}),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/login?e=bad")

    def test_login_page_language_toggle(self):
        status, _, body = self.request("GET", "/login")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("输入访问密码", html)
        self.assertIn('href="/login?lang=en"', html)

        status, _, body = self.request("GET", "/login?lang=en")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn('<html lang="en">', html)
        self.assertIn("Enter your access password", html)
        self.assertIn('href="/login?lang=zh"', html)
        self.assertIn('action="/login?lang=en"', html)

        status, _, body = self.request("GET", "/login?lang=fr")
        self.assertEqual(status, 200)
        self.assertIn("输入访问密码", body.decode("utf-8"))

    def test_login_language_does_not_leak_to_app(self):
        status, _, _ = self.request("GET", "/login?lang=en")
        self.assertEqual(status, 200)
        self.assertEqual(self.app.payload()["lang"], "zh")

    def test_login_error_keeps_language(self):
        status, headers, _ = self.request(
            "POST",
            "/login?lang=en",
            urlencode({"password": "wrong"}),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/login?e=bad&lang=en")

        _, _, body = self.request("GET", "/login?e=bad&lang=en")
        html = body.decode("utf-8")
        self.assertIn("Incorrect password", html)
        self.assertIn('href="/login?lang=zh&amp;e=bad"', html)

    def test_login_success_carries_language(self):
        status, headers, _ = self.request(
            "POST",
            "/login?lang=en",
            urlencode({"password": "secret123"}),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/?lang=en")

    def test_logout_keeps_language(self):
        _, cookie = self.login()
        status, headers, _ = self.request(
            "POST", "/logout?lang=en", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/login?lang=en")

    def test_login_state_and_language(self):
        status, cookie = self.login()
        self.assertEqual(status, 303)
        self.assertIn("sid=", cookie)

        status, _, body = self.request("GET", "/api/state", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["lang"], "zh")

        status, _, body = self.request(
            "GET", "/api/state?lang=en", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["lang"], "en")

    def test_root_serves_app_when_logged_in(self):
        _, cookie = self.login()
        status, _, body = self.request("GET", "/", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b'id="app"', body)

    def test_refresh_requires_custom_header(self):
        _, cookie = self.login()
        status, _, _ = self.request(
            "POST", "/api/refresh", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 403)  # 未带 X-Stockfolio，拒绝（不触发联网）

    def test_logout_invalidates_session(self):
        _, cookie = self.login()
        status, headers, _ = self.request(
            "POST", "/logout", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 303)
        self.assertIn("Max-Age=0", headers.get("Set-Cookie", ""))
        status, _, _ = self.request("GET", "/api/state", headers={"Cookie": cookie})
        self.assertEqual(status, 401)


class NoAuthServerTest(unittest.TestCase):
    """未设置密码：不跳登录页，直接进入。"""

    @classmethod
    def setUpClass(cls):
        cls.app = web.App(EXAMPLE, None)
        cls.server = web.StockfolioServer(("127.0.0.1", 0), cls.app)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_root_serves_app_directly(self):
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="app"', body)

    def test_api_state_usable_without_login(self):
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["auth"])

    def test_login_page_redirects_home(self):
        status, headers, _ = self.request("GET", "/login")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/")

    def test_logout_returns_home(self):
        status, headers, _ = self.request("POST", "/logout")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/")


if __name__ == "__main__":
    unittest.main()
