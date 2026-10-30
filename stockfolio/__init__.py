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

from . import analysis, config, gui, i18n, market
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
from .gui import show

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_VALUATION",
    "analysis",
    "cash_rules",
    "config",
    "enrich_holdings",
    "fetch_all",
    "fetch_klines",
    "fetch_quote",
    "fetch_rates",
    "fetch_sina_list",
    "gui",
    "holding_heats",
    "benchmark_heats",
    "i18n",
    "ladder_plan",
    "load_book",
    "market",
    "offline_quotes_rates",
    "resolve_base",
    "run_book",
    "show",
    "smoke",
    "summarize",
    "validate_book",
    "validate_file",
    "valuation_config",
    "valuation_view",
]


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
    for h in book["持仓"]:
        for key in ("标的", "代码", "大类", "货币", "数量", "成本价"):
            if key not in h:
                raise ValueError(f"持仓缺少字段 {key}: {h}")
        if h["大类"] not in names:
            raise ValueError(f"未知大类 {h['大类']}: {h}")
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
