"""
app.py
======
Giao diện Hệ thống Phân tích & Định giá Cổ phiếu Việt Nam (VibeStock Lens)
Phát triển trên nền tảng Plotly Dash theo chuẩn UI/UX Pro Max (Executive Light High-Contrast Theme).

Thiết kế tối ưu:
- Nền sáng chuyên nghiệp, độ tương phản cao, tối ưu hiển thị máy chiếu hội thảo
- Không còn bất kỳ ô bảng nền đen nào (Bug 5 fixed: White/Gray striped cells với chữ đen đậm)
- Khung nhập liệu màu xám sáng với chữ đen đậm rõ nét (Bug 2 fixed)
- Chữ cảnh báo và thông tin PDF tương phản cao, đậm nét, dễ đọc (Bug 3 fixed)
- Toàn bộ chữ, tiêu đề, nhãn trục, số liệu trên biểu đồ Plotly đều là màu đen đậm rõ nét (Bug 4 fixed)
- Toàn bộ chữ trên tab bấm luôn luôn hiển thị rõ ràng, không bị mờ hay ẩn khi chưa hover (Bug 1 fixed)
- Kiến trúc chuẩn Plotly Dash, loại bỏ hoàn toàn Streamlit (Bug 6 fixed)
"""

from __future__ import annotations

import os
import sys
import json
import base64
from technical_chart import create_chart, CHART_CONFIG
from report_support import reference_source, date_label
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Optional, List

# Thiết lập UTF-8 encoding an toàn trên Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

# Thiết lập đường dẫn thư mục gốc
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import re
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import dash
from dash import html, dcc, Input, Output, State, ctx
import dash_bootstrap_components as dbc

# Import backend pipeline
import pipeline
import technical

# Khởi tạo Dash App với Bootstrap Theme & Custom CSS
app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
        "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap"
    ],
    title="VibeStock Lens | Phân Tích & Định Giá Cổ Phiếu",
    suppress_callback_exceptions=True
)

server = app.server

# ══════════════════════════════ TIỆN ÍCH ĐỊNH DẠNG & THEME BIỂU ĐỒ ══════════════════════════════

def format_vnd(val: Any) -> str:
    """Định dạng tiền tệ VND với dấu phân cách nghìn."""
    if val is None or not isinstance(val, (int, float)) or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{val:,.0f} ₫".replace(",", ".")

def format_pct(val: Any, decimals: int = 2) -> str:
    """Định dạng tỷ lệ phần trăm kèm dấu +/-."""
    if val is None or not isinstance(val, (int, float)) or (isinstance(val, float) and pd.isna(val)):
        return "—"
    prefix = "+" if val > 0 else ""
    return f"{prefix}{val * 100:.{decimals}f}%"

def get_rating_badge_element(rating: Optional[str]) -> html.Span:
    """Tạo badge màu tương phản cao cho xếp hạng đầu tư."""
    r = str(rating or "").upper()
    if any(k in r for k in ["MUA", "CÓ CƠ HỘI", "TÍCH LŨY"]):
        return html.Span(f"▲ {rating}", className="badge-rating-buy")
    elif any(k in r for k in ["NẮM GIỮ", "CẦN THEO DÕI"]):
        return html.Span(f"■ {rating}", className="badge-rating-hold")
    elif any(k in r for k in ["BÁN", "GIẢM TỶ TRỌNG", "CHƯA HẤP DẪN"]):
        return html.Span(f"▼ {rating}", className="badge-rating-sell")
    else:
        return html.Span(f"● {rating or 'Chưa đánh giá'}", className="badge-rating-nodata")

def render_executive_table(df: pd.DataFrame, align_right_cols: list[str] = None) -> html.Div:
    """Render bảng dữ liệu HTML sạch đẹp: nền trắng xám, chữ đen đậm (Fix Bug 5)."""
    if df.empty:
        return html.Div("Không có dữ liệu hiển thị", className="alert-custom-info")

    right_set = set(align_right_cols or [])
    headers = [
        html.Th(col, className="table-num-right" if col in right_set else "")
        for col in df.columns
    ]

    rows = []
    for _, r in df.iterrows():
        tds = []
        for col in df.columns:
            val_str = str(r[col])
            is_right = col in right_set
            tds.append(html.Td(val_str, className="table-num-right" if is_right else ""))
        rows.append(html.Tr(tds))

    return html.Div(
        html.Table([
            html.Thead(html.Tr(headers)),
            html.Tbody(rows)
        ], className="executive-table"),
        className="table-container-card"
    )

def apply_chart_high_contrast(fig: go.Figure, title_text: str = "", height: int = 420) -> go.Figure:
    """Áp dụng theme tương phản cao, chữ đen đậm rõ nét cho biểu đồ Plotly (Fix Bug 4)."""
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=60, r=25, t=45, b=45),
        font=dict(family="IBM Plex Sans, -apple-system, sans-serif", size=12, color="#0F172A"),
        title=dict(
            text=f"<b>{title_text}</b>" if title_text else "",
            font=dict(family="IBM Plex Sans, -apple-system, sans-serif", size=14, color="#0F172A")
        ) if title_text else None,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(family="IBM Plex Sans, sans-serif", size=12, color="#0F172A")
        ),
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        hoverlabel=dict(
            bgcolor="#0F172A",
            font=dict(color="#FFFFFF", size=12, family="IBM Plex Sans, sans-serif")
        )
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="#E2E8F0",
        tickfont=dict(color="#0F172A", size=11, family="IBM Plex Sans, sans-serif"),
        title_font=dict(color="#0F172A", size=13, family="IBM Plex Sans, sans-serif"),
        linecolor="#CBD5E1",
        zerolinecolor="#CBD5E1"
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#E2E8F0",
        tickfont=dict(color="#0F172A", size=11, family="IBM Plex Sans, sans-serif"),
        title_font=dict(color="#0F172A", size=13, family="IBM Plex Sans, sans-serif"),
        linecolor="#CBD5E1",
        zerolinecolor="#CBD5E1"
    )
    return fig

# ══════════════════════════════ TẠO BIỂU ĐỒ PLOTLY TƯƠNG TÁC ══════════════════════════════

def create_technical_plotly_chart(tech: dict) -> go.Figure:
    return create_chart(tech, apply_chart_high_contrast)


def create_financial_bar_chart(fin_table: list[dict]) -> go.Figure:
    """Tạo biểu đồ Doanh thu & Lợi nhuận đa niên sắp xếp theo thứ tự thời gian chuẩn (2021 đến 2026 TTM)."""
    if not fin_table:
        fig = go.Figure()
        fig.add_annotation(
            text="Chưa có dữ liệu báo cáo tài chính",
            showarrow=False,
            font=dict(size=14, color="#0F172A", family="IBM Plex Sans")
        )
        return apply_chart_high_contrast(fig, "Xu hướng Doanh thu & Lợi nhuận", height=340)

    # Sắp xếp năm theo thứ tự thời gian tăng dần: 2021 -> 2022 -> ... -> 2025 -> 2026 (TTM)
    def _year_sort_val(y_val):
        s = str(y_val)
        m = re.match(r"^(\d+)", s)
        num = int(m.group(1)) if m else 0
        has_ttm = 1 if "TTM" in s else 0
        return (num, has_ttm)

    sorted_fin = sorted(fin_table, key=lambda r: _year_sort_val(r.get("year", "")))
    df_fin = pd.DataFrame(sorted_fin)
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=[str(y) for y in df_fin["year"]],
        y=df_fin["revenue"] / 1e9,
        name="Doanh thu thuần (tỷ ₫)",
        marker_color="#2563EB",
        text=[f"{v:,.0f}B" if pd.notna(v) else "—" for v in (df_fin["revenue"] / 1e9)],
        textposition="auto",
        textfont=dict(color="#0F172A", size=11, family="IBM Plex Sans")
    ))

    fig.add_trace(go.Bar(
        x=[str(y) for y in df_fin["year"]],
        y=df_fin["net_profit"] / 1e9,
        name="Lợi nhuận sau thuế (tỷ ₫)",
        marker_color="#059669",
        text=[f"{v:,.0f}B" if pd.notna(v) else "—" for v in (df_fin["net_profit"] / 1e9)],
        textposition="auto",
        textfont=dict(color="#0F172A", size=11, family="IBM Plex Sans")
    ))

    fig = apply_chart_high_contrast(
        fig,
        title_text="Xu Hướng Doanh Thu Thuần & Lợi Nhuận Sau Thuế Qua Các Năm (2021 - 2026 TTM)",
        height=340
    )
    fig.update_layout(barmode="group")
    fig.update_yaxes(title_text="Giá trị (Tỷ VND)", tickformat=",.0f")
    return fig

# ══════════════════════════════ NẠP DỮ LIỆU BAN ĐẦU ══════════════════════════════

def load_initial_cache(default_ticker: str = "FPT") -> Optional[dict]:
    """Tự động nạp dữ liệu phân tích gần nhất nếu có sẵn để hiển thị ngay."""
    p_cache = BASE_DIR / "reports" / f"pipeline_{default_ticker}_2026-10-09.json"
    if p_cache.exists():
        try:
            with open(p_cache, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Thử quét file reports gần nhất
    reports_dir = BASE_DIR / "reports"
    if reports_dir.exists():
        manifests = sorted(reports_dir.glob(f"pipeline_{default_ticker}_*.json"), reverse=True)
        if manifests:
            try:
                with open(manifests[0], encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return None

INITIAL_DATA = load_initial_cache("FPT")

# ══════════════════════════════ LAYOUT CHÍNH ══════════════════════════════

app.layout = dbc.Container([
    # Store lưu trữ dữ liệu phân tích & Download component
    dcc.Store(id="analysis-data-store", data=INITIAL_DATA),
    dcc.Download(id="download-pdf-action"),

    dbc.Row([
        # ── SIDEBAR ĐIỀU KHIỂN (BUG 2 FIX) ──
        dbc.Col([
            html.Div([
                html.Div([
                    html.H2("⚙️ BẢNG ĐIỀU KHIỂN", className="sidebar-title"),
                    html.P("Cấu hình tham số & Vận hành luồng", className="sidebar-subtitle")
                ], style={"borderBottom": "1.5px solid #CBD5E1", "paddingBottom": "14px", "marginBottom": "20px"}),

                # 1. Ô nhập mã cổ phiếu
                html.Div([
                    html.Label("Mã cổ phiếu (Ticker):", className="input-label"),
                    dcc.Input(
                        id="input-ticker",
                        type="text",
                        value="FPT",
                        maxLength=6,
                        className="form-control",
                        placeholder="VD: FPT, HPG..."
                    )
                ], style={"marginBottom": "16px"}),

                # 2. Ô chọn ngày báo cáo
                html.Div([
                    html.Label("Ngày chốt báo cáo (as_of):", className="input-label"),
                    dcc.Input(
                        id="input-as-of",
                        type="date",
                        value=date.today().strftime("%Y-%m-%d"),
                        className="form-control"
                    )
                ], style={"marginBottom": "16px"}),

                # 3. Chọn hồ sơ rủi ro
                html.Div([
                    html.Label("Hồ sơ rủi ro nhà đầu tư:", className="input-label"),
                    dcc.Dropdown(
                        id="input-risk",
                        options=[
                            {"label": "Cân bằng (Balanced)", "value": "balanced"},
                            {"label": "Thận trọng (Conservative)", "value": "conservative"},
                            {"label": "Chấp nhận rủi ro (Aggressive)", "value": "aggressive"}
                        ],
                        value="balanced",
                        clearable=False
                    )
                ], style={"marginBottom": "24px"}),

                # 4. Nút bấm kích hoạt phân tích
                html.Button(
                    "🚀 BẮT ĐẦU PHÂN TÍCH",
                    id="btn-analyze",
                    n_clicks=0,
                    className="btn-analyze-action"
                ),

                # Thông báo trạng thái phân tích
                dcc.Loading(
                    id="loading-spinner",
                    type="circle",
                    children=html.Div(id="status-notification", style={"marginTop": "14px"})
                ),

                # 5. Nút tải PDF nhanh từ sidebar (nếu đã có báo cáo)
                html.Div(id="sidebar-download-container", style={"marginTop": "16px"}),

                # Chân trang thông tin hệ thống
                html.Div([
                    html.B("Hệ thống VibeStock v2.0 (Plotly Dash)", style={"color": "#1E3A8A"}),
                    html.Br(),
                    "Chuẩn hóa: Technical • Fundamental • Valuation • PDF"
                ], style={
                    "marginTop": "40px",
                    "padding": "12px",
                    "background": "#F8FAFC",
                    "border": "1px solid #CBD5E1",
                    "borderRadius": "8px",
                    "fontSize": "11px",
                    "color": "#475569"
                })
            ], className="sidebar-container")
        ], width=12, md=3, lg=3, style={"padding": "0"}),

        # ── KHU VỰC NỘI DUNG CHÍNH ──
        dbc.Col([
            html.Div([
                # 1. BRAND HEADER (HERO BAR)
                html.Div([
                    html.Div([
                        html.Div([
                            html.H1([
                                html.Span("📈", style={"fontSize": "26px"}),
                                " VIBESTOCK LENS • EQUITY INTELLIGENCE"
                            ], className="brand-main-title"),
                            html.P(
                                "Hệ thống phân tích cơ hội đầu tư, định giá đa phương pháp & tự động phát hành Báo cáo PDF",
                                className="brand-description"
                            )
                        ]),
                        html.Div([
                            html.Span("MÃ ĐANG THEO DÕI", style={"fontSize": "12px", "fontWeight": "700", "color": "#64748B", "display": "block"}),
                            html.Div(id="brand-ticker-display", className="active-ticker-display", children="FPT")
                        ], style={"textAlign": "right"})
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "flexWrap": "wrap", "gap": "14px"})
                ], className="brand-header-card"),

                # 2. HÀNG 4 THẺ KPI CHỦ LỰC
                html.Div(id="kpi-cards-container", style={"marginBottom": "20px"}),

                # 3. BẢNG 5 TAB CHI TIẾT (BUG 1 FIX)
                dbc.Tabs([
                    dbc.Tab(label="📊 Định Giá & Khuyến Nghị", tab_id="tab-val", children=[
                        html.Div(id="tab-valuation-content", style={"paddingTop": "14px"})
                    ]),
                    dbc.Tab(label="📈 Phân Tích Kỹ Thuật", tab_id="tab-tech", children=[
                        html.Div(id="tab-technical-content", style={"paddingTop": "14px"})
                    ]),
                    dbc.Tab(label="📑 Phân Tích Cơ Bản (BCTC)", tab_id="tab-fund", children=[
                        html.Div(id="tab-fundamental-content", style={"paddingTop": "14px"})
                    ]),
                    dbc.Tab(label="📰 Tin Tức & Rủi Ro", tab_id="tab-news", children=[
                        html.Div(id="tab-news-content", style={"paddingTop": "14px"})
                    ]),
                    dbc.Tab(label="📄 Báo Cáo PDF", tab_id="tab-pdf", children=[
                        html.Div(id="tab-pdf-content", style={"paddingTop": "14px"})
                    ])
                ], id="main-tabs", active_tab="tab-val", className="nav-tabs")

            ], style={"padding": "24px 20px"})
        ], width=12, md=9, lg=9)
    ], className="g-0")
], fluid=True, style={"backgroundColor": "#F8FAFC", "minHeight": "100vh", "padding": "0"})


# ══════════════════════════════ CALLBACKS TƯƠNG TÁC ══════════════════════════════

# Callback 1: Thực thi pipeline khi bấm nút Phân tích
@app.callback(
    Output("analysis-data-store", "data"),
    Output("status-notification", "children"),
    Input("btn-analyze", "n_clicks"),
    State("input-ticker", "value"),
    State("input-as-of", "value"),
    State("input-risk", "value"),
    prevent_initial_call=True
)
def run_analysis_pipeline(n_clicks: int, ticker_in: str, as_of_in: str, risk_in: str):
    if not n_clicks:
        return dash.no_update, dash.no_update

    t_clean = (ticker_in or "").strip().upper()
    if not t_clean or len(t_clean) != 3:
        err_msg = html.Div(
            "⚠️ Vui lòng nhập đúng mã cổ phiếu gồm 3 ký tự (Ví dụ: FPT, HPG, VCB).",
            className="alert-custom-warning"
        )
        return dash.no_update, err_msg

    try:
        result = pipeline.run_pipeline(
            ticker=t_clean,
            as_of=as_of_in or date.today().strftime("%Y-%m-%d"),
            risk_profile=risk_in or "balanced",
            output_dir="outputs",
            reports_dir="reports"
        )

        if result.get("status") in ["success", "partial"]:
            success_msg = html.Div(
                f"✅ Hoàn tất phân tích {t_clean} và xuất bản PDF thành công!",
                className="alert-custom-success"
            )
            return result, success_msg
        else:
            err_msg = html.Div(
                f"❌ Quá trình phân tích {t_clean} gặp sự cố: {result.get('errors')}",
                className="alert-custom-danger"
            )
            return dash.no_update, err_msg

    except Exception as exc:
        err_msg = html.Div(
            f"❌ Lỗi ngoại lệ trong pipeline: {str(exc)}",
            className="alert-custom-danger"
        )
        return dash.no_update, err_msg


# Callback 2: Cập nhật giao diện toàn diện theo dữ liệu Store
@app.callback(
    Output("brand-ticker-display", "children"),
    Output("kpi-cards-container", "children"),
    Output("tab-valuation-content", "children"),
    Output("tab-technical-content", "children"),
    Output("tab-fundamental-content", "children"),
    Output("tab-news-content", "children"),
    Output("tab-pdf-content", "children"),
    Output("sidebar-download-container", "children"),
    Input("analysis-data-store", "data")
)
def update_dashboard_ui(res: Optional[dict]):
    if not res:
        empty_msg = html.Div(
            "👈 Hãy chọn mã cổ phiếu và nhấn nút '🚀 BẮT ĐẦU PHÂN TÍCH' để khởi chạy dữ liệu!",
            className="alert-custom-info"
        )
        return "CHƯA CHỌN", empty_msg, empty_msg, empty_msg, empty_msg, empty_msg, empty_msg, None

    ticker = res.get("ticker", "—")
    data = res.get("data", {})
    tech = data.get("technical", {})
    fund = data.get("fundamental", {})
    news = data.get("company_news", {})
    assess = data.get("assessment", {})
    pdf_info = data.get("report_pdf", {})
    pdf_path_str = res.get("pdf_path")

    # 1. RENDER 4 THẺ KPI CHỦ LỰC
    price_val = tech.get("price", {}).get("close")
    ret_1m_val = tech.get("return_1m")
    ret_sub_str = f"1 tháng: {format_pct(ret_1m_val)}" if ret_1m_val is not None else ""
    ret_sub_color = "#15803D" if (ret_1m_val or 0) >= 0 else "#DC2626"

    rating_val = assess.get("rating", "THIẾU DỮ LIỆU")
    conf_val = assess.get("confidence", "—")

    target_val = assess.get("target_price")
    upside_val = assess.get("upside")
    up_color = "#15803D" if (upside_val or 0) >= 0 else "#DC2626"

    trend_val = tech.get("trend", "—")
    trend_map = {"up": ("TĂNG TRƯỞNG", "#15803D"), "down": ("GIẢM ĐIỂM", "#DC2626"), "sideways": ("ĐI NGANG", "#B45309")}
    t_label, t_color = trend_map.get(trend_val, (trend_val, "#64748B"))
    rsi_val = tech.get("rsi14", "—")

    kpi_cards = dbc.Row([
        dbc.Col([
            html.Div([
                html.Div("GIÁ THAM CHIẾU (ĐÃ ĐIỀU CHỈNH)", className="kpi-label"),
                html.Div(format_vnd(price_val), className="kpi-value"),
                html.Div(ret_sub_str, className="kpi-subtitle", style={"color": ret_sub_color})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3),

        dbc.Col([
            html.Div([
                html.Div("ĐÁNH GIÁ ĐẦU TƯ", className="kpi-label"),
                html.Div(get_rating_badge_element(rating_val), style={"margin": "4px 0 6px 0"}),
                html.Div([
                    "Độ tin cậy: ",
                    html.B(conf_val, style={"color": "#0F172A"})
                ], className="kpi-subtitle", style={"color": "#64748B"})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3),

        dbc.Col([
            html.Div([
                html.Div("GIÁ MỤC TIÊU & UPSIDE", className="kpi-label"),
                html.Div(format_vnd(target_val), className="kpi-value"),
                html.Div([
                    "Chênh lệch: ",
                    html.B(format_pct(upside_val), style={"color": up_color})
                ], className="kpi-subtitle", style={"color": up_color})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3),

        dbc.Col([
            html.Div([
                html.Div("XU HƯỚNG & RSI (14)", className="kpi-label"),
                html.Div(t_label, className="kpi-value", style={"color": t_color}),
                html.Div([
                    f"RSI: {rsi_val} | MA20: {format_vnd(tech.get('ma20'))}"
                ], className="kpi-subtitle", style={"color": "#64748B"})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3)
    ], className="g-3")

    # 2. RENDER TAB 1: ĐỊNH GIÁ & KHUYẾN NGHỊ
    scens = assess.get("scenarios") or {}
    scen_cards_row = []
    if scens:
        scen_info = [
            ("Thận trọng (Bear)", scens.get("bear", {}), "#DC2626"),
            ("Cơ sở (Base)", scens.get("base", {}), "#1D4ED8"),
            ("Tích cực (Bull)", scens.get("bull", {}), "#15803D")
        ]
        scen_cols = []
        for name, s_data, color in scen_info:
            scen_cols.append(dbc.Col([
                html.Div([
                    html.B(name, style={"color": color, "fontSize": "14px", "fontWeight": "800"}),
                    html.Div(format_vnd(s_data.get("price")), style={"fontSize": "22px", "fontWeight": "800", "color": "#0F172A", "margin": "6px 0"}),
                    html.Div(["Upside: ", html.B(format_pct(s_data.get("upside")), style={"color": color})], style={"fontSize": "13px", "color": "#475569"}),
                    html.Div(["Xác suất: ", html.B(format_pct(s_data.get("probability")))], style={"fontSize": "12px", "color": "#64748B"}),
                    html.Div(["Tăng trưởng EPS: ", html.B(format_pct(s_data.get("eps_growth")))], style={"fontSize": "12px", "color": "#64748B"})
                ], className="scenario-card", style={"borderTop": f"3.5px solid {color}"})
            ], width=12, md=4))
        scen_cards_row = dbc.Row(scen_cols, className="g-3")

    methods = assess.get("methods") or []
    methods_table_comp = None
    if methods:
        bear_prob_pct = f"{(scens.get('bear', {}).get('probability') or 0.25) * 100:.0f}%"
        base_prob_pct = f"{(scens.get('base', {}).get('probability') or 0.50) * 100:.0f}%"
        bull_prob_pct = f"{(scens.get('bull', {}).get('probability') or 0.25) * 100:.0f}%"
        bear_col = f"Giá trị Bear ({bear_prob_pct})"
        base_col = f"Giá trị Base ({base_prob_pct})"
        bull_col = f"Giá trị Bull ({bull_prob_pct})"
        m_rows = []
        for m in methods:
            m_rows.append({
                "Phương pháp": m.get("name"),
                "Trọng số áp dụng": format_pct(m.get("weight")),
                "Cơ sở tính toán": m.get("basis_label"),
                "Giá trị cơ sở": format_vnd(m.get("basis_value")),
                bear_col: format_vnd((m.get("values") or {}).get("bear")),
                base_col: format_vnd((m.get("values") or {}).get("base")),
                bull_col: format_vnd((m.get("values") or {}).get("bull")),
            })
        m_df = pd.DataFrame(m_rows)
        methods_table_comp = render_executive_table(
            m_df,
            align_right_cols=["Trọng số áp dụng", "Giá trị cơ sở", bear_col, base_col, bull_col]
        )

    reasons = assess.get("reasons") or ["Chưa có luận điểm ghi nhận."]
    triggers = assess.get("downgrade_triggers") or []
    gaps = assess.get("data_gaps") or []

    tab_val_content = html.Div([
        html.H3("🎯 Kịch Bản Định Giá (Scenarios Matrix)", className="section-headline"),
        scen_cards_row or html.Div("Chưa có dữ liệu kịch bản", className="alert-custom-info"),

        html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
        html.H3("📐 Phương Pháp Áp Dụng & Phân Bổ Trọng Số", className="section-headline"),
        methods_table_comp or html.Div("Chưa có bảng phương pháp", className="alert-custom-info"),

        html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
        dbc.Row([
            dbc.Col([
                html.H3("💡 Luận Điểm Đánh Giá Cốt Lõi", className="section-headline"),
                html.Ul([
                    html.Li(r, style={"color": "#0F172A", "marginBottom": "6px", "fontWeight": "600"})
                    for r in reasons
                ])
            ], width=12, md=6),
            dbc.Col([
                html.H3("⚠️ Yếu Tố Hạ Bậc & Giả Định Rủi Ro", className="section-headline"),
                html.Div([
                    html.Div([
                        html.B("Cảnh báo hạ bậc đã kích hoạt:", style={"color": "#78350F", "display": "block", "marginBottom": "4px"}),
                        html.Ul([html.Li(t, style={"color": "#78350F", "fontWeight": "600"}) for t in triggers])
                    ], className="alert-custom-warning") if triggers else None,

                    html.Div([
                        html.B("Dữ liệu còn thiếu / Giả định:", style={"color": "#1E3A8A", "display": "block", "marginBottom": "4px"}),
                        html.Ul([html.Li(g, style={"color": "#1E3A8A", "fontWeight": "600"}) for g in gaps])
                    ], className="alert-custom-info") if gaps else None,

                    html.Div("✅ Dữ liệu phân tích đầy đủ, không phát hiện rủi ro hạ bậc nghiêm trọng.", className="alert-custom-success")
                    if not triggers and not gaps else None
                ])
            ], width=12, md=6)
        ])
    ])

    # 3. RENDER TAB 2: PHÂN TÍCH KỸ THUẬT
    fig_tech = create_technical_plotly_chart(tech)

    tech_df = pd.DataFrame([
        {"Chỉ số kỹ thuật": "Giá đóng cửa gần nhất", "Giá trị": format_vnd(tech.get("price", {}).get("close"))},
        {"Chỉ số kỹ thuật": "Đường trung bình MA20", "Giá trị": format_vnd(tech.get("ma20"))},
        {"Chỉ số kỹ thuật": "Đường trung bình MA50", "Giá trị": format_vnd(tech.get("ma50"))},
        {"Chỉ số kỹ thuật": "Đường trung bình MA200", "Giá trị": format_vnd(tech.get("ma200"))},
        {"Chỉ số kỹ thuật": "Chỉ số RSI (14 phiên)", "Giá trị": str(tech.get("rsi14", "—"))},
        {"Chỉ số kỹ thuật": "Vùng Hỗ trợ kỹ thuật", "Giá trị": format_vnd(tech.get("support"))},
        {"Chỉ số kỹ thuật": "Vùng Kháng cự kỹ thuật", "Giá trị": format_vnd(tech.get("resistance"))},
        {"Chỉ số kỹ thuật": "Khối lượng TB 20 phiên", "Giá trị": f"{tech.get('avg_volume_20d', 0):,.0f} cp"},
        {"Chỉ số kỹ thuật": "Lợi suất 1 tháng", "Giá trị": format_pct(tech.get("return_1m"))},
        {"Chỉ số kỹ thuật": "Lợi suất 3 tháng", "Giá trị": format_pct(tech.get("return_3m"))},
        {"Chỉ số kỹ thuật": "Lợi suất 1 năm", "Giá trị": format_pct(tech.get("return_1y"))},
        {"Chỉ số kỹ thuật": "Xu hướng trung hạn", "Giá trị": str(tech.get("trend", "—")).upper()}
    ])
    tech_table_comp = render_executive_table(tech_df, align_right_cols=["Giá trị"])

    # Preview ảnh tĩnh ReportLab nếu có
    chart_p = tech.get("chart_path")
    chart_abs = BASE_DIR / chart_p if chart_p else None
    chart_img_element = None
    if chart_abs and chart_abs.exists():
        try:
            with open(chart_abs, "rb") as f_img:
                b64_chart = base64.b64encode(f_img.read()).decode("utf-8")
                chart_img_element = html.Details([
                    html.Summary("🖼️ Xem Biểu Đồ Kỹ Thuật Xuất Bản (PNG Chuẩn Báo Cáo)", style={"cursor": "pointer", "fontWeight": "700", "color": "#1D4ED8", "marginTop": "14px"}),
                    html.Img(src=f"data:image/png;base64,{b64_chart}", style={"width": "100%", "borderRadius": "8px", "marginTop": "8px", "border": "1px solid #CBD5E1"})
                ])
        except Exception:
            pass

    tab_tech_content = html.Div([
        html.H3("Biểu Đồ Nến & Chỉ Báo Kỹ Thuật", className="section-headline"),
        html.Div([
            html.Button("← Trước", id="chart-previous", type="button"),
            html.Button("Sau →", id="chart-next", type="button"),
            html.Button("+ Phóng to", id="chart-zoom-in", type="button"),
            html.Button("− Thu nhỏ", id="chart-zoom-out", type="button"),
            html.Button("Đặt lại", id="chart-reset", type="button"),
        ], className="technical-chart-controls", role="group", **{"aria-label": "Điều khiển biểu đồ giá"}),
        html.P("Giữ chuột và kéo để xem lịch sử · Lăn chuột để zoom · Nhấp đúp để đặt lại. Nến rỗng: tăng hoặc không đổi; nến đặc: giảm.", className="technical-chart-help"),
        dcc.Graph(id="technical-chart", figure=fig_tech, config=CHART_CONFIG),
        html.P("Dải hỗ trợ/kháng cự nhấn mạnh các mốc hiện tại trong bảng bên dưới, không phải mốc lịch sử của từng phiên. RSI 14: trên 70 là vùng quá mua, dưới 30 là vùng quá bán; không tự xác nhận điểm mua/bán.", className="technical-chart-help"),
        chart_img_element or html.Div(),

        html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
        html.H3("🔢 Bảng Chỉ Số Kỹ Thuật Chi Tiết", className="section-headline"),
        tech_table_comp
    ])

    # 4. RENDER TAB 3: PHÂN TÍCH CƠ BẢN (BCTC)
    per_share = fund.get("per_share") or {}
    prof = fund.get("profitability") or {}
    growth = fund.get("growth") or {}
    margins = fund.get("margins") or {}
    lev = fund.get("leverage") or {}
    fin_table = fund.get("financials_table") or []

    fund_kpi_row = dbc.Row([
        dbc.Col([
            html.Div([
                html.Div("EPS (TTM) & BVPS", className="kpi-label"),
                html.Div(format_vnd(per_share.get("eps_ttm") or per_share.get("eps")), className="kpi-value"),
                html.Div([f"BVPS: {format_vnd(per_share.get('bvps'))}"], className="kpi-subtitle", style={"color": "#64748B"})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3),

        dbc.Col([
            html.Div([
                html.Div("SINH LỜI (ROE / ROA)", className="kpi-label"),
                html.Div(format_pct(prof.get("roe")), className="kpi-value", style={"color": "#15803D"}),
                html.Div([f"ROA: {format_pct(prof.get('roa'))}"], className="kpi-subtitle", style={"color": "#64748B"})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3),

        dbc.Col([
            html.Div([
                html.Div("BIÊN LỢI NHUẬN RÒNG", className="kpi-label"),
                html.Div(format_pct(prof.get("net_margin") or margins.get("net_margin")), className="kpi-value", style={"color": "#1D4ED8"}),
                html.Div([f"Biên gộp: {format_pct(prof.get('gross_margin') or margins.get('gross_margin'))}"], className="kpi-subtitle", style={"color": "#64748B"})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3),

        dbc.Col([
            html.Div([
                html.Div("CỔ TỨC TIỀN MẶT (DPS)", className="kpi-label"),
                html.Div(format_vnd(per_share.get("dps") or per_share.get("dividend_per_share")), className="kpi-value", style={"color": "#B45309"}),
                html.Div([f"Nợ/VCSH: {lev.get('debt_to_equity', '—')}"], className="kpi-subtitle", style={"color": "#64748B"})
            ], className="kpi-metric-card")
        ], width=12, sm=6, md=3)
    ], className="g-3")

    # Bảng BCTC đa niên
    fin_table_comp = None
    if fin_table:
        tbl_rows = []
        for row in fin_table:
            tbl_rows.append({
                "Năm": str(row.get("year")),
                "Doanh thu (tỷ ₫)": f"{row.get('revenue', 0) / 1e9:,.0f}" if row.get("revenue") else "—",
                "Lợi nhuận gộp (tỷ ₫)": f"{row.get('gross_profit', 0) / 1e9:,.0f}" if row.get("gross_profit") else "—",
                "LNST (tỷ ₫)": f"{row.get('net_profit', 0) / 1e9:,.0f}" if row.get("net_profit") else "—",
                "Tổng tài sản (tỷ ₫)": f"{row.get('total_assets', 0) / 1e9:,.0f}" if row.get("total_assets") else "—",
                "Vốn CSH (tỷ ₫)": f"{row.get('equity', 0) / 1e9:,.0f}" if row.get("equity") else "—",
                "EPS (₫)": f"{row.get('eps', 0):,.0f}" if row.get("eps") else "—",
                "BVPS (₫)": f"{row.get('bvps', 0):,.0f}" if row.get("bvps") else "—",
                "ROE": format_pct(row.get("roe")),
                "ROA": format_pct(row.get("roa")),
                "Biên ròng": format_pct(row.get("net_margin")),
                "Nợ/VCSH": f"{row.get('debt_to_equity', 0):.2f}" if row.get("debt_to_equity") is not None else "—"
            })
        fin_df = pd.DataFrame(tbl_rows)
        fin_table_comp = render_executive_table(
            fin_df,
            align_right_cols=[c for c in fin_df.columns if c != "Năm"]
        )

    fig_fin = create_financial_bar_chart(fin_table)

    # Tăng trưởng & Bội số ngành
    g_df = pd.DataFrame([
        {"Chỉ tiêu tăng trưởng": "Tăng trưởng Doanh thu (YoY)", "Tỷ lệ": format_pct(growth.get("revenue_yoy") or growth.get("revenue_growth_yoy"))},
        {"Chỉ tiêu tăng trưởng": "Tăng trưởng LNST (YoY)", "Tỷ lệ": format_pct(growth.get("net_income_yoy") or growth.get("net_income_growth_yoy"))},
        {"Chỉ tiêu tăng trưởng": "Tăng trưởng Doanh thu (Quý gần nhất)", "Tỷ lệ": format_pct(growth.get("revenue_qoq_or_recent") or growth.get("revenue_growth_qoq"))},
        {"Chỉ tiêu tăng trưởng": "Tăng trưởng LNST (Quý gần nhất)", "Tỷ lệ": format_pct(growth.get("net_income_qoq_or_recent"))},
        {"Chỉ tiêu tăng trưởng": "Tăng trưởng EPS 3 năm (CAGR)", "Tỷ lệ": format_pct(growth.get("eps_cagr_3y"))},
    ])
    g_table_comp = render_executive_table(g_df, align_right_cols=["Tỷ lệ"])

    ref = fund.get("valuation_ref") or {}
    pe_ref = ref.get("pe") or {}
    pb_ref = ref.get("pb") or {}
    ref_df = pd.DataFrame([
        {"Bội số tham chiếu": "P/E Ngành", "Phân vị 25%": pe_ref.get("p25", "—"), "Trung vị": pe_ref.get("median", "—"), "Phân vị 75%": pe_ref.get("p75", "—")},
        {"Bội số tham chiếu": "P/B Ngành", "Phân vị 25%": pb_ref.get("p25", "—"), "Trung vị": pb_ref.get("median", "—"), "Phân vị 75%": pb_ref.get("p75", "—")}
    ])
    ref_table_comp = render_executive_table(ref_df, align_right_cols=["Phân vị 25%", "Trung vị", "Phân vị 75%"])

    # Bảng chỉ số chuyên biệt ngành Ngân hàng (nếu có)
    banking_metrics = fund.get("banking")
    banking_card_comp = None
    if banking_metrics and isinstance(banking_metrics, dict):
        b_df = pd.DataFrame([
            {"Chỉ số Ngân hàng": "Tỷ lệ thu nhập lãi thuần (NIM)", "Giá trị": format_pct(banking_metrics.get("nim"))},
            {"Chỉ số Ngân hàng": "Tỷ lệ chi phí trên thu nhập (CIR)", "Giá trị": format_pct(banking_metrics.get("cir"))},
            {"Chỉ số Ngân hàng": "Tỷ lệ nợ xấu (NPL)", "Giá trị": format_pct(banking_metrics.get("npl"))},
            {"Chỉ số Ngân hàng": "Tỷ lệ dư nợ / tiền gửi (LDR)", "Giá trị": format_pct(banking_metrics.get("ldr"))},
            {"Chỉ số Ngân hàng": "Hệ số an toàn vốn (CAR)", "Giá trị": format_pct(banking_metrics.get("car"))},
            {"Chỉ số Ngân hàng": "Tỷ lệ tiền gửi không kỳ hạn (CASA)", "Giá trị": format_pct(banking_metrics.get("casa"))},
        ])
        banking_card_comp = render_executive_table(b_df, align_right_cols=["Giá trị"])

    tab_fund_content = html.Div([
        html.H3(f"🏢 Chỉ Số Tài Chính Cốt Lõi — {fund.get('sector', 'Toàn ngành')}", className="section-headline"),
        fund_kpi_row,

        html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
        html.H3("📑 Báo Cáo Tài Chính Đa Niên (2021 - 2026 TTM)", className="section-headline"),
        fin_table_comp or html.Div("Chưa có bảng BCTC", className="alert-custom-info"),

        html.Div([
            html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
            html.H3("🏦 Chỉ Số Chuyên Biệt Ngành Ngân Hàng (NIM, CIR, NPL, CAR, CASA)", className="section-headline"),
            banking_card_comp
        ]) if banking_card_comp else None,

        html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
        html.H3("📊 Biểu Đồ Xu Hướng Doanh Thu Thuần & Lợi Nhuận Sau Thuế", className="section-headline"),
        dcc.Graph(figure=fig_fin, config={"displayModeBar": True, "responsive": True}),

        html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
        dbc.Row([
            dbc.Col([
                html.H3("📈 Tăng Trưởng Doanh Thu & Lợi Nhuận", className="section-headline"),
                g_table_comp
            ], width=12, md=6),
            dbc.Col([
                html.H3("📊 Hệ Số Định Giá Tham Chiếu", className="section-headline"),
                html.P(reference_source(ref)),
                html.P(ref.get("note") or "Cần xác minh nguồn trước khi sử dụng các hệ số để so sánh ngành."),
                ref_table_comp
            ], width=12, md=6)
        ])
    ])

    # 5. RENDER TAB 4: TIN TỨC & RỦI RO
    summary_text = news.get("business_summary")
    flags = news.get("risk_flags") or []
    news_items = news.get("news") or []
    rev_mix = news.get("revenue_mix") or {}
    drivers = news.get("drivers") or []

    flag_elements = []
    if flags:
        for fl in flags:
            sev = fl.get("severity", "medium")
            c_class = "alert-custom-danger" if sev == "high" else "alert-custom-warning"
            flag_elements.append(html.Div([
                html.B(f"Mức độ {sev.upper()}: "),
                html.Span(fl.get("text"), style={"fontWeight": "600"}),
                html.Br(),
                html.Small(f"Nguồn: {fl.get('source')} | Ngày: {fl.get('date')}", style={"color": "#475569", "fontWeight": "500"})
            ], className=c_class))
    else:
        flag_elements.append(html.Div(
            "✅ Không ghi nhận cảnh báo rủi ro nghiêm trọng từ tin tức và sự kiện gần đây.",
            className="alert-custom-success"
        ))

    news_list_elements = []
    if news_items:
        for it in news_items[:8]:
            n_title = it.get("title") or it.get("text")
            n_url = it.get("url")
            n_date = date_label(it.get("date"), "Không rõ ngày xuất bản")
            n_src = it.get("source", "Tin tức")

            link_comp = html.A(
                n_title,
                href=n_url,
                target="_blank",
                style={"color": "#1D4ED8", "textDecoration": "none", "fontWeight": "700"}
            ) if n_url else html.Span(n_title, style={"fontWeight": "700", "color": "#0F172A"})

            news_list_elements.append(html.Div([
                html.Span("• ", style={"color": "#1D4ED8", "fontWeight": "800"}),
                link_comp,
                html.Br(),
                html.Small(f"{n_src} • {n_date}", style={"color": "#64748B", "fontWeight": "600"})
            ], style={"padding": "10px 0", "borderBottom": "1px solid #E2E8F0"}))
    else:
        news_list_elements.append(html.Div("Không có tin tức mới ghi nhận.", className="alert-custom-info"))

    # Cơ cấu doanh thu & Động lực kinh doanh
    rev_badges = []
    if isinstance(rev_mix, dict):
        for seg_k, seg_v in rev_mix.items():
            rev_badges.append(html.Span(f"{seg_k}: {seg_v}", className="badge-scenario-base", style={"marginRight": "8px", "marginBottom": "6px", "display": "inline-block"}))

    driver_items = []
    if isinstance(drivers, list):
        for dr in drivers[:4]:
            d_t = (dr.get("text") or dr.get("summary")) if isinstance(dr, dict) else str(dr)
            driver_items.append(html.Li(d_t, style={"color": "#0F172A", "fontWeight": "600", "marginBottom": "4px"}))

    tab_news_content = html.Div([
        html.Div([
            html.B("🏢 Hồ Sơ Doanh Nghiệp Tóm Tắt:", style={"color": "#1E3A8A", "fontSize": "15px"}),
            html.Br(),
            html.Span(summary_text or "Đang cập nhật hồ sơ doanh nghiệp.", style={"color": "#0F172A", "fontWeight": "600"}),
            html.Hr(style={"borderColor": "#CBD5E1", "margin": "12px 0"}) if rev_badges else None,
            html.Div([
                html.B("📊 Cơ Cấu Doanh Thu: ", style={"color": "#1E3A8A", "marginRight": "8px"}),
                html.Div(rev_badges, style={"display": "inline-block"})
            ]) if rev_badges else None,
            html.Hr(style={"borderColor": "#CBD5E1", "margin": "12px 0"}) if driver_items else None,
            html.Div([
                html.B("🚀 Động Lực Tăng Trưởng Cốt Lõi:", style={"color": "#1E3A8A", "marginBottom": "6px"}),
                html.Ul(driver_items, style={"marginBottom": "0", "paddingLeft": "20px"})
            ]) if driver_items else None,
        ], className="alert-custom-info"),

        dbc.Row([
            dbc.Col([
                html.H3("🚨 Cờ Cảnh Báo Rủi Ro (Tin Tức, Ngành & Tài Chính)", className="section-headline"),
                html.Div(flag_elements)
            ], width=12, md=6),

            dbc.Col([
                html.H3("🗞️ Tin Tức & Động Lực Doanh Nghiệp", className="section-headline"),
                html.Div(news_list_elements)
            ], width=12, md=6)
        ])
    ])

    # 6. RENDER TAB 5: BÁO CÁO PDF
    pdf_status_comp = None
    sidebar_download_comp = None

    if pdf_path_str and Path(pdf_path_str).exists():
        p_file = Path(pdf_path_str)
        size_mb = p_file.stat().st_size / (1024 * 1024)

        # Fix Bug 3: Chữ đậm nét, màu xanh navy tương phản cao trên nền sáng
        pdf_status_comp = html.Div([
            html.Div([
                html.H4("📄 Báo Cáo Phân Tích Đã Sẵn Sàng", style={"color": "#1E3A8A", "fontWeight": "800", "margin": "0 0 8px 0"}),
                html.P([
                    html.B(f"Tên file: "), html.Code(p_file.name, style={"color": "#0F172A", "fontSize": "14px"}),
                    html.Br(),
                    html.B(f"Dung lượng: "), f"{size_mb:.2f} MB",
                    html.Br(),
                    html.B(f"Thời gian tạo: "), f"{datetime.fromtimestamp(p_file.stat().st_mtime).strftime('%d/%m/%Y %H:%M:%S')}"
                ], style={"color": "#1E3A8A", "margin": "0 0 16px 0", "fontWeight": "600"}),

                html.Button(
                    f"📥 TẢI XUỐNG BÁO CÁO PDF ({p_file.name})",
                    id="btn-download-pdf-tab",
                    n_clicks=0,
                    className="btn-pdf-download",
                    style={"maxWidth": "400px"}
                )
            ], className="alert-custom-info")
        ])

        sidebar_download_comp = html.Button(
            f"📥 TẢI PDF ({p_file.name[:16]}...)",
            id="btn-download-pdf-sidebar",
            n_clicks=0,
            className="btn-pdf-download"
        )
    else:
        pdf_status_comp = html.Div("Chưa có file PDF nào được tạo cho phiên này.", className="alert-custom-warning")

    # Bảng Data Sources
    sources = pdf_info.get("sources") or []
    sources_table_comp = None
    if sources:
        src_df = pd.DataFrame([
            {"Tên nguồn dữ liệu": s.get("name"), "Địa chỉ URL": s.get("url"), "Ngày ghi nhận": s.get("date")}
            for s in sources
        ])
        sources_table_comp = render_executive_table(src_df)

    tab_pdf_content = html.Div([
        html.H3("📄 Báo Cáo Phân Tích Định Dạng PDF Chuẩn In Ấn (ReportLab A4)", className="section-headline"),
        pdf_status_comp,

        html.Hr(style={"borderColor": "#E2E8F0", "margin": "22px 0"}),
        html.H3("🔗 Nguồn Dữ Liệu Tham Chiếu Kiểm Toán (Data Sources)", className="section-headline"),
        sources_table_comp or html.Div("Chưa có danh sách nguồn", className="alert-custom-info")
        # Hoàn toàn không còn JSON pipeline manifest theo yêu cầu người dùng
    ])

    return (
        ticker,
        kpi_cards,
        tab_val_content,
        tab_tech_content,
        tab_fund_content,
        tab_news_content,
        tab_pdf_content,
        sidebar_download_comp
    )


# Callback 3: Tải file PDF khi người dùng nhấn nút Tải PDF (tại Tab 5 hoặc Sidebar)
@app.callback(
    Output("download-pdf-action", "data"),
    Input("btn-download-pdf-tab", "n_clicks"),
    Input("btn-download-pdf-sidebar", "n_clicks"),
    State("analysis-data-store", "data"),
    prevent_initial_call=True
)
def download_pdf_file(n_tab: int, n_side: int, data_store: Optional[dict]):
    if not (n_tab or n_side) or not data_store:
        return dash.no_update

    pdf_p = data_store.get("pdf_path")
    if pdf_p and Path(pdf_p).exists():
        return dcc.send_file(str(Path(pdf_p).resolve()))
    return dash.no_update


# ══════════════════════════════ KHỞI CHẠY SERVER ══════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    print("=" * 70)
    print("📈 VIBESTOCK LENS - EQUITY INTELLIGENCE DASHBOARD (PLOTLY DASH)")
    print(f"🚀 Ứng dụng đã sẵn sàng! Mở trình duyệt tại: http://127.0.0.1:{port}")
    print("=" * 70)
    app.run(host="127.0.0.1", port=port, debug=False)
