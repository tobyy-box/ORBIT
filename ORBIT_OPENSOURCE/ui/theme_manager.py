"""Themes, palettes and the studio stylesheet.

One accent colour, a restrained dark surface, and enough contrast for
accessibility.  Light and fully custom themes use the same code path, so
nothing is hard-coded to the default look.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QColor, QFont

from core.models import Settings

UI_FONT_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"]
MONO_FONT_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"]


def ui_font(size: float, weight: "QFont.Weight | int" = QFont.Weight.Normal, *, mono: bool = False) -> QFont:
    """A font for the overlay, sized in logical pixels so the layout is exact."""
    font = QFont()
    font.setFamilies(MONO_FONT_FAMILIES if mono else UI_FONT_FAMILIES)
    font.setPixelSize(max(7, int(round(size))))
    font.setWeight(weight if isinstance(weight, QFont.Weight) else QFont.Weight(weight))
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font

EASING_CHOICES: tuple[tuple[str, str], ...] = (
    ("out_cubic", "Smooth (out cubic)"),
    ("out_quart", "Soft landing (out quart)"),
    ("in_out_cubic", "Balanced (in-out cubic)"),
    ("out_back", "Playful (out back)"),
    ("out_expo", "Fast then gentle (out expo)"),
    ("linear", "Linear"),
)

_EASING_MAP = {
    "linear": QEasingCurve.Type.Linear,
    "in_quad": QEasingCurve.Type.InQuad,
    "out_quad": QEasingCurve.Type.OutQuad,
    "in_out_quad": QEasingCurve.Type.InOutQuad,
    "in_cubic": QEasingCurve.Type.InCubic,
    "out_cubic": QEasingCurve.Type.OutCubic,
    "in_out_cubic": QEasingCurve.Type.InOutCubic,
    "out_quart": QEasingCurve.Type.OutQuart,
    "out_expo": QEasingCurve.Type.OutExpo,
    "out_back": QEasingCurve.Type.OutBack,
    "out_elastic": QEasingCurve.Type.OutElastic,
}


def easing_curve(name: str) -> QEasingCurve:
    curve = QEasingCurve(_EASING_MAP.get(name, QEasingCurve.Type.OutCubic))
    return curve


@dataclass(frozen=True, slots=True)
class Theme:
    name: str
    is_dark: bool
    background: str
    surface: str
    surface_alt: str
    elevated: str
    text: str
    text_muted: str
    border: str
    accent: str
    danger: str
    success: str
    warning: str
    overlay_tint: str

    def color(self, key: str) -> QColor:
        return QColor(getattr(self, key))

    @property
    def accent_color(self) -> QColor:
        return QColor(self.accent)

    @property
    def text_color(self) -> QColor:
        return QColor(self.text)

    @property
    def muted_color(self) -> QColor:
        return QColor(self.text_muted)


DARK_PALETTE = {
    "background": "#0E1013",
    "surface": "#171A1F",
    "surface_alt": "#1F232A",
    "elevated": "#242932",
    "text": "#ECEFF5",
    "text_muted": "#98A0B0",
    "border": "#2B313B",
    "danger": "#F2605E",
    "success": "#4FC98A",
    "warning": "#E8B24C",
    "overlay_tint": "#05070A",
}

LIGHT_PALETTE = {
    "background": "#F4F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#EDF0F6",
    "elevated": "#FFFFFF",
    "text": "#161A21",
    "text_muted": "#5C6474",
    "border": "#D6DBE5",
    "danger": "#C4382F",
    "success": "#1E8E52",
    "warning": "#9A6B10",
    "overlay_tint": "#0B0D11",
}


def build_theme(settings: Settings) -> Theme:
    """Resolve the active :class:`Theme` from the user's settings."""
    accent = QColor(settings.accent)
    if not accent.isValid():
        accent = QColor("#6C8CFF")

    if settings.theme == "orbit-light":
        palette = dict(LIGHT_PALETTE)
        is_dark = False
    elif settings.theme == "custom":
        palette = {
            "background": settings.custom_background,
            "surface": settings.custom_surface,
            "surface_alt": settings.custom_surface_alt,
            "elevated": settings.custom_surface_alt,
            "text": settings.custom_text,
            "text_muted": settings.custom_text_muted,
            "border": settings.custom_border,
            "danger": settings.custom_danger,
            "success": "#4FC98A",
            "warning": "#E8B24C",
            "overlay_tint": settings.custom_background,
        }
        is_dark = QColor(settings.custom_background).lightness() < 128
    else:
        palette = dict(DARK_PALETTE)
        is_dark = True

    for key, value in list(palette.items()):
        if key == "overlay_tint":
            continue
        colour = QColor(value)
        if not colour.isValid():
            palette[key] = getattr(build_theme_defaults(), key, "#000000")

    return Theme(
        name=settings.theme,
        is_dark=is_dark,
        accent=accent.name(),
        **palette,
    )


def build_theme_defaults() -> Theme:
    return Theme(name="orbit-dark", is_dark=True, accent="#6C8CFF", **DARK_PALETTE)


# ------------------------------------------------------------------- colour math
def with_alpha(colour: QColor | str, alpha: float) -> QColor:
    base = QColor(colour) if not isinstance(colour, QColor) else QColor(colour)
    if not base.isValid():
        base = QColor("#FFFFFF")
    base.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return base


def mix(colour_a: QColor | str, colour_b: QColor | str, ratio: float) -> QColor:
    """Blend two colours, ``ratio`` = share of ``colour_b``."""
    first = QColor(colour_a) if not isinstance(colour_a, QColor) else QColor(colour_a)
    second = QColor(colour_b) if not isinstance(colour_b, QColor) else QColor(colour_b)
    if not first.isValid():
        first = QColor("#000000")
    if not second.isValid():
        second = QColor("#FFFFFF")
    ratio = max(0.0, min(1.0, float(ratio)))
    return QColor(
        int(first.red() + (second.red() - first.red()) * ratio),
        int(first.green() + (second.green() - first.green()) * ratio),
        int(first.blue() + (second.blue() - first.blue()) * ratio),
        int(first.alpha() + (second.alpha() - first.alpha()) * ratio),
    )


def lighten(colour: QColor | str, amount: float) -> QColor:
    return mix(colour, "#FFFFFF", amount)


def darken(colour: QColor | str, amount: float) -> QColor:
    return mix(colour, "#000000", amount)


def readable_on(colour: QColor | str, theme: Theme) -> QColor:
    """Pick text that stays readable on top of ``colour``."""
    base = QColor(colour)
    return QColor("#FFFFFF") if base.lightness() < 150 else QColor("#101318")


# ---------------------------------------------------------------------- sheets
def studio_stylesheet(theme: Theme) -> str:
    """QSS for ORBIT Studio and its dialogs."""
    return f"""
    QWidget {{
        color: {theme.text};
        font-size: 13px;
    }}
    QMainWindow, #StudioRoot {{
        background: {theme.background};
    }}
    #Sidebar {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {mix(theme.surface, theme.accent, 0.08)},
            stop:0.55 {theme.surface}, stop:1 {darken(theme.surface, 0.04)});
        border-right: 1px solid {theme.border};
    }}
    #SidebarTitle {{
        font-size: 15px;
        font-weight: 600;
        letter-spacing: 0.4px;
        color: {theme.text};
    }}
    #SidebarSubtitle, .Muted, QLabel[muted="true"] {{
        color: {theme.text_muted};
    }}
    #SidebarIntro {{
        color: {theme.text_muted};
        font-size: 12px;
        line-height: 1.35em;
        padding: 8px 4px 4px 4px;
    }}
    #PageTitle {{
        font-size: 21px;
        font-weight: 600;
    }}
    #PageDescription {{
        color: {theme.text_muted};
        font-size: 13px;
    }}
    QStackedWidget {{
        background: {theme.background};
    }}
    QStatusBar {{
        padding-left: 10px;
        min-height: 28px;
    }}
    #SectionTitle {{
        font-size: 12px;
        font-weight: 600;
        color: {theme.text_muted};
        letter-spacing: 0.8px;
        text-transform: uppercase;
    }}
    QPushButton {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border};
        border-radius: 8px;
        padding: 6px 12px;
        min-height: 20px;
    }}
    QPushButton:hover {{
        background: {theme.elevated};
        border-color: {mix(theme.border, theme.accent, 0.45)};
    }}
    QPushButton:pressed {{
        background: {darken(theme.surface_alt, 0.12)};
    }}
    QPushButton:disabled {{
        color: {theme.text_muted};
        background: {theme.surface};
    }}
    QPushButton#Primary {{
        background: {theme.accent};
        border-color: {theme.accent};
        color: {readable_on(theme.accent, theme).name()};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{
        background: {lighten(theme.accent, 0.12)};
    }}
    QPushButton#Primary:disabled {{
        background: {mix(theme.surface_alt, theme.accent, 0.35)};
        color: {theme.text_muted};
    }}
    QPushButton#Ghost {{
        background: transparent;
        border-color: transparent;
        color: {theme.text_muted};
    }}
    QPushButton#Ghost:hover {{
        background: {theme.surface_alt};
        color: {theme.text};
    }}
    QPushButton#Danger {{
        border-color: {mix(theme.border, theme.danger, 0.6)};
        color: {theme.danger};
    }}
    QPushButton#NavButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 9px;
        padding: 10px 12px;
        min-height: 24px;
        text-align: left;
        color: {theme.text_muted};
        font-size: 13.5px;
    }}
    QPushButton#NavButton:hover {{
        background: {theme.surface_alt};
        color: {theme.text};
    }}
    QPushButton#NavButton:checked {{
        background: {mix(theme.surface_alt, theme.accent, 0.22)};
        border-color: {mix(theme.border, theme.accent, 0.5)};
        color: {theme.text};
        font-weight: 600;
    }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border};
        border-radius: 8px;
        padding: 5px 8px;
        selection-background-color: {theme.accent};
        selection-color: {readable_on(theme.accent, theme).name()};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {theme.accent};
    }}
    QLineEdit[invalid="true"] {{
        border-color: {theme.danger};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{
        color: {theme.text_muted};
        background: {theme.surface};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox QAbstractItemView {{
        background: {theme.elevated};
        border: 1px solid {theme.border};
        border-radius: 8px;
        padding: 4px;
        selection-background-color: {mix(theme.surface_alt, theme.accent, 0.35)};
        outline: none;
    }}
    QListWidget, QTreeWidget, QTableWidget {{
        background: {theme.surface};
        border: 1px solid {theme.border};
        border-radius: 10px;
        padding: 4px;
        outline: none;
    }}
    QListWidget::item, QTreeWidget::item {{
        border-radius: 7px;
        padding: 5px 6px;
    }}
    QListWidget::item:hover, QTreeWidget::item:hover {{
        background: {theme.surface_alt};
    }}
    QListWidget::item:selected, QTreeWidget::item:selected {{
        background: {mix(theme.surface_alt, theme.accent, 0.3)};
        color: {theme.text};
    }}
    QHeaderView::section {{
        background: {theme.surface};
        color: {theme.text_muted};
        border: none;
        border-bottom: 1px solid {theme.border};
        padding: 6px 8px;
        font-size: 11.5px;
        font-weight: 600;
    }}
    QTableWidget {{
        gridline-color: {mix(theme.border, theme.background, 0.35)};
        selection-background-color: {mix(theme.surface_alt, theme.accent, 0.3)};
        selection-color: {theme.text};
    }}
    QGroupBox {{
        border: 1px solid {theme.border};
        border-radius: 10px;
        margin-top: 14px;
        padding: 14px 12px 12px 12px;
        background: {theme.surface};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
        color: {theme.text_muted};
        font-size: 11.5px;
        font-weight: 600;
        letter-spacing: 0.5px;
    }}
    QCheckBox, QRadioButton {{
        spacing: 8px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {theme.border};
        background: {theme.surface_alt};
    }}
    QCheckBox::indicator {{
        border-radius: 5px;
    }}
    QRadioButton::indicator {{
        border-radius: 8px;
    }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border-color: {theme.accent};
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {theme.accent};
        border-color: {theme.accent};
        image: none;
    }}
    QSlider::groove:horizontal {{
        height: 4px;
        background: {theme.surface_alt};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {theme.accent};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {theme.text};
        width: 14px;
        height: 14px;
        margin: -6px 0;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: #FFFFFF;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {mix(theme.border, theme.text_muted, 0.3)};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {theme.text_muted};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        height: 0;
        width: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {mix(theme.border, theme.text_muted, 0.3)};
        border-radius: 5px;
        min-width: 30px;
    }}
    QTabWidget::pane {{
        border: 1px solid {theme.border};
        border-radius: 10px;
        background: {theme.surface};
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {theme.text_muted};
        padding: 7px 14px;
        border-radius: 8px;
        margin-right: 4px;
    }}
    QTabBar::tab:selected {{
        background: {theme.surface_alt};
        color: {theme.text};
    }}
    QTabBar::tab:hover {{
        color: {theme.text};
    }}
    QToolTip {{
        background: {theme.elevated};
        color: {theme.text};
        border: 1px solid {theme.border};
        border-radius: 6px;
        padding: 5px 7px;
    }}
    QStatusBar {{
        color: {theme.text_muted};
        border-top: 1px solid {theme.border};
        background: {theme.surface};
    }}
    QSplitter::handle {{
        background: transparent;
        width: 10px;
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    #Card {{
        background: {theme.surface};
        border: 1px solid {theme.border};
        border-radius: 12px;
    }}
    #PreviewStage {{
        background: {darken(theme.overlay_tint, 0.15).name()};
        border: 1px solid {theme.border};
        border-radius: 12px;
    }}
    #Badge {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border};
        border-radius: 7px;
        padding: 1px 7px;
        color: {theme.text_muted};
        font-size: 11px;
    }}
    #BadgeAccent {{
        background: {mix(theme.surface_alt, theme.accent, 0.25)};
        border: 1px solid {mix(theme.border, theme.accent, 0.45)};
        border-radius: 7px;
        padding: 1px 7px;
        color: {theme.text};
        font-size: 11px;
    }}
    #BadgeDanger {{
        background: {mix(theme.surface_alt, theme.danger, 0.2)};
        border: 1px solid {mix(theme.border, theme.danger, 0.5)};
        border-radius: 7px;
        padding: 1px 7px;
        color: {theme.danger};
        font-size: 11px;
    }}
    QMenu {{
        background: {theme.elevated};
        border: 1px solid {theme.border};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 18px 6px 10px;
        border-radius: 7px;
    }}
    QMenu::item:selected {{
        background: {mix(theme.surface_alt, theme.accent, 0.3)};
    }}
    QMenu::separator {{
        height: 1px;
        background: {theme.border};
        margin: 5px 8px;
    }}
    """


def dialog_stylesheet(theme: Theme) -> str:
    """Smaller stylesheet for overlay-owned dialogs (confirmations, notices)."""
    return f"""
    QDialog {{
        background: {theme.elevated};
        border: 1px solid {theme.border};
        border-radius: 14px;
    }}
    QLabel {{
        color: {theme.text};
        font-size: 13.5px;
    }}
    QLabel#Title {{
        font-size: 16px;
        font-weight: 600;
    }}
    QLabel#Muted, QLabel[muted="true"] {{
        color: {theme.text_muted};
    }}
    QPushButton {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border};
        border-radius: 8px;
        padding: 7px 14px;
        color: {theme.text};
        min-width: 84px;
    }}
    QPushButton:hover {{
        background: {theme.surface};
        border-color: {mix(theme.border, theme.accent, 0.5)};
    }}
    QPushButton#Primary {{
        background: {theme.accent};
        border-color: {theme.accent};
        color: {readable_on(theme.accent, theme).name()};
        font-weight: 600;
    }}
    QPushButton#Danger {{
        background: {theme.danger};
        border-color: {theme.danger};
        color: {readable_on(theme.danger, theme).name()};
        font-weight: 600;
    }}
    QPlainTextEdit, QTextEdit {{
        background: {theme.background};
        border: 1px solid {theme.border};
        border-radius: 8px;
        color: {theme.text_muted};
        padding: 6px;
    }}
    """
