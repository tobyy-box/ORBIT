"""Reusable action button for the radial menu.

Buttons paint themselves (rounded surface, soft shadow, restrained glow,
icon, optional shortcut badge) and animate hover/selection on the GPU-friendly
paint path instead of restyling widget sheets, which keeps hover transitions
flicker-free.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from core.models import Action
from ui import icons
from ui.theme_manager import Theme, readable_on, with_alpha
from ui.theme_manager import ui_font

GLOW_MARGIN = 28


def painted_extent(scale: float) -> float:
    """How far the button paints beyond its surface, at full hover.

    The widget is this much bigger than the button on every side so the glow
    and shadow are never clipped into a visible square.
    """
    glow = (10.0 + 12.0) * max(0.6, scale)
    shadow = 6.0 * (2.2 + 1.4) * max(0.6, scale) + 1.5
    return max(glow, shadow) + 2.0


@dataclass(frozen=True, slots=True)
class ButtonStyle:
    """Everything an :class:`ActionButton` needs to draw itself."""

    diameter: float
    icon_size: float
    theme: Theme
    scale: float = 1.0
    opacity: float = 1.0
    shadow: bool = True
    glow: bool = True
    indicator: bool = False          # opens another menu
    issue: bool = False              # has a configuration problem
    badge: str = ""
    accent: str = ""
    disabled: bool = False

    @property
    def margin(self) -> float:
        return painted_extent(self.scale)

    @property
    def widget_size(self) -> int:
        return int(round(self.diameter + self.margin * 2))


class ActionButton(QWidget):
    """One circular action button."""

    clicked = Signal(object)          # Action
    hover_changed = Signal(object, bool)

    def __init__(self, action: Action, style: ButtonStyle, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._action = action
        self._style = style
        self._hover = 0.0
        self._reveal = 1.0
        self._selected = False
        self._pressed = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(style.widget_size, style.widget_size))
        self.setToolTip("")
        self._animation = QPropertyAnimation(self, b"hover_progress", self)
        self._animation.setDuration(130)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---------------------------------------------------------------- properties
    @property
    def action(self) -> Action:
        return self._action

    def set_action(self, action: Action) -> None:
        self._action = action
        self.update()

    @property
    def style(self) -> ButtonStyle:
        return self._style

    def apply_style(self, style: ButtonStyle) -> None:
        self._style = style
        self.setFixedSize(QSize(style.widget_size, style.widget_size))
        self.update()

    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = max(0.0, min(1.0, value))
        self.update()

    hover_progress = Property(float, _get_hover, _set_hover)

    def _get_reveal(self) -> float:
        return self._reveal

    def _set_reveal(self, value: float) -> None:
        self._reveal = max(0.0, min(1.0, value))
        self.update()

    reveal = Property(float, _get_reveal, _set_reveal)

    def set_selected(self, selected: bool, *, animate: bool = True) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._animate_hover(1.0 if selected else (0.55 if self.underMouse() else 0.0), animate)

    def _animate_hover(self, target: float, animate: bool = True) -> None:
        self._animation.stop()
        if not animate:
            self.hover_progress = target
            return
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(target)
        self._animation.start()

    # ------------------------------------------------------------------- events
    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._animate_hover(1.0)
        self.hover_changed.emit(self._action, True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._animate_hover(0.55 if self._selected else 0.0)
        self.hover_changed.emit(self._action, False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit(self._action)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -------------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:  # noqa: N802
        style = self._style
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        theme = style.theme
        accent = QColor(style.accent) if style.accent else theme.accent_color
        if not accent.isValid():
            accent = theme.accent_color

        progress = self._hover
        reveal = self._reveal
        if reveal <= 0.001:
            painter.end()
            return

        base_diameter = style.diameter * (0.72 + 0.28 * _ease_out(reveal))
        diameter = base_diameter * (0.94 + 0.06 * progress)
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = diameter / 2.0
        alpha = style.opacity * reveal
        # Nothing may ever be painted outside the widget: a glow that reaches
        # the widget edge reads as a hard-edged square around the button.
        limit = max(0.0, self.width() / 2.0 - 1.0)

        # --- soft shadow (painted, not a widget effect: cheaper and smoother)
        if style.shadow:
            for step in range(6, 0, -1):
                spread = min(step * (2.2 + 1.4 * progress) * max(0.6, style.scale), limit - radius)
                if spread <= 0:
                    break
                shadow = QColor(0, 0, 0)
                shadow.setAlphaF(0.052 * alpha * (1.0 - (step - 1) / 6.0) * (1.0 + 0.5 * progress))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(shadow)
                painter.drawEllipse(centre, radius + spread, radius + spread + 1.5 * progress)

        # --- glow around the selected button
        if style.glow and progress > 0.01:
            glow_radius = min(radius + (10 + 12 * progress) * max(0.6, style.scale), limit)
            gradient = QRadialGradient(centre, glow_radius)
            inner = with_alpha(accent, 0.24 * progress * alpha)
            outer = with_alpha(accent, 0.0)
            gradient.setColorAt(0.72, inner)
            gradient.setColorAt(1.0, outer)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(centre, glow_radius, glow_radius)

        # --- surface
        surface = QColor(theme.surface)
        surface = _blend(surface, accent, 0.16 * progress)
        surface.setAlphaF(min(1.0, 0.94 * alpha * style.opacity))
        fill = QLinearGradient(centre.x(), centre.y() - radius, centre.x(), centre.y() + radius)
        fill.setColorAt(0.0, _lighten(surface, 0.07))
        fill.setColorAt(1.0, surface)
        painter.setBrush(fill)

        border = QColor(theme.border)
        if progress > 0.01:
            border = _blend(border, accent, 0.85 * progress)
        border.setAlphaF(min(1.0, (0.55 + 0.45 * progress) * alpha))
        pen = QPen(border)
        pen.setWidthF(1.0 + 0.6 * progress)
        painter.setPen(pen)
        painter.drawEllipse(centre, radius, radius)

        # --- icon
        icon_colour = QColor(theme.text)
        if style.disabled:
            icon_colour = QColor(theme.text_muted)
        elif progress > 0.01:
            icon_colour = _blend(icon_colour, _lighten(accent, 0.55), 0.55 * progress)
        icon_colour.setAlphaF(min(1.0, (0.92 if not style.disabled else 0.5) * alpha))
        icon_size = int(round(style.icon_size * (0.92 + 0.08 * progress)))
        # A custom image always wins over the glyph, so both ways of giving a
        # button artwork (the icon picker's "file:" entry or the image field)
        # behave the same.
        icon_value = (self._action.image or "").strip() or self._action.icon or None
        # Custom images are masked into a circle so a photo or logo fills the
        # round surface instead of looking like a pasted square.
        kind, _payload = icons.resolve_icon(icon_value)
        pixmap = icons.icon_pixmap(icon_value, icon_size, icon_colour, circular=(kind == "image"))
        painter.setOpacity(min(1.0, alpha))
        painter.drawPixmap(
            QPointF(centre.x() - icon_size / 2.0, centre.y() - icon_size / 2.0),
            pixmap,
        )

        # --- submenu indicator: an accent arc on the outward edge
        if style.indicator:
            arc_pen = QPen(with_alpha(accent, min(1.0, (0.65 + 0.35 * progress) * alpha)))
            arc_pen.setWidthF(2.0 * max(0.7, style.scale))
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            margin = 3.0 * max(0.8, style.scale)
            painter.drawArc(
                QRectF(centre.x() - radius + margin, centre.y() - radius + margin,
                       (radius - margin) * 2, (radius - margin) * 2),
                int(200 * 16),
                int(-40 * 16),
            )

        # --- configuration problem marker
        if style.issue:
            marker_radius = 3.4 * max(0.8, style.scale)
            centre_marker = QPointF(centre.x() + radius * 0.66, centre.y() - radius * 0.66)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(theme.danger, min(1.0, alpha)))
            painter.drawEllipse(centre_marker, marker_radius, marker_radius)

        # --- shortcut badge
        if style.badge:
            self._draw_badge(painter, centre, radius, style, alpha)

        painter.setOpacity(1.0)
        painter.end()

    def _draw_badge(
        self,
        painter: QPainter,
        centre: QPointF,
        radius: float,
        style: ButtonStyle,
        alpha: float,
    ) -> None:
        theme = style.theme
        font = ui_font(max(8.0, 9.0 * style.scale), QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = style.badge
        width = metrics.horizontalAdvance(text) + 10 * max(0.8, style.scale)
        height = metrics.height() + 3
        angle = -0.72
        anchor = QPointF(
            centre.x() + radius * 0.86 * _cos(angle),
            centre.y() + radius * 0.86 * _sin(angle) + radius * 0.36,
        )
        rect = QRectF(anchor.x() - width / 2, anchor.y() - height / 2, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        badge_bg = QColor(theme.elevated)
        badge_bg.setAlphaF(min(1.0, 0.92 * alpha))
        painter.setBrush(badge_bg)
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(with_alpha(theme.text_muted, min(1.0, alpha)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


# ----------------------------------------------------------------- tiny helpers
def _ease_out(value: float) -> float:
    clamped = max(0.0, min(1.0, value))
    return 1.0 - (1.0 - clamped) ** 3


def _blend(colour_a: QColor, colour_b: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(colour_a.red() + (colour_b.red() - colour_a.red()) * ratio),
        int(colour_a.green() + (colour_b.green() - colour_a.green()) * ratio),
        int(colour_a.blue() + (colour_b.blue() - colour_a.blue()) * ratio),
        colour_a.alpha(),
    )


def _lighten(colour: QColor, amount: float) -> QColor:
    return _blend(colour, QColor("#FFFFFF"), amount)


def _cos(angle: float) -> float:
    import math

    return math.cos(angle)


def _sin(angle: float) -> float:
    import math

    return math.sin(angle)


def restyle(style: ButtonStyle, **changes) -> ButtonStyle:
    return replace(style, **changes)
