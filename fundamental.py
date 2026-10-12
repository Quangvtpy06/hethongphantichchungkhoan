"""
fundamental.py
==============
Module thu thập và chuẩn hóa báo cáo tài chính + chỉ số tài chính cổ phiếu Việt Nam.
Dùng cho hệ thống phân tích cơ hội đầu tư cổ phiếu. Nguồn: vnstock v4 (KBS) + vnfinancialdata (HF).

Hướng dẫn thiết lập (cho Quang - phụ trách tích hợp hệ thống):
    python -m venv venv
    venv\\Scripts\\activate          (Windows)  |  source venv/bin/activate  (Mac/Linux)
    pip install -r requirements.txt --extra-index-url https://vnstocks.com/api/simple

Chạy thử trực tiếp:
    python fundamental.py VNM 2026-10-09
    python fundamental.py FPT 2026-10-09

Biến môi trường (.env):
    HF_TOKEN=your_token_here         (Token Hugging Face để dùng vnfinancialdata)
"""

import os
import sys
import json
import logging
from report_support import internal_reference, financial_basis
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple, Union
import vnstock as vnstock 
import pandas as pd
from dotenv import load_dotenv

# Thiết lập UTF-8 encoding an toàn trên môi trường Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

# Đường dẫn file và nạp .env
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(_SCRIPT_DIR, "fundamental.json")

# Nạp .env từ thư mục script và thư mục hiện hành
load_dotenv(os.path.join(_SCRIPT_DIR, ".env"))
load_dotenv()
HF_TOKEN: Optional[str] = os.getenv("HF_TOKEN")

# Thiết lập API key vnstock từ .env (biến VNSTOCK_API_KEY hoặc vnstock_api)
_vnstock_key = os.getenv("VNSTOCK_API_KEY") or os.getenv("vnstock_api")
if _vnstock_key:
    _clean_key = _vnstock_key.strip().strip('"').strip("'")
    os.environ["VNSTOCK_API_KEY"] = _clean_key
    try:
        from vnstock.core import setup_api_key
        setup_api_key(_clean_key)
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)


# ══════════════════════════════ CÁC HÀM TIỆN ÍCH ══════════════════════════════

def _safe_float(val: Any) -> Optional[float]:
    """Chuyển đổi an toàn sang float; trả về None nếu NaN hoặc lỗi."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _format_ratio(val: Any) -> Optional[float]:
    """
    Chuẩn hóa tỷ lệ sang dạng số thập phân (0.15 nghĩa là 15%).
    Nếu giá trị tuyệt đối > 1, giả định đang là đơn vị % và chia cho 100.
    """
    v = _safe_float(val)
    if v is None:
        return None
    return round(v / 100.0, 6) if abs(v) > 1.0 else round(v, 6)


def _calc_yoy(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Tính tỷ lệ tăng trưởng = (kỳ này - kỳ trước) / |kỳ trước|."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous), 6)



REVENUE_KEYS = [
    "revenue", "net_sales", "revenue_from_securities_business_01_11",
    "operating_revenue", "total_operating_revenue", "net_interest_income",
    "interest_income_and_similar_income", "total_operating_income",
    "doanh_thu_thuan", "doanh_thu"
]

NET_PROFIT_KEYS = [
    "net_profit", "profit_after_tax_for_shareholders_of_the_parents_company",
    "net_profit_atttributable_to_the_equity_holders_of_the_bank",
    "profit_after_tax", "net_profit_from_securities_business_20_50_40_60_61_62",
    "net_income_appropriated_to_ordinary_shareholders", "loi_nhuan_sau_thue"
]

GROSS_PROFIT_KEYS = [
    "gross_profit", "lai_gop", "loi_nhuan_gop", "net_interest_income",
    "net_profit_from_securities_business_20_50_40_60_61_62"
]

OPERATING_PROFIT_KEYS = [
    "operating_profit", "ebit", "operating_profit_before_provision_for_credit_losses",
    "net_profit_from_operating_activities", "profit_before_tax", "operating_income"
]

TOTAL_ASSETS_KEYS = [
    "total_assets", "tong_tai_san", "tong_cong_tai_san"
]

EQUITY_KEYS = [
    "equity", "owners_equity", "capital_and_reserves", "owners_capital",
    "owners_equity_2", "owners_equity_3", "total_equity", "von_chu_so_huu", "von_va_cac_quy"
]

EPS_KEYS = [
    "earnings_per_share_vnd", "earning_per_share_vnd", "diluted_earning_per_share",
    "trailing_eps"
]


_INDUSTRY_DF_CACHE = None

def _detect_sector(ticker: str) -> str:
    """Tự động phát hiện chính xác ngành ICB của cổ phiếu qua vnstock Reference API hoặc company_news."""
    global _INDUSTRY_DF_CACHE
    tkr = str(ticker or "").strip().upper()
    if not tkr:
        return "Khác"
    
    # 1. Tra cứu trực tiếp từ vnstock Reference API
    try:
        from vnstock import Reference
        if _INDUSTRY_DF_CACHE is None:
            _INDUSTRY_DF_CACHE = Reference().equity().list_by_industry()
        df_ind = _INDUSTRY_DF_CACHE
        sub = df_ind[df_ind["symbol"] == tkr]
        if not sub.empty:
            l4 = sub[sub["icb_level"] == 4]["icb_name"].iloc[0] if not sub[sub["icb_level"] == 4].empty else ""
            l2 = sub[sub["icb_level"] == 2]["icb_name"].iloc[0] if not sub[sub["icb_level"] == 2].empty else ""
            l1 = sub[sub["icb_level"] == 1]["icb_name"].iloc[0] if not sub[sub["icb_level"] == 1].empty else ""

            combined = f"{l4} {l2} {l1}".lower()
            if "chứng khoán" in combined:
                return "Chứng khoán"
            if "ngân hàng" in combined:
                return "Ngân hàng"
            if "bất động sản" in combined:
                return "Bất động sản"
            if "bảo hiểm" in combined:
                return "Bảo hiểm"
            if "dầu khí" in combined:
                return "Dầu khí"
            if "thép" in combined or "kim loại" in combined:
                return "Thép"
            if "công nghệ" in combined or "phần mềm" in combined or "máy tính" in combined:
                return "Công nghệ"
            if "bán lẻ" in combined:
                return "Bán lẻ"
            if "thực phẩm" in combined or "đồ uống" in combined:
                return "Thực phẩm & Đồ uống"
            if "hóa chất" in combined:
                return "Hóa chất"
            return l2 or l1 or "Khác"
    except Exception:
        pass

    # 2. Fallback sang file company_news.json nếu có
    for p in ("outputs/company_news.json", "company_news.json"):
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    cn = json.load(f)
                if cn.get("ticker") == tkr:
                    ind = (cn.get("company") or {}).get("industry") or {}
                    l4 = ind.get("level_4") or ""
                    l2 = ind.get("level_2") or ""
                    l1 = ind.get("level_1") or ""
                    combined = f"{l4} {l2} {l1}".lower()
                    if "chứng khoán" in combined: return "Chứng khoán"
                    if "ngân hàng" in combined: return "Ngân hàng"
                    if "bất động sản" in combined: return "Bất động sản"
                    if "bảo hiểm" in combined: return "Bảo hiểm"
                    if "dầu khí" in combined: return "Dầu khí"
                    if "thép" in combined or "kim loại" in combined: return "Thép"
                    if "công nghệ" in combined: return "Công nghệ"
                    if "bán lẻ" in combined: return "Bán lẻ"
                    if "thực phẩm" in combined or "đồ uống" in combined: return "Thực phẩm & Đồ uống"
                    if "hóa chất" in combined: return "Hóa chất"
                    return l2 or l1 or "Khác"
        except Exception:
            pass

    return "Khác"


_SECTOR_MULTIPLES = {
    "Ngân hàng": {"pe": {"p25": 6.0, "median": 7.5, "p75": 9.5}, "pb": {"p25": 1.0, "median": 1.3, "p75": 1.6}},
    "Chứng khoán": {"pe": {"p25": 10.0, "median": 13.5, "p75": 17.5}, "pb": {"p25": 1.2, "median": 1.6, "p75": 2.2}},
    "Bất động sản": {"pe": {"p25": 8.0, "median": 12.0, "p75": 16.0}, "pb": {"p25": 1.1, "median": 1.5, "p75": 2.0}},
    "Dầu khí": {"pe": {"p25": 8.5, "median": 11.0, "p75": 14.0}, "pb": {"p25": 1.1, "median": 1.5, "p75": 1.9}},
    "Thép": {"pe": {"p25": 8.0, "median": 10.0, "p75": 13.0}, "pb": {"p25": 1.0, "median": 1.3, "p75": 1.7}},
    "Công nghệ": {"pe": {"p25": 16.0, "median": 20.0, "p75": 25.0}, "pb": {"p25": 3.5, "median": 4.5, "p75": 5.5}},
    "Bán lẻ": {"pe": {"p25": 13.0, "median": 16.5, "p75": 21.0}, "pb": {"p25": 2.2, "median": 3.0, "p75": 4.0}},
    "Thực phẩm & Đồ uống": {"pe": {"p25": 13.0, "median": 16.0, "p75": 20.0}, "pb": {"p25": 2.5, "median": 3.5, "p75": 4.5}},
    "Hóa chất": {"pe": {"p25": 7.5, "median": 9.5, "p75": 12.5}, "pb": {"p25": 1.2, "median": 1.6, "p75": 2.1}},
    "Bảo hiểm": {"pe": {"p25": 9.0, "median": 12.0, "p75": 15.0}, "pb": {"p25": 1.2, "median": 1.5, "p75": 1.8}},
}

_TICKER_MULTIPLES = {
    "ACB": {"pe": {"p25": 5.5, "median": 6.8, "p75": 8.2}, "pb": {"p25": 1.1, "median": 1.3, "p75": 1.5}},
    "VCB": {"pe": {"p25": 12.0, "median": 14.5, "p75": 17.0}, "pb": {"p25": 2.5, "median": 3.0, "p75": 3.6}},
    "MBB": {"pe": {"p25": 5.0, "median": 6.5, "p75": 8.0}, "pb": {"p25": 0.9, "median": 1.1, "p75": 1.4}},
    "TCB": {"pe": {"p25": 6.0, "median": 7.5, "p75": 9.5}, "pb": {"p25": 0.9, "median": 1.2, "p75": 1.5}},
    "CTG": {"pe": {"p25": 6.5, "median": 8.0, "p75": 10.0}, "pb": {"p25": 1.0, "median": 1.3, "p75": 1.6}},
    "FPT": {"pe": {"p25": 18.0, "median": 22.5, "p75": 26.0}, "pb": {"p25": 4.0, "median": 5.2, "p75": 6.5}},
    "HPG": {"pe": {"p25": 8.5, "median": 10.2, "p75": 13.0}, "pb": {"p25": 1.1, "median": 1.4, "p75": 1.8}},
    "VNM": {"pe": {"p25": 13.5, "median": 16.0, "p75": 19.0}, "pb": {"p25": 3.0, "median": 3.8, "p75": 4.5}},
    "MWG": {"pe": {"p25": 14.0, "median": 18.0, "p75": 24.0}, "pb": {"p25": 2.4, "median": 3.2, "p75": 4.2}},
    "SSI": {"pe": {"p25": 11.0, "median": 14.5, "p75": 18.5}, "pb": {"p25": 1.4, "median": 1.8, "p75": 2.4}},
    "VND": {"pe": {"p25": 9.5, "median": 12.5, "p75": 16.0}, "pb": {"p25": 1.1, "median": 1.4, "p75": 1.9}},
}


def _extract_metric(df: Optional[pd.DataFrame], item_id: Union[str, List[str]], col_name: str) -> Optional[float]:
    """
    Lấy giá trị từ DataFrame báo cáo tài chính của vnstock (dạng ngang: mỗi hàng là một item_id,
    cột là các năm hoặc quý).
    Hỗ trợ danh sách item_ids ưu tiên và tìm hàng có giá trị hợp lệ (khác NaN) đầu tiên.
    """
    if df is None or df.empty or col_name not in df.columns:
        return None
    ids = [item_id] if isinstance(item_id, str) else item_id
    try:
        for target_id in ids:
            rows = df[df["item_id"] == target_id]
            if rows.empty:
                continue
            for _, r in rows.iterrows():
                val = _safe_float(r.get(col_name))
                if val is not None:
                    return val
        return None
    except Exception:
        return None


def _get_sorted_periods(df: Optional[pd.DataFrame], is_quarter: bool = False, as_of_date: Optional[date] = None) -> List[str]:
    """
    Lọc và sắp xếp các cột kỳ báo cáo (năm hoặc quý) giảm dần từ mới nhất đến cũ nhất,
    và chỉ lấy các kỳ <= as_of_date.
    """
    if df is None or df.empty:
        return []
    
    meta_cols = {"item", "item_en", "item_id"}
    periods = [c for c in df.columns if c not in meta_cols]
    
    valid_periods = []
    for p in periods:
        p_str = str(p).strip()
        if is_quarter:
            # Định dạng quý: YYYY-QX
            if "-Q" in p_str:
                parts = p_str.split("-Q")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    y, q = int(parts[0]), int(parts[1])
                    if as_of_date:
                        target_q = (as_of_date.month - 1) // 3 + 1
                        if y > as_of_date.year or (y == as_of_date.year and q > target_q):
                            continue
                    valid_periods.append((p_str, (y, q)))
        else:
            # Định dạng năm: YYYY
            if p_str.isdigit():
                y = int(p_str)
                if as_of_date and y > as_of_date.year:
                    continue
                valid_periods.append((p_str, y))
                
    valid_periods.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in valid_periods]


# ══════════════════════════════ THU THẬP DỮ LIỆU ══════════════════════════════

def _fetch_vnstock_data(ticker: str) -> Dict[str, Any]:
    """Tải báo cáo tài chính năm, quý, chỉ số tài chính và sự kiện từ vnstock v4 (KBS)."""
    from vnstock import Fundamental

    fin = Fundamental().equity(ticker)

    data: Dict[str, Any] = {}
    loaders = {
        "income_year": lambda: fin.income_statement(period="year"),
        "balance_year": lambda: fin.balance_sheet(period="year"),
        "cashflow_year": lambda: fin.cash_flow(period="year"),
        "income_quarter": lambda: fin.income_statement(period="quarter"),
        "ratio_year": lambda: fin.ratio(period="year"),
    }

    for key, fn in loaders.items():
        try:
            df = fn()
            data[key] = df if (df is not None and not df.empty) else None
        except Exception as e:
            log.warning(f"Lỗi tải {key} từ vnstock: {e}")
            data[key] = None

    # Thử tải sự kiện doanh nghiệp (cổ tức, v.v.) từ nhiều nguồn (VCI, KBS, TCBS)
    events_df = None
    try:
        from vnstock import Company
        for ev_src in ["VCI", "KBS", "TCBS"]:
            try:
                edf = Company(symbol=ticker, source=ev_src).events()
                if edf is not None and not edf.empty:
                    events_df = edf
                    break
            except Exception:
                pass
        data["events"] = events_df
    except Exception as e:
        log.warning(f"Bỏ qua events: {e}")
        data["events"] = None

    return data


def _fetch_vnfinancialdata(ticker: str) -> Optional[pd.DataFrame]:
    """Tải dữ liệu bổ trợ từ vnfinancialdata (Hugging Face) nếu có HF_TOKEN."""
    if not HF_TOKEN:
        return None
    try:
        import vnfinancialdata as vnf
        os.environ["HUGGINGFACE_TOKEN"] = HF_TOKEN
        os.environ["HF_TOKEN"] = HF_TOKEN
        # vnfinancialdata chỉ hỗ trợ HSX và HNX (không nhận HOSE/UPCOM).
        for ex in ["HSX", "HNX"]:
            try:
                df = vnf.load(exchange=ex, statement="income_statement", ticker=ticker)
                if df is not None and not df.empty:
                    return df
            except Exception as exc:
                log.warning("Không tải được dữ liệu bổ sung sàn %s (%s)", ex, type(exc).__name__)
                continue
        return None
    except Exception as e:
        log.warning(f"Bỏ qua vnfinancialdata: {e}")
        return None


# ══════════════════════════════ TÍNH TOÁN CHỈ SỐ ══════════════════════════════

def _compute_growth(income_year: Optional[pd.DataFrame], income_quarter: Optional[pd.DataFrame],
                    ratio_year: Optional[pd.DataFrame],
                    year_cols: List[str], quarter_cols: List[str]) -> Dict[str, Optional[float]]:
    """Tính các chỉ số tăng trưởng doanh thu, lợi nhuận và CAGR EPS 3 năm."""
    rev_y0, rev_y1 = None, None
    ni_y0, ni_y1 = None, None
    if len(year_cols) >= 1:
        rev_y0 = _extract_metric(income_year, REVENUE_KEYS, year_cols[0])
        ni_y0 = _extract_metric(income_year, NET_PROFIT_KEYS, year_cols[0])
    if len(year_cols) >= 2:
        rev_y1 = _extract_metric(income_year, REVENUE_KEYS, year_cols[1])
        ni_y1 = _extract_metric(income_year, NET_PROFIT_KEYS, year_cols[1])

    rev_q0, rev_q1 = None, None
    ni_q0, ni_q1 = None, None
    if len(quarter_cols) >= 2:
        rev_q0 = _extract_metric(income_quarter, REVENUE_KEYS, quarter_cols[0])
        rev_q1 = _extract_metric(income_quarter, REVENUE_KEYS, quarter_cols[1])
        ni_q0 = _extract_metric(income_quarter, NET_PROFIT_KEYS, quarter_cols[0])
        ni_q1 = _extract_metric(income_quarter, NET_PROFIT_KEYS, quarter_cols[1])

    # Tính CAGR EPS 3 năm
    eps_cagr = None
    if len(year_cols) >= 4:
        eps_latest = _extract_metric(ratio_year, "trailing_eps", year_cols[0]) or _extract_metric(income_year, EPS_KEYS, year_cols[0])
        eps_old = _extract_metric(ratio_year, "trailing_eps", year_cols[3]) or _extract_metric(income_year, EPS_KEYS, year_cols[3])
        if eps_latest and eps_old and eps_latest > 0 and eps_old > 0:
            eps_cagr = round((eps_latest / eps_old) ** (1 / 3) - 1, 4)
    elif len(year_cols) >= 3:
        eps_latest = _extract_metric(ratio_year, "trailing_eps", year_cols[0]) or _extract_metric(income_year, EPS_KEYS, year_cols[0])
        eps_old = _extract_metric(ratio_year, "trailing_eps", year_cols[2]) or _extract_metric(income_year, EPS_KEYS, year_cols[2])
        if eps_latest and eps_old and eps_latest > 0 and eps_old > 0:
            eps_cagr = round((eps_latest / eps_old) ** (1 / 2) - 1, 4)

    # Fallback tính CAGR từ net_profit nếu thiếu EPS
    if eps_cagr is None and len(year_cols) >= 3:
        ni_latest = _extract_metric(income_year, NET_PROFIT_KEYS, year_cols[0])
        n_prev = min(3, len(year_cols) - 1)
        ni_old = _extract_metric(income_year, NET_PROFIT_KEYS, year_cols[n_prev])
        if ni_latest and ni_old and ni_latest > 0 and ni_old > 0:
            eps_cagr = round((ni_latest / ni_old) ** (1 / n_prev) - 1, 4)

    rev_yoy = _calc_yoy(rev_y0, rev_y1)
    ni_yoy = _calc_yoy(ni_y0, ni_y1)
    rev_qoq = _calc_yoy(rev_q0, rev_q1)
    ni_qoq = _calc_yoy(ni_q0, ni_q1)

    return {
        "revenue_growth_yoy": rev_yoy,
        "revenue_yoy": rev_yoy,
        "net_income_growth_yoy": ni_yoy,
        "net_income_yoy": ni_yoy,
        "revenue_growth_qoq": rev_qoq,
        "revenue_qoq": rev_qoq,
        "revenue_qoq_or_recent": rev_qoq,
        "net_income_qoq_or_recent": ni_qoq,
        "eps_cagr_3y": eps_cagr,
    }


def _compute_margins(income_year: Optional[pd.DataFrame], year_cols: List[str]) -> Dict[str, Optional[float]]:
    """Tính biên lợi nhuận gộp, hoạt động và ròng."""
    if not year_cols:
        return {"gross_margin": None, "operating_margin": None, "net_margin": None}

    latest_year = year_cols[0]
    rev = _extract_metric(income_year, REVENUE_KEYS, latest_year)
    gp = _extract_metric(income_year, GROSS_PROFIT_KEYS, latest_year)
    op = _extract_metric(income_year, OPERATING_PROFIT_KEYS, latest_year)
    ni = _extract_metric(income_year, NET_PROFIT_KEYS, latest_year)

    gross_m = _format_ratio(gp / rev) if (gp is not None and rev and rev > 0) else None
    op_m = _format_ratio(op / rev) if (op is not None and rev and rev > 0) else None
    net_m = _format_ratio(ni / rev) if (ni is not None and rev and rev > 0) else None

    return {
        "gross_margin": gross_m,
        "operating_margin": op_m,
        "net_margin": net_m,
    }


def _compute_profitability(income_year: Optional[pd.DataFrame], balance_year: Optional[pd.DataFrame],
                           ratio_year: Optional[pd.DataFrame], year_cols: List[str]) -> Dict[str, Optional[float]]:
    """Tính chỉ số sinh lời ROE, ROA, ROIC và các biên lợi nhuận đồng bộ."""
    if not year_cols:
        return {"roe": None, "roa": None, "roic": None, "net_margin": None, "gross_margin": None, "operating_margin": None}

    latest_year = year_cols[0]
    roe_direct = _extract_metric(ratio_year, "roe", latest_year)
    roa_direct = _extract_metric(ratio_year, "roa", latest_year)
    roce_direct = _extract_metric(ratio_year, "return_on_capital_employed_roce", latest_year)

    ni = _extract_metric(income_year, NET_PROFIT_KEYS, latest_year)
    equity = _extract_metric(balance_year, EQUITY_KEYS, latest_year)
    assets = _extract_metric(balance_year, TOTAL_ASSETS_KEYS, latest_year)
    rev = _extract_metric(income_year, REVENUE_KEYS, latest_year)
    gp = _extract_metric(income_year, GROSS_PROFIT_KEYS, latest_year)
    op = _extract_metric(income_year, OPERATING_PROFIT_KEYS, latest_year)

    roe = _format_ratio(roe_direct) if roe_direct is not None else (_format_ratio(ni / equity) if (ni is not None and equity and equity > 0) else None)
    roa = _format_ratio(roa_direct) if roa_direct is not None else (_format_ratio(ni / assets) if (ni is not None and assets and assets > 0) else None)
    roic = _format_ratio(roce_direct)

    gross_m = _format_ratio(gp / rev) if (gp is not None and rev and rev > 0) else _format_ratio(_extract_metric(ratio_year, "gross_margin", latest_year))
    op_m = _format_ratio(op / rev) if (op is not None and rev and rev > 0) else _format_ratio(_extract_metric(ratio_year, "ebit_margin", latest_year))
    net_m = _format_ratio(ni / rev) if (ni is not None and rev and rev > 0) else _format_ratio(_extract_metric(ratio_year, "net_margin", latest_year))

    return {
        "roe": roe,
        "roa": roa,
        "roic": roic,
        "net_margin": net_m,
        "gross_margin": gross_m,
        "operating_margin": op_m,
    }


def _compute_leverage(balance_year: Optional[pd.DataFrame], ratio_year: Optional[pd.DataFrame],
                      year_cols: List[str]) -> Dict[str, Optional[float]]:
    """Tính các chỉ số đòn bẩy và thanh toán ngắn hạn."""
    if not year_cols:
        return {"debt_to_equity": None, "debt_to_assets": None, "current_ratio": None, "quick_ratio": None}

    latest_year = year_cols[0]
    de_direct = _extract_metric(ratio_year, "debt_to_equity", latest_year)
    da_direct = _extract_metric(ratio_year, "debt_to_assets", latest_year)
    cr_direct = _extract_metric(ratio_year, ["current_ratio", "short_term_ratio"], latest_year)
    qr_direct = _extract_metric(ratio_year, "quick_ratio", latest_year)

    liabilities = _extract_metric(balance_year, ["total_liabilities", "no_phai_tra", "tong_no_phai_tra"], latest_year)
    equity = _extract_metric(balance_year, EQUITY_KEYS, latest_year)
    assets = _extract_metric(balance_year, TOTAL_ASSETS_KEYS, latest_year)
    cur_assets = _extract_metric(balance_year, "current_assets", latest_year)
    cur_liab = _extract_metric(balance_year, "current_liabilities", latest_year)
    inv = _extract_metric(balance_year, ["inventories", "inventories_2"], latest_year) or 0.0

    de = _format_ratio(de_direct) if de_direct is not None else (round(liabilities / equity, 4) if (liabilities is not None and equity and equity > 0) else None)
    da = _format_ratio(da_direct) if da_direct is not None else (round(liabilities / assets, 4) if (liabilities is not None and assets and assets > 0) else None)
    cr = round(cr_direct, 4) if cr_direct is not None else (round(cur_assets / cur_liab, 4) if (cur_assets is not None and cur_liab and cur_liab > 0) else None)
    qr = round(qr_direct, 4) if qr_direct is not None else (round((cur_assets - inv) / cur_liab, 4) if (cur_assets is not None and cur_liab and cur_liab > 0) else None)

    return {
        "debt_to_equity": de,
        "debt_to_assets": da,
        "current_ratio": cr,
        "quick_ratio": qr,
    }


def _compute_cash_flow(cashflow_year: Optional[pd.DataFrame], income_year: Optional[pd.DataFrame],
                       year_cols: List[str]) -> Dict[str, Any]:
    """Tính dòng tiền hoạt động, dòng tiền tự do (FCF) và FCF/cổ phiếu với unit tách riêng từng field."""
    if not year_cols:
        return {
            "operating_cash_flow": None,
            "operating_cash_flow_unit": "VND",
            "free_cash_flow": None,
            "free_cash_flow_unit": "VND",
            "fcf_per_share": None,
            "fcf_per_share_unit": "VND/cổ phiếu",
        }

    latest_year = year_cols[0]
    ocf = _extract_metric(cashflow_year, "operating_cash_flow", latest_year)
    capex = _extract_metric(cashflow_year, "payment_for_fixed_assets_constructions_and_other_long_term_assets", latest_year)
    
    fcf = None
    if ocf is not None and capex is not None:
        fcf = ocf - abs(capex)

    fcf_per_share = None
    eps = _extract_metric(income_year, "earnings_per_share_vnd", latest_year)
    ni = _extract_metric(income_year, "net_profit", latest_year)
    if fcf is not None and eps and eps > 0 and ni and ni > 0:
        est_shares = ni / eps
        if est_shares > 0:
            fcf_per_share = round(fcf / est_shares, 2)

    return {
        "operating_cash_flow": ocf,
        "operating_cash_flow_unit": "VND",
        "free_cash_flow": fcf,
        "free_cash_flow_unit": "VND",
        "fcf_per_share": fcf_per_share,
        "fcf_per_share_unit": "VND/cổ phiếu",
    }


def _compute_banking_metrics(ratio_year: Optional[pd.DataFrame], balance_year: Optional[pd.DataFrame],
                            income_year: Optional[pd.DataFrame], year_cols: List[str]) -> Optional[Dict[str, Any]]:
    """Tính các chỉ số chuyên biệt cho nhóm ngân hàng (NIM, NPL, CIR, LDR, CAR, CASA)."""
    if not year_cols:
        return None
    latest_y = year_cols[0]

    nim = _extract_metric(ratio_year, ["net_interest_margin_nim", "nim"], latest_y)
    cir = _extract_metric(ratio_year, ["cost_income_ratio_cir", "cir"], latest_y)
    npl = _extract_metric(ratio_year, ["bad_debt_ratio", "npl", "loan_loss_provision_ratio"], latest_y)
    ldr = _extract_metric(ratio_year, ["outstanding_loans_customer_deposits", "ldr"], latest_y)
    car = _extract_metric(ratio_year, ["capital_adequacy_ratio_car", "car"], latest_y)
    casa = _extract_metric(ratio_year, ["demand_deposits_ratio", "casa"], latest_y)

    def _ratio_pct(val):
        if val is None:
            return None
        return round(val / 100.0, 4) if abs(val) > 1.0 else round(val, 4)

    return {
        "nim": _ratio_pct(nim),
        "cir": _ratio_pct(cir),
        "npl": _ratio_pct(npl),
        "ldr": _ratio_pct(ldr),
        "car": _ratio_pct(car),
        "casa": _ratio_pct(casa),
    }


def _compute_ttm(income_quarter: Optional[pd.DataFrame], quarter_cols: List[str]) -> Dict[str, Any]:
    """Tính các chỉ số TTM (Trailing Twelve Months) với chuẩn hóa đơn vị VND."""
    if not quarter_cols:
        return {
            "ttm_revenue": None,
            "ttm_revenue_unit": "VND",
            "ttm_net_income": None,
            "ttm_net_income_unit": "VND",
            "ttm_eps": None,
            "ttm_eps_unit": "VND/cổ phiếu",
        }

    recent_4q = quarter_cols[:4]
    rev_vals = []
    ni_vals = []
    eps_vals = []

    for q in recent_4q:
        q_rev = _extract_metric(income_quarter, REVENUE_KEYS, q)
        q_ni = _extract_metric(income_quarter, NET_PROFIT_KEYS, q)
        q_eps = _extract_metric(income_quarter, EPS_KEYS, q)

        if q_rev is not None:
            rev_vals.append(q_rev)
        if q_ni is not None:
            ni_vals.append(q_ni)
        if q_eps is not None:
            eps_vals.append(q_eps)

    ttm_rev = None
    if rev_vals:
        ttm_rev = sum(rev_vals) if len(rev_vals) == 4 else sum(rev_vals) * (4.0 / len(rev_vals))

    ttm_ni = None
    if ni_vals:
        ttm_ni = sum(ni_vals) if len(ni_vals) == 4 else sum(ni_vals) * (4.0 / len(ni_vals))

    ttm_eps_final = None
    if eps_vals:
        e_sum = sum(eps_vals) if len(eps_vals) == 4 else sum(eps_vals) * (4.0 / len(eps_vals))
        ttm_eps_final = round(e_sum / 1000.0, 2) if e_sum > 100000 else round(e_sum, 2)

    return {
        "ttm_revenue": ttm_rev,
        "ttm_revenue_unit": "VND",
        "ttm_net_income": ttm_ni,
        "ttm_net_income_unit": "VND",
        "ttm_eps": ttm_eps_final,
        "ttm_eps_unit": "VND/cổ phiếu",
    }


def _extract_dividend_per_share(events_df: Optional[pd.DataFrame], as_of_date: date) -> Optional[float]:
    """
    Trích xuất cổ tức bằng tiền mặt trên mỗi cổ phiếu (VND/cổ phiếu) trong vòng 1 năm tính đến as_of_date.
    Lấy từ bảng events của vnstock (cột value_per_share với action trả cổ tức tiền mặt).
    """
    if events_df is None or events_df.empty or "value_per_share" not in events_df.columns:
        return None

    try:
        df = events_df.copy()
        date_col = None
        for col in ["exright_date", "record_date", "public_date"]:
            if col in df.columns:
                date_col = col
                break
        
        if not date_col:
            return None

        df["event_date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
        df = df[df["event_date"].notna() & (df["event_date"] <= as_of_date)]

        # Lọc các sự kiện cổ tức tiền mặt (value_per_share > 0)
        cash_div = df[df["value_per_share"].notna() & (df["value_per_share"] > 0)]
        if cash_div.empty:
            return None

        # Tổng hợp cổ tức trong vòng 12 tháng gần nhất tính đến as_of_date
        one_year_prior = date(as_of_date.year - 1, as_of_date.month, as_of_date.day)
        recent_divs = cash_div[cash_div["event_date"] >= one_year_prior]
        
        if not recent_divs.empty:
            return round(float(recent_divs["value_per_share"].sum()), 2)
        else:
            # Nếu trong 12 tháng chưa có đợt mới, lấy đợt chia cổ tức gần nhất trước đó
            latest_row = cash_div.sort_values("event_date", ascending=False).iloc[0]
            return round(float(latest_row["value_per_share"]), 2)
    except Exception as e:
        log.warning(f"Lỗi tính dividend_per_share: {e}")
        return None


def _compute_per_share(income_year: Optional[pd.DataFrame], balance_year: Optional[pd.DataFrame],
                       ratio_year: Optional[pd.DataFrame], events_df: Optional[pd.DataFrame],
                       year_cols: List[str], as_of_date: date,
                       ttm_eps_val: Optional[float] = None) -> Dict[str, Any]:
    """Tính EPS, BVPS và Cổ tức mỗi cổ phiếu, chuẩn hoá đơn vị VND/cổ phiếu."""
    if not year_cols:
        return {
            "eps": None,
            "eps_unit": "VND/cổ phiếu",
            "eps_ttm": None,
            "eps_avg_5y": None,
            "bvps": None,
            "bvps_unit": "VND/cổ phiếu",
            "dividend_per_share": None,
            "dps": None,
            "dividend_per_share_unit": "VND/cổ phiếu",
        }

    latest_year = year_cols[0]
    
    # 1. EPS từ ratio hoặc income_statement
    eps_ratio = _extract_metric(ratio_year, "trailing_eps", latest_year)
    eps_inc = _extract_metric(income_year, "earnings_per_share_vnd", latest_year)
    
    if eps_ratio is not None and eps_ratio > 0:
        eps = round(eps_ratio)
    elif eps_inc is not None:
        eps = round(eps_inc / 1000.0) if eps_inc > 100000 else round(eps_inc)
    else:
        eps = None

    # EPS TTM
    eps_ttm = round(ttm_eps_val) if ttm_eps_val else eps

    # EPS trung bình 5 năm (hoặc số năm tối đa hiện có)
    eps_list = []
    for y in year_cols[:5]:
        e_r = _extract_metric(ratio_year, "trailing_eps", y)
        if e_r is not None and e_r > 0:
            eps_list.append(e_r)
        else:
            e_i = _extract_metric(income_year, "earnings_per_share_vnd", y)
            if e_i is not None:
                eps_list.append(e_i / 1000.0 if e_i > 100000 else e_i)
    eps_avg_5y = round(sum(eps_list) / len(eps_list)) if eps_list else eps

    # 2. BVPS
    bvps_direct = _extract_metric(ratio_year, ["book_value_per_share_bvps", "book_value_per_share"], latest_year)
    if bvps_direct is not None:
        bvps = round(bvps_direct, 2)
    else:
        equity = _extract_metric(balance_year, ["equity", "owners_equity", "owners_capital", "owners_equity_2", "owners_equity_3"], latest_year)
        ni = _extract_metric(income_year, "net_profit", latest_year)
        bvps = None
        if equity is not None and eps and eps > 0 and ni and ni > 0:
            est_shares = ni / eps
            if est_shares > 0:
                bvps = round(equity / est_shares, 2)

    # 3. Cổ tức bằng tiền mặt (DPS)
    div_per_share = _extract_dividend_per_share(events_df, as_of_date)
    # Nếu không có từ events, thử tính từ dividend_yield trong ratio nếu có
    if div_per_share is None:
        dy = _extract_metric(ratio_year, "dividend_yield", latest_year)
        if dy is not None and dy > 0 and bvps:
            # Ước lượng DPS = (dy% * P) xấp xỉ
            pass

    return {
        "eps": eps,
        "eps_unit": "VND/cổ phiếu",
        "eps_ttm": eps_ttm,
        "eps_avg_5y": eps_avg_5y,
        "bvps": bvps,
        "bvps_unit": "VND/cổ phiếu",
        "dividend_per_share": div_per_share,
        "dps": div_per_share,
        "dividend_per_share_unit": "VND/cổ phiếu",
    }


BASELINE_2021: Dict[str, Dict[str, Any]] = {
    "FPT": {"revenue": 35657000000000, "gross_profit": 13865000000000, "operating_profit": 6345000000000, "net_profit": 5337000000000, "total_assets": 53698000000000, "equity": 21418000000000, "eps": 4349, "bvps": 19530, "roe": 0.272, "roa": 0.108, "net_margin": 0.1497, "debt_to_equity": 1.51},
    "HPG": {"revenue": 149679000000000, "gross_profit": 41249000000000, "operating_profit": 37120000000000, "net_profit": 34521000000000, "total_assets": 178236000000000, "equity": 90781000000000, "eps": 7720, "bvps": 20290, "roe": 0.445, "roa": 0.221, "net_margin": 0.2306, "debt_to_equity": 0.96},
    "VCB": {"revenue": 62569000000000, "gross_profit": 30200000000000, "operating_profit": 27376000000000, "net_profit": 21940000000000, "total_assets": 1414484000000000, "equity": 110385000000000, "eps": 4450, "bvps": 29800, "roe": 0.218, "roa": 0.016, "net_margin": 0.3506, "debt_to_equity": 11.8},
    "ACB": {"revenue": 18944792000000, "gross_profit": 18944792000000, "operating_profit": 11995000000000, "net_profit": 9603000000000, "total_assets": 527769944000000, "equity": 45145000000000, "eps": 2799, "bvps": 16700, "roe": 0.238, "roa": 0.019, "net_margin": 0.507, "debt_to_equity": 10.7},
    "VNM": {"revenue": 60919000000000, "gross_profit": 26230000000000, "operating_profit": 12890000000000, "net_profit": 10633000000000, "total_assets": 53332000000000, "equity": 35849000000000, "eps": 4510, "bvps": 16800, "roe": 0.301, "roa": 0.202, "net_margin": 0.1745, "debt_to_equity": 0.49},
    "SSI": {"revenue": 7772000000000, "gross_profit": 4100000000000, "operating_profit": 3360000000000, "net_profit": 2672000000000, "total_assets": 50793000000000, "equity": 14472000000000, "eps": 3120, "bvps": 14500, "roe": 0.224, "roa": 0.063, "net_margin": 0.3438, "debt_to_equity": 2.51},
    "MWG": {"revenue": 122958000000000, "gross_profit": 27720000000000, "operating_profit": 6540000000000, "net_profit": 4901000000000, "total_assets": 62997000000000, "equity": 20378000000000, "eps": 3350, "bvps": 13900, "roe": 0.267, "roa": 0.088, "net_margin": 0.0399, "debt_to_equity": 2.09},
    "TCB": {"revenue": 37075000000000, "gross_profit": 28500000000000, "operating_profit": 23238000000000, "net_profit": 18400000000000, "total_assets": 568729000000000, "equity": 93080000000000, "eps": 5260, "bvps": 26500, "roe": 0.217, "roa": 0.036, "net_margin": 0.4963, "debt_to_equity": 5.11},
}


def _first_number(*values):
    """Giữ cả số 0, chỉ bỏ giá trị thiếu hoặc không hữu hạn."""
    for value in values:
        number = _safe_float(value)
        if number is not None and pd.notna(number) and abs(number) != float("inf"):
            return number
    return None


def _merge_annual_frames(fin_data, supplemental, as_of_date):
    """Ưu tiên từng chỉ tiêu HF, bù từ vnstock cùng năm; không ghép khác kỳ."""
    mappings = {
        "income_year": {"revenue": REVENUE_KEYS, "net_profit": NET_PROFIT_KEYS,
                        "gross_profit": GROSS_PROFIT_KEYS, "operating_profit": OPERATING_PROFIT_KEYS,
                        "eps": EPS_KEYS},
        "balance_year": {"total_assets": TOTAL_ASSETS_KEYS, "equity": EQUITY_KEYS,
                         "liabilities": ["total_liabilities", "no_phai_tra", "tong_no_phai_tra"]},
        "ratio_year": {"eps": ["trailing_eps"], "bvps": ["book_value_per_share_bvps", "book_value_per_share"],
                       "roe": ["roe"], "roa": ["roa"], "net_margin": ["net_margin"],
                       "debt_to_equity": ["debt_to_equity"]},
    }
    result = dict(fin_data)
    provenance = {}
    for name, mapping in mappings.items():
        original = fin_data.get(name)
        frame = original.copy() if original is not None else pd.DataFrame(columns=["item_id"])
        years = set(_get_sorted_periods(original, as_of_date=as_of_date))
        years.update(y for y in supplemental if str(y).isdigit() and int(y) <= as_of_date.year)
        rows = []
        for metric, aliases in mapping.items():
            row = {"item_id": aliases[0]}
            for year in sorted(years):
                preferred = _first_number(supplemental.get(year, {}).get(metric))
                fallback = _first_number(_extract_metric(original, aliases, year))
                row[year] = preferred if preferred is not None else fallback
                if row[year] is not None:
                    provenance.setdefault(year, {})[f"{name}.{metric}"] = (
                        "vnfinancialdata" if preferred is not None else "vnstock")
            # Bỏ các alias cũ để các hàm tính chỉ số cùng đọc giá trị đã ghép.
            if "item_id" in frame:
                frame = frame[~frame["item_id"].isin(aliases)]
            rows.append(row)
        result[name] = pd.concat([pd.DataFrame(rows), frame], ignore_index=True)
    return result, provenance


def _fetch_vnfinancialdata_table(ticker: str) -> Dict[str, Dict[str, Any]]:
    """
    Tải BCTC các năm 2021-2025 từ vnfinancialdata (Hugging Face).
    Trả về dict {year_str: {...row metrics...}}.
    """
    res = {}
    try:
        import vnfinancialdata as vnf
        for ex in ["HSX", "HNX"]:
            try:
                frames = []
                for statement in ("income_statement", "balance_sheet"):
                    try:
                        frame = vnf.get(ticker=ticker, statement=statement, exchange=ex, start=2021, end=2025)
                        frames.append(frame if frame is not None else pd.DataFrame())
                    except Exception as exc:
                        log.warning("vnfinancialdata %s/%s: %s", ex, statement, type(exc).__name__)
                        frames.append(pd.DataFrame())
                df_inc, df_bs = frames
                if not df_inc.empty or not df_bs.empty:
                    years = sorted(set(df_inc.get("year", [])) | set(df_bs.get("year", [])))
                    for y in years:
                        sub_inc = df_inc[df_inc["year"] == y].set_index("item_code")["value"].to_dict() if not df_inc.empty else {}
                        sub_bs = df_bs[df_bs["year"] == y].set_index("item_code")["value"].to_dict() if not df_bs.empty else {}

                        # Doanh thu: hỗ trợ phi tài chính, chứng khoán, ngân hàng
                        rev = (
                            _first_number(sub_inc.get("is_doanh_so_thuan"), sub_inc.get("is_doanh_so"), sub_inc.get("is_thu_nhap_lai_thuan"), sub_inc.get("is_tong_thu_nhap_hoat_dong"), sub_inc.get("is_doanh_thu_hoat_dong"), sub_inc.get("is_doanh_thu_thuan_ve_hoat_dong_kinh_doanh"), sub_inc.get("is_doanh_thu_thuan"))
                        )
                        gp = (
                            _first_number(sub_inc.get("is_lai_gop"), sub_inc.get("is_loi_nhuan_gop"), sub_inc.get("is_thu_nhap_lai_thuan"), sub_inc.get("is_tong_thu_nhap_hoat_dong"), sub_inc.get("is_ket_qua_hoat_dong"))
                        )
                        op = (
                            _first_number(sub_inc.get("is_lai_lo_tu_hoat_dong_kinh_doanh"), sub_inc.get("is_ebit"), sub_inc.get("is_ln_thuan_tu_hoat_dong_kinh_doanh_truoc_cf_du_phong_rui_ro_tin_dung"), sub_inc.get("is_tong_loi_nhuan_truoc_thue"), sub_inc.get("is_ket_qua_hoat_dong"))
                        )
                        ni = (
                            _first_number(sub_inc.get("is_loi_nhuan_sau_thue"), sub_inc.get("is_lai_lo_thuan_sau_thue"), sub_inc.get("is_co_dong_cua_cong_ty_me"), sub_inc.get("is_loi_nhuan_cua_co_dong_cua_cong_ty_me"), sub_inc.get("is_loi_nhuan_sau_thue_phan_bo_cho_chu_so_huu"), sub_inc.get("is_loi_nhuan_ke_toan_sau_thue"))
                        )
                        eps = _first_number(sub_inc.get("is_lai_co_ban_tren_co_phieu"), sub_inc.get("is_thu_nhap_thuan_tren_co_phieu_pho_thong"))

                        assets = _first_number(sub_bs.get("bs_tong_tai_san"), sub_bs.get("bs_tong_cong_tai_san"))
                        equity = None
                        for eq_k, eq_v in sub_bs.items():
                            if eq_k.startswith("bs_von_chu_so_huu") or eq_k in ("bs_tong_von_chu_so_huu", "bs_von_va_cac_quy"):
                                if _first_number(eq_v) is not None:
                                    equity = eq_v
                                    break
                        if equity is None:
                            equity = _first_number(sub_bs.get("bs_von_chu_so_huu_4d280b22"), sub_bs.get("bs_von_chu_so_huu_6cda78ae"), sub_bs.get("bs_von_va_cac_quy"))
                        debt = _first_number(sub_bs.get("bs_tong_no_phai_tra"), sub_bs.get("bs_no_phai_tra"))

                        roe = round(ni / equity, 4) if (ni is not None and equity is not None and equity > 0) else None
                        roa = round(ni / assets, 4) if (ni is not None and assets is not None and assets > 0) else None
                        net_m = round(ni / rev, 4) if (ni is not None and rev is not None and rev > 0) else None
                        de = round(debt / equity, 4) if (debt is not None and equity is not None and equity > 0) else None

                        bvps = None
                        if equity and eps and ni and ni > 0 and eps > 0:
                            est_shares = ni / eps
                            bvps = round(equity / est_shares)

                        res[str(y)] = {
                            "year": str(y),
                            "revenue": rev,
                            "gross_profit": gp,
                            "operating_profit": op,
                            "net_profit": ni,
                            "total_assets": assets,
                            "equity": equity,
                            "eps": round(eps) if eps is not None else None,
                            "liabilities": debt,
                            "bvps": bvps,
                            "roe": roe,
                            "roa": roa,
                            "net_margin": net_m,
                            "debt_to_equity": de
                        }
                    if res:
                        return res
            except Exception:
                continue
    except Exception as e:
        log.warning(f"Bỏ qua vnfinancialdata: {e}")
    return res


def _compute_financials_table(
    income_year: Optional[pd.DataFrame],
    balance_year: Optional[pd.DataFrame],
    ratio_year: Optional[pd.DataFrame],
    year_cols: List[str],
    ttm_data: Optional[Dict[str, Any]] = None,
    per_share_data: Optional[Dict[str, Any]] = None,
    ticker: str = "",
    vnf_data: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Dựng bảng tổng hợp báo cáo tài chính đa niên kết hợp:
    - 2026: Lấy các quý gần nhất từ vnstock (TTM).
    - Bảng năm: ưu tiên vnfinancialdata, bù từng chỉ tiêu từ vnstock cùng kỳ.
    """
    table = []

    # 1. Bổ sung kỳ 2026 (TTM) từ vnstock
    if ttm_data and (ttm_data.get("ttm_revenue") is not None or ttm_data.get("ttm_net_income") is not None):
        latest_y = year_cols[0] if year_cols else "2025"
        latest_assets = _extract_metric(balance_year, TOTAL_ASSETS_KEYS, latest_y)
        latest_equity = _extract_metric(balance_year, EQUITY_KEYS, latest_y)
        latest_bvps = _extract_metric(ratio_year, ["book_value_per_share_bvps", "book_value_per_share"], latest_y)
        latest_roe = _format_ratio(_extract_metric(ratio_year, "roe", latest_y))
        latest_roa = _format_ratio(_extract_metric(ratio_year, "roa", latest_y))
        latest_de = _format_ratio(_extract_metric(ratio_year, "debt_to_equity", latest_y))
        ttm_rev = ttm_data.get("ttm_revenue")
        ttm_ni = ttm_data.get("ttm_net_income")
        net_m = round(ttm_ni / ttm_rev, 4) if (ttm_ni and ttm_rev and ttm_rev > 0) else None

        table.append({
            "year": "2026 (TTM)",
            "revenue": ttm_rev,
            "gross_profit": None,
            "operating_profit": None,
            "net_profit": ttm_ni,
            "total_assets": latest_assets,
            "equity": latest_equity,
            "eps": round(ttm_data.get("ttm_eps")) if ttm_data.get("ttm_eps") else None,
            "bvps": round(latest_bvps) if latest_bvps else None,
            "roe": latest_roe,
            "roa": latest_roa,
            "net_margin": net_m,
            "debt_to_equity": latest_de,
        })

    # 2. Lấy dữ liệu 2021 - 2025 từ vnfinancialdata
    if vnf_data is None:
        raw = _fetch_vnfinancialdata_table(ticker)
        merged, _ = _merge_annual_frames(
            {"income_year": income_year, "balance_year": balance_year, "ratio_year": ratio_year},
            raw, date.today())
        income_year, balance_year, ratio_year = (merged[k] for k in ("income_year", "balance_year", "ratio_year"))
        year_cols = sorted(set(year_cols) | set(_get_sorted_periods(income_year)) | set(_get_sorted_periods(balance_year)), reverse=True)

    years_needed = sorted(set(year_cols), reverse=True)
    for y in years_needed:

        # Fallback 1: lấy từ vnstock income_year / balance_year
        rev = _extract_metric(income_year, REVENUE_KEYS, y)
        gp = _extract_metric(income_year, GROSS_PROFIT_KEYS, y)
        op = _extract_metric(income_year, OPERATING_PROFIT_KEYS, y)
        ni = _extract_metric(income_year, NET_PROFIT_KEYS, y)
        assets = _extract_metric(balance_year, TOTAL_ASSETS_KEYS, y)
        equity = _extract_metric(balance_year, EQUITY_KEYS, y)
        liab = _extract_metric(balance_year, ["total_liabilities", "no_phai_tra", "tong_no_phai_tra"], y)

        eps_val = _extract_metric(ratio_year, "trailing_eps", y)
        if eps_val is None:
            eps_inc = _extract_metric(income_year, "earnings_per_share_vnd", y)
            if eps_inc:
                eps_val = eps_inc / 1000.0 if eps_inc > 100000 else eps_inc

        bvps_val = _extract_metric(ratio_year, ["book_value_per_share_bvps", "book_value_per_share"], y)
        roe_val = _format_ratio(_extract_metric(ratio_year, "roe", y))
        if roe_val is None and ni and equity and equity > 0:
            roe_val = round(ni / equity, 4)

        roa_val = _format_ratio(_extract_metric(ratio_year, "roa", y))
        if roa_val is None and ni and assets and assets > 0:
            roa_val = round(ni / assets, 4)

        net_m = _format_ratio(_extract_metric(ratio_year, "net_margin", y))
        if net_m is None and ni and rev and rev > 0:
            net_m = round(ni / rev, 4)

        de_val = _format_ratio(_extract_metric(ratio_year, "debt_to_equity", y))
        if de_val is None and liab and equity and equity > 0:
            de_val = round(liab / equity, 4)

        if any(value is not None for value in (rev, ni, assets, equity)):
            table.append({
                "year": str(y),
                "revenue": rev,
                "gross_profit": gp,
                "operating_profit": op,
                "net_profit": ni,
                "total_assets": assets,
                "equity": equity,
                "eps": round(eps_val) if eps_val is not None else None,
                "bvps": round(bvps_val) if bvps_val is not None else None,
                "roe": roe_val,
                "roa": roa_val,
                "net_margin": net_m,
                "debt_to_equity": de_val,
            })
    return table


# ══════════════════════════════ HÀM CHÍNH ══════════════════════════════════════

def run(ticker: str, as_of: str) -> dict:
    """
    Thu thập và chuẩn hóa báo cáo tài chính + chỉ số tài chính cho một mã cổ phiếu.

    Args:
        ticker: Mã cổ phiếu (VD: "VNM", "FPT")
        as_of: Ngày định dạng "YYYY-MM-DD", lấy báo cáo gần nhất tính đến thời điểm này

    Returns:
        dict theo đúng cấu trúc JSON yêu cầu. Nếu lỗi: {"ticker": ticker, "error": "..."}
    """
    ticker = str(ticker).upper().strip()
    generated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def _persist(data: dict) -> dict:
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            log.info(f"Đã lưu kết quả vào: {OUTPUT_FILE}")
        except Exception as err:
            log.error(f"Lỗi ghi file JSON: {err}")
        return data

    # 1. Kiểm tra tính hợp lệ của as_of
    try:
        as_of_date = date.fromisoformat(as_of)
    except ValueError:
        return _persist({
            "ticker": ticker,
            "error": f"Định dạng ngày as_of không hợp lệ: '{as_of}'. Vui lòng dùng định dạng YYYY-MM-DD."
        })

    # Hai nguồn độc lập: lỗi một nguồn không ngăn thử nguồn còn lại.
    warnings = []
    log.info(f"Bắt đầu thu thập dữ liệu cho mã {ticker} tính đến {as_of}...")
    try:
        fin_data = _fetch_vnstock_data(ticker)
    except Exception as exc:
        warnings.append(f"vnstock không khả dụng ({type(exc).__name__}); thử nguồn bổ sung.")
        fin_data = {}

    has_vnstock = any(isinstance(v, pd.DataFrame) and not v.empty for v in fin_data.values())
    try:
        supplemental = _fetch_vnfinancialdata_table(ticker)
    except Exception as exc:
        warnings.append(f"vnfinancialdata không khả dụng ({type(exc).__name__}); dùng vnstock nếu có.")
        supplemental = {}
    supplemental = {str(y): row for y, row in supplemental.items()
                    if str(y).isdigit() and int(y) <= as_of_date.year}
    if not supplemental:
        warnings.append("vnfinancialdata không có dữ liệu phù hợp; dùng vnstock nếu có.")
    if not has_vnstock:
        warnings.append("vnstock không có dữ liệu; chỉ sử dụng nguồn bổ sung nếu có.")
    fin_data, provenance = _merge_annual_frames(fin_data, supplemental, as_of_date)

    income_year = fin_data.get("income_year")
    balance_year = fin_data.get("balance_year")
    cashflow_year = fin_data.get("cashflow_year")
    income_quarter = fin_data.get("income_quarter")
    ratio_year = fin_data.get("ratio_year")
    events_df = fin_data.get("events")

    # Kiểm tra có dữ liệu trả về hay không
    if not provenance:
        return _persist({
            "ticker": ticker,
            "error": f"Không tìm thấy dữ liệu tài chính phù hợp từ cả hai nguồn cho '{ticker}'.",
            "warnings": warnings,
        })

    # 3. Lấy danh sách các kỳ báo cáo hợp lệ <= as_of
    year_cols = sorted(provenance, reverse=True)
    quarter_cols = _get_sorted_periods(income_quarter, is_quarter=True, as_of_date=as_of_date)

    if not year_cols:
        return _persist({
            "ticker": ticker,
            "error": f"Không có kỳ báo cáo tài chính nào của '{ticker}' trước hoặc bằng ngày {as_of}."
        })

    sources = []
    if has_vnstock:
        sources.append({"name": "vnstock (KBS)", "url": "https://vnstocks.com", "date": as_of})
    if any("vnfinancialdata" in fields.values() for fields in provenance.values()):
        sources.append({
            "name": "vnfinancialdata (Hugging Face)",
            "url": "https://huggingface.co/datasets/thanhnp-uel/vietnam-listed-companies-financial-statements",
            "date": as_of
        })

    # 5. Xây dựng JSON đầu ra chuẩn hóa
    ttm_data = _compute_ttm(income_quarter, quarter_cols)
    per_share_data = _compute_per_share(income_year, balance_year, ratio_year, events_df, year_cols, as_of_date, ttm_data.get("ttm_eps"))
    fin_table = _compute_financials_table(
        income_year, balance_year, ratio_year, year_cols,
        ttm_data=ttm_data, per_share_data=per_share_data, ticker=ticker, vnf_data={}
    )



    detected_sec = _detect_sector(ticker)
    banking_data = _compute_banking_metrics(ratio_year, balance_year, income_year, year_cols) if "ngân hàng" in detected_sec.lower() else None

    # Bội số định giá ngành tham chiếu
    sec_ref = _SECTOR_MULTIPLES.get(detected_sec) or {"pe": {"p25": 10.0, "median": 13.0, "p75": 17.0}, "pb": {"p25": 1.2, "median": 1.6, "p75": 2.2}}
    if ticker in _TICKER_MULTIPLES:
        sec_ref = _TICKER_MULTIPLES[ticker]

    result = {
        "ticker": ticker,
        "as_of": as_of,
        "generated_at": generated_at,
        "latest_period": year_cols[0],
        "sector": detected_sec,
        "valuation_ref": internal_reference(sec_ref, detected_sec),
        "financials_table": fin_table,
        "field_sources": provenance,
        "warnings": warnings,
        "unit": "VND",
        "basis": financial_basis({"financials_table": fin_table, "latest_period": year_cols[0]}),
        "growth": _compute_growth(income_year, income_quarter, ratio_year, year_cols, quarter_cols),
        "margins": _compute_margins(income_year, year_cols),
        "profitability": _compute_profitability(income_year, balance_year, ratio_year, year_cols),
        "leverage": _compute_leverage(balance_year, ratio_year, year_cols),
        "banking": banking_data,
        "cash_flow": _compute_cash_flow(cashflow_year, income_year, year_cols),
        "ttm": ttm_data,
        "per_share": per_share_data,
        "sources": sources
    }

    return _persist(result)


# ══════════════════════════════ ĐIỂM CHẠY DÒNG LỆNH ══════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Sử dụng: python fundamental.py <ticker> <as_of>")
        print("Ví dụ:   python fundamental.py VNM 2026-10-09")
        sys.exit(1)

    input_ticker = sys.argv[1]
    input_as_of = sys.argv[2]

    output_data = run(input_ticker, input_as_of)
    print(json.dumps(output_data, ensure_ascii=False, indent=2, default=str))
