import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QMargins, QRect, Qt
from PyQt5.QtWidgets import QApplication, QWidget

from src.session_graph import restore_geometry


class SessionGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_oversized_hidden_window_fits_with_frame_after_show(self):
        window = QWidget()
        self.addCleanup(window.close)
        area = self.app.primaryScreen().availableGeometry()
        restore_geometry(window, [area.left(), area.top(), area.width() + 1000, area.height() + 1000])
        self.assertFalse(window.isVisible())
        window.show()
        self.app.processEvents()
        self.assertTrue(area.contains(window.frameGeometry()))
        self.assertLess(window.height(), area.height())

    def test_native_title_bar_is_reserved_on_each_monitor(self):
        primary = QRect(0, 0, 1920, 1032)
        secondary = QRect(-1920, 0, 1920, 1032)
        screens = [SimpleNamespace(availableGeometry=lambda: primary),
                   SimpleNamespace(availableGeometry=lambda: secondary)]
        for saved, expected in (
            ([0, 0, 1920, 1032], QRect(8, 31, 1904, 993)),
            ([1920, 0, 1920, 1032], QRect(8, 31, 1904, 993)),
            ([-1920, 0, 1920, 1032], QRect(-1912, 31, 1904, 993)),
            ([100, 100, 800, 600], QRect(100, 100, 800, 600)),
            ([1800, 900, 400, 300], QRect(1512, 724, 400, 300)),
        ):
            with self.subTest(saved=saved):
                window = Mock()
                window.windowHandle.return_value.frameMargins.return_value = QMargins(8, 31, 8, 8)
                with patch('src.session_graph.QApplication.screens', return_value=screens), \
                     patch('src.session_graph.QApplication.primaryScreen', return_value=screens[0]):
                    restore_geometry(window, saved)
                window.setGeometry.assert_called_once_with(expected)

    def test_frameless_window_can_use_entire_available_area(self):
        window = QWidget(flags=Qt.FramelessWindowHint)
        self.addCleanup(window.close)
        area = self.app.primaryScreen().availableGeometry()
        restore_geometry(window, area.getRect())
        window.show()
        self.app.processEvents()
        self.assertEqual(window.frameGeometry(), area)

    def test_missing_geometry_does_not_change_window(self):
        window = Mock()
        restore_geometry(window, None)
        window.winId.assert_not_called()
        window.setGeometry.assert_not_called()


if __name__ == '__main__':
    unittest.main()
