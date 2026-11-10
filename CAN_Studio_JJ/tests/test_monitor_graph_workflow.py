import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Database, Message, Signal
from src.main_window import UniversalCANMonitor
from src.log_viewer import LogViewerWindow
from src.session_manager import SessionManager


class MonitorGraphWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.main.session.ready = False
        self.main.session.directory = Path(self.temp.name)
        self.main.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.addCleanup(self.dispose, self.main)
        database = Database(messages=[Message(can_id, f'M{can_id:X}', 2,
            [Signal('Value', 0, 8), Signal('Status', 8, 8)]) for can_id in (0x130, 0x131)])
        self.main.install_database(database, 1, 'workflow.dbc', database.as_dbc_string().encode('cp1252'), '')
        for message in database.messages:
            self.main.add_message_to_tree(1, message.frame_id)

    def dispose(self, widget):
        widget.close()
        widget.deleteLater()

    def settle(self):
        self.app.processEvents()
        self.app.processEvents()

    def viewer(self):
        with patch.object(LogViewerWindow, 'start_parsing'):
            viewer = LogViewerWindow('unused.trc', self.main.db_messages)
        self.addCleanup(self.dispose, viewer)
        viewer.signal_data = {(1, can_id): {name: ([1, 2], [3, 4]) for name in ('Value', 'Status')}
                              for can_id in (0x130, 0x131)}
        viewer.populate_tree(viewer.signal_data)
        return viewer

    def assert_unchecked(self, tree, column):
        for index in range(tree.topLevelItemCount()):
            message = tree.topLevelItem(index)
            self.assertEqual(message.checkState(column), Qt.Unchecked)
            for row in range(message.childCount()):
                self.assertEqual(message.child(row).checkState(column), Qt.Unchecked)

    def test_main_graph_opens_selected_signals_then_clears_parent_and_child_checks(self):
        first, second = (self.main.msg_tree_items[(1, can_id)] for can_id in (0x130, 0x131))
        first.setCheckState(3, Qt.Checked)
        second.child(0).setCheckState(3, Qt.Checked)
        self.main.btn_view_graph.click()
        self.assertEqual(len(self.main.active_graphs), 1)
        graph = self.main.active_graphs[0]
        self.assertEqual([(binding['id'], binding['signal']) for binding in graph.signal_bindings.values()],
                         [(0x130, 'Value'), (0x130, 'Status'), (0x131, 'Value')])
        self.assert_unchecked(self.main.tree, 3)
        second.child(1).setCheckState(3, Qt.Checked)
        self.main.btn_view_graph.click()
        self.assertEqual(list(self.main.active_graphs[1].signal_bindings.values())[0]['signal'], 'Status')
        self.assertEqual(len(self.main.active_graphs[1].signal_names), 1)
        self.assert_unchecked(self.main.tree, 3)

    def test_log_graph_opens_selected_signals_then_clears_parent_and_child_checks(self):
        viewer = self.viewer()
        first, second = (viewer.tree.topLevelItem(index) for index in range(2))
        first.setCheckState(0, Qt.Checked)
        second.child(0).setCheckState(0, Qt.Checked)
        viewer.btn_view_graph.click()
        self.assertEqual(len(viewer.active_graphs), 1)
        self.assertEqual(viewer.active_graphs[0].signal_names,
                         ['B1:130:Value', 'B1:130:Status', 'B1:131:Value'])
        self.assert_unchecked(viewer.tree, 0)
        second.child(1).setCheckState(0, Qt.Checked)
        viewer.btn_view_graph.click()
        self.assertEqual(viewer.active_graphs[1].signal_names, ['B1:131:Status'])
        self.assert_unchecked(viewer.tree, 0)

    def test_graph_creation_failure_keeps_signal_selection(self):
        message = self.main.msg_tree_items[(1, 0x130)]
        message.setCheckState(3, Qt.Checked)
        with patch.object(self.main, '_show_realtime_graph', side_effect=RuntimeError('failed')):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                self.main.open_combined_graph()
        self.assertEqual(message.checkState(3), Qt.Checked)
        viewer = self.viewer()
        message = viewer.tree.topLevelItem(0)
        message.setCheckState(0, Qt.Checked)
        with patch('src.log_viewer.LogGraphWindow', side_effect=RuntimeError('failed')):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                viewer.open_graph()
        self.assertEqual(message.checkState(0), Qt.Checked)

    def open_mixed_graphs(self, owner, column):
        for _ in range(3):
            owner.tree.topLevelItem(0).child(0).setCheckState(column, Qt.Checked)
            owner.btn_view_graph.click()
        owner.active_graphs[-1].chk_sync.setChecked(False)
        owner.open_combined_view()
        self.settle()
        self.assertEqual(len(owner.active_graphs), 3)
        self.assertEqual(len(owner.combined_view_window.graphs), 2)
        return owner.combined_view_window

    def assert_graphs_closed(self, owner, combined):
        owner.btn_close_graphs.click()
        self.assertEqual(owner.active_graphs, [])
        self.assertEqual(owner.synced_graphs_ordered, [])
        self.assertIsNone(owner.combined_view_window)
        self.assertFalse(combined.isVisible())
        self.assertEqual(combined.proxies, [])
        # The action is also safe when there are no graphs left.
        owner.btn_close_graphs.click()

    def test_main_close_graphs_button_order_and_scope(self):
        combined = self.open_mixed_graphs(self.main, 3)
        viewer = self.viewer()
        other_combined = self.open_mixed_graphs(viewer, 0)
        other_graphs = list(viewer.active_graphs)
        toolbar = self.main.btn_view_graph.parentWidget().layout().itemAt(0).layout()
        self.assertEqual([toolbar.itemAt(index).widget() for index in range(toolbar.count() - 3, toolbar.count())],
                         [self.main.btn_close_graphs, self.main.btn_clear_data, self.main.btn_record])
        self.assertFalse(self.main.btn_close_graphs.icon().isNull())
        self.assert_graphs_closed(self.main, combined)
        self.assertEqual(viewer.active_graphs, other_graphs)
        self.assertIs(viewer.combined_view_window, other_combined)
        self.assertTrue(other_combined.isVisible())

    def test_log_close_graphs_button_is_last_and_only_closes_its_own_graphs(self):
        main_combined = self.open_mixed_graphs(self.main, 3)
        main_graphs = list(self.main.active_graphs)
        viewer, other = self.viewer(), self.viewer()
        combined = self.open_mixed_graphs(viewer, 0)
        other_combined = self.open_mixed_graphs(other, 0)
        other_graphs = list(other.active_graphs)
        toolbar = viewer.dbc_tab.layout().itemAt(0).layout()
        self.assertIs(toolbar.itemAt(toolbar.count() - 1).widget(), viewer.btn_close_graphs)
        self.assertFalse(viewer.btn_close_graphs.icon().isNull())
        self.assert_graphs_closed(viewer, combined)
        self.assertEqual(self.main.active_graphs, main_graphs)
        self.assertIs(self.main.combined_view_window, main_combined)
        self.assertTrue(main_combined.isVisible())
        self.assertEqual(other.active_graphs, other_graphs)
        self.assertIs(other.combined_view_window, other_combined)
        self.assertTrue(other_combined.isVisible())

    def receive_order(self):
        return [(int(self.main.tree.topLevelItem(index).text(0)),
                 int(self.main.tree.topLevelItem(index).text(2), 16))
                for index in range(self.main.tree.topLevelItemCount())]

    def test_rx_bus_sort_uses_can_id_for_existing_and_new_packets(self):
        for bus, can_id in ((2, 0x510), (1, 0x700), (2, 0x100), (1, 0x1000), (4, 0x200), (1, 0x002)):
            self.main.add_message_to_tree(bus, can_id)
        self.assertEqual(self.receive_order(), [(1, 0x002), (1, 0x130), (1, 0x131), (1, 0x700),
                                               (1, 0x1000), (2, 0x100), (2, 0x510), (4, 0x200)])
        self.main.add_message_to_tree(2, 0x010)
        self.assertEqual(self.receive_order()[-4:], [(2, 0x010), (2, 0x100), (2, 0x510), (4, 0x200)])
        ascending = self.receive_order()
        self.main.tree.sortByColumn(0, Qt.DescendingOrder)
        self.assertEqual(self.receive_order(), list(reversed(ascending)))

    def test_rx_name_header_sorts_names_and_bus_header_restores_grouping(self):
        self.main.msg_tree_items[(1, 0x130)].setText(3, 'Zulu')
        self.main.msg_tree_items[(1, 0x131)].setText(3, 'Alpha')
        self.main.add_message_to_tree(2, 0x100)
        self.main.msg_tree_items[(2, 0x100)].setText(3, 'Beta')
        self.main.show()
        self.settle()
        tree = self.main.tree
        header = tree.header()
        for _ in range(2):
            position = QPoint(header.sectionViewportPosition(3) + header.sectionSize(3) // 2,
                              header.height() // 2)
            QTest.mouseClick(header.viewport(), Qt.LeftButton, pos=position)
            self.settle()
            self.assertEqual(tree.sortColumn(), 3)
            descending = header.sortIndicatorOrder() == Qt.DescendingOrder
            self.assertEqual([tree.topLevelItem(index).text(3) for index in range(3)],
                             sorted(['Zulu', 'Alpha', 'Beta'], reverse=descending))
            message = self.main.msg_tree_items[(1, 0x130)]
            self.assertEqual([message.child(row).text(3).strip() for row in range(2)],
                             sorted(['Value', 'Status'], reverse=descending))
        self.main.tree.sortByColumn(0, Qt.AscendingOrder)
        self.assertEqual(self.receive_order(), [(1, 0x130), (1, 0x131), (2, 0x100)])

    def test_rx_cycle_column_sorts_numeric_values_and_reorders_updates(self):
        for can_id, cycle in ((0x400, '9.0'), (0x401, '100.0'), (0x402, '2.5'), (0x403, '-1.0')):
            self.main.add_message_to_tree(2, can_id)
            self.main.msg_tree_items[(2, can_id)].setText(7, cycle)
        self.main.tree.sortByColumn(7, Qt.AscendingOrder)
        self.assertEqual([can_id for bus, can_id in self.receive_order() if bus == 2],
                         [0x403, 0x402, 0x400, 0x401])
        self.main.msg_tree_items[(2, 0x400)].setText(7, '200.0')
        self.assertEqual([can_id for bus, can_id in self.receive_order() if bus == 2],
                         [0x403, 0x402, 0x401, 0x400])
        self.main.tree.sortByColumn(7, Qt.DescendingOrder)
        self.assertEqual([can_id for bus, can_id in self.receive_order() if bus == 2],
                         [0x400, 0x401, 0x402, 0x403])

    def test_settings_folds_resize_rx_and_preserve_user_chosen_tx_height(self):
        self.main.resize(1280, 1200)
        self.main.show()
        self.settle()
        splitter = self.main.packet_splitter
        for tx_height in (220, 330):
            splitter.setSizes([splitter.height() - tx_height, tx_height])
            self.settle()
            for group in (self.main.conn_group, self.main.db_group):
                before = splitter.sizes()
                group.header.click()
                self.settle()
                collapsed = splitter.sizes()
                self.assertEqual(collapsed[1], before[1])
                self.assertGreater(collapsed[0], before[0])
                group.header.click()
                self.settle()
                self.assertEqual(splitter.sizes(), before)
            before = splitter.sizes()
            self.main.conn_group.set_collapsed(True)
            self.main.db_group.set_collapsed(True)
            self.settle()
            self.assertEqual(splitter.sizes()[1], before[1])
            self.assertGreater(splitter.sizes()[0], before[0])
            self.main.conn_group.set_collapsed(False)
            self.main.db_group.set_collapsed(False)
            self.settle()
            self.assertEqual(splitter.sizes(), before)


if __name__ == '__main__':
    unittest.main()
