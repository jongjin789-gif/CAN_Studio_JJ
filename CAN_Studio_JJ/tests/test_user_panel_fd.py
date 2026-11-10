import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
from src.main_window import UniversalCANMonitor
from src.user_panel_v2.config_dialog import WidgetConfigDialog


class PanelFDTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_round_trip(self):
        for is_fd, brs in ((False, False), (True, False), (True, True)):
            dialog = WidgetConfigDialog({}, fixed_behavior="tx")
            dialog.combo_frame_type.setCurrentText("FD" if is_fd else "Classic")
            dialog.chk_brs.setChecked(brs)
            cfg = dialog.get_config()
            restored = WidgetConfigDialog({}, preset=cfg)
            self.assertEqual(restored.get_config()["binding"], cfg["binding"])
            self.assertEqual(cfg["binding"]["is_fd"], is_fd)
            self.assertEqual(cfg["binding"]["brs"], brs)
            self.assertEqual(restored.chk_brs.isEnabled(), is_fd)
            dialog.deleteLater()
            restored.deleteLater()

    def test_frame_flags_and_channel_validation(self):
        sent = []
        owner = SimpleNamespace(
            user_tx_cache={}, user_frame_properties={},
            buses={1: SimpleNamespace(send=sent.append)},
            bus_capabilities={1: {"is_fd": True}},
            record_tx_activity=lambda *args, **kwargs: None,
        )
        owner._pack_signal_to_payload = lambda *args: UniversalCANMonitor._pack_signal_to_payload(owner, *args)
        for is_fd, brs in ((False, False), (True, False), (True, True)):
            binding = dict(bus=1, can_id=0x123, dlc=8, bit_length=8, is_fd=is_fd, brs=brs)
            UniversalCANMonitor.stage_user_panel_value(owner, binding, 7)
            UniversalCANMonitor.flush_user_panel_frame(owner, 1, 0x123, 8)
            self.assertEqual((sent[-1].is_fd, sent[-1].bitrate_switch), (is_fd, brs))
            self.assertEqual(sent[-1].data, bytes([7] + [0] * 7))
        owner.bus_capabilities[1]["is_fd"] = False
        with self.assertRaises(RuntimeError):
            UniversalCANMonitor.flush_user_panel_frame(owner, 1, 0x123, 8)
        with self.assertRaises(ValueError):
            UniversalCANMonitor.stage_user_panel_value(owner, dict(dlc=12, is_fd=False), 0)


if __name__ == "__main__":
    unittest.main()
