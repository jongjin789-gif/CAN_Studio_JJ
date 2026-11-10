import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel, QSlider, QLineEdit, QPushButton
from PyQt5.QtTest import QTest
from src.user_panel_v2.window import UserPanelWindow


class SliderInputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_keyboard_set_home_and_bounds(self):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.close)
        cfg = dict(id='s', title='Slider', behavior='none', widget_type='slider',
                   row=0, col=0, row_span=4, col_span=5,
                   binding=dict(min=-10, max=10, tx_resolution=0.5, tx_initial_value=2))
        panel._load_panel_data(dict(widgets=[cfg]))
        panel.show()
        panel.set_mode('run')
        panel.activateWindow()
        ctrl = panel.widget_controls['s']
        slider = ctrl.findChild(QSlider, 'slider')
        entry = ctrl.findChild(QLineEdit, 'slider_value_input')
        button = ctrl.findChild(QPushButton, 'slider_set')
        slider.setFocus()
        self.app.processEvents()
        QTest.keyClick(slider, Qt.Key_Right)
        self.assertEqual(float(entry.text()), 2.5)
        QTest.keyClick(slider, Qt.Key_Left)
        self.assertEqual(float(entry.text()), 2)
        for text, expected in [('4.5', 4.5), ('999', 10), ('-999', -10),
                               ('abc', 2), ('', 2), ('nan', 2), ('inf', 2)]:
            entry.setText(text)
            button.click()
            self.assertEqual(float(entry.text()), expected)
            self.assertEqual(slider.value(), round((expected+10)*2))
        entry.setText('-3.5')
        QTest.keyClick(entry, Qt.Key_Return)
        self.assertEqual(float(entry.text()), -3.5)
        panel.set_mode('edit')
        self.assertTrue(all(s.isEnabled() for s in panel._shortcuts))

    def test_tx_uses_existing_value_change_path(self):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.close)
        cfg = dict(widget_type='slider', behavior='tx', binding=dict(min=0, max=10, tx_initial_value=3))
        ctrl, _ = panel._create_runtime_widget(cfg)
        self.addCleanup(ctrl.close)
        ctrl.resize(400, 64)
        ctrl.show()
        self.app.processEvents()
        entry = ctrl.findChild(QLineEdit, 'slider_value_input')
        slider = ctrl.findChild(QSlider, 'slider')
        value_label = ctrl.findChild(QLabel, 'value_label')
        self.assertEqual(entry.text(), '3')
        self.assertLessEqual(entry.width(), 96)
        set_button = ctrl.findChild(QPushButton, 'slider_set')
        self.assertEqual(entry.height(), 20)
        self.assertEqual(set_button.height(), 20)
        self.assertEqual(
            set_button.x() - entry.geometry().right() - 1,
            4,
        )
        with patch.object(panel, '_emit_tx') as send:
            slider.setValue(slider.maximum() // 2)
            self.assertEqual(value_label.text(), '5')
            self.assertEqual(entry.text(), '3')
            entry.setText('7')
            slider.setValue(slider.maximum() * 3 // 4)
            self.assertEqual(entry.text(), '7')
            panel._update_widget_value(cfg, 6, force=True)
            self.assertEqual(entry.text(), '7')
            send.reset_mock()
            set_button.click()
            send.assert_called_once_with(cfg, 7)
            self.assertEqual(entry.text(), '7')
            entry.setText('8')
            ctrl.findChild(QPushButton, 'slider_home').click()
            self.assertEqual(entry.text(), '8')
            send.reset_mock()
            entry.setText('invalid')
            set_button.click()
            send.assert_called_once_with(cfg, 3)
