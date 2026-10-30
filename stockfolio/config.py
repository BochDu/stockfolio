"""配置结构与默认值：估值参数、参考基准、现金规则（可自由定义）。"""

from __future__ import annotations

DEFAULT_VALUATION = {
    "回看交易日": 1200,
    "均线窗口": 250,
    "参考基准": [
        {"名称": "A股", "代码": "sh000300"},
        {"名称": "港股", "代码": "hkHSI"},
        {"名称": "美股", "代码": "usIXIC"},
    ],
}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """把数值夹在 [low, high] 区间内。"""
    return max(low, min(high, value))


def cash_rules(cfg: dict) -> list[dict]:
    """解析并校验「估值.现金规则」（必填）：温度锚点 → 建议现金占比。

    每个锚点形如 {"温度": 0..100, "名称": str, "现金": 0..100}，
    返回按温度升序、至少两个锚点的干净列表。任何锚点缺失或非法
    （字段不全、非数字、温度/现金越界、名称为空）直接报错——
    不内置兜底规则、不静默丢弃，规则唯一来源是 JSON。
    """
    raw = (cfg or {}).get("现金规则")
    if not isinstance(raw, list):
        raise ValueError(
            "持仓 JSON 缺少「估值.现金规则」：请在 JSON 里定义温度→现金比例锚点，"
            '例如 [{"温度": 0, "名称": "档位名", "现金": 0}, ...]'
        )
    if len(raw) < 2:
        raise ValueError("「估值.现金规则」至少需要两个锚点（温度→现金）")
    cleaned = []
    for index, rule in enumerate(raw):
        label = f"「估值.现金规则[{index}]」"
        if not isinstance(rule, dict):
            raise ValueError(f"{label} 必须是 {{温度, 名称, 现金}} 对象")
        try:
            temperature = float(rule["温度"])
            cash_pct = float(rule["现金"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"{label} 格式无效：需要 {温度, 名称, 现金} 三个字段，且温度/现金为数字"
            ) from None
        if not 0.0 <= temperature <= 100.0:
            raise ValueError(f"{label}.温度 应在 0~100，实际为 {temperature}")
        if not 0.0 <= cash_pct <= 100.0:
            raise ValueError(
                f"{label}.现金 应在 0~100（建议现金占比%），实际为 {cash_pct}"
            )
        name = str(rule.get("名称") or "").strip()
        if not name:
            raise ValueError(f"{label}.名称 缺失或为空")
        cleaned.append({"温度": temperature, "名称": name, "现金": cash_pct})
    cleaned.sort(key=lambda r: r["温度"])
    return cleaned


def valuation_config(book: dict) -> dict:
    """从持仓 JSON 的「估值」段合并默认值，返回统一配置。"""
    cfg = book.get("估值") or {}
    references = cfg.get("参考基准") or DEFAULT_VALUATION["参考基准"]
    return {
        "回看交易日": int(cfg.get("回看交易日", DEFAULT_VALUATION["回看交易日"])),
        "均线窗口": int(cfg.get("均线窗口", DEFAULT_VALUATION["均线窗口"])),
        "参考基准": list(references),
        "现金规则": cash_rules(cfg),
        "本位币": str(book.get("本位币") or "CNY"),
    }
