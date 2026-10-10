"""Chuẩn hóa nguồn tham chiếu và rủi ro, dùng chung cho app và PDF."""
from copy import deepcopy
import math


def date_label(value, missing_label):
    text = str(value).strip() if value is not None else ''
    if text.lower() in ('', 'chưa có dữ liệu', 'null', 'none', 'n/a', 'unknown', 'nat', 'nan', '—', '-'):
        return missing_label
    return text


def financial_unit(fund):
    """Đọc metadata; hỗ trợ JSON cũ của bộ thu thập đã chuẩn hóa VND."""
    explicit = fund.get('unit')
    if explicit:
        return explicit
    ttm = fund.get('ttm') or {}
    if ttm.get('ttm_revenue_unit') == 'VND' and ttm.get('ttm_net_income_unit') == 'VND':
        return 'VND'
    return None


def billion_value(value, unit):
    factors = {'vnd': 1e-9, 'đồng': 1e-9, 'nghìn vnd': 1e-6,
               'triệu vnd': 1e-3, 'tỷ vnd': 1, 'ty vnd': 1}
    factor = factors.get(str(unit or '').strip().lower())
    if factor is None or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return value * factor


def financial_basis(fund):
    explicit = fund.get('basis') or fund.get('reporting_basis') or fund.get('period_basis')
    if explicit:
        return explicit
    rows = fund.get('financials_table') or []
    if not rows:
        return 'Chưa xác định kỳ báo cáo'
    if any('TTM' in str(row.get('year', '')).upper() for row in rows):
        period = fund.get('latest_period') or 'gần nhất có dữ liệu'
        return (f'Báo cáo theo năm; cột TTM dùng doanh thu và lợi nhuận của 12 tháng gần nhất. '
                f'Tài sản, vốn chủ và các chỉ số còn lại trong cột TTM lấy từ kỳ {period}, '
                'trừ biên lợi nhuận ròng tính từ doanh thu và lợi nhuận TTM.')
    return 'Báo cáo theo từng năm ghi trên cột.'


def internal_reference(ref, sector):
    result = deepcopy(ref)
    result.update(source=f"Bảng giả định nội bộ của ứng dụng - nhóm {sector}",
                  reference_type="internal_assumption",
                  note="Các mức thấp/cơ sở/cao là giả định của mô hình, chưa phải phân vị thống kê từ doanh nghiệp cùng ngành.")
    return result


def reference_source(ref):
    return ref.get("source") or "Đầu vào chưa ghi nguồn; chưa xác minh được xuất xứ các hệ số."


def risk_flags(fund, tech, news, as_of):
    """Giữ cảnh báo nguồn; bổ sung nhận xét có căn cứ và yếu tố ngành cần theo dõi."""
    flags = [dict(f) for f in news.get("risk_flags") or [] if isinstance(f, dict) and f.get("text")]
    seen = {f["text"] for f in flags}

    def add(text, source, category="observed", date=None):
        if text not in seen:
            flags.append(dict(text=text, source=source, severity="medium", category=category, date=date or as_of))
            seen.add(text)

    for item in news.get("risks") or []:
        if isinstance(item, dict):
            text = item.get("text") or item.get("summary")
            if text:
                add(text, item.get("source") or "Tin tức đầu vào", date=item.get("date"))
        elif isinstance(item, str):
            add(item, "Tin tức đầu vào")
    for key, label in (("revenue_yoy", "Doanh thu"), ("net_income_yoy", "Lợi nhuận sau thuế")):
        value = (fund.get("growth") or {}).get(key)
        if isinstance(value, (int, float)) and math.isfinite(value) and value < 0:
            add(f"{label} giảm {abs(value):.1%} so với cùng kỳ; cần theo dõi khả năng phục hồi kinh doanh.", "Số liệu tăng trưởng trong báo cáo tài chính")
    if tech.get("trend") == "down":
        add("Giá đang trong xu hướng giảm; có thể tiếp tục biến động bất lợi trong ngắn hạn.", "Chỉ báo kỹ thuật", date=(tech.get("price") or {}).get("date"))
    sector = str(fund.get("sector") or "Khác")
    sector_notes = {
        "chứng khoán": "Thanh khoản thị trường giảm có thể làm giảm phí môi giới; danh mục tự doanh và cho vay ký quỹ có thể chịu thiệt hại khi giá cổ phiếu giảm.",
        "ngân hàng": "Cần theo dõi nợ xấu, chi phí dự phòng và chênh lệch giữa lãi cho vay với lãi huy động.",
        "bảo hiểm": "Chi phí bồi thường và biến động lợi suất đầu tư có thể ảnh hưởng lợi nhuận.",
        "bất động sản": "Cần theo dõi pháp lý dự án, tiến độ bàn giao, khả năng bán hàng và lịch trả nợ.",
        "công nghệ": "Cần theo dõi nhu cầu khách hàng, chi phí nhân sự và biến động tỷ giá.",
        "bán lẻ": "Sức mua yếu, hàng tồn kho và cạnh tranh giá có thể làm giảm lợi nhuận.",
        "thép": "Giá nguyên liệu, nhu cầu xây dựng và cạnh tranh nhập khẩu có thể làm biến động biên lợi nhuận.",
        "dầu khí": "Biến động giá dầu, chi phí đầu tư và tiến độ dự án có thể ảnh hưởng kết quả kinh doanh.",
        "thực phẩm": "Cần theo dõi giá nguyên liệu, sức mua và yêu cầu an toàn thực phẩm.",
        "hóa chất": "Biến động giá đầu vào, chu kỳ tiêu thụ và yêu cầu môi trường có thể ảnh hưởng lợi nhuận.",
    }
    note = next((v for k, v in sector_notes.items() if k in sector.lower()),
                "Cần theo dõi sức cầu, chi phí đầu vào, cạnh tranh và thay đổi chính sách liên quan đến hoạt động doanh nghiệp.")
    add("Yếu tố cần theo dõi (không phải sự kiện đã xác nhận): " + note,
        f"Khung rà soát nội bộ theo ngành: {sector}", "sector_watch")
    if news.get("error") or not news.get("news"):
        add("Chưa đủ tin tức để rà soát sự kiện bất lợi riêng của doanh nghiệp; không có cảnh báo không đồng nghĩa không có rủi ro.", "Phạm vi dữ liệu đầu vào", "data_gap")
    if (fund.get("valuation_ref") or {}).get("reference_type") == "internal_assumption":
        add("Định giá sử dụng hệ số giả định nội bộ; kết quả có thể thay đổi đáng kể khi thay hệ số bằng thống kê thị trường đã xác minh.", "Giả định mô hình định giá", "model")
    return flags
