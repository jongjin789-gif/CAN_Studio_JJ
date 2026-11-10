import math
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PyQt5.QtWidgets import QApplication
from src.combined_graph_view import CombinedGraphView
from src.graph_log import LogGraphWindow


class CombinedLogRangeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def graph(self, data):
        graph = LogGraphWindow(data)
        self.addCleanup(graph.deleteLater)
        self.addCleanup(graph.close)
        graph.show()
        self.app.processEvents()
        return graph

    def combined(self, graphs):
        view = CombinedGraphView(graphs, None)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.exit_combined_view)
        view.show()
        self.app.processEvents()
        return view

    def assert_xrange(self, graphs, low, high):
        self.app.processEvents()
        for graph in graphs:
            actual = graph.plot_widget.getViewBox().viewRange()[0]
            self.assertAlmostEqual(actual[0], low, delta=0.01)
            self.assertAlmostEqual(actual[1], high, delta=0.01)

    def test_reset_and_fit_restore_common_full_log_range_and_each_y_scale(self):
        first = self.graph({'a': dict(times=[10, 50, 90], values=[1, 2, 3])})
        second = self.graph({'b': dict(times=[0, 60, 100], values=[1000, 2000, 3000])})
        combined = self.combined([first, second])
        for action in (combined.do_reset_zoom, combined.do_fit_all_data):
            with self.subTest(action=action.__name__):
                first.plot_widget.setXRange(30, 40, padding=0)
                for graph in (first, second):
                    graph.plot_widget.setYRange(5000, 9000, padding=0)
                action()
                self.assert_xrange((first, second), 0, 100)
                for graph, low, high in ((first, 1, 3), (second, 1000, 3000)):
                    y_range = graph.plot_widget.getViewBox().viewRange()[1]
                    self.assertLessEqual(y_range[0], low)
                    self.assertGreaterEqual(y_range[1], high)
                    self.assertLess(y_range[1], 5000)
        first.plot_widget.setXRange(20, 50, padding=0)
        self.assert_xrange((first, second), 20, 50)

    def test_empty_graph_does_not_hide_other_graph_data(self):
        empty = self.graph({'empty': dict(times=[], values=[])})
        filled = self.graph({'value': dict(times=[120, 180], values=[2, 4]),
                             'derived': dict(times=[100, 200], values=[3, 5])})
        combined = self.combined([empty, filled])
        for action in (combined.do_reset_zoom, combined.do_fit_all_data):
            action()
            self.assert_xrange((empty, filled), 100, 200)

    def test_single_numpy_timestamp_gets_nonzero_visible_range(self):
        graph = self.graph({'value': dict(times=np.array([123.0]), values=np.array([7.0]))})
        combined = self.combined([graph])
        for action in (combined.do_reset_zoom, combined.do_fit_all_data):
            action()
            self.app.processEvents()
            low, high = graph.plot_widget.getViewBox().viewRange()[0]
            self.assertLess(low, high)
            self.assertLessEqual(low, 123)
            self.assertGreaterEqual(high, 123)

    def test_all_empty_and_no_graphs_do_not_raise(self):
        graph = self.graph({'empty': dict(times=[], values=[])})
        for graphs in ([graph], []):
            combined = self.combined(graphs)
            combined.do_reset_zoom()
            combined.do_fit_all_data()
            for instance in graphs:
                ranges = instance.plot_widget.getViewBox().viewRange()
                self.assertTrue(all(math.isfinite(value) for axis in ranges for value in axis))


if __name__ == '__main__':
    unittest.main()
