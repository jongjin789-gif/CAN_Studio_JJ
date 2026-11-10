import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from src.db_frame_format import load_sym_with_fd
from src.user_panel_v2.dbc_identity import database_token
from src.user_panel_v2.window import UserPanelWindow, _PACKAGE_FILTER
from src.user_panel_v2.storage import save_bundle


class PanelFileDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'monitor.sym'
        self.raw = (b'FormatVersion=6.0\n{SENDRECEIVE}\n[Monitor]\n'
                    b'ID=130h\nType=Standard\nLen=1\nVar=Value unsigned 0,8\n')
        self.source.write_bytes(self.raw)
        self.messages = {bus: {} for bus in (1, 2, 3, 4)}
        self.messages[1].update({m.frame_id: m for m in load_sym_with_fd(self.raw.decode()).messages})
        self.owner = SimpleNamespace(
            buses={}, bus_capabilities={},
            database_content_signature={str(bus): [] for bus in (1, 2, 3, 4)},
            get_db_file_paths_by_bus=Mock(return_value={1: [str(self.source)]}))
        self.owner.database_content_signature['1'] = [database_token(self.source.name, self.raw)]
        self.owner.replace_db_files_by_bus = Mock(side_effect=self.replace_databases)
        self.panel = UserPanelWindow(self.owner, self.messages)
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.close)
        self.panel._load_panel_data(dict(
            title='Portable monitor', pages=[dict(id='main', name='Main'), dict(id='extra', name='Inspect')],
            active_page_id='extra', widgets=[dict(
                id='value', page_id='main', widget_type='label', behavior='rx',
                binding=dict(bus=1, can_id=0x130, signal_name='Value', dlc=1,
                             start_bit=0, bit_length=8))]))
        info = patch('src.user_panel_v2.window.QMessageBox.information')
        info.start()
        self.addCleanup(info.stop)

    def replace_databases(self, paths):
        self.assertEqual(self.panel.mode, 'standby')
        for bus in (1, 2, 3, 4):
            self.messages[bus].clear()
            tokens = []
            for filename in paths[bus]:
                raw = Path(filename).read_bytes()
                db = load_sym_with_fd(raw.decode('utf-8-sig'))
                self.messages[bus].update({m.frame_id: m for m in db.messages})
                tokens.append(database_token(filename, raw))
            self.owner.database_content_signature[str(bus)] = tokens

    def test_default_save_and_general_load_transfer_panel_and_embedded_sym(self):
        stem = self.folder / 'portable'
        expected = copy.deepcopy(self.panel._panel_data())
        with patch('src.user_panel_v2.window.QFileDialog.getSaveFileName',
                   return_value=(str(stem), _PACKAGE_FILTER)) as dialog:
            self.panel.save_panel_to_file()
        self.assertEqual(dialog.call_args.args[3], _PACKAGE_FILTER)
        package = stem.with_suffix('.pjjupkg')
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(json.loads(archive.read('panel/panel.json')), expected)
            self.assertEqual(archive.read('db/bus1/000_monitor.sym'), self.raw)
        # The package remains usable after the external source file is gone.
        self.source.unlink()
        self.messages[1].clear()
        self.panel.setWindowTitle('Changed')
        make_temp = tempfile.mkdtemp
        with patch('src.user_panel_v2.window.QFileDialog.getOpenFileName',
                   return_value=(str(package), '')) as dialog, \
             patch.object(self.panel, '_authorize_communication', return_value=True) as authorize, \
             patch('src.user_panel_v2.storage.tempfile.mkdtemp',
                   side_effect=lambda prefix: make_temp(prefix=prefix, dir=self.temp.name)):
            self.panel.load_panel_from_file()
        self.assertEqual(dialog.call_args.args[3], _PACKAGE_FILTER)
        authorize.assert_called_once()
        self.owner.replace_db_files_by_bus.assert_called_once()
        self.assertEqual(self.panel.windowTitle(), 'Portable monitor')
        self.assertEqual(self.panel.pages, expected['pages'])
        self.assertEqual(self.panel.active_page_id, 'extra')
        self.assertEqual(self.panel.mode, 'standby')
        self.assertEqual(self.messages[1][0x130].signals[0].name, 'Value')

    def test_json_file_is_rejected_without_changing_panel_or_databases(self):
        filename = self.folder / 'settings.upp.json'
        filename.write_text(json.dumps(self.panel._panel_data()), encoding='utf-8')
        self.panel.setWindowTitle('Keep current panel')
        before = copy.deepcopy(self.panel._panel_data())
        with patch('src.user_panel_v2.window.QFileDialog.getOpenFileName',
                   return_value=(str(filename), '')), \
             patch('src.user_panel_v2.window.QMessageBox.warning') as warning, \
             patch.object(self.panel, '_authorize_communication') as authorize:
            self.panel.load_panel_from_file()
        warning.assert_called_once()
        self.assertIn('.pjjupkg', warning.call_args.args[2])
        authorize.assert_not_called()
        self.owner.replace_db_files_by_bus.assert_not_called()
        self.assertEqual(self.panel._panel_data(), before)

    def test_save_always_writes_a_package_and_accepts_uppercase_extension(self):
        for filename in ('portable', 'portable.PJJUPKG'):
            with self.subTest(filename=filename):
                path = self.folder / filename
                with patch('src.user_panel_v2.window.QFileDialog.getSaveFileName',
                           return_value=(str(path), _PACKAGE_FILTER)):
                    self.panel.save_panel_to_file()
                saved = path if filename.lower().endswith('.pjjupkg') else path.with_suffix('.pjjupkg')
                self.assertTrue(zipfile.is_zipfile(saved))
                with zipfile.ZipFile(saved) as archive:
                    self.assertEqual(json.loads(archive.read('panel/panel.json'))['title'], 'Portable monitor')
        self.assertFalse(list(self.folder.glob('*.upp.json')))

    def test_package_load_requires_existing_communication_authorization(self):
        before = copy.deepcopy(self.panel._panel_data())
        with patch('src.user_panel_v2.window.QFileDialog.getOpenFileName',
                   return_value=(str(self.folder / 'blocked.pjjupkg'), '')), \
             patch.object(self.panel, '_authorize_communication', return_value=False), \
             patch('src.user_panel_v2.window.load_bundle') as load:
            self.panel.load_panel_from_file()
        load.assert_not_called()
        self.owner.replace_db_files_by_bus.assert_not_called()
        self.assertEqual(self.panel._panel_data(), before)

    def test_malformed_packages_leave_panel_and_databases_unchanged(self):
        invalid = [
            dict(tx_packets=[None]),
            dict(tx_packets=[{}]),
            dict(widgets=[None]),
            dict(widgets=[dict(id='bad', binding=None)]),
            dict(widgets=[dict(id='bad', row='invalid')]),
            dict(widgets=[dict(id='a'), dict(id='a')]),
            dict(widgets=[dict(id='a', parent_id='b'), dict(id='b', parent_id='a')]),
            dict(pages=[dict(id=[], name='Invalid')]),
            dict(grid=None),
        ]
        before = copy.deepcopy(self.panel._panel_data())
        before_messages = dict(self.messages[1])
        for change in invalid:
            with self.subTest(change=change):
                bad = dict(before, title='Must not apply', **change)
                package = save_bundle(str(self.folder / 'bad'), bad, {1: [str(self.source)]})
                with patch.object(self.panel, '_authorize_communication', return_value=True), \
                        patch('src.user_panel_v2.window.show_error') as error:
                    self.panel._load_package_file(package)
                error.assert_called_once()
                self.owner.replace_db_files_by_bus.assert_not_called()
                self.assertEqual(self.panel._panel_data(), before)
                self.assertEqual(self.messages[1], before_messages)

    def test_cancelling_file_dialog_does_not_write_or_load(self):
        before = copy.deepcopy(self.panel._panel_data())
        with patch('src.user_panel_v2.window.QFileDialog.getSaveFileName', return_value=('', '')), \
             patch('src.user_panel_v2.window.QFileDialog.getOpenFileName', return_value=('', '')), \
             patch.object(self.panel, '_authorize_communication') as authorize:
            self.panel.save_panel_to_file()
            self.panel.load_panel_from_file()
        authorize.assert_not_called()
        self.owner.get_db_file_paths_by_bus.assert_not_called()
        self.owner.replace_db_files_by_bus.assert_not_called()
        self.assertEqual(self.panel._panel_data(), before)
        self.assertEqual([path.name for path in self.folder.iterdir()], ['monitor.sym'])


if __name__ == '__main__':
    unittest.main()
