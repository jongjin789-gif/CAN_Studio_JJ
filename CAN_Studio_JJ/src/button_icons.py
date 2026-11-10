"""Small, consistent icons for the monitor's text buttons."""
from functools import lru_cache
from PyQt5.QtCore import QSize
from PyQt5.QtGui import QIcon
from src.utils import get_resource_path


@lru_cache(maxsize=24)
def button_icon(name):
    shared = {
        'uncheck': 'icon/graph_controls/uncheck_all.svg',
        'start': 'icon/graph_controls/start.svg',
        'stop': 'icon/graph_controls/stop.svg',
        'graph_close': 'icon/graph_controls/close_all.svg',
        'panel': 'icon/user_panel.svg',
    }
    return QIcon(get_resource_path(shared.get(name, f'icon/main_controls/{name}.svg')))


def decorate_button(button, icon, tooltip, text=None):
    if text is not None:
        button.setText(text)
    button.setIcon(button_icon(icon))
    button.setIconSize(QSize(16, 16))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
