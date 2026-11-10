import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication, QSizePolicy
from src.combined_graph_view import CombinedGraphView
from src.graph_log import LogGraphWindow
from src.graph_realtime import SignalGraphWindow


class CombinedScreenshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def combined(self, realtime=False, legend_counts=(1, 1, 1, 1)):
        graphs = []
        for bus, count in enumerate(legend_counts, 1):
            names = [f'B{bus}:Signal_{index}' for index in range(count)]
            if realtime:
                graph = SignalGraphWindow(names)
                for name in names:
                    graph.update_data(name, 1, 1)
                    graph.update_data(name, 2, 3000)
                    graph.update_data(name, 3, 1)
            else:
                graph = LogGraphWindow({name: dict(times=[1, 2, 3], values=[1, 3000, 1])
                                        for name in names})
            self.addCleanup(graph.deleteLater)
            self.addCleanup(graph.close)
            graph.show()
            graphs.append(graph)
        view = CombinedGraphView(graphs, None)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.exit_combined_view)
        view.resize(1400, 1000)
        view.show()
        self.settle()
        return view

    def settle(self):
        self.app.processEvents()
        self.app.processEvents()

    def widget_states(self, view):
        return [(widget.size(), widget.minimumSize(), widget.maximumSize(), widget.sizePolicy())
                for graph in view.graphs
                for widget in (graph.graph_container, graph.plot_widget, graph.legend_widget)]

    def test_repeated_screenshots_keep_four_graphs_equal_and_restore_screen(self):
        for realtime in (False, True):
            with self.subTest(realtime=realtime):
                view = self.combined(realtime)
                before = self.widget_states(view)
                screenshots = []
                captures = []
                with ExitStack() as stack:
                    stack.enter_context(patch('src.combined_graph_view.os.makedirs'))
                    stack.enter_context(patch('src.combined_graph_view.QMessageBox.information'))
                    stack.enter_context(patch.object(
                        QPixmap, 'save', new=lambda pixmap, *_: screenshots.append(pixmap.size()) or True))
                    for graph in view.graphs:
                        original_grab = graph.graph_container.grab

                        def grab(original=original_grab, graph=graph):
                            pixmap = original()
                            captures.append((pixmap.size(), graph.plot_widget.height()))
                            return pixmap

                        stack.enter_context(patch.object(graph.graph_container, 'grab', side_effect=grab))
                    for _ in range(2):
                        view.do_screenshot()
                        self.settle()
                        self.assertEqual(self.widget_states(view), before)
                self.assertEqual(len(captures), 8)
                self.assertTrue(all(size == captures[0][0] for size, _ in captures))
                self.assertTrue(all(height == captures[0][1] for _, height in captures))
                self.assertEqual(screenshots[0], screenshots[1])
                self.assertEqual(screenshots[0].height(), 4 * captures[0][0].height())
                view.exit_combined_view()

    def test_long_legend_fits_without_changing_other_graph_heights_or_selection(self):
        view = self.combined(legend_counts=(1, 40, 2, 1))
        legend = view.graphs[1].legend_widget
        legend.item(0).setText('Very long signal name ' * 8)
        legend.item(3).setSelected(True)
        legend.item(39).setSelected(True)
        legend.scrollToBottom()
        legend.setTextElideMode(Qt.ElideMiddle)
        self.settle()
        before = self.widget_states(view)
        scroll = legend.verticalScrollBar().value()
        required_width = legend.sizeHintForColumn(0) + legend.contentsMargins().left() + legend.contentsMargins().right()
        required_height = sum(legend.sizeHintForRow(row) for row in range(legend.count()))
        pixmaps = view._capture_graph_pixmaps()
        self.settle()
        self.assertTrue(all(pixmap.size() == pixmaps[0].size() for pixmap in pixmaps))
        self.assertGreaterEqual(pixmaps[1].height(), required_height)
        self.assertGreater(pixmaps[1].width(), required_width)
        self.assertEqual(self.widget_states(view), before)
        self.assertEqual(legend.selectedItems(), [legend.item(3), legend.item(39)])
        self.assertEqual(legend.textElideMode(), Qt.ElideMiddle)
        self.assertEqual(legend.verticalScrollBar().value(), scroll)

    def test_capture_failure_restores_custom_constraints_and_selection(self):
        view = self.combined()
        graph = view.graphs[1]
        graph.plot_widget.setMinimumHeight(120)
        graph.plot_widget.setMaximumHeight(350)
        policy = graph.plot_widget.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Preferred)
        policy.setVerticalStretch(3)
        graph.plot_widget.setSizePolicy(policy)
        graph.legend_widget.item(0).setSelected(True)
        self.settle()
        before = self.widget_states(view)
        with patch.object(graph.graph_container, 'grab', side_effect=RuntimeError('capture failed')):
            with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                view._capture_graph_pixmaps()
        self.settle()
        self.assertEqual(self.widget_states(view), before)
        self.assertEqual(graph.legend_widget.selectedItems(), [graph.legend_widget.item(0)])


if __name__ == '__main__':
    unittest.main()
