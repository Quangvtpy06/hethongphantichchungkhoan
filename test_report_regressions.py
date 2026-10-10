"""Kiểm tra ngoại tuyến: dùng ảnh/dữ liệu kiểm thử, không gọi API thị trường."""
import json
import unittest
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch

import report_pdf
import valuation
from report_support import internal_reference, risk_flags, reference_source, billion_value, financial_unit, financial_basis

BASE = Path(__file__).resolve().parent


class ReportRegressionTests(unittest.TestCase):
    def test_financial_units_and_legacy_metadata(self):
        for value, unit, expected in [(7772000000000, 'VND', 7772), (7772, 'tỷ VND', 7772), (7772000, 'triệu VND', 7772), (0, 'VND', 0), (-2e9, 'VND', -2)]:
            self.assertAlmostEqual(billion_value(value, unit), expected)
        self.assertIsNone(billion_value(None, 'VND'))
        self.assertIsNone(billion_value(100, 'USD'))
        legacy = json.loads((BASE/'fundamental.json').read_text(encoding='utf-8'))
        legacy.pop('unit', None)
        self.assertEqual(financial_unit(legacy), 'VND')
        self.assertIn('12 tháng', financial_basis(legacy))
        self.assertIn(legacy['latest_period'], financial_basis(legacy))

    def test_financial_chart_profit_alias_and_integer_labels(self):
        report_pdf.setup_fonts()
        rows = [dict(year='2024', revenue=1.6e9, net_profit=0), dict(year='2025', revenue=2.7e9, net_profit=-1.2e9)]
        chart = report_pdf.financial_charts(rows, 'VND')[1]
        from reportlab.graphics.shapes import Drawing, String
        drawings = [item for item in chart._cellvalues[0] if isinstance(item, Drawing)]
        self.assertEqual(len(drawings), 2)
        labels = [item.text for drawing in drawings for item in drawing.contents if isinstance(item, String)]
        for expected in ('2', '3', '0', '-1', 'Lợi nhuận sau thuế'):
            self.assertIn(expected, labels)

    def test_pipeline_overwrites_old_json_after_provider_failure(self):
        import pipeline
        root = BASE/'tmp/pdfs/pipeline-regression'
        out = root/'outputs'
        out.mkdir(parents=True, exist_ok=True)
        for name in report_pdf.MODULES:
            (out/(name+'.json')).write_text('{"ticker":"OLD"}', encoding='utf-8')
        def check_pdf(**kwargs):
            for name in report_pdf.MODULES:
                current = json.loads((out/(name+'.json')).read_text(encoding='utf-8'))
                self.assertEqual(current['ticker'], 'SSI')
            return {'pdf_path': str(root/'test.pdf')}
        with patch.object(pipeline, 'BASE_DIR', root), \
             patch.object(pipeline.technical, 'run', side_effect=RuntimeError('test unavailable')), \
             patch.object(pipeline.fundamental, 'run', side_effect=RuntimeError('test unavailable')), \
             patch.object(pipeline.company_news, 'run', side_effect=RuntimeError('test unavailable')), \
             patch.object(pipeline.report_pdf, 'run', side_effect=check_pdf):
            result = pipeline.run_pipeline('SSI', '2026-10-10', output_dir=str(out), reports_dir=str(root/'reports'))
        self.assertEqual(result['status'], 'partial')

    def test_legacy_and_absolute_chart_paths(self):
        with nullcontext(BASE/'tmp/pdfs/path-regression') as tmp:
            root = Path(tmp)
            chart = root / 'outputs/charts/ABC.png'
            chart.parent.mkdir(parents=True, exist_ok=True)
            chart.write_bytes(b'test path resolution')
            for value in ('outputs/charts/ABC.png', 'charts/ABC.png', str(chart)):
                with self.subTest(path=value):
                    self.assertEqual(Path(report_pdf.technical_image({'chart_path': value}, root/'outputs')), chart)

    def test_history_renders_without_chart_file(self):
        data = json.loads((BASE/'technical.json').read_text(encoding='utf-8'))
        data['chart_path'] = 'missing.png'
        stream = report_pdf.technical_image(data, BASE)
        self.assertEqual(stream.read(8), b'\x89PNG\r\n\x1a\n')

    def test_risks_apply_to_every_sector_and_preserve_sources(self):
        for ticker, sector in [('SSI', 'Chứng khoán'), ('VND', 'Chứng khoán'), ('ACB', 'Ngân hàng'), ('VHM', 'Bất động sản'), ('FPT', 'Công nghệ'), ('HPG', 'Thép'), ('ABC', 'Khác')]:
            fund = dict(ticker=ticker, sector=sector, growth={'net_income_yoy': -.26})
            news = {'risks': [{'summary': 'Cảnh báo từ nguồn', 'source': 'Nguồn kiểm thử'}]}
            flags = risk_flags(fund, {}, news, '2026-10-10')
            with self.subTest(ticker=ticker):
                self.assertTrue(any('26.0%' in f['text'] for f in flags))
                self.assertTrue(any(f['category'] == 'sector_watch' for f in flags))
                self.assertTrue(any(f['source'] == 'Nguồn kiểm thử' for f in flags))
                self.assertEqual(flags, risk_flags(fund, {}, dict(news, risk_flags=flags), '2026-10-10'))

    def test_reference_does_not_mutate_defaults(self):
        ref = {'pe': {'p25': 8, 'median': 10, 'p75': 12}}
        annotated = internal_reference(ref, 'Khác')
        annotated['pe']['median'] = 99
        self.assertEqual(ref['pe']['median'], 10)
        self.assertNotIn('source', ref)
        self.assertIn('chưa', reference_source({}))

    def test_risk_profiles_and_loss_making_company(self):
        fund = json.loads((BASE/'fundamental.json').read_text(encoding='utf-8'))
        tech = json.loads((BASE/'technical.json').read_text(encoding='utf-8'))
        fund['valuation_ref'] = internal_reference({'pe': {'p25': 11, 'median': 14.5, 'p75': 18.5}, 'pb': {'p25': 1.4, 'median': 1.8, 'p75': 2.4}}, fund['sector'])
        for profile, weights in valuation.RISK_SCEN_W.items():
            result = valuation.assess(fund, tech, {}, risk=profile)
            self.assertTrue(result['risks'])
            self.assertEqual(result['scenarios']['bear']['probability'], weights['bear'])
            self.assertIn(f"{weights['bear']:.0%}", result['reasons'][0])
        fund['per_share']['eps_ttm'] = -100
        result = valuation.assess(fund, tech, {})
        self.assertEqual([m['kind'] for m in result['methods']], ['PB'])
        self.assertIsNone(valuation.assess({}, {}, {})['target_price'])


if __name__ == '__main__':
    unittest.main()
