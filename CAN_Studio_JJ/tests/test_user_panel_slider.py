import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QSlider, QLineEdit
from cantools.database.conversion import BaseConversion
from cantools.database.can import Message, Signal
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.binding import pack_value


class SliderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_precision_initial_and_home(self):
        panel = UserPanelWindow(SimpleNamespace(), {})
        sent = []
        panel._emit_tx = lambda cfg, value: sent.append(value)
        for resolution, expected in ((1, "5"), (0.1, "5.0"), (0.01, "5.00")):
            cfg = dict(widget_type="slider", behavior="tx", binding=dict(
                min=0, max=10, tx_resolution=resolution, tx_initial_value=5))
            ctrl, _ = panel._create_runtime_widget(cfg)
            slider = ctrl.findChild(QSlider)
            label = ctrl.findChild(QLabel, "value_label")
            home = ctrl.findChild(QPushButton, "slider_home")
            self.assertEqual(label.text(), expected)
            sent.clear()
            slider.setValue(slider.maximum())
            home.click()
            self.assertEqual(sent, [10, 5])
            self.assertEqual(label.text(), expected)
            ctrl.resize(240, 64)
            ctrl.show()
            self.app.processEvents()
            self.assertGreaterEqual(home.width(), home.sizeHint().width())
            slider_top_left = slider.mapTo(ctrl, QPoint(0, 0))
            self.assertLess(home.geometry().right(), slider_top_left.x())
            entry = ctrl.findChild(QLineEdit, "slider_value_input")
            set_button = ctrl.findChild(QPushButton, "slider_set")
            self.assertLess(entry.geometry().right(), set_button.geometry().left())
            self.assertLess(entry.geometry().center().y(), slider_top_left.y() + slider.height() / 2)
            value_top_left = label.mapTo(ctrl, QPoint(0, 0))
            self.assertGreater(value_top_left.y() + label.height() / 2,
                               slider_top_left.y() + slider.height() / 2)
            self.assertLessEqual(value_top_left.y() - (slider_top_left.y() + slider.height()), 4)
            self.assertLessEqual(ctrl.rect().bottom() - (value_top_left.y() + label.height()), 16)
            ctrl.deleteLater()
        panel.db_messages = {1: {1: SimpleNamespace(get_signal_by_name=lambda name: SimpleNamespace(is_float=False))}}
        self.assertEqual(panel._format_slider_value(dict(bus=1, can_id=1, signal_name="Count", tx_resolution=0.1), 5), "5")
        panel.deleteLater()

    def test_initial_value_round_trip(self):
        dialog = WidgetConfigDialog({}, fixed_behavior="tx")
        dialog.combo_widget_type.setCurrentText("slider")
        dialog.spin_slider_initial.setValue(12.345678)
        cfg = dialog.get_config()
        restored = WidgetConfigDialog({}, preset=cfg)
        self.assertEqual(restored.get_config()["binding"]["tx_initial_value"], 12.345678)
        dialog.deleteLater()
        restored.deleteLater()

    def test_slider_tool_adapts_grid_span_and_keeps_controls_inside(self):
        panel = UserPanelWindow(SimpleNamespace(), {})
        panel._load_panel_data(dict(grid=dict(rows=8, cols=20, cell_size=32), widgets=[dict(
            id="slider", title="ANGLE", widget_type="slider", behavior="tx",
            row=0, col=0, row_span=2, col_span=4,
            binding=dict(min=-65, max=65, tx_resolution=0.1, tx_initial_value=0))]))
        panel.resize(800, 500)
        panel.show()
        self.app.processEvents()

        self.assertEqual(panel.widgets_config[0]["row_span"], 4)
        self.assertEqual(panel.widgets_config[0]["col_span"], 12)
        control = panel.widget_controls["slider"]
        home = control.findChild(QPushButton, "slider_home")
        set_button = control.findChild(QPushButton, "slider_set")
        value_input = control.findChild(QLineEdit, "slider_value_input")
        slider = control.findChild(QSlider, "slider")
        value = control.findChild(QLabel, "value_label")
        title = control.findChild(QLabel, "slider_title")
        title_top_left = title.mapTo(control, QPoint(0, 0))
        home_top_left = home.mapTo(control, QPoint(0, 0))
        slider_top_left = slider.mapTo(control, QPoint(0, 0))
        value_top_left = value.mapTo(control, QPoint(0, 0))
        self.assertEqual(home.width(), 3 * panel.grid_cell_size)
        self.assertEqual(home.font().pixelSize(), 10)
        self.assertGreaterEqual(control.height(), home.sizeHint().height())
        self.assertEqual(home.height(), 2 * panel.grid_cell_size)
        self.assertLess(home.height(), control.height())
        self.assertGreaterEqual(title.width(), 3 * panel.grid_cell_size)
        self.assertEqual(title.toolTip(), "[TX] ANGLE")
        self.assertEqual(title_top_left.x(), home_top_left.x())
        self.assertGreater(home_top_left.y(), title_top_left.y())
        self.assertEqual(
            slider_top_left.x() - home.geometry().right() - 1,
            max(1, panel.grid_cell_size // 2),
        )
        self.assertLess(value_input.geometry().right(), set_button.geometry().left())
        self.assertGreaterEqual(control.rect().bottom(), home.geometry().bottom())
        self.assertGreaterEqual(control.rect().bottom(), slider_top_left.y() + slider.height())
        self.assertGreaterEqual(control.rect().bottom(), value_top_left.y() + value.height())

        home_width = home.width()
        slider_width = slider.width()
        control_height = control.height()
        panel.selected_widget_id = "slider"
        panel._sync_geom_editor_from_selection()
        self.assertEqual(panel.spin_sel_row_span.minimum(), 4)
        self.assertEqual(panel.spin_sel_row_span.maximum(), 4)
        panel.resize_selected_span(0, 2)
        self.assertEqual(panel.widgets_config[0]["row_span"], 4)
        panel.widgets_config[0]["col_span"] += 4
        panel.rebuild_grid()
        wider_home = panel.widget_controls["slider"].findChild(QPushButton, "slider_home")
        wider_slider = panel.widget_controls["slider"].findChild(QSlider, "slider")
        self.assertEqual(wider_home.width(), home_width)
        self.assertGreater(wider_slider.width(), slider_width)
        self.assertEqual(panel.widget_controls["slider"].height(), control_height)
        panel.close()

    def test_tx_slider_displays_the_dbc_decoded_value(self):
        conversion = BaseConversion.factory(scale=0.125, offset=0.1)
        message = Message(0x123, "Scaled", 1, [Signal("Value", 0, 8, conversion=conversion)])
        panel = UserPanelWindow(SimpleNamespace(), {1: {0x123: message}})
        sent_values = []
        panel._emit_tx = lambda cfg, value: sent_values.append(value)
        binding = dict(bus=1, can_id=0x123, dlc=1, signal_name="Value", start_bit=0,
                       bit_length=8, scale=0.125, offset=0.1, signed=False,
                       byte_order="little_endian", min=0, max=1, tx_resolution=0.2,
                       tx_initial_value=0)
        control, _ = panel._create_runtime_widget(dict(
            id="slider", title="Value", widget_type="slider", behavior="tx", binding=binding))
        slider = control.findChild(QSlider, "slider")
        label = control.findChild(QLabel, "value_label")

        slider.setValue(2)

        encoded = pack_value(bytes(1), binding, sent_values[-1])
        decoded = message.decode(encoded, decode_choices=False)["Value"]
        self.assertEqual(float(label.text()), decoded)
        self.assertEqual(sent_values[-1], decoded)
        control.deleteLater()
        panel.deleteLater()

    def test_dbc_scale_and_offset_precision_survive_signal_selection_and_save(self):
        conversion = BaseConversion.factory(scale=0.0625, offset=-780.125)
        signal = Signal("Value", 7, 16, byte_order="big_endian", conversion=conversion)
        message = Message(0x123, "Scaled", 8, [signal])
        dialog = WidgetConfigDialog({1: {0x123: message}}, fixed_behavior="tx")
        dialog.combo_widget_type.setCurrentText("slider")
        dialog.combo_message.setCurrentIndex(dialog.combo_message.findData(0x123))
        dialog.combo_signal.setCurrentIndex(dialog.combo_signal.findData("Value"))

        config = dialog.get_config()

        self.assertEqual(dialog.spin_scale.value(), 0.0625)
        self.assertEqual(dialog.spin_offset.value(), -780.125)
        self.assertEqual(config["binding"]["scale"], 0.0625)
        self.assertEqual(config["binding"]["offset"], -780.125)
        dialog.deleteLater()
