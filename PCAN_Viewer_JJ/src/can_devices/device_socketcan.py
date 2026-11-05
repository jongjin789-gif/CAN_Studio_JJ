"""Linux SocketCAN (can0, can1, ... / vcan0 ...), independent of the adapter vendor.

PCAN (peak_usb), Kvaser (kvaser_usb) and other adapters with kernel drivers all
appear as ``canX`` interfaces and are handled here the same way.
"""
import os
import subprocess
import time

import can
from PyQt5.QtWidgets import QInputDialog, QLineEdit

from src.bus_config import bitrate_value
from .device_base import CanDeviceBackend, DeviceOpenCancelled, DeviceOpenError


class SocketCanBackend(CanDeviceBackend):
    key = 'socketcan'
    label = 'SocketCAN'
    bustypes = ('socketcan',)
    platforms = ('Linux',)
    diag_context = 'SocketCAN 연결'
    open_failure_text = 'Failed to open Linux CAN channel for Bus {bus}:\n{error}'

    CMD_TIMEOUT_S = 10
    SUDO_PASSWORD_TTL_S = 60

    def __init__(self, host):
        super().__init__(host)
        self._sudo_password = ''

    # ---- channel discovery -------------------------------------------------
    def list_channels(self):
        names = []
        try:
            names = sorted(iface for iface in os.listdir('/sys/class/net')
                           if iface.startswith('can') or iface.startswith('vcan'))
        except Exception:
            pass  # /sys/class/net이 없는 비정상적인 리눅스 환경일 수 있음
        # vcan 인터페이스는 테스트용임을 명확히 표기합니다.
        return [(f"Test-Only ({ch})" if ch.startswith('vcan') else f"{ch} [SocketCAN]",
                 {'bustype': 'socketcan', 'handle': ch, 'is_fd': True}) for ch in names]

    def status_label(self, channel_data):
        return 'SocketCAN'

    # ---- connection --------------------------------------------------------
    def open(self, channel_data, settings):
        channel_handle = channel_data['handle']
        bitrate = bitrate_value(settings.bitrate_text)
        fd_enabled = False
        dbitrate = None
        if settings.data_enabled and settings.data_text not in ("Off", "N/A"):
            fd_enabled = True
            dbitrate = bitrate_value(settings.data_text)

        if not channel_handle.startswith('vcan'):  # 실제 CAN 하드웨어는 ip link 설정 수행
            down_cmd = ['ip', 'link', 'set', channel_handle, 'down']
            down_result = self._checked(self.run_ip_command(down_cmd))
            if down_result.returncode != 0:
                raise DeviceOpenError("CAN 인터페이스 설정 오류",
                                      f"{' '.join(down_cmd)}\n{down_result.stderr.strip()}")
            ip_cmd = ['ip', 'link', 'set', channel_handle, 'up', 'type', 'can', 'bitrate', str(bitrate)]
            if fd_enabled and dbitrate:
                ip_cmd.extend(['dbitrate', str(dbitrate), 'fd', 'on'])
                ip_cmd.extend(['fd-non-iso', 'on' if settings.fd_iso == 'Non-ISO' else 'off'])
            else:
                ip_cmd.extend(['fd', 'off'])
            res = self._checked(self.run_ip_command(ip_cmd))
            if res.returncode != 0:
                title = "명령 실행 오류" if self._is_root() else "sudo 인증 실패"
                raise DeviceOpenError(title,
                                      f"CAN 인터페이스 '{channel_handle}' 설정에 실패했습니다.\n"
                                      f"실행된 명령어: {' '.join(ip_cmd)}\n오류: {res.stderr.strip()}")
        else:  # vcan은 일반적으로 sudo 없이도 up 가능하지만, 실패 시 안내
            res = self._checked(self.run_ip_command(['ip', 'link', 'set', channel_handle, 'up']))
            if res.returncode != 0:
                raise DeviceOpenError("권한 오류 가능성",
                                      f"가상 CAN 인터페이스 '{channel_handle}' 활성화에 실패했습니다.\n"
                                      f"{res.stderr.strip()}")

        kwargs = {'bustype': 'socketcan', 'channel': channel_handle}
        if fd_enabled:
            kwargs['fd'] = True
        return can.interface.Bus(**kwargs), fd_enabled

    @staticmethod
    def _checked(result):
        if result is None:
            raise DeviceOpenCancelled()
        return result

    @staticmethod
    def _is_root():
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

    # ---- privileged ip commands -------------------------------------------
    @staticmethod
    def ip_binary():
        """PATH 변조 영향을 받지 않도록 시스템 ip 경로를 우선 사용합니다."""
        for candidate in ('/usr/sbin/ip', '/sbin/ip', '/usr/bin/ip', '/bin/ip'):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return 'ip'

    def run_subprocess(self, command, **kwargs):
        try:
            return subprocess.run(command, capture_output=True, text=True,
                                  timeout=self.CMD_TIMEOUT_S, **kwargs)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                command, 1, '', f'명령이 {self.CMD_TIMEOUT_S}초 안에 끝나지 않았습니다.')

    def ask_sudo_password(self):
        password, accepted = QInputDialog.getText(
            self.host, 'sudo 인증', 'CAN 인터페이스 설정을 위한 sudo 비밀번호:', QLineEdit.Password)
        return password if accepted and password else None

    def run_ip_command(self, command):
        """Run an ``ip`` command, elevating with sudo only on a permission error.

        Returns the CompletedProcess, or ``None`` when the user cancelled the prompt.
        """
        if command and command[0] == 'ip':
            command = [self.ip_binary(), *command[1:]]
        result = self.run_subprocess(command)
        if result.returncode == 0:
            return result
        try:
            is_root = os.geteuid() == 0
        except AttributeError:
            return result
        if is_root:
            return result
        error_text = f'{result.stderr}\n{result.stdout}'.lower()
        if 'operation not permitted' not in error_text and 'permission denied' not in error_text:
            return result

        # 1) sudo 자체 인증 캐시(timestamp)가 살아 있으면 비밀번호 없이 실행합니다.
        elevated = self.run_subprocess(['sudo', '-n', '--', *command])
        sudo_error = f'{elevated.stderr}\n{elevated.stdout}'.lower()
        if elevated.returncode == 0 or ('password' not in sudo_error and 'terminal' not in sudo_error):
            # 성공했거나, sudo 인증은 통과했지만 ip 명령 자체가 실패한 경우: 다시 묻지 않습니다.
            return elevated

        # 2) 비밀번호는 한 번의 연결 작업(down/up 등 연속 명령)에만 짧게 재사용하고 버립니다.
        password = ''
        cached = self._sudo_password
        if isinstance(cached, tuple) and time.monotonic() - cached[1] < self.SUDO_PASSWORD_TTL_S:
            password = cached[0]
        self._sudo_password = ''
        if not password:
            password = self.ask_sudo_password()
            if not password:
                return None

        elevated = self.run_subprocess(['sudo', '-S', '-p', '', '--', *command],
                                       input=password + '\n')
        if elevated.returncode == 0:
            self._sudo_password = (password, time.monotonic())
        return elevated
