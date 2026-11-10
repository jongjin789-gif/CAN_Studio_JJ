"""Copyable diagnostics, including the original worker-thread traceback."""
import datetime
import platform
import sys
import traceback

from PyQt5.QtCore import Qt, QT_VERSION_STR, PYQT_VERSION_STR
from PyQt5.QtWidgets import QApplication, QMessageBox


def diagnostic_details(feature, context=None):
    lines = [f'발생 시각: {datetime.datetime.now().astimezone().isoformat(timespec="seconds")}',
             f'기능: {feature}', f'OS: {platform.platform()}',
             f'Python: {platform.python_version()} / Qt: {QT_VERSION_STR} / PyQt: {PYQT_VERSION_STR}',
             f'실행 형태: {"EXE" if getattr(sys, "frozen", False) else "Python"}']
    for key, value in (context or {}).items():
        lines.append(f'{key}: {value}')
    exc = sys.exc_info()[1]
    if exc is not None:
        lines.append(f'예외 종류: {type(exc).__module__}.{type(exc).__name__}')
        code = getattr(exc, 'error_code', None)
        if code is not None:
            lines.append(f'오류 코드: {code}')
        lines.extend(['원본 예외 / 호출 경로:', traceback.format_exc()])
    else:
        lines.extend(['알림 호출 경로 (원본 예외 정보 없음):',
                      ''.join(traceback.format_stack(limit=12)[:-1])])
    return '\n'.join(lines)


def show_error(parent, title, message, *, details=None, context=None,
               icon=QMessageBox.Critical):
    report = f'{title}\n{message}\n\n' + (
        details if details is not None else diagnostic_details(title, context))
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(icon)
    box.setTextFormat(Qt.PlainText)
    box.setText(message)
    box.setInformativeText('원인 확인을 요청할 때 [진단 내용 복사]로 복사한 내용을 함께 보내주세요.')
    box.setStandardButtons(QMessageBox.Ok)
    box.setDetailedText(report)
    # Qt's built-in expandable details button uses the application's locale.
    detail_button = next((b for b in box.buttons()
                          if box.buttonRole(b) == QMessageBox.ActionRole), None)
    if detail_button is not None:
        detail_button.setText('자세히…')
        expanded = [False]
        def update_detail_label():
            expanded[0] = not expanded[0]
            detail_button.setText('간단히' if expanded[0] else '자세히…')
        detail_button.clicked.connect(update_detail_label)
    copy_button = box.addButton('진단 내용 복사', QMessageBox.ActionRole)
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
    return box.exec_()
