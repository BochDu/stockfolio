"""命令行入口：python -m stockfolio [--book PATH] [--smoke]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import stockfolio

DEFAULT_BOOK = Path("data/stock.json")
EXAMPLE_BOOK = Path(__file__).resolve().parent.parent / "example_book.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="stockfolio —— 股票仓位温度计：行情/日线 + 可配置现金规则建议"
    )
    parser.add_argument(
        "--book",
        default=None,
        help="持仓 JSON 路径（默认 ./data/stock.json，不存在则用 examples/example_book.json）",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="启动即联网拉取报价（默认离线启动，点界面「刷新」联网）",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="只跑离线数据管道并打印摘要 JSON，不打开窗口",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验持仓 JSON（全部字段），不打开窗口；错误时退出码 1",
    )
    args = parser.parse_args()

    book_path = args.book
    if book_path is None:
        book_path = DEFAULT_BOOK if DEFAULT_BOOK.exists() else EXAMPLE_BOOK

    # 运行前先校验：--check 只校验；其余模式校验不通过则列出全部错误并退出
    problems = stockfolio.validate_file(book_path)
    if args.check:
        if problems:
            print(f"校验未通过（{len(problems)} 项）：")
            for problem in problems:
                print(f"  ✗ {problem}")
            sys.exit(1)
        print("校验通过 ✓")
        return
    if problems:
        print(f"校验未通过（{len(problems)} 项）：", file=sys.stderr)
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        print("请修复后重试，或先用 `--check` 复查。", file=sys.stderr)
        sys.exit(1)

    if args.smoke:
        result = stockfolio.smoke(book_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    stockfolio.run_book(book_path, offline=not args.online)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
