"""stockfolio —— 股票仓位温度计（零第三方依赖，Python 标准库 + Tkinter）。

用法：
    python -m stockfolio --book path/to/stock.json
或代码内：
    import stockfolio
    stockfolio.run_book("path/to/stock.json")

数据文件是普通 JSON：标的、代码、大类、货币、数量、成本价，
外加「本位币」（24 种货币任选，缺省 CNY，见 market.FX_CODE）与
「估值.现金规则」（自由定义温度 → 建议现金比例的锚点，必填，见 README）。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import analysis, config, i18n, market
from .analysis import (
    enrich_holdings,
    holding_heats,
    benchmark_heats,
    ladder_plan,
    summarize,
    valuation_view,
)
from .config import (
    DEFAULT_VALUATION,
    cash_rules,
    valuation_config,
)
from .market import (
    fetch_all,
    fetch_klines,
    fetch_quote,
    fetch_rates,
    fetch_sina_list,
    offline_quotes_rates,
    resolve_base,
)
from .validate import validate_book, validate_file

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_VALUATION",
    "analysis",
    "cash_rules",
    "check_book",
    "clear_password",
    "config",
    "enrich_holdings",
    "fetch_all",
    "fetch_klines",
    "fetch_quote",
    "fetch_rates",
    "fetch_sina_list",
    "holding_heats",
    "benchmark_heats",
    "i18n",
    "ladder_plan",
    "load_book",
    "market",
    "offline_quotes_rates",
    "resolve_base",
    "run_book",
    "serve",
    "set_password",
    "show",
    "smoke",
    "summarize",
    "validate_book",
    "validate_file",
    "valuation_config",
    "valuation_view",
]


def show(*args, **kwargs):
    """打开 Tk 界面。延迟导入，缺少 tkinter 的服务器也能启动 Web 模式。"""
    from .gui import show as _show

    return _show(*args, **kwargs)


def serve(*args, **kwargs):
    """启动 Web 服务。延迟导入，避免引入 Tkinter 依赖。"""
    from .web import serve as _serve

    return _serve(*args, **kwargs)


def set_password(*args, **kwargs):
    """设置访问密码（写入密码文件）。"""
    from .auth import set_password as _set_password

    return _set_password(*args, **kwargs)


def clear_password(*args, **kwargs):
    """关闭访问密码（删除密码文件）。"""
    from .auth import clear_password as _clear_password

    return _clear_password(*args, **kwargs)


def load_book(path) -> dict:
    """读取并校验持仓 JSON。"""
    with Path(path).open(encoding="utf-8") as f:
        book = json.load(f)
    if not book.get("大类"):
        raise ValueError(f"{path} 缺少大类")
    if "持仓" not in book:
        raise ValueError(f"{path} 缺少持仓")
    names = {c["名称"] for c in book["大类"]}
    base = str(book.get("本位币") or "CNY").upper()
    if base not in market.SUPPORTED_CURRENCIES:
        raise ValueError(
            f"本位币不支持: {base}（仅支持 {', '.join(market.SUPPORTED_CURRENCIES)}）"
        )
    book["本位币"] = base
    cash = book.get("现金")
    if isinstance(cash, dict):
        book["现金"] = {str(ccy).upper(): amount for ccy, amount in cash.items()}
    for h in book["持仓"]:
        for key in ("标的", "代码", "大类", "货币", "数量", "成本价"):
            if key not in h:
                raise ValueError(f"持仓缺少字段 {key}: {h}")
        if h["大类"] not in names:
            raise ValueError(f"未知大类 {h['大类']}: {h}")
        h["货币"] = str(h["货币"]).upper()
        h["数量"] = float(h["数量"])
        h["成本价"] = float(h["成本价"])
        if "现价" in h and h["现价"] is not None:
            h["现价"] = float(h["现价"])
    return book


def smoke(path) -> dict:
    """只跑离线数据管道（不联网、不开窗口），返回摘要与配置，便于自查/CI。"""
    book = load_book(path)
    quotes, rates = offline_quotes_rates(book)
    enriched = enrich_holdings(book, quotes, rates)
    summary = summarize(book, enriched, rates)
    cfg = valuation_config(book)
    plan = ladder_plan(42.0, cfg["现金规则"])  # 示例温度，验证现金规则可用
    return {
        "标题": book.get("标题"),
        "估值配置": {
            "回看交易日": cfg["回看交易日"],
            "均线窗口": cfg["均线窗口"],
            "现金规则": cfg["现金规则"],
        },
        "摘要": summary,
        "示例建议(温度42)": plan,
    }


def check_book(path, opener=None) -> dict:
    """校验持仓 JSON，并探测行情、汇率、日线接口（需联网）。"""
    problems = validate_file(path)
    result = {
        "格式": {"ok": not problems, "错误": problems},
        "接口": [],
        "ok": False,
    }
    if problems:
        return result

    book = load_book(path)
    base = market.resolve_base(book)
    ma_window = valuation_config(book)["均线窗口"]
    kwargs = {} if opener is None else {"opener": opener}
    quotes, rates = market.fetch_all(book, **kwargs)

    need = [h for h in book["持仓"] if float(h["数量"]) > 0]
    priced = sum(1 for h in need if quotes.get(h["代码"]) is not None)
    quote_ok = priced == len(need)
    quote_detail = "无持仓，跳过" if not need else f"{priced}/{len(need)} 只取价成功"

    used = {h["货币"] for h in book["持仓"]}
    used.update((book.get("现金") or {}).keys())
    used.discard(base)
    missing = sorted(ccy for ccy in used if rates.get(ccy) is None)
    canary = "USD" if base != "USD" else "CNY"
    if missing:
        rate_ok = False
        rate_detail = "缺少汇率: " + ", ".join(missing)
    elif used:
        rate_ok = rates.get(canary) is not None
        rate_detail = (
            f"可用（本位币 {base}）" if rate_ok else "接口不可用"
        )
    else:
        rate_ok = True  # 没有外币敞口，不需要汇率
        rate_detail = f"无外币敞口（本位币 {base}）"

    symbols = sorted(
        {
            symbol
            for h in need
            if (symbol := market.kline_symbol(h["代码"]))
        }
    )
    klines = market.fetch_klines(
        symbols, count=ma_window + 1, **kwargs
    )
    rows = {sym: len(klines.get(sym) or []) for sym in symbols}
    # 温度计算需要完整均线窗口；只探测到 1 行不算成功
    kline_ok = all(count >= ma_window for count in rows.values())
    kline_detail = (
        "无日线标的，跳过"
        if not rows
        else ", ".join(
            f"{sym} {count}/{ma_window} 行" for sym, count in rows.items()
        )
    )

    result["接口"] = [
        {"名称": "行情", "ok": quote_ok, "说明": quote_detail},
        {"名称": "汇率", "ok": rate_ok, "说明": rate_detail},
        {"名称": "日线", "ok": kline_ok, "说明": kline_detail},
    ]
    result["ok"] = quote_ok and rate_ok and kline_ok
    return result


def run_book(path, offline: bool = True) -> None:
    """加载持仓并打开界面。默认离线启动（手填现价可用），点「刷新」联网。"""
    book = load_book(path)
    if offline:
        quotes, rates = offline_quotes_rates(book)
        initial_status = "initial"
    else:
        quotes, rates = fetch_all(book)
        initial_status = "fetched"
    enriched = enrich_holdings(book, quotes, rates)
    cfg = valuation_config(book)
    references = [
        {"名称": str(entry.get("名称") or entry.get("代码") or ""), "热度": None, "可用": False}
        for entry in cfg["参考基准"]
    ]
    lang = i18n.default_lang(book)
    summary = summarize(book, enriched, rates, lang)
    view = valuation_view(references, None, summary, cfg["现金规则"], lang)
    show(book, enriched, summary, quotes, rates, view, initial_status=initial_status)
