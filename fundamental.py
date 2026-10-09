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

    # Thử tải sự kiện doanh nghiệp (cổ tức, v.v.)
    try:
        from vnstock import Company
        events_df = Company(symbol=ticker, source="KBS").events()
        data["events"] = events_df if (events_df is not None and not events_df.empty) else None
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
        for ex in ["HOSE", "HNX", "UPCOM"]:
            try:
                df = vnf.load(exchange=ex, statement="income_statement", ticker=ticker)
                if df is not None and not df.empty:
                    return df
            except Exception:
                continue
        return None
    except Exception as e:
        log.warning(f"Bỏ qua vnfinancialdata: {e}")
        return None


# ══════════════════════════════ TÍNH TOÁN CHỈ SỐ ══════════════════════════════

def _compute_growth(income_year: Optional[pd.DataFrame], income_quarter: Optional[pd.DataFrame],
                    year_cols: List[str], quarter_cols: List[str]) -> Dict[str, Optional[float]]:
    """Tính các chỉ số tăng trưởng doanh thu và lợi nhuận."""
    rev_y0, rev_y1 = None, None
    ni_y0, ni_y1 = None, None
    if len(year_cols) >= 1:
        rev_y0 = _extract_metric(income_year, "revenue", year_cols[0])
        ni_y0 = _extract_metric(income_year, "net_profit", year_cols[0])
    if len(year_cols) >= 2:
        rev_y1 = _extract_metric(income_year, "revenue", year_cols[1])
        ni_y1 = _extract_metric(income_year, "net_profit", year_cols[1])

    rev_q0, rev_q1 = None, None
    if len(quarter_cols) >= 2:
        rev_q0 = _extract_metric(income_quarter, "revenue", quarter_cols[0])
        rev_q1 = _extract_metric(income_quarter, "revenue", quarter_cols[1])

    return {
        "revenue_growth_yoy": _calc_yoy(rev_y0, rev_y1),
        "net_income_growth_yoy": _calc_yoy(ni_y0, ni_y1),
        "revenue_growth_qoq": _calc_yoy(rev_q0, rev_q1),
    }


def _compute_margins(income_year: Optional[pd.DataFrame], year_cols: List[str]) -> Dict[str, Optional[float]]:
    """Tính biên lợi nhuận gộp, hoạt động và ròng."""
    if not year_cols:
        return {"gross_margin": None, "operating_margin": None, "net_margin": None}

    latest_year = year_cols[0]
    rev = _extract_metric(income_year, "revenue", latest_year)
    gp = _extract_metric(income_year, "gross_profit", latest_year)
    op = _extract_metric(income_year, "operating_profit", latest_year)
    ni = _extract_metric(income_year, "net_profit", latest_year)

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
    """Tính chỉ số sinh lời ROE, ROA, ROIC."""
    if not year_cols:
        return {"roe": None, "roa": None, "roic": None}

    latest_year = year_cols[0]
    roe_direct = _extract_metric(ratio_year, "roe", latest_year)
    roa_direct = _extract_metric(ratio_year, "roa", latest_year)

    ni = _extract_metric(income_year, "net_profit", latest_year)
    equity = _extract_metric(balance_year, ["equity", "owners_equity", "owners_capital", "owners_equity_2", "owners_equity_3"], latest_year)
    assets = _extract_metric(balance_year, "total_assets", latest_year)

    roe = _format_ratio(roe_direct) if roe_direct is not None else (_format_ratio(ni / equity) if (ni is not None and equity and equity > 0) else None)
    roa = _format_ratio(roa_direct) if roa_direct is not None else (_format_ratio(ni / assets) if (ni is not None and assets and assets > 0) else None)

    return {
        "roe": roe,
        "roa": roa,
        "roic": None,
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

    liabilities = _extract_metric(balance_year, "total_liabilities", latest_year)
    equity = _extract_metric(balance_year, ["equity", "owners_equity", "owners_capital", "owners_equity_2", "owners_equity_3"], latest_year)
    assets = _extract_metric(balance_year, "total_assets", latest_year)
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


def _compute_ttm(income_quarter: Optional[pd.DataFrame], quarter_cols: List[str]) -> Dict[str, Any]:
    """Tính các chỉ số TTM (Trailing Twelve Months) với unit tách riêng từng field."""
    if len(quarter_cols) < 4:
        return {
            "ttm_revenue": None,
            "ttm_revenue_unit": "VND",
            "ttm_net_income": None,
            "ttm_net_income_unit": "VND",
            "ttm_eps": None,
            "ttm_eps_unit": "VND/cổ phiếu",
        }

    recent_4q = quarter_cols[:4]
    rev_sum = 0.0
    ni_sum = 0.0
    eps_sum = 0.0
    has_rev, has_ni, has_eps = True, True, True

    for q in recent_4q:
        q_rev = _extract_metric(income_quarter, "revenue", q)
        q_ni = _extract_metric(income_quarter, "net_profit", q)
        q_eps = _extract_metric(income_quarter, "earnings_per_share_vnd", q)

        if q_rev is not None:
            rev_sum += q_rev
        else:
            has_rev = False

        if q_ni is not None:
            ni_sum += q_ni
        else:
            has_ni = False

        if q_eps is not None:
            eps_sum += q_eps
        else:
            has_eps = False

    return {
        "ttm_revenue": rev_sum if has_rev else None,
        "ttm_revenue_unit": "VND",
        "ttm_net_income": ni_sum if has_ni else None,
        "ttm_net_income_unit": "VND",
        "ttm_eps": round(eps_sum, 2) if has_eps else None,
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
                       year_cols: List[str], as_of_date: date) -> Dict[str, Any]:
    """Tính EPS, BVPS và Cổ tức mỗi cổ phiếu với unit tách riêng từng field."""
    if not year_cols:
        return {
            "eps": None,
            "eps_unit": "VND/cổ phiếu",
            "bvps": None,
            "bvps_unit": "VND/cổ phiếu",
            "dividend_per_share": None,
            "dividend_per_share_unit": "VND/cổ phiếu",
        }

    latest_year = year_cols[0]
    eps = _extract_metric(income_year, "earnings_per_share_vnd", latest_year)
    if eps is None:
        eps = _extract_metric(ratio_year, ["trailing_eps", "earnings_per_share"], latest_year)

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

    div_per_share = _extract_dividend_per_share(events_df, as_of_date)

    return {
        "eps": eps,
        "eps_unit": "VND/cổ phiếu",
        "bvps": bvps,
        "bvps_unit": "VND/cổ phiếu",
        "dividend_per_share": div_per_share,
        "dividend_per_share_unit": "VND/cổ phiếu",
    }


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

    # 2. Tải dữ liệu từ vnstock
    log.info(f"Bắt đầu thu thập dữ liệu cho mã {ticker} tính đến {as_of}...")
    try:
        fin_data = _fetch_vnstock_data(ticker)
    except Exception as exc:
        msg = str(exc)
        if any(w in msg.lower() for w in ["not found", "không tìm thấy", "invalid", "404"]):
            err_desc = f"Mã '{ticker}' không tồn tại hoặc thuộc sàn UPCoM chưa được hỗ trợ."
        elif any(w in msg.lower() for w in ["timeout", "connection", "mạng", "network"]):
            err_desc = f"Lỗi kết nối mạng khi tải dữ liệu cho '{ticker}': {msg}"
        else:
            err_desc = f"Lỗi gọi API vnstock: {msg}"
        return _persist({"ticker": ticker, "error": err_desc})

    income_year = fin_data.get("income_year")
    balance_year = fin_data.get("balance_year")
    cashflow_year = fin_data.get("cashflow_year")
    income_quarter = fin_data.get("income_quarter")
    ratio_year = fin_data.get("ratio_year")
    events_df = fin_data.get("events")

    # Kiểm tra có dữ liệu trả về hay không
    if not any(df is not None and not df.empty for df in [income_year, balance_year]):
        return _persist({
            "ticker": ticker,
            "error": f"Không tìm thấy dữ liệu báo cáo tài chính cho mã '{ticker}'. Mã có thể không tồn tại hoặc thuộc UPCoM."
        })

    # 3. Lấy danh sách các kỳ báo cáo hợp lệ <= as_of
    ref_df = income_year if income_year is not None else balance_year
    year_cols = _get_sorted_periods(ref_df, is_quarter=False, as_of_date=as_of_date)
    quarter_cols = _get_sorted_periods(income_quarter, is_quarter=True, as_of_date=as_of_date)

    if not year_cols:
        return _persist({
            "ticker": ticker,
            "error": f"Không có kỳ báo cáo tài chính nào của '{ticker}' trước hoặc bằng ngày {as_of}."
        })

    # 4. Thử nạp vnfinancialdata (nếu có cấu hình)
    _fetch_vnfinancialdata(ticker)

    sources = [
        {"name": "vnstock (KBS)", "url": "https://vnstocks.com", "date": as_of}
    ]
    if HF_TOKEN:
        sources.append({
            "name": "vnfinancialdata (Hugging Face)",
            "url": "https://huggingface.co/datasets/datasets-for-good/vnfinancialdata",
            "date": as_of
        })

    # 5. Xây dựng JSON đầu ra chuẩn hóa
    result = {
        "ticker": ticker,
        "as_of": as_of,
        "generated_at": generated_at,
        "latest_period": year_cols[0],
        "growth": _compute_growth(income_year, income_quarter, year_cols, quarter_cols),
        "margins": _compute_margins(income_year, year_cols),
        "profitability": _compute_profitability(income_year, balance_year, ratio_year, year_cols),
        "leverage": _compute_leverage(balance_year, ratio_year, year_cols),
        "cash_flow": _compute_cash_flow(cashflow_year, income_year, year_cols),
        "ttm": _compute_ttm(income_quarter, quarter_cols),
        "per_share": _compute_per_share(income_year, balance_year, ratio_year, events_df, year_cols, as_of_date),
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
