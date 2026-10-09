import json
import os
import re
import sys
import unicodedata
from datetime import date

OUTPUT_FILE = "assessment.json"
RATINGS = ["Có cơ hội", "Cần theo dõi", "Chưa hấp dẫn"]
NO_DATA = "Thiếu dữ liệu"
CONF = ["Cao", "Trung bình", "Thấp"]
CASES = ("bear", "base", "bull")
CASE_KEY = {"bear": "p25", "base": "median", "bull": "p75"}
SCEN_W = {"bear": 0.25, "base": 0.50, "bull": 0.25}

# Ngưỡng "Có cơ hội" theo hồ sơ rủi ro; "Cần theo dõi" = 0% <= upside < ngưỡng; dưới 0% = "Chưa hấp dẫn"
RISK_THRESHOLD = {"conservative": 0.25, "balanced": 0.20, "aggressive": 0.15}

# (trọng số P/B, trọng số P/E)
SECTOR_WEIGHTS = {
    "Ngân hàng": (0.70, 0.30), "Chứng khoán": (0.60, 0.40), "Bảo hiểm": (0.50, 0.50),
    "Bất động sản": (0.60, 0.40), "Ngành chu kỳ": (0.50, 0.50), "Khác": (0.30, 0.70),
}
CYCLICAL_KW = ("thép", "hóa chất", "khai khoáng", "khai thác", "dầu khí", "phân bón", "xi măng",
               "kim loại", "cao su", "vận tải biển", "hàng hải", "nhựa")

# Tham số kịch bản (giả định của nhóm, có thể chỉnh)
G_MIN, G_MAX, G_GAP = -0.10, 0.15, 0.10     # tăng trưởng EPS cơ sở bị chặn; bear/bull = cơ sở -/+ G_GAP
GORDON_R = 0.12                              # chi phí vốn chủ sở hữu
GORDON_G_CAP, GORDON_G_DEFAULT = 0.05, 0.03  # tăng trưởng dài hạn
GORDON_SPREAD = 0.01                         # bear: r+1%, g-1%; bull: r-1%, g+1%
BEAR_DROP_LIMIT, ABSURD_HI, ABSURD_LO, RSI_HOT = -0.25, 0.50, -0.40, 75

DISCLAIMER = ("Kết quả do hệ thống tự động tạo từ dữ liệu công khai, chỉ mang tính tham khảo, "
              "không phải khuyến nghị mua/bán. Nhà đầu tư tự chịu trách nhiệm với quyết định của mình.")


# ------------------------------------------------------------------ tiện ích
def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def _g(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _pct(x):
    return f"{x * 100:.1f}%"


def _vnd(x):
    return f"{x:,.0f}".replace(",", ".") + " VND"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _drop_conf(c):
    return CONF[min(CONF.index(c) + 1, len(CONF) - 1)]


def _worse_conf(c, cap):
    return CONF[max(CONF.index(c), CONF.index(cap))]


def sector_group(sector):
    """Chuẩn hóa trường sector về nhóm định giá."""
    s = unicodedata.normalize("NFC", str(sector or "")).lower()
    for name in ("Ngân hàng", "Chứng khoán", "Bảo hiểm", "Bất động sản"):
        if name.lower() in s:
            return name
    if any(k in s for k in CYCLICAL_KW):
        return "Ngành chu kỳ"
    return "Khác"


def _mult_ok(m):
    v = [_num(_g(m, k)) for k in ("p25", "median", "p75")]
    if None in v or min(v) <= 0:
        return None
    v.sort()
    return {"p25": v[0], "median": v[1], "p75": v[2]}


def _merge_sources(*docs):
    seen, out = set(), []
    for d in docs:
        for s in (d or {}).get("sources") or []:
            key = (s.get("name"), s.get("url"))
            if key not in seen:
                seen.add(key)
                out.append({"name": s.get("name"), "url": s.get("url"), "date": s.get("date")})
    return out


# ------------------------------------------------------------------ định giá
def _growth_cases(fund):
    cagr = _num(_g(fund, "growth", "eps_cagr_3y"))
    base = 0.0 if cagr is None else _clamp(cagr, G_MIN, G_MAX)
    return {"bear": _clamp(base - G_GAP, -0.30, 0.30), "base": base,
            "bull": _clamp(base + G_GAP, -0.30, 0.30)}, cagr


def comp_value(c, case, shock=0.0, mult_case=None):
    """Giá trị 1 phương pháp ở 1 kịch bản. shock: thay đổi cơ sở (EPS/BVPS/DPS)."""
    mc = mult_case or case
    b = c["basis_value"] * (1 + shock)
    if c["kind"] == "PE":
        return b * (1 + c["growth"][case]) * c["multiples"][CASE_KEY[mc]]
    if c["kind"] == "PB":
        return b * c["multiples"][CASE_KEY[mc]]
    r, g = c["params"][mc]
    return b * (1 + g) / (r - g)


def _finish(c):
    c["values"] = {k: round(comp_value(c, k)) for k in CASES}
    return c


def _multiple_components(group, ref, eps_basis, bvps, growth, eps_label):
    """Dựng P/E, P/B theo trọng số ngành. P/E hoặc P/B âm/lỗi -> dồn 100% vào phương pháp còn lại."""
    pe_m, pb_m = _mult_ok(_g(ref, "pe")), _mult_ok(_g(ref, "pb"))
    pe_ok = pe_m is not None and eps_basis is not None and eps_basis > 0
    pb_ok = pb_m is not None and bvps is not None and bvps > 0
    w_pb, w_pe = SECTOR_WEIGHTS[group]
    if pe_ok and pb_ok:
        wts = {"PB": w_pb, "PE": w_pe}
    elif pb_ok:
        wts = {"PB": 1.0}
    elif pe_ok:
        wts = {"PE": 1.0}
    else:
        return []
    comps = []
    if "PB" in wts:
        comps.append(_finish({"name": "P/B", "kind": "PB", "weight": wts["PB"],
                              "basis_label": "BVPS hiện tại (VND/cp)", "basis_value": bvps, "multiples": pb_m}))
    if "PE" in wts:
        comps.append(_finish({"name": "P/E", "kind": "PE", "weight": wts["PE"], "basis_label": eps_label,
                              "basis_value": eps_basis, "multiples": pe_m, "growth": growth}))
    return comps


def _gordon_component(dps, growth_base, cagr):
    if dps is None or dps <= 0:
        return None
    gb = GORDON_G_DEFAULT if cagr is None else _clamp(cagr, 0.0, GORDON_G_CAP)
    params = {"bear": (GORDON_R + GORDON_SPREAD, max(0.0, gb - GORDON_SPREAD)),
              "base": (GORDON_R, gb),
              "bull": (GORDON_R - GORDON_SPREAD, gb + GORDON_SPREAD)}
    if any(r <= g for r, g in params.values()):
        return None
    return _finish({"name": "Gordon (chiết khấu cổ tức)", "kind": "GORDON", "weight": 1.0,
                    "basis_label": "Cổ tức DPS (VND/cp), D1 = DPS x (1+g)", "basis_value": dps, "params": params})


def valuate(fund, tech, growth, cagr, group, gaps, assumptions):
    """Fallback 4 cấp. Trả (components, level, level_label)."""
    ref = fund.get("valuation_ref") or {}
    src = str(ref.get("source") or "").lower()
    is_hist = "lịch sử" in src
    sector_ref = None if is_hist else ref
    hist_ref = fund.get("valuation_history") or (ref if is_hist else None)

    eps_ttm = _num(_g(fund, "per_share", "eps_ttm"))
    eps5 = _num(_g(fund, "per_share", "eps_avg_5y"))
    bvps = _num(_g(fund, "per_share", "bvps"))
    dps = _num(_g(fund, "per_share", "dps"))
    if group == "Ngành chu kỳ":
        if eps5 is not None:
            eps_basis, eps_label = eps5, "EPS bình quân 5 năm (VND/cp)"
            assumptions.append("Ngành chu kỳ: P/E áp dụng trên EPS bình quân 5 năm (hoặc số năm tối đa hiện có "
                               "nếu niêm yết < 5 năm) để làm mượt chu kỳ lợi nhuận.")
        else:
            eps_basis, eps_label = eps_ttm, "EPS TTM (VND/cp)"
            gaps.append("Ngành chu kỳ nhưng thiếu EPS bình quân 5 năm (eps_avg_5y); dùng tạm EPS TTM.")
    else:
        eps_basis, eps_label = eps_ttm, "EPS TTM (VND/cp)"

    if eps_basis is not None and eps_basis <= 0:
        gaps.append("EPS <= 0 (doanh nghiệp lỗ): loại phương pháp P/E.")
    if bvps is not None and bvps <= 0:
        gaps.append("BVPS <= 0 (âm vốn chủ): loại phương pháp P/B.")

    comps = _multiple_components(group, sector_ref, eps_basis, bvps, growth, eps_label) if sector_ref else []
    if comps:
        return comps, 1, "Cấp 1: bội số ngành"
    if sector_ref is None or not (_mult_ok(_g(sector_ref, "pe")) or _mult_ok(_g(sector_ref, "pb"))):
        gaps.append("Thiếu bội số ngành (valuation_ref theo trung vị ngành).")
    else:
        gaps.append("Có bội số ngành nhưng EPS/BVPS không dương nên không áp dụng được.")

    g = _gordon_component(dps, growth["base"], cagr)
    if g:
        assumptions.append(f"Mô hình Gordon: r = {_pct(GORDON_R)}, g cơ sở = {_pct(g['params']['base'][1])}; "
                           "bear r+1%/g-1%, bull r-1%/g+1%. Chỉ dựa vào cổ tức nên rating tối đa là 'Cần theo dõi'.")
        return [g], 2, "Cấp 2: mô hình Gordon"
    gaps.append("Không dùng được mô hình Gordon (cổ tức DPS không dương hoặc r <= g).")

    comps = _multiple_components(group, hist_ref, eps_basis, bvps, growth, eps_label) if hist_ref else []
    if comps:
        assumptions.append("Dùng trung vị P/E, P/B lịch sử 3-5 năm của chính cổ phiếu (không có bội số ngành).")
        return comps, 3, "Cấp 3: lịch sử bản thân"
    gaps.append("Thiếu bội số lịch sử 3-5 năm của cổ phiếu (valuation_ref lịch sử hoặc valuation_history).")
    return [], 4, "Cấp 4: không đủ dữ liệu"


# ------------------------------------------------------------------ kết luận
def assess(fund, tech, news, risk="balanced", as_of=None, risk_profile=None):
    risk = risk_profile or risk  # nhận cả tên tham số cũ risk_profile
    fund, tech, news = fund or {}, tech or {}, news or {}
    ticker = str(fund.get("ticker") or tech.get("ticker") or "").upper() or None
    as_of = as_of or fund.get("as_of") or tech.get("as_of") or str(date.today())
    risk = risk if risk in RISK_THRESHOLD else "balanced"
    gaps, assumptions, reasons, risks = [], [], [], []
    srcs = _merge_sources(fund, tech, news)

    # --- lỗi từ module khác -> Thiếu dữ liệu
    for name, d in (("Tài chính (fundamental)", fund), ("Kỹ thuật (technical)", tech)):
        if d.get("error"):
            gaps.append(f"{name} trả lỗi: {d['error']}")
    price = _num(_g(tech, "price", "close"))
    price_date = _g(tech, "price", "date")
    if price is None or price <= 0:
        price = None
        gaps.append("Thiếu giá đóng cửa điều chỉnh (price.close).")
    for w in tech.get("warnings") or []:
        gaps.append(f"Cảnh báo từ technical: {w}")
    if price is not None and price_date and str(price_date) != str(as_of):
        assumptions.append(f"Giá đóng cửa điều chỉnh là của phiên {price_date}, khác ngày báo cáo {as_of} "
                           "(phiên giao dịch gần nhất có dữ liệu).")
    if _g(tech, "price", "adjusted") is False:
        gaps.append("Technical báo giá CHƯA điều chỉnh (price.adjusted = false), trái quy ước giá điều chỉnh.")
    if gaps:
        return _no_data(ticker, as_of, risk, fund, price, price_date, gaps, srcs)

    if fund.get("is_sample"):
        gaps.append("CẢNH BÁO: fundamental.json là DỮ LIỆU MẪU để thử luồng, KHÔNG dùng để nộp/ra kết luận.")
    group = sector_group(fund.get("sector"))
    if not fund.get("sector"):
        gaps.append("Thiếu tên ngành (sector); xếp vào nhóm 'Khác'.")
    growth, cagr = _growth_cases(fund)
    if cagr is None:
        gaps.append("Thiếu tăng trưởng EPS 3 năm (eps_cagr_3y); kịch bản cơ sở giả định tăng trưởng 0%.")
    if group == "Bất động sản":
        assumptions.append("Hạn chế: Doanh nghiệp BĐS chưa định giá theo NAV.")

    comps, level, level_label = valuate(fund, tech, growth, cagr, group, gaps, assumptions)
    if not comps:
        gaps.append("Không tính được kịch bản định giá nào.")
        return _no_data(ticker, as_of, risk, fund, price, price_date, gaps, srcs)

    scen = {k: sum(c["weight"] * comp_value(c, k) for c in comps) for k in CASES}
    target = sum(SCEN_W[k] * scen[k] for k in CASES)
    upside = (target - price) / price
    bear_up = (scen["bear"] - price) / price

    # --- phân hạng ban đầu theo hồ sơ rủi ro
    hi = RISK_THRESHOLD[risk]
    idx = 0 if upside >= hi else 1 if upside >= 0 else 2
    base_rating = RATINGS[idx]

    # --- quy tắc hạ bậc: chỉ hạ, tối đa 1 bậc
    triggers = []
    if bear_up < BEAR_DROP_LIMIT:
        triggers.append(f"Kịch bản bear thấp hơn giá hiện tại {_pct(-bear_up)} (> 25%).")
    if _g(tech, "trend") == "down":
        triggers.append("Xu hướng kỹ thuật đang giảm (trend = down).")
    rsi = _num(_g(tech, "rsi14"))
    if rsi is not None and rsi > RSI_HOT:
        triggers.append(f"RSI14 = {rsi:.0f} > 75: vùng quá mua, rủi ro điều chỉnh ngắn hạn.")
    flags = news.get("risk_flags") or [] if not news.get("error") else []
    high = [f for f in flags if f.get("severity") == "high"]
    for f in high:
        triggers.append(f"Rủi ro mức cao: {f.get('text')} ({f.get('source')}, {f.get('date')})")
    final_idx = min(idx + 1, 2) if triggers else idx
    # chặn MUA ảo: chỉ dựa vào Gordon thì tối đa "Cần theo dõi"
    if level == 2:
        final_idx = max(final_idx, 1)
    rating = RATINGS[final_idx]

    # --- độ tin cậy
    n_years = len(fund.get("financials_table") or [])
    sec_keys = [("profitability", "roe"), ("profitability", "roa"), ("profitability", "net_margin"),
                ("growth", "revenue_yoy"), ("growth", "net_income_yoy"), ("growth", "eps_cagr_3y")]
    if group not in ("Ngân hàng", "Chứng khoán", "Bảo hiểm"):
        sec_keys.append(("profitability", "gross_margin"))
    missing_sec = [".".join(k) for k in sec_keys if _num(_g(fund, *k)) is None]
    conf = "Cao"
    if level == 2 or len(missing_sec) > 2:
        conf = "Thấp"
    if level == 3:
        conf = _worse_conf(conf, "Trung bình")
    if len(triggers) == 1:
        conf = _worse_conf(conf, "Trung bình")
    elif len(triggers) >= 2:
        conf = _worse_conf(conf, "Thấp")
    if n_years < 5:
        conf = _worse_conf(conf, "Trung bình")
        gaps.append(f"BCTC chỉ có {n_years} năm trong financials_table (cần 5 năm cho độ tin cậy cao).")
    if level == 1 and len(comps) == 1:
        conf = _worse_conf(conf, "Trung bình")
    if missing_sec:
        gaps.append("Thiếu chỉ tiêu phụ: " + ", ".join(missing_sec) + ".")
    if upside > ABSURD_HI or upside < ABSURD_LO:
        assumptions.append(f"CẢNH BÁO kết quả bất thường: upside {_pct(upside)} nằm ngoài khoảng "
                           f"[{_pct(ABSURD_LO)}, +{_pct(ABSURD_HI)}]; cần kiểm tra lại dữ liệu đầu vào. "
                           "Độ tin cậy đã hạ 1 mức.")
        conf = _drop_conf(conf)

    # --- diễn giải
    reasons.append(f"Giá mục tiêu {_vnd(target)} (25% bear + 50% base + 25% bull) so với giá điều chỉnh "
                   f"{_vnd(price)}: upside {_pct(upside)}. Phương pháp: {level_label}.")
    reasons.append(f"Hồ sơ {risk}: 'Có cơ hội' khi upside >= {_pct(hi)}; 'Cần theo dõi' khi 0% <= upside < {_pct(hi)}; "
                   "'Chưa hấp dẫn' khi upside < 0%.")
    for c in comps:
        if c["kind"] == "PE" and c["basis_value"] > 0:
            reasons.append(f"P/E hiện tại {price / c['basis_value']:.1f}x so với trung vị tham chiếu "
                           f"{c['multiples']['median']:.1f}x (trọng số {c['weight']:.0%}).")
        if c["kind"] == "PB":
            reasons.append(f"P/B hiện tại {price / c['basis_value']:.2f}x so với trung vị tham chiếu "
                           f"{c['multiples']['median']:.2f}x (trọng số {c['weight']:.0%}).")
    roe, ni = _num(_g(fund, "profitability", "roe")), _num(_g(fund, "growth", "net_income_yoy"))
    if roe is not None:
        reasons.append(f"ROE (TTM) {_pct(roe)}.")
    if ni is not None:
        reasons.append(f"Lợi nhuận sau thuế tăng trưởng {_pct(ni)} so với cùng kỳ.")
    if _g(tech, "trend"):
        t = {"up": "tăng", "down": "giảm", "sideways": "đi ngang"}.get(tech["trend"], tech["trend"])
        reasons.append(f"Xu hướng giá {t}; hỗ trợ {_g(tech, 'support')}, kháng cự {_g(tech, 'resistance')}.")
    if triggers:
        if final_idx != idx:
            reasons.append(f"Hạ 1 bậc từ '{base_rating}' xuống '{rating}' do quản trị rủi ro (tối đa 1 bậc).")
        else:
            reasons.append(f"Có cảnh báo rủi ro nhưng rating '{rating}' đã ở mức thấp nhất của thang cơ hội.")
    if level == 2 and base_rating == "Có cơ hội":
        reasons.append("Chặn 'Có cơ hội' vì định giá chỉ dựa trên mô hình Gordon; rating tối đa 'Cần theo dõi'.")
    for d in (news.get("drivers") or [])[:3] if not news.get("error") else []:
        reasons.append(f"Động lực: {d.get('text')}")
    risks += triggers
    for f in flags:
        if f.get("severity") != "high":
            risks.append(f"[{f.get('severity')}] {f.get('text')} ({f.get('source')}, {f.get('date')})")
    if bear_up >= BEAR_DROP_LIMIT and bear_up < 0:
        risks.append(f"Kịch bản bear thấp hơn giá hiện tại {_pct(-bear_up)}.")
    if news.get("error") or not news:
        gaps.append("Thiếu dữ liệu tin tức/rủi ro doanh nghiệp (company_news); chưa kiểm tra được cờ rủi ro 'high'.")

    assumptions += [
        "Giá đóng cửa là giá điều chỉnh; EPS và ROE tính theo TTM (trừ P/E ngành chu kỳ dùng EPS bình quân 5 năm).",
        f"Nhóm định giá: {group}; trọng số P/B/P/E = {SECTOR_WEIGHTS[group][0]:.0%}/{SECTOR_WEIGHTS[group][1]:.0%} "
        "(tự dồn 100% vào phương pháp còn lại nếu một chỉ số âm/lỗi).",
        f"Tăng trưởng EPS 1 năm: bear {_pct(growth['bear'])}, base {_pct(growth['base'])}, bull {_pct(growth['bull'])} "
        "(cơ sở lấy từ eps_cagr_3y, chặn trong [-10%, +15%]; bear/bull = cơ sở -/+ 10 điểm %). P/B áp dụng trên BVPS hiện tại.",
        "Bear/Base/Bull dùng bội số P25/Median/P75; xác suất 25/50/25.",
    ]

    return {
        "ticker": ticker, "as_of": as_of, "unit": "VND/cổ phiếu", "risk_profile": risk,
        "sector": fund.get("sector"), "sector_group": group,
        "price": {"close": price, "date": price_date, "adjusted": True},
        "rating": rating, "rating_before_adjustment": base_rating, "confidence": conf,
        "target_price": round(target), "upside": round(upside, 4),
        "scenarios": {k: {"price": round(scen[k]), "upside": round((scen[k] - price) / price, 4),
                          "probability": SCEN_W[k], "eps_growth": growth[k]} for k in CASES},
        "valuation_level": level_label, "methods": comps,
        "sensitivity": _sensitivity(comps, price),
        "downgrade_triggers": triggers,
        "reasons": reasons, "risks": risks, "data_gaps": gaps, "assumptions": assumptions,
        "disclaimer": DISCLAIMER, "sources": srcs,
    }


def _sensitivity(comps, price):
    out = []
    for shock in (-0.10, 0.0, 0.10):
        for mc in CASES:
            v = sum(c["weight"] * comp_value(c, "base", shock, mc) for c in comps)
            out.append({"basis_change": shock, "multiple_case": CASE_KEY[mc], "target_price": round(v),
                        "upside": round((v - price) / price, 4)})
    return out


def _no_data(ticker, as_of, risk, fund, price, price_date, gaps, srcs):
    return {
        "ticker": ticker, "as_of": as_of, "unit": "VND/cổ phiếu", "risk_profile": risk,
        "sector": (fund or {}).get("sector"), "sector_group": None,
        "price": {"close": price, "date": price_date, "adjusted": True},
        "rating": NO_DATA, "rating_before_adjustment": None, "confidence": None,
        "target_price": None, "upside": None,
        "scenarios": {k: {"price": None, "upside": None, "probability": SCEN_W[k], "eps_growth": None} for k in CASES},
        "valuation_level": "Cấp 4: không đủ dữ liệu", "methods": [], "sensitivity": [], "downgrade_triggers": [],
        "reasons": ["Không đủ dữ liệu để đánh giá cơ hội đầu tư: " + "; ".join(gaps)],
        "risks": [], "data_gaps": gaps, "assumptions": [], "disclaimer": DISCLAIMER, "sources": srcs,
    }


# ------------------------------------------------------------------ chạy toàn luồng
def _load(modname, ticker, as_of):
    """Gọi run() của module đồng đội; nếu chưa có thì đọc file JSON cùng tên (thư mục DATA_DIR)."""
    try:
        mod = __import__(modname)
        import_err = None
    except Exception as e:  # ImportError, SyntaxError... của file đồng đội -> thử đọc JSON thay thế
        mod, import_err = None, e
    if mod is None:
        dirs = [os.environ.get("DATA_DIR"), ".", "outputs", "output", "data"]
        tried = []
        for d in [x for x in dirs if x]:
            path = os.path.join(d, f"{modname}.json")
            tried.append(path)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("ticker") and str(data["ticker"]).upper() != ticker:
                    return {"ticker": ticker, "error": f"{path} là của mã {data['ticker']}, không phải {ticker}"}
                return data
        why = f" (import {modname}.py lỗi: {import_err})" if not isinstance(import_err, ModuleNotFoundError) else ""
        return {"ticker": ticker,
                "error": f"Không tìm thấy module {modname} hoặc file JSON (đã tìm: {', '.join(tried)}){why}"}
    try:
        return mod.run(ticker, as_of)
    except Exception as e:  # module đồng đội lỗi -> coi như thiếu dữ liệu, không crash
        return {"ticker": ticker, "error": f"{modname}.run lỗi: {e}"}


def run(ticker, as_of=None, risk="balanced"):
    ticker = str(ticker or "").strip().upper()
    as_of = as_of or str(date.today())
    if not re.fullmatch(r"[A-Z0-9]{3}", ticker):
        result = {"ticker": ticker or None, "error": "Mã cổ phiếu không hợp lệ (cần 3 ký tự, ví dụ HPG, VCB)."}
    else:
        try:
            result = assess(_load("fundamental", ticker, as_of), _load("technical", ticker, as_of),
                            _load("company_news", ticker, as_of), risk=risk, as_of=as_of)
        except Exception as e:  # không để crash
            result = {"ticker": ticker, "error": f"valuation lỗi: {e}"}
    os.makedirs("outputs", exist_ok=True)
    for path in (OUTPUT_FILE, os.path.join("outputs", OUTPUT_FILE)):  # ghi cả thư mục gốc và outputs/
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    out = run(sys.argv[1] if len(sys.argv) > 1 else "HPG",
              sys.argv[2] if len(sys.argv) > 2 else None,
              sys.argv[3] if len(sys.argv) > 3 else "balanced")
    print(json.dumps(out, ensure_ascii=False, indent=2))