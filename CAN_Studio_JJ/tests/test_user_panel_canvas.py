import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QLabel
from src.user_panel_v2.window import UserPanelWindow


class PanelCanvasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_canvas_expands_logical_grid_to_fill_viewport(self):
        panel = UserPanelWindow(SimpleNamespace(), {})
        full_title = "A very long button title for a narrow grid cell"
        panel._load_panel_data({
            "grid": {"rows": 36, "cols": 36, "cell_size": 32},
            "widgets": [{"id": "button", "title": full_title,
                         "widget_type": "button", "behavior": "tx",
                         "row": 2, "col": 3, "row_span": 3, "col_span": 5}],
        })
        self.assertEqual(panel.grid_cell_size, 16)
        panel.show()
        self.app.processEvents()
        canvas_size = panel.canvas.size()
        self.assertEqual(canvas_size.width(), panel.grid_cols * panel.grid_cell_size)
        self.assertEqual(canvas_size.height(), panel.grid_rows * panel.grid_cell_size)
        self.assertGreaterEqual(canvas_size.width(), panel.canvas_scroll.viewport().width())
        self.assertGreaterEqual(canvas_size.height(), panel.canvas_scroll.viewport().height())
        initial_grid = (panel.grid_rows, panel.grid_cols)
        frame_rect = panel.widget_frames["button"].geometry()
        control_size = panel.widget_controls["button"].size()
        title = panel.widget_frames["button"].findChild(QLabel)
        self.assertIsNotNone(title)
        self.assertGreaterEqual(title.font().pixelSize(), 8)
        self.assertLessEqual(title.font().pixelSize(), 11)
        self.assertEqual(title.toolTip(), f"[TX] {full_title}")
        self.assertGreaterEqual(panel.grid_rows, 36)
        self.assertGreaterEqual(panel.grid_cols, 36)
        for width, height in ((800, 600), (1000, 700)):
            panel.resize(width, height)
            self.app.processEvents()
            self.assertEqual(panel.canvas.width(), panel.grid_cols * panel.grid_cell_size)
            self.assertEqual(panel.canvas.height(), panel.grid_rows * panel.grid_cell_size)
            self.assertGreaterEqual(panel.canvas.width(), panel.canvas_scroll.viewport().width())
            self.assertGreaterEqual(panel.canvas.height(), panel.canvas_scroll.viewport().height())
            self.assertEqual(panel.widget_frames["button"].geometry(), frame_rect)
            self.assertEqual(panel.widget_controls["button"].size(), control_size)
        scroll = panel.canvas_scroll
        scroll.horizontalScrollBar().setValue(100)
        scroll.verticalScrollBar().setValue(100)
        point = QPoint(3 * 16 + 1, 2 * 16 + 1)
        self.assertEqual(panel._cell_from_global_in_parent(None, panel.canvas.mapToGlobal(point)), (2, 3))
        saved = panel._panel_data()
        panel._load_panel_data(saved)
        self.assertEqual(panel.grid_cell_size, 16)
        panel.resize(2000, 1700)
        self.app.processEvents()
        self.assertGreater(panel.canvas.width(), canvas_size.width())
        self.assertGreater(panel.canvas.height(), canvas_size.height())
        self.assertGreaterEqual(panel.grid_cols, initial_grid[1])
        self.assertGreaterEqual(panel.grid_rows, initial_grid[0])
        self.assertEqual(panel.canvas.width(), panel.grid_cols * 16)
        self.assertEqual(panel.canvas.height(), panel.grid_rows * 16)
        image = panel.canvas.grab().toImage()
        first_extra_cell = image.pixelColor((initial_grid[1] + 1) * 16 + 2, 18)
        second_extra_cell = image.pixelColor((initial_grid[1] + 2) * 16 + 2, 18)
        self.assertNotEqual(first_extra_cell, second_extra_cell)
        self.assertEqual(panel.widget_controls["button"].size(), control_size)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
        panel.resize(1000, 700)
        self.app.processEvents()
        self.assertEqual(panel.canvas.width(), panel.grid_cols * panel.grid_cell_size)
        self.assertEqual(panel.canvas.height(), panel.grid_rows * panel.grid_cell_size)
        cfg = next(c for c in panel.widgets_config if c["id"] == "button")
        panel._show_drag_preview(cfg, panel.grid_rows, panel.grid_cols)
        preview_rect = panel._drag_preview_band.geometry()
        self.assertEqual(preview_rect.width(), cfg["col_span"] * panel.grid_cell_size)
        self.assertEqual(preview_rect.height(), cfg["row_span"] * panel.grid_cell_size)
        self.assertLessEqual(preview_rect.right() + 1, panel.canvas.width())
        panel._hide_drag_preview()
        panel._load_panel_data({"grid": {"rows": 12, "cols": 12}, "widgets": []})
        self.app.processEvents()
        self.assertEqual(panel.canvas.minimumWidth(), panel.grid_cols * 16)
        self.assertEqual(panel.canvas.width(), panel.grid_cols * 16)
        self.assertGreaterEqual(panel.canvas.width(), panel.canvas_scroll.viewport().width())
        self.assertGreaterEqual(panel.canvas.height(), panel.canvas_scroll.viewport().height())
        panel.close()
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
