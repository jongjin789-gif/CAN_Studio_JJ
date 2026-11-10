from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QSpinBox,
                             QLineEdit, QCheckBox, QDialogButtonBox, QMessageBox, QComboBox)
from src.tx_crc import parse_extra, validate_crc


class SignalCRCDialog(QDialog):
    def __init__(self, signal, mode, length, config=None, parent=None):
        super().__init__(parent)
        self.signal, self.mode, self.length = signal, mode, length
        self.config = None
        width = 8 if mode == 'crc8' else 16
        self.setWindowTitle(f'{signal.name} — CRC-{width} 설정')
        self.resize(480, 360)
        cfg = config or {}
        layout = QVBoxLayout(self)
        help_text = QLabel('바이트 번호는 0부터 시작합니다. 예: 2~31 → 시작 2, 사이즈 30\n'
                           '추가 HEX 데이터는 선택 범위 뒤에 입력 순서대로 붙여 계산합니다.\n'
                           '결과는 Factor/Offset 없이 기록합니다. CRC-16 결과 순서는 아래에서 선택합니다.')
        layout.addWidget(help_text)
        form = QFormLayout()
        layout.addLayout(form)
        self.start = QSpinBox()
        self.start.setRange(0, 63)
        self.start.setValue(cfg.get('start', 0))
        self.size = QSpinBox()
        self.size.setRange(1, 64)
        self.size.setValue(cfg.get('size', 1))
        form.addRow('연산 시작 바이트 (10진수)', self.start)
        form.addRow('연산 사이즈 (바이트)', self.size)
        self.extra = QLineEdit(' '.join(f'{b:02X}' for b in cfg.get('extra', [])))
        self.extra.setPlaceholderText('예: 00 00 또는 0x0000 (빈 값 = 추가 없음)')
        form.addRow('추가 연산 데이터 (HEX)', self.extra)
        self.result_byte_order = QComboBox()
        self.result_byte_order.addItem('LSB 먼저 (Intel / Little Endian)', 'little_endian')
        self.result_byte_order.addItem('MSB 먼저 (Motorola / Big Endian)', 'big_endian')
        default_order = signal.byte_order if config else 'little_endian'
        self.result_byte_order.setCurrentIndex(self.result_byte_order.findData(cfg.get('result_byte_order', default_order)))
        self.result_byte_order.setToolTip('CRC=0x1234일 때 LSB: 34 12, MSB: 12 34.\n계산 입력 데이터와 RefIn/RefOut에는 영향을 주지 않습니다. DBC 신호의 비트 위치는 유지합니다.')
        if width == 16:
            form.addRow('CRC-16 결과 바이트 순서', self.result_byte_order)
        else:
            self.result_byte_order.hide()
        self.fields = {}
        for key, default in [('poly', 0x07 if width == 8 else 0x1021), ('init', 0 if width == 8 else 0xFFFF), ('xorout', 0)]:
            field = QLineEdit(f"0x{cfg.get(key, default):0{width // 4}X}")
            self.fields[key] = field
            form.addRow(f'{key} (HEX)', field)
        self.fields['poly'].setToolTip('최상위 x^width 항을 제외한 정방향 다항식. 예: CRC-16 0x1021. Reflection을 켜도 반전 다항식을 입력하지 않습니다.')
        self.refin = QCheckBox('입력 바이트 비트 반전 (RefIn)')
        self.refout = QCheckBox('최종 CRC 비트 반전 (RefOut)')
        self.refin.setChecked(cfg.get('refin', False))
        self.refout.setChecked(cfg.get('refout', False))
        form.addRow('Reflection', self.refin)
        form.addRow('', self.refout)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_config(self):
        try:
            params = {key: int(field.text().strip(), 16) for key, field in self.fields.items()}
        except ValueError:
            raise ValueError('poly, init, xorout은 HEX 정수로 입력하세요.')
        cfg = dict(mode=self.mode, start=self.start.value(), size=self.size.value(),
                   extra=parse_extra(self.extra.text()), refin=self.refin.isChecked(),
                   refout=self.refout.isChecked(), **params)
        if self.mode == 'crc16':
            cfg['result_byte_order'] = self.result_byte_order.currentData()
        validate_crc(self.signal, cfg, self.length)
        return cfg

    def accept(self):
        try:
            self.config = self.get_config()
        except ValueError as exc:
            QMessageBox.warning(self, 'CRC 설정 오류', str(exc))
            return
        super().accept()
