"""行情与历史数据获取：新浪实时报价/汇率、新浪与腾讯日线。仅用标准库。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

SINA_HQ = "https://hq.sinajs.cn/list={symbols}"
UA = "Mozilla/5.0 (compatible; stockfolio/1.0)"
SINA_HEADERS = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}

# 新浪外汇：X 兑人民币（1 单位 X = 多少 CNY）。覆盖外汇板块全部常见币种，
# 汇率按「本位币」口径统一换算；CNY 恒为 1.0。
FX_CODE = {
    "CNY": None,
    "USD": "fx_susdcny",
    "HKD": "fx_shkdcny",
    "JPY": "fx_sjpycny",
    "EUR": "fx_seurcny",
    "GBP": "fx_sgbpcny",
    "AUD": "fx_saudcny",
    "CAD": "fx_scadcny",
    "CHF": "fx_schfcny",
    "SGD": "fx_ssgdcny",
    "NZD": "fx_snzdcny",
    "KRW": "fx_skrwcny",
    "TWD": "fx_stwdcny",
    "THB": "fx_sthbcny",
    "MOP": "fx_smopcny",
    "MYR": "fx_smyrcny",
    "RUB": "fx_srubcny",
    "INR": "fx_sinrcny",
    "IDR": "fx_sidrcny",
    "PHP": "fx_sphpcny",
    "DKK": "fx_sdkkcny",
    "SEK": "fx_ssekcny",
    "NOK": "fx_snokcny",
    "ZAR": "fx_szarcny",
}
SUPPORTED_CURRENCIES = tuple(FX_CODE)
FX_SYMBOLS = [sym for sym in FX_CODE.values() if sym]

SINA_KLINE = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}"
)
QQ_KLINE = (
    "https://web.ifzq.gtimg.cn/appstock/app/{path}/get"
    "?param={symbol},day,,,{count},qfq"
)
QQ_HEADERS = {"User-Agent": UA, "Referer": "https://gu.qq.com"}


def _default_open(req, timeout=8):
    return urllib.request.urlopen(req, timeout=timeout)


# ---------- 实时报价与汇率 ----------


def code_to_sina(code: str) -> str | None:
    """把 JSON 里的代码（如 600036.SS / 0700.HK / VOO）映射到新浪 list 符号。"""
    if not code:
        return None
    raw = code.strip()
    upper = raw.upper()
    if upper.endswith("CNY=X") or upper in ("HKDCNY", "USDCNY"):
        return FX_CODE.get(upper[:3])
    if upper.endswith(".SS"):
        return "sh" + raw[:-3]
    if upper.endswith(".SZ"):
        return "sz" + raw[:-3]
    if upper.endswith(".HK"):
        return "rt_hk" + raw[:-3].zfill(5)
    compact = upper.replace("-", "").replace(".", "")
    if compact == "BRKB":
        return "gb_brkb"
    return "gb_" + raw.lower().replace("-", "").replace(".", "")


def _price_from_sina_fields(sina_sym: str, fields: list[str]) -> float | None:
    if not fields or fields == [""]:
        return None
    try:
        if sina_sym.startswith("fx_"):
            return (float(fields[1]) + float(fields[2])) / 2.0
        if sina_sym.startswith("rt_hk"):
            return float(fields[6])
        if sina_sym.startswith("gb_"):
            price = float(fields[1])
            return price if price > 0 else None
        return float(fields[3])
    except (IndexError, ValueError):
        return None


def _parse_sina_hq(text: str) -> dict[str, float | None]:
    parsed: dict[str, float | None] = {}
    for line in text.splitlines():
        match = re.match(r'var hq_str_([^=]+)="(.*)";?\s*$', line.strip())
        if not match:
            continue
        sym, payload = match.group(1), match.group(2)
        fields = payload.split(",")
        parsed[sym] = _price_from_sina_fields(sym, fields)
    return parsed


def fetch_sina_list(
    sina_symbols: list[str], opener=_default_open, timeout: float = 8
) -> dict[str, float | None]:
    """批量拉新浪报价/汇率；单只失败不拖垮整体。"""
    uniq = list(dict.fromkeys(s for s in sina_symbols if s))
    if not uniq:
        return {}
    url = SINA_HQ.format(symbols=",".join(uniq))
    req = urllib.request.Request(url, headers=SINA_HEADERS)
    blank = {sym: None for sym in uniq}
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read()
        text = raw.decode("gbk", errors="replace")
        parsed = _parse_sina_hq(text)
        return {sym: parsed.get(sym) for sym in uniq}
    except (
        OSError,
        urllib.error.URLError,
        ValueError,
        TypeError,
        UnicodeError,
    ):
        return blank


def fetch_quote(code: str, opener=_default_open) -> float | None:
    sina = code_to_sina(code)
    if not sina:
        return None
    return fetch_sina_list([sina], opener=opener).get(sina)


def resolve_base(book: dict) -> str:
    """持仓 JSON 的「本位币」（基准币种），缺省 CNY。"""
    base = str(book.get("本位币") or "CNY").upper()
    if base not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"本位币不支持: {base}（仅支持 {', '.join(SUPPORTED_CURRENCIES)}）"
        )
    return base


def _rates_in_base(to_cny: dict[str, float | None], base: str) -> dict[str, float | None]:
    """把「1 单位币种 = X 人民币」换算成「1 单位币种 = X 本位币」。

    本位币本身恒为 1.0；本位币兑人民币汇率缺失时，其他币种一律不可折算（None），
    避免用错误口径算钱——只有本位币持仓仍可汇总。
    """
    base_rate = to_cny.get(base)
    if base_rate is None or base_rate <= 0:
        return {ccy: (1.0 if ccy == base else None) for ccy in to_cny}
    return {
        ccy: (v / base_rate if v is not None else None)
        for ccy, v in to_cny.items()
    }


def fetch_rates(
    opener=_default_open, base: str | None = None
) -> dict[str, float | None]:
    """拉全部外汇（X 兑人民币）后换算到本位币口径；CNY 恒为基准。"""
    prices = fetch_sina_list(FX_SYMBOLS, opener=opener)
    to_cny = {
        ccy: (1.0 if sym is None else prices.get(sym))
        for ccy, sym in FX_CODE.items()
    }
    return _rates_in_base(to_cny, (base or "CNY").upper())


def _holding_needs_network_quote(h: dict) -> bool:
    return float(h["数量"]) > 0


def fetch_all(
    book: dict, opener=_default_open, base: str | None = None
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """按持仓清单批量取报价与汇率（换算到本位币）；零数量且未手填现价的标的记 None。"""
    base = base or resolve_base(book)
    codes_to_fetch = {
        h["代码"] for h in book["持仓"] if _holding_needs_network_quote(h)
    }
    code_sina = {code: code_to_sina(code) for code in codes_to_fetch}
    sina_syms = [sym for sym in code_sina.values() if sym]
    prices = fetch_sina_list([*sina_syms, *FX_SYMBOLS], opener=opener)

    quotes: dict[str, float | None] = {
        code: prices.get(sym) if sym else None for code, sym in code_sina.items()
    }
    for h in book["持仓"]:
        code = h["代码"]
        if code in quotes:
            continue
        if float(h["数量"]) == 0 and "现价" not in h:
            quotes[code] = None

    rates = _rates_in_base(
        {
            ccy: (1.0 if sym is None else prices.get(sym))
            for ccy, sym in FX_CODE.items()
        },
        base,
    )
    return quotes, rates


def offline_quotes_rates(
    book: dict, base: str | None = None
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """启动时不联网：仅占位；手填「现价」仍可由 enrich 使用（仅本位币口径可用）。"""
    base = base or resolve_base(book)
    quotes = {h["代码"]: None for h in book["持仓"]}
    rates: dict[str, float | None] = {
        ccy: (1.0 if ccy == base else None) for ccy in SUPPORTED_CURRENCIES
    }
    return quotes, rates


# ---------- 日线历史 ----------


def kline_symbol(code: str) -> str | None:
    """持仓代码 → 日线符号。A股走新浪，港股/美股走腾讯。"""
    if not code:
        return None
    raw = code.strip()
    upper = raw.upper()
    if upper in ("HKDCNY=X", "HKDCNY", "USDCNY=X", "USDCNY"):
        return None
    if upper.endswith(".SS"):
        return "sh" + raw[:-3]
    if upper.endswith(".SZ"):
        return "sz" + raw[:-3]
    if upper.endswith(".HK"):
        return "hk" + raw[:-3].zfill(5)
    return "us" + raw


def fetch_kline(
    symbol: str, count: int = 1200, opener=_default_open
) -> list[list]:
    """日线历史：[[日期, 收盘, 最高, 成交额], ...] 升序。失败返回 []。"""
    if not symbol:
        return []
    if symbol.startswith(("sh", "sz")):
        url = SINA_KLINE.format(symbol=symbol, count=count)
        headers = SINA_HEADERS
        source = "sina"
    elif symbol.startswith("hk"):
        url = QQ_KLINE.format(path="fqkline", symbol=symbol, count=count)
        headers = QQ_HEADERS
        source = "qq"
    elif symbol.startswith("us"):
        url = QQ_KLINE.format(path="usfqkline", symbol=symbol, count=count)
        headers = QQ_HEADERS
        source = "qq"
    else:
        return []

    req = urllib.request.Request(url, headers=headers)
    try:
        with opener(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (OSError, urllib.error.URLError, ValueError, TypeError, UnicodeError):
        return []

    out: list[list] = []
    if source == "sina":
        for row in payload if isinstance(payload, list) else []:
            try:
                close = float(row["close"])
                high = float(row["high"])
                volume = float(row["volume"])
            except (KeyError, TypeError, ValueError):
                continue
            if close > 0:
                out.append([str(row.get("day", "")), close, high, volume])
        return out

    data = (payload or {}).get("data") or {}
    rows = (data.get(symbol) or {}).get("day") or []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            close = float(row[2])
            high = float(row[3])
            volume = float(row[5])
        except (IndexError, ValueError):
            continue
        if close > 0:
            out.append([row[0], close, high, volume])
    return out


def fetch_klines(
    symbols: list[str],
    count: int = 1200,
    opener=_default_open,
    workers: int = 6,
) -> dict[str, list[list]]:
    """并发拉多条日线，避免逐只串行等待。"""
    targets = list(dict.fromkeys(s for s in symbols if s))
    if not targets:
        return {}
    results: dict[str, list[list]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
        futures = {
            pool.submit(fetch_kline, symbol, count, opener): symbol
            for symbol in targets
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception:  # noqa: BLE001 — 单只失败不影响整体
                results[symbol] = []
    return results
