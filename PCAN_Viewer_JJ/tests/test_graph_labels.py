import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QTreeWidgetItem
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion
from src.graph_labels import signal_labels, update_realtime_labels
from src.graph_realtime import SignalGraphWindow
from src.graph_log import LogGraphWindow
from src.log_viewer import LogViewerWindow
from src.main_window import UniversalCANMonitor
from src.session_graph import graph_state, restore_graph


class GraphLabelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_short_names_and_duplicate_signal_names(self):
        metadata = dict(
            a=dict(bus=1, symbol='Left', signal='Speed', id=0x100),
            b=dict(bus=2, symbol='Right', signal='Speed', id=0x200),
            c=dict(bus=1, symbol='Status', signal='Ready', id=0x300))
        self.assertEqual(signal_labels(metadata),
                         dict(a='B1:Left.Speed', b='B2:Right.Speed', c='B1:Ready'))
        self.assertEqual(signal_labels({'a': metadata['a']}), {'a': 'B1:Speed'})

    def test_same_symbol_and_signal_names_remain_distinguishable(self):
        metadata = {key: dict(bus=1, symbol='Same', signal='Value', id=can_id)
                    for key, can_id in [('a', 0x100), ('b', 0x200)]}
        self.assertEqual(signal_labels(metadata),
                         dict(a='B1:Same[0x100].Value', b='B1:Same[0x200].Value'))

    def test_database_unicode_survives_realtime_and_log_tags(self):
        for encoding, unit, choice in [
                ('utf-8', '°/s · ℃ · Ω · µm · ± · ²', '온도 <정상> & ±1°'),
                ('utf-8-sig', '°/s · ℃ · Ω · µm · ± · ²', '온도 <정상> & ±1°'),
                ('cp1252', '°/s · µm · ± · ²', 'Ready <OK> & ±1°')]:
            with self.subTest(encoding=encoding):
                content = f'''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 291 Angle: 1 ECU
 SG_ Angle : 0|8@1+ (1,0) [0|255] "{unit}" ECU
VAL_ 291 Angle 1 "{choice}";
'''
                db = UniversalCANMonitor.parse_database_bytes(
                    None, 'unicode.dbc', content.encode(encoding))
                signal = db.get_message_by_name('Angle').get_signal_by_name('Angle')
                self.assertEqual(signal.unit, unit)
                self.assertEqual(str(signal.choices[1]), choice)
                for realtime in (True, False):
                    with self.subTest(realtime=realtime):
                        if realtime:
                            graph = SignalGraphWindow(['Angle'])
                            graph.update_data('Angle', 1, 1, signal.unit)
                        else:
                            graph = LogGraphWindow({'Angle': dict(times=[1], values=[1], unit=signal.unit)})
                        self.addCleanup(graph.deleteLater)
                        self.addCleanup(graph.close)
                        graph.display_names['Angle'] = '각도 <Angle> & ±1°'
                        # Enum lookup uses the actual database signal in both graph types.
                        if realtime:
                            graph._bound_signals['Angle'] = (db.messages[0], signal)
                        else:
                            graph.plot_data['Angle']['choices'] = signal.choices
                        graph.add_tag_for_signal('Angle', 1)
                        tag = graph.active_tags[('Angle', 1.0)][1]
                        text = tag.toPlainText()
                        self.assertIn('각도 <Angle> & ±1°', text)
                        self.assertIn(unit, text)
                        self.assertIn(choice, text)

    def test_realtime_names_restore_while_preserving_custom_labels_and_keys(self):
        keys = ['B1:100:0:Speed', 'B1:200:0:Temperature']
        graph = SignalGraphWindow(keys)
        self.addCleanup(graph.close)
        graph.signal_bindings = {key: dict(bus=1, id=can_id, signal=signal)
                                 for key, can_id, signal in [(keys[0], 0x100, 'Speed'),
                                                             (keys[1], 0x200, 'Temperature')]}
        bound = {}
        for key, binding in graph.signal_bindings.items():
            signal = Signal(binding['signal'], 0, 8)
            message = Message(binding['id'], 'Message', 1, [signal])
            bound[key] = (message, signal)
        graph._bound_signals = bound
        update_realtime_labels(graph)
        self.assertEqual(graph.display_names[keys[0]], 'B1:Speed')
        self.assertEqual(graph.legend_widget.item(0).text(), 'B1:Speed')
        self.assertEqual(graph.legend_widget.item(0).toolTip(), 'B1:Message.Speed')
        self.assertEqual(graph.combo_hover_signal.itemText(1), 'B1:Temperature')
        graph.update_data(keys[0], 1, 25)
        self.assertEqual(list(graph.values[keys[0]]), [25])
        graph.display_names[keys[0]] = 'Custom speed'
        graph.display_names[keys[1]] = 'B1:Message.Temperature'
        state = graph_state(graph)
        restored = SignalGraphWindow(keys)
        self.addCleanup(restored.close)
        restore_graph(restored, state)
        restored._bound_signals = bound
        update_realtime_labels(restored, preserve_custom=True)
        self.assertEqual(restored.display_names[keys[0]], 'Custom speed')
        self.assertEqual(restored.display_names[keys[1]], 'B1:Temperature')
        self.assertEqual(restored.legend_widget.item(0).data(Qt.UserRole), keys[0])

    def test_log_viewer_keeps_duplicate_signal_data_and_enum_values_separate(self):
        messages = {}
        for can_id, symbol, choice in [(0x100, 'First', 'ON'), (0x200, 'Second', 'READY')]:
            signal = Signal('State', 0, 8, conversion=BaseConversion.factory(choices={1: choice}))
            messages[can_id] = Message(can_id, symbol, 1, [signal])
        signal = Signal('Temperature', 0, 8)
        messages[0x300] = Message(0x300, 'Third', 1, [signal])
        with patch.object(LogViewerWindow, 'start_parsing'):
            viewer = LogViewerWindow('unused.trc', {1: messages})
        self.addCleanup(viewer.close)
        for index, (can_id, message) in enumerate(messages.items()):
            parent = QTreeWidgetItem(viewer.tree, [message.name])
            child = QTreeWidgetItem(parent, [message.signals[0].name])
            child.setData(0, Qt.UserRole + 1, (1, can_id))
            child.setCheckState(0, Qt.Checked)
            viewer.signal_data[(1, can_id)] = {message.signals[0].name: ([0, 1], [index, index + 1])}
        viewer.open_graph()
        graph = viewer.active_graphs[0]
        self.addCleanup(graph.close)
        self.assertEqual(graph.legend_widget.count(), 3)
        self.assertEqual([graph.legend_widget.item(i).text() for i in range(3)],
                         ['B1:First.State', 'B1:Second.State', 'B1:Temperature'])
        keys = graph.signal_names
        self.assertEqual(graph.plot_data[keys[0]]['values'], [0, 1])
        self.assertEqual(graph.plot_data[keys[1]]['values'], [1, 2])
        self.assertEqual(graph._get_enum_string(keys[0], 1), 'ON')
        self.assertEqual(graph._get_enum_string(keys[1], 1), 'READY')
        self.assertEqual(graph.combo_hover_signal.itemText(2), 'B1:Temperature')


if __name__ == '__main__':
    unittest.main()
