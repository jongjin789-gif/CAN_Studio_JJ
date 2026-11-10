import os, sys, datetime
from PyQt5.QtWidgets import QTreeWidgetItem
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
import pyqtgraph as pg

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    # 현재 파일(utils.py)의 상위 폴더(src)의 상위 폴더(Root)를 기준으로 경로 반환
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)



class SortableTreeWidgetItem(QTreeWidgetItem):
    """QTreeWidget에서 숫자와 문자열을 올바르게 정렬하기 위한 커스텀 클래스"""
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()

        # 1. 이름(3번 컬럼)으로 정렬할 경우, 아이템 내부에 저장된 UserRole 데이터(start_bit) 유무를
        #    판단하여 메시지와 시그널을 명확하게 구분하고, 그에 맞는 기준으로 정렬합니다.
        #    이는 self.parent()를 확인하는 기존 방식보다 훨씬 안정적입니다.
        if column == 3:
            self_start_bit = self.data(3, Qt.UserRole)
            other_start_bit = other.data(3, Qt.UserRole)

            # 두 아이템 모두 start_bit 데이터를 가짐 -> 시그널이므로 start_bit로 정렬
            if self_start_bit is not None and other_start_bit is not None:
                return self_start_bit < other_start_bit
            
            # 두 아이템 모두 start_bit 데이터가 없음 -> 메시지이므로 이름(text)으로 정렬
            elif self_start_bit is None and other_start_bit is None:
                return self.text(3) < other.text(3)
            
            # 메시지와 시그널을 직접 비교하는 경우는 없어야 하지만, 만약 발생 시 이름으로 정렬 (안전장치)
            else:
                return self.text(3) < other.text(3)

        # 2. 그 외 다른 컬럼으로 정렬하는 경우는 기존 로직을 그대로 따릅니다.
        self_text = self.text(column)
        other_text = other.text(column)

        # CAN ID (0x 또는 h 포함) 또는 숫자로 변환 가능한 컬럼들에 대한 예외 정렬
        if self_text.startswith('0x') and other_text.startswith('0x'):
            try: return int(self_text, 16) < int(other_text, 16)
            except ValueError: pass
            
        if self_text.endswith('h') and other_text.endswith('h'):
            try: return int(self_text[:-1], 16) < int(other_text[:-1], 16)
            except ValueError: pass
            
        if self_text.isdigit() and other_text.isdigit():
            try: return int(self_text) < int(other_text)
            except ValueError: pass
        
        # 다른 모든 경우 (다른 컬럼, 시그널 항목 등)에는 기본 문자열 비교 사용
        return self.text(column) < other.text(column)

class ReceiveTreeWidgetItem(SortableTreeWidgetItem):
    """RX headers sort their displayed values; Bus also sorts by CAN ID."""

    def _sort_value(self, column):
        text = self.text(column)
        if column in (0, 2, 5, 7, 8):
            try:
                return (0, int(text, 16) if column == 2 else float(text))
            except ValueError:
                pass
        return (1, text)

    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        if column == 0 and self.parent() is None and other.parent() is None:
            return (self._sort_value(0), self._sort_value(2)) < (other._sort_value(0), other._sort_value(2))
        return self._sort_value(column) < other._sort_value(column)


class TimeAxisItem(pg.AxisItem):
    """X축(시간)을 HH:mm:ss.000 포맷으로 표시하기 위한 커스텀 축"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enableAutoSIPrefix(False) # 자동 배수 표기(x1e+09 등) 비활성화

    def tickStrings(self, values, scale, spacing):
        strings = []
        for v in values:
            try:
                strings.append(datetime.datetime.fromtimestamp(v).strftime('%H:%M:%S.%f')[:-3])
            except (ValueError, OSError, TypeError):
                strings.append(f"{v:.3f}")
        return strings

class TagTextItem(pg.TextItem):
    """마커와 함께 표시될 태그(텍스트 라벨) 아이템"""
    def __init__(self, text, bg_color, signal_name, t_val, marker):
        # 배경은 해당 데이터 선의 색상으로 하되, 글자 가독성을 위해 약간 투명하게(Alpha=220) 설정
        color_with_alpha = QColor(*bg_color)
        color_with_alpha.setAlpha(220) 
        
        # 검은색 텍스트로 설정하고, 앵커(0.5, 1.2)를 줘서 점의 약간 위쪽에 배치
        super().__init__(text=text, color=(0, 0, 0), fill=color_with_alpha, anchor=(0.5, 1.2))
        self.signal_name = signal_name
        self.t_val = t_val
        self.marker = marker
        # 자체 마우스 이벤트 수신을 없애고 SignalGraphWindow에서 중앙 제어하도록 변경
