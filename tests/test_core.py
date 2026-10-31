"""stockfolio 核心逻辑测试（仅标准库，不联网）。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import stockfolio
from stockfolio import analysis, config, gui, i18n, market

EXAMPLE = Path(__file__).resolve().parent.parent / "example_book.json"

# 测试用规则（与示例 JSON 相同）；规则唯一来源是 JSON，代码不内置默认
DEFAULT_CASH_RULES = [
    {"温度": 0.0, "名称": "极度悲观", "现金": 0.0},
    {"温度": 33.3, "名称": "合理低估", "现金": 10.0},
    {"温度": 66.7, "名称": "乐观高估", "现金": 20.0},
    {"温度": 100.0, "名称": "全面高估", "现金": 30.0},
]


class LoadBookTest(unittest.TestCase):
    def test_load_example(self):
        book = stockfolio.load_book(EXAMPLE)
        self.assertIn("大类", book)
        self.assertTrue(book["持仓"])
        for h in book["持仓"]:
            self.assertIsInstance(h["数量"], float)
            self.assertIsInstance(h["成本价"], float)

    def test_currency_keys_normalized(self):
        import tempfile

        book = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        book["现金"] = {"cny": 1000}
        book["持仓"][0]["货币"] = "cny"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lower.json"
            p.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
            loaded = stockfolio.load_book(p)
        self.assertEqual(loaded["持仓"][0]["货币"], "CNY")
        self.assertIn("CNY", loaded["现金"])
        quotes, rates = market.offline_quotes_rates(loaded)
        rows = analysis.enrich_holdings(loaded, quotes, rates)
        self.assertTrue(rows[0]["可汇总"])

    def test_missing_field_raises(self):
        bad = {"大类": [{"名称": "A", "比例": 1.0}], "持仓": [{"标的": "X"}]}
        # 直接调内部校验路径：写临时文件太重，验证 load_book 对缺字段的报错
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                stockfolio.load_book(p)


class PackagingTest(unittest.TestCase):
    def test_bundled_demo_matches_root(self):
        root = EXAMPLE
        bundled = root.parent / "stockfolio" / "example_book.json"
        self.assertTrue(bundled.exists())
        self.assertEqual(
            json.loads(root.read_text(encoding="utf-8")),
            json.loads(bundled.read_text(encoding="utf-8")),
        )


class OfflinePipelineTest(unittest.TestCase):
    def test_enrich_summarize_offline(self):
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        summary = analysis.summarize(book, enriched, rates)
        self.assertIsNotNone(summary)
        # 离线只取本位币（CNY）汇率：CNY 持仓可汇总，HKD/USD 持仓等待联网
        self.assertEqual(summary["报价成功"], 2)
        self.assertEqual(summary["本位币"], "CNY")
        rows = {r["标的"]: r for r in enriched}
        self.assertAlmostEqual(rows["示例·沪深300ETF"]["市值_本位"], 1000 * 4.2)
        self.assertFalse(rows["示例·标普500"]["可汇总"])
        self.assertAlmostEqual(
            summary["当前总仓_w"], (1000 * 4.2 + 1500 * 3.0) / 10000, places=2
        )

    def test_target_total(self):
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        self.assertEqual(analysis.summarize(book, enriched, rates)["目标总仓"], 100)

    def test_target_total_ignores_legacy_key(self):
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        book.pop("目标总仓")
        book["配额总仓"] = 60  # 旧字段不再生效
        summary = analysis.summarize(book, enriched, rates)
        self.assertIsNone(summary["目标总仓"])
        self.assertTrue(all(cat["目标仓"] is None for cat in summary["大类"]))
        self.assertTrue(all(cat["平衡"] is None for cat in summary["大类"]))

    def test_category_four_metrics(self):
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        summary = analysis.summarize(book, enriched, rates)
        steady = next(c for c in summary["大类"] if c["名称"] == "稳健")
        # 目标 = 目标总仓 100w × 40%；当前仅人民币两只可取价
        self.assertAlmostEqual(steady["目标仓"], 40.0)
        self.assertAlmostEqual(steady["当前仓"], 0.87, places=2)
        self.assertAlmostEqual(steady["当前占比"], 100.0)
        self.assertAlmostEqual(steady["平衡"], 39.13, places=2)
        self.assertTrue(steady["操作"].startswith("加仓"))

    def test_category_without_target_total(self):
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        book.pop("目标总仓")
        summary = analysis.summarize(book, enriched, rates)
        for cat in summary["大类"]:
            self.assertIsNone(cat["目标仓"])
            self.assertIsNone(cat["平衡"])
            self.assertIsNone(cat["操作"])
            self.assertIsNotNone(cat["当前占比"])


class RatesTest(unittest.TestCase):
    """本位币汇率换算：mock 新浪响应，验证三种本位币口径。"""

    TEXT = (
        'var hq_str_fx_susdcny="a,6.65,6.66,0";\n'
        'var hq_str_fx_shkdcny="a,0.85,0.86,0";\n'
        'var hq_str_fx_sjpycny="a,0.0423,0.0424,0";\n'
        'var hq_str_fx_seurcny="a,7.41,7.43,0";\n'
    )

    class _FakeResp:
        def __init__(self, text):
            self._text = text

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._text.encode("gbk")

    class _FakeOpen:
        def __init__(self, text):
            self._text = text

        def __call__(self, req, timeout=8):
            return RatesTest._FakeResp(self._text)

    def _rates(self, base):
        return market.fetch_rates(opener=self._FakeOpen(self.TEXT), base=base)

    def test_base_cny(self):
        rates = self._rates("CNY")
        self.assertEqual(rates["CNY"], 1.0)
        self.assertAlmostEqual(rates["HKD"], 0.855, places=6)
        self.assertAlmostEqual(rates["USD"], 6.655, places=6)

    def test_base_usd(self):
        rates = self._rates("USD")
        self.assertAlmostEqual(rates["USD"], 1.0, places=6)
        self.assertAlmostEqual(rates["CNY"], 1 / 6.655, places=6)
        self.assertAlmostEqual(rates["HKD"], 0.855 / 6.655, places=6)

    def test_base_hkd(self):
        rates = self._rates("HKD")
        self.assertAlmostEqual(rates["HKD"], 1.0, places=6)
        self.assertAlmostEqual(rates["CNY"], 1 / 0.855, places=6)
        self.assertAlmostEqual(rates["USD"], 6.655 / 0.855, places=6)

    def test_base_jpy(self):
        """日元本位币：除 JPY 外的其它币种全部按 jpy_cny 换算。"""
        rates = self._rates("JPY")
        jpy_cny = 0.04235
        self.assertAlmostEqual(rates["JPY"], 1.0, places=6)
        self.assertAlmostEqual(rates["CNY"], 1 / jpy_cny, places=4)
        self.assertAlmostEqual(rates["USD"], 6.655 / jpy_cny, places=4)
        self.assertAlmostEqual(rates["EUR"], 7.42 / jpy_cny, places=4)
        self.assertAlmostEqual(rates["HKD"], 0.855 / jpy_cny, places=4)

    def test_base_rate_missing_degrades(self):
        # 本位币汇率拉不到：只有本位币本身可折算，其他币种记 None
        text = 'var hq_str_fx_susdcny="";\nvar hq_str_fx_shkdcny="a,0.85,0.86,0";\n'
        rates = market.fetch_rates(opener=self._FakeOpen(text), base="USD")
        self.assertEqual(rates["USD"], 1.0)
        self.assertIsNone(rates["CNY"])
        self.assertIsNone(rates["HKD"])
        self.assertIsNone(rates["JPY"])

    def test_resolve_base_and_offline(self):
        book = {"本位币": "USD"}
        self.assertEqual(market.resolve_base(book), "USD")
        self.assertEqual(market.resolve_base({}), "CNY")
        self.assertEqual(market.resolve_base({"本位币": "JPY"}), "JPY")  # 全覆盖：日元合法
        quotes, rates = market.offline_quotes_rates({"本位币": "USD", "持仓": []})
        # 离线覆盖全部币种：本位币 1.0，其余 None
        self.assertEqual(rates["USD"], 1.0)
        self.assertIsNone(rates["CNY"])
        self.assertIsNone(rates["HKD"])
        self.assertIsNone(rates["JPY"])
        self.assertIsNone(rates["EUR"])
        self.assertEqual(len(rates), len(market.SUPPORTED_CURRENCIES))

    def test_invalid_base_raises(self):
        with self.assertRaises(ValueError):
            market.resolve_base({"本位币": "BTC"})


class I18nTest(unittest.TestCase):
    def test_default_lang(self):
        self.assertEqual(i18n.default_lang({}), "zh")
        self.assertEqual(i18n.default_lang({"语言": "en"}), "en")
        self.assertEqual(i18n.default_lang({"language": "EN-US"}), "en")
        self.assertEqual(i18n.default_lang({"语言": "法语"}), "zh")

    def test_ui_text(self):
        self.assertEqual(i18n.t("刷新", "zh"), "刷新")
        self.assertEqual(i18n.t("刷新", "en"), "Refresh")
        self.assertEqual(i18n.t("市值(w)", "en"), "Value(k)")
        self.assertEqual(i18n.t("未知键", "en"), "未知键")  # 未注册原样

    def test_status_text(self):
        self.assertEqual(i18n.status_text("initial", "en"), "Offline — click Refresh to fetch")
        self.assertEqual(i18n.status_text("ok", "en", ok=11, fail=2), "Quotes 11 ok / 2 degraded")

    def test_term_mapping(self):
        self.assertEqual(i18n.term("极度悲观", "en"), "Extremely Pessimistic")
        self.assertEqual(i18n.term("合理低估", "en"), "Fairly Undervalued")
        self.assertEqual(i18n.term("A股", "en"), "A-Shares")
        self.assertEqual(i18n.term("自定义档位", "en"), "自定义档位")  # 未命中原样
        self.assertEqual(i18n.term("极度悲观", "zh"), "极度悲观")

    def test_unit_conversion(self):
        self.assertEqual(i18n.unit_w("zh"), "w")
        self.assertEqual(i18n.unit_w("en"), "k")
        self.assertAlmostEqual(i18n.to_display(19.68, "zh"), 19.68)
        self.assertAlmostEqual(i18n.to_display(19.68, "en"), 196.8)

    def test_action_text_en(self):
        self.assertEqual(analysis.action_text(10, 8, "en"), "Add 20.0 k")
        self.assertEqual(analysis.action_text(8, 10, "en"), "Reduce 20.0 k")
        self.assertEqual(analysis.action_text(10, 10, "en"), "Flat")
        self.assertEqual(analysis.action_text(10, 8, "zh"), "加仓 2.0")

    def test_heat_reading_en(self):
        self.assertEqual(analysis.heat_reading(30, "en"), "Cool")
        self.assertEqual(analysis.heat_reading(90, "en"), "Overheated")

    def test_ladder_zone_en(self):
        plan = analysis.ladder_plan(50, DEFAULT_CASH_RULES, "en")
        self.assertIn("Between Fairly Undervalued and Optimistically Valued", plan["档位"])
        plan_edge = analysis.ladder_plan(3, DEFAULT_CASH_RULES, "en")
        self.assertEqual(plan_edge["档位"], "Extremely Pessimistic")

    def test_summarize_lang(self):
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        sm_en = analysis.summarize(book, enriched, rates, lang="en")
        op_en = sm_en["大类"][0]["操作"]
        self.assertIn(op_en[:3], ("Add", "Red"))  # 加仓/减仓的英文
        self.assertTrue(op_en.endswith(" k"))
        sm_zh = analysis.summarize(book, enriched, rates)
        self.assertTrue(sm_zh["大类"][0]["操作"].startswith(("加仓", "减仓")))

    def _view_zh(self, book, summary):
        cfg = stockfolio.valuation_config(book)
        refs = [{"名称": "A股", "热度": 42.0, "可用": True}]
        holdings = {
            "温度": 50.0,
            "窗口": 1200,
            "样本": "2/2",
            "跳过": [],
            "明细": [],
            "大类热度": [],
            "代码热度": {},
        }
        return stockfolio.valuation_view(refs, holdings, summary, cfg["现金规则"], "zh")

    def test_relocalize_view(self):
        """切语言后 view 的档位/操作/参考行读数必须随语言重生成（不混乱）。"""
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        summary = analysis.summarize(book, enriched, rates)
        view = self._view_zh(book, summary)
        self.assertEqual(view["参考行"][0][2], "中性")  # 42 → 中性
        self.assertIn("之间", view["建议"]["档位"])

        gui.relocalize_view(view, book, "en")
        self.assertEqual(view["参考行"][0][2], "Neutral")
        self.assertIn("Between Fairly Undervalued and Optimistically Valued", view["建议"]["档位"])
        self.assertTrue(str(view["操作"]).startswith("Add"))
        self.assertTrue(str(view["操作"]).endswith(" k"))

        # 再切回中文应还原
        gui.relocalize_view(view, book, "zh")
        self.assertEqual(view["参考行"][0][2], "中性")
        self.assertIn("之间", view["建议"]["档位"])
        self.assertTrue(str(view["操作"]).startswith(("加仓", "减仓")))

    def test_relocalize_summary(self):
        book = stockfolio.load_book(EXAMPLE)
        quotes, rates = market.offline_quotes_rates(book)
        enriched = analysis.enrich_holdings(book, quotes, rates)
        sm_zh = analysis.summarize(book, enriched, rates)
        sm_en = gui.relocalize_summary(book, enriched, rates, sm_zh, "en")
        self.assertIn(sm_en["大类"][0]["操作"][:3], ("Add", "Red"))
        self.assertTrue(sm_en["大类"][0]["操作"].endswith(" k"))
        self.assertIsNone(gui.relocalize_summary(book, enriched, rates, None, "en"))


class LadderPlanTest(unittest.TestCase):
    DEFAULT = DEFAULT_CASH_RULES

    def test_default_anchors(self):
        for temp, cash in ((0.0, 0.0), (33.3, 10.0), (66.7, 20.0), (100.0, 30.0)):
            plan = analysis.ladder_plan(temp, self.DEFAULT)
            self.assertAlmostEqual(plan["现金"], cash, places=1)
            self.assertAlmostEqual(plan["股票"], 100.0 - cash, places=1)

    def test_linear_interpolation(self):
        plan = analysis.ladder_plan(50.0, self.DEFAULT)
        self.assertAlmostEqual(plan["现金"], 15.0, places=1)
        self.assertEqual(plan["比例"], "8.5:1.5")

    def test_custom_rules(self):
        rules = [
            {"温度": 0, "名称": "低位", "现金": 0},
            {"温度": 100, "名称": "高位", "现金": 50},
        ]
        plan = analysis.ladder_plan(50.0, rules)
        self.assertAlmostEqual(plan["现金"], 25.0, places=1)
        self.assertEqual(plan["档位"], "低位 与 高位 之间（偏高位）")

    def test_out_of_range_clamps(self):
        plan = analysis.ladder_plan(-5.0, self.DEFAULT)
        self.assertEqual(plan["现金"], 0.0)
        plan = analysis.ladder_plan(150.0, self.DEFAULT)
        self.assertEqual(plan["现金"], 30.0)

    def test_none_returns_none(self):
        self.assertIsNone(analysis.ladder_plan(None, self.DEFAULT))


class CashRulesConfigTest(unittest.TestCase):
    def test_missing_or_invalid_raises(self):
        with self.assertRaises(ValueError):
            config.cash_rules({})
        with self.assertRaises(ValueError):
            config.cash_rules({"现金规则": [{"温度": 0, "名称": "a", "现金": 0}, {"温度": 50}]})  # 第二锚点缺字段

    def test_rule_missing_fields_message_lists_fields(self):
        """缺字段的锚点必须报 ValueError（含字段清单），而非 NameError。"""
        with self.assertRaises(ValueError) as ctx:
            config.cash_rules({"现金规则": [{"温度": 0, "名称": "a", "现金": 0}, {"温度": 50}]})
        self.assertIn("温度", str(ctx.exception))
        self.assertIn("名称", str(ctx.exception))
        self.assertIn("现金", str(ctx.exception))
        with self.assertRaises(ValueError):
            config.cash_rules({"现金规则": "bad"})
        with self.assertRaises(ValueError):
            config.cash_rules({"现金规则": [{"温度": 0, "名称": "a", "现金": 1}]})  # 只有一个锚点

    def test_custom_parsing(self):
        rules = config.cash_rules(
            {"现金规则": [{"温度": 100, "名称": "a", "现金": 99}, {"温度": 0, "名称": "b", "现金": 1}]}
        )
        self.assertEqual([r["温度"] for r in rules], [0.0, 100.0])

    def test_one_bad_anchor_raises_not_skipped(self):
        """4 个锚点中 1 个坏：必须报错，不能静默丢弃（规则唯一来源是 JSON）。"""
        rules = DEFAULT_CASH_RULES + [{"温度": 50, "名称": "", "现金": 15}]  # 名称为空
        with self.assertRaisesRegex(ValueError, "名称 缺失或为空"):
            config.cash_rules({"现金规则": rules})
        bad_cash = [dict(r) for r in DEFAULT_CASH_RULES]
        bad_cash[1]["现金"] = 150  # 越界
        with self.assertRaisesRegex(ValueError, "现金 应在 0~100"):
            config.cash_rules({"现金规则": bad_cash})
        bad_temp = [dict(r) for r in DEFAULT_CASH_RULES]
        bad_temp[0]["温度"] = -5
        with self.assertRaisesRegex(ValueError, "温度 应在 0~100"):
            config.cash_rules({"现金规则": bad_temp})
        not_dict = [{"温度": 0, "名称": "a", "现金": 0}, "bad"]
        with self.assertRaisesRegex(ValueError, "必须是"):
            config.cash_rules({"现金规则": not_dict})

    def test_valuation_config_merges(self):
        cfg = config.valuation_config(
            {"估值": {"回看交易日": 800, "现金规则": DEFAULT_CASH_RULES}}
        )
        self.assertEqual(cfg["回看交易日"], 800)
        self.assertEqual(cfg["均线窗口"], 250)
        self.assertEqual(len(cfg["现金规则"]), 4)


class IndicatorsTest(unittest.TestCase):
    def test_kline_indicators_heat_range(self):
        # 单调下跌的合成日线 → 分位低、回撤深 → 冷
        rows = []
        price = 100.0
        for i in range(300):
            rows.append([f"d{i}", price, price * 1.01, 1000 + i])
            price *= 0.999
        ind = analysis.kline_indicators(rows, 1200, 250)
        self.assertIsNotNone(ind)
        self.assertLessEqual(ind["热度_位置"], 20)
        self.assertLessEqual(ind["热度_回撤"], 40)
        self.assertTrue(0 <= ind["热度_均线"] <= 100)

    def test_insufficient_history(self):
        rows = [[f"d{i}", 1.0, 1.0, 1.0] for i in range(10)]
        self.assertIsNone(analysis.kline_indicators(rows, 1200, 250))

    def test_lookback_smaller_than_ma_window(self):
        """回看窗口小于均线窗口：数据不足应返回 None，不能拿不足的天数算均线。"""
        rows = [[f"d{i}", 100.0, 101.0, 1000.0] for i in range(300)]
        self.assertIsNone(analysis.kline_indicators(rows, 100, 250))


class MappingTest(unittest.TestCase):
    def test_code_to_sina(self):
        self.assertEqual(market.code_to_sina("600036.SS"), "sh600036")
        self.assertEqual(market.code_to_sina("000999.SZ"), "sz000999")
        self.assertEqual(market.code_to_sina("0700.HK"), "rt_hk00700")
        self.assertEqual(market.code_to_sina("BRK-B"), "gb_brkb")
        self.assertEqual(market.code_to_sina("VOO"), "gb_voo")
        self.assertEqual(market.code_to_sina("HKDCNY=X"), "fx_shkdcny")

    def test_kline_symbol(self):
        self.assertEqual(market.kline_symbol("600036.SS"), "sh600036")
        self.assertEqual(market.kline_symbol("0700.HK"), "hk00700")
        self.assertEqual(market.kline_symbol("VOO"), "usVOO")
        self.assertIsNone(market.kline_symbol("HKDCNY=X"))


class PriceResolveTest(unittest.TestCase):
    def test_zero_quote_falls_back_to_manual(self):
        holding = {"代码": "X", "现价": 10.0}
        self.assertEqual(analysis.resolve_price(holding, {"X": 0.0}), 10.0)
        self.assertEqual(analysis.resolve_price(holding, {"X": None}), 10.0)
        self.assertIsNone(analysis.resolve_price({"代码": "X"}, {"X": 0.0}))


class UsKlineTest(unittest.TestCase):
    """美股日线需要交易所后缀；腾讯只有带后缀才返回完整 qfq 历史。"""

    class _Resp:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._data

    def test_suffix_lookup_and_qfq_parse(self):
        urls = []

        def opener(req, timeout=8):
            urls.append(req.full_url)
            if "qt.gtimg.cn/q=" in req.full_url:
                return self._Resp(b'v_usNVDA="200~x~NVDA.OQ~204.64";')
            rows = [
                ["2026-10-29", "1", "2", "3", "4", "5"],
                ["2026-10-30", "2", "3", "4", "5", "6"],
            ]
            return self._Resp(
                json.dumps({"data": {"usNVDA.OQ": {"qfqday": rows}}}).encode()
            )

        rows = market.fetch_kline("usNVDA", 30, opener=opener)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1][1], 3.0)  # qfqday: [日期, 开, 收, 高, 低, 量]
        self.assertTrue(any("q=usNVDA" in u for u in urls))
        self.assertTrue(any("param=usNVDA.OQ" in u for u in urls))

    def test_dash_ticker_normalized(self):
        urls = []

        def opener(req, timeout=8):
            urls.append(req.full_url)
            if "qt.gtimg.cn/q=" in req.full_url:
                return self._Resp(b'v_usTEST.B="200~x~TEST.B.N~1";')
            return self._Resp(
                json.dumps(
                    {"data": {"usTEST.B.N": {"day": [["2026-10-30", "1", "2", "3", "4", "5"]]}}}
                ).encode()
            )

        rows = market.fetch_kline("usTEST-B", 30, opener=opener)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("q=usTEST.B" in u for u in urls))
        self.assertTrue(any("param=usTEST.B.N" in u for u in urls))


class ActionTest(unittest.TestCase):
    def test_action_text(self):
        self.assertEqual(analysis.action_text(10.0, 8.0), "加仓 2.0")
        self.assertEqual(analysis.action_text(8.0, 10.0), "减仓 2.0")
        self.assertEqual(analysis.action_text(10.0, 10.02), "持平")


class ValidatorTest(unittest.TestCase):
    def _base_book(self):
        return {
            "标题": "T",
            "语言": "zh",
            "本位币": "CNY",
            "现金": {"CNY": 10000},
            "大类": [{"名称": "A", "比例": 0.5}, {"名称": "B", "比例": 0.5}],
            "估值": {
                "现金规则": [
                    {"温度": 0, "名称": "低", "现金": 0},
                    {"温度": 100, "名称": "高", "现金": 50},
                ]
            },
            "持仓": [
                {"标的": "X", "代码": "600036.SS", "大类": "A", "货币": "CNY", "数量": 100, "成本价": 10.0}
            ],
        }

    def test_example_passes(self):
        self.assertEqual(stockfolio.validate_file(EXAMPLE), [])

    def test_category_sum_must_be_one(self):
        book = self._base_book()
        book["大类"][0]["比例"] = 0.6  # 总和 1.1
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("比例总和" in e for e in errors))

    def test_cash_rule_range(self):
        book = self._base_book()
        book["估值"]["现金规则"][1]["现金"] = 150  # >100
        book["估值"]["现金规则"][0]["温度"] = -5  # <0
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("大于 100" in e for e in errors))
        self.assertTrue(any("小于 0" in e for e in errors))

    def test_rules_required(self):
        book = self._base_book()
        del book["估值"]["现金规则"]
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("现金规则 缺失" in e for e in errors))

    def test_valuation_section_missing(self):
        """整个「估值」段缺失也必须报错（现金规则必填）。"""
        book = self._base_book()
        del book["估值"]
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("估值 缺失" in e for e in errors))

    def test_non_finite_rejected(self):
        book = self._base_book()
        book["目标总仓"] = float("nan")
        book["现金"]["CNY"] = float("inf")
        book["持仓"][0]["数量"] = float("nan")
        errors = stockfolio.validate_book(book)
        self.assertEqual(sum("不是有限数字" in e for e in errors), 3)

    def test_non_utf8_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "gbk.json"
            p.write_bytes('{"标题":"示例"}'.encode("gbk"))
            errors = stockfolio.validate_file(p)
        self.assertTrue(any("UTF-8" in e for e in errors))

    def test_rule_anchor_count(self):
        book = self._base_book()
        book["估值"]["现金规则"] = [{"温度": 0, "名称": "低", "现金": 0}]
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("至少 2 个锚点" in e for e in errors))

    def test_code_formats(self):
        book = self._base_book()
        for bad in ("600036", "070000.HK", "VOO.", "sh600036", "SZ000001"):
            book["持仓"][0]["代码"] = bad
            errors = stockfolio.validate_book(book)
            self.assertTrue(any("代码 格式非法" in e for e in errors), bad)
        for good in (
            "600036.SS", "000001.SZ", "0700.HK", "07000.HK",
            "VOO", "BRK-B", "BRK.B", "HKDCNY=X",
        ):
            book["持仓"][0]["代码"] = good
            self.assertEqual(stockfolio.validate_book(book), [], good)

    def test_currency_support(self):
        book = self._base_book()
        book["本位币"] = "BTC"
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("本位币 不支持" in e for e in errors))
        book = self._base_book()
        book["现金"]["JPY"] = 100  # JPY 在 24 币种内
        self.assertEqual(stockfolio.validate_book(book), [])
        book["持仓"][0]["货币"] = "EUR"  # EUR 支持
        self.assertEqual(stockfolio.validate_book(book), [])
        book["持仓"][0]["货币"] = "BTC"
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("货币 不支持" in e for e in errors))

    def test_category_undefined_and_duplicate(self):
        book = self._base_book()
        book["持仓"][0]["大类"] = "不存在"
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("大类 未定义" in e for e in errors))
        book = self._base_book()
        book["大类"][1]["名称"] = "A"  # 重名
        errors = stockfolio.validate_book(book)
        self.assertTrue(any("名称 重复" in e for e in errors))

    def test_numbers_non_negative(self):
        book = self._base_book()
        book["现金"]["CNY"] = -100
        book["持仓"][0]["数量"] = -1
        book["持仓"][0]["成本价"] = -5
        book["持仓"][0]["现价"] = -1
        errors = stockfolio.validate_book(book)
        self.assertEqual(len(errors), 4)
        self.assertTrue(all("小于 0" in e for e in errors))

    def test_invalid_json_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{ 不是合法json", encoding="utf-8")
            errors = stockfolio.validate_file(p)
            self.assertTrue(any("解析失败" in e for e in errors))


class CheckBookTest(unittest.TestCase):
    """check_book（--ping）：格式 + 行情/汇率/日线接口（mock，不联网）。"""

    # 腾讯报价：美股代码在 field[2] 带交易所后缀（SPY.AM）
    QQ_US = 'v_usSPY="200~x~SPY.AM~520.0";'

    QUOTES = (
        'var hq_str_sh510300="a,4,4,4.2,0";\n'
        'var hq_str_sh510880="a,2,2,3.0,0";\n'
        'var hq_str_rt_hk02800="a,1,2,3,4,5,23.0,0";\n'
        'var hq_str_gb_spy="a,520.0,0";\n'
        'var hq_str_fx_susdcny="a,6.65,6.66,0";\n'
        'var hq_str_fx_shkdcny="a,0.85,0.86,0";\n'
        'var hq_str_fx_sjpycny="a,0.0423,0.0424,0";\n'
    )

    class _Resp:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._data

    class _Open:
        def __init__(self, quotes=None, klines=True, count=251):
            self.quotes = CheckBookTest.QUOTES if quotes is None else quotes
            self.klines = klines
            self.count = count

        def _sina_rows(self):
            return [
                {"day": f"d{i}", "close": "4.2", "high": "4.3", "volume": "100"}
                for i in range(self.count)
            ]

        def _qq_rows(self):
            return [
                [f"d{i}", "4.0", "4.2", "4.3", "3.9", "100"]
                for i in range(self.count)
            ]

        def __call__(self, req, timeout=8):
            url = req.full_url
            if "qt.gtimg.cn/q=" in url:
                # 美股后缀查询；失败用例返回空文本让日线直接缺席
                text = CheckBookTest.QQ_US if self.quotes else ""
                return CheckBookTest._Resp(text.encode("gbk"))
            if "getKLineData" in url:
                rows = self._sina_rows() if self.klines else []
                return CheckBookTest._Resp(json.dumps(rows).encode())
            if "gtimg.cn" in url:
                symbol = url.split("param=")[1].split(",")[0]
                rows = self._qq_rows() if self.klines else []
                return CheckBookTest._Resp(
                    json.dumps({"data": {symbol: {"qfqday": rows}}}).encode()
                )
            return CheckBookTest._Resp(self.quotes.encode("gbk"))

    def test_example_ok(self):
        result = stockfolio.check_book(EXAMPLE, opener=self._Open())
        self.assertTrue(result["ok"])
        self.assertTrue(result["格式"]["ok"])
        self.assertEqual([i["名称"] for i in result["接口"]], ["行情", "汇率", "日线"])
        self.assertTrue(all(i["ok"] for i in result["接口"]))
        self.assertIn("4/4", result["接口"][0]["说明"])
        self.assertIn("251/250", result["接口"][2]["说明"])

    def test_insufficient_klines_fail(self):
        """日线不足均线窗口时必须判失败（--ping 不能只看到 >0 行就放行）。"""
        result = stockfolio.check_book(EXAMPLE, opener=self._Open(count=10))
        day = next(i for i in result["接口"] if i["名称"] == "日线")
        self.assertFalse(day["ok"])
        self.assertFalse(result["ok"])
        self.assertIn("10/250", day["说明"])

    def test_no_foreign_currency_needs_no_fx(self):
        """纯人民币组合不需要外币汇率：即使汇率响应为空也应通过。"""
        import tempfile

        book = {
            "标题": "纯人民币",
            "本位币": "CNY",
            "现金": {"CNY": 1000},
            "大类": [{"名称": "A", "比例": 1.0}],
            "估值": {
                "现金规则": [
                    {"温度": 0, "名称": "低", "现金": 0},
                    {"温度": 100, "名称": "高", "现金": 50},
                ]
            },
            "持仓": [
                {
                    "标的": "沪深300",
                    "代码": "510300.SS",
                    "大类": "A",
                    "货币": "CNY",
                    "数量": 1000,
                    "成本价": 4.0,
                }
            ],
        }
        quotes = (
            'var hq_str_sh510300="a,4,4,4.2,0";\n'
            'var hq_str_fx_susdcny="";\n'
            'var hq_str_fx_shkdcny="";\n'
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cny.json"
            p.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
            result = stockfolio.check_book(
                p, opener=self._Open(quotes=quotes)
            )
        rate = next(i for i in result["接口"] if i["名称"] == "汇率")
        self.assertTrue(rate["ok"])
        self.assertIn("无外币敞口", rate["说明"])
        self.assertTrue(result["ok"])

    def test_format_error_skips_interfaces(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{ bad", encoding="utf-8")
            result = stockfolio.check_book(p)
        self.assertFalse(result["ok"])
        self.assertFalse(result["格式"]["ok"])
        self.assertEqual(result["接口"], [])

    def test_interface_failure(self):
        result = stockfolio.check_book(
            EXAMPLE, opener=self._Open(quotes="", klines=False)
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            {i["名称"]: i["ok"] for i in result["接口"]},
            {"行情": False, "汇率": False, "日线": False},
        )


if __name__ == "__main__":
    unittest.main()
