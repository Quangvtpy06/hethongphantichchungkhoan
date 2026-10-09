@echo off
cd /d "%~dp0"
py report_pdf.py --ticker HPG --as-of 2026-10-09 --demo
if errorlevel 1 (
  echo Loi chay. Xem thong bao phia tren.
  echo Neu chua cai thu vien, chay: py -m pip install -r requirements.txt
) else (
  py -c "import json,os; from pathlib import Path; r=json.loads(Path('reports/report_pdf.json').read_text(encoding='utf-8')); os.startfile(r['pdf_path'])"
)
pause
