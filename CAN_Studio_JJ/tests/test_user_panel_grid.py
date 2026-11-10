import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Database, Message, Signal
from cantools.database.conversion import BaseConversion

from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.user_panel_v2.binding import validate_config
from src.user_panel_v2.config_dialog import WidgetConfigDialog, minimum_tool_spans
from src.user_panel_v2.properties import ToolProperties
from src.user_panel_v2.rx_grid import GridSignalDialog, RxGridControl, signal_key, grid_minimum_size
from src.user_panel_v2.storage import load_bundle, save_bundle
from src.user_panel_v2.window import UserPanelWindow


class GridTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def database(self):
        return {1: {
            0x130: Message(0x130, 'Voltage', 3, [
                Signal('Value', 0, 16, is_signed=True, unit='V',
                       conversion=BaseConversion.factory(scale=0.5, offset=-10)),
                Signal('State', 16, 8, conversion=BaseConversion.factory(choices={1: 'Ready'}))]),
            0x131: Message(0x131, 'Temperature', 1, [Signal('Value', 0, 8, unit='C')])},
            2: {0x130: Message(0x130, 'OtherVoltage', 1, [Signal('Value', 0, 8, unit='V')])}}

    def entry(self, bus=1, can_id=0x130, name='Value'):
        message = self.database()[bus][can_id]
        return dict(bus=bus, can_id=can_id, signal_name=name, symbol_name=message.name,
                    unit=message.get_signal_by_name(name).unit or '')

    def config(self, entries=None):
        return dict(id='grid', title='Monitor', widget_type='grid_view', behavior='rx',
                    row=0, col=0, row_span=10, col_span=28,
                    binding=dict(grid_signals=entries if entries is not None else [self.entry()]))

    def panel(self, config=None, main=None, db=None):
        panel = UserPanelWindow(main or SimpleNamespace(), db or self.database())
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(widgets=[copy.deepcopy(config or self.config())],
                                   grid=dict(rows=24, cols=36)))
        return panel

    def test_tree_checks_append_in_click_order_and_rechecked_signals_move_to_end(self):
        dialog = GridSignalDialog(self.database())
        self.addCleanup(dialog.deleteLater)
        state = dialog.items[(1, 0x130, 'State')]
        value = dialog.items[(1, 0x130, 'Value')]
        other = dialog.items[(2, 0x130, 'Value')]
        state.setCheckState(0, Qt.Checked)
        self.assertEqual(value.parent().checkState(0), Qt.PartiallyChecked)
        other.setCheckState(0, Qt.Checked)
        value.parent().setCheckState(0, Qt.Checked)
        self.assertEqual([signal_key(e) for e in dialog.selected_signals()],
                         [(1, 0x130, 'State'), (2, 0x130, 'Value'), (1, 0x130, 'Value')])
        self.assertEqual(value.text(3), '3')
        state.setCheckState(0, Qt.Unchecked)
        state.setCheckState(0, Qt.Checked)
        self.assertEqual([e['signal_name'] for e in dialog.selected_signals()], ['Value', 'Value', 'State'])
        value.parent().setCheckState(0, Qt.Unchecked)
        self.assertEqual([signal_key(e) for e in dialog.selected_signals()], [(2, 0x130, 'Value')])
        dialog.clear_selection()
        self.assertEqual(dialog.selected_signals(), [])

    def test_config_tree_dialog_apply_and_package_keep_order(self):
        entries = [self.entry(2), self.entry(name='State'), self.entry()]
        editor = WidgetConfigDialog(self.database(), fixed_behavior='rx')
        self.addCleanup(editor.deleteLater)
        editor.combo_widget_type.setCurrentText('grid_view')

        def choose(dialog):
            for entry in entries:
                dialog.items[signal_key(entry)].setCheckState(0, Qt.Checked)
            return dialog.Accepted

        with patch.object(GridSignalDialog, 'exec_', choose):
            editor.btn_grid_signals.click()
        config = editor.get_config()
        validate_config(config)
        self.assertEqual(config['binding']['grid_signals'], entries)
        panel = self.panel(config)
        self.assertEqual(ToolProperties.bound_buses(config), {1, 2})
        with tempfile.TemporaryDirectory() as directory:
            filename = save_bundle(str(Path(directory) / 'grid'), panel._panel_data(), {})
            with patch('src.user_panel_v2.storage.tempfile.mkdtemp', return_value=directory):
                data, _ = load_bundle(filename)
            panel._load_panel_data(data)
        self.assertEqual(panel.properties.editor.grid_signals, entries)
        control = next(iter(panel.widget_controls.values()))
        self.assertEqual([control.table.item(row, 0).text() for row in range(3)], ['BUS2', 'BUS1', 'BUS1'])
        self.assertEqual([control.table.item(row, 2).text() for row in range(3)], ['Value', 'State', 'Value'])

    def test_receive_uses_bus_and_id_and_dbc_physical_enum_values(self):
        entries = [self.entry(2), self.entry(can_id=0x131), self.entry(), self.entry(name='State')]
        panel = self.panel(self.config(entries))
        control = panel.widget_controls['grid']
        self.assertIsInstance(control, RxGridControl)
        message = panel.db_messages[1][0x130]
        payload = message.encode(dict(Value=-25.5, State=1))
        panel.on_message_update(1, 0x130, payload, 0)
        self.assertEqual(control.table.item(2, 3).text(), '-')
        panel.set_mode('standby')
        panel.on_message_update(1, 0x130, payload, 1)
        panel.on_message_update(1, 0x131, bytes([40]), 1)
        panel.on_message_update(2, 0x130, bytes([75]), 1)
        panel.on_signal_update(1, 'Value', 999, '', 1)
        self.assertEqual([control.table.item(row, 3).text() for row in range(4)],
                         ['75 V', '40 C', '-25.5 V', 'Ready (1)'])
        with patch.object(message, 'decode', wraps=message.decode) as decode:
            panel.on_message_update(1, 0x130, payload, 2)
            decode.assert_not_called()

    def test_properties_apply_and_copy_keep_row_order_and_independent_settings(self):
        entries = [self.entry(), self.entry(2)]
        panel = self.panel(self.config(entries))
        editor = panel.properties.editor
        editor.grid_signals.reverse()
        editor.accept()
        self.assertEqual(panel.widgets_config[0]['binding']['grid_signals'], entries[::-1])
        self.assertEqual(panel.widget_controls['grid'].table.item(0, 0).text(), 'BUS2')
        panel.copy_selected_tools()
        panel.paste_tools()
        self.assertEqual(len(panel.widgets_config), 2)
        cloned = panel.widgets_config[1]
        self.assertEqual(cloned['binding']['grid_signals'], entries[::-1])
        cloned['binding']['grid_signals'][0]['signal_name'] = 'Changed'
        self.assertEqual(panel.widgets_config[0]['binding']['grid_signals'][0]['signal_name'], 'Value')

    def test_enum_uses_signed_raw_value_before_scale_and_offset(self):
        choices = {2: 'Run', 11: 'PhysicalCollision', -2: 'Reverse', 9: 'RawNine', 10: 'RawTen'}
        for byte_order, start in (('little_endian', 0), ('big_endian', 7)):
            with self.subTest(byte_order=byte_order):
                signal = Signal('State', start, 16, byte_order=byte_order, is_signed=True, unit='V',
                                conversion=BaseConversion.factory(scale=0.5, offset=10, choices=choices))
                message = Message(0x200, 'ScaledEnum', 2, [signal])
                entries = [dict(bus=1, can_id=0x200, signal_name='State')]
                panel = self.panel(self.config(entries), db={1: {0x200: message}})
                panel.set_mode('standby')
                for raw, expected in ((2, 'Run (11 V)'), (-2, 'Reverse (9 V)'),
                                      (11, 'PhysicalCollision (15.5 V)'), (0, '10 V')):
                    payload = message.encode({'State': raw}, scaling=False)
                    panel.on_message_update(1, 0x200, payload, 0)
                    self.assertEqual(panel.widget_controls['grid'].table.item(0, 3).text(), expected)

    def test_enum_keeps_large_raw_integer_identity_without_float_round_trip(self):
        raw = (1 << 60) + 1
        signal = Signal('State', 0, 64, conversion=BaseConversion.factory(
            scale=0.5, offset=10, choices={raw: 'ExactRaw'}))
        message = Message(0x200, 'LargeEnum', 8, [signal])
        panel = self.panel(self.config([dict(bus=1, can_id=0x200, signal_name='State')]),
                           db={1: {0x200: message}})
        panel.set_mode('standby')
        panel.on_message_update(1, 0x200, message.encode({'State': raw}, scaling=False), 0)
        self.assertTrue(panel.widget_controls['grid'].table.item(0, 3).text().startswith('ExactRaw ('))

    def test_float_and_multiplexed_signals_decode_and_clear_inactive_values(self):
        floating = Signal('Float', 0, 32, conversion=BaseConversion.factory(is_float=True))
        mux = Signal('Mux', 32, 8, is_multiplexer=True)
        first = Signal('First', 40, 8, multiplexer_ids=[1], multiplexer_signal='Mux')
        second = Signal('Second', 40, 8, multiplexer_ids=[2], multiplexer_signal='Mux')
        message = Message(0x200, 'Multiplex', 6, [floating, mux, first, second])
        entries = [dict(bus=1, can_id=0x200, signal_name=name) for name in ('Float', 'First', 'Second')]
        panel = self.panel(self.config(entries), db={1: {0x200: message}})
        panel.set_mode('standby')
        table = panel.widget_controls['grid'].table
        panel.on_message_update(1, 0x200, message.encode(dict(Float=12.5, Mux=1, First=7)), 0)
        self.assertEqual([table.item(r, 3).text() for r in range(3)], ['12.5', '7', '-'])
        panel.on_message_update(1, 0x200, message.encode(dict(Float=9.5, Mux=2, Second=8)), 1)
        self.assertEqual([table.item(r, 3).text() for r in range(3)], ['9.5', '-', '8'])

    def test_missing_dbc_selections_remain_editable_and_validation_rejects_empty_duplicate(self):
        saved = [self.entry(), self.entry(2)]
        dialog = GridSignalDialog({}, saved)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.selected_signals(), saved)
        dialog.items[(1, 0x130, 'Value')].setCheckState(0, Qt.Unchecked)
        self.assertEqual(dialog.selected_signals(), [saved[1]])
        for entries in ([], [saved[0], saved[0]]):
            with self.assertRaises(ValueError):
                validate_config(self.config(entries))

    def test_optional_columns_user_names_and_mandatory_value_survive_package(self):
        panel = self.panel()
        editor = panel.properties.editor
        for key, check in editor.grid_column_checks.items():
            check.setChecked(key == 'user_name')

        def rename(dialog):
            dialog.items[(1, 0x130, 'Value')].setText(4, '배터리 전압')
            return dialog.Accepted

        with patch.object(GridSignalDialog, 'exec_', rename):
            editor.btn_grid_signals.click()
        editor.accept()
        config = panel.widgets_config[0]
        self.assertEqual(config['binding']['grid_columns'], ['user_name'])
        self.assertEqual(config['binding']['grid_signals'][0]['user_name'], '배터리 전압')
        with tempfile.TemporaryDirectory() as directory:
            filename = save_bundle(str(Path(directory) / 'columns'), panel._panel_data(), {})
            with patch('src.user_panel_v2.storage.tempfile.mkdtemp', return_value=directory):
                saved, _ = load_bundle(filename)
            panel._load_panel_data(saved)
        control = panel.widget_controls['grid']
        self.assertEqual(control.columns, ['user_name', 'value'])
        self.assertEqual(control.table.item(0, 0).text(), '배터리 전압')
        self.assertEqual(control.table.horizontalHeaderItem(1).text(), '값')
        panel.set_mode('standby')
        payload = panel.db_messages[1][0x130].encode(dict(Value=25, State=1))
        panel.on_message_update(1, 0x130, payload, 1)
        self.assertEqual(control.table.item(0, 1).text(), '25 V')
        panel.set_mode('edit')
        panel.selected_widget_id = 'grid'
        panel.selected_widget_ids = {'grid'}
        panel.properties.refresh(force=True)
        editor = panel.properties.editor
        for check in editor.grid_column_checks.values():
            check.setChecked(False)
        editor.accept()
        self.assertEqual(panel.widget_controls['grid'].columns, ['value'])

    def test_alias_edit_recheck_and_missing_database_keep_user_name(self):
        dialog = GridSignalDialog(self.database(), [self.entry()])
        self.addCleanup(dialog.deleteLater)
        item = dialog.items[(1, 0x130, 'Value')]
        item.setText(4, '전압')
        item.setCheckState(0, Qt.Unchecked)
        item.setCheckState(0, Qt.Checked)
        selected = dialog.selected_signals()
        self.assertEqual(selected[0]['user_name'], '전압')
        missing = GridSignalDialog({}, selected)
        self.addCleanup(missing.deleteLater)
        self.assertEqual(missing.items[(1, 0x130, 'Value')].text(4), '전압')
        item.setText(4, '')
        self.assertNotIn('user_name', dialog.selected_signals()[0])

    def test_column_widths_follow_text_and_extra_width_goes_to_value(self):
        config = self.config([self.entry(), self.entry(2)])
        control = RxGridControl(config, self.database(), lambda: None)
        self.addCleanup(control.deleteLater)
        self.addCleanup(control.close)
        minimum = grid_minimum_size(config, self.database())
        control.resize(minimum.width() + 200, minimum.height())
        control.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertGreater(control.table.columnWidth(1), control.table.columnWidth(0))
        metrics = QFontMetrics(control.table.font())
        for row in range(2):
            for column in range(4):
                self.assertGreaterEqual(control.table.columnWidth(column),
                                        metrics.horizontalAdvance(control.table.item(row, column).text()) + 8)
        allocated = sum(control.table.columnWidth(c) for c in range(3))
        self.assertEqual(control.table.columnWidth(3), control.table.viewport().width() - allocated)
        self.assertEqual(control.table.horizontalScrollBar().maximum(), 0)

    def test_content_minimum_clamps_resize_and_fonts_grow_and_shrink_with_tool(self):
        short = self.config()
        short['binding']['grid_columns'] = []
        longer = self.config([dict(self.entry(), user_name='배터리 시스템 전압'), self.entry(name='State')])
        longer['binding']['grid_columns'] = ['user_name']
        short_min = minimum_tool_spans('grid_view', 16, 48, 80, short, self.database())
        longer_min = minimum_tool_spans('grid_view', 16, 48, 80, longer, self.database())
        self.assertGreater(longer_min[0], short_min[0])
        self.assertGreater(longer_min[1], short_min[1])
        panel = self.panel(longer)
        panel.resize_selected_span(-1000, -1000)
        expected = minimum_tool_spans('grid_view', panel.grid_cell_size, panel.grid_rows,
                                      panel.grid_cols, longer, panel.db_messages)
        cfg = panel.widgets_config[0]
        self.assertEqual((cfg['row_span'], cfg['col_span']), expected)
        control = RxGridControl(longer, self.database(), lambda: None)
        self.addCleanup(control.deleteLater)
        self.addCleanup(control.close)
        minimum = grid_minimum_size(longer, self.database())
        control.resize(minimum)
        control.show()
        self.app.processEvents()
        self.app.processEvents()
        small_font = control.table.font().pixelSize()
        self.assertEqual(control.table.horizontalScrollBar().maximum(), 0)
        self.assertEqual(control.table.verticalScrollBar().maximum(), 0)
        control.resize(minimum.width() * 3, minimum.height() * 3)
        self.app.processEvents()
        self.app.processEvents()
        self.assertGreater(control.table.font().pixelSize(), small_font)
        self.assertEqual(control.table.horizontalScrollBar().maximum(), 0)
        self.assertEqual(control.table.verticalScrollBar().maximum(), 0)
        control.resize(minimum)
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(control.table.font().pixelSize(), small_font)

    def test_graph_icon_opens_main_realtime_graph_in_grid_order_and_receives_bound_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
                main = UniversalCANMonitor()
            main.session.directory = Path(directory)
            main.session.autosave_path = Path(directory) / 'auto.pjjsettings'
            main.session.report = Mock()
            try:
                for bus, messages in self.database().items():
                    path = Path(directory) / f'bus{bus}.dbc'
                    path.write_text(Database(messages=list(messages.values())).as_dbc_string(), encoding='cp1252')
                    main.load_db_from_path(str(path), bus)
                entries = [self.entry(2), self.entry(can_id=0x131), self.entry()]
                panel = self.panel(self.config(entries), main=main, db=main.db_messages)
                control = panel.widget_controls['grid']
                control.graph_button.click()
                graph = main.active_graphs[-1]
                self.assertEqual(graph.signal_names, ['B2:130:0:Value', 'B1:131:0:Value', 'B1:130:0:Value'])
                self.assertIs(graph.main_window, main)
                main._graph_pending_frames = {(2, 0x130, False): (1.0, bytes([88]))}
                main.update_bound_graphs()
                self.assertEqual(list(graph.values['B2:130:0:Value']), [88])
                self.assertEqual(list(graph.values['B1:130:0:Value']), [])
            finally:
                main.session.ready = False
                main.close()
                main.deleteLater()


if __name__ == '__main__':
    unittest.main()
