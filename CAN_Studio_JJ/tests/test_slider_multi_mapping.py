import copy
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication, QSlider, QLineEdit, QPushButton
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.packets import PacketRuntime, bind_packet
from src.user_panel_v2.config_dialog import WidgetConfigDialog


class SliderMappingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.sent = []
        main = SimpleNamespace(
            buses={b: SimpleNamespace(send=lambda msg, bus=b: self.sent.append((bus, bytes(msg.data))))
                   for b in (1, 2)},
            bus_capabilities={1: {'is_fd': False}, 2: {'is_fd': False}})
        self.panel = UserPanelWindow(main, {})
        self.panel.show()
        self.panel.mode = 'run'
        self.addCleanup(self.panel.close)
        packets = [dict(packet_id=str(b), bus=b, id=0x123, length=8, data=[0]*8,
                        cycle=0, is_fd=False, is_brs=False, crc_type='N/A') for b in (1, 2)]
        self.panel.tx_packets = packets
        self.panel._packet_runtimes = {p['packet_id']: PacketRuntime(p, {}, main) for p in packets}
        bindings = []
        for packet, high, home in zip(packets, (1000, 100), (200, 30)):
            binding = dict(min=0, max=high, tx_initial_value=home, tx_resolution=1,
                           start_bit=0, bit_length=16, byte_order='little_endian', scale=1, offset=0)
            bind_packet(binding, packet)
            bindings.append(binding)
        self.multi = dict(id='multi', widget_type='slider', behavior='tx', binding=bindings[0],
                          tx_commands=[dict(enabled=True, binding=bindings[1])])
        self.single1 = dict(id='one', widget_type='slider', behavior='tx', binding=copy.deepcopy(bindings[0]))
        self.single2 = dict(id='two', widget_type='slider', behavior='tx', binding=copy.deepcopy(bindings[1]))
        self.panel.widgets_config = [self.multi, self.single1, self.single2]
        for cfg in self.panel.widgets_config:
            ctrl, _ = self.panel._create_runtime_widget(cfg)
            self.panel.widget_controls[cfg['id']] = ctrl
            self.addCleanup(ctrl.deleteLater)

    def slider(self, cfg):
        return self.panel.widget_controls[cfg['id']].findChild(QSlider, 'slider')

    def test_ratio_then_single_resets_multi_display_without_other_bus_send(self):
        self.multi['tx_commands'][0]['binding']['slider_mapping'] = 'percent'
        self.slider(self.multi).setValue(500)
        self.assertEqual([(b, int.from_bytes(data[:2], 'little')) for b, data in self.sent], [(1, 500), (2, 50)])
        self.assertEqual(self.slider(self.single1).value(), 500)
        self.assertEqual(self.slider(self.single2).value(), 50)
        self.sent.clear()
        self.slider(self.single2).setValue(70)
        self.assertEqual([(b, int.from_bytes(data[:2], 'little')) for b, data in self.sent], [(2, 70)])
        self.assertEqual(self.slider(self.multi).value(), 200)
        self.assertEqual(self.slider(self.single1).value(), 500)
        self.assertEqual(int.from_bytes(self.panel._packet_runtimes['1'].overlay[:2], 'little'), 500)
        entry = self.panel.widget_controls['multi'].findChild(QLineEdit, 'slider_value_input')
        self.assertEqual(entry.text(), '200')

    def test_home_uses_each_commands_own_home_not_percentage(self):
        self.slider(self.multi).setValue(500)
        self.sent.clear()
        ctrl = self.panel.widget_controls['multi']
        ctrl.findChild(QPushButton, 'slider_home').click()
        self.assertEqual([(b, int.from_bytes(data[:2], 'little')) for b, data in self.sent], [(1, 200), (2, 30)])
        self.assertEqual(self.slider(self.single1).value(), 200)
        self.assertEqual(self.slider(self.single2).value(), 30)

    def test_direct_mode_uses_main_value_and_limits_to_target_range(self):
        binding = self.multi['tx_commands'][0]['binding']
        binding.pop('slider_mapping', None)  # Unspecified mode defaults to direct.
        binding['min'] = 10
        self.slider(self.multi).setValue(50)
        self.assertEqual(self.slider(self.single2).value(), 50)
        self.slider(self.multi).setValue(500)
        self.assertEqual(self.slider(self.single2).value(), 100)
        self.slider(self.multi).setValue(0)
        self.assertEqual(self.slider(self.single2).value(), 10)
        self.panel.widget_controls['multi'].findChild(QPushButton, 'slider_home').click()
        self.assertEqual(self.slider(self.single2).value(), 30)

    def test_mapping_setting_round_trip_and_legacy_default(self):
        preset = copy.deepcopy(self.single2)
        dialog = WidgetConfigDialog({}, preset=preset, fixed_behavior='tx', allow_multi=False)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.combo_slider_mapping.currentData(), 'direct')
        dialog.combo_slider_mapping.setCurrentIndex(dialog.combo_slider_mapping.findData('percent'))
        saved = dialog.get_config()
        self.assertEqual(saved['binding']['slider_mapping'], 'percent')
        restored = WidgetConfigDialog({}, preset=saved, fixed_behavior='tx', allow_multi=False)
        self.addCleanup(restored.deleteLater)
        self.assertEqual(restored.combo_slider_mapping.currentData(), 'percent')


if __name__ == '__main__':
    unittest.main()
