"""界面双语：UI 文案表、金融术语中英映射、默认语言解析与单位规则。

- 语言：JSON 顶层「语言」/「language」配置默认，界面按钮可切换。
- 单位：中文用 w（万本位币），英文用 k（千本位币），显示值 ×10。
- 术语：代码硬编码词汇与内置常见档位/基准名按金融常用术语映射；
  JSON 数据（标的、大类名等）不在映射表中，英文模式原样显示。
"""

from __future__ import annotations

LANG_ZH = "zh"
LANG_EN = "en"

SUPPORTED_LANGS = (LANG_ZH, LANG_EN)


def default_lang(book: dict) -> str:
    """读 JSON 顶层「语言」/「language」，缺省 zh；非法值回退 zh。"""
    raw = str(
        book.get("语言") or book.get("language") or LANG_ZH
    ).strip().lower()
    return LANG_EN if raw in ("en", "english", "en-us", "en_us", "us") else LANG_ZH


def unit_w(lang: str) -> str:
    """万本位币的单位：中文 w，英文 k（千）。"""
    return "w" if lang == LANG_ZH else "k"


def to_display(value: float, lang: str) -> float:
    """把万本位币转成显示口径：中文不变，英文 ×10（万→千）。"""
    return value * 10.0 if lang == LANG_EN else value


# ---------- UI 文案（代码硬编码部分） ----------

UI = {
    "刷新": {"zh": "刷新", "en": "Refresh"},
    "EN": {"zh": "EN", "en": "中文"},  # 按钮显示目标语言
    "股票持仓": {"zh": "股票持仓", "en": "Stock Portfolio"},
    "本位币": {"zh": "本位币", "en": "Base"},
    "目标总仓": {"zh": "目标总仓", "en": "Target Portfolio"},
    "目标": {"zh": "目标", "en": "Target"},
    "持仓": {"zh": "持仓", "en": "Holdings"},
    "占比": {"zh": "占比", "en": "Share"},
    "平衡": {"zh": "平衡", "en": "Balance"},
    "当前总仓": {"zh": "当前总仓", "en": "Current Holdings"},
    "当前现金": {"zh": "当前现金", "en": "Current Cash"},
    "现金占比": {"zh": "现金占比", "en": "Cash Ratio"},
    "整体盈亏": {"zh": "整体盈亏", "en": "Total P&L"},
    "市场温度": {"zh": "市场温度", "en": "Market Heat"},
    "持仓温度": {"zh": "持仓温度", "en": "Portfolio Heat"},
    "大类占比": {"zh": "大类占比", "en": "Category Mix"},
    "标的占比": {"zh": "标的占比", "en": "Holding Mix"},
    "暂无占比": {"zh": "暂无占比", "en": "No data"},
    "摘要": {"zh": "摘要", "en": "Summary"},
    "价格/汇率未就绪": {"zh": "价格/汇率未就绪", "en": "Prices/rates not ready"},
    "未拉取报价 — 点击「刷新」联网": {
        "zh": "未拉取报价 — 点击「刷新」联网",
        "en": "Offline — click Refresh to fetch",
    },
    "正在拉取报价…": {"zh": "正在拉取报价…", "en": "Fetching quotes…"},
    "未拿到可用报价（可检查网络或手填现价）": {
        "zh": "未拿到可用报价（可检查网络或手填现价）",
        "en": "No quotes available (check network or manual prices)",
    },
    "现金未折算：": {"zh": "现金未折算：", "en": "Cash not converted: "},
    "报价不全：部分持仓未取到价格，市值、现金占比与平衡建议仅按已取价部分计算。": {
        "zh": "报价不全：部分持仓未取到价格，市值、现金占比与平衡建议仅按已取价部分计算。",
        "en": "Incomplete quotes: some holdings are unpriced; value, cash ratio and balancing suggestions use priced holdings only.",
    },
    "三条大盘只做参考，不参与温度计算（0=冰点 100=泡沫）": {
        "zh": "三条大盘只做参考，不参与温度计算（0=冰点 100=泡沫）",
        "en": "Benchmarks are reference only (0=ice cold, 100=bubble)",
    },
    "由每只持仓自己的逆向指标按市值加权合成": {
        "zh": "由每只持仓自己的逆向指标按市值加权合成",
        "en": "Each holding's indicators, market-cap weighted",
    },
    "点击「刷新」拉取估值数据": {
        "zh": "点击「刷新」拉取估值数据",
        "en": "Click Refresh to fetch valuation data",
    },
    "判定档位": {"zh": "判定档位", "en": "Valuation Tier"},
    "现金建议": {"zh": "现金建议", "en": "Cash Target"},
    "合理操作": {"zh": "合理操作", "en": "Suggested Action"},
    "无足够历史": {"zh": "无足够历史", "en": "Insufficient History"},
    "无持仓": {"zh": "无持仓", "en": "No holdings"},
    "温度": {"zh": "温度", "en": "Heat"},
    "标的": {"zh": "标的", "en": "Name"},
    "货币": {"zh": "货币", "en": "Ccy"},
    "数量": {"zh": "数量", "en": "Qty"},
    "成本价": {"zh": "成本价", "en": "Cost"},
    "现价": {"zh": "现价", "en": "Price"},
    "市值(w)": {"zh": "市值(w)", "en": "Value(k)"},
    "占大类%": {"zh": "占大类%", "en": "Cat. %"},
    "盈亏%": {"zh": "盈亏%", "en": "P&L %"},
}

# 状态文案：按键 + 参数组装
_STATUS = {
    "initial": {"zh": "未拉取报价 — 点击「刷新」联网", "en": "Offline — click Refresh to fetch"},
    "fetched": {"zh": "报价已拉取", "en": "Quotes fetched"},
    "busy": {"zh": "正在拉取报价…", "en": "Fetching quotes…"},
    "noquote": {
        "zh": "未拿到可用报价（可检查网络或手填现价）",
        "en": "No quotes available (check network or manual prices)",
    },
    "error": {"zh": "拉取失败: {msg}", "en": "Fetch failed: {msg}"},
    "ok": {"zh": "报价 {ok} 成功 / {fail} 降级", "en": "Quotes {ok} ok / {fail} degraded"},
}


def t(key: str, lang: str) -> str:
    """取 UI 文案；未注册的 key 原样返回。"""
    entry = UI.get(key)
    if not entry:
        return key
    return entry.get(lang, entry.get(LANG_ZH, key))


def status_text(key: str, lang: str, **params) -> str:
    entry = _STATUS.get(key)
    if not entry:
        return key
    template = entry.get(lang, entry.get(LANG_ZH, key))
    try:
        return template.format(**params)
    except (KeyError, ValueError):
        return template


# ---------- 金融术语中英映射 ----------
# 覆盖内置默认档位名/热度读数/参考基准名等常见中文值；
# 命中则翻译，未命中（如用户自定义名）英文模式原样显示。

TERMS = {
    # 估值档位（现金规则锚点）
    "极度悲观": "Extremely Pessimistic",
    "合理低估": "Fairly Undervalued",
    "乐观高估": "Optimistically Valued",
    "全面高估": "Broadly Overvalued",
    # 热度读数
    "冰点": "Ice Cold",
    "偏冷": "Cool",
    "中性": "Neutral",
    "偏热": "Warm",
    "过热": "Overheated",
    # 参考基准
    "A股": "A-Shares",
    "港股": "HK Market",
    "美股": "US Market",
}


def term(name: str, lang: str) -> str:
    """数据侧术语映射：中文原样，英文命中 TERMS 则翻译，否则原样。"""
    if lang == LANG_EN and name in TERMS:
        return TERMS[name]
    return name
