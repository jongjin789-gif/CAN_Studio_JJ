from src.bus_config import BUS_IDS
"""Menu actions share the existing buttons' availability and behavior."""
import sys
from pathlib import Path
from PyQt5.QtWidgets import QAction, QDialog, QMessageBox, QTextBrowser, QVBoxLayout
from src.utils import get_resource_path
from src.app_info import APP_NAME


def install_main_menu(main):
    bar = main.menuBar()
    mirrored = []
    availability = []

    def action(menu, text, callback, button=None, shortcut=None):
        item = QAction(text, main)
        item.triggered.connect(lambda checked=False: callback())
        if shortcut:
            item.setShortcut(shortcut)
        menu.addAction(item)
        if button:
            mirrored.append((item, button))
        return item

    files = bar.addMenu('파일')
    action(files, '새 화면', main.session.new_screen)
    files.addSeparator()
    if not main.viewer_only:
        action(files, '통합 설정 저장…', main.session.save_dialog, shortcut='Ctrl+Shift+S')
        action(files, '통합 설정 불러오기…', main.session.load_dialog, shortcut='Ctrl+Shift+O')
        files.addSeparator()
    action(files, 'CAN 로그 열기… (TRC/ASC/BLF)', main.btn_open_log.click, main.btn_open_log)
    files.addSeparator()
    action(files, '종료', main.close)

    database = bar.addMenu('데이터베이스')
    for bus in BUS_IDS:
        menu = database.addMenu(f'BUS {bus}')
        action(menu, 'DBC/SYM 등록…', main.btn_load_db[bus].click, main.btn_load_db[bus])
        action(menu, '선택 DB 등록 해제', lambda b=bus: main.remove_selected_db_file(b))
        action(menu, '선택 DB 내보내기…', lambda b=bus: main.session.export_databases(b, selected=True))
        action(menu, 'BUS의 모든 DB 내보내기…', lambda b=bus: main.session.export_databases(b))
    database.addSeparator()
    action(database, '전체 DB 내보내기…', main.session.export_databases)
    action(database, '전체 DB 등록 해제', main.btn_clear_all_db.click, main.btn_clear_all_db)

    if not main.viewer_only:
        can_menu = bar.addMenu('CAN')
        for bus in BUS_IDS:
            menu = can_menu.addMenu(f'BUS {bus}')
            action(menu, '장치 검색', lambda b=bus: main.search_can_channels(b), main.btn_refresh[bus])
            opened = action(menu, '연결', lambda b=bus: main.open_can(b))
            closed = action(menu, '연결 해제', lambda b=bus: main.close_can(b))
            availability.append((opened, lambda b=bus: main.can_open(b)))
            availability.append((closed, lambda b=bus: main.buses[b] is not None and b not in main._bus_busy))
        action(can_menu, '전체 연결 해제', main.close_can)
        can_menu.addSeparator()
        action(can_menu, '신호 통계 / 수신 타임아웃…', main.open_signal_stats)
        action(can_menu, 'BUS 게이트웨이…', main.open_gateway)
        trigger_clear = action(can_menu, '트리거 해제', main.clear_trigger)
        availability.append((trigger_clear, lambda: main.trigger.config is not None))
        tx = bar.addMenu('송신')
        for label, button in (('패킷 생성…', main.tx_panel.btn_add),
                              ('TX 목록 저장…', main.tx_panel.btn_save),
                              ('TX 목록 불러오기…', main.tx_panel.btn_load)):
            action(tx, label, button.click, button)
        tx.addSeparator()
        action(tx, '전체 송신 정지', main.session.stop_all, shortcut='Ctrl+Shift+X')
        action(tx, '전체 패킷 삭제', main.tx_panel.btn_clear_all.click, main.tx_panel.btn_clear_all)

    help_menu = bar.addMenu('도움말')
    for label, filename in (('사용 설명서', 'MANUAL.md'),
                             ('UserPanel·시퀀스 설명서', 'USER_PANEL_SEQUENCE.md'),
                             ('변경 내역', 'USER_PANEL_RELEASE_NOTES.md')):
        action(help_menu, label, lambda name=filename, title=label: show_document(main, name, title))
    action(help_menu, '프로그램 정보', lambda: QMessageBox.information(
        main, '프로그램 정보', f'{APP_NAME} {main.get_app_version()}\n통합 설정 형식: v2 (.pjjsettings) / v1 불러오기 지원'))

    def refresh():
        for item, button in mirrored:
            item.setEnabled(button.isEnabled())
        for item, predicate in availability:
            item.setEnabled(predicate())
    # Refresh on opening any menu, including submenus, to match the controls.
    def connect_menu(menu):
        menu.aboutToShow.connect(refresh)
        for item in menu.actions():
            if item.menu():
                connect_menu(item.menu())
    for item in bar.actions():
        connect_menu(item.menu())
    refresh()


def show_document(main, filename, title):
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
    path = base / filename
    if not path.exists():
        path = Path(get_resource_path(filename))
    try:
        contents = path.read_text(encoding='utf-8')
    except OSError as exc:
        QMessageBox.warning(main, title, f'설명서를 열지 못했습니다: {exc}')
        return
    dialog = QDialog(main)
    dialog.setWindowTitle(title)
    dialog.resize(850, 650)
    browser = QTextBrowser(dialog)
    browser.setOpenExternalLinks(False)
    browser.setMarkdown(contents)
    QVBoxLayout(dialog).addWidget(browser)
    dialog.exec_()
