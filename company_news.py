"""
company_news.py
===============
Proxy module chuyển tiếp lời gọi đến src.company_news.
Hỗ trợ tích hợp đồng bộ giữa valuation.py, report_pdf.py và app.py.
"""

from __future__ import annotations
import sys
from pathlib import Path

# Thêm thư mục gốc vào sys.path để import src
_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

try:
    from src.company_news import run, CompanyNewsAnalyzer, main
except ImportError:
    from .src.company_news import run, CompanyNewsAnalyzer, main

__all__ = ["run", "CompanyNewsAnalyzer", "main"]

if __name__ == "__main__":
    main()
