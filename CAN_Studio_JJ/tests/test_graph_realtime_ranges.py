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

    def test_batch_defers_redraw_until_end(self):
        graph = self.graph('signal', [])
        graph.begin_batch()
        for timestamp in range(1, 6):
            graph.update_data('signal', timestamp, timestamp * 10)
        self.assertEqual(len(graph.curves['signal'].getData()[0] or []), 0)
        graph.end_batch()
        self.assertEqual(list(graph.curves['signal'].getData()[1]), [10, 20, 30, 40, 50])

    def test_bound_graph_receives_every_frame_between_ui_ticks(self):
        from types import SimpleNamespace
        from cantools.database.can import Message, Signal
        from src.main_window import UniversalCANMonitor

        signal = Signal('Value', start=0, length=8)
        message = Message(frame_id=0x100, name='Msg', length=8, signals=[signal])
        graph = self.graph('B1:100:0:Value', [])
        graph.signal_bindings = {'B1:100:0:Value': {'bus': 1, 'id': 0x100, 'extended': False}}
        graph._bound_signals = {'B1:100:0:Value': (message, signal)}
        # Ten 10 ms frames inside one 50 ms+ UI tick, plus an unrelated ID.
        frames = [(1.0 + i * 0.01, bytes([i, 0, 0, 0, 0, 0, 0, 0])) for i in range(10)]
        host = SimpleNamespace(active_graphs=[graph], _graph_pending_frames={
            (1, 0x100, False): frames, (1, 0x200, False): [(1.0, bytes(8))]})

        UniversalCANMonitor.update_bound_graphs(host)

        self.assertEqual(list(graph.values['B1:100:0:Value']), list(range(10)))
        self.assertEqual(host._graph_pending_frames, {})
        self.assertEqual(list(graph.curves['B1:100:0:Value'].getData()[1]), list(range(10)))

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
