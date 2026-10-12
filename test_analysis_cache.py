"""Kiểm tra cache và phép đo pipeline ngoại tuyến, không gọi dịch vụ thật."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from analysis_cache import load_or_fetch


class AnalysisCacheTests(unittest.TestCase):
    def test_expiry_refresh_isolation_and_failed_fetch(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            fetch = Mock(return_value={"ticker": "FPT", "value": 1})
            def get(ticker="FPT", day="2026-10-12", **kwargs):
                return load_or_fetch(directory, "news", ticker, day, 900, fetch, **kwargs)
            with patch("analysis_cache.time.time", return_value=1000):
                self.assertFalse(get()[1])
                data, hit = get()
                self.assertTrue(hit)
                data["value"] = 9
                self.assertEqual(get()[0]["value"], 1)
                self.assertEqual(fetch.call_count, 1)
                self.assertFalse(get(ticker="HPG")[1])
                self.assertFalse(get(day="2026-10-11")[1])
                self.assertFalse(get(refresh=True)[1])
            with patch("analysis_cache.time.time", return_value=1900):
                self.assertFalse(get()[1])
            fetch.return_value = {"error": "unavailable"}
            self.assertFalse(get(ticker="ERR")[1])
            self.assertFalse(get(ticker="ERR")[1])

    def test_pipeline_timings_and_cache_reuse(self):
        modules = {name: Mock() for name in
                   ("technical", "fundamental", "company_news", "valuation", "report_pdf")}
        for name in ("technical", "fundamental", "company_news"):
            modules[name].run.return_value = {"ticker": "FPT", "as_of": "2026-10-12"}
        modules["valuation"].assess.return_value = {"rating": "test"}
        modules["report_pdf"].run.return_value = {"pdf_path": "mock.pdf"}
        spec = importlib.util.spec_from_file_location("pipeline_under_test", Path(__file__).with_name("pipeline.py"))
        pipeline = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", modules):
            spec.loader.exec_module(pipeline)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            with patch.object(pipeline, "BASE_DIR", root), \
                 patch.object(pipeline, "_enrich_fundamental", side_effect=lambda data, *a, **kw: data), \
                 patch.object(pipeline, "_enrich_news", side_effect=lambda data, *a: data), \
                 patch.object(pipeline, "risk_flags", return_value=[]):
                args = dict(ticker="FPT", as_of="2026-10-12", output_dir=str(root/"out"), reports_dir=str(root/"reports"))
                first = pipeline.run_pipeline(**args)
                second = pipeline.run_pipeline(**args)
                self.assertFalse(first["cache_hits"]["company_news"])
                self.assertTrue(second["cache_hits"]["company_news"])
                self.assertEqual(modules["fundamental"].run.call_count, 1)
                self.assertEqual(modules["company_news"].run.call_count, 1)
                self.assertEqual(modules["technical"].run.call_count, 2)
                self.assertEqual(modules["valuation"].assess.call_count, 2)
                self.assertEqual(modules["report_pdf"].run.call_count, 2)
                self.assertEqual(set(second["timings_seconds"]),
                                 {"initialization", "technical", "fundamental", "company_news", "valuation", "pdf", "total"})
                self.assertTrue(all(v >= 0 for v in second["timings_seconds"].values()))
                pipeline.run_pipeline(**args, refresh_data=True)
                self.assertEqual(modules["company_news"].run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
