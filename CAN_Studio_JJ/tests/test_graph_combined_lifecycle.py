import os
import unittest
from contextlib import ExitStack
from types import MethodType, SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5 import sip
from PyQt5.QtWidgets import QApplication
from src.combined_graph_view import CombinedGraphView
from src.graph_log import LogGraphWindow
from src.graph_realtime import SignalGraphWindow
from src.log_viewer import LogViewerWindow
from src.main_window import UniversalCANMonitor


class CombinedGraphLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def owner(self, realtime):
        owner = SimpleNamespace(active_graphs=[], synced_graphs_ordered=[], combined_view_window=None)
        handler = UniversalCANMonitor.handle_sync_toggle if realtime else LogViewerWindow.handle_sync_toggle
        owner.handle_sync_toggle = MethodType(handler, owner)
        return owner

    def dispose(self, widget):
        if not sip.isdeleted(widget):
            widget.close()
            widget.deleteLater()

    def graph(self, owner, realtime, name):
        if realtime:
            graph = SignalGraphWindow([name], main_window=owner)
        else:
            graph = LogGraphWindow({name: dict(times=[1, 2], values=[0, 1])}, viewer_window=owner)
        self.addCleanup(self.dispose, graph)
        owner.active_graphs.append(graph)
        graph.show()
        self.app.processEvents()
        return graph

    def combined(self, owner):
        view = CombinedGraphView(owner.synced_graphs_ordered, owner)
        self.addCleanup(self.dispose, view)
        owner.combined_view_window = view
        view.show()
        self.app.processEvents()
        return view

    def test_new_graphs_default_to_sync_and_close_unregisters_them(self):
        for realtime in (False, True):
            with self.subTest(realtime=realtime):
                owner = self.owner(realtime)
                graphs = [self.graph(owner, realtime, str(i)) for i in range(3)]
                self.assertTrue(all(graph.chk_sync.isChecked() for graph in graphs))
                self.assertEqual(owner.synced_graphs_ordered, graphs)
                graphs[1].chk_sync.setChecked(False)
                self.assertEqual(owner.synced_graphs_ordered, [graphs[0], graphs[2]])
                graphs[1].chk_sync.setChecked(True)
                self.assertEqual(owner.synced_graphs_ordered, [graphs[0], graphs[2], graphs[1]])
                graphs[1].close()
                self.assertEqual(owner.active_graphs, [graphs[0], graphs[2]])
                self.assertEqual(owner.synced_graphs_ordered, [graphs[0], graphs[2]])

    def assert_graph_shutdown(self, action):
        for realtime in (False, True):
            with self.subTest(realtime=realtime):
                owner = self.owner(realtime)
                graphs = [self.graph(owner, realtime, str(i)) for i in range(3)]
                outside = self.graph(owner, realtime, 'outside')
                outside.chk_sync.setChecked(False)
                view = self.combined(owner)
                self.assertFalse(view.btn_close_all.icon().isNull())
                self.assertFalse(view.btn_exit_combined.icon().isNull())
                toolbar = view.layout().itemAt(0).layout()
                self.assertIs(toolbar.itemAt(toolbar.count() - 2).widget(), view.btn_exit_combined)
                self.assertIs(toolbar.itemAt(toolbar.count() - 1).widget(), view.btn_close_all)
                self.assertGreater(view.btn_exit_combined.x(), view.btn_screenshot.geometry().right())
                self.assertGreater(view.btn_close_all.x(), view.btn_exit_combined.geometry().right())
                self.assertEqual(view.btn_close_all.geometry().right(),
                                 view.width() - view.layout().contentsMargins().right() - 1)
                with ExitStack() as stack:
                    show_calls = [stack.enter_context(patch.object(graph, 'show')) for graph in graphs]
                    close_calls = [stack.enter_context(patch.object(graph, 'close', wraps=graph.close))
                                   for graph in graphs]
                    if action == 'titlebar':
                        view.close()
                    else:
                        view.btn_close_all.click()
                    for show, close in zip(show_calls, close_calls):
                        show.assert_not_called()
                        close.assert_called_once_with()
                self.assertEqual(owner.active_graphs, [outside])
                self.assertEqual(owner.synced_graphs_ordered, [])
                self.assertIsNone(owner.combined_view_window)
                self.assertFalse(view.isVisible())
                self.assertTrue(outside.isVisible())
                self.assertEqual(view.proxies, [])
                self.assertEqual(view._xrange_slots, [])
                outside.chk_sync.setChecked(True)
                reopened = self.combined(owner)
                self.assertEqual(reopened.graphs, [outside])
                reopened.close()

    def test_graph_exit_button_closes_every_member_without_showing_individual_windows(self):
        self.assert_graph_shutdown('button')

    def test_titlebar_close_closes_every_member_without_showing_individual_windows(self):
        self.assert_graph_shutdown('titlebar')

    def test_exit_combined_button_restores_individual_windows_and_sync_membership(self):
        for realtime in (False, True):
            with self.subTest(realtime=realtime):
                owner = self.owner(realtime)
                graphs = [self.graph(owner, realtime, str(i)) for i in range(3)]
                view = self.combined(owner)
                view.btn_exit_combined.click()
                self.app.processEvents()
                self.assertTrue(all(graph.isVisible() for graph in graphs))
                self.assertTrue(all(not graph.is_in_combined_view for graph in graphs))
                self.assertTrue(all(graph.graph_container.parentWidget() is graph for graph in graphs))
                self.assertEqual(owner.active_graphs, graphs)
                self.assertEqual(owner.synced_graphs_ordered, graphs)
                self.assertIsNone(owner.combined_view_window)
                self.assertEqual(view.proxies, [])
                self.assertEqual(view._xrange_slots, [])


if __name__ == '__main__':
    unittest.main()
