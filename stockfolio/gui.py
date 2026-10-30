"""Tkinter 界面：持仓总览、大类/标的占比图、市场温度与现金建议。零第三方依赖。

中英双语：右上角按钮切换（默认语言由 JSON「语言」/「language」配置）。
中文单位 w（万本位币），英文单位 k（千本位币，数值 ×10）。
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from .analysis import (
    _counts_in_quote_status,
    action_text,
    enrich_holdings,
    heat_reading,
    holding_heats,
    benchmark_heats,
    ladder_plan,
    summarize,
    valuation_view,
)
from .config import valuation_config
from .i18n import LANG_EN, LANG_ZH, default_lang, status_text, t, term, to_display, unit_w
from .market import fetch_all

BG = "#070b14"
PANEL = "#0e1626"
CARD = "#121c30"
BORDER = "#24344f"
TEXT = "#e7eef8"
MUTED = "#8aa0bb"
GOLD = "#e7c15a"
CYAN = "#4ad7ff"
MINT = "#3de6a6"
LOSS = "#ff6b7a"
GRID = "#1c2b44"
FONT = "Microsoft YaHei UI"
MONO = "Consolas"
PIE_COLORS = (CYAN, GOLD, MINT, "#7aa2ff", "#e7a67a", "#c792ea")
DASH = "—"


def fmt_w(value: float | None, lang: str = LANG_ZH) -> str:
    """万本位币 → 显示值：中文不变（w），英文 ×10（千，k）。"""
    if value is None:
        return DASH
    display = to_display(value, lang)
    return f"{display:.2f}".rstrip("0").rstrip(".")


def _join(values: list[str], lang: str) -> str:
    return ", ".join(values) if lang == LANG_EN else "、".join(values)


def _auto_wrap(label: tk.Label, reserve: int | None | callable = None) -> None:
    """让 Label 文本跟随父容器宽度自动换行，避免英文长文本撑破布局。

    reserve：同一行里其它组件占用的像素宽（数字），或一个返回该宽度的函数；
    None 表示只留 24px 边距。
    """

    def sync(_event=None) -> None:
        try:
            master = label.master
            avail = master.winfo_width()
            if avail <= 0:
                return
            pad = reserve() if callable(reserve) else (reserve if reserve is not None else 24)
            wrap = avail - pad
            if wrap > 40 and wrap != label.cget("wraplength"):
                label.configure(wraplength=wrap)
        except Exception:  # noqa: BLE001 — 布局期 widget 可能已被销毁
            pass

    label.master.bind("<Configure>", sync)
    sync()


def relocalize_summary(
    book: dict,
    enriched: list[dict],
    rates: dict | None,
    summary: dict | None,
    lang: str,
) -> dict | None:
    """切换语言后重生成摘要（大类操作文案等语言字段），不重新联网。"""
    if summary is None:
        return None
    return summarize(book, enriched, rates, lang)


def relocalize_view(view: dict | None, book: dict, lang: str) -> dict | None:
    """切换语言后重生成估值视图的语言字段（参考行读数/档位/操作）。"""
    if view is None:
        return None
    rows = []
    for name, heat_text, _reading in view.get("参考行") or []:
        heat = None if heat_text == DASH else float(heat_text)
        rows.append(
            (name, heat_text, heat_reading(heat, lang) if heat is not None else DASH)
        )
    view["参考行"] = rows
    plan = view.get("建议")
    if plan is not None:
        cash_rules = valuation_config(book)["现金规则"]
        rebuilt = ladder_plan(plan.get("温度"), cash_rules, lang)
        if rebuilt is not None:
            view["建议"] = rebuilt
    if view.get("操作"):
        view["操作"] = action_text(view["配额股票w"], view["当前股票w"], lang)
    return view


def _fmt_px(value: float | None) -> str:
    if value is None:
        return DASH
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _fmt_qty(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return DASH
    return f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def _paint_pie(
    canvas: tk.Canvas,
    slices: list[tuple[str, float]],
    colors: tuple[str, ...] | list[str],
    lang: str = LANG_ZH,
) -> None:
    canvas.delete("all")
    width = canvas.winfo_width()
    height = canvas.winfo_height()
    if width < 40 or height < 40:
        return

    usable = [(label, float(value)) for label, value in slices if value > 0]
    empty_text = t("暂无占比", lang)
    if not usable:
        canvas.create_text(
            width / 2,
            height / 2,
            text=empty_text,
            fill=MUTED,
            font=(FONT, 12),
        )
        return

    total = sum(value for _, value in usable)
    legend_w = min(210, max(120, width * 0.40))
    margin = 14
    size = min(width - legend_w - margin * 3, height - margin * 2)
    if size < 24:
        canvas.create_text(
            width / 2,
            height / 2,
            text=empty_text,
            fill=MUTED,
            font=(FONT, 12),
        )
        return

    x0 = margin
    y0 = (height - size) / 2
    start = 90.0
    consumed = 0.0
    for index, (_label, value) in enumerate(usable):
        if index == len(usable) - 1:
            extent = -(360.0 - consumed)
        else:
            extent = -(value / total) * 360.0
            consumed += -extent
        if abs(extent) >= 359.99:
            extent = -359.99
        color = colors[index % len(colors)]
        canvas.create_arc(
            x0,
            y0,
            x0 + size,
            y0 + size,
            start=start,
            extent=extent,
            fill=color,
            outline=BG,
            width=1,
        )
        start += extent

    line_h = 22
    legend_x = x0 + size + 16
    legend_top = max(margin, (height - len(usable) * line_h) / 2)
    for index, (label, value) in enumerate(usable):
        color = colors[index % len(colors)]
        y = legend_top + index * line_h
        canvas.create_rectangle(legend_x, y + 2, legend_x + 10, y + 12, fill=color, outline="")
        pct = value / total * 100
        canvas.create_text(
            legend_x + 16,
            y + 7,
            text=f"{label}  {pct:.1f}%",
            fill=TEXT,
            anchor="w",
            font=(FONT, 9),
        )


def _apply_style(root: tk.Misc) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        "Treeview",
        background="#10192b",
        foreground=TEXT,
        fieldbackground="#10192b",
        borderwidth=0,
        rowheight=28,
        font=(MONO, 10),
    )
    style.configure(
        "Treeview.Heading",
        background="#182338",
        foreground=MUTED,
        borderwidth=0,
        font=(FONT, 9),
    )
    style.map("Treeview", background=[("selected", "#16384a")], foreground=[("selected", TEXT)])
    style.configure(
        "TCombobox",
        fieldbackground="#10192b",
        background="#182338",
        foreground=TEXT,
        arrowcolor=MUTED,
        borderwidth=0,
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", "#10192b")],
        foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", "#16384a")],
        selectforeground=[("readonly", TEXT)],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=PANEL,
        troughcolor=BG,
        arrowcolor=MUTED,
        borderwidth=0,
    )


def _metric_card(parent: tk.Frame, name: str, value: str, accent: str) -> tk.Frame:
    card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    tk.Frame(card, bg=accent, height=3).pack(fill="x")
    tk.Label(card, text=name, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=12, pady=(10, 0))
    tk.Label(card, text=value, bg=CARD, fg=TEXT, font=(MONO, 15, "bold")).pack(
        anchor="w", padx=12, pady=(2, 12)
    )
    return card


def _summary_row(parent: tk.Frame, summary: dict | None, view: dict | None = None, lang: str = LANG_ZH) -> None:
    cards = tk.Frame(parent, bg=BG)
    cards.pack(fill="x", padx=22, pady=(0, 12))
    if summary is None:
        card = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=4)
        tk.Frame(card, bg=GOLD, height=3).pack(fill="x")
        tk.Label(card, text=t("摘要", lang), bg=CARD, fg=MUTED, font=(FONT, 9)).pack(
            anchor="w", padx=12, pady=(10, 0)
        )
        tk.Label(
            card,
            text=t("价格/汇率未就绪", lang),
            bg=CARD,
            fg=TEXT,
            font=(FONT, 14, "bold"),
        ).pack(anchor="w", padx=12, pady=(2, 12))
        return

    unit = unit_w(lang)
    pnl = summary.get("整体盈亏pct")
    pnl_color = MINT if pnl is not None and pnl >= 0 else LOSS if pnl is not None else MUTED
    target = summary.get("目标配额")
    ratio_value = DASH
    ratio_color = MUTED
    current_pct = (view or {}).get("当前现金pct")
    if current_pct is not None:
        ratio_value = f"{current_pct:.1f}%"
        ratio_color = _heat_color((view or {}).get("温度"))
    metrics = (
        (t("目标配额", lang), f"{fmt_w(target, lang)} {unit}" if target is not None else DASH, GOLD),
        (t("当前总仓", lang), f"{fmt_w(summary.get('当前总仓_w'), lang)} {unit}", CYAN),
        (t("当前现金", lang), f"{fmt_w(summary.get('当前现金_w'), lang)} {unit}", CYAN),
        (t("现金占比", lang), ratio_value, ratio_color),
        (t("整体盈亏", lang), _fmt_pct(pnl), pnl_color),
    )
    widgets = [_metric_card(cards, name, value, accent) for name, value, accent in metrics]

    def relayout(_event=None) -> None:
        for c in range(5):
            cards.columnconfigure(c, weight=0)
        for r in range(6):
            cards.rowconfigure(r, weight=0)
        cards.rowconfigure(0, weight=1)
        for c in range(5):
            cards.columnconfigure(c, weight=1)
        for i, w in enumerate(widgets):
            w.grid(row=0, column=i, sticky="nsew", padx=4, pady=4)

    cards.bind("<Configure>", relayout)
    relayout()

    skipped = summary.get("现金跳过") or []
    if skipped:
        note = tk.Label(
            parent,
            text=t("现金未折算：", lang) + _join(skipped, lang),
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            anchor="w",
        )
        note.pack(anchor="w", padx=26, pady=(2, 0))
        _auto_wrap(note, reserve=26)


def _attach_pie(canvas: tk.Canvas, get_slices, lang: str) -> None:
    def redraw(_event=None) -> None:
        _paint_pie(canvas, get_slices(), PIE_COLORS, lang)

    canvas.bind("<Configure>", redraw)


def _chart_panel(parent: tk.Frame, title: str, column: int) -> tuple[tk.Frame, tk.Canvas, tk.Frame]:
    panel = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
    panel.grid(row=0, column=column, sticky="nsew", padx=4)
    top = tk.Frame(panel, bg=PANEL)
    top.pack(fill="x", padx=12, pady=(10, 0))
    tk.Label(top, text=title, bg=PANEL, fg=TEXT, font=(FONT, 11)).pack(side="left")
    canvas = tk.Canvas(panel, bg=PANEL, highlightthickness=0, height=240)
    canvas.pack(fill="both", expand=True, padx=8, pady=(4, 8))
    return panel, canvas, top


def _chart_row(parent: tk.Frame, book: dict, enriched: list[dict], summary: dict, lang: str = LANG_ZH) -> None:
    charts = tk.Frame(parent, bg=BG)
    charts.pack(fill="x", padx=22, pady=(0, 12))
    charts.columnconfigure(0, weight=1)
    charts.columnconfigure(1, weight=1)
    charts.rowconfigure(0, weight=1)

    _left_panel, left_canvas, _left_top = _chart_panel(charts, t("大类占比", lang), 0)

    def category_slices() -> list[tuple[str, float]]:
        return [(item["名称"], float(item["当前仓"])) for item in summary.get("大类") or []]

    _attach_pie(left_canvas, category_slices, lang)

    _right_panel, right_canvas, right_top = _chart_panel(charts, t("标的占比", lang), 1)
    names = [item["名称"] for item in book.get("大类") or []]
    default = names[0] if names else ""
    for name in names:
        if any(
            row["大类"] == name and row.get("可汇总") and row.get("市值_本位", 0) > 0
            for row in enriched
        ):
            default = name
            break
    selected = tk.StringVar(value=default)

    def holding_slices() -> list[tuple[str, float]]:
        name = selected.get()
        return [
            (row["标的"], float(row["市值_本位"]))
            for row in enriched
            if row["大类"] == name and row.get("可汇总")
        ]

    combo = ttk.Combobox(
        right_top,
        textvariable=selected,
        values=names,
        state="readonly",
        width=12,
        font=(FONT, 9),
    )
    combo.pack(side="right")

    def on_pick(_event=None) -> None:
        _paint_pie(right_canvas, holding_slices(), PIE_COLORS, lang)

    combo.bind("<<ComboboxSelected>>", on_pick)
    _attach_pie(right_canvas, holding_slices, lang)


def _action_color(text: str) -> str:
    if text.startswith("加仓") or text.startswith("Add"):
        return MINT
    if text.startswith("减仓") or text.startswith("Reduce"):
        return LOSS
    return MUTED


def _heat_color(heat: float | None) -> str:
    if heat is None:
        return MUTED
    if heat >= 80.0:
        return LOSS
    if heat >= 60.0:
        return GOLD
    if heat >= 40.0:
        return TEXT
    if heat >= 20.0:
        return CYAN
    return MINT


def _category_blocks(
    parent: tk.Frame,
    book: dict,
    enriched: list[dict],
    summary: dict | None,
    view: dict | None = None,
    lang: str = LANG_ZH,
) -> None:
    lookup = {}
    if summary is not None:
        lookup = {item["名称"]: item for item in summary.get("大类") or []}
    heat_lookup = {
        item["名称"]: item for item in (view or {}).get("大类热度") or []
    }
    code_heats = (view or {}).get("代码热度") or {}

    for cat in book.get("大类") or []:
        name = cat["名称"]
        ratio = float(cat["比例"])
        meta = lookup.get(name)
        panel = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        panel.pack(fill="x", padx=22, pady=(0, 12))

        head = tk.Frame(panel, bg=PANEL)
        head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(
            head,
            text=f"{name}  ·  {ratio * 100:.0f}%",
            bg=PANEL,
            fg=TEXT,
            font=(FONT, 12, "bold"),
        ).pack(side="left")
        if meta is None:
            detail = f"配额 {DASH}    当前 {DASH}    {DASH}"
            detail_fg = MUTED
        else:
            unit = unit_w(lang)
            if lang == LANG_EN:
                detail = (
                    f"Quota {fmt_w(meta['配额仓'], lang)} {unit}    "
                    f"Current {fmt_w(meta['当前仓'], lang)} {unit}    "
                    f"{meta['操作']}"
                )
            else:
                detail = (
                    f"配额 {fmt_w(meta['配额仓'], lang)} {unit}    "
                    f"当前 {fmt_w(meta['当前仓'], lang)} {unit}    "
                    f"{meta['操作']}"
                )
            detail_fg = _action_color(meta["操作"])
        tk.Label(head, text=detail, bg=PANEL, fg=detail_fg, font=(MONO, 10)).pack(side="right")

        cat_heat_info = heat_lookup.get(name)
        cat_heat = (cat_heat_info or {}).get("热度")
        if cat_heat_info is not None:
            sub = tk.Frame(panel, bg=PANEL)
            sub.pack(fill="x", padx=12, pady=(0, 6))
            tk.Label(
                sub,
                text=f"{t('温度', lang)} {cat_heat:.1f} · {heat_reading(cat_heat, lang)}"
                if cat_heat is not None
                else f"{t('温度', lang)} —",
                bg=PANEL,
                fg=_heat_color(cat_heat),
                font=(MONO, 10),
            ).pack(side="left")
            tk.Label(
                sub,
                text=(
                    f"    配额占比 {ratio * 100:.0f}%    覆盖 {cat_heat_info.get('样本', DASH)} 只"
                    if lang == LANG_ZH
                    else f"    Quota {ratio * 100:.0f}%    Coverage {cat_heat_info.get('样本', DASH)}"
                ),
                bg=PANEL,
                fg=MUTED,
                font=(MONO, 9),
            ).pack(side="left")

        rows = [row for row in enriched if row["大类"] == name]
        if not rows:
            tk.Label(panel, text=t("无持仓", lang), bg=PANEL, fg=MUTED, font=(FONT, 10)).pack(
                anchor="w", padx=12, pady=(0, 12)
            )
            continue

        class_total = sum(row["市值_本位"] for row in rows if row.get("可汇总"))
        columns = ("name", "ccy", "qty", "cost", "px", "mv", "share", "pnl", "heat")
        table = ttk.Treeview(panel, columns=columns, show="headings", height=max(len(rows), 1))
        headings = (
            ("name", t("标的", lang), 140, "w"),
            ("ccy", t("货币", lang), 64, "center"),
            ("qty", t("数量", lang), 80, "e"),
            ("cost", t("成本价", lang), 100, "e"),
            ("px", t("现价", lang), 90, "e"),
            ("mv", t("市值(w)", lang), 90, "e"),
            ("share", t("占大类%", lang), 80, "e"),
            ("pnl", t("盈亏%", lang), 80, "e"),
            ("heat", t("温度", lang), 70, "e"),
        )
        for key, title, width, anchor in headings:
            table.heading(key, text=title)
            table.column(key, width=width, anchor=anchor, stretch=(key == "name"))
        table.tag_configure("odd", background="#10192b")
        table.tag_configure("even", background="#0d1524")
        table.tag_configure("gain", foreground=MINT)
        table.tag_configure("loss", foreground=LOSS)
        table.tag_configure("muted", foreground=MUTED)

        for index, row in enumerate(rows):
            quoted = bool(row.get("可汇总"))
            heat = code_heats.get(str(row["代码"]))
            heat_text = DASH if heat is None else f"{heat:.1f}"
            if quoted:
                share = (
                    f"{row['市值_本位'] / class_total * 100:.1f}%"
                    if class_total > 0
                    else DASH
                )
                mv = fmt_w(row["市值_本位"] / 10000.0, lang)
                pnl_value = row.get("盈亏pct")
                pnl = _fmt_pct(pnl_value)
                if pnl_value is None:
                    tone = ""
                elif pnl_value >= 0:
                    tone = "gain"
                else:
                    tone = "loss"
            else:
                share = DASH
                mv = DASH
                pnl = DASH
                tone = "muted"
            stripe = "odd" if index % 2 else "even"
            tags = (stripe, tone) if tone else (stripe,)
            table.insert(
                "",
                "end",
                values=(
                    row["标的"],
                    row["货币"],
                    _fmt_qty(row["数量"]),
                    _fmt_px(row["成本价"]),
                    _fmt_px(row.get("解析现价")),
                    mv,
                    share,
                    pnl,
                    heat_text,
                ),
                tags=tags,
            )
        table.pack(fill="x", padx=8, pady=(0, 8))


def _valuation_block(parent: tk.Frame, view: dict, lang: str = LANG_ZH) -> None:
    panel = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
    panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

    head = tk.Frame(panel, bg=PANEL)
    head.pack(fill="x", padx=12, pady=(10, 6))
    tk.Label(
        head,
        text=t("市场温度", lang),
        bg=PANEL,
        fg=TEXT,
        font=(FONT, 13, "bold"),
    ).pack(side="left")

    refs = tk.Frame(panel, bg=PANEL)
    refs.pack(fill="x", padx=12, pady=(0, 6))
    for name, heat_text, reading in view.get("参考行") or []:
        heat = None if heat_text == DASH else float(heat_text)
        row = tk.Frame(refs, bg=PANEL)
        row.pack(fill="x", pady=(2, 0))
        tk.Label(
            row, text=term(name, lang), bg=PANEL, fg=MUTED, font=(FONT, 10), anchor="w"
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            row,
            text=f"{heat_text} · {reading}",
            bg=PANEL,
            fg=_heat_color(heat),
            font=(MONO, 14, "bold"),
        ).pack(side="left")

    footnote = tk.Label(
        panel,
        text=t("三条大盘只做参考，不参与温度计算（0=冰点 100=泡沫）", lang),
        bg=PANEL,
        fg=MUTED,
        font=(FONT, 10),
        justify="left",
        anchor="w",
    )
    footnote.pack(anchor="w", padx=12, pady=(0, 10))
    _auto_wrap(footnote, reserve=24)


def _holding_block(parent: tk.Frame, view: dict, lang: str = LANG_ZH) -> None:
    """持仓温度专区：温度怎么来的、落在哪档、要动多少钱。"""
    panel = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
    panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

    head = tk.Frame(panel, bg=PANEL)
    head.pack(fill="x", padx=12, pady=(10, 6))
    tk.Label(
        head,
        text=t("持仓温度", lang),
        bg=PANEL,
        fg=TEXT,
        font=(FONT, 13, "bold"),
    ).pack(side="left")

    plan = view.get("建议")
    temperature = view.get("温度")
    if plan is None:
        tk.Label(
            head,
            text=t("点击「刷新」拉取估值数据", lang),
            bg=PANEL,
            fg=MUTED,
            font=(MONO, 10),
        ).pack(side="right")
        hint = tk.Label(
            panel,
            text=t("由每只持仓自己的逆向指标按市值加权合成", lang),
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 10),
            justify="left",
            anchor="w",
        )
        hint.pack(anchor="w", padx=12, pady=(0, 12))
        _auto_wrap(hint, reserve=24)
        return

    rows: list[tuple[str, str, str]] = [
        (
            t("持仓温度", lang),
            f"{plan['温度']:.1f} / 100 · {heat_reading(temperature, lang)}",
            _heat_color(temperature),
        ),
        (t("判定档位", lang), plan["档位"], TEXT),
    ]
    current_pct = view.get("当前现金pct")
    plan_cash = plan["现金"]
    cash_label = f"{plan_cash:.1f}%"
    if current_pct is not None:
        cash_label = f"{current_pct:.1f}% → {plan_cash:.1f}%"
    rows.append((t("现金建议", lang), cash_label, TEXT))
    if view.get("操作"):
        rows.append((t("合理操作", lang), str(view["操作"]), _action_color(str(view["操作"]))))
    skipped = view.get("跳过") or []
    if skipped:
        rows.append((t("无足够历史", lang), _join(skipped, lang), MUTED))

    for label, value, color in rows:
        line = tk.Frame(panel, bg=PANEL)
        line.pack(fill="x", padx=12, pady=(2, 0))
        lab = tk.Label(line, text=label, bg=PANEL, fg=MUTED, font=(FONT, 10), anchor="w")
        lab.pack(side="left", padx=(0, 8))
        val = tk.Label(
            line,
            text=value,
            bg=PANEL,
            fg=color,
            font=(MONO, 11),
            anchor="w",
            justify="left",
        )
        val.pack(side="left", fill="x", expand=True)
        _auto_wrap(val, reserve=lambda: lab.winfo_reqwidth() + 8)
    tk.Frame(panel, bg=PANEL, height=10).pack()


def _fill_body(
    parent: tk.Frame,
    book: dict,
    enriched: list[dict],
    summary: dict | None,
    view: dict | None = None,
    lang: str = LANG_ZH,
) -> None:
    _summary_row(parent, summary, view, lang)
    if view is not None:
        _temp_row = tk.Frame(parent, bg=BG)
        _temp_row.pack(fill="x", padx=22, pady=(0, 12))
        _temp_row.columnconfigure(0, weight=2)
        _temp_row.columnconfigure(1, weight=3, minsize=400)
        _temp_row.rowconfigure(0, weight=1)
        _valuation_block(_temp_row, view, lang)
        _holding_block(_temp_row, view, lang)
    if summary is not None:
        _chart_row(parent, book, enriched, summary, lang)
    _category_blocks(parent, book, enriched, summary, view, lang)


def show(
    book: dict,
    enriched: list[dict],
    summary: dict | None,
    quotes: dict | None = None,
    rates: dict | None = None,
    view: dict | None = None,
    initial_status: str = "initial",
) -> None:
    """打开持仓窗口。右上角按钮切换中英；刷新时清空 body 再按最新报价重建。"""
    state = {
        "enriched": enriched,
        "summary": summary,
        "quotes": quotes,
        "rates": rates,
        "view": view,
        "lang": default_lang(book),
        "status": (initial_status, {}),
    }
    root = tk.Tk()
    base = str(book.get("本位币") or "CNY")

    def window_title() -> str:
        return (
            f"{book.get('标题') or t('股票持仓', state['lang'])}"
            f" · {t('本位币', state['lang'])} {base}"
        )

    root.title(window_title())
    root.geometry("1080x760")
    root.minsize(860, 640)
    root.configure(bg=BG)
    _apply_style(root)

    tk.Frame(root, bg=GOLD, height=2).pack(fill="x")

    header = tk.Frame(root, bg=BG)
    header.pack(fill="x", padx=22, pady=(14, 10))
    title_row = tk.Frame(header, bg=BG)
    title_row.pack(fill="x")
    tk.Label(title_row, text="STOCK", bg=BG, fg=GOLD, font=(MONO, 11, "bold")).pack(side="left")

    # 刷新按钮钉最右；语言按钮在其左侧
    refresh_btn = tk.Button(
        title_row,
        text=t("刷新", state["lang"]),
        command=lambda: on_refresh(),
        bg=PANEL,
        fg=TEXT,
        activebackground=CARD,
        activeforeground=TEXT,
        relief="flat",
        bd=0,
        padx=14,
        pady=4,
        font=(FONT, 10),
        cursor="hand2",
    )
    refresh_btn.pack(side="right")
    lang_btn = tk.Button(
        title_row,
        text=t("EN", state["lang"]),
        command=lambda: toggle_lang(),
        bg=PANEL,
        fg=GOLD,
        activebackground=CARD,
        activeforeground=GOLD,
        relief="flat",
        bd=0,
        padx=12,
        pady=4,
        font=(FONT, 10),
        cursor="hand2",
    )
    lang_btn.pack(side="right", padx=(0, 8))

    status_var = tk.StringVar()
    status_label = tk.Label(
        title_row,
        textvariable=status_var,
        bg=BG,
        fg=TEXT,
        font=(FONT, 10),
        anchor="e",
    )
    status_label.pack(side="right", padx=(0, 12))

    def render_status() -> None:
        key, params = state["status"]
        status_var.set(status_text(key, state["lang"], **params))
        if key == "busy":
            status_label.configure(fg=GOLD)
        elif key in ("noquote", "error"):
            status_label.configure(fg=LOSS)
        elif key == "ok" and not params.get("fail"):
            status_label.configure(fg=MINT)
        elif key == "ok":
            status_label.configure(fg=LOSS)
        else:
            status_label.configure(fg=MUTED)

    def set_status(key: str, **params) -> None:
        state["status"] = (key, params)
        render_status()

    render_status()

    tk.Label(
        header,
        text=book.get("标题") or t("股票持仓", state["lang"]),
        bg=BG,
        fg=TEXT,
        font=(FONT, 20, "bold"),
    ).pack(anchor="w")

    holder: dict = {"body": None, "scroll": None}

    def on_wheel(event) -> None:
        canvas = holder.get("scroll")
        if canvas is not None and canvas.winfo_exists():
            canvas.yview_scroll(int(-event.delta / 120), "units")

    root.bind_all("<MouseWheel>", on_wheel)

    def mount_body() -> None:
        previous = holder.get("body")
        if previous is not None and previous.winfo_exists():
            previous.destroy()
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)
        holder["body"] = body

        scroll_wrap = tk.Frame(body, bg=BG)
        scroll_wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroll_wrap, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(scroll_wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def sync_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def stretch(event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        inner.bind("<Configure>", sync_region)
        canvas.bind("<Configure>", stretch)
        holder["scroll"] = canvas
        _fill_body(inner, book, state["enriched"], state["summary"], state["view"], state["lang"])

    def toggle_lang() -> None:
        new_lang = LANG_EN if state["lang"] == LANG_ZH else LANG_ZH
        state["lang"] = new_lang
        # 结果里的语言字段（档位/操作/参考行读数/大类操作）按新语言重生成，不重新联网
        state["summary"] = relocalize_summary(
            book, state["enriched"], state["rates"], state["summary"], new_lang
        )
        state["view"] = relocalize_view(state["view"], book, new_lang)
        lang_btn.configure(text=t("EN", new_lang))
        refresh_btn.configure(text=t("刷新", new_lang))
        root.title(window_title())
        render_status()
        mount_body()

    def on_refresh() -> None:
        refresh_btn.configure(state="disabled")
        set_status("busy")

        def work() -> None:
            error: str | None = None
            new_quotes: dict | None = None
            new_rates: dict | None = None
            new_enriched: list[dict] | None = None
            new_summary: dict | None = None
            new_view: dict | None = None
            ok = 0
            fail = 0
            try:
                new_quotes, new_rates = fetch_all(book)
                new_enriched = enrich_holdings(book, new_quotes, new_rates)
                new_summary = summarize(book, new_enriched, new_rates)
                ok = sum(1 for row in new_enriched if row["可汇总"])
                fail = sum(
                    1
                    for row in new_enriched
                    if _counts_in_quote_status(row) and not row["可汇总"]
                )
                cfg = valuation_config(book)
                references = benchmark_heats(cfg)
                holdings = holding_heats(new_enriched, cfg)
                new_view = valuation_view(
                    references, holdings, new_summary, cfg["现金规则"], state["lang"]
                )
            except Exception as exc:  # noqa: BLE001 — 后台线程统一回传到 UI
                error = str(exc)

            def apply() -> None:
                if not root.winfo_exists():
                    return
                if error is not None:
                    set_status("error", msg=error)
                elif new_summary is None:
                    state["quotes"] = new_quotes
                    state["rates"] = new_rates
                    state["enriched"] = new_enriched
                    state["summary"] = new_summary
                    state["view"] = new_view
                    set_status("noquote")
                    mount_body()
                else:
                    state["quotes"] = new_quotes
                    state["rates"] = new_rates
                    state["enriched"] = new_enriched
                    state["summary"] = new_summary
                    state["view"] = new_view
                    set_status("ok", ok=ok, fail=fail)
                    mount_body()
                if refresh_btn.winfo_exists():
                    refresh_btn.configure(state="normal")

            root.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    mount_body()
    root.mainloop()
