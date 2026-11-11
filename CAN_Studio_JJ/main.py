import sys
import os
import json
import platform
import ctypes
from datetime import date, datetime
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPalette

# User panel edit-mode password settings (modifiable by code)
USER_PANEL_EDIT_PASSWORD_ENABLED = True
USER_PANEL_EDIT_PASSWORD = "1234"
USER_PANEL_COMMUNICATION_PASSWORD = "1234"
# Last usable day (inclusive): YYYYMMDD integer encoded as 8 hex digits, without 0x.
PROGRAM_EXPIRATION_DATE = "01354DA9"


def check_program_expiration(today=None):
    """Reject expired or malformed validity settings before opening any windows."""
    try:
        value = PROGRAM_EXPIRATION_DATE
        if not isinstance(value, str) or len(value) != 8 or any(c not in '0123456789abcdefABCDEF' for c in value):
            raise ValueError('Expected 8 hexadecimal digits')
        date_text = str(int(value, 16))
        if len(date_text) != 8:
            raise ValueError('Decoded date must be YYYYMMDD')
        expiration = datetime.strptime(date_text, '%Y%m%d').date()
        usable = (today if today is not None else date.today()) <= expiration
    except ValueError:
        usable = False
    if not usable:
        QMessageBox.warning(None, 'Program Unavailable', 'This program can no longer be used.')
    return usable

def get_launch_options():
    """Read deployment flags beside the executable, independent of saved sessions."""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
    from src.app_info import pk_path as resolve_pk_path
    pk_path = resolve_pk_path(base_dir)
    options = {'viewer_mode_only': False, 'user_panel_only': False}
    
    if os.path.exists(pk_path):
        try:
            with open(pk_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for key in options:
                        options[key] = data.get(key) is True
        except Exception:
            pass
            
    # Panel-only deployment takes precedence over the legacy log viewer flag.
    if options['user_panel_only']:
        options['viewer_mode_only'] = False
    return options


def get_viewer_mode():
    return get_launch_options()['viewer_mode_only']

if __name__ == '__main__':
    if platform.system() == 'Windows':
        try:
            myappid = 'mycompany.myproduct.subproduct.version'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass
            
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
            
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Some Linux desktop themes (e.g. Raspberry Pi PiXtrix) report white tooltip
    # text on Fusion's pale-yellow tooltip background, making tooltips unreadable.
    palette = app.palette()
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffdc"))
    palette.setColor(QPalette.ToolTipText, QColor("#000000"))
    app.setPalette(palette)
    ui_font = app.font()
    ui_font.setFamily("Consolas")
    ui_font.setStyleHint(QFont.Monospace)
    ui_font.setFixedPitch(True)
    app.setFont(ui_font)

    if not check_program_expiration():
        sys.exit(1)
    
    # Windows에서 절전 모드 진입으로 인한 성능 저하 방지
    if platform.system() == 'Windows':
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        try:
            # 시스템이 절전 모드로 전환되거나 디스플레이가 꺼지는 것을 방지
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
        except Exception as e:
            print(f"[Warning] Could not prevent system sleep: {e}")

    exit_code = 1
    try:
        from src.main_window import UniversalCANMonitor
        options = get_launch_options()
        window = UniversalCANMonitor(
            viewer_only=options['viewer_mode_only'],
            user_panel_only=options['user_panel_only'],
            communication_password=USER_PANEL_COMMUNICATION_PASSWORD,
            user_panel_security={
                "enabled": USER_PANEL_EDIT_PASSWORD_ENABLED,
                "password": USER_PANEL_EDIT_PASSWORD,
            },
        )
        if options['user_panel_only']:
            window.tx_panel.stop_all_timers()
            panel_opened = (not getattr(window, '_panel_open_cancelled', False)
                            and window.open_user_panel())
            if not panel_opened:
                window.close()
        else:
            window.show()
        exit_code = app.exec_() if not options['user_panel_only'] or panel_opened else 0
    finally:
        # 프로그램 종료 시, 시스템 절전 방지 설정을 원래대로 복원
        if platform.system() == 'Windows':
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception as e:
                print(f"[Warning] Could not restore system sleep settings: {e}")

    sys.exit(exit_code)
