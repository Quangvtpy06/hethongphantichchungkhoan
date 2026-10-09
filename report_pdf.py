"""Bộ xuất PDF của Hân. Đọc JSON của nhóm; không tự lấy dữ liệu hoặc định giá."""
from __future__ import annotations
import argparse
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pypdf import PdfReader, PdfWriter, Transformation
from pathlib import Path
from xml.sax.saxutils import escape
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
RATINGS = ('MUA', 'TÍCH LŨY', 'NẮM GIỮ', 'GIẢM TỶ TRỌNG', 'BÁN', 'KHÔNG ĐỦ DỮ LIỆU')
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
            pdfmetrics.registerFont(TTFont(name, str(BASE/'assets'/'fonts'/filename)))
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
    for keys,title in [(('revenue','doanh_thu'),'Doanh thu'),(('net_income','lnst','LNST'),'Lợi nhuận sau thuế')]:
        vals=[]
        for row in rows:
            value=next((row[k] for k in keys if k in row),None)
            year=next((row[k] for k in ('year','nam','năm') if k in row),None)
            if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and year is not None:
                vals.append((str(year),value))
        if len(vals)<2:continue
        vals=vals[-5:]
        factor=1e9 if unit=='VND' else 1
        shown=[(year,value/factor) for year,value in vals]
        chart_unit='tỷ VND' if unit=='VND' else str(unit or 'Chưa xác định đơn vị')
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
            d.add(String(x,label_y,num(value,1),fontName='Noto',fontSize=6.8,textAnchor='middle',fillColor=INK))
            d.add(String(x,10,year,fontName='Noto',fontSize=7,textAnchor='middle',fillColor=MUTED))
        diagrams.append(d)
    if not diagrams:return []
    widths=[(WIDTH-16)/2,16,(WIDTH-16)/2]
    row=[diagrams[0],Spacer(16,1),diagrams[1] if len(diagrams)>1 else Spacer(1,1)]
    chart=Table([row],colWidths=widths,hAlign='LEFT');chart.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    return [Spacer(1,12),chart,p('Biểu đồ từ financials_table; bỏ qua điểm null.','small')]


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
    ticker=data['ticker'];as_of=data['as_of'];assets=Path(assets_dir) if assets_dir else BASE/'assets'
    out=Path(output_path).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    generated_at=generated_at or now_vietnam()
    if generated_at.tzinfo is None:raise ValueError('generated_at phải có múi giờ.')
    generated_at=generated_at.astimezone(VN_TZ)
    doc=ReportDoc(str(out),ticker,as_of,generated_at,assets/'cover.jpg')
    story=[Spacer(1,1),NextPageTemplate('Content'),PageBreak(),p('MỤC LỤC','title')]
    story.append(p(f'{ticker} | Ngày báo cáo {as_of} | Hồ sơ rủi ro: {RISKS[data.get("risk_profile","balanced")]}','small'))
    toc=TableOfContents();toc.levelStyles=[ParagraphStyle('toc',fontName='Noto',fontSize=10,leading=18,
        textColor=NAVY,spaceBefore=12,leftIndent=0,rightIndent=12)]
    story.extend([Spacer(1,14),toc])
    def section(number,title):
        if number in (1,3,4,6,9):story.append(PageBreak())
        else:story.append(Spacer(1,16))
        h=p(f'{number:02d}  {title}','title');h.toc_key=f'section-{number}';story.append(h)
    def sub(title):story.extend([Spacer(1,8),p(title,'h2')])
    section(1,'Tổng quan và đánh giá')
    rating=a.get('rating');not_enough=rating=='KHÔNG ĐỦ DỮ LIỆU'
    price=tech.get('price') or {}
    story.append(metric_strip([('Mã cổ phiếu',ticker),('Giá điều chỉnh • VND/cp',num(price.get('close'))),('Ngành',display(fund.get('sector')))]))
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
    section(2,'Ngành và hồ sơ doanh nghiệp')
    story.append(callout('Ngành: '+display(fund.get('sector')),display(news.get('business_summary'))))
    sub('Cơ cấu doanh thu');story.extend(details(news.get('revenue_mix')))
    sub('Động lực kinh doanh');story.extend(bullets(news.get('drivers')))
    section(3,'Phân tích tài chính')
    unit=fund.get('unit');basis=fund.get('basis',fund.get('reporting_basis',fund.get('period_basis')))
    story.append(p('Đơn vị tổng tiền: '+display(unit)+' | Cơ sở chỉ tiêu: '+display(basis),'small'))
    if basis is None:story.append(p('Cần bổ sung cơ sở TTM hoặc năm gần nhất. EPS TTM được xác định riêng theo field eps_ttm.','small'))
    rows=[]
    aliases=[('year','nam','năm'),('revenue','doanh_thu','doanh thu'),('net_income','lnst','LNST'),
        ('total_assets','tong_tai_san'),('equity','vcsh','VCSH'),('cfo','CFO')]
    ft=fund.get('financials_table')
    if ft is not None and not isinstance(ft,list):raise ValueError('financials_table phải là list các dòng dict.')
    for row in ft or []:
        if not isinstance(row,dict):raise ValueError('Mỗi dòng financials_table phải là dict.')
        vals=[]
        for i,keys in enumerate(aliases):
            v=next((row[k] for k in keys if k in row),None);vals.append(str(v) if i==0 and v is not None else EMPTY if i==0 else num(v))
        rows.append(vals)
    selected=rows[-5:]
    if selected:
        transposed=[[label]+[r[i] for r in selected] for i,label in enumerate(['Năm','Doanh thu','LNST','Tổng tài sản','Vốn chủ sở hữu','CFO']) if i>0]
        story.append(table(['Chỉ tiêu']+[r[0] for r in selected],transposed,[115]+[(WIDTH-115)/len(selected)]*len(selected)))
    else:story.append(table(['Chỉ tiêu','Dữ liệu'],[['Bảng tài chính',EMPTY]]))
    story.extend(financial_charts(ft,unit))
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
    chart=tech.get('chart_path')
    if chart:
        path=Path(chart)
        if not path.is_absolute():path=Path(data_dir or BASE/'data')/path
        if path.is_file():
            image=Image(str(path));ratio=min(WIDTH/image.imageWidth,230/image.imageHeight)
            image.drawWidth=image.imageWidth*ratio;image.drawHeight=image.imageHeight*ratio
            story.append(image)
        else:story.append(p('Chưa có ảnh biểu đồ tại đường dẫn được cung cấp.'))
    else:story.append(callout('Biểu đồ giá','Chưa có chart_path. Không tạo biểu đồ giả.'))
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
        story.append(p(' | '.join([display(n.get('date')),display(n.get('source')),
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
        story.append(table(['Kết quả','Giá trị'],[['Giá mục tiêu (VND/cp)',num(a.get('target_price'))],['Chênh lệch so với giá tham chiếu',pct(a.get('upside'))]]))
        sub('Kịch bản thận trọng / cơ sở / tích cực');story.extend(details(a.get('scenarios')))
        sub('Phương pháp định giá');story.extend(details(a.get('methods')))
        sub('Phân tích độ nhạy');story.extend(details(a.get('sensitivity')))
    sub('Tham chiếu định giá')
    ref=fund.get('valuation_ref') or {};story.append(p('Nguồn tham chiếu: '+display(ref.get('source'))))
    story.append(table(['Hệ số','p25','Trung vị','p75'],[[k.upper()]+[num((ref.get(k) or {}).get(q),2) for q in ('p25','median','p75')] for k in ('pe','pb')]))
    sub('Giả định');story.extend(details(a.get('assumptions')))
    section(7,'Rủi ro và thách thức')
    sub('Rủi ro từ đánh giá tổng hợp');story.extend(bullets(a.get('risks')))
    sub('Cảnh báo từ tin tức')
    flags=news.get('risk_flags') or []
    if not flags:story.append(p(EMPTY))
    for flag in flags:
        severity={'high':'Cao','medium':'Trung bình','low':'Thấp'}.get(flag.get('severity'),EMPTY)
        story.append(p('Mức độ '+severity+': '+display(flag.get('text'))))
        story.append(p('Nguồn: '+display(flag.get('source'))+' | Ngày: '+display(flag.get('date')),'small'))
    section(8,'Kết luận và điều cần theo dõi')
    story.append(callout('Đánh giá: '+rating,'Độ tin cậy: '+display(a.get('confidence'))))
    sub('Lý do đánh giá');story.extend(bullets(a.get('data_gaps') if not_enough else a.get('reasons')))
    sub('Dữ liệu còn thiếu');story.extend(bullets(a.get('data_gaps')))
    section(9,'Nguồn dữ liệu')
    sources=collect_sources(data)
    if not sources:story.append(p('Chưa có nguồn trong các JSON đầu vào.'))
    for i,s in enumerate(sources,1):
        story.append(p(f'{i:02d}. '+display(s.get('name')),'h2'))
        story.append(p('Ngày nguồn: '+display(s.get('date')),'small'))
        url=s.get('url')
        if isinstance(url,str) and url.startswith(('https://','http://')):
            story.append(Paragraph('<link href="'+escape(url,{'"':'&quot;'})+'" color="#146BDF">'+escape(url)+'</link>',STYLES['small']))
        else:story.append(p('Đường dẫn: '+EMPTY,'small'))
        story.append(Spacer(1,12))
    section(10,'Phương pháp và giới hạn')
    story.append(p('Giá, EPS, BVPS và cổ tức dùng VND/cổ phiếu. Tổng tiền được giữ theo unit của fundamental.json. Tỷ lệ đầu vào là số thập phân và chỉ đổi sang % khi hiển thị. Null được hiển thị là “Chưa có dữ liệu”; số 0 hợp lệ được giữ nguyên.'))
    story.append(p('Giá đóng cửa là giá điều chỉnh theo quy ước của nhóm. Báo cáo không tự tính lại định giá, tự thay khuyến nghị hoặc suy diễn dữ liệu còn thiếu.'))
    sub('Tuyên bố từ module đánh giá');story.extend(details(a.get('disclaimer')))
    doc.multiBuild(story,maxPasses=8)
    apply_background(out,assets/'background.pdf',ticker,as_of,generated_at)
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
