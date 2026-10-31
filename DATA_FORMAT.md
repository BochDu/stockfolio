# 数据格式 · Data Format

本文件讲解 `stockfolio` 持仓 JSON 的每个字段；完整可运行示例见 `example_book.json`。
This document explains every field of the stockfolio book JSON; see `example_book.json` for a runnable sample.

> 自定义 JSON 运行前先校验（一次查完全部字段，列出所有错误）：
> Validate your custom JSON before running — it checks every field and lists all errors at once:
> ```bash
> python -m stockfolio --book example_book.json --check   # 换成你自己的文件路径
> ```

---

## 1. 顶层字段 · Top-level fields

| 字段 Field | 中文说明 | English |
|---|---|---|
| `标题` | 界面大标题（必填，可为任意文本） | Window title (required, any text) |
| `语言` | 界面默认语言：`zh` 中文 / `en` 英文，缺省 `zh`；也可写 `language` | Default UI language: `zh` / `en`, default `zh`; key `language` also accepted |
| `本位币` | 记账基准币种，24 种货币任选（见 §5），缺省 `CNY`；所有市值/现金/目标/建议金额都折算到它，非法币种启动报错 | Base currency for reporting; any of the 24 currencies (see §5), default `CNY`; invalid value raises on load |
| `目标总仓` | 远期目标总仓位（万本位币），可选 | Long-term target portfolio value (in 10k base units), optional |
| `现金` | 按币种的现金金额，如 `{"CNY": 100000, "JPY": 5000000}` | Cash amount per currency |

```json
{
  "标题": "示例组合（替换为你的持仓）",
  "语言": "zh",
  "本位币": "CNY",
  "目标总仓": 100,
  "现金": { "CNY": 100000, "HKD": 50000, "USD": 20000 }
}
```

## 2. 大类 · Categories

按权重分配目标仓位，`比例` 为 0~1 的小数，**总和必须为 1**（允许 0.001 误差），否则加载报错。
各大类「目标仓」= 目标总仓 × 比例（设定目标总仓时），为远期绝对目标；「平衡」= 当前股票总市值 × 比例 − 当前仓，表示在保持整体股票仓位不变的前提下该大类需调增/调减的金额（相对当前总仓的配置偏离）。

Weight-based target allocation; `比例` is a 0–1 fraction and **the sum must equal 1** (tolerance 0.001), otherwise the book is rejected on load.
Category `目标仓` = target portfolio × ratio (when set), the long-term absolute target; `平衡` = current stock total value × ratio − 当前仓, the relative deviation from the current total (how much to add/reduce while keeping the overall stock position unchanged).

```json
"大类": [
  { "名称": "稳健", "比例": 0.3 },
  { "名称": "成长", "比例": 0.3 },
  { "名称": "海外", "比例": 0.4 }
]
```

## 3. 估值 · Valuation

| 字段 Field | 中文说明 | English |
|---|---|---|
| `回看交易日` | 历史回看天数（默认 1200） | Historical lookback in trading days (default 1200) |
| `均线窗口` | 均线天数（默认 250） | Moving-average window (default 250) |
| `参考基准` | 三条大盘只做参考、不参与温度合成，`名称`/`代码` 可自由改 | Three benchmarks for reference only (not part of portfolio heat); `名称`/`代码` editable |
| `现金规则` | **必填**：温度 → 建议现金% 的锚点列表，见下方规则 | **Required**: anchor list mapping temperature → suggested cash %, see rules below |

### 现金规则 · Cash rules

`现金规则` 是一组锚点：`温度` 0–100，`名称` 显示在界面档位，`现金` 是该温度下建议现金占总投资的比例（%）。
锚点按温度排序，锚点间**线性插值**，区间外取端点值。锚点数量 ≥2，缺失或字段非法会在启动时抛错。可自由增删改——这就是"现金留多少"的规则唯一来源，改 JSON 即可，不用动代码。

`现金规则` is a list of anchors: `温度` 0–100, `名称` shown as the tier label, `现金` = suggested cash ratio (%) of total investment at that temperature. Anchors are sorted by temperature, interpolated linearly between anchors, clamped at the ends. At least 2 anchors required; missing or invalid fields raise on load. Add/remove/edit freely — it is the single source of your cash rules, no code changes needed.

```json
"估值": {
  "回看交易日": 1200,
  "均线窗口": 250,
  "参考基准": [
    { "名称": "A股", "代码": "sh000300" },
    { "名称": "港股", "代码": "hkHSI" },
    { "名称": "美股", "代码": "usIXIC" }
  ],
  "现金规则": [
    { "温度": 0,   "名称": "极度悲观", "现金": 0 },
    { "温度": 33.3, "名称": "合理低估", "现金": 10 },
    { "温度": 66.7, "名称": "乐观高估", "现金": 20 },
    { "温度": 100, "名称": "全面高估", "现金": 30 }
  ]
}
```

## 4. 持仓 · Holdings

每只标的一条记录。`现价` 可选：手填后离线也能算盈亏；联网「刷新」后自动用实时价覆盖。

One record per holding. `现价` is optional: with a manual price, P&L works offline; clicking Refresh replaces it with the live price.

| 字段 Field | 中文说明 | English |
|---|---|---|
| `标的` | 显示名称（必填） | Display name (required) |
| `代码` | 行情代码，格式见下方 | Quote code, see below |
| `大类` | 归属的大类名称（必须已在「大类」中定义） | Category (must match a defined category) |
| `货币` | 该标的计价币种（24 种之一） | Currency of the holding (one of 24) |
| `数量` | 持有数量；`0` 为观察仓（不联网取价，有手填现价才显示） | Shares held; `0` = watchlist item (no live quote; shown only with a manual price) |
| `成本价` | 买入均价 | Average cost |
| `现价` | 可选，手填价格 | Optional manual price |

```json
"持仓": [
  { "标的": "示例·沪深300ETF", "代码": "510300.SS", "大类": "稳健", "货币": "CNY", "数量": 1000, "成本价": 4.0 },
  { "标的": "示例·盈富基金",   "代码": "02800.HK",  "大类": "成长", "货币": "HKD", "数量": 1000, "成本价": 22.0, "现价": 23.0 },
  { "标的": "示例·标普500",    "代码": "SPY",       "大类": "海外", "货币": "USD", "数量": 10,   "成本价": 500.0 }
]
```

### 代码格式 · Quote codes

| 市场 Market | 格式 Format | 示例 Example |
|---|---|---|
| A股 | `600036.SS`（上交所）/ `000001.SZ`（深交所） | `600036.SS` |
| 港股 | `0700.HK`（5 位数字） | `0700.HK` |
| 美股 | 直接代码 | `VOO`、`BRK-B` |

## 5. 支持的货币 · Supported currencies (24)

`CNY`、`USD`、`HKD`、`JPY`、`EUR`、`GBP`、`AUD`、`CAD`、`CHF`、`SGD`、`NZD`、`KRW`、`TWD`、`THB`、`MOP`、`MYR`、`RUB`、`INR`、`IDR`、`PHP`、`DKK`、`SEK`、`NOK`、`ZAR`

汇率来自新浪外汇（X 兑人民币），刷新时一次拉取，统一换算到本位币口径。注意：股票行情源只覆盖 A股/港股/美股，非这三种市场的标的需手填「现价」才能折算。

FX rates come from Sina FX (X → CNY), fetched on refresh and converted into your base currency. Note: equity quotes cover only A/HK/US markets — holdings in other markets need a manual `现价` to convert.

## 6. 单位与温度 · Units & heat

- 金额单位：中文界面 `w` = 万本位币；英文界面 `k` = 千本位币（数值 ×10）
  Money units: zh `w` = 10k base units; en `k` = 1k base units (×10)
- 温度 0–100：分位、均线偏离、高点回撤、成交比等逆向指标合成；越高越贵，越低越便宜
  Heat 0–100 synthesized from percentile/MA-deviation/drawdown/volume-ratio; higher = pricier
- 大盘温度仅作参考，不参与持仓温度合成
  Benchmark heat is reference only
- **美股无温度**：日线数据源对 `usXXX`（个股与 `usIXIC` 等美股基准）只回 1 天，无法合成逆向指标，界面显示「无足够历史」；实时价格与盈亏仍正常。
  **No heat for US symbols**: the daily-kline source returns only 1 day for `usXXX` (US holdings and US benchmarks), so their heat shows "Insufficient History"; live prices and P&L still work.
