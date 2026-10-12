"""Kiểm tra tránh tải trùng và tái sử dụng bài báo ngoại tuyến."""
import tempfile
import unittest
from unittest.mock import Mock, patch
from src.company_news import CompanyNewsAnalyzer


class NewsPerformanceTests(unittest.TestCase):
    def test_deduplicate_existing_and_cache_articles(self):
        with tempfile.TemporaryDirectory() as directory:
            analyzer = CompanyNewsAnalyzer("GVR", "2026-10-12", output_dir=directory)
            with patch.object(analyzer, "_collect_portal_links", return_value=[("https://example.com/old", "A"), ("https://example.com/new", "A")]), \
                 patch.object(analyzer, "_collect_official_links", return_value=[("https://example.com/new#top", "B")]), \
                 patch.object(analyzer, "_extract_article", return_value={"title": "GVR", "url": "https://example.com/new"}) as fetch:
                first = analyzer.fetch_web_news({"name": "GVR"}, [{"url": "https://example.com/old"}])
                second = analyzer.fetch_web_news({"name": "GVR"}, [{"url": "https://example.com/old"}])
                self.assertEqual(first, second)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(analyzer.web_stats["article_cache_hits"], 1)

    def test_request_timeout_and_no_retry(self):
        analyzer = CompanyNewsAnalyzer("GVR")
        with patch("requests.Session") as session:
            analyzer._request("https://example.com")
            self.assertEqual(session.return_value.get.call_args.kwargs["timeout"], (2, 5))
            adapter = session.return_value.mount.call_args.args[1]
            self.assertEqual(adapter.max_retries.total, 0)

    def test_disabled_does_not_fetch(self):
        analyzer = CompanyNewsAnalyzer("GVR", enable_web_news=False)
        with patch.object(analyzer, "_collect_portal_links") as fetch:
            self.assertEqual(analyzer.fetch_web_news({}), [])
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
