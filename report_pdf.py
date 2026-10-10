"""Bộ xuất PDF của Hân. Đọc JSON của nhóm; không tự lấy dữ liệu hoặc định giá."""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from report_support import reference_source, risk_flags, financial_unit, billion_value, financial_basis, date_label
from pypdf import PdfReader, PdfWriter, Transformation
from pathlib import Path
from xml.sax.saxutils import escape

# Thiết lập UTF-8 encoding an toàn trên môi trường Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Spacer, Table, TableStyle, PageBreak, NextPageTemplate, Image, KeepTogether)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.graphics.shapes import Drawing, Rect, String, Line

BASE = Path(__file__).resolve().parent
MODULES = ('technical', 'fundamental', 'company_news', 'assessment')
RATINGS = ('MUA', 'TÍCH LŨY', 'NẮM GIỮ', 'GIẢM TỶ TRỌNG', 'BÁN', 'KHÔNG ĐỦ DỮ LIỆU', 'Có cơ hội', 'Cần theo dõi', 'Chưa hấp dẫn', 'Thiếu dữ liệu')
RISKS = {'conservative': 'Thận trọng', 'balanced': 'Cân bằng', 'aggressive': 'Chấp nhận rủi ro cao'}
W, H = A4
M = 45
WIDTH = W-2*M
NAVY = colors.HexColor('#092C63')
BLUE = colors.HexColor('#146BDF')
INK = colors.HexColor('#233F61')
MUTED = colors.HexColor('#58708D')
PALE = colors.HexColor('#EEF6FF')
LINE = colors.HexColor('#D1E3F6')
EMPTY = 'Chưa có dữ liệu'
STYLES = {}
PERCENT_KEYS = {'confidence', 'upside', 'roe', 'roa', 'gross_margin', 'net_margin', 'revenue_yoy',
    'net_income_yoy', 'eps_cagr_3y', 'return_1m', 'return_3m', 'return_1y',
    'nim', 'npl', 'cir', 'car', 'discount_rate', 'growth_rate', 'weight'}
MONEY_KEYS = {'target_price', 'fair_value', 'price', 'eps_ttm', 'bvps', 'dps', 'eps_avg_5y'}
LABELS = {'bear':'Thận trọng', 'base':'Cơ sở', 'bull':'Tích cực', 'target_price':'Giá mục tiêu (VND/cp)',
    'upside':'Chênh lệch so với giá tham chiếu', 'method':'Phương pháp', 'methods':'Phương pháp',
    'weight':'Trọng số', 'value':'Giá trị', 'confidence':'Độ tin cậy', 'assumptions':'Giả định',
    'reason':'Lý do', 'text':'Nội dung', 'source':'Nguồn', 'date':'Ngày', 'name':'Tên',
    'discount_rate':'Tỷ lệ chiết khấu', 'growth_rate':'Tăng trưởng', 'note':'Ghi chú',
    'fair_value':'Giá trị hợp lý (VND/cp)', 'description':'Mô tả', 'parameter':'Tham số',
    'low':'Thấp', 'high':'Cao', 'median':'Trung vị', 'p25':'Phân vị 25%', 'p75':'Phân vị 75%',
    'severity':'Mức độ', 'high':'Cao', 'medium':'Trung bình', 'low':'Thấp'}


def setup_fonts():
    """Nhúng Noto Sans từ gói, không phụ thuộc font trên máy Windows."""
    for name, filename in [('Noto', 'NotoSans-Regular.ttf'), ('NotoB', 'NotoSans-Bold.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():
            font_path = BASE/'assets'/'fonts'/filename
            if not font_path.exists():
                font_path = BASE/filename
            pdfmetrics.registerFont(TTFont(name, str(font_path)))
    pdfmetrics.registerFontFamily('Noto', normal='Noto', bold='NotoB', italic='Noto', boldItalic='NotoB')
    specs = {'body':(9,14,INK), 'small':(8,12,MUTED), 'title':(15,21,NAVY),
        'h2':(10.5,15,BLUE), 'cell':(8.3,12,INK), 'cellhead':(8.2,12,NAVY)}
    for name,(size,leading,color) in specs.items():
        STYLES[name] = ParagraphStyle(name, fontName='NotoB' if name in ('title','h2','cellhead') else 'Noto',
            fontSize=size, leading=leading, textColor=color, spaceAfter=8 if name in ('title','h2','body') else 0,
            splitLongWords=True, allowWidows=0, allowOrphans=0)
    STYLES['title'].textColor = colors.white
    STYLES['title'].backColor = NAVY
    STYLES['title'].borderPadding = 8
    STYLES['title'].spaceBefore = 9
    STYLES['title'].spaceAfter = 16
    STYLES['h2'].backColor = PALE
    STYLES['h2'].borderPadding = (3,6,3,6)
    STYLES['h2'].spaceBefore = 5
    STYLES['h2'].spaceAfter = 8
    STYLES['title'].keepWithNext = True
    STYLES['h2'].keepWithNext = True
    STYLES['cellhead'].textColor=colors.white
    STYLES['metric']=ParagraphStyle('metric',fontName='NotoB',fontSize=16,leading=22,textColor=NAVY)


def technical_image(tech, data_dir):
    """Ưu tiên dựng từ lịch sử của báo cáo; hỗ trợ cả đường dẫn JSON cũ."""
    if tech.get('price_history'):
        import pandas as pd
        from technical import generate_chart
        frame = pd.DataFrame(tech['price_history']).rename(columns={'date': 'time'})
        stream = BytesIO()
        generate_chart(frame, tech.get('ticker', ''), stream)
        stream.seek(0)
        return stream
    chart = tech.get('chart_path')
    if not chart:
        return None
    path = Path(chart)
    root = Path(data_dir or BASE/'data').resolve()
    candidates = [path] if path.is_absolute() else [root/path, root.parent/path, BASE/path]
    return next((str(x) for x in candidates if x.is_file()), None)


def valuation_content(a):
    """Diễn giải cùng kết quả mô hình bằng bảng có nhãn tiếng Việt."""
    items = [p('Giá mục tiêu là giá ước tính theo các giả định bên dưới, không phải mức giá chắc chắn đạt được. Chênh lệch dương nghĩa là giá ước tính cao hơn giá đóng cửa; chênh lệch âm nghĩa là thấp hơn.')]
    items.append(table(['Kết quả', 'Giá trị'], [
        ['Giá đóng cửa dùng để so sánh (đồng/cổ phiếu)', num((a.get('price') or {}).get('close'))],
        ['Giá mục tiêu tham khảo (đồng/cổ phiếu)', num(a.get('target_price'))],
        ['Chênh lệch so với giá đóng cửa', pct(a.get('upside'))]]))
    items.append(p('Ba tình huống để tham khảo', 'h2'))
    items.append(p('Thận trọng: điều kiện kém thuận lợi. Cơ sở: theo giả định chính. Tích cực: điều kiện thuận lợi hơn. Tỷ trọng là mức đóng góp vào giá mục tiêu, không phải xác suất đã được kiểm chứng.'))
    rows = []
    for key, label in [('bear', 'Thận trọng'), ('base', 'Cơ sở'), ('bull', 'Tích cực')]:
        case = (a.get('scenarios') or {}).get(key) or {}
        rows.append([label, num(case.get('price')), pct(case.get('upside')), pct(case.get('probability'))])
    items.append(table(['Tình huống', 'Giá ước tính (đ/cp)', 'Chênh lệch giá', 'Tỷ trọng'], rows))
    items.append(p('Giá ước tính được tính như thế nào?', 'h2'))
    items.append(p('P/E so sánh giá với lợi nhuận trên mỗi cổ phiếu (EPS). P/B so sánh giá với giá trị sổ sách trên mỗi cổ phiếu (BVPS), tức phần vốn chủ sở hữu phân bổ cho một cổ phiếu. TTM nghĩa là 12 tháng gần nhất. Giá trị sổ sách không phải giá bán thanh lý tài sản.'))
    for method in a.get('methods') or []:
        kind = method.get('kind')
        label = method.get('name') or kind or 'Phương pháp khác'
        items.append(p(f"{label} - đóng góp {pct(method.get('weight'))} vào kết quả", 'h2'))
        if kind in ('PE', 'PB'):
            basis = 'Lợi nhuận trên mỗi cổ phiếu' if kind == 'PE' else 'Giá trị sổ sách trên mỗi cổ phiếu'
            formula = 'EPS × (1 + tăng trưởng giả định) × hệ số P/E.' if kind == 'PE' else 'BVPS × hệ số P/B.'
            items.append(p(f"{basis} dùng trong mô hình: {num(method.get('basis_value'))} đồng. Cách tính: {formula}"))
            items.append(table(['Mức tham chiếu', 'Thận trọng', 'Cơ sở', 'Tích cực'], [
                ['Hệ số (lần)'] + [num((method.get('multiples') or {}).get(k), 2) for k in ('p25', 'median', 'p75')],
                ['Giá ước tính (đ/cp)'] + [num((method.get('values') or {}).get(k)) for k in ('bear', 'base', 'bull')]]))
            if kind == 'PE':
                items.append(p('Tăng trưởng EPS giả định: ' + ' / '.join(pct((method.get('growth') or {}).get(k)) for k in ('bear', 'base', 'bull')) + ' (thận trọng / cơ sở / tích cực).'))
        else:
            items.append(p('Mô hình cổ tức (Gordon) ước tính giá trị từ cổ tức tương lai. Kết quả phụ thuộc mức sinh lời yêu cầu và tốc độ tăng cổ tức giả định.'))
            items.append(p('Cổ tức mỗi cổ phiếu dùng để tính: '+num(method.get('basis_value'))+' đồng.'))
            items.append(table(['Tình huống', 'Sinh lời yêu cầu', 'Tăng cổ tức', 'Giá ước tính (đ/cp)'], [
                [label, pct((method.get('params') or {}).get(k, [None, None])[0]), pct((method.get('params') or {}).get(k, [None, None])[1]), num((method.get('values') or {}).get(k))]
                for k, label in [('bear', 'Thận trọng'), ('base', 'Cơ sở'), ('bull', 'Tích cực')]]))
    if a.get('sensitivity'):
        items.append(p('Nếu đầu vào thay đổi, giá ước tính thay đổi ra sao?', 'h2'))
        items.append(p('Bảng dưới thử thay đổi dữ liệu cơ sở của mô hình và hệ số định giá; đây là phép thử, không phải dự báo giá sẽ diễn biến như vậy.'))
        names = {'p25': 'Thấp', 'median': 'Cơ sở', 'p75': 'Cao'}
        items.append(table(['Thay đổi đầu vào', 'Mức hệ số', 'Giá ước tính (đ/cp)', 'Chênh lệch giá'], [
            [pct(row.get('basis_change')), names.get(row.get('multiple_case'), str(row.get('multiple_case'))), num(row.get('target_price')), pct(row.get('upside'))]
            for row in a['sensitivity']]))
    return items


def p(text, style='body'):
    return Paragraph(escape(str(text)).replace('\n','<br/>'), STYLES[style])


def num(value, digits=0):
    if value is None:
        return EMPTY
    if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        return EMPTY
    return f'{value:,.{digits}f}'.replace(',','@').replace('.',',').replace('@','.')


def pct(value):
    return EMPTY if value is None else num(value*100,2)+'%' if isinstance(value,(int,float)) and not isinstance(value,bool) else EMPTY


def display(value, key=''):
    if value is None:
        return EMPTY
    if key.lower() == 'confidence' and isinstance(value,str):
        return value
    if key.lower() in PERCENT_KEYS:
        return pct(value)
    if isinstance(value,bool):
        return 'Có' if value else 'Không'
    if isinstance(value,(int,float)):
        return num(value,0 if key in MONEY_KEYS else 2)
    return str(value)


def bullets(items):
    if not items:
        return [p(EMPTY)]
    if isinstance(items,str):
        items=[items]
    if isinstance(items,dict):
        return details(items)
    result=[]
    for item in items:
        if isinstance(item,dict):
            text=item.get('text',item.get('reason'))
            if text is not None:
                result.append(p('• '+str(text)))
                tail=[str(item[k]) for k in ('source','date') if item.get(k) is not None]
                if tail: result.append(p(' | '.join(tail),'small'))
            else:
                result.extend(details(item))
        else:
            result.append(p('• '+display(item)))
    return result


def details(obj, level=0):
    """In cấu trúc scenarios/methods/sensitivity linh hoạt; giữ mọi giả định."""
    if obj is None:
        return [p(EMPTY)]
    if isinstance(obj,list):
        result=[]
        for item in obj:
            result.extend(details(item,level+1));result.append(Spacer(1,5))
        return result or [p(EMPTY)]
    if isinstance(obj,dict):
        result=[]
        for key,value in obj.items():
            label=LABELS.get(key,key)
            if isinstance(value,(dict,list)):
                result.append(p(label,'h2'));result.extend(details(value,level+1))
            else:
                result.append(p(f'{label}: {display(value,key)}'))
        return result or [p(EMPTY)]
    return [p(display(obj))]


def table(headers,rows,widths=None):
    widths=widths or [WIDTH/len(headers)]*len(headers)
    data=[[p(h,'cellhead') for h in headers]]
    data.extend([[p(display(v),'cell') for v in row] for row in rows])
    t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=1)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F3F8FD')]),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LINEBELOW',(0,0),(-1,0),.6,LINE),('LINEBELOW',(0,1),(-1,-1),.3,LINE)]))
    return t


def callout(title,body):
    # Single cell table can split long paragraphs over pages.
    t=Table([[p(title,'h2')],[p(body)]],colWidths=[WIDTH],splitByRow=1,splitInRow=1)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('LEFTPADDING',(0,0),(-1,-1),10),
        ('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,0),8),
        ('BOTTOMPADDING',(0,-1),(-1,-1),8)]))
    return t


def metric_strip(items):
    cells=[]
    for label,value in items:
        style='metric' if len(str(value))<24 else 'body'
        cells.append([p(label,'small'),p(value,style)])
    t=Table([cells],colWidths=[WIDTH/len(items)]*len(items),hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),
        ('LINEBEFORE',(1,0),(-1,0),.5,LINE)]))
    return t


def financial_charts(rows,unit):
    """Biểu đồ vector; chỉ vẽ điểm thật, không dùng 0 thay dữ liệu thiếu."""
    if not isinstance(rows,list):return []
    diagrams=[]
    for keys,title in [(('revenue','doanh_thu'),'Doanh thu'),(('net_income','net_profit','lnst','LNST'),'Lợi nhuận sau thuế')]:
        vals=[]
        for row in rows:
            value=next((row[k] for k in keys if k in row),None)
            year=next((row[k] for k in ('year','nam','năm') if k in row),None)
            if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and year is not None:
                vals.append((str(year),value))
        if len(vals)<2:continue
        vals=vals[-5:]
        shown=[(year,billion_value(value,unit)) for year,value in vals]
        shown=[(year,value) for year,value in shown if value is not None]
        if not shown:continue
        chart_unit='tỷ VND (làm tròn đến hàng tỷ)'
        width=(WIDTH-16)/2; height=155
        d=Drawing(width,height)
        d.add(Rect(0,0,width,height,fillColor=colors.white,strokeColor=None))
        d.add(String(12,height-18,title,fontName='NotoB',fontSize=9,fillColor=NAVY))
        d.add(String(12,height-32,chart_unit,fontName='Noto',fontSize=7,fillColor=MUTED))
        low=min(0,min(v for _,v in shown)); high=max(0,max(v for _,v in shown));span=high-low or 1
        left=12; right=width-12; bottom=27; top=height-47
        y=lambda value:bottom+(value-low)/span*(top-bottom)
        zero=y(0);d.add(Line(left,zero,right,zero,strokeColor=LINE,strokeWidth=.5))
        gap=(right-left)/len(shown);bar=gap*.54
        for i,(year,value) in enumerate(shown):
            x=left+gap*(i+.5);end=y(value)
            d.add(Rect(x-bar/2,min(zero,end),bar,max(abs(end-zero),.8),fillColor=BLUE,strokeColor=None))
            label_y=end+5 if value>=0 else end-10
            d.add(String(x,label_y,num(value,0),fontName='Noto',fontSize=6.8,textAnchor='middle',fillColor=INK))
            d.add(String(x,10,year,fontName='Noto',fontSize=7,textAnchor='middle',fillColor=MUTED))
        diagrams.append(d)
    if not diagrams:return []
    widths=[(WIDTH-16)/2,16,(WIDTH-16)/2]
    row=[diagrams[0],Spacer(16,1),diagrams[1] if len(diagrams)>1 else Spacer(1,1)]
    chart=Table([row],colWidths=widths,hAlign='LEFT');chart.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    return [Spacer(1,12),chart,p('Biểu đồ doanh thu và lợi nhuận theo kỳ báo cáo; chỉ hiển thị các kỳ có số liệu.','small')]


VN_TZ = timezone(timedelta(hours=7))


def now_vietnam():
    return datetime.now(VN_TZ).replace(microsecond=0)


def apply_background(pdf_path, background_path, ticker, as_of, generated_at):
    """Ghép PDF nền gốc từ trang 2; trang 1 giữ bìa JPG; giữ bookmark/mục lục/font nhúng."""
    source = PdfReader(str(pdf_path))
    background = PdfReader(str(background_path)).pages[0]
    background.transfer_rotation_to_content()
    bw, bh = float(background.mediabox.width), float(background.mediabox.height)
    writer = PdfWriter()
    writer.clone_document_from_reader(source)
    for page in writer.pages[1:]:
        page.merge_transformed_page(background, Transformation().scale(W/bw,H/bh), over=False)
    stamp = generated_at.strftime("D:%Y%m%d%H%M%S+07'00'")
    writer.add_metadata({'/Title':f'{ticker} - Phân tích cổ phiếu - {as_of}',
        '/Subject':f'ticker={ticker}; as_of={as_of}; generated_at={generated_at.isoformat()}',
        '/CreationDate':stamp, '/ModDate':stamp})
    buf = BytesIO()
    writer.write(buf)
    Path(pdf_path).write_bytes(buf.getvalue())


class ReportDoc(BaseDocTemplate):
    def __init__(self, filename, ticker, as_of, generated_at, cover_path):
        super().__init__(filename,pagesize=A4,title=f'{ticker} - Báo cáo phân tích {as_of}',author='Nhóm dự án',
            leftMargin=M,rightMargin=M,topMargin=98,bottomMargin=112)
        self.ticker,self.as_of,self.generated_at=ticker,as_of,generated_at
        self.cover=ImageReader(str(cover_path))
        f=Frame(M,112,WIDTH,H-210,id='main',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
        cf=Frame(M,M,WIDTH,H-2*M,id='coverframe',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
        self.addPageTemplates([PageTemplate(id='Cover',frames=[cf],onPage=self.draw_cover),
            PageTemplate(id='Content',frames=[f],onPage=self.draw_content)])
    def draw_cover(self,c,doc):
        # Trang 1 chỉ dùng nguyên JPG; không ghép PDF nền hay chèn nhãn.
        c.saveState()
        iw,ih=self.cover.getSize()
        scale=min(W/iw,H/ih)
        c.drawImage(self.cover,(W-iw*scale)/2,(H-ih*scale)/2,iw*scale,ih*scale)
        c.restoreState()
    def draw_content(self,c,doc):
        c.saveState()
        c.setFillColor(NAVY);c.setFont('NotoB',8);c.drawString(M+32,H-62,'HIỂU THỊ TRƯỜNG • KIẾN TẠO GIÁ TRỊ')
        c.setFont('Noto',7);c.drawRightString(W-M,H-62,f'{self.ticker} | {self.as_of}')
        c.setFillColor(colors.white);c.roundRect(M-8,57,WIDTH+16,24,5,fill=1,stroke=0)
        c.setFillColor(MUTED);c.setFont('Noto',7)
        c.drawString(M,68,self.ticker+' | Xuất '+self.generated_at.strftime('%d/%m/%Y %H:%M:%S')+' (UTC+7)')
        c.setFont('NotoB',8);c.drawRightString(W-M,68,f'{doc.page:02d}')
        c.restoreState()
    def afterFlowable(self,flowable):
        if hasattr(flowable,'toc_key'):
            text=flowable.getPlainText();key=flowable.toc_key
            self.canv.bookmarkPage(key);self.canv.addOutlineEntry(text,key,0)
            self.notify('TOCEntry',(0,text,self.page,key))


def validate(data):
    ticker=data.get('ticker');as_of=data.get('as_of')
    if not isinstance(ticker,str) or not re.fullmatch(r'[A-Z0-9]{2,10}',ticker):
        raise ValueError('ticker phải viết HOA, gồm 2-10 chữ cái hoặc số.')
    if not isinstance(as_of,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',as_of):
        raise ValueError('as_of phải có định dạng YYYY-MM-DD.')
    date.fromisoformat(as_of)
    for name in MODULES:
        module=data.get(name)
        if not isinstance(module,dict):raise ValueError(f'{name} phải là dict.')
        if module.get('error'):raise ValueError(f'{name}: {module["error"]}')
        if module.get('ticker')!=ticker:raise ValueError(f'{name}: ticker không khớp {ticker}.')
        if module.get('as_of')!=as_of:raise ValueError(f'{name}: as_of không khớp {as_of}.')
    def check_numbers(obj,path=''):
        if isinstance(obj,dict):
            for k,v in obj.items():check_numbers(v,path+'.'+k)
        elif isinstance(obj,list):
            for i,v in enumerate(obj):check_numbers(v,f'{path}[{i}]')
        elif isinstance(obj,float) and not math.isfinite(obj):raise ValueError(f'{path}: dùng null thay NaN/Infinity.')
    check_numbers(data)
    assessment_risk=data['assessment'].get('risk_profile',data['assessment'].get('risk'))
    if assessment_risk is not None and assessment_risk!=data.get('risk_profile','balanced'):
        raise ValueError('Hồ sơ rủi ro khác assessment. Cần gọi lại assess() trước khi xuất PDF.')
    rating=data['assessment'].get('rating')
    if rating not in RATINGS:raise ValueError('assessment.rating không thuộc 6 giá trị đã thống nhất.')
    if data.get('risk_profile','balanced') not in RISKS:raise ValueError('Hồ sơ rủi ro không hợp lệ.')


def collect_sources(data):
    result=[];seen=set()
    for name in MODULES:
        for s in data[name].get('sources') or []:
            if not isinstance(s,dict):continue
            key=(s.get('name'),s.get('url'),s.get('date'))
            if key not in seen:
                seen.add(key);result.append({k:s.get(k) for k in ('name','url','date')})
    return result


def generate_report(report_data,output_path,*,assets_dir=None,data_dir=None,demo=False,generated_at=None):
    """Nhận dict đã gộp 4 module, xuất PDF; trả đường dẫn str. Lỗi được run() xử lý."""
    setup_fonts();validate(report_data)
    data=report_data;tech=data['technical'];fund=data['fundamental'];news=data['company_news'];a=data['assessment']
    ticker=data['ticker'];as_of=data['as_of'];assets=Path(assets_dir) if assets_dir else (BASE/'assets' if (BASE/'assets').exists() else BASE)
    out=Path(output_path).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    generated_at=generated_at or now_vietnam()
    if generated_at.tzinfo is None:raise ValueError('generated_at phải có múi giờ.')
    generated_at=generated_at.astimezone(VN_TZ)
    cover_file = assets/'cover.jpg' if (assets/'cover.jpg').exists() else BASE/'cover.jpg'
    doc=ReportDoc(str(out),ticker,as_of,generated_at,cover_file)
    story=[Spacer(1,1),NextPageTemplate('Content'),PageBreak(),p('MỤC LỤC','title')]
    story.append(p(f'{ticker} | Ngày báo cáo {as_of} | Hồ sơ rủi ro: {RISKS[data.get("risk_profile","balanced")]}','small'))
    toc=TableOfContents();toc.levelStyles=[ParagraphStyle('toc',fontName='Noto',fontSize=10,leading=18,
        textColor=NAVY,spaceBefore=12,leftIndent=0,rightIndent=12)]
    story.extend([Spacer(1,14),toc])
    def section(number,title):
        if number in (1, 2, 3, 4, 6, 7, 8, 9):story.append(PageBreak())
        else:story.append(Spacer(1,16))
        h=p(f'{number:02d}  {title}','title');h.toc_key=f'section-{number}';story.append(h)
    def sub(title):story.extend([Spacer(1,8),p(title,'h2')])

    comp = news.get('company') or {}
    gov = news.get('governance') or {}
    ind = comp.get('industry') or {}

    # Đảm bảo phân loại ICB 4 cấp luôn đầy đủ cho mọi mã cổ phiếu
    if not ind.get('level_1') or not ind.get('level_4'):
        try:
            from fundamental import _INDUSTRY_DF_CACHE
            from vnstock import Reference
            df_ind = _INDUSTRY_DF_CACHE if _INDUSTRY_DF_CACHE is not None else Reference().equity().list_by_industry()
            sub_ind = df_ind[df_ind["symbol"] == ticker.upper()]
            if not sub_ind.empty:
                ind = {
                    'level_1': sub_ind[sub_ind["icb_level"] == 1]["icb_name"].iloc[0] if not sub_ind[sub_ind["icb_level"] == 1].empty else ind.get('level_1'),
                    'level_2': sub_ind[sub_ind["icb_level"] == 2]["icb_name"].iloc[0] if not sub_ind[sub_ind["icb_level"] == 2].empty else ind.get('level_2'),
                    'level_3': sub_ind[sub_ind["icb_level"] == 3]["icb_name"].iloc[0] if not sub_ind[sub_ind["icb_level"] == 3].empty else ind.get('level_3'),
                    'level_4': sub_ind[sub_ind["icb_level"] == 4]["icb_name"].iloc[0] if not sub_ind[sub_ind["icb_level"] == 4].empty else ind.get('level_4'),
                }
        except Exception:
            pass

    resolved_sector = fund.get('sector')
    if not resolved_sector or resolved_sector == 'Khác':
        resolved_sector = ind.get('level_2') or ind.get('level_1') or 'Khác'

    section(1,'Tổng quan và đánh giá')
    rating=a.get('rating');not_enough=rating in ('KHÔNG ĐỦ DỮ LIỆU', 'Thiếu dữ liệu')
    price=tech.get('price') or {}
    story.append(metric_strip([('Mã cổ phiếu',ticker),('Giá điều chỉnh • VND/cp',num(price.get('close'))),('Ngành',display(resolved_sector))]))
    story.append(Spacer(1,10))
    story.append(callout('ĐÁNH GIÁ • '+rating,'Độ tin cậy: '+display(a.get('confidence'),'confidence')+' | Hồ sơ rủi ro: '+RISKS[data.get('risk_profile','balanced')]))
    story.append(Spacer(1,10))
    ref=fund.get('valuation_ref') or {}
    trend={'up':'Tăng','down':'Giảm','sideways':'Đi ngang'}.get(tech.get('trend'),EMPTY)
    story.append(metric_strip([('Sinh lời • ROE',pct((fund.get('profitability') or {}).get('roe'))),
        ('Tham chiếu • P/E trung vị',num((ref.get('pe') or {}).get('median'),2)),('Xu hướng giá',trend)]))
    story.append(p('Các ô trên hiển thị chỉ số thực tế; không phải thang chấm điểm.','small'))
    if not not_enough:
        story.append(metric_strip([('Giá mục tiêu • VND/cp',num(a.get('target_price'))),('Chênh lệch so với giá',pct(a.get('upside')))]))
    sub('Dữ liệu còn thiếu' if not_enough else 'Luận điểm chính')
    points=a.get('data_gaps') if not_enough else a.get('reasons')
    story.extend(bullets(points[:3] if isinstance(points,list) else points))
    story.append(p('Ngày dữ liệu: '+as_of+' | Ngày giá: '+display(price.get('date')),'small'))

    section(2, 'Ngành và hồ sơ doanh nghiệp')

    # 1. Bảng Thông tin Pháp lý, Quản trị & Niêm yết
    sub('Hồ sơ pháp lý & Thông tin niêm yết')
    cap_data = comp.get('charter_capital') or {}
    cap_val = cap_data.get('value')
    cap_unit = cap_data.get('unit', 'VND')
    cap_str = f"{cap_val / 1e9:,.0f} tỷ {cap_unit}" if (cap_val and cap_val > 1e9) else (f"{cap_val:,.0f} {cap_unit}" if cap_val else EMPTY)
    emp_val = comp.get('number_of_employees')
    emp_str = f"{emp_val:,.0f} nhân sự" if emp_val else EMPTY

    c_info = [
        ['Tên doanh nghiệp', display(comp.get('name')), 'Sàn niêm yết', display(comp.get('exchange'))],
        ['Tên quốc tế', display(comp.get('english_name')), 'Ngày niêm yết', display(comp.get('listing_date'))],
        ['Vốn điều lệ', cap_str, 'Ngày thành lập', display(comp.get('founded_date'))],
        ['Đại diện pháp luật', display(comp.get('ceo_name')), 'Chức vụ', display(comp.get('ceo_position'))],
        ['Quy mô lao động', emp_str, 'Đơn vị kiểm toán', display(comp.get('auditor'))],
        ['Mã số thuế / GPKD', display(comp.get('tax_id') or comp.get('business_code')), 'Điện thoại', display(comp.get('phone'))],
        ['Trụ sở chính', display(comp.get('address')), 'Website', display(comp.get('website'))],
    ]
    story.append(table(['Chỉ tiêu', 'Thông tin', 'Chỉ tiêu', 'Thông tin'], c_info, [WIDTH * 0.22, WIDTH * 0.28, WIDTH * 0.22, WIDTH * 0.28]))
    story.append(Spacer(1, 8))

    # 2. Phân loại Ngành theo chuẩn ICB 4 cấp
    sub('Phân loại ngành theo chuẩn ICB (Industry Classification Benchmark)')
    icb_info = [
        ['Cấp 1 • Ngành tổng quan', display(ind.get('level_1')), 'Cấp 3 • Ngành phụ', display(ind.get('level_3'))],
        ['Cấp 2 • Nhóm ngành', display(ind.get('level_2')), 'Cấp 4 • Phân ngành chuyên sâu', display(ind.get('level_4'))],
    ]
    story.append(table(['Cấp độ', 'Phân loại', 'Cấp độ', 'Phân loại'], icb_info, [WIDTH * 0.24, WIDTH * 0.26, WIDTH * 0.24, WIDTH * 0.26]))
    story.append(Spacer(1, 8))

    # 3. Mô hình hoạt động kinh doanh cốt lõi
    sub('Mô hình hoạt động & Hoạt động kinh doanh cốt lõi')
    b_summary = news.get('business_summary') or comp.get('business_model') or comp.get('history') or comp.get('name')
    if isinstance(b_summary, str) and len(b_summary) > 650:
        b_summary = b_summary[:647] + "..."
    story.append(callout('Ngành phân tích: ' + display(resolved_sector), display(b_summary)))
    story.append(Spacer(1, 8))

    # 4. Cơ cấu doanh thu theo mảng
    rev_mix = news.get('revenue_mix') or comp.get('revenue_structure')
    sub('Cơ cấu doanh thu theo mảng kinh doanh')
    story.extend(details(rev_mix))
    story.append(Spacer(1, 6))

    # 5. Cơ cấu sở hữu & Cổ đông lớn
    shareholders = gov.get('major_shareholders') or []
    ownership = gov.get('ownership') or []
    if shareholders or ownership:
        sub('Cơ cấu sở hữu & Cổ đông lớn')
        if shareholders:
            sh_rows = []
            for s in shareholders[:6]:
                shares_val = s.get('shares_owned')
                shares_str = f"{shares_val:,.0f} cp" if isinstance(shares_val, (int, float)) else EMPTY
                pct_val = s.get('ownership_percentage')
                pct_str = f"{pct_val * 100:.2f}%" if isinstance(pct_val, (int, float)) else EMPTY
                sh_rows.append([
                    display(s.get('name')),
                    shares_str,
                    pct_str,
                    display(s.get('update_date')),
                ])
            story.append(table(['Cổ đông lớn', 'Số lượng cổ phần', 'Tỷ lệ sở hữu', 'Ngày cập nhật'], sh_rows, [WIDTH * 0.38, WIDTH * 0.24, WIDTH * 0.18, WIDTH * 0.20]))
            story.append(Spacer(1, 6))

        if ownership:
            own_rows = []
            for ow in ownership[:4]:
                pct_val = ow.get('ownership_percentage')
                pct_str = f"{pct_val * 100:.2f}%" if isinstance(pct_val, (int, float)) else EMPTY
                own_rows.append([
                    display(ow.get('owner_type')),
                    pct_str,
                    display(ow.get('update_date')),
                ])
            story.append(table(['Nhóm đối tượng sở hữu', 'Tỷ lệ sở hữu', 'Thời điểm cập nhật'], own_rows, [WIDTH * 0.50, WIDTH * 0.25, WIDTH * 0.25]))
            story.append(Spacer(1, 8))

    # 6. Ban lãnh đạo & Quản trị chủ chốt
    officers = gov.get('officers') or []
    if officers:
        sub('Ban lãnh đạo & Quản trị chủ chốt')
        off_rows = []
        for o in officers[:6]:
            off_rows.append([
                display(o.get('name')),
                display(o.get('position') or o.get('position_en')),
                date_label(o.get('from_date'), 'Không công khai'),
            ])
        story.append(table(['Họ và tên lãnh đạo', 'Chức danh đảm nhiệm', 'Ngày bổ nhiệm'], off_rows, [WIDTH * 0.40, WIDTH * 0.40, WIDTH * 0.20]))
        story.append(Spacer(1, 8))

    # 7. Hệ thống công ty con & Liên kết tiêu biểu
    subsidiaries = gov.get('subsidiaries') or []
    if subsidiaries:
        sub('Hệ thống công ty con & Liên kết tiêu biểu')
        sub_rows = []
        for sb in subsidiaries[:6]:
            cap_sub = sb.get('charter_capital')
            cap_sub_str = f"{cap_sub / 1e9:,.0f} tỷ VND" if (cap_sub and cap_sub > 1e9) else EMPTY
            pct_sub = sb.get('ownership_percent')
            pct_sub_str = f"{pct_sub * 100:.1f}%" if isinstance(pct_sub, (int, float)) else EMPTY
            sub_rows.append([
                display(sb.get('name')),
                display(sb.get('type') or 'Công ty con'),
                cap_sub_str,
                pct_sub_str,
            ])
        story.append(table(['Tên đơn vị thành viên', 'Loại hình', 'Vốn điều lệ', 'Tỷ lệ sở hữu'], sub_rows, [WIDTH * 0.44, WIDTH * 0.20, WIDTH * 0.20, WIDTH * 0.16]))
        story.append(Spacer(1, 8))

    # 8. Lịch sử tăng vốn điều lệ gần nhất
    cap_hist = gov.get('capital_history') or []
    if cap_hist:
        sub('Lịch sử các đợt thay đổi vốn điều lệ gần nhất')
        hist_rows = []
        for ch in cap_hist[:4]:
            val_ch = ch.get('charter_capital')
            val_ch_str = f"{val_ch / 1e9:,.0f} tỷ {ch.get('currency', 'VND')}" if (val_ch and val_ch > 1e9) else EMPTY
            hist_rows.append([
                display(ch.get('date')),
                val_ch_str,
                display(ch.get('currency', 'VND')),
            ])
        story.append(table(['Ngày hiệu lực', 'Vốn điều lệ sau tăng', 'Đơn vị tính'], hist_rows, [WIDTH * 0.35, WIDTH * 0.45, WIDTH * 0.20]))
        story.append(Spacer(1, 8))

    # 9. Động lực kinh doanh cốt lõi
    drivs = news.get('drivers') or news.get('growth_drivers')
    if isinstance(drivs, list) and drivs and isinstance(drivs[0], dict):
        drivs = [d.get('summary') or d.get('text') for d in drivs if d.get('summary') or d.get('text')]
    sub('Động lực kinh doanh & Luận điểm phát triển')
    story.extend(bullets(drivs))

    section(3, 'Phân tích tài chính')
    unit = financial_unit(fund)
    basis = financial_basis(fund)
    display_unit = 'tỷ VND (làm tròn đến hàng tỷ)' if billion_value(1,unit) is not None else 'Chưa xác định đơn vị nguồn; chưa quy đổi'
    story.append(p('Đơn vị tổng tiền: ' + display_unit + '. ROE và biên lợi nhuận: %.', 'small'))
    story.append(p('Cơ sở chỉ tiêu: ' + basis, 'small'))

    ft = fund.get('financials_table')
    if ft is not None and not isinstance(ft, list):
        raise ValueError('financials_table phải là list các dòng dict.')

    def _year_sort_key(r):
        y_str = str(r.get('year', ''))
        m = re.search(r'\d{4}', y_str)
        return (float(m.group(0)) + (0.5 if 'TTM' in y_str.upper() else 0)) if m else 0.0

    sorted_ft = sorted(ft or [], key=_year_sort_key)
    selected_ft = sorted_ft[-6:] if sorted_ft else []

    if selected_ft:
        year_headers = [str(r.get('year', '')) for r in selected_ft]
        aliases = [
            ('Doanh thu', ('revenue', 'doanh_thu', 'doanh thu')),
            ('Lợi nhuận sau thuế', ('net_income', 'lnst', 'LNST', 'net_profit')),
            ('Tổng tài sản', ('total_assets', 'tong_tai_san')),
            ('Vốn chủ sở hữu', ('equity', 'vcsh', 'VCSH')),
            ('ROE', ('roe',)),
            ('Biên LN ròng', ('net_margin',)),
        ]
        t_rows = []
        for label, keys in aliases:
            row_vals = [label]
            for r in selected_ft:
                v = next((r[k] for k in keys if k in r), None)
                if label in ('ROE', 'Biên LN ròng'):
                    row_vals.append(pct(v))
                else:
                    row_vals.append(num(billion_value(v, unit)))
            t_rows.append(row_vals)

        n_col = len(year_headers)
        label_w = 110
        col_w = (WIDTH - label_w) / n_col
        story.append(table(['Chỉ tiêu'] + year_headers, t_rows, [label_w] + [col_w] * n_col))
    else:
        story.append(table(['Chỉ tiêu', 'Dữ liệu'], [['Bảng tài chính', EMPTY]]))

    story.extend(financial_charts(selected_ft, unit))
    sub('Chỉ số tài chính • TTM / kỳ gần nhất theo nguồn')
    per=fund.get('per_share') or {}
    stats=[[label,num(per.get(k))] for k,label in [('eps_ttm','EPS TTM (VND/cp)'),('bvps','BVPS (VND/cp)'),('dps','Cổ tức (VND/cp)'),('eps_avg_5y','EPS TB 5 năm (VND/cp)')]]
    for group,items in [('profitability',[('roe','ROE'),('roa','ROA'),('gross_margin','Biên LN gộp'),('net_margin','Biên LN ròng')]),
        ('growth',[('revenue_yoy','Doanh thu YoY'),('net_income_yoy','LNST YoY'),('eps_cagr_3y','EPS CAGR 3 năm')])]:
        for k,label in items:stats.append([label,pct((fund.get(group) or {}).get(k))])
    leverage=fund.get('leverage') or {};bank='ngân hàng' in str(fund.get('sector','')).lower()
    if bank:
        bank_data={str(k).lower():v for k,v in {**(fund.get('banking') or {}),**leverage}.items()}
        stats.extend([[k.upper(),pct(bank_data.get(k))] for k in ('nim','npl','cir','car')])
    else:
        stats.extend([[label,num(leverage.get(k),2)+' lần' if leverage.get(k) is not None else EMPTY] for k,label in [('debt_to_equity','Nợ / VCSH'),('current_ratio','Thanh toán hiện hành')]])
    paired=[]
    for i in range(0,len(stats),2):paired.append(stats[i]+(stats[i+1] if i+1<len(stats) else ['','']))
    story.append(table(['Chỉ tiêu','Giá trị','Chỉ tiêu','Giá trị'],paired,[WIDTH*.29,WIDTH*.21,WIDTH*.29,WIDTH*.21]))
    section(4,'Phân tích kỹ thuật')
    trend={'up':'Tăng','down':'Giảm','sideways':'Đi ngang'}.get(tech.get('trend'),EMPTY)
    story.append(p('Xu hướng: '+trend+' | Giá đóng cửa điều chỉnh theo quy ước nhóm.'))
    story.append(p('Dữ liệu giá bỏ qua những ngày không có phiên giao dịch.', 'small'))
    chart = technical_image(tech, data_dir)
    if chart:
        image=Image(chart);ratio=min(WIDTH/image.imageWidth,260/image.imageHeight)
        image.drawWidth=image.imageWidth*ratio;image.drawHeight=image.imageHeight*ratio
        story.append(image)
    else:story.append(callout('Biểu đồ giá','Nguồn chưa cung cấp lịch sử giá hoặc ảnh hợp lệ để vẽ biểu đồ.'))
    story.append(Spacer(1,10))
    technical_rows=[[label,num(tech.get(k),2 if k=='rsi14' else 0)] for k,label in
        [('rsi14','RSI 14'),('ma20','MA20 (VND/cp)'),('ma50','MA50 (VND/cp)'),('ma200','MA200 (VND/cp)'),
         ('avg_volume_20d','Khối lượng bình quân 20 phiên (cp)'),('support','Hỗ trợ (VND/cp)'),('resistance','Kháng cự (VND/cp)')]]
    technical_rows.extend([[label,pct(tech.get(k))] for k,label in [('return_1m','Lợi suất 1 tháng'),('return_3m','Lợi suất 3 tháng'),('return_1y','Lợi suất 1 năm')]])
    story.append(table(['Chỉ tiêu','Kết quả'],technical_rows,[WIDTH*.65,WIDTH*.35]))
    section(5,'Tin tức và sự kiện')
    if not news.get('news'):story.append(p(EMPTY))
    for n in news.get('news') or []:
        story.append(p(display(n.get('title')),'h2'))
        story.append(p(' | '.join([date_label(n.get('date'), 'Không rõ ngày xuất bản'),display(n.get('source')),
            {'positive':'Tích cực','neutral':'Trung lập','negative':'Tiêu cực'}.get(n.get('sentiment'),EMPTY)]),'small'))
        url=n.get('link')
        if isinstance(url,str) and url.startswith(('https://','http://')):
            story.append(Paragraph('<link href="'+escape(url,{'"':'&quot;'})+'" color="#146BDF">'+escape(url)+'</link>',STYLES['small']))
        story.append(Spacer(1,12))
    section(6,'Định giá cổ phiếu')
    if not_enough:
        story.append(callout('KHÔNG ĐỦ DỮ LIỆU','Chưa công bố giá mục tiêu. Lý do được lấy trực tiếp từ data_gaps.'))
        story.extend(bullets(a.get('data_gaps')))
    else:
        story.extend(valuation_content(a))
    sub('Tham chiếu định giá')
    ref=fund.get('valuation_ref') or {};story.append(p('Nguồn tham chiếu: '+reference_source(ref)))
    story.append(p(ref.get('note') or 'Các cột p25 / median / p75 là mức thấp / giữa / cao của dữ liệu đầu vào. Chỉ coi là phân vị ngành khi nguồn và mẫu thống kê đã được xác minh.'))
    story.append(table(['Hệ số','Thấp (p25)','Cơ sở (median)','Cao (p75)'],[[k.upper()]+[num((ref.get(k) or {}).get(q),2) for q in ('p25','median','p75')] for k in ('pe','pb')]))
    sub('Các giả định và giới hạn cần hiểu')
    story.append(p('Trọng số cho biết mỗi phương pháp hoặc tình huống đóng góp bao nhiêu vào kết quả. Các giả định có thể khác thực tế; nên đọc cùng số liệu tài chính và rủi ro.'))
    terms = {'eps_cagr_3y': 'tốc độ tăng EPS bình quân 3 năm', 'eps_avg_5y': 'EPS bình quân 5 năm',
             'EPS TTM': 'lợi nhuận mỗi cổ phiếu trong 12 tháng gần nhất',
             'TTM': '12 tháng gần nhất', 'ROE': 'tỷ suất lợi nhuận trên vốn chủ sở hữu',
             'BVPS': 'giá trị sổ sách mỗi cổ phiếu', 'EPS': 'lợi nhuận mỗi cổ phiếu',
             'Bear/Base/Bull': 'Thận trọng/Cơ sở/Tích cực', 'P25/Median/P75': 'thấp/cơ sở/cao',
             'bear/bull': 'thận trọng/tích cực', 'bear': 'thận trọng', 'base': 'cơ sở', 'bull': 'tích cực',
             'upside': 'chênh lệch so với giá đóng cửa', 'rating': 'mức đánh giá',
             'xác suất theo hồ sơ rủi ro': 'tỷ trọng tính giá theo hồ sơ rủi ro',
             'conservative': 'thận trọng', 'balanced': 'cân bằng', 'aggressive': 'chấp nhận rủi ro cao',
             'NAV': 'giá trị tài sản ròng sau khi trừ nợ'}
    for assumption in a.get('assumptions') or []:
        text = str(assumption)
        for term, meaning in terms.items():
            text = text.replace(term, meaning)
        story.append(p(text))
    section(7,'Rủi ro và thách thức')
    sub('Rủi ro từ đánh giá tổng hợp');story.extend(bullets(a.get('risks') or ['Chưa ghi nhận điều kiện cảnh báo trong đánh giá hiện có. Xem các yếu tố cần theo dõi và giới hạn dữ liệu bên dưới.']))
    sub('Cảnh báo từ tin tức, đặc thù ngành & đòn bẩy tài chính')
    flags=risk_flags(fund, tech, news, as_of)
    if not flags and news.get('risks'):
        flags=[{"severity": "medium", "text": r.get('summary') or r.get('text', ''), "source": r.get('source', 'Tin tức'), "date": r.get('date', as_of)} for r in news.get('risks') if isinstance(r, dict)]
    if not flags:story.append(p(EMPTY))
    for flag in flags:
        severity={'high':'Cao','medium':'Trung bình','low':'Thấp'}.get(flag.get('severity'),EMPTY)
        story.append(p('Mức độ '+severity+': '+display(flag.get('text'))))
        story.append(p('Nguồn: '+display(flag.get('source'))+' | Ngày: '+display(flag.get('date')),'small'))
    section(8,'Kết luận và điều cần theo dõi')
    story.append(callout('Đánh giá: '+rating,'Độ tin cậy: '+display(a.get('confidence'))))
    sub('Lý do đánh giá');story.extend(bullets(a.get('data_gaps') if not_enough else a.get('reasons')))
    sub('Dữ liệu còn thiếu')
    if a.get('data_gaps'):
        story.extend(bullets(a.get('data_gaps')))
    else:
        story.append(p('Hệ thống ghi nhận đầy đủ báo cáo tài chính và dữ liệu thị trường; không có thiếu sót trọng yếu.'))
    section(9,'Nguồn dữ liệu')
    sources=collect_sources(data)
    if not sources:story.append(p('Chưa có nguồn trong các JSON đầu vào.'))
    for i,s in enumerate(sources,1):
        story.append(p(f'{i:02d}. '+display(s.get('name')),'h2'))
        source_date = s.get('date')
        if not source_date or str(source_date).strip().lower() in ('', 'chưa có dữ liệu', 'null', 'none', 'n/a', 'unknown'):
            source_date = 'Không rõ thời gian xuất bản'
        story.append(p('Ngày nguồn: '+str(source_date),'small'))
        url=s.get('url')
        if isinstance(url,str) and url.startswith(('https://','http://')):
            story.append(Paragraph('<link href="'+escape(url,{'"':'&quot;'})+'" color="#146BDF">'+escape(url)+'</link>',STYLES['small']))
        else:story.append(p('Đường dẫn: '+EMPTY,'small'))
        story.append(Spacer(1,12))
    section(10,'Phương pháp và giới hạn')
    story.append(p('Giá, EPS, BVPS và cổ tức dùng VND/cổ phiếu. Tổng tiền trong bảng và biểu đồ tài chính được quy đổi sang tỷ VND, làm tròn đến hàng tỷ khi hiển thị; dữ liệu gốc giữ nguyên để tính toán. Tỷ lệ được hiển thị theo %. Số liệu thiếu được ghi là “Chưa có dữ liệu”; số 0 hợp lệ được giữ nguyên.'))
    story.append(p('Giá đóng cửa là giá điều chỉnh theo quy ước của nhóm. Báo cáo không tự tính lại định giá, tự thay khuyến nghị hoặc suy diễn dữ liệu còn thiếu.'))
    sub('Tuyên bố từ module đánh giá');story.extend(details(a.get('disclaimer')))
    doc.multiBuild(story,maxPasses=8)
    bg_file = assets/'background.pdf' if (assets/'background.pdf').exists() else BASE/'background.pdf'
    apply_background(out,bg_file,ticker,as_of,generated_at)
    return str(out)


def run(ticker,as_of,*,input_dir=None,output_dir=None,risk='balanced',demo=False):
    """API cho Quang: đọc 4 JSON, tạo PDF và report_pdf.json; mọi lỗi trả dict."""
    ticker=str(ticker).strip().upper();inputs=Path(input_dir) if input_dir else BASE/'data'
    outputs=Path(output_dir) if output_dir else BASE/'reports'
    generated_at=now_vietnam()
    result={'ticker':ticker,'as_of':as_of,'generated_at':generated_at.isoformat(),'sources':[]}
    try:
        data={'ticker':ticker,'as_of':as_of,'risk_profile':risk}
        for name in MODULES:
            data[name]=json.loads((inputs/(name+'.json')).read_text(encoding='utf-8-sig'))
        result['sources']=collect_sources(data)
        if any(m.get('fixture') for m in (data[n] for n in MODULES)) and not demo:
            raise ValueError('JSON mẫu kiểm thử: dùng demo=True, hoặc thay bằng JSON thật của nhóm.')
        filename=f'{ticker}_{as_of}_xuat_{generated_at.strftime("%Y-%m-%d_%H-%M-%S")}.pdf'
        # Validation occurs before using user inputs as a filename.
        validate(data)
        path=generate_report(data,outputs/filename,data_dir=inputs,demo=demo,generated_at=generated_at)
        result.update({'pdf_path':path,'rating':data['assessment']['rating'],'risk_profile':risk,'demo':demo})
    except Exception as exc:
        result['error']=str(exc)
    try:
        outputs.mkdir(parents=True,exist_ok=True)
        (outputs/'report_pdf.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    except Exception as exc:
        result['error']=result.get('error','')+'; Không ghi được report_pdf.json: '+str(exc)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description='Xuất báo cáo PDF từ JSON của nhóm')
    ap.add_argument('--ticker',default='HPG');ap.add_argument('--as-of',default=now_vietnam().date().isoformat())
    ap.add_argument('--input-dir');ap.add_argument('--output-dir');ap.add_argument('--risk',choices=list(RISKS),default='balanced')
    ap.add_argument('--demo',action='store_true')
    args=ap.parse_args()
    result=run(args.ticker,args.as_of,input_dir=args.input_dir,output_dir=args.output_dir,risk=args.risk,demo=args.demo)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(1 if result.get('error') else 0)
