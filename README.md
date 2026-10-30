# stockfolio

持仓股票簿：加载持仓 JSON → 拉行情/汇率 → 合成市场与持仓温度 → 按你定义的现金规则给出建议。
零第三方依赖（标准库 `urllib` + `tkinter`），Python 3.10+。

A stock dashboard: load holdings JSON → fetch quotes/FX → compute market & portfolio heat → suggest cash by your own rules. Zero third-party deps (stdlib only), Python 3.10+.

## 运行 Run

```bash
python -m stockfolio                                   # 用示例 example_book.json
python -m stockfolio --book example_book.json          # 指定持仓文件
python -m stockfolio --book example_book.json --online # 启动即联网
python -m stockfolio --book example_book.json --smoke  # 只跑数据管道，打印 JSON
python -m stockfolio --book example_book.json --check  # 运行前先校验 JSON
```

路径换成你自己的文件即可。Replace with your own file path.

自定义 JSON 运行前先校验：`--check` 会一次查完大类占比、现金规则、代码格式、货币等全部字段，列出所有错误。
Validate your custom JSON before running: `--check` verifies category weights, cash rules, codes, currencies, etc., and lists every error at once.

界面上点「刷新 / Refresh」联网，右上角按钮切换中英。
Click **Refresh** to fetch; the top-right button toggles 中文 / English.

## 数据格式 Data format

字段说明见 `DATA_FORMAT.md`（中英双语）；完整示例 `example_book.json`。
See `DATA_FORMAT.md` for the JSON format (zh & en); full sample: `example_book.json`.

## 特性 Features

- 持仓/现金支持 24 种货币，统一按本位币折算（中文单位 `w`=万，英文 `k`=千）
  24 currencies, unified in your base currency (zh `w`=10k, en `k`=1k)
- 温度 → 现金规则由 JSON 自由定义，改数据不改代码
  Temperature → cash rules are yours to define in JSON
- 中英界面切换，默认语言 JSON 可配
  Built-in 中文/English toggle; default language set in JSON

## 已知限制 Known limits

- 美股持仓/美股基准无温度：日线数据源对 `usXXX` 只回 1 天，显示「无足够历史」；实时价格与盈亏不受影响。
  US holdings/benchmarks have no heat: the daily-kline source returns only 1 day for `usXXX` (shown as “Insufficient History”); live prices and P&L are unaffected.

## License

MIT
