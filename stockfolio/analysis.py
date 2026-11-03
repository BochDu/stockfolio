"""组合分析：持仓汇总、逆向指标、市场温度、现金规则建议。"""

from __future__ import annotations

from .config import clamp
from .i18n import LANG_EN, LANG_ZH, default_lang, term
from .market import _default_open, fetch_klines, kline_symbol

HEAT_BANDS = (
    (20.0, "冰点"),
    (40.0, "偏冷"),
    (60.0, "中性"),
    (80.0, "偏热"),
    (100.0, "过热"),
)


# ---------- 持仓汇总 ----------


def resolve_price(holding: dict, quotes: dict[str, float | None]) -> float | None:
    code = holding["代码"]
    q = quotes.get(code)
    if q is not None and float(q) > 0:
        return float(q)
    manual = holding.get("现价")
    if manual is not None:
        return float(manual)
    return None


def action_text(target: float, current: float, lang: str = LANG_ZH) -> str:
    """加仓/减仓建议。金额单位：中文 w（万本位币），英文 k（千，×10）。"""
    delta = round(target - current, 1)
    if abs(delta) < 0.05:
        return "持平" if lang == LANG_ZH else "Flat"
    if lang == LANG_EN:
        amount = f"{abs(delta) * 10:.1f} k"
        return f"Add {amount}" if delta > 0 else f"Reduce {amount}"
    if delta > 0:
        return f"加仓 {delta:.1f}"
    return f"减仓 {abs(delta):.1f}"


def enrich_holdings(
    book: dict,
    quotes: dict[str, float | None],
    rates: dict[str, float | None],
) -> list[dict]:
    rows = []
    for h in book["持仓"]:
        row = dict(h)
        price = resolve_price(h, quotes)
        rate = rates.get(h["货币"])
        qty = float(h["数量"])
        # 观察仓（数量 0）：现价照样展示，但没有市值/盈亏，也不参与汇总
        row["观察"] = qty <= 0
        row["解析现价"] = price
        row["汇率"] = rate
        row["可汇总"] = qty > 0 and price is not None and rate is not None
        if row["可汇总"]:
            mv_native = qty * price
            cost_native = qty * h["成本价"]
            row["市值_本位"] = mv_native * rate
            row["成本_本位"] = cost_native * rate
            if h["成本价"] > 0:
                row["盈亏pct"] = round(
                    (price - h["成本价"]) / h["成本价"] * 100, 2
                )
            else:
                row["盈亏pct"] = None
        rows.append(row)
    return rows


def _counts_in_quote_status(row: dict) -> bool:
    """报价状态只看有持仓的标的；观察仓不影响市值，取不到价不算失败。"""
    return float(row["数量"]) > 0


def summarize(
    book: dict,
    enriched: list[dict],
    rates: dict[str, float | None],
    lang: str | None = None,
) -> dict | None:
    if lang is None:
        lang = default_lang(book)
    active = [r for r in enriched if r["可汇总"]]
    if not active:
        return None

    total_mv = sum(r["市值_本位"] for r in active)
    total_cost = sum(r["成本_本位"] for r in active)
    total_w = total_mv / 10000.0

    cash_cny = 0.0
    cash_skipped = []
    for ccy, amount in (book.get("现金") or {}).items():
        rate = rates.get(ccy)
        if rate is None:
            cash_skipped.append(ccy)
            continue
        cash_cny += float(amount) * rate
    cash_w = cash_cny / 10000.0

    quota = book.get("目标总仓")
    target_w = float(quota) if quota is not None else None

    categories = []
    for cat in book["大类"]:
        name = cat["名称"]
        ratio = float(cat["比例"])
        current = sum(
            r["市值_本位"] for r in active if r["大类"] == name
        ) / 10000.0
        # 目标仓：远期绝对目标 = 目标总仓 × 比例（未设目标总仓时为 None）
        target = target_w * ratio if target_w is not None else None
        # 平衡：相对「当前全部股票总市值」的配置偏离，保持整体仓位不变下的调增/调减
        expected = total_w * ratio
        share = (current / total_w * 100.0) if total_w > 0 else None
        categories.append(
            {
                "名称": name,
                "比例": ratio,
                "目标仓": None if target is None else round(target, 2),
                "当前仓": round(current, 2),
                "当前占比": None if share is None else round(share, 1),
                "平衡": round(expected - current, 2),
                "操作": action_text(expected, current, lang),
            }
        )

    pnl = None
    if total_cost > 0:
        pnl = round((total_mv - total_cost) / total_cost * 100, 2)

    return {
        "本位币": str(book.get("本位币") or "CNY"),
        "目标总仓": quota,
        "当前总仓_w": round(total_w, 2),
        "当前现金_w": round(cash_w, 2),
        "整体盈亏pct": pnl,
        "现金跳过": cash_skipped,
        "大类": categories,
        "报价成功": len(active),
        "报价失败": sum(
            1
            for r in enriched
            if _counts_in_quote_status(r) and not r["可汇总"]
        ),
    }


# ---------- 逆向指标：持仓个股合成温度，大盘只留参考 ----------


def kline_indicators(
    rows: list[list], lookback: int, ma_window: int
) -> dict | None:
    """把一段日线压成 4 个逆向指标 + 各自热度（0=冰点 100=泡沫）。"""
    if len(rows) < ma_window:
        return None
    window = rows[-lookback:] if lookback > 0 else rows
    # 回看窗口小于均线窗口时数据不足：宁可不算，不拿不足的天数除以窗口
    if len(window) < ma_window:
        return None
    closes = [row[1] for row in window]
    highs = [row[2] for row in window]
    last = closes[-1]

    # 价格分位：当前价在过去 N 日里排第几
    pct = sum(1 for c in closes if c <= last) / len(closes) * 100.0
    # 均线偏离：离长期均线越远越热
    ma = sum(closes[-ma_window:]) / ma_window
    bias = (last / ma - 1.0) * 100.0
    # 高点回撤：跌得越深越冷
    drawdown = (last / max(highs) - 1.0) * 100.0
    # 成交热度：近 20 日 vs 近 250 日
    volumes = [row[3] for row in rows]
    volume_ratio = None
    base = sum(volumes[-ma_window:]) / ma_window
    if len(volumes) >= ma_window and base > 0:
        volume_ratio = (sum(volumes[-20:]) / 20.0) / base

    return {
        "最新": last,
        "分位": pct,
        "均线偏离": bias,
        "高点回撤": drawdown,
        "成交比": volume_ratio,
        "热度_位置": clamp(pct),
        "热度_均线": clamp(50.0 + bias * 4.0),
        "热度_回撤": clamp(100.0 + drawdown * 2.5),
        "热度_成交": (
            None
            if volume_ratio is None
            else clamp((volume_ratio - 0.6) / 0.9 * 100.0)
        ),
    }


def _average_heat(indicators: dict) -> float | None:
    heats = [
        heat
        for heat in (
            indicators["热度_位置"],
            indicators["热度_均线"],
            indicators["热度_回撤"],
            indicators["热度_成交"],
        )
        if heat is not None
    ]
    if not heats:
        return None
    return sum(heats) / len(heats)


def benchmark_heats(cfg: dict, opener=_default_open) -> list[dict]:
    """三条大盘参考：一条一行，只看热度数字，不展开细节。"""
    entries = cfg["参考基准"]
    klines = fetch_klines(
        [str(entry.get("代码") or "") for entry in entries],
        cfg["回看交易日"] + 1,
        opener=opener,
    )
    out: list[dict] = []
    for entry in entries:
        name = str(entry.get("名称") or entry.get("代码") or "")
        code = str(entry.get("代码") or "")
        rows = klines.get(code) or []
        indicators = (
            kline_indicators(rows, cfg["回看交易日"], cfg["均线窗口"])
            if len(rows) >= cfg["均线窗口"]
            else None
        )
        heat = _average_heat(indicators) if indicators else None
        out.append({"名称": name, "代码": code, "热度": heat, "可用": heat is not None})
    return out


def holding_heats(
    enriched: list[dict], cfg: dict, opener=_default_open
) -> dict:
    """温度的核心来源：每只持仓自己的逆向指标，按市值加权合成。

    观察仓（数量 0）没有市值，不参与持仓温度与大类温度，但日线拉得到就单独
    算一份个股温度，供明细表展示——没持仓也想知道贵不贵。
    """
    ma_window = cfg["均线窗口"]
    held = [
        row
        for row in enriched
        if row.get("可汇总") and float(row.get("数量") or 0) > 0
    ]
    # 观察仓只需要代码能映射到日线，不依赖报价/汇率
    watch = [
        row
        for row in enriched
        if float(row.get("数量") or 0) <= 0
        and kline_symbol(str(row.get("代码") or ""))
    ]
    symbols: dict[str, str] = {}
    for row in [*held, *watch]:
        symbol = kline_symbol(str(row.get("代码") or ""))
        if symbol:
            symbols[str(row["代码"])] = symbol

    klines = fetch_klines(list(symbols.values()), cfg["回看交易日"] + 1, opener=opener)

    prepared_held: list[tuple[dict, list[list]]] = []
    prepared_watch: list[tuple[dict, list[list]]] = []
    skipped: list[str] = []
    for row in held:
        rows = klines.get(symbols.get(str(row["代码"]), "")) or []
        if len(rows) < ma_window:
            skipped.append(str(row["标的"]))
            continue
        prepared_held.append((row, rows))
    for row in watch:
        rows = klines.get(symbols.get(str(row["代码"]), "")) or []
        if len(rows) < ma_window:
            continue  # 观察仓历史不足只影响它自己的温度，不进「无足够历史」名单
        prepared_watch.append((row, rows))

    result = {
        "明细": [],
        "温度": None,
        "窗口": None,
        "代码热度": {},
        "大类热度": [],
        "样本": f"0/{len(held)}",
        "跳过": skipped,
    }
    if not prepared_held and not prepared_watch:
        return result

    # 统一窗口：持仓用同一段历史长度，避免新股只有短历史导致口径不一致；
    # 没有持仓时退化为观察仓之间对齐
    base = prepared_held or prepared_watch
    window = max(ma_window, min(len(rows) for _row, rows in base))
    mv_total = 0.0
    weighted = 0.0
    covered_held = 0
    for row, rows in [*prepared_held, *prepared_watch]:
        indicators = kline_indicators(rows, window, ma_window)
        heat = _average_heat(indicators) if indicators else None
        if heat is None:
            if float(row.get("数量") or 0) > 0:
                result["跳过"].append(str(row["标的"]))
            continue
        mv = float(row.get("市值_本位") or 0.0)
        weighted += heat * mv
        mv_total += mv
        if float(row.get("数量") or 0) > 0:
            covered_held += 1
        result["明细"].append(
            {
                "代码": str(row["代码"]),
                "标的": row["标的"],
                "大类": row.get("大类"),
                "热度": heat,
                "市值_本位": mv,
                "历史": len(rows),
                "观察": float(row.get("数量") or 0) <= 0,
            }
        )

    result["窗口"] = window
    if mv_total > 0:
        result["温度"] = clamp(weighted / mv_total)
    result["样本"] = f"{covered_held}/{len(held)}"

    heat_by_code = {item["代码"]: item["热度"] for item in result["明细"]}
    result["代码热度"] = heat_by_code

    grouped: dict[str, list[dict]] = {}
    for row in held:
        grouped.setdefault(str(row.get("大类") or ""), []).append(row)
    for name, members in grouped.items():
        covered = [
            member
            for member in members
            if heat_by_code.get(str(member["代码"])) is not None
        ]
        if not covered:
            result["大类热度"].append(
                {"名称": name, "热度": None, "样本": f"0/{len(members)}"}
            )
            continue
        weight = sum(float(member.get("市值_本位") or 0.0) for member in covered)
        heat = None
        if weight > 0:
            heat = clamp(
                sum(
                    heat_by_code[str(member["代码"])]
                    * float(member.get("市值_本位") or 0.0)
                    for member in covered
                )
                / weight
            )
        result["大类热度"].append(
            {"名称": name, "热度": heat, "样本": f"{len(covered)}/{len(members)}"}
        )
    return result


# ---------- 现金规则：温度 → 股票:现金建议 ----------


def heat_reading(heat: float | None, lang: str = LANG_ZH) -> str:
    if heat is None:
        return "—"
    for edge, label in HEAT_BANDS:
        if heat < edge:
            return term(label, lang)
    return term(HEAT_BANDS[-1][1], lang)


def ratio_text(stock_pct: float, cash_pct: float) -> str:
    """按 股票:现金 两项合计为 10 的风格输出。"""
    return f"{stock_pct / 10:.1f}:{cash_pct / 10:.1f}"


def _zone_between(low: dict, high: dict, fraction: float, lang: str) -> str:
    """两锚点之间的档位描述（偏近一侧）。"""
    closer = low if fraction < 0.5 else high
    if lang == LANG_EN:
        return (
            f"Between {term(low['名称'], lang)} and {term(high['名称'], lang)}"
            f" (closer to {term(closer['名称'], lang)})"
        )
    return (
        f"{low['名称']} 与 {high['名称']} 之间"
        f"（偏{closer['名称']}）"
    )


def ladder_plan(
    temperature: float | None, cash_rules: list[dict], lang: str = LANG_ZH
) -> dict | None:
    """温度 → 建议现金%（在现金规则锚点间线性插值）。

    现金规则形如 [{"温度": 0, "名称": "极度悲观", "现金": 0}, ...]，
    锚点可自由增删；温度落在锚点区间内线性插值，区间外取端点值。
    """
    if temperature is None or not cash_rules:
        return None
    rules = sorted(cash_rules, key=lambda r: r["温度"])
    t = clamp(temperature, rules[0]["温度"], rules[-1]["温度"])
    cash_pct = None
    zone = None
    for index in range(len(rules) - 1):
        low = rules[index]
        high = rules[index + 1]
        if low["温度"] <= t <= high["温度"]:
            span = high["温度"] - low["温度"]
            fraction = 0.0 if span <= 0 else (t - low["温度"]) / span
            cash_pct = low["现金"] + fraction * (high["现金"] - low["现金"])
            if fraction < 0.15:
                zone = term(low["名称"], lang)
            elif fraction > 0.85:
                zone = term(high["名称"], lang)
            else:
                zone = _zone_between(low, high, fraction, lang)
            break
    if cash_pct is None:
        cash_pct = rules[-1]["现金"]
        zone = term(rules[-1]["名称"], lang)
    cash_pct = clamp(cash_pct, 0.0, 100.0)
    stock_pct = 100.0 - cash_pct
    return {
        "温度": round(temperature, 1),
        "股票": round(stock_pct, 1),
        "现金": round(cash_pct, 1),
        "比例": ratio_text(stock_pct, cash_pct),
        "档位": zone,
    }


def _reference_rows(references: list[dict], lang: str = LANG_ZH) -> list[tuple[str, str, str]]:
    """三条大盘参考：(市场, 热度, 读数)。细节一律不展示。"""
    rows: list[tuple[str, str, str]] = []
    for reference in references:
        heat = reference.get("热度")
        rows.append(
            (
                str(reference.get("名称") or ""),
                "—" if heat is None else f"{heat:.1f}",
                heat_reading(heat, lang) if heat is not None else "—",
            )
        )
    return rows


def valuation_view(
    references: list[dict],
    holdings: dict | None,
    summary: dict | None,
    cash_rules: list[dict],
    lang: str = LANG_ZH,
) -> dict:
    holdings = holdings or {"明细": [], "温度": None, "窗口": None, "样本": "0/0", "跳过": []}
    plan = ladder_plan(holdings.get("温度"), cash_rules, lang)
    view = {
        "参考行": _reference_rows(references, lang),
        "温度": holdings.get("温度"),
        "窗口": holdings.get("窗口"),
        "样本": holdings.get("样本"),
        "跳过": holdings.get("跳过") or [],
        "代码热度": holdings.get("代码热度") or {},
        "大类热度": holdings.get("大类热度") or [],
        "建议": plan,
        "当前比例": None,
        "操作": None,
    }
    if plan is None or summary is None:
        return view

    total = float(summary.get("当前总仓_w") or 0.0)
    cash = float(summary.get("当前现金_w") or 0.0)
    pool = total + cash
    if pool <= 0:
        return view
    current_stock = total / pool * 100.0
    target_stock = pool * plan["股票"] / 100.0
    view["当前现金pct"] = round(100.0 - current_stock, 1)
    view["当前比例"] = ratio_text(current_stock, 100.0 - current_stock)
    view["操作"] = action_text(target_stock, total, lang)
    view["目标股票w"] = round(target_stock, 2)
    view["当前股票w"] = round(total, 2)
    return view
