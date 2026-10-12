"""Kiểm thử chuyển nguồn ngoại tuyến; không import thư viện xác thực thật."""
import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

import pandas as pd

fake_vnstock = ModuleType("vnstock")
fake_core = ModuleType("vnstock.core")
fake_core.setup_api_key = Mock()
fake_dotenv = ModuleType("dotenv")
fake_dotenv.load_dotenv = Mock()
spec = importlib.util.spec_from_file_location("fundamental_under_test", Path(__file__).with_name("fundamental.py"))
fund = importlib.util.module_from_spec(spec)
with patch.dict("sys.modules", {"vnstock": fake_vnstock, "vnstock.core": fake_core, "dotenv": fake_dotenv}):
    spec.loader.exec_module(fund)


def frame(**values):
    return pd.DataFrame([{"item_id": key, "2025": value} for key, value in values.items()])


class FundamentalFallbackTests(unittest.TestCase):
    def execute(self, stock, hf):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(fund, "OUTPUT_FILE", str(Path(directory)/"result.json")), \
             patch.object(fund, "_fetch_vnstock_data", side_effect=stock if isinstance(stock, Exception) else None, return_value=stock), \
             patch.object(fund, "_fetch_vnfinancialdata_table", side_effect=hf if isinstance(hf, Exception) else None, return_value=hf) as fetch, \
             patch.object(fund, "_detect_sector", return_value="Khác"):
            result = fund.run("XYZ", "2026-10-12")
            self.assertEqual(fetch.call_count, 1)
            return result

    def test_vnstock_failure_still_uses_hf(self):
        result = self.execute(RuntimeError("offline"), {"2025": {"revenue": 100, "net_profit": 10, "equity": 50, "eps": 2}})
        self.assertNotIn("error", result)
        self.assertEqual(result["financials_table"][0]["revenue"], 100)
        self.assertEqual(result["per_share"]["eps"], 2)
        self.assertIsNone(result["ttm"]["ttm_revenue"])
        self.assertIsNone(result["cash_flow"]["operating_cash_flow"])
        self.assertEqual(len(result["sources"]), 1)

    def test_hf_failure_still_uses_vnstock(self):
        result = self.execute({"income_year": frame(revenue=100, net_profit=10)}, RuntimeError("offline"))
        self.assertNotIn("error", result)
        self.assertEqual(result["financials_table"][0]["revenue"], 100)
        self.assertEqual(result["sources"][0]["name"], "vnstock (KBS)")

    def test_merge_each_field_preserves_zero_and_negative(self):
        result = self.execute({"income_year": frame(revenue=100, net_profit=10),
                               "balance_year": frame(total_assets=300, equity=80),
                               "ratio_year": frame(trailing_eps=4, book_value_per_share_bvps=8)},
                              {"2025": {"revenue": 0, "net_profit": -5, "eps": float("nan"), "total_assets": None}})
        row = result["financials_table"][0]
        self.assertEqual((row["revenue"], row["net_profit"], row["eps"], row["total_assets"]), (0, -5, 4, 300))
        self.assertEqual(result["field_sources"]["2025"]["ratio_year.eps"], "vnstock")
        self.assertEqual(result["field_sources"]["2025"]["income_year.revenue"], "vnfinancialdata")

    def test_empty_sources_and_future_only_are_errors(self):
        self.assertIn("error", self.execute({}, {}))
        self.assertIn("error", self.execute({}, {"2027": {"revenue": 10}}))

    def test_different_years_are_not_mixed(self):
        result = self.execute({"income_year": frame(revenue=100, net_profit=10)},
                              {"2024": {"revenue": 50, "eps": 2}, "2027": {"revenue": 999}})
        rows = {row["year"]: row for row in result["financials_table"]}
        self.assertEqual(set(rows), {"2025", "2024"})
        self.assertEqual(rows["2025"]["revenue"], 100)
        self.assertIsNone(rows["2025"]["eps"])
        self.assertEqual(rows["2024"]["eps"], 2)
        self.assertEqual(result["latest_period"], "2025")

    def test_balance_only_is_retained(self):
        result = self.execute({"income_year": pd.DataFrame()}, {"2025": {"total_assets": 100, "equity": 40}})
        self.assertNotIn("error", result)
        self.assertEqual(result["financials_table"][0]["total_assets"], 100)
        self.assertIsNone(result["financials_table"][0]["revenue"])

    def test_hf_loader_keeps_partial_statement_and_zero(self):
        provider = ModuleType("vnfinancialdata")
        def get(**kwargs):
            if kwargs["statement"] == "balance_sheet":
                raise RuntimeError("unavailable")
            return pd.DataFrame([{"year": 2025, "item_code": "is_doanh_so_thuan", "value": 0},
                                 {"year": 2025, "item_code": "is_loi_nhuan_sau_thue", "value": -5}])
        provider.get = Mock(side_effect=get)
        with patch.dict("sys.modules", {"vnfinancialdata": provider}):
            result = fund._fetch_vnfinancialdata_table("XYZ")
        self.assertEqual(result["2025"]["revenue"], 0)
        self.assertEqual(result["2025"]["net_profit"], -5)
        self.assertIsNone(result["2025"]["total_assets"])


if __name__ == "__main__":
    unittest.main()
