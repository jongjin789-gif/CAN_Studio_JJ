import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

from src.user_panel_v2.color_picker import ColorPicker, COLOR_CHART, RECOMMENDED_COLORS
from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.storage import load_panel_json, save_panel_json
from src.user_panel_v2.window import UserPanelWindow


class PanelColorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, widgets):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(grid=dict(rows=36, cols=60), widgets=copy.deepcopy(widgets)))
        return panel

    def test_group_and_shape_editors_offer_clickable_colors(self):
        for kind in ('group_box', 'tab_container', 'shape_rect', 'shape_line'):
            with self.subTest(kind=kind):
                editor = WidgetConfigDialog({}, preset=dict(widget_type=kind, behavior='none'),
                                            live_preview_default=False)
                self.addCleanup(editor.close)
                self.assertFalse(editor.grp_shape.isHidden())
                editor.edit_stroke_color.chart_buttons[38].click()
                self.assertEqual(editor.get_config()['binding']['stroke_color'], COLOR_CHART[38])
                if kind == 'shape_line':
                    self.assertTrue(editor.edit_fill_color.isHidden())
                else:
                    self.assertFalse(editor.edit_fill_color.isHidden())
                    self.assertFalse(editor.chk_fill.isChecked())
                    editor.edit_fill_color.recommended_buttons[3].click()
                    binding = editor.get_config()['binding']
                    self.assertTrue(binding['fill'])
                    self.assertEqual(binding['fill_color'], RECOMMENDED_COLORS[3][1])
                    editor.chk_fill.setChecked(False)
                    restored = WidgetConfigDialog({}, preset=editor.get_config())
                    self.addCleanup(restored.close)
                    self.assertFalse(restored.chk_fill.isChecked())
                    self.assertEqual(restored.edit_fill_color.text(), RECOMMENDED_COLORS[3][1])

    def test_custom_color_accept_and_cancel(self):
        picker = ColorPicker('#E8F1FF')
        self.addCleanup(picker.close)
        selected = []
        picker.colorSelected.connect(selected.append)
        with patch('src.user_panel_v2.color_picker.QColorDialog.getColor', return_value=QColor('#123456')):
            picker.custom_button.click()
        self.assertEqual(picker.text(), '#123456')
        with patch('src.user_panel_v2.color_picker.QColorDialog.getColor', return_value=QColor()):
            picker.custom_button.click()
        self.assertEqual(picker.text(), '#123456')
        self.assertEqual(selected, ['#123456'])

    def test_nested_container_surfaces_keep_their_own_colors(self):
        panel = self.panel([
            dict(id='outer', widget_type='group_box', row_span=28, col_span=40,
                 binding=dict(fill=True, fill_color='#FFEDD5', stroke_color='#EA580C')),
            dict(id='tabs', widget_type='tab_container', parent_id='outer', row_span=16, col_span=28,
                 tabs=['A', 'B'], binding=dict(fill=True, fill_color='#CCFBF1', stroke_color='#0D9488')),
            dict(id='inner', widget_type='group_box', parent_id='tabs', row_span=8, col_span=16,
                 binding=dict(fill=True, fill_color='#EDE9FE', stroke_color='#7C3AED')),
            dict(id='value', widget_type='label', parent_id='inner', row_span=3, col_span=6),
        ])
        panel.show()
        self.app.processEvents()
        for key, expected in ((('outer', 0), '#ffedd5'), (('tabs', 0), '#ccfbf1'),
                              (('tabs', 1), '#ccfbf1'), (('inner', 0), '#ede9fe')):
            with self.subTest(page=key):
                canvas = panel._container_pages[key].canvas
                image = canvas.grab().toImage()
                self.assertEqual(image.pixelColor(image.width() - 2, image.height() - 2).name(), expected)
        # Parent fill must not turn an ordinary child card into a colored card.
        frame = panel.widget_frames['value']
        image = frame.grab().toImage()
        self.assertEqual(image.pixelColor(10, 2).name(), '#ffffff')

    def test_properties_apply_undo_redo_and_file_roundtrip(self):
        for kind in ('group_box', 'tab_container', 'shape_rect'):
            with self.subTest(kind=kind):
                panel = self.panel([dict(id='tool', widget_type=kind)])
                panel.selected_widget_id = 'tool'
                panel.selected_widget_ids = {'tool'}
                panel.properties.refresh(force=True)
                editor = panel.properties.editor
                editor.edit_fill_color.recommended_buttons[6].click()
                editor.edit_stroke_color.chart_buttons[32].click()
                editor.accept()
                saved = panel._panel_data()
                binding = saved['widgets'][0]['binding']
                self.assertTrue(binding['fill'])
                self.assertEqual(binding['fill_color'], '#FCE7F3')
                self.assertEqual(binding['stroke_color'], '#DC2626')
                panel.undo_edit()
                self.assertFalse(panel._cfg_by_id('tool')['binding']['fill'])
                panel.redo_edit()
                self.assertEqual(panel._cfg_by_id('tool')['binding'], binding)
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'colors.upp.json'
                    save_panel_json(path, saved)
                    restored = self.panel(load_panel_json(path)['widgets'])
                self.assertEqual(restored._cfg_by_id('tool')['binding'], binding)


if __name__ == '__main__':
    unittest.main()
