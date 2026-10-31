# stockfolio

股票持仓温度计：拉行情/汇率 → 计算持仓温度 → 按你的现金规则给出建议。浏览器访问，密码可选，零第三方依赖。

A personal stock thermometer: fetch quotes/FX → compute portfolio heat → suggest cash by your rules. Browser access with optional password, zero dependencies.

## 快速开始 Quick start

在 `stockfolio/` 目录内运行。Run inside `stockfolio/`.

```bash
python -m stockfolio --serve                            # 本机访问 / local
python -m stockfolio --serve --book example_book.json   # 指定持仓 / custom book
# 持仓格式见 DATA_FORMAT.md / Format: DATA_FORMAT.md
python -m stockfolio --check example_book.json          # 离线格式校验 / format check
python -m stockfolio --ping example_book.json           # 联网取数检查 / network check
```

## 密码（可选）Password (optional)

```bash
python -m stockfolio --set-password   # 开启密码 / enable password
python -m stockfolio --no-password    # 关闭密码 / disable password
```

- 未设置密码则直接进入；HTTP 仅限本机或可信局域网，公网请置于 HTTPS 反向代理之后。
  No password = direct access; HTTP for local/trusted LAN only; public use requires an HTTPS reverse proxy.
