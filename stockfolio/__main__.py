"""命令行入口：python -m stockfolio [--book PATH] [--smoke] [--serve]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import stockfolio

DEFAULT_BOOK = Path("data/stock.json")


def _example_book() -> Path:
    """源码根目录优先，安装后回退到包内置副本。"""
    source_root = Path(__file__).resolve().parent.parent / "example_book.json"
    if source_root.exists():
        return source_root
    return Path(__file__).resolve().parent / "example_book.json"


EXAMPLE_BOOK = _example_book()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="stockfolio —— 股票仓位温度计：行情/日线 + 可配置现金规则建议"
    )
    parser.add_argument(
        "--book",
        default=None,
        help="持仓 JSON 路径（默认 ./data/stock.json，不存在则用内置 example_book.json）",
    )
    parser.add_argument(
        "book_file",
        nargs="?",
        default=None,
        help="持仓 JSON 路径（可省略 --book）",
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
        help="离线校验持仓 JSON 格式，不联网；失败时退出码 1",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="联网检查持仓所需行情/汇率/日线能否获取；失败时退出码 1",
    )
    parser.add_argument(
        "--set-password",
        action="store_true",
        help="可选：设置 Web 访问密码后退出；不设置则直接进入",
    )
    parser.add_argument(
        "--no-password",
        action="store_true",
        help="关闭访问密码（删除密码文件）后退出；之后 --serve 直接进入",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="配合 --set-password 非交互设置密码（会留在命令历史，建议省略以交互输入）",
    )
    parser.add_argument(
        "--auth-file",
        default=None,
        help="Web 密码文件路径（默认 ~/.stockfolio/auth.json）",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动 Web 服务，用浏览器访问持仓（默认仅本机）",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Web 监听地址（默认 127.0.0.1；--host 0.0.0.0 开放局域网）",
    )
    parser.add_argument("--port", type=int, default=8000, help="Web 端口（默认 8000）")
    args = parser.parse_args()

    if args.no_password:
        path = stockfolio.clear_password(args.auth_file)
        print(f"已关闭密码: {path}")
        if not args.serve:
            return

    if args.set_password:
        path = stockfolio.set_password(args.auth_file, password=args.password)
        print(f"密码已写入: {path}")
        return

    book_path = args.book or args.book_file
    if book_path is None:
        book_path = DEFAULT_BOOK if DEFAULT_BOOK.exists() else EXAMPLE_BOOK

    # --check/--ping 为一次性动作，优先于 --serve，不会启动 Web
    # --check 只做离线格式校验；--ping 联网探测行情/汇率/日线
    if args.check:
        problems = stockfolio.validate_file(book_path)
        if problems:
            print(f"数据格式未通过（{len(problems)} 项）：")
            for problem in problems:
                print(f"  [FAIL] {problem}")
            sys.exit(1)
        print("数据格式 [OK]")
        sys.exit(0)

    if args.ping:
        result = stockfolio.check_book(book_path)
        errors = result["格式"]["错误"]
        if errors:
            print(f"数据格式未通过（{len(errors)} 项），无法联网检查：")
            for problem in errors:
                print(f"  [FAIL] {problem}")
            sys.exit(1)
        print(f"联网检查：{book_path}")
        for item in result["接口"]:
            mark = "[OK]" if item["ok"] else "[FAIL]"
            print(f"  {mark} {item['名称']}: {item['说明']}")
        sys.exit(0 if result["ok"] else 1)

    problems = stockfolio.validate_file(book_path)
    if problems:
        print(f"校验未通过（{len(problems)} 项）：", file=sys.stderr)
        for problem in problems:
            print(f"  [FAIL] {problem}", file=sys.stderr)
        print("请修复后重试，或先用 `--check` 复查。", file=sys.stderr)
        sys.exit(1)

    if args.serve:
        stockfolio.serve(
            book_path,
            host=args.host,
            port=args.port,
            auth_file=args.auth_file,
            password=args.password,
        )
        return

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
