import copy
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt, QPoint, QPointF
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication
from src.user_panel_v2.window import UserPanelWindow


class NestedToolDragTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(grid=dict(rows=24, cols=32), widgets=[
            dict(id='tabs', widget_type='tab_container', tabs=['A', 'B', 'C'],
                 row=1, col=1, row_span=22, col_span=28),
            dict(id='child', widget_type='label', parent_id='tabs', tab_index=1,
                 behavior='rx', row=2, col=2, row_span=3, col_span=5),
            dict(id='hidden', widget_type='group_box', parent_id='tabs', tab_index=0,
                 row=0, col=0, row_span=20, col_span=25),
        ]))
        panel.show()
        panel.activateWindow()
        self.app.processEvents()
        panel.widget_controls['tabs'].setCurrentIndex(1)
        self.app.processEvents()
        return panel

    def mouse(self, frame, kind, global_pos, modifiers=Qt.NoModifier):
        button = Qt.NoButton if kind == QMouseEvent.MouseMove else Qt.LeftButton
        buttons = Qt.NoButton if kind == QMouseEvent.MouseButtonRelease else Qt.LeftButton
        event = QMouseEvent(kind, QPointF(frame.mapFromGlobal(global_pos)), QPointF(global_pos),
                            button, buttons, modifiers)
        QApplication.sendEvent(frame, event)
        self.app.processEvents()

    def test_second_tab_stays_selected_on_click_properties_and_nudge(self):
        panel = self.panel()
        for index in (1, 2):
            with self.subTest(tab=index):
                panel._cfg_by_id('child')['tab_index'] = index
                panel.widget_controls['tabs'].setCurrentIndex(index)
                panel.rebuild_grid()
                self.app.processEvents()
                frame = panel.widget_frames['child']
                start = frame.mapToGlobal(QPoint(6, 6))
                self.mouse(frame, QMouseEvent.MouseButtonPress, start)
                self.assertEqual(panel.widget_controls['tabs'].currentIndex(), index)
                self.mouse(frame, QMouseEvent.MouseButtonRelease, start)
                editor = panel.properties.editor
                editor.edit_title.setText('Changed')
                editor.accept()
                self.app.processEvents()
                self.assertEqual(panel.widget_controls['tabs'].currentIndex(), index)
                panel.nudge_selected(1, 0)
                self.app.processEvents()
                self.assertEqual(panel.widget_controls['tabs'].currentIndex(), index)
                self.assertTrue(panel.widget_frames['child'].isVisible())

    def test_drag_preview_and_drop_match_actual_second_tab_cells(self):
        panel = self.panel()
        tabs = panel.widget_controls['tabs']
        host = tabs.widget(1).canvas
        layout = host.layout()
        frame = panel.widget_frames['child']
        start = frame.mapToGlobal(QPoint(6, 6))
        target_row, target_col = 9, 14
        self.mouse(frame, QMouseEvent.MouseButtonPress, start)
        cell = layout.cellRect(target_row, target_col)
        destination = host.mapToGlobal(cell.topLeft()) + QPoint(6, 6)
        self.mouse(frame, QMouseEvent.MouseMove, destination)
        band = panel._drag_preview_band
        self.assertIsNotNone(band)
        self.assertIs(band.parentWidget(), host)
        self.assertTrue(band.isVisible())
        self.assertEqual(band.geometry().topLeft(), cell.topLeft())
        preview = band.geometry()
        self.mouse(frame, QMouseEvent.MouseButtonRelease, destination)
        self.assertEqual(panel.widget_controls['tabs'].currentIndex(), 1)
        cfg = panel._cfg_by_id('child')
        self.assertEqual((cfg['parent_id'], cfg['tab_index'], cfg['row'], cfg['col']),
                         ('tabs', 1, target_row, target_col))
        self.assertEqual(panel.widget_frames['child'].geometry(), preview)

    def test_drag_out_of_tab_previews_on_canvas(self):
        panel = self.panel()
        frame = panel.widget_frames['child']
        self.mouse(frame, QMouseEvent.MouseButtonPress, frame.mapToGlobal(QPoint(6, 6)))
        target_row, target_col = panel.grid_rows - 5, panel.grid_cols - 7
        destination = panel.canvas.mapToGlobal(QPoint(target_col * panel.grid_cell_size + 6,
                                                     target_row * panel.grid_cell_size + 6))
        self.mouse(frame, QMouseEvent.MouseMove, destination)
        band = panel._drag_preview_band
        self.assertIs(band.parentWidget(), panel.canvas)
        self.assertTrue(band.isVisible())
        preview = band.geometry()
        self.mouse(frame, QMouseEvent.MouseButtonRelease, destination)
        cfg = panel._cfg_by_id('child')
        self.assertIsNone(cfg['parent_id'])
        self.assertEqual((cfg['row'], cfg['col']), (target_row, target_col))
        self.assertEqual(panel.widget_frames['child'].geometry(), preview)

    def test_tab_edge_drop_matches_preview_and_stays_inside_grid(self):
        panel = self.panel()
        frame = panel.widget_frames['child']
        self.mouse(frame, QMouseEvent.MouseButtonPress, frame.mapToGlobal(QPoint(2, 2)))
        host = panel.widget_controls['tabs'].widget(1).canvas
        destination = host.mapToGlobal(host.rect().bottomRight() - QPoint(1, 1))
        self.mouse(frame, QMouseEvent.MouseMove, destination)
        band = panel._drag_preview_band
        preview = band.geometry()
        self.assertIs(band.parentWidget(), host)
        self.mouse(frame, QMouseEvent.MouseButtonRelease, destination)
        cfg = panel._cfg_by_id('child')
        self.assertLessEqual(cfg['row'] + cfg['row_span'], panel.grid_rows)
        self.assertLessEqual(cfg['col'] + cfg['col_span'], panel.grid_cols)
        self.assertEqual(panel.widget_frames['child'].geometry(), preview)

    def test_nested_grid_tracks_stay_in_sync_when_canvas_grows(self):
        panel = self.panel()
        child_size = panel.widget_frames['child'].size()
        before = panel.widget_controls['tabs'].widget(1).grid.cellRect(8, 12)
        panel.resize(panel.width() + 350, panel.height() + 200)
        self.app.processEvents()
        tabs = panel.widget_controls['tabs']
        for index in range(tabs.count()):
            cell = tabs.widget(index).grid.cellRect(8, 12)
            self.assertEqual((cell.width(), cell.height()), (panel.grid_cell_size, panel.grid_cell_size))
        self.assertEqual(panel.widget_frames['child'].size(), child_size)
        panel.rebuild_grid()
        self.app.processEvents()
        self.assertEqual(panel.widget_controls['tabs'].currentIndex(), 1)
        self.assertEqual(panel.widget_controls['tabs'].widget(1).grid.cellRect(8, 12), before)
        self.assertEqual(panel.widget_frames['child'].size(), child_size)

    def test_drag_into_container_and_back_keeps_size_and_preview(self):
        for kind in ('group_box', 'tab_container'):
            with self.subTest(kind=kind):
                panel = UserPanelWindow(SimpleNamespace(), {})
                self.addCleanup(panel.close)
                panel._load_panel_data(dict(grid=dict(rows=40, cols=48), widgets=[
                    dict(id='group', widget_type=kind, tabs=['A', 'B'],
                         row=4, col=1, row_span=16, col_span=22),
                    dict(id='tool', widget_type='label', behavior='rx',
                         row=1, col=26, row_span=3, col_span=6),
                ]))
                panel.show()
                self.app.processEvents()
                index = 1 if kind == 'tab_container' else 0
                if index:
                    panel.widget_controls['group'].setCurrentIndex(index)
                    self.app.processEvents()
                frame = panel.widget_frames['tool']
                original_size = frame.size()
                start = frame.mapToGlobal(QPoint(6, 6))
                self.mouse(frame, QMouseEvent.MouseButtonPress, start)
                host = panel._container_pages[('group', index)].canvas
                cell = host.layout().cellRect(2, 3)
                destination = host.mapToGlobal(cell.topLeft() + QPoint(6, 6))
                self.mouse(frame, QMouseEvent.MouseMove, destination)
                preview = panel._drag_preview_band.geometry()
                self.assertEqual(preview.size(), original_size)
                self.mouse(frame, QMouseEvent.MouseButtonRelease, destination)
                self.assertEqual(panel._cfg_by_id('tool')['parent_id'], 'group')
                self.assertEqual(panel.widget_frames['tool'].geometry(), preview)
                self.assertEqual(panel.widget_frames['tool'].size(), original_size)
                if index:
                    self.assertEqual(panel._cfg_by_id('tool')['tab_index'], index)
                panel.undo_edit()
                self.app.processEvents()
                self.assertIsNone(panel._cfg_by_id('tool')['parent_id'])
                self.assertEqual(panel.widget_frames['tool'].size(), original_size)
                panel.redo_edit()
                self.app.processEvents()
                frame = panel.widget_frames['tool']
                self.assertEqual(frame.size(), original_size)
                self.mouse(frame, QMouseEvent.MouseButtonPress, frame.mapToGlobal(QPoint(6, 6)))
                destination = panel.canvas.mapToGlobal(QPoint(30 * panel.grid_cell_size + 6,
                                                              25 * panel.grid_cell_size + 6))
                self.mouse(frame, QMouseEvent.MouseMove, destination)
                preview = panel._drag_preview_band.geometry()
                self.assertEqual(preview.size(), original_size)
                self.mouse(frame, QMouseEvent.MouseButtonRelease, destination)
                self.assertIsNone(panel._cfg_by_id('tool')['parent_id'])
                self.assertEqual(panel.widget_frames['tool'].geometry(), preview)
                self.assertEqual(panel.widget_frames['tool'].size(), original_size)
                panel.close()

    def test_nested_small_groups_scroll_and_move_without_scaling_children(self):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(grid=dict(rows=36, cols=36), widgets=[
            dict(id='outer', widget_type='group_box', row=1, col=1, row_span=10, col_span=12),
            dict(id='inner', widget_type='group_box', parent_id='outer',
                 row=1, col=1, row_span=6, col_span=8),
            dict(id='tool', widget_type='label', behavior='rx', parent_id='inner',
                 row=4, col=7, row_span=3, col_span=6),
        ]))
        panel.show()
        self.app.processEvents()
        tool_size = panel.widget_frames['tool'].size()
        self.assertEqual((tool_size.width(), tool_size.height()), (96, 48))
        inner = panel._container_pages[('inner', 0)]
        self.assertGreater(inner.horizontalScrollBar().maximum(), 0)
        self.assertGreater(inner.verticalScrollBar().maximum(), 0)
        inner.horizontalScrollBar().setValue(inner.horizontalScrollBar().maximum())
        inner.verticalScrollBar().setValue(inner.verticalScrollBar().maximum())
        scroll_position = inner.scroll_position()
        before = panel.widget_frames['tool'].mapToGlobal(QPoint())
        panel.selected_widget_id = 'outer'
        panel.selected_widget_ids = {'outer'}
        panel.nudge_selected(2, 3)
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(panel._container_pages[('inner', 0)].scroll_position(), scroll_position)
        self.assertEqual(panel.widget_frames['tool'].size(), tool_size)
        self.assertEqual(panel.widget_frames['tool'].mapToGlobal(QPoint()) - before,
                         QPoint(2 * panel.grid_cell_size, 3 * panel.grid_cell_size))
        panel._load_panel_data(copy.deepcopy(panel._panel_data()))
        self.app.processEvents()
        self.assertEqual(panel.widget_frames['tool'].size(), tool_size)


if __name__ == '__main__':
    unittest.main()
