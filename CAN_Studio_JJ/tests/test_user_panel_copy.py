import copy
import os
import unittest
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt, QPoint, QPointF
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from src.user_panel_v2.window import UserPanelWindow


class ToolCopyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, configs):
        panel = UserPanelWindow(SimpleNamespace(), {})
        panel._load_panel_data(dict(widgets=configs, grid=dict(rows=20, cols=20)))
        panel.show()
        panel.activateWindow()
        self.app.processEvents()
        self.addCleanup(panel.close)
        return panel

    def cfg(self, ident, row=1, col=1, parent=None, kind='shape_rect'):
        return dict(id=ident, title=ident, behavior='none', widget_type=kind, row=row, col=col,
                    row_span=3, col_span=4, parent_id=parent,
                    binding=dict(sequence_steps=[dict(kind='DEL', delay_ms=25)]))

    def test_keyboard_multi_copy_and_independent_settings_undo(self):
        panel = self.panel([self.cfg('a'), self.cfg('b', 5, 6)])
        panel.selected_widget_ids = {'a', 'b'}
        panel.selected_widget_id = 'a'
        original = copy.deepcopy(panel.widgets_config)
        panel.canvas.setFocus()
        QTest.keyClick(panel.canvas, Qt.Key_C, Qt.ControlModifier)
        QTest.keyClick(panel.canvas, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(len(panel.widgets_config), 4)
        clones = panel.selected_configs()
        self.assertEqual(len(clones), 2)
        self.assertTrue(all(c['id'] not in {'a', 'b'} for c in clones))
        self.assertEqual(panel.widgets_config[:2], original)
        self.assertEqual((clones[1]['row'] - clones[0]['row'], clones[1]['col'] - clones[0]['col']), (4, 5))
        panel.undo_edit()
        self.assertEqual(len(panel.widgets_config), 2)
        panel.redo_edit()
        self.assertEqual(len(panel.widgets_config), 4)
        panel.widgets_config[2]['binding']['sequence_steps'][0]['delay_ms'] = 99
        self.assertEqual(panel.widgets_config[0]['binding']['sequence_steps'][0]['delay_ms'], 25)

    def test_group_clones_descendants_once_and_maps_parents(self):
        panel = self.panel([self.cfg('group', kind='group_box'), self.cfg('child', 0, 0, 'group')])
        panel.selected_widget_ids = {'group', 'child'}
        panel.selected_widget_id = 'group'
        panel.copy_selected_tools()
        panel.paste_tools()
        self.assertEqual(len(panel.widgets_config), 4)
        group, child = panel.widgets_config[2:]
        self.assertEqual(child['parent_id'], group['id'])
        self.assertEqual((child['row'], child['col']), (0, 0))
        self.assertEqual(panel.widgets_config[1]['parent_id'], 'group')

    def nested_group(self):
        group = self.cfg('group', kind='group_box')
        group.update(row_span=16, col_span=16)
        tabs = self.cfg('tabs', 0, 0, 'group', 'tab_container')
        tabs.update(row_span=12, col_span=12, tabs=['First', 'Second'])
        inner = self.cfg('inner', 0, 0, 'tabs', 'group_box')
        inner.update(tab_index=1, row_span=8, col_span=10)
        # Children precede parents to verify traversal does not depend on list order.
        return [dict(self.cfg('child', 1, 2, 'inner'), tab_index=0),
                dict(self.cfg('first', 0, 0, 'tabs'), tab_index=0), inner, tabs, group]

    def test_group_only_copy_includes_nested_and_inactive_tab_children(self):
        panel = self.panel(self.nested_group())
        panel.selected_widget_id = 'group'
        panel.selected_widget_ids = {'group'}
        original = copy.deepcopy(panel.widgets_config)
        panel.widget_controls['tabs'].setCurrentIndex(1)
        panel.canvas.setFocus()
        QTest.keyClick(panel.canvas, Qt.Key_C, Qt.ControlModifier)
        QTest.keyClick(panel.canvas, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(len(panel.widgets_config), 10)
        self.assertEqual(panel.widgets_config[:5], original)
        clones = {cfg['title'].removesuffix(' (복사)'): cfg for cfg in panel.widgets_config[5:]}
        for source in original:
            clone = clones[source['title']]
            self.assertNotEqual(clone['id'], source['id'])
            self.assertEqual(clone['binding'], source['binding'])
            if source['parent_id']:
                self.assertEqual(clone['parent_id'], clones[source['parent_id']]['id'])
                self.assertEqual((clone['row'], clone['col']), (source['row'], source['col']))
            self.assertEqual(clone.get('tab_index'), source.get('tab_index'))
        first = clones['first']
        cloned_tabs = panel.widget_controls[clones['tabs']['id']]
        self.assertEqual(cloned_tabs.currentIndex(), 1)
        self.assertTrue(cloned_tabs.widget(0).isAncestorOf(panel.widget_frames[first['id']]))
        self.assertTrue(cloned_tabs.widget(1).isAncestorOf(panel.widget_frames[clones['inner']['id']]))

    def test_keyboard_cut_group_to_other_page_preserves_tree_and_undo_redo(self):
        for host_name in ('canvas', 'list_tools'):
            with self.subTest(host=host_name):
                panel = self.panel(self.nested_group() + [self.cfg('keep')])
                panel.pages.append(dict(id='other', name='Other'))
                panel._sync_page_tabs()
                panel.selected_widget_id = 'group'
                panel.selected_widget_ids = {'group'}
                panel._refresh_selection_ui()
                panel._reset_history()
                original = copy.deepcopy(panel.widgets_config)
                panel.widget_controls['tabs'].setCurrentIndex(1)
                host = getattr(panel, host_name)
                host.setFocus()
                self.app.processEvents()
                QTest.keyClick(host, Qt.Key_X, Qt.ControlModifier)
                self.assertEqual([cfg['id'] for cfg in panel.widgets_config], ['keep'])
                self.assertEqual(len(panel._undo_stack), 1)
                panel.page_tabs.setCurrentIndex(1)
                host.setFocus()
                self.app.processEvents()
                QTest.keyClick(host, Qt.Key_V, Qt.ControlModifier)
                self.assertEqual(len(panel.widgets_config), 6)
                for source in original[:5]:
                    moved = panel._cfg_by_id(source['id'])
                    for key in ('title', 'binding', 'parent_id', 'row', 'col', 'tab_index'):
                        self.assertEqual(moved.get(key), source.get(key), key)
                    self.assertEqual(moved['page_id'], 'other')
                self.assertFalse(panel._tool_clipboard)
                self.assertEqual(panel.widget_controls['tabs'].currentIndex(), 1)
                self.assertEqual(len(panel._undo_stack), 2)
                panel.undo_edit()
                self.assertEqual([cfg['id'] for cfg in panel.widgets_config], ['keep'])
                panel.undo_edit()
                self.assertEqual(panel.widgets_config, original)
                panel.redo_edit()
                panel.redo_edit()
                self.assertEqual(panel._cfg_by_id('group')['page_id'], 'other')
                panel.paste_tools()
                self.assertEqual(len(panel.widgets_config), 6)
                panel.close()

    def test_cut_child_to_other_page_detaches_external_parent(self):
        panel = self.panel([self.cfg('group', kind='group_box'), self.cfg('child', 0, 0, 'group')])
        panel.selected_widget_id = 'child'
        panel.selected_widget_ids = {'child'}
        panel.act_cut.trigger()
        panel.pages.append(dict(id='other', name='Other'))
        panel._sync_page_tabs()
        panel.page_tabs.setCurrentIndex(1)
        panel.act_paste.trigger()
        self.assertIsNone(panel._cfg_by_id('child')['parent_id'])
        self.assertEqual(panel._cfg_by_id('child')['page_id'], 'other')
        self.assertEqual(panel._cfg_by_id('group')['page_id'], 'default')

    def test_undo_cut_before_paste_does_not_overwrite_restored_sources(self):
        panel = self.panel([self.cfg('group', kind='group_box'), self.cfg('child', 0, 0, 'group')])
        original = copy.deepcopy(panel.widgets_config)
        panel.cut_selected_tools()
        self.assertEqual(panel.widgets_config, [])
        panel.undo_edit()
        panel.paste_tools()
        self.assertEqual(panel.widgets_config[:2], original)
        self.assertEqual(len({cfg['id'] for cfg in panel.widgets_config}), 4)
        clone_group, clone_child = panel.widgets_config[2:]
        self.assertEqual(clone_child['parent_id'], clone_group['id'])

    def test_cut_is_edit_only_and_properties_text_uses_text_clipboard(self):
        panel = self.panel([self.cfg('a')])
        text = panel.properties.editor.edit_title
        text.setFocus()
        text.selectAll()
        self.app.processEvents()
        QTest.keyClick(text, Qt.Key_X, Qt.ControlModifier)
        self.assertEqual(text.text(), '')
        self.assertEqual(len(panel.widgets_config), 1)
        self.assertFalse(panel._tool_clipboard)
        QTest.keyClick(text, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(text.text(), 'a')
        panel.copy_selected_tools()
        clipboard = copy.deepcopy(panel._tool_clipboard)
        panel.set_mode('standby')
        panel.cut_selected_tools()
        panel.paste_tools()
        self.assertEqual(len(panel.widgets_config), 1)
        self.assertEqual(panel._tool_clipboard, clipboard)
        self.assertFalse(panel.act_cut.isEnabled())

    def test_ctrl_drag_copies_selection_without_moving_originals(self):
        panel = self.panel([self.cfg('a'), self.cfg('b', 5, 6)])
        panel.selected_widget_ids = {'a', 'b'}
        panel.selected_widget_id = 'a'
        original = copy.deepcopy(panel.widgets_config)
        frame = panel.widget_frames['a']
        start = QPoint(20, 20)
        end = start + QPoint(64, 64)
        for kind, pos, button, buttons in [
            (QMouseEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
            (QMouseEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
            (QMouseEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton),
        ]:
            event = QMouseEvent(kind, QPointF(pos), QPointF(frame.mapToGlobal(pos)), button, buttons, Qt.ControlModifier)
            QApplication.sendEvent(frame, event)
        self.assertEqual(len(panel.widgets_config), 4)
        self.assertEqual(panel.widgets_config[:2], original)
        self.assertEqual(len(panel.selected_configs()), 2)
        self.assertGreater(panel.widgets_config[2]['row'], original[0]['row'])

    def test_copy_blocked_outside_edit_and_list_shortcuts(self):
        panel = self.panel([self.cfg('a')])
        panel.list_tools.setFocus()
        QTest.keyClick(panel.list_tools, Qt.Key_C, Qt.ControlModifier)
        QTest.keyClick(panel.list_tools, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(len(panel.widgets_config), 2)
        panel.set_mode('standard')
        panel.paste_tools()
        self.assertEqual(len(panel.widgets_config), 2)

    def test_drag_moves_selection_together_and_undoes_once(self):
        panel = self.panel([self.cfg('a'), self.cfg('b', 5, 6)])
        panel.selected_widget_ids = {'a', 'b'}
        panel.selected_widget_id = 'a'
        original = copy.deepcopy(panel.widgets_config)
        frame = panel.widget_frames['a']
        start = QPoint(20, 20)
        end = start + QPoint(64, 64)
        for kind, pos, button, buttons in [
            (QMouseEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
            (QMouseEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
            (QMouseEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton),
        ]:
            event = QMouseEvent(kind, QPointF(pos), QPointF(frame.mapToGlobal(pos)),
                                button, buttons, Qt.NoModifier)
            QApplication.sendEvent(frame, event)
        a, b = panel.widgets_config
        self.assertEqual(len(panel.selected_configs()), 2)
        self.assertGreater(a['row'], original[0]['row'])
        self.assertEqual((b['row'] - a['row'], b['col'] - a['col']), (4, 5))
        panel.undo_edit()
        self.assertEqual(panel.widgets_config, original)
        panel.redo_edit()
        self.assertGreater(panel.widgets_config[0]['row'], original[0]['row'])

    def test_multi_nudge_boundary_and_selected_group_child(self):
        panel = self.panel([self.cfg('a'), self.cfg('b', 5, 6),
                            self.cfg('child', 0, 0, 'a')])
        panel.selected_widget_ids = {'a', 'b', 'child'}
        panel.selected_widget_id = 'a'
        panel.nudge_selected(-10, -10)
        self.assertEqual([(c['row'], c['col']) for c in panel.widgets_config],
                         [(0, 0), (4, 5), (0, 0)])
        panel.nudge_selected(1, 1)
        self.assertEqual([(c['row'], c['col']) for c in panel.widgets_config],
                         [(1, 1), (5, 6), (0, 0)])

    def test_copied_tx_can_bind_other_bus_without_changing_source(self):
        from src.user_panel_v2.packets import bind_packet
        p = dict(packet_id='p', bus=1, id=0x123, length=8, data=[0]*8,
                 cycle=10, is_fd=False, is_brs=False)
        q = dict(p, packet_id='q', bus=2)
        cfg = self.cfg('a', kind='spinbox')
        cfg.update(behavior='tx')
        cfg['binding'] = dict(start_bit=0, bit_length=8)
        bind_packet(cfg['binding'], p)
        panel = self.panel([])
        panel._load_panel_data(dict(widgets=[cfg], tx_packets=[p, q]))
        panel.copy_selected_tools()
        panel.paste_tools()
        editor = panel.properties.editor
        editor.combo_bus.setCurrentText('2')
        editor.accept()
        original, cloned = panel.widgets_config
        self.assertEqual(original['binding']['packet_id'], 'p')
        self.assertEqual(cloned['binding']['packet_id'], 'q')
        self.assertEqual(cloned['binding']['bus'], 2)
