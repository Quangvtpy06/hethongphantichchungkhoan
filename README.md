# Phần PDF của Hân

Bộ tạo PDF thật từ technical.json, fundamental.json, company_news.json và assessment.json. Trang 1 dùng nguyên ảnh JPG bìa; từ trang 2 (mục lục) trở đi dùng PDF nền; các đề mục có nền màu nổi bật; nhúng Noto Sans; mục lục bấm được và tự tính lại số trang khi nội dung dài. Số trang không cố định.

## Chạy thử trên Windows

1. Giải nén toàn bộ thư mục Han_PDF; giữ nguyên assets, data và report_pdf.py.
2. Mở Terminal trong thư mục Han_PDF (PyCharm cũng được).
3. Cài thư viện:

    py -m pip install -r requirements.txt

4. Chạy PDF kiểm thử:

    py report_pdf.py --ticker HPG --as-of 2026-10-09 --demo

5. Mở PDF trong reports, hoặc nhấp đúp CHAY_THU_PDF.bat để tạo và mở tự động.

Các JSON có fixture=true là cấu trúc mẫu với giá trị null; không phải dữ liệu HPG thật. Không bỏ fixture để biến mẫu thành báo cáo thật. Thay bằng JSON đã kiểm tra của các thành viên, rồi chạy không có --demo.

## Chạy với dữ liệu thật

Đặt 4 JSON của cùng mã và ngày trong data, dùng đúng tên technical.json, fundamental.json, company_news.json, assessment.json. Sau đó:

    py report_pdf.py --ticker HPG --as-of 2026-10-09

Đổi ticker/ngày theo JSON. Nếu ticker hoặc as_of giữa các module không khớp, chương trình trả error. Mã không được nguồn hỗ trợ phải được module dữ liệu trả error; bộ PDF chuyển lỗi đó cho Quang, không tự xác nhận thị trường hỗ trợ hay tự lấy dữ liệu.

## Tích hợp với Quang

```python
from report_pdf import run

result = run(
    ticker="HPG",
    as_of="2026-10-09",
    input_dir="data/HPG",
    output_dir="reports",
    risk="balanced",
)
if "error" in result:
    # Hiển thị result["error"] trong app
    pass
else:
    pdf_path = result["pdf_path"]
    # Đọc bytes của pdf_path và đưa vào nút tải PDF
```

run(ticker, as_of) trả dict và ghi reports/report_pdf.json, có ticker, as_of, sources và pdf_path hoặc error. Không gửi thông tin xác thực trong JSON. Không tạo lại báo cáo cho mỗi lần render giao diện; nên tạo khi bấm nút.

Quang phải gọi assess(fund, tech, news, risk=...) để có assessment phù hợp hồ sơ rủi ro trước khi gọi PDF. Hàm run của PDF chỉ hiển thị assessment, không tự định giá lại. Nếu assessment có risk_profile hoặc risk, PDF kiểm tra giá trị đó khớp risk được truyền vào. Nếu thiếu metadata này, PDF không thể xác minh đánh giá đã dùng hồ sơ nào.

Với app có nhiều người dùng: mỗi lần phân tích dùng thư mục input và output riêng (ví dụ theo UUID yêu cầu), tránh ghi đè các JSON/PDF giữa các phiên.

## Dùng dict trực tiếp

```python
from report_pdf import generate_report

pdf_path = generate_report(
    {
        "ticker": "HPG",
        "as_of": "2026-10-09",
        "risk_profile": "balanced",
        "technical": tech,
        "fundamental": fund,
        "company_news": news,
        "assessment": assessment,
    },
    "reports/HPG_2026-10-09.pdf",
    data_dir="data/HPG",
)
```

generate_report là hàm dựng PDF và có thể phát sinh exception. App dùng run để nhận error dict hoặc tự try/except khi tích hợp trực tiếp.

## Các quy ước đã xử lý

- ticker viết HOA, as_of đúng YYYY-MM-DD; đầu vào các module phải khớp nhau.
- Giá đóng cửa là giá điều chỉnh theo xác nhận của Quang.
- Giá/EPS/BVPS/cổ tức hiển thị VND/cp. Tổng tiền giữ nguyên theo unit, không tự đoán đơn vị hoặc tự chia một tỷ.
- Tỷ lệ 0.15 hiển thị 15,00%; null hiển thị “Chưa có dữ liệu”; 0 vẫn là 0. ROE/ROA/biên LN/tăng trưởng/NIM/NPL/CIR/CAR/lợi suất/upside dùng tỷ lệ thập phân.
- debt_to_equity, current_ratio và P/E, P/B hiển thị như hệ số, không đổi sang %.
- confidence nhận số thập phân (0.8 -> 80%) hoặc mô tả bằng chữ; nếu Kiều dùng thang điểm khác cần thống nhất trước.
- Sáu rating đúng quy ước nhóm; KHÔNG ĐỦ DỮ LIỆU ẩn target_price, upside và kịch bản giá, in data_gaps. PDF không sửa rating của Kiều.
- sources từ cả 4 module được gộp, bỏ bản ghi trùng và in liên kết bấm được.
- chart_path tương đối được tính từ input_dir/data_dir. Nên dùng đường dẫn tương đối để chuyển máy được. Ảnh thiếu được ghi rõ; không tạo biểu đồ giả.
- Tiêu đề có bookmark, mục lục tự cập nhật; bảng có header lặp lại nếu dài.
- PDF không có dữ liệu thị trường chung vì chưa có module tương ứng. Có ghi rõ thiếu, không tự bịa tổng quan thị trường/ngành.

## Các chi tiết JSON cần thống nhất thêm

Tên key trong dòng financials_table chưa được chỉ định trong tin nhắn nhóm. Bộ PDF dùng:

```json
{"year": 2025, "revenue": null, "net_income": null, "total_assets": null, "equity": null, "cfo": null}
```

Cũng hỗ trợ alias nam/năm, doanh_thu, lnst/LNST, tong_tai_san, vcsh/VCSH, CFO. Tất cả số tiền trong bảng dùng unit của fundamental.json.

Thêm basis vào fundamental.json để ghi “TTM”, “Năm 2025” hoặc mô tả cơ sở từng nhóm chỉ tiêu. Nếu basis không có, PDF ghi rõ cần bổ sung, không tự suy đoán. Không ghi basis=TTM cho mọi chỉ tiêu nếu BVPS hoặc ROE có cơ sở khác.

Ngân hàng: đặt NIM/NPL/CIR/CAR trong leverage hoặc banking; chấp nhận key hoa hoặc thường. NIM không phải current_ratio và không dùng biên lợi nhuận gộp thay NIM.

Các cấu trúc methods/scenarios/sensitivity/assumptions chưa có schema nội bộ hoàn chỉnh; PDF in được dict/list/text. Các giá phải đặt tên key rõ như target_price/fair_value; nếu key tùy ý thì chỉ in giá trị và nhãn gốc, không đoán đơn vị.

Ngày trong sources.date là ngày nguồn theo nhóm cung cấp; không tự dùng as_of thay ngày công bố. Ngày truy xuất có thể bổ sung sau khi nhóm chốt field.

## Nội dung bàn giao

- report_pdf.py, requirements.txt
- assets/cover.jpg (bìa trang 1); assets/background.pdf (nền từ trang mục lục trở đi)
- assets/fonts/NotoSans-Regular.ttf và NotoSans-Bold.ttf (nhúng vào PDF)
- data/: 4 JSON mẫu cấu trúc, chưa có dữ liệu thật
- reports/: PDF kiểm thử và report_pdf.json

Đã kiểm tra tỷ lệ/null/0, 6 rating, chỉ số ngân hàng, lỗi đầu vào, nguồn trùng, ngắt trang với nội dung dài và số trang mục lục. VCB/HPG/VHM thật cần chạy tiếp khi nhận JSON thật của nhóm.

## Cập nhật theo feedback thiết kế

- Bỏ nhãn “BẢN KIỂM THỬ - CHƯA CÓ DỮ LIỆU THẬT” trên bìa và nhãn kiểm thử ở chân trang. Không bỏ nội dung dữ liệu thiếu hoặc lời giải thích từ assessment.
- Trang bìa dùng nguyên ảnh cover.jpg; không đặt thêm tiêu đề/nhãn lên ảnh. Từ trang mục lục đến hết dùng background.pdf ghép dạng PDF gốc, không chụp lại thành ảnh.
- Đề mục chính chữ trắng trên nền navy; đề mục phụ màu xanh trên nền xanh nhạt.
- generated_at là thời điểm xuất thực tế theo UTC+7, có giờ/phút/giây; không thay as_of bằng giờ xuất.
- Mã và ngày dữ liệu xuất hiện ở mục lục và bảng tổng quan; mã + giờ xuất đến giây ở chân các trang nội dung. Không ghi đè thông tin lên ảnh bìa. Metadata PDF cũng lưu ticker, as_of, generated_at.
- Tên PDF dạng HPG_2026-10-09_xuat_2026-10-09_09-18-38.pdf. Giờ được tính lại mỗi lần run().
- report_pdf.json thêm generated_at dạng ISO 8601 có +07:00; các field nguồn và error giữ nguyên.
- run() mặc định xuất thời gian mới; ứng dụng nhiều phiên vẫn cần thư mục riêng để không ghi đè manifest.
