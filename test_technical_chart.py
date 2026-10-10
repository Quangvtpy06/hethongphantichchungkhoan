import json
import unittest
from pathlib import Path
import pandas as pd
from technical import _calc_rsi
from technical_chart import create_chart, CHART_CONFIG


def theme(fig, height):
    return fig.update_layout(template='plotly_white', height=height)


class TechnicalChartTests(unittest.TestCase):
    def test_real_history_and_matching_indicators(self):
        data = json.loads((Path(__file__).parent/'technical.json').read_text(encoding='utf-8'))
        fig = create_chart(data, theme)
        self.assertEqual(fig.data[0].type, 'candlestick')
        self.assertEqual(list(fig.data[0].close), [row['close'] for row in data['price_history']])
        self.assertAlmostEqual(fig.data[-1].y[-1], data['rsi14'], delta=.11)
        self.assertEqual(list(fig.layout.meta['priceLevels']), [data['support'], data['resistance']])
        self.assertEqual(fig.layout.dragmode, 'pan')
        self.assertTrue(CHART_CONFIG['scrollZoom'])
        self.assertEqual(list(fig.layout.yaxis3.range), [0,100])
        self.assertFalse(fig.layout.xaxis.rangeslider.visible)

    def test_rsi_edge_cases_and_warmup(self):
        self.assertTrue(_calc_rsi(pd.Series(range(10))).isna().all())
        for values, expected in [(range(30),100), (range(30,0,-1),0), ([10]*30,50)]:
            self.assertEqual(_calc_rsi(pd.Series(values)).iloc[-1], expected)

    def test_empty_and_short_history(self):
        self.assertEqual(len(create_chart({}, theme).data), 0)
        history = [dict(date='2026-10-09', open=10, high=12, low=9, close=11)]
        fig = create_chart({'price_history':history}, theme)
        self.assertEqual(fig.data[0].close[0],11)
        self.assertIsNone(fig.data[-1].y[0])
        self.assertEqual(fig.layout.meta['priceLevels'],[])


if __name__ == '__main__':
    unittest.main()
