"""
technical.py
============
Module phân tích kỹ thuật và tạo biểu đồ cho cổ phiếu Việt Nam.
Nguồn dữ liệu: vnstock (VCI/KBS).
Tính toán các chỉ báo: RSI14, MA20, MA50, MA200, Returns (1M, 3M, 1Y), Support/Resistance, Trend.
Tạo biểu đồ kỹ thuật chuyên nghiệp lưu vào outputs/charts/{ticker}_price.png.
"""

from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

# Thiết lập UTF-8 encoding an toàn trên Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # Chế độ headless cho server / background
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "outputs" / "technical.json"


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Tính Relative Strength Index (RSI) theo công thức Wilder chuẩn."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return rsi


def generate_chart(df: pd.DataFrame, ticker: str, chart_path: Path) -> None:
    """Vẽ biểu đồ giá, các đường trung bình MA và khối lượng theo phong cách tài chính cao cấp (Light High-Contrast)."""
    if isinstance(chart_path, (str, Path)):
        Path(chart_path).parent.mkdir(parents=True, exist_ok=True)
    
    plt.style.use("default")
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(10, 5.5), gridspec_kw={"height_ratios": [3.2, 1]}, sharex=True, dpi=130
    )
    fig.patch.set_facecolor("#FFFFFF")
    ax_price.set_facecolor("#FAFAFA")
    ax_vol.set_facecolor("#FAFAFA")
    
    plot_df = df.tail(120).copy()
    plot_df["date_dt"] = pd.to_datetime(plot_df["time"])
    dates = plot_df["date_dt"]
    
    # Vẽ giá đóng cửa và đường MA
    ax_price.plot(dates, plot_df["close"], label="Giá đóng cửa", color="#0284C7", linewidth=2.0)
    if "ma20" in plot_df.columns and plot_df["ma20"].notna().any():
        ax_price.plot(dates, plot_df["ma20"], label="MA20", color="#F59E0B", linewidth=1.3, alpha=0.95)
    if "ma50" in plot_df.columns and plot_df["ma50"].notna().any():
        ax_price.plot(dates, plot_df["ma50"], label="MA50", color="#10B981", linewidth=1.3, alpha=0.95)
    if "ma200" in plot_df.columns and plot_df["ma200"].notna().any():
        ax_price.plot(dates, plot_df["ma200"], label="MA200", color="#8B5CF6", linewidth=1.3, alpha=0.85)
        
    latest_close = plot_df["close"].iloc[-1]
    latest_date = dates.iloc[-1]
    ax_price.scatter([latest_date], [latest_close], color="#0284C7", s=40, zorder=5)
    ax_price.annotate(
        f"{latest_close:,.0f} đ",
        xy=(latest_date, latest_close),
        xytext=(10, 0),
        textcoords="offset points",
        color="#FFFFFF",
        fontsize=9,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#0284C7", edgecolor="none", alpha=0.9)
    )
    
    ax_price.set_title(f"{ticker} — Biểu đồ Kỹ thuật & Khối lượng", fontsize=13, fontweight="bold", color="#0F172A", pad=12)
    ax_price.grid(True, linestyle="--", alpha=0.35, color="#CBD5E1")
    ax_price.legend(loc="upper left", framealpha=0.85, facecolor="#FFFFFF", edgecolor="#E2E8F0", fontsize=8.5)
    ax_price.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x:,.0f}"))
    ax_price.tick_params(colors="#475569")
    
    # Vẽ khối lượng giao dịch
    colors_vol = ["#10B981" if c >= o else "#EF4444" for c, o in zip(plot_df["close"], plot_df["open"])]
    ax_vol.bar(dates, plot_df["volume"], color=colors_vol, width=0.8, alpha=0.85)
    ax_vol.grid(True, linestyle="--", alpha=0.35, color="#CBD5E1")
    ax_vol.set_ylabel("KL", fontsize=8, color="#475569")
    ax_vol.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x/1e6:.1f}M" if x >= 1e6 else f"{x/1e3:.0f}K"))
    ax_vol.tick_params(colors="#475569")
    
    ax_vol.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m/%y"))
    fig.autofmt_xdate(rotation=0, ha="center")
    
    plt.tight_layout()
    fig.savefig(chart_path, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)


def run(ticker: str, as_of: Optional[str] = None, output_dir: str = "outputs") -> Dict[str, Any]:
    """
    Thực thi phân tích kỹ thuật cho mã cổ phiếu `ticker` tính đến ngày `as_of`.
    Lưu kết quả vào {output_dir}/technical.json và {output_dir}/charts/{ticker}_price.png.
    """
    normalized = str(ticker or "").strip().upper()
    as_of_str = as_of or datetime.now().strftime("%Y-%m-%d")
    
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "technical.json"
    chart_dir = out_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_file = chart_dir / f"{normalized}_price.png"
    chart_rel_path = str(chart_file.resolve())

    def _persist(data: dict) -> dict:
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # Cũng ghi file vào thư mục gốc nếu output_dir != "."
            if output_dir != ".":
                with open(BASE_DIR / "technical.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"Lỗi ghi technical.json: {e}")
        return data

    if not normalized or len(normalized) != 3:
        return _persist({
            "ticker": normalized,
            "as_of": as_of_str,
            "error": f"Mã cổ phiếu '{normalized}' không hợp lệ (cần 3 ký tự)."
        })

    # Tính khoảng thời gian lấy lịch sử (khoảng 730 ngày / 2 năm để đủ tính MA200, return_1y và cho phép kéo lùi biểu đồ sâu)
    try:
        as_of_date = date.fromisoformat(as_of_str)
    except ValueError:
        as_of_date = date.today()
        as_of_str = as_of_date.isoformat()
        
    start_date = (as_of_date - timedelta(days=730)).isoformat()

    # Thu thập dữ liệu từ vnstock
    df = None
    fetch_err = None
    for src in ["vci", "kbs", "dnse"]:
        try:
            from vnstock import Quote
            raw_df = Quote(symbol=normalized, source=src).history(start=start_date, end=as_of_str)
            if raw_df is not None and not raw_df.empty and "close" in raw_df.columns:
                df = raw_df.copy()
                break
        except Exception as e:
            fetch_err = str(e)
            continue

    # Fallback nếu không kết nối được hoặc hết quota: thử nạp từ file JSON mẫu có sẵn
    if df is None or df.empty:
        cached_paths = [out_json, BASE_DIR / "outputs" / "technical.json", BASE_DIR / "technical.json"]
        for cp in cached_paths:
            if cp.exists():
                try:
                    with open(cp, encoding="utf-8") as f:
                        cached = json.load(f)
                    if cached.get("ticker") == normalized:
                        if cached.get("as_of") != as_of_str:
                            continue
                        if cached.get("price_history"):
                            cached_df = pd.DataFrame(cached["price_history"]).rename(columns={"date": "time"})
                            generate_chart(cached_df, normalized, chart_file)
                            cached["chart_path"] = chart_rel_path
                        return _persist(cached)
                except Exception:
                    pass
        return _persist({
            "ticker": normalized,
            "as_of": as_of_str,
            "error": f"Không tải được dữ liệu giá cho '{normalized}': {fetch_err or 'Không có dữ liệu'}"
        })

    # Chuẩn hóa cột
    df = df.sort_values("time").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["open"] = pd.to_numeric(df.get("open", df["close"]), errors="coerce")
    df["high"] = pd.to_numeric(df.get("high", df["close"]), errors="coerce")
    df["low"] = pd.to_numeric(df.get("low", df["close"]), errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)

    # Đổi đơn vị sang VND nếu vnstock trả theo nghìn VND (< 1000)
    if df["close"].dropna().median() < 1000:
        df["close"] = df["close"] * 1000
        df["open"] = df["open"] * 1000
        df["high"] = df["high"] * 1000
        df["low"] = df["low"] * 1000

    # Lọc các phiên <= as_of_str
    df["date_str"] = df["time"].astype(str).str.slice(0, 10)
    df = df[df["date_str"] <= as_of_str]
    if df.empty:
        return _persist({
            "ticker": normalized,
            "as_of": as_of_str,
            "error": f"Không có phiên giao dịch nào trước hoặc bằng {as_of_str}."
        })

    # Tính toán các chỉ số
    df["ma20"] = df["close"].rolling(20, min_periods=5).mean()
    df["ma50"] = df["close"].rolling(50, min_periods=10).mean()
    df["ma200"] = df["close"].rolling(200, min_periods=30).mean()
    df["rsi14"] = _calc_rsi(df["close"], 14)

    latest = df.iloc[-1]
    close_price = round(float(latest["close"]))
    price_date = str(latest["date_str"])
    
    rsi14 = round(float(latest["rsi14"]), 1) if pd.notna(latest["rsi14"]) else None
    ma20 = round(float(latest["ma20"])) if pd.notna(latest["ma20"]) else close_price
    ma50 = round(float(latest["ma50"])) if pd.notna(latest["ma50"]) else None
    ma200 = round(float(latest["ma200"])) if pd.notna(latest["ma200"]) else None

    # Lợi suất quá khứ
    n = len(df)
    ret_1m = round(float((close_price - df["close"].iloc[-min(22, n)]) / df["close"].iloc[-min(22, n)]), 4) if n >= 5 else 0.0
    ret_3m = round(float((close_price - df["close"].iloc[-min(66, n)]) / df["close"].iloc[-min(66, n)]), 4) if n >= 20 else 0.0
    ret_1y = round(float((close_price - df["close"].iloc[0]) / df["close"].iloc[0]), 4) if n >= 50 else 0.0

    # Khối lượng trung bình 20 phiên
    vol_20 = int(df["volume"].tail(20).mean()) if n >= 5 else int(latest["volume"])

    # Ngưỡng hỗ trợ & kháng cự (20 phiên gần nhất)
    recent_20 = df.tail(20)
    support = round(float(recent_20["low"].min()))
    resistance = round(float(recent_20["high"].max()))

    # Xác định xu hướng (trend)
    if ma50 is not None:
        if close_price > ma20 and ma20 > ma50:
            trend = "up"
        elif close_price < ma20 and ma20 < ma50:
            trend = "down"
        else:
            trend = "sideways"
    else:
        trend = "up" if close_price >= ma20 else "down"

    # Vẽ biểu đồ kỹ thuật và lưu
    try:
        generate_chart(df, normalized, chart_file)
    except Exception as e:
        log.warning(f"Lỗi tạo biểu đồ kỹ thuật: {e}")

    # Lưu toàn bộ lịch sử giá giao dịch để vẽ biểu đồ tương tác trên web (cho phép kéo lùi/phóng to thu nhỏ)
    p_hist = []
    for _, row in df.iterrows():
        p_hist.append({
            "date": str(row["date_str"]),
            "open": round(float(row["open"])),
            "high": round(float(row["high"])),
            "low": round(float(row["low"])),
            "close": round(float(row["close"])),
            "volume": int(row["volume"]),
            "ma20": round(float(row["ma20"])) if pd.notna(row["ma20"]) else None,
            "ma50": round(float(row["ma50"])) if pd.notna(row["ma50"]) else None,
            "ma200": round(float(row["ma200"])) if pd.notna(row["ma200"]) else None,
            "rsi14": round(float(row["rsi14"]), 1) if pd.notna(row["rsi14"]) else None,
        })


    result = {
        "ticker": normalized,
        "as_of": as_of_str,
        "price": {
            "close": close_price,
            "date": price_date,
            "adjusted": True
        },
        "trend": trend,
        "rsi14": rsi14,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "return_1m": ret_1m,
        "return_3m": ret_3m,
        "return_1y": ret_1y,
        "avg_volume_20d": vol_20,
        "support": support,
        "resistance": resistance,
        "chart_path": chart_rel_path,
        "price_history": p_hist,
        "sources": [
            {
                "name": "vnstock (Lịch sử giá)",
                "url": "https://vnstocks.com",
                "date": price_date
            }
        ]
    }

    return _persist(result)


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else "HPG"
    d = sys.argv[2] if len(sys.argv) > 2 else "2026-10-09"
    res = run(t, d)
    print(json.dumps(res, ensure_ascii=False, indent=2))
