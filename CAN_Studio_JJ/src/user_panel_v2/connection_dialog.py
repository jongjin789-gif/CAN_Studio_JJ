"""The panel uses the same four-BUS grid and live state as the monitor."""
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QSizePolicy
from src.bus_config import BUS_IDS
from src.connection_controls import ConnectionGrid, CollapsibleGroup


class ConnectionDialog(QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel, self.main = panel, panel.main_window
        self.setWindowTitle('통신 설정 / DBC')
        self.resize(1120, 500)
        self.databases = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        notice = QLabel('메인창과 연결 상태를 공유합니다. 변경 시 패널은 STANDARD로 전환됩니다.')
        notice.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        root.addWidget(notice)
        self.grid = ConnectionGrid(self.main, self, mirror=True,
                                   before_action=lambda: self.panel.set_mode('standby'))
        self.fields, self.states, self.buttons = self.grid.fields, self.grid.states, self.grid.buttons
        root.addWidget(self.grid)
        self.db_group = CollapsibleGroup('Database Files (.dbc, .sym)')
        self.db_group.set_collapsed(self.main.db_group.is_collapsed())
        self.db_group.collapsed_changed.connect(self.main.db_group.set_collapsed)
        self.main.db_group.collapsed_changed.connect(self.db_group.set_collapsed)
        self.db_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        root.addWidget(self.db_group)
        row = QHBoxLayout(self.db_group.content)
        for bus in BUS_IDS:
            column = QVBoxLayout()
            column.addWidget(QLabel(f'BUS {bus}'))
            for name, title, callback in [
                ('db_add', 'DBC/SYM 등록', lambda b=bus: self.main.load_database_file(b)),
                ('db_remove', '선택 DBC/SYM 해제', lambda b=bus: self.remove_database(b)),
            ]:
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, cb=callback, b=bus, t=title:
                                       self.perform(cb, f'BUS {b}: {t}'))
                self.buttons[bus, name] = button
                column.addWidget(button)
            self.databases[bus] = QListWidget()
            column.addWidget(self.databases[bus])
            row.addLayout(column, 1)
        close = QPushButton('닫기')
        close.clicked.connect(self.accept)
        root.addStretch()
        root.addWidget(close)
        self.db_group.collapsed_changed.connect(lambda collapsed: QTimer.singleShot(0, self.fit_to_content))
        self.resize(1120, self.sizeHint().height())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(200)
        self.refresh()

    def fit_to_content(self):
        self.layout().invalidate()
        self.layout().activate()
        self.resize(self.width(), self.layout().sizeHint().height())

    def perform(self, callback, description):
        self.panel.set_mode('standby')
        self.panel.log_system(description)
        try:
            callback()
            self.main.session.autosave()
        except Exception as exc:
            self.panel.log_system(f'{description} 실패: {exc}', 'ERROR')
        self.refresh()

    def remove_database(self, bus):
        index = self.databases[bus].currentRow()
        if index >= 0:
            self.main.list_db_files[bus].setCurrentRow(index)
            self.main.remove_selected_db_file(bus)

    def refresh(self):
        self.grid.refresh()
        for bus in BUS_IDS:
            source, target = self.main.list_db_files[bus], self.databases[bus]
            names = [source.item(i).text() for i in range(source.count())]
            if names != [target.item(i).text() for i in range(target.count())]:
                selected = target.currentRow()
                target.clear()
                target.addItems(names)
                target.setCurrentRow(min(selected, target.count() - 1))
