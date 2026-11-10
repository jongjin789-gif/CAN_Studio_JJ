import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication, QMessageBox
from src.error_dialog import diagnostic_details, show_error
from src.can_threads import CANReceiverThread


class ErrorDiagnosticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_exception_retains_type_and_origin(self):
        try:
            raise ValueError('sample failure')
        except ValueError:
            details = diagnostic_details('test operation', {'BUS': 2})
        self.assertIn('ValueError: sample failure', details)
        self.assertIn('test_error_dialog.py', details)
        self.assertIn('BUS: 2', details)

    def test_receiver_preserves_traceback_before_notifying_gui(self):
        class Bus:
            channel_info = 'fake BUS'
            def recv(self, timeout):
                raise RuntimeError('The CAN controller was read too late')
        worker = CANReceiverThread(Bus(), {})
        reports = []
        worker.error_signal.connect(lambda text: reports.append((text, worker.last_error_details)))
        worker.run()
        self.assertEqual(len(reports), 1)
        self.assertIn('Rx Error:', reports[0][0])
        self.assertIn('can_threads.py', reports[0][1])
        self.assertIn('RuntimeError', reports[0][1])
        self.assertIn('fake BUS', reports[0][1])

    def test_details_expand_and_copy_original_report(self):
        def inspect(box):
            self.assertIn('original worker traceback', box.detailedText())
            detail = next(b for b in box.buttons() if b.text() == '자세히…')
            detail.click()
            self.assertEqual(detail.text(), '간단히')
            detail.click()
            self.assertEqual(detail.text(), '자세히…')
            copy = next(b for b in box.buttons() if b.text() == '진단 내용 복사')
            copy.click()
            self.assertIn('original worker traceback', self.app.clipboard().text())
            self.assertIn('read too late', self.app.clipboard().text())
            return QMessageBox.Ok
        with patch.object(QMessageBox, 'exec_', inspect):
            show_error(None, 'BUS 1', 'read too late', details='original worker traceback')


if __name__ == '__main__':
    unittest.main()
