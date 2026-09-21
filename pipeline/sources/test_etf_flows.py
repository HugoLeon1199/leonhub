"""Regression cases for the publisher's mixed-case Total column."""
import unittest
from datetime import datetime, timezone
from pipeline.sources.etf_flows import parse


class FlowTableTests(unittest.TestCase):
    def rows(self, cells):
        table = '<tr><td></td><td>ETHA</td><td>FETH</td><td>ETHW</td><td>Total</td></tr>'
        table += '<tr><td>18 Sep 2026</td>' + ''.join(f'<td>{v}</td>' for v in cells) + '</tr>'
        return parse(table, 'eth', datetime.now(timezone.utc))

    def test_total_header_and_official_net(self):
        net = [r for r in self.rows(['10', '(5)', '-', '5.1']) if r['series'].endswith('net_flow')]
        self.assertEqual([r['value'] for r in net], [5.1])

    def test_published_zero_is_not_missing(self):
        net = [r for r in self.rows(['-', '-', '-', '0.0']) if r['series'].endswith('net_flow')]
        self.assertEqual([r['value'] for r in net], [0.0])

    def test_incomplete_total_is_not_zero_or_partial_sum(self):
        net = [r for r in self.rows(['10', '-', '-', '-']) if r['series'].endswith('net_flow')]
        self.assertEqual(net, [])


if __name__ == '__main__':
    unittest.main()
