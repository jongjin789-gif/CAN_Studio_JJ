"""Presentation for generated panel tools; independent of saved configuration."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

TOOL_STYLE = """
QFrame[panelCard="true"] {
    background: #ffffff; border: 1px solid #dce3ed; border-radius: 8px;
}
QFrame[panelCard="true"][selected="true"] {
    border: 1px solid #2563eb; background: #f5f8ff;
}
QLabel { color: #334155; background: transparent; border: none; }
QLabel[panelTitle="true"] { color: #64748b; font-weight: 600; }
QLabel#value_label, QLabel[panelValue="true"] {
    color: #0f172a; font-weight: 600;
}
QPushButton {
    color: #334155; background: #f1f5f9; border: 1px solid #cbd5e1;
    border-radius: 6px; padding: 3px 8px; font-weight: 600;
}
QPushButton:hover { background: #e2e8f0; border-color: #94a3b8; }
QPushButton:pressed { background: #cbd5e1; }
QPushButton[panelPrimary="true"], QPushButton:checked {
    color: white; background: #2563eb; border-color: #2563eb;
}
QPushButton[panelPrimary="true"]:hover, QPushButton:checked:hover { background: #1d4ed8; }
QPushButton[panelPrimary="true"]:pressed { background: #1e40af; }
QPushButton:focus, QDoubleSpinBox:focus { border: 1px solid #0ea5e9; }
QPushButton:disabled {
    color: #94a3b8; background: #f1f5f9; border-color: #e2e8f0;
}
QSlider::groove:horizontal { height: 6px; background: #e2e8f0; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #3b82f6; border-radius: 3px; }
QSlider::handle:horizontal {
    background: white; border: 2px solid #2563eb; width: 12px;
    margin: -5px 0; border-radius: 8px;
}
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus { background: #dbeafe; }
QSlider::sub-page:horizontal:disabled { background: #cbd5e1; }
QSlider::handle:horizontal:disabled { border-color: #94a3b8; }
QDoubleSpinBox {
    color: #0f172a; background: #f8fafc; border: 1px solid #cbd5e1;
    border-radius: 6px; padding: 2px 4px;
}
QDoubleSpinBox:disabled { color: #94a3b8; }
QProgressBar {
    color: #0f172a; background: #e8eef6; border: none;
    border-radius: 6px; text-align: center; font-weight: 600;
}
QProgressBar::chunk { background: #60a5fa; border-radius: 6px; }
QGroupBox {
    color: #475569; border: 1px solid #e2e8f0; border-radius: 6px;
    margin-top: 10px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QTabWidget::pane { border: 1px solid #e2e8f0; background: white; }
QTabBar::tab { background: #f1f5f9; color: #64748b; padding: 4px 10px; }
QTabBar::tab:selected { background: #dbeafe; color: #1d4ed8; }
"""


def lamp_style(is_on):
    background, foreground, border = (
        ("#dcfce7", "#166534", "#86efac") if is_on
        else ("#f1f5f9", "#64748b", "#cbd5e1")
    )
    return (
        f"background:{background}; color:{foreground}; border:1px solid {border};"
        "border-radius:6px; padding:3px; font-weight:600;"
    )


def apply_container_style(frame, control, pages, binding):
    """Color container surfaces without recoloring their child tools."""
    stroke = QColor(binding.get('stroke_color', '#333333'))
    stroke = stroke.name() if stroke.isValid() else '#333333'
    color = QColor(binding.get('fill_color', '#E8F1FF'))
    if not color.isValid():
        color = QColor('#E8F1FF')
    filled = bool(binding.get('fill', False))
    background = color.name() if filled else 'transparent'
    foreground = '#ffffff' if filled and color.lightnessF() < 0.5 else '#334155'
    width = max(1, min(20, int(binding.get('stroke_width', 2))))
    line_style = {'dash': 'dashed', 'dot': 'dotted'}.get(binding.get('stroke_style'), 'solid')
    radius = max(0, min(100, int(binding.get('corner_radius', 0))))
    border = f'{width}px {line_style} {stroke}'
    frame.setStyleSheet(TOOL_STYLE)
    if filled:
        frame.setStyleSheet(TOOL_STYLE + (
            f'QFrame[panelCard="true"] {{ background: {background}; }}'
            f'QFrame[panelCard="true"][selected="true"] {{ background: {background}; }}'
        ))
    control.setObjectName('panelGroupContainer')
    control.setStyleSheet(
        f'QGroupBox#panelGroupContainer {{ background: {background}; color: {foreground}; '
        f'border: {border}; border-radius: {radius}px; margin-top: 10px; }}'
        'QGroupBox#panelGroupContainer::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }'
        f'QTabWidget#panelGroupContainer::pane {{ background: {background}; '
        f'border: {border}; border-radius: {radius}px; }}'
    )
    if hasattr(control, 'tabBar'):
        tab_background = color.lighter(120).name() if filled else '#f1f5f9'
        selected_background = background if filled else '#dbeafe'
        control.tabBar().setStyleSheet(
            f'QTabBar::tab {{ background: {tab_background}; color: {foreground}; padding: 4px 10px; }}'
            f'QTabBar::tab:selected {{ background: {selected_background}; color: {foreground}; '
            f'font-weight: bold; border-bottom: 2px solid {stroke}; }}'
        )
    for page in pages:
        for surface in (page.viewport(), page.canvas):
            surface.setObjectName('panelContainerSurface')
            surface.setAttribute(Qt.WA_StyledBackground, True)
            surface.setStyleSheet(f'QWidget#panelContainerSurface {{ background: {background}; }}')


def apply_drawing_style(control, kind, binding):
    stroke = QColor(binding.get('stroke_color', '#333333'))
    stroke = stroke.name() if stroke.isValid() else '#333333'
    width = max(1, min(20, int(binding.get('stroke_width', 2))))
    line_style = {'dash': 'dashed', 'dot': 'dotted'}.get(binding.get('stroke_style'), 'solid')
    border = f'{width}px {line_style} {stroke}'
    if kind == 'shape_line':
        vertical = binding.get('shape_line_direction') == 'vertical'
        control.setFrameShape(control.VLine if vertical else control.HLine)
        control.setFrameShadow(control.Plain)
        control.setStyleSheet(f'border: 0; border-{"left" if vertical else "top"}: {border};')
    else:
        color = QColor(binding.get('fill_color', '#E8F1FF'))
        fill = color.name() if color.isValid() and binding.get('fill', False) else 'transparent'
        radius = max(0, min(100, int(binding.get('corner_radius', 0))))
        control.setStyleSheet(f'border: {border}; background:{fill}; border-radius:{radius}px;')
