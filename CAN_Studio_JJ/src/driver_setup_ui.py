"""Driver setup guidance in the main window (Windows).

Rules
* Asked only after a search the user started (Refresh, '장치 검색' menu, choosing a CAN
  method); automatic searches (start-up, session restore) only mark the channel list.
* At most one question per user action, even when several adapters need a driver; the rest
  are mentioned and offered on the next Refresh.
* An adapter that was asked once is not asked again until the program restarts.
* While a driver operation runs, no other question is shown.
"""
import logging
import threading

from PyQt5.QtCore import QObject, Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QMessageBox, QProgressDialog

from src import driver_setup as ds

log = logging.getLogger(__name__)
WATCH_SECONDS = 120


class DriverSetupController(QObject):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.asked = set()          # instance IDs already asked in this session
        self.busy = False
        self._watch_timer = None

    # ---- what the channel list shows --------------------------------------------------
    @staticmethod
    def products_for(method):
        return None if method == 'all' else {method}

    def issues(self, method):
        if not ds.supported() or getattr(self.main, 'viewer_only', False):
            return []
        try:
            return ds.find_issues(self.products_for(method))
        except Exception as exc:   # detection must never break the channel search
            log.warning('driver check failed: %s', exc)
            return []

    # ---- asking -----------------------------------------------------------------------
    def offer_later(self, buses):
        """Ask after the current search has finished filling the combo boxes."""
        QTimer.singleShot(0, lambda: self.offer(buses))

    def offer(self, buses):
        """At most one question for ``buses`` (the first BUS with an adapter that needs a driver)."""
        main = self.main
        if self.busy:
            return
        for bus in buses:
            if main.buses.get(bus) is not None:
                continue
            pending = [issue for issue in self.issues(main.selected_method(bus))
                       if issue.instance_id not in self.asked]
            if pending:
                break
        else:
            return
        issue = pending[0]
        self.asked.add(issue.instance_id)
        others = len(pending) - 1
        note = (f'\n\n드라이버가 필요한 다른 장치 {others}개는 이 작업 후 Refresh를 누르면 이어서 안내합니다.'
                if others else '')
        handler = {'gs_usb': self._offer_gs_usb, 'pcan': self._offer_pcan, 'kvaser': self._offer_kvaser}[issue.product]
        main.log_bus_event(bus, f'드라이버 확인: {issue.name} · {issue.hint}', 'WARN', issue.instance_id, issue=False)
        handler(bus, issue, note)

    def _ask(self, title, text):
        box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.No, self._parent())
        box.setDefaultButton(QMessageBox.Yes)
        return box.exec_() == QMessageBox.Yes

    def _parent(self):
        panel = getattr(self.main, 'user_panel_window', None)
        return panel if panel is not None and panel.isVisible() else self.main

    def _info(self, title, text, warning=False):
        (QMessageBox.warning if warning else QMessageBox.information)(self._parent(), title, text)

    def _offer_download(self, title, text, url):
        if self._ask(title, text + '\n\n다운로드 페이지를 열까요?'):
            QDesktopServices.openUrl(QUrl(url))

    def _offer_gs_usb(self, bus, issue, note):
        inf, error = ds.gs_usb_fix_plan(issue)
        if error:
            self._info('candleLight 드라이버', f'{issue.name}: {error}\n\n수동 방법: Driver\\candleLight_WinUSB\\README.txt', True)
            return
        current = issue.service or '없음'
        if not self._ask('candleLight 드라이버 전환',
                         f'candleLight 어댑터가 연결되어 있지만 드라이버가 WinUSB가 아니라(현재: {current}) '
                         f'사용할 수 없습니다.\n\nWinUSB 드라이버로 전환할까요? 관리자 권한 확인 창이 표시됩니다.{note}'):
            return
        commands = ([f'/delete-driver {inf} /uninstall /force'] if inf else []) + ['/scan-devices']
        self._run(bus, issue, 'candleLight 드라이버 전환', 'WinUSB 드라이버로 전환하는 중입니다…',
                  lambda: ds.run_elevated(*ds.pnputil_command(*commands)), replug=True)

    def _offer_pcan(self, bus, issue, note):
        inf = ds.pcan_inf()
        if not inf:
            self._offer_download('PCAN 드라이버', f'{issue.name}이(가) 연결되어 있지만 PCAN 드라이버가 없습니다.\n'
                                 'Driver\\pcan_drivers 폴더도 없습니다.' + note, ds.PEAK_DOWNLOAD_URL)
            return
        if not self._ask('PCAN 드라이버 설치',
                         f'{issue.name}이(가) 연결되어 있지만 PCAN 드라이버가 설치되어 있지 않습니다.\n\n'
                         f'PCAN-USB 드라이버를 설치할까요? 관리자 권한 확인 창이 표시됩니다.{note}'):
            return
        self._run(bus, issue, 'PCAN 드라이버 설치', 'PCAN-USB 드라이버를 설치하는 중입니다…',
                  lambda: ds.run_elevated(*ds.pnputil_command(f'/add-driver "{inf}" /install')), replug=True,
                  failure_url=ds.PEAK_DOWNLOAD_URL)

    def _offer_kvaser(self, bus, issue, note):
        setup = ds.kvaser_setup()
        if not setup:
            self._offer_download('Kvaser 드라이버', f'{issue.name}이(가) 연결되어 있지만 Kvaser 드라이버가 없습니다.\n'
                                 'Driver\\kvaser_drivers 폴더에 설치 파일도 없습니다.' + note, ds.KVASER_DOWNLOAD_URL)
            return
        if not self._ask('Kvaser 드라이버 설치',
                         f'{issue.name}이(가) 연결되어 있지만 Kvaser 드라이버가 설치되어 있지 않습니다.\n\n'
                         'Kvaser 드라이버 설치 프로그램을 실행할까요? 설치가 끝나면 CAN Studio JJ를 다시 시작해야 '
                         f'Kvaser 장치를 사용할 수 있습니다.{note}'):
            return
        self._run(bus, issue, 'Kvaser 드라이버 설치', 'Kvaser 드라이버 설치 프로그램이 끝나기를 기다리는 중입니다…',
                  lambda: ds.run_elevated(setup, '', show_window=True), replug=False, restart=True,
                  failure_url=ds.KVASER_DOWNLOAD_URL)

    # ---- running and waiting ----------------------------------------------------------
    def _run(self, bus, issue, title, waiting_text, job, replug, restart=False, failure_url=None):
        self.busy = True
        outcome = {}

        def work():
            try:
                outcome['code'] = job()
            except Exception as exc:
                outcome['error'] = str(exc)

        worker = threading.Thread(target=work, name='driver setup', daemon=True)
        dialog = QProgressDialog(waiting_text, None, 0, 0, self._parent())
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setCancelButton(None)
        dialog.show()
        worker.start()
        poll = QTimer(self)

        def check():
            if worker.is_alive():
                return
            poll.stop()
            dialog.close()
            self.busy = False
            self._finished(bus, issue, title, outcome, replug, restart, failure_url)

        poll.timeout.connect(check)
        poll.start(100)

    def _finished(self, bus, issue, title, outcome, replug, restart, failure_url):
        main = self.main
        code = outcome.get('code')
        if 'error' in outcome:
            text, ok = f'실행하지 못했습니다: {outcome["error"]}', False
        elif code is None:
            text, ok = '관리자 권한이 승인되지 않아 진행하지 않았습니다.', False
        elif code in (ds.PNPUTIL_OK if replug else (0,)):
            text, ok = '', True
        else:
            text, ok = f'완료되지 않았습니다 (종료 코드 {code}).', False
        if not ok:
            main.log_bus_event(bus, f'{title} 실패: {text}', 'WARN', issue.instance_id, issue=False)
            if failure_url and code is not None:
                self._offer_download(title, text, failure_url)
            else:
                self._info(title, text, True)
            return
        main.log_bus_event(bus, f'{title} 완료: {issue.name}', 'INFO', issue.instance_id, issue=False)
        if restart:
            self._info(title, '설치를 마쳤습니다. CAN Studio JJ를 종료한 뒤 다시 실행해 주세요.\n'
                              '(Kvaser 라이브러리는 프로그램을 다시 시작해야 인식됩니다.)')
            return
        if replug:
            self._info(title, f'{issue.name}: 드라이버 작업을 마쳤습니다.\n\nUSB를 분리했다가 다시 연결해 주세요. '
                              '다시 연결되면 장치를 자동으로 다시 검색합니다.')
            self._watch(bus, issue)

    def _watch(self, bus, issue):
        """Re-search automatically once the adapter is connected with a working driver."""
        if self._watch_timer is not None:
            self._watch_timer.stop()
        timer = self._watch_timer = QTimer(self)
        state = {'ticks': 0}

        def tick():
            state['ticks'] += 1
            try:
                ready = any(ok for record, ok in ds.connected_adapters({issue.product}))
            except Exception:
                ready = False
            if ready or state['ticks'] >= WATCH_SECONDS:
                timer.stop()
                if ready and self.main.buses.get(bus) is None:
                    self.main.search_can_channels(bus)
                    self.main.statusBar().showMessage(f'BUS {bus}: {issue.label} 드라이버 적용 확인 · 장치를 다시 검색했습니다.', 8000)

        timer.timeout.connect(tick)
        timer.start(1000)
