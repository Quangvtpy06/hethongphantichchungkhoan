"""Biểu đồ nến, khối lượng và RSI dùng chung trục thời gian."""
import math
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from technical import _calc_rsi

CHART_CONFIG = {
    'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False,
    'responsive': True, 'doubleClick': 'reset',
    'modeBarButtonsToRemove': ['select2d', 'lasso2d', 'zoom2d', 'autoScale2d'],
}


def create_chart(tech, theme):
    history = tech.get('price_history') or []
    df = pd.DataFrame(history)
    required = {'date', 'open', 'high', 'low', 'close'}
    if not required.issubset(df.columns):
        fig = theme(go.Figure(), height=620)
        fig.add_annotation(text='Chưa đủ dữ liệu giá mở/cao/thấp/đóng cửa để vẽ nến.', showarrow=False)
        return fig
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    for key in ('open', 'high', 'low', 'close', 'volume', 'ma20', 'ma50', 'ma200', 'rsi14'):
        df[key] = pd.to_numeric(df.get(key, pd.Series(index=df.index, dtype=float)), errors='coerce')
    df = df.dropna(subset=list(required)).sort_values('date').drop_duplicates('date', keep='last')
    df = df[(df['low'] > 0) & (df['high'] >= df[['open', 'close', 'low']].max(axis=1)) &
            (df['low'] <= df[['open', 'close']].min(axis=1))]
    if df.empty:
        return create_chart({}, theme)
    # JSON cũ chưa có RSI từng phiên: tính từ toàn bộ lịch sử, cùng hàm với bảng chỉ số.
    df['rsi14'] = df['rsi14'].combine_first(_calc_rsi(df['close']))
    dates = df['date'].dt.strftime('%Y-%m-%d').tolist()
    def values(key):
        return [float(v) if pd.notna(v) and math.isfinite(v) else None for v in df[key]]

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.045,
                        row_heights=[.60, .16, .24])
    fig.add_trace(go.Candlestick(
        x=dates, open=values('open'), high=values('high'), low=values('low'), close=values('close'),
        name='Giá OHLC', increasing=dict(line=dict(color='#087F8C', width=1), fillcolor='white'),
        decreasing=dict(line=dict(color='#D14343', width=1), fillcolor='#D14343'),
        whiskerwidth=.3,
        text=[f"{d}<br>Mở: {o:,.0f}<br>Cao: {h:,.0f}<br>Thấp: {l:,.0f}<br>Đóng: {c:,.0f} đồng"
              for d,o,h,l,c in zip(dates, df.open, df.high, df.low, df.close)], hoverinfo='text'), row=1, col=1)
    for key, color in [('ma20', '#D68A00'), ('ma50', '#2563EB'), ('ma200', '#8055C8')]:
        if df[key].notna().any():
            fig.add_trace(go.Scatter(x=dates, y=values(key), name=key.upper(), mode='lines',
                                    line=dict(color=color, width=1.3),
                                    hovertemplate='%{y:,.0f} đồng<extra>%{fullData.name}</extra>'), row=1, col=1)
    levels = []
    for key, label, color, tint in [
        ('support', 'Hỗ trợ', '#087F8C', 'rgba(8,127,140,.12)'),
        ('resistance', 'Kháng cự', '#BE4141', 'rgba(190,65,65,.12)')]:
        value = tech.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            levels.append(value)
            # Dải màu chỉ nhấn mạnh mốc có sẵn; không tự đặt biên giá vùng mới.
            fig.add_hline(y=value, line=dict(color=tint, width=14), layer='below', row=1, col=1)
            fig.add_hline(y=value, line=dict(color=color, width=1, dash='dot'), row=1, col=1,
                          annotation_text=f'{label} {value:,.0f}', annotation_position='top left',
                          annotation_font=dict(color=color, size=11), annotation_bgcolor='rgba(255,255,255,.85)')
    fig.add_trace(go.Bar(x=dates, y=values('volume'), name='Khối lượng',
                        marker_color=['#087F8C' if c >= o else '#D14343' for c,o in zip(df.close, df.open)],
                        opacity=.55, hovertemplate='%{y:,.0f} cp<extra>Khối lượng</extra>'), row=2, col=1)
    fig.add_trace(go.Scatter(x=dates, y=values('rsi14'), mode='lines', name='RSI 14',
                            line=dict(color='#8055C8', width=1.6),
                            hovertemplate='%{y:.1f}<extra>RSI 14</extra>'), row=3, col=1)
    fig.add_hrect(y0=30, y1=70, fillcolor='rgba(128,85,200,.05)', line_width=0, row=3, col=1)
    for level in (30, 70):
        fig.add_hline(y=level, line=dict(color='#94A3B8', width=1, dash='dash'), row=3, col=1)
    fig = theme(fig, height=700)
    start = max(0, len(df)-80)
    view = df.iloc[start:]
    bounds = view[['low', 'high', 'ma20', 'ma50', 'ma200']].stack().dropna().tolist() + levels
    lower, upper = min(bounds), max(bounds)
    pad = max((upper-lower)*.08, upper*.005)
    right = df.date.iloc[-1] + pd.Timedelta(days=2)
    left = df.date.iloc[start] - pd.Timedelta(days=1)
    missing = pd.date_range(df.date.iloc[0], df.date.iloc[-1], freq='D').difference(df.date)
    fig.update_layout(
        dragmode='pan', hovermode='x', spikedistance=-1,
        uirevision=f"{tech.get('ticker')}:{tech.get('as_of')}:{dates[-1]}",
        margin=dict(l=48, r=65, t=65, b=35),
        legend=dict(x=0, xanchor='left', y=1.02, yanchor='bottom', font=dict(size=11)),
        meta=dict(priceLevels=levels, initialRange=[left.isoformat(), right.isoformat()]),
    )
    fig.update_xaxes(type='date', range=[left, right], rangeslider_visible=False,
                     rangebreaks=[dict(values=missing.strftime('%Y-%m-%d').tolist())],
                     minallowed=df.date.iloc[0]-pd.Timedelta(days=1), maxallowed=right,
                     showspikes=True, spikemode='across', spikesnap='cursor', spikethickness=1,
                     spikecolor='#94A3B8', tickformat='%d/%m/%y', gridcolor='#F1F5F9')
    fig.update_yaxes(fixedrange=True, side='right', zeroline=False, gridcolor='#EEF2F6')
    fig.update_yaxes(range=[lower-pad, upper+pad], title_text='VND', tickformat=',.0f', row=1, col=1)
    fig.update_yaxes(title_text='KL', tickformat='.2s', rangemode='tozero', row=2, col=1)
    fig.update_yaxes(range=[0,100], tickvals=[0,30,50,70,100], title_text='RSI', row=3, col=1)
    return fig
