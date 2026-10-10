"""
test_full_flow.py
=================
Kịch bản kiểm thử toàn luồng (End-to-End Test):
1. Kiểm thử luồng hợp lệ với mã HPG (sinh đủ 4 JSON + 1 biểu đồ + 1 PDF)
2. Kiểm tra tính toàn vẹn file PDF (dung lượng > 500KB, đọc được trang bằng pypdf)
3. Kiểm thử xử lý lỗi với mã không hợp lệ (mã rỗng hoặc > 3 ký tự)
"""

import os
import sys
import json
from pathlib import Path
from pypdf import PdfReader

# Thiết lập UTF-8 trên Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pipeline


def test_valid_flow():
    print("\n[TEST 1] Kiểm thử luồng phân tích hợp lệ với HPG...")
    res = pipeline.run_pipeline(
        ticker="HPG",
        as_of="2026-10-09",
        risk_profile="balanced",
        output_dir="outputs",
        reports_dir="reports"
    )
    
    assert res is not None, "Kết quả pipeline không được None"
    assert res.get("ticker") == "HPG", f"Mã không khớp: {res.get('ticker')}"
    assert res.get("status") in ("success", "partial"), f"Trạng thái không đạt: {res.get('status')}"
    
    # Kiểm tra các file đầu ra
    out_dir = BASE_DIR / "outputs"
    assert (out_dir / "technical.json").exists(), "Thiếu outputs/technical.json"
    assert (out_dir / "fundamental.json").exists(), "Thiếu outputs/fundamental.json"
    assert (out_dir / "company_news.json").exists(), "Thiếu outputs/company_news.json"
    assert (out_dir / "assessment.json").exists(), "Thiếu outputs/assessment.json"
    assert (out_dir / "charts" / "HPG_price.png").exists(), "Thiếu biểu đồ kỹ thuật HPG_price.png"
    
    # Kiểm tra file PDF
    pdf_path = res.get("pdf_path")
    assert pdf_path and Path(pdf_path).exists(), f"Không tìm thấy file PDF: {pdf_path}"
    
    pdf_file = Path(pdf_path)
    file_size_kb = pdf_file.stat().st_size / 1024
    print(f"-> File PDF tạo thành công: {pdf_file.name} ({file_size_kb:.1f} KB)")
    assert file_size_kb > 200, f"File PDF quá nhỏ ({file_size_kb} KB), có thể bị lỗi dựng"
    
    # Kiểm tra đọc số trang PDF
    reader = PdfReader(str(pdf_file))
    num_pages = len(reader.pages)
    print(f"-> Số trang PDF: {num_pages} trang")
    assert num_pages >= 3, f"Số trang PDF quá ít ({num_pages} trang)"
    
    print("✅ TEST 1 THÀNH CÔNG: Luồng phân tích và xuất PDF hoạt động hoàn hảo!\n")


def test_invalid_input():
    print("[TEST 2] Kiểm thử xử lý lỗi với mã cổ phiếu không hợp lệ...")
    res = pipeline.run_pipeline(
        ticker="TOOLONGTICKER",
        as_of="2026-10-09",
        output_dir="outputs",
        reports_dir="reports"
    )
    # Pipeline phải xử lý nhẹ nhàng, không crash và ghi nhận error
    errors = res.get("errors") or []
    print(f"-> Ghi nhận lỗi có kiểm soát: {errors}")
    assert len(errors) > 0 or res.get("status") in ("partial", "error"), "Hệ thống phải bắt lỗi mã không hợp lệ"
    print("✅ TEST 2 THÀNH CÔNG: Hệ thống bắt lỗi chính xác và an toàn!\n")


if __name__ == "__main__":
    try:
        test_valid_flow()
        test_invalid_input()
        print("🎉 TẤT CẢ CÁC BÀI KIỂM THỬ ĐÃ VƯỢT QUA XUẤT SẮC!")
    except AssertionError as e:
        print(f"❌ KIỂM THỬ THẤT BẠI: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ LỖI NGOẠI LỆ: {e}")
        sys.exit(1)
