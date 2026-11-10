import unittest
from datetime import date
from unittest.mock import patch

import main


class ProgramExpirationTests(unittest.TestCase):
    def test_before_and_on_last_day_are_allowed_without_warning(self):
        with patch.object(main, 'PROGRAM_EXPIRATION_DATE', '01352897'), \
                patch.object(main.QMessageBox, 'warning') as warning:
            self.assertTrue(main.check_program_expiration(date(2026, 10, 14)))
            self.assertTrue(main.check_program_expiration(date(2026, 10, 15)))
            warning.assert_not_called()

    def test_day_after_expiration_shows_english_warning(self):
        with patch.object(main, 'PROGRAM_EXPIRATION_DATE', '01352897'), \
                patch.object(main.QMessageBox, 'warning') as warning:
            self.assertFalse(main.check_program_expiration(date(2026, 10, 16)))
            warning.assert_called_once_with(
                None, 'Program Unavailable', 'This program can no longer be used.')

    def test_invalid_settings_are_rejected(self):
        for value in ('01352586', '1352897', '0x01352897', '0135289G', '00000000', 'FFFFFFFF', '', None):
            with self.subTest(value=value), \
                    patch.object(main, 'PROGRAM_EXPIRATION_DATE', value), \
                    patch.object(main.QMessageBox, 'warning') as warning:
                self.assertFalse(main.check_program_expiration(date(2026, 10, 14)))
                warning.assert_called_once()

    def test_default_check_uses_local_system_date(self):
        with patch.object(main, 'PROGRAM_EXPIRATION_DATE', '01352897'), \
                patch.object(main, 'date') as clock, \
                patch.object(main.QMessageBox, 'warning'):
            clock.today.return_value = date(2026, 10, 16)
            self.assertFalse(main.check_program_expiration())
            clock.today.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
