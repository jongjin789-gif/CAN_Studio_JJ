"""DBC-independent masked payload input. X means preserve (TX) or ignore (RX)."""
import re
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QComboBox, QLineEdit, QMessageBox


def parse_masked(text, length, mode='HEX'):
    raw = re.sub(r'\s+', '', text).upper()
    size, base, valid = (2, 16, '0123456789ABCDEFX') if mode == 'HEX' else (8, 2, '01X')
    if not raw or len(raw) % size or len(raw) > length * size or any(c not in valid for c in raw):
        raise ValueError(f'{mode}: 완전한 바이트 단위로 입력하세요. 최대 {length}바이트이며 X를 사용할 수 있습니다.')
    data, mask = [], []
    for start in range(0, len(raw), size):
        chunk = raw[start:start + size]
        data.append(int(chunk.replace('X', '0'), base))
        mask.append(int(''.join('0' if c == 'X' else ('F' if mode == 'HEX' else '1') for c in chunk), base))
    return data + [0] * (length - len(data)), mask + [0] * (length - len(mask))


def format_masked(data, mask, mode='HEX'):
    chunks = []
    for value, selected in zip(data, mask):
        if mode == 'BIN':
            bits = ''.join(str((value >> b) & 1) if selected & (1 << b) else 'X' for b in range(7, -1, -1))
            chunks.append(bits[:4] + ' ' + bits[4:])
        else:
            digits = ''
            for shift in (4, 0):
                nibble = (selected >> shift) & 15
                if nibble not in (0, 15):
                    raise ValueError('일부 비트만 지정한 마스크는 BIN으로 편집하세요.')
                digits += f'{(value >> shift) & 15:X}' if nibble else 'X'
            chunks.append(digits)
    return ' '.join(chunks)


def masked_match(data, expected, mask, allow_length_mismatch=False):
    if len(expected) != len(mask) or not any(mask):
        return False
    if not allow_length_mismatch and len(data) != len(expected):
        return False
    return all(not m or (i < len(data) and (data[i] & m) == (expected[i] & m)) for i, m in enumerate(mask))


class MaskedDataInput(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.length = 8
        self.mode = 'HEX'
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.format = QComboBox()
        self.format.addItems(['HEX', 'BIN'])
        self.text = QLineEdit()
        self.text.setPlaceholderText('01 02 XX 04 / 0010 0001 1100 XXXX')
        self.text.setToolTip('X와 생략한 뒤쪽 바이트: CMD는 현재 값 유지, RCV는 비교 제외. 왼쪽은 첫 바이트의 상위 비트입니다.')
        layout.addWidget(self.format)
        layout.addWidget(self.text, 1)
        self.format.currentTextChanged.connect(self.change_format)

    def set_data(self, data, mask, mode='HEX'):
        self.length = len(data)
        try:
            text = format_masked(data, mask, mode)
        except ValueError:
            mode = 'BIN'
            text = format_masked(data, mask, mode)
        self.mode = mode
        self.format.blockSignals(True)
        self.format.setCurrentText(mode)
        self.format.blockSignals(False)
        self.text.setText(text)

    def values(self, length):
        return parse_masked(self.text.text(), length, self.mode)

    def change_format(self, mode):
        try:
            # Convert exactly the entered bytes; DLC changes must not truncate
            # input or silently add comparisons while switching formats.
            raw = re.sub(r'\s+', '', self.text.text())
            data, mask = self.values(len(raw) // (2 if self.mode == 'HEX' else 8))
            text = format_masked(data, mask, mode)
        except ValueError as exc:
            self.format.blockSignals(True)
            self.format.setCurrentText(self.mode)
            self.format.blockSignals(False)
            QMessageBox.warning(self, '입력 형식', str(exc))
            return
        self.mode = mode
        self.text.setText(text)
