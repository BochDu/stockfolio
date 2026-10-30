"""持仓 JSON 校验：把所有可自定义字段都查一遍，一次性收集全部错误。

用法：
    from stockfolio import validate_file
    errors = validate_file("my_book.json")   # [] = 通过
    # 或命令行：python -m stockfolio --check my_book.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .market import SUPPORTED_CURRENCIES

A_SHARE = re.compile(r"^\d{6}(?!\d)\.(SS|SZ)$")
H_SHARE = re.compile(r"^\d{1,5}(?!\d)\.HK$")
FX_CODE = re.compile(r"^[A-Z]{3}CNY=X$")
# 美股：字母开头，可含点/连字符，不能以点或连字符结尾
US_SHARE = re.compile(r"^[A-Z](?:[A-Z0-9.\-]*[A-Z0-9])?$")
SINA_ASHARE = re.compile(r"^(SH|SZ)\d{6}$")


def _num(
    value, label: str, errors: list[str],
    minimum: float | None = None, maximum: float | None = None,
) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{label} 不是数字: {value!r}")
        return None
    if minimum is not None and number < minimum:
        errors.append(f"{label} 小于 {minimum}: {number}")
    if maximum is not None and number > maximum:
        errors.append(f"{label} 大于 {maximum}: {number}")
    return number


def _check_code(code: str, label: str, errors: list[str]) -> None:
    upper = code.upper()
    if A_SHARE.match(upper) or H_SHARE.match(upper) or FX_CODE.match(upper):
        return
    if SINA_ASHARE.match(upper):
        errors.append(
            f"{label}.代码 格式非法: {code!r}"
            "（新浪 A股符号；A股请写成 600036.SS / 000001.SZ）"
        )
        return
    if US_SHARE.match(upper):
        return
    errors.append(
        f"{label}.代码 格式非法: {code!r}"
        "（A股 600036.SS / 000001.SZ，港股 0700.HK，美股 VOO / BRK-B）"
    )


def validate_book(book: dict) -> list[str]:
    """校验整个持仓 JSON，返回错误列表；空列表表示通过。"""
    errors: list[str] = []
    if not isinstance(book, dict):
        return ["持仓 JSON 顶层必须是对象"]

    # ---- 标题 / 语言 / 本位币 / 配额 ----
    title = book.get("标题")
    if not isinstance(title, str) or not title.strip():
        errors.append("标题 缺失或为空")

    lang = book.get("语言") or book.get("language")
    if lang is not None and str(lang).strip().lower() not in ("zh", "en"):
        errors.append(f"语言 非法: {lang!r}（应为 zh 或 en）")

    base = str(book.get("本位币") or "CNY").upper()
    if base not in SUPPORTED_CURRENCIES:
        errors.append(f"本位币 不支持: {base}（支持: {'、'.join(SUPPORTED_CURRENCIES)}）")

    quota = book.get("配额总仓")
    if quota is None:
        quota = book.get("配额总仓_2030")
    if quota is not None:
        _num(quota, "配额总仓", errors, minimum=0)

    # ---- 现金 ----
    cash = book.get("现金")
    if cash is not None:
        if not isinstance(cash, dict):
            errors.append("现金 必须是 {币种: 金额} 对象")
        else:
            for ccy, amount in cash.items():
                if str(ccy).upper() not in SUPPORTED_CURRENCIES:
                    errors.append(f"现金 币种不支持: {ccy!r}")
                _num(amount, f"现金[{ccy}]", errors, minimum=0)

    # ---- 大类：名称唯一 + 比例 0~1 + 总和 = 1 ----
    cats = book.get("大类")
    if not isinstance(cats, list) or not cats:
        errors.append("大类 缺失或为空列表")
        cats = []
    cat_names: set[str] = set()
    total_ratio = 0.0
    for i, cat in enumerate(cats):
        label = f"大类[{i}]"
        if not isinstance(cat, dict):
            errors.append(f"{label} 必须是对象")
            continue
        name = cat.get("名称")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.名称 缺失或为空")
        elif name in cat_names:
            errors.append(f"{label}.名称 重复: {name!r}")
        cat_names.add(name)
        ratio = _num(cat.get("比例"), f"{label}.比例", errors, minimum=0, maximum=1)
        if ratio is not None:
            total_ratio += ratio
    if cats and abs(total_ratio - 1.0) > 1e-3:
        errors.append(f"大类 比例总和应为 1，实际为 {total_ratio:.4f}")

    # ---- 持仓：字段完整 + 代码格式 + 大类归属 + 货币支持 ----
    holdings = book.get("持仓")
    if not isinstance(holdings, list):
        errors.append("持仓 缺失或不是列表")
        holdings = []
    for i, h in enumerate(holdings):
        label = f"持仓[{i}]"
        if not isinstance(h, dict):
            errors.append(f"{label} 必须是对象")
            continue
        name = h.get("标的")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.标的 缺失或为空")
        code = str(h.get("代码") or "").strip()
        if not code:
            errors.append(f"{label}.代码 缺失或为空")
        else:
            _check_code(code, label, errors)
        cat = h.get("大类")
        if cat not in cat_names:
            errors.append(f"{label}.大类 未定义: {cat!r}（大类列表中没有该名称）")
        ccy = str(h.get("货币") or "").upper()
        if not ccy:
            errors.append(f"{label}.货币 缺失")
        elif ccy not in SUPPORTED_CURRENCIES:
            errors.append(f"{label}.货币 不支持: {h.get('货币')!r}")
        _num(h.get("数量"), f"{label}.数量", errors, minimum=0)
        _num(h.get("成本价"), f"{label}.成本价", errors, minimum=0)
        if "现价" in h:
            _num(h.get("现价"), f"{label}.现价", errors, minimum=0)

    # ---- 估值：回看/均线/参考基准/现金规则 ----
    val = book.get("估值")
    if "估值" in book and not isinstance(val, dict):
        errors.append("估值 必须是对象")
        val = None
    if val is None:
        errors.append("估值 缺失（必填的「现金规则」需放在「估值」里）")
    else:
        for key in ("回看交易日", "均线窗口"):
            if key in val:
                _num(val.get(key), f"估值.{key}", errors, minimum=1)
        refs = val.get("参考基准")
        if refs is not None:
            if not isinstance(refs, list):
                errors.append("估值.参考基准 必须是列表")
            else:
                for i, ref in enumerate(refs):
                    if (
                        not isinstance(ref, dict)
                        or not str(ref.get("名称") or "").strip()
                        or not str(ref.get("代码") or "").strip()
                    ):
                        errors.append(f"估值.参考基准[{i}] 缺少 名称/代码")
        rules = val.get("现金规则")
        if rules is None:
            errors.append("估值.现金规则 缺失（必填：温度 → 建议现金% 锚点）")
        elif not isinstance(rules, list) or len(rules) < 2:
            errors.append("估值.现金规则 必须是至少 2 个锚点的列表")
        else:
            rule_names: set[str] = set()
            for i, rule in enumerate(rules):
                label = f"估值.现金规则[{i}]"
                if not isinstance(rule, dict):
                    errors.append(f"{label} 必须是对象")
                    continue
                _num(rule.get("温度"), f"{label}.温度", errors, minimum=0, maximum=100)
                _num(rule.get("现金"), f"{label}.现金", errors, minimum=0, maximum=100)
                rname = rule.get("名称")
                if not isinstance(rname, str) or not rname.strip():
                    errors.append(f"{label}.名称 缺失或为空")
                elif rname in rule_names:
                    errors.append(f"{label}.名称 重复: {rname!r}")
                rule_names.add(rname)

    return errors


def validate_file(path) -> list[str]:
    """读取 JSON 文件并校验，返回错误列表；文件本身解析失败也作为错误返回。"""
    try:
        with Path(path).open(encoding="utf-8") as f:
            book = json.load(f)
    except json.JSONDecodeError as exc:
        return [f"JSON 解析失败: {exc}"]
    except OSError as exc:
        return [f"无法读取文件: {exc}"]
    return validate_book(book)
