import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from src.graph_realtime import SignalGraphWindow
from src.combined_graph_view import CombinedGraphView


class RealtimeGraphRangeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def graph(self, name, samples):
        graph = SignalGraphWindow([name])
        graph.show()
        for timestamp, value in samples:
            graph.update_data(name, timestamp, value)
        self.app.processEvents()
        self.addCleanup(graph.close)
        self.addCleanup(graph.deleteLater)
        return graph

    def test_reset_restores_recent_window_and_visible_data_y_range(self):
        graph = self.graph('signal', [(0, 10000), (70, 1), (85, 2), (100, 3)])
        graph.set_time_range(0, 100, follow_latest=False)

        graph.reset_zoom()
        self.app.processEvents()

        x_range, y_range = graph.plot_widget.getViewBox().viewRange()
        self.assertAlmostEqual(x_range[0], 70, delta=0.1)
        self.assertAlmostEqual(x_range[1], 100, delta=0.1)
        self.assertTrue(graph.btn_autoscroll.isChecked())
        self.assertTrue(graph.chk_auto_y.isChecked())
        self.assertLess(y_range[1], 10000)

    def test_auto_y_includes_new_values_beyond_previous_view(self):
        graph = self.graph('signal', [(70, 1), (85, 2), (100, 3)])
        graph.reset_zoom()
        graph.update_data('signal', 101, 100)
        self.app.processEvents()

        y_range = graph.plot_widget.getViewBox().viewRange()[1]
        self.assertGreaterEqual(y_range[1], 100)

    def test_combined_reset_uses_common_recent_window_and_fit_all_is_separate(self):
        first = self.graph('first', [(0, 1), (50, 2), (90, 3)])
        second = self.graph('second', [(5, 10), (60, 20), (100, 30)])
        combined = CombinedGraphView([first, second], None)
        self.addCleanup(combined.exit_combined_view)
        combined.show()
        self.app.processEvents()

        combined.do_reset_zoom()
        self.app.processEvents()
        for graph in (first, second):
            x_range = graph.plot_widget.getViewBox().viewRange()[0]
            self.assertAlmostEqual(x_range[0], 70, delta=0.1)
            self.assertAlmostEqual(x_range[1], 100, delta=0.1)

        combined.do_fit_all_data()
        self.app.processEvents()
        for graph in (first, second):
            x_range = graph.plot_widget.getViewBox().viewRange()[0]
            self.assertAlmostEqual(x_range[0], 0, delta=0.1)
            self.assertAlmostEqual(x_range[1], 100, delta=0.1)

        first.plot_widget.setXRange(20, 50, padding=0)
        self.app.processEvents()
        for graph in (first, second):
            x_range = graph.plot_widget.getViewBox().viewRange()[0]
            self.assertAlmostEqual(x_range[0], 20, delta=0.1)
            self.assertAlmostEqual(x_range[1], 50, delta=0.1)


if __name__ == '__main__':
    unittest.main()
