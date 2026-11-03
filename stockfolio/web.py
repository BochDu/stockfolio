"""零依赖 Web 服务：密码登录 + 本机/局域网浏览器访问。

只负责传输与鉴权，行情和分析仍复用 market/analysis 的现有函数；
Tk 桌面界面不受影响，两种入口共用同一套数据管道。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import i18n
from .analysis import (
    _counts_in_quote_status,
    benchmark_heats,
    enrich_holdings,
    heat_reading,
    holding_heats,
    summarize,
    valuation_view,
)
from .auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    LoginLimiter,
    Sessions,
    default_auth_file,
    hash_password,
    load_auth,
    verify_password,
)
from .config import valuation_config
from .i18n import LANG_EN, LANG_ZH, default_lang, status_text, t, term, unit_w
from .market import fetch_all, offline_quotes_rates

STATIC_DIR = Path(__file__).resolve().parent / "web_static"
STATIC_FILES = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
}
LOGIN_TEXT = {
    "zh": {
        "html_lang": "zh-CN",
        "title": "登录",
        "heading": "登录",
        "hint": "输入访问密码查看个人持仓",
        "placeholder": "访问密码",
        "submit": "登录",
        "toggle": "English",
        "errors": {
            "bad": "密码错误，请重试",
            "locked": "尝试次数过多，请稍后再试",
            "expired": "登录已过期，请重新登录",
        },
    },
    "en": {
        "html_lang": "en",
        "title": "Sign in",
        "heading": "Sign in",
        "hint": "Enter your access password to view your portfolio",
        "placeholder": "Access password",
        "submit": "Sign in",
        "toggle": "中文",
        "errors": {
            "bad": "Incorrect password, please try again",
            "locked": "Too many attempts, please try again later",
            "expired": "Session expired, please sign in again",
        },
    },
}
MAX_BODY = 4096


class App:
    """持有持仓快照、会话与密码记录；所有网络请求线程共享一个实例。"""

    def __init__(self, book_path, auth_record: dict | None):
        self.book_path = Path(book_path)
        self.auth_record = auth_record
        self.sessions = Sessions()
        self.limiter = LoginLimiter()
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._refreshing = False
        self._state = self._offline_state()

    # ---------- 数据管道 ----------

    def _load(self) -> dict:
        from . import load_book

        return load_book(self.book_path)

    def _offline_state(self) -> dict:
        book = self._load()
        cfg = valuation_config(book)
        quotes, rates = offline_quotes_rates(book)
        enriched = enrich_holdings(book, quotes, rates)
        references = [
            {"名称": str(entry.get("名称") or entry.get("代码") or ""), "热度": None, "可用": False}
            for entry in cfg["参考基准"]
        ]
        return {
            "book": book,
            "quotes": quotes,
            "rates": rates,
            "enriched": enriched,
            "references": references,
            "holdings": None,
            "cash_rules": cfg["现金规则"],
            "status": {"key": "initial", "params": {}},
            "fetched_at": None,
        }

    def resolve_lang(self, requested: str | None = None) -> str:
        if requested in (LANG_ZH, LANG_EN):
            return requested
        with self._lock:
            book = self._state["book"]
        return default_lang(book)

    def payload(self, lang: str | None = None) -> dict:
        """把当前快照整理成前端可直接渲染的 JSON；切语言不重新联网。"""
        lang = self.resolve_lang(lang)
        with self._lock:
            state = self._state
            refreshing = self._refreshing
            book = state["book"]
            enriched = state["enriched"]
            rates = state["rates"]
            references = state["references"]
            holdings = state["holdings"]
            cash_rules = state["cash_rules"]
            status = state["status"]
            fetched_at = state["fetched_at"]

        summary = summarize(book, enriched, rates, lang)
        view = valuation_view(references, holdings, summary, cash_rules, lang)
        view["参考行"] = [
            [term(name, lang), heat_text, reading]
            for name, heat_text, reading in view.get("参考行") or []
        ]
        view["大类热度"] = [
            {**item, "读数": heat_reading(item.get("热度"), lang)}
            for item in view.get("大类热度") or []
        ]

        return {
            "lang": lang,
            "title": book.get("标题") or t("股票持仓", lang),
            "base": str(book.get("本位币") or "CNY"),
            "unit": unit_w(lang),
            "labels": {key: t(key, lang) for key in i18n.UI},
            "status": {
                "key": status["key"],
                "text": status_text(status["key"], lang, **(status.get("params") or {})),
            },
            "summary": summary,
            "view": view,
            "heat_reading": heat_reading(view.get("温度"), lang),
            "enriched": enriched,
            "book": {"大类": book.get("大类") or []},
            "auth": self.auth_record is not None,
            "refreshing": refreshing,
            "updated": fetched_at,
        }

    def refresh(self) -> bool:
        """联网刷新；已有刷新在执行时立即返回 False（调用方回 202）。"""
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            with self._lock:
                self._refreshing = True
            try:
                book = self._load()
                cfg = valuation_config(book)
                quotes, rates = fetch_all(book)
                enriched = enrich_holdings(book, quotes, rates)
                references = benchmark_heats(cfg)
                holdings = holding_heats(enriched, cfg)
                summary = summarize(book, enriched, rates)
                ok = sum(1 for row in enriched if row["可汇总"])
                fail = sum(
                    1
                    for row in enriched
                    if _counts_in_quote_status(row) and not row["可汇总"]
                )
                positions = [
                    row for row in enriched if float(row["数量"]) > 0
                ]
                if summary is None:
                    # 没有持仓（全是数量 0 的标的）不算「取价失败」：
                    # 行情与温度可能都拿到了，只是没有仓位可汇总
                    status = {"key": "watch" if not positions else "noquote", "params": {}}
                else:
                    status = {"key": "ok", "params": {"ok": ok, "fail": fail}}
                with self._lock:
                    self._state = {
                        "book": book,
                        "quotes": quotes,
                        "rates": rates,
                        "enriched": enriched,
                        "references": references,
                        "holdings": holdings,
                        "cash_rules": cfg["现金规则"],
                        "status": status,
                        "fetched_at": time.time(),
                    }
            except Exception as exc:  # noqa: BLE001 — 失败保留旧快照，只改状态
                with self._lock:
                    failed = dict(self._state)
                    failed["status"] = {"key": "error", "params": {"msg": str(exc)}}
                    self._state = failed
            finally:
                with self._lock:
                    self._refreshing = False
            return True
        finally:
            self._refresh_lock.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "stockfolio"
    sys_version = ""

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 — 保持控制台干净
        pass

    # ---------- 响应工具 ----------

    def _send(self, status: int, body: bytes | str, content_type: str, extra=None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _html(self, status: int, html: str, extra=None) -> None:
        self._send(
            status,
            html,
            "text/html; charset=utf-8",
            {"Cache-Control": "no-store", **(extra or {})},
        )

    def _json(self, status: int, data: dict, extra=None) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self._send(
            status,
            body,
            "application/json; charset=utf-8",
            {"Cache-Control": "no-store", **(extra or {})},
        )

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _redirect(self, location: str, extra=None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _close_redirect(self, location: str) -> None:
        """请求体未读取时关闭连接，避免残留字节污染下一次 keep-alive 请求。"""
        self.close_connection = True
        self._redirect(location, {"Connection": "close"})

    # ---------- 会话与安全 ----------

    def _token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 — 畸形 Cookie 视为未登录
            return None
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _authorized(self) -> bool:
        if self.app.auth_record is None:  # 未设密码：直接放行
            return True
        return self.app.sessions.valid(self._token())

    def _secure_cookie(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _session_cookie(self, token: str) -> str:
        parts = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={SESSION_TTL}",
        ]
        if self._secure_cookie():
            parts.append("Secure")
        return "; ".join(parts)

    def _cleared_cookie(self) -> str:
        return f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"

    # ---------- 路由 ----------

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler 约定
        url = urlsplit(self.path)
        path = url.path
        try:
            if path == "/healthz":
                return self._send(200, "ok", "text/plain; charset=utf-8")
            if path in STATIC_FILES:
                return self._static(path)
            if path == "/login":
                return self._login_page(url.query)
            if path == "/":
                if not self._authorized():
                    lang = (parse_qs(url.query).get("lang") or [""])[0]
                    target = f"/login?lang={lang}" if lang in LOGIN_TEXT else "/login"
                    return self._redirect(target)
                return self._index_page()
            if path == "/logout":
                return self._logout()
            if path == "/api/state":
                return self._api_state(url.query)
            return self._error(404, "not found")
        except Exception:  # noqa: BLE001 — 统一 500，不泄露堆栈
            return self._error(500, "internal error")

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        try:
            if url.path == "/login":
                return self._login_submit()
            if url.path == "/logout":
                return self._logout()
            if url.path == "/api/refresh":
                return self._api_refresh(url.query)
            return self._error(404, "not found")
        except Exception:  # noqa: BLE001
            return self._error(500, "internal error")

    # ---------- 页面 ----------

    def _static(self, path: str) -> None:
        name, content_type = STATIC_FILES[path]
        try:
            data = (STATIC_DIR / name).read_bytes()
        except OSError:
            return self._error(404, "not found")
        self._send(200, data, content_type, {"Cache-Control": "no-cache"})

    def _index_page(self) -> None:
        try:
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            return self._error(404, "not found")
        self._html(200, html)

    def _login_page(self, query: str) -> None:
        if self._authorized():
            lang = (parse_qs(query).get("lang") or [""])[0]
            return self._redirect(f"/?lang={lang}" if lang in LOGIN_TEXT else "/")
        params = parse_qs(query)
        code = (params.get("e") or [""])[0]
        lang = self.app.resolve_lang((params.get("lang") or [""])[0] or None)
        text = LOGIN_TEXT[lang]
        message = text["errors"].get(code, "")
        other = "zh" if lang == "en" else "en"
        toggle_href = f"/login?lang={other}"
        if message:
            toggle_href += f"&amp;e={code}"
        try:
            html = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
        except OSError:
            return self._error(404, "not found")
        replacements = {
            "{{lang}}": text["html_lang"],
            "{{title}}": text["title"],
            "{{heading}}": text["heading"],
            "{{hint}}": text["hint"],
            "{{placeholder}}": text["placeholder"],
            "{{submit}}": text["submit"],
            "{{toggle_label}}": text["toggle"],
            "{{toggle_href}}": toggle_href,
            "{{action}}": f"/login?lang={lang}",
            "{{error}}": f'<p class="err">{message}</p>' if message else "",
        }
        for placeholder, value in replacements.items():
            html = html.replace(placeholder, value)
        self._html(200, html)

    # ---------- 认证 ----------

    def _login_submit(self) -> None:
        if self.app.auth_record is None:  # 未设密码：无需登录
            return self._close_redirect("/")
        params = parse_qs(urlsplit(self.path).query)
        lang = (params.get("lang") or [""])[0]
        suffix = f"&lang={lang}" if lang in LOGIN_TEXT else ""
        ip = self.client_address[0]
        if self.app.limiter.blocked(ip):
            return self._close_redirect(f"/login?e=locked{suffix}")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._close_redirect(f"/login?e=bad{suffix}")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        password = (parse_qs(raw).get("password") or [""])[0]
        if not verify_password(password, self.app.auth_record):
            self.app.limiter.fail(ip)
            return self._redirect(f"/login?e=bad{suffix}")
        self.app.limiter.reset(ip)
        token = self.app.sessions.create()
        target = f"/?lang={lang}" if lang in LOGIN_TEXT else "/"
        return self._redirect(target, {"Set-Cookie": self._session_cookie(token)})

    def _logout(self) -> None:
        self.app.sessions.destroy(self._token())
        lang = (parse_qs(urlsplit(self.path).query).get("lang") or [""])[0]
        target = "/login" if self.app.auth_record is not None else "/"
        if self.app.auth_record is not None and lang in LOGIN_TEXT:
            target = f"/login?lang={lang}"
        return self._redirect(target, {"Set-Cookie": self._cleared_cookie()})

    # ---------- API ----------

    def _api_state(self, query: str) -> None:
        if not self._authorized():
            return self._error(401, "unauthorized")
        lang = (parse_qs(query).get("lang") or [None])[0]
        return self._json(200, self.app.payload(lang))

    def _api_refresh(self, query: str) -> None:
        if not self._authorized():
            return self._error(401, "unauthorized")
        if (self.headers.get("X-Stockfolio") or "") != "1":
            return self._error(403, "missing request header")
        lang = (parse_qs(query).get("lang") or [None])[0]
        started = self.app.refresh()
        payload = self.app.payload(lang)
        if not started:
            return self._json(202, {**payload, "refreshing": True})
        return self._json(200, payload)


class StockfolioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app: App):
        super().__init__(address, Handler)
        self.app = app


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def serve(
    book_path,
    host: str = "127.0.0.1",
    port: int = 8000,
    auth_file=None,
    password: str | None = None,
) -> None:
    """启动 Web 服务。默认只绑本机；--host 0.0.0.0 开放局域网。"""
    path = Path(auth_file) if auth_file else default_auth_file()
    record = load_auth(path)
    if record is None:
        secret = password or os.environ.get("STOCKFOLIO_PASSWORD")
        if secret:
            record = hash_password(secret)

    app = App(book_path, record)
    try:
        server = StockfolioServer((host, port), app)
    except OSError as exc:
        # 端口被占用 / 地址不可绑定时给一句人话，别抛 traceback
        raise SystemExit(f"启动失败：{host}:{port} 无法监听（{exc}）") from exc
    lines = [f"stockfolio web: http://127.0.0.1:{port}" if host in ("127.0.0.1", "localhost") else f"stockfolio web: http://{host}:{port}"]
    if record is None:
        lines.append("未设置密码：打开即进入，无需登录。设置密码请运行 --set-password。")
    if host in ("0.0.0.0", ""):
        ip = _lan_ip()
        if ip:
            lines.append(f"局域网访问: http://{ip}:{port}")
        if record is None:
            lines.append("警告：未设密码且开放局域网，同网设备均可直接查看。")
        lines.append("提示：HTTP 仅适合本机/可信局域网；公网请置于 HTTPS 反向代理或隧道之后。")
    print("\n".join(lines), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
