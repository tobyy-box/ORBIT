"""The radial menu: layout, painting, animation and interaction.

The widget owns no application state beyond the menu it is showing: it draws a
:class:`core.models.Menu` using settings/theme snapshots, reports which action
the user activated and asks its parent to close or go back.  That is what lets
the same widget be embedded in ORBIT Studio as the live preview.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from core.models import Action, Config, Menu
from ui import icons
from ui.action_button import ActionButton, ButtonStyle, painted_extent
from ui.theme_manager import Theme, easing_curve, ui_font, with_alpha

HINT_TEXT_NESTED = "Backspace  back   Enter  run   N  note   T  checklist   Esc  close"
HINT_TEXT_ROOT = "Enter  run   N  note   T  checklist   Esc  close"
MAX_RINGS = 3


@dataclass(slots=True)
class ButtonPlacement:
    action: Action
    button: ActionButton
    index: int
    ring: int
    angle_deg: float
    radius: float
    centre: QPointF


class RadialMenu(QWidget):
    """A centred radial menu with one to three concentric rings."""

    actionTriggered = Signal(object)     # Action
    previewClicked = Signal(object)      # Action (preview mode only)
    selectionChanged = Signal(object)    # Action | None
    requestClose = Signal()
    requestBack = Signal()
    revealFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._config: Config | None = None
        self._theme: Theme | None = None
        self._executor = None
        self._menu: Menu | None = None
        self._placements: list[ButtonPlacement] = []
        self._breadcrumb: list[Menu] = []
        self._selected = -1
        self._hovered_index = -1
        self._hub_hover = 0.0
        self._content_scale = 1.0
        self._static_preview = False
        self._execute_enabled = True
        self._animation: QPropertyAnimation | None = None
        self._pending_after_animation = None
        self._reveal = 0.0

        # Debounced pointer check: a leave event alone is not proof that the
        # pointer left (it also fires when moving to a neighbouring button, and
        # Windows can report a stale cursor position inside the event).
        self._hover_check = QTimer(self)
        self._hover_check.setSingleShot(True)
        self._hover_check.setInterval(70)
        self._hover_check.timeout.connect(self._sync_pointer_hover)

        self._hint = HINT_TEXT_ROOT

    # ------------------------------------------------------------------ plumbing
    def configure(self, config: Config, theme: Theme, *, executor=None, static_preview: bool = False) -> None:
        self._config = config
        self._theme = theme
        self._executor = executor
        self._static_preview = static_preview

    @property
    def config(self) -> Config:
        return self._config or Config()

    @property
    def theme(self) -> Theme:
        from ui.theme_manager import build_theme_defaults

        return self._theme or build_theme_defaults()

    @property
    def menu(self) -> Menu | None:
        return self._menu

    @property
    def placements(self) -> list[ButtonPlacement]:
        return self._placements

    def actions(self) -> list[Action]:
        return [placement.action for placement in self._placements]

    def current_menu_id(self) -> str:
        return self._menu.id if self._menu else ""

    def set_content_scale(self, scale: float) -> None:
        scale = max(0.45, min(1.6, float(scale)))
        if abs(scale - self._content_scale) < 0.005:
            return
        self._content_scale = scale
        self._rebuild()

    @property
    def content_scale(self) -> float:
        return self._content_scale

    def set_breadcrumb(self, menus: list[Menu]) -> None:
        self._breadcrumb = list(menus)
        self._hint = HINT_TEXT_NESTED if len(self._breadcrumb) > 1 else HINT_TEXT_ROOT
        self.update()

    # ---------------------------------------------------------------- menu state
    def set_menu(self, menu: Menu) -> None:
        self._menu = menu
        self._rebuild()
        if self._placements:
            self.select_index(0)
        else:
            self._selected = -1

    def refresh(self) -> None:
        """Rebuild from the current configuration (used by the live preview)."""
        if self._menu is None:
            return
        current_id = self._menu.id
        latest = self.config.menu_by_id(current_id)
        if latest is not None:
            self._menu = latest
        previous = self.selected_action()
        self._rebuild()
        if previous is not None:
            self.select_action(previous.id)
        elif self._placements:
            self.select_index(0)

    # ------------------------------------------------------------- reveal anim
    def _get_reveal(self) -> float:
        return self._reveal

    def _set_reveal(self, value: float) -> None:
        self._reveal = max(0.0, min(1.0, value))
        for placement in self._placements:
            placement.button.reveal = self._reveal
        self._apply_positions()
        self.update()

    reveal = Property(float, _get_reveal, _set_reveal)

    def _duration(self, factor: float = 1.0) -> int:
        settings = self.config.settings
        if settings.reduced_motion:
            return 0
        return max(0, int(settings.animation_duration * factor))

    def animate_in(self, *, delay_ms: int = 0) -> None:
        duration = self._duration()
        self.reveal = 0.0
        if self._static_preview or duration <= 0:
            self.reveal = 1.0
            self.revealFinished.emit()
            return
        self._start_animation(0.0, 1.0, duration, delay_ms=delay_ms)

    def animate_out(self, callback=None) -> None:
        duration = self._duration(0.6)
        if duration <= 0:
            self.reveal = 0.0
            if callback:
                callback()
            return
        self._start_animation(self._reveal, 0.0, duration, on_finished=callback)

    def _start_animation(
        self,
        start: float,
        end: float,
        duration: int,
        *,
        on_finished=None,
        delay_ms: int = 0,
    ) -> None:
        if self._animation is not None:
            self._animation.stop()
            self._animation.deleteLater()
            self._animation = None
        animation = QPropertyAnimation(self, b"reveal", self)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(max(1, duration))
        animation.setEasingCurve(easing_curve(self.config.settings.easing))
        if on_finished is not None:
            animation.finished.connect(on_finished)
        if delay_ms > 0:
            animation.setStartValue(start)
            animation.setDuration(max(1, duration + delay_ms))
        self._animation = animation
        animation.start()

    def transition_to(self, menu: Menu, *, direction: int = 1, on_swapped=None) -> None:
        """Fade out, swap menu, fade in — used for nested navigation."""
        duration = self._duration(0.55)
        if duration <= 0 or self._static_preview:
            self.set_menu(menu)
            self.reveal = 1.0
            if on_swapped is not None:
                on_swapped()
            return

        def swap() -> None:
            self.set_menu(menu)
            if on_swapped is not None:
                on_swapped()
            self.animate_in()

        self._start_animation(self._reveal, 0.0, duration, on_finished=swap)

    # -------------------------------------------------------------------- layout
    def _rebuild(self) -> None:
        if self._menu is None:
            return
        settings = self.config.settings
        scale = self._content_scale
        actions = [action for action in self._menu.sorted_actions()]
        if self._executor is not None:
            actions = self._executor.visible_actions(actions)

        for placement in self._placements:
            placement.button.setParent(None)
            placement.button.deleteLater()
        self._placements = []

        count = len(actions)
        self._per_ring = 0
        self._rings = 0
        if count:
            per_ring_target = max(3, int(self._setting("max_per_ring", settings.max_per_ring)))
            rings = max(1, min(MAX_RINGS, math.ceil(count / per_ring_target)))
            per_ring = math.ceil(count / rings)
            self._rings = rings
            self._per_ring = per_ring

            radius = max(80.0, self._setting("radius", settings.radius) * scale)
            ring_gap = max(56.0, self._setting("ring_gap", settings.ring_gap) * scale)
            diameter = max(40.0, self._setting("button_size", settings.button_size) * scale)
            icon_size = max(12.0, self._setting("icon_size", settings.icon_size) * scale)
            issue_ids = self._problem_action_ids()

            for index, action in enumerate(actions):
                ring = index // per_ring
                slot = index % per_ring
                angle = -90.0 + (360.0 / per_ring) * slot
                ring_radius = radius + ring * ring_gap
                style = ButtonStyle(
                    diameter=diameter,
                    icon_size=icon_size,
                    theme=self.theme,
                    scale=scale,
                    shadow=settings.button_shadow,
                    glow=settings.button_glow,
                    indicator=self._opens_submenu(action),
                    issue=action.id in issue_ids,
                    badge=action.shortcut.strip() if settings.show_shortcut_badges else "",
                    accent=action.accent.strip(),
                    disabled=not action.enabled,
                )
                button = ActionButton(action, style, self)
                button.clicked.connect(self._on_button_clicked)
                button.hover_changed.connect(self._on_button_hover)
                button.show()
                button.reveal = self._reveal
                self._placements.append(
                    ButtonPlacement(
                        action=action,
                        button=button,
                        index=index,
                        ring=ring,
                        angle_deg=angle,
                        radius=ring_radius,
                        centre=QPointF(0, 0),
                    )
                )

        metrics = self._metrics()
        self.setFixedSize(QSize(int(metrics["width"]), int(metrics["height"])))
        self._apply_positions()
        if self._selected >= len(self._placements):
            self._selected = len(self._placements) - 1
        self._sync_selection()
        self.update()

    def _problem_action_ids(self) -> set[str]:
        if self._executor is None:
            return set()
        ids: set[str] = set()
        for action in (self._menu.actions if self._menu else []):
            try:
                if self._executor.validate(action):
                    ids.add(action.id)
            except Exception:
                continue
        return ids

    def _opens_submenu(self, action: Action) -> bool:
        if action.type != "submenu":
            return False
        return bool(action.submenu_id)

    def _setting(self, key: str, default: Any) -> Any:
        """Per-menu layout override from ``menu.layout``, else the global setting."""
        if self._menu is not None and isinstance(self._menu.layout, dict):
            value = self._menu.layout.get(key)
            if value not in (None, ""):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return default
                if number > 0:
                    return number
        return default

    def _metrics(self) -> dict[str, float]:
        settings = self.config.settings
        scale = self._content_scale
        radius = max(80.0, self._setting("radius", settings.radius) * scale)
        ring_gap = max(56.0, self._setting("ring_gap", settings.ring_gap) * scale)
        diameter = max(40.0, self._setting("button_size", settings.button_size) * scale)
        rings = max(1, self._rings) if self._placements else 1
        max_radius = radius + (rings - 1) * ring_gap
        # Everything painted beyond the ring edge: the button's own glow and
        # shadow margin, a gap, then the name/purpose label.
        margin = painted_extent(scale)
        if settings.show_labels == "never":
            label_space = margin + 4.0 * scale
        else:
            label_space = margin + 10.0 * scale + 17.0 * scale
        extent = max_radius + diameter / 2.0 + label_space + 2.0 * scale
        # Labels are horizontal, so the widget needs extra room left and right:
        # otherwise a button at 3 o'clock has nowhere to put its name and the
        # text ends up tucked under the icon (or clipped at the edge).
        label_width = (
            max(112.0 * scale, diameter * 1.6) if settings.show_labels != "never" else 0.0
        )
        # A label sits entirely to one side of its button, so the widget needs
        # that much room on each side (labels never straddle the wheel).
        half_width = max(
            extent,
            max_radius + diameter / 2.0 + margin + 8.0 * scale + label_width + 2.0 * scale,
        )
        top = 30.0 * scale
        bottom = 34.0 * scale
        return {
            "scale": scale,
            "radius": radius,
            "ring_gap": ring_gap,
            "diameter": diameter,
            "button_margin": margin,
            "extent": extent,
            "label_width": label_width,
            "top": top,
            "bottom": bottom,
            "width": half_width * 2.0,
            "height": extent * 2.0 + top + bottom,
            "centre_x": half_width,
            "centre_y": top + extent,
            "hub_radius": max(22.0, min(40.0, settings.button_size * scale * 0.42)),
        }

    def content_size_hint(self) -> QSize:
        metrics = self._metrics()
        return QSize(int(metrics["width"]), int(metrics["height"]))

    def _apply_positions(self) -> None:
        if not self._placements:
            return
        metrics = self._metrics()
        centre = QPointF(metrics["centre_x"], metrics["centre_y"])
        ease = 1.0 - (1.0 - min(1.0, self._reveal)) ** 3
        for placement in self._placements:
            angle = math.radians(placement.angle_deg)
            distance = placement.radius * (0.12 + 0.88 * ease)
            dx = math.cos(angle) * distance
            dy = math.sin(angle) * distance
            placement.centre = QPointF(centre.x() + dx, centre.y() + dy)
            size = placement.button.width()
            placement.button.move(
                int(round(placement.centre.x() - size / 2.0)),
                int(round(placement.centre.y() - size / 2.0)),
            )
        interactive = self._reveal > 0.12 or self._static_preview
        flipped = False
        for placement in self._placements:
            was = placement.button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            placement.button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)
            flipped = flipped or (was and interactive)
        if flipped:
            # The cursor may already sit where a button just appeared; Qt does
            # not resend the enter event, so re-check the pointer by hand.
            self._sync_pointer_hover()

    def _sync_pointer_hover(self) -> bool:
        """Select whatever is under the mouse right now. Returns ``True`` if one.

        Qt does not resend ``enter`` events when a widget becomes interactive
        under a stationary pointer, and it can send a ``leave`` while the
        pointer is still on a button (moving between neighbours, or when the
        window loses activation).  Checking the real pointer position is the
        only dependable answer, and it is what makes the label appear the
        instant the wheel opens and stay put while the mouse rests on a button.
        """
        if not self.isVisible():
            return False
        point = self.mapFromGlobal(QCursor.pos())
        for placement in self._placements:
            if placement.button.geometry().contains(point):
                changed = placement.index != self._hovered_index
                self._hovered_index = placement.index
                if placement.index != self._selected:
                    self.select_index(placement.index)
                elif changed:
                    self.update()
                return True
        if self._hovered_index >= 0:
            self._hovered_index = -1
            self.update()
        return False

    # ----------------------------------------------------------------- selection
    def select_index(self, index: int, *, notify: bool = True) -> None:
        if not self._placements:
            return
        index %= len(self._placements)
        self._selected = index
        self._sync_selection()
        # The hub shows the selected action's name and description, so the menu
        # itself has to repaint whenever the selection moves.
        self.update()
        if notify:
            self.selectionChanged.emit(self.selected_action())

    def select_action(self, action_id: str, *, notify: bool = True) -> bool:
        for placement in self._placements:
            if placement.action.id == action_id:
                self.select_index(placement.index, notify=notify)
                return True
        return False

    def step(self, delta: int) -> None:
        if not self._placements:
            return
        self.select_index((self._selected + delta) % len(self._placements))

    def selected_action(self) -> Action | None:
        if 0 <= self._selected < len(self._placements):
            return self._placements[self._selected].action
        return None

    def _sync_selection(self) -> None:
        for placement in self._placements:
            placement.button.set_selected(placement.index == self._selected)

    def set_execution_enabled(self, enabled: bool) -> None:
        """When disabled the menu is fully interactive but runs nothing.

        Used by the studio preview: hovering and keyboard navigation behave
        exactly like the real overlay, yet nothing is launched by accident.
        """
        self._execute_enabled = bool(enabled)

    @property
    def execution_enabled(self) -> bool:
        return self._execute_enabled

    def _emit_action(self, action: Action) -> None:
        if self._execute_enabled:
            self.actionTriggered.emit(action)
        else:
            self.previewClicked.emit(action)

    def _on_button_clicked(self, action: Action) -> None:
        self.select_action(action.id, notify=False)
        self._emit_action(action)

    def _on_button_hover(self, action: Action, entered: bool) -> None:
        # Hover is the primary way to learn what a button does, so it must
        # update the label and the hub immediately — without a repaint the
        # information would only appear on the next unrelated redraw.
        if entered:
            self._hover_check.stop()
            for placement in self._placements:
                if placement.action.id == action.id:
                    self._hovered_index = placement.index
                    self.select_index(placement.index)
                    self.update()
                    return
            return
        # Only drop the label if the pointer really is no longer over a button.
        self._hover_check.start()

    # -------------------------------------------------------------------- events
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.handle_key(event.key(), event.text()):
            event.accept()
            return
        super().keyPressEvent(event)

    def handle_key(self, key: int, text: str = "") -> bool:
        """Handle one key press. Returns ``True`` when it was consumed."""
        if key == Qt.Key.Key_Escape:
            self.requestClose.emit()
            return True
        if key == Qt.Key.Key_Backspace:
            if len(self._breadcrumb) > 1:
                self.requestBack.emit()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            action = self.selected_action()
            if action is not None:
                self._emit_action(action)
            return True
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Tab):
            self.step(1)
            return True
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Backtab):
            self.step(-1)
            return True
        if key == Qt.Key.Key_Home:
            self.select_index(0)
            return True
        if key == Qt.Key.Key_End:
            self.select_index(len(self._placements) - 1)
            return True
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            index = key - Qt.Key.Key_1
            if index < len(self._placements):
                self.select_index(index, notify=False)
                self._emit_action(self._placements[index].action)
            return True
        if text and text.isalpha():
            for placement in self._placements:
                if placement.action.display_name().lower().startswith(text.lower()):
                    self.select_index(placement.index)
                    return True
        return False

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self.step(-1 if delta > 0 else 1)
            event.accept()
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        metrics = self._metrics()
        centre = QPointF(metrics["centre_x"], metrics["centre_y"])
        distance = math.hypot(
            event.position().x() - centre.x(), event.position().y() - centre.y()
        )
        inside_hub = distance <= metrics["hub_radius"] + 6.0 * metrics["scale"]
        target = 1.0 if inside_hub else 0.0
        if abs(target - self._hub_hover) > 0.01:
            self._hub_hover = target
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hub_hover:
            self._hub_hover = 0.0
            self.update()
        self._hover_check.start()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hover_check.stop()
        self._hovered_index = -1
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        metrics = self._metrics()
        centre = QPointF(metrics["centre_x"], metrics["centre_y"])
        distance = math.hypot(
            event.position().x() - centre.x(), event.position().y() - centre.y()
        )
        if distance <= metrics["hub_radius"] + 6.0 * metrics["scale"]:
            if len(self._breadcrumb) > 1:
                self.requestBack.emit()
            else:
                self.requestClose.emit()
            event.accept()
            return
        # A click on the empty area inside the menu behaves like "outside".
        self.requestClose.emit()
        event.accept()

    # --------------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:  # noqa: N802
        if self._menu is None:
            return
        settings = self.config.settings
        theme = self.theme
        scale = self._content_scale
        metrics = self._metrics()
        centre = QPointF(metrics["centre_x"], metrics["centre_y"])
        radius = metrics["radius"]
        alpha = 0.35 + 0.65 * self._reveal

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # --- guide rings behind the buttons
        ring_pen = QPen(with_alpha(theme.border, 0.30 * alpha))
        ring_pen.setWidthF(1.0)
        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rings = self._rings or 1
        for ring in range(rings):
            ring_radius = radius + ring * metrics["ring_gap"]
            painter.drawEllipse(centre, ring_radius * self._reveal, ring_radius * self._reveal)

        # --- hub
        hub_radius = metrics["hub_radius"]
        hub_bg = QColor(theme.surface)
        hub_bg.setAlphaF(min(1.0, 0.95 * alpha))
        gradient = QRadialGradient(centre, hub_radius * 1.05)
        gradient.setColorAt(0.0, _blend(hub_bg, theme.accent_color, 0.10 * self._hub_hover))
        gradient.setColorAt(1.0, hub_bg)
        painter.setBrush(gradient)
        hub_pen = QPen(with_alpha(_blend(QColor(theme.border), theme.accent_color, self._hub_hover), 0.9 * alpha))
        hub_pen.setWidthF(1.2 + 0.6 * self._hub_hover)
        painter.setPen(hub_pen)
        painter.drawEllipse(centre, hub_radius, hub_radius)

        nested = len(self._breadcrumb) > 1
        icon_colour = _blend(QColor(theme.text_muted), QColor(theme.text), 0.4 + 0.6 * self._hub_hover)
        icon_colour.setAlphaF(alpha)
        if self._hub_hover > 0.35:
            glyph = "chevron-left" if nested else "x"
        else:
            glyph = self._menu.icon or "orbit"
        hub_icon_size = int(round(hub_radius * 1.05))
        painter.setOpacity(alpha)
        painter.drawPixmap(
            QPointF(centre.x() - hub_icon_size / 2.0, centre.y() - hub_icon_size / 2.0),
            icons.icon_pixmap(glyph, hub_icon_size, icon_colour),
        )
        painter.setOpacity(1.0)

        # --- breadcrumb above the menu
        if self._breadcrumb:
            crumb_font = ui_font(10.5 * scale, QFont.Weight.DemiBold)
            painter.setFont(crumb_font)
            names = "  ›  ".join(menu.name.upper() for menu in self._breadcrumb[-3:])
            crumb_rect = QRectF(0, max(2.0, metrics["top"] * 0.16), self.width(), metrics["top"])
            painter.setPen(with_alpha(theme.text_muted, 0.85 * alpha))
            painter.drawText(crumb_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, names)

        # --- button names are laid out up front: the hub needs to know whether
        # the hovered button is already carrying its own name.
        label_rects = (
            self._label_rects(metrics, scale) if settings.show_labels != "never" else {}
        )

        # --- the selected action's name and purpose, under the hub.
        # While the pointer rests on a labelled button, that button already
        # shows its name, so the hub only adds the description there.
        selected = self.selected_action()
        hover_labelled = settings.show_labels == "hover" and self._hovered_index in label_rects
        if selected is not None:
            max_width = max(120.0, (radius - metrics["diameter"] / 2.0 - 26.0 * scale) * 1.9)
            name_font = ui_font(15.0 * scale, QFont.Weight.DemiBold)
            desc_font = ui_font(11.5 * scale, QFont.Weight.Normal)
            name_y = centre.y() + hub_radius + 10.0 * scale
            has_description = settings.show_descriptions and bool(selected.description.strip())
            if not hover_labelled:
                painter.setFont(name_font)
                name = self._elide(painter, selected.display_name(), max_width)
                painter.setPen(with_alpha(theme.text, alpha))
                painter.drawText(
                    QRectF(centre.x() - max_width / 2.0, name_y, max_width,
                           name_font.pixelSize() + 8),
                    Qt.AlignmentFlag.AlignCenter,
                    name,
                )
            if has_description:
                # Hovering: the purpose replaces the name here, so the two
                # read-outs never repeat each other.
                description_y = name_y if hover_labelled else name_y + name_font.pixelSize() + 3
                description_font = ui_font(12.5 * scale, QFont.Weight.Normal) if hover_labelled else desc_font
                painter.setFont(description_font)
                description = self._elide(painter, selected.description.strip(), max_width)
                painter.setPen(
                    with_alpha(theme.text_muted if not hover_labelled else theme.text, 0.95 * alpha)
                )
                painter.drawText(
                    QRectF(centre.x() - max_width / 2.0, description_y, max_width,
                           description_font.pixelSize() + 8),
                    Qt.AlignmentFlag.AlignCenter,
                    description,
                )

        # --- button names.
        # "always" labels every unselected button; "hover" (the default) labels
        # only the button under the pointer, so a name appears the moment the
        # mouse arrives — no key press and no click needed.  The longer purpose
        # text lives under the hub, where there is always room for it.
        if settings.show_labels != "never" and self._placements:
            show_all = settings.show_labels == "always"
            name_font = ui_font(11.0 * scale, QFont.Weight.DemiBold)
            for placement in self._placements:
                rect = label_rects.get(placement.index)
                if rect is None:
                    continue
                hovered = placement.index == self._hovered_index
                if show_all:
                    if placement.index == self._selected:
                        continue
                    strength = 0.82
                elif hovered:
                    strength = 1.0
                else:
                    continue
                colour = theme.text if hovered else theme.text_muted
                painter.setFont(name_font)
                painter.setPen(with_alpha(QColor(colour), strength * self._reveal))
                # Push the text towards its button so short names do not float
                # in the middle of the reserved box.
                cos_a = math.cos(math.radians(placement.angle_deg))
                align = Qt.AlignmentFlag.AlignHCenter
                if abs(cos_a) >= 0.5:
                    align = (
                        Qt.AlignmentFlag.AlignLeft if cos_a >= 0
                        else Qt.AlignmentFlag.AlignRight
                    )
                painter.drawText(
                    rect,
                    int(align) | int(Qt.AlignmentFlag.AlignVCenter),
                    self._elide(painter, placement.action.display_name(), rect.width()),
                )

        # --- hint line
        hint_font = ui_font(10.5 * scale, QFont.Weight.Medium)
        painter.setFont(hint_font)
        painter.setPen(with_alpha(theme.text_muted, 0.75 * self._reveal))
        painter.drawText(
            QRectF(0, self.height() - metrics["bottom"], self.width(), metrics["bottom"]),
            Qt.AlignmentFlag.AlignCenter,
            self._hint,
        )
        painter.end()

    def _label_rects(self, metrics: dict[str, float], scale: float) -> dict[int, QRectF]:
        """Where each button's name goes, or omitted when it would cover a button.

        A name is put beside its button on the horizontal sides and above or
        below it at the top and bottom, always clear of the icon it describes.
        At very dense rings that space is taken by a neighbour, and then the
        name is dropped: the hub still shows what the selected action is.
        """
        if not self._placements:
            return {}
        name_height = ui_font(11.0 * scale, QFont.Weight.DemiBold).pixelSize() + 4
        width = metrics["label_width"]
        gap = metrics["diameter"] / 2.0 + metrics["button_margin"] + 8.0 * scale
        reach = metrics["diameter"] / 2.0
        rects: dict[int, QRectF] = {}
        for placement in self._placements:
            angle = math.radians(placement.angle_deg)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            centre = placement.centre
            horizontal = QPointF(
                centre.x() + (gap + width / 2.0) * (1 if cos_a >= 0 else -1),
                centre.y(),
            )
            vertical = QPointF(
                centre.x(),
                centre.y() + (gap + name_height / 2.0) * (1 if sin_a >= 0 else -1),
            )
            # Beside the button on the sides, above/below at top and bottom;
            # the other axis is the fallback when a neighbour is in the way.
            order = (horizontal, vertical) if abs(cos_a) >= 0.5 else (vertical, horizontal)
            for spot in order:
                cx = min(max(spot.x(), width / 2.0 + 2), self.width() - width / 2.0 - 2)
                rect = QRectF(cx - width / 2.0, spot.y() - name_height / 2.0, width, name_height)
                if self._label_clashes(rect, placement, reach):
                    continue
                rects[placement.index] = rect
                break
        return rects

    def _label_clashes(self, rect: QRectF, placement: ButtonPlacement, reach: float) -> bool:
        """True when ``rect`` would sit on any other button."""
        for other in self._placements:
            if other is placement:
                continue
            dx = max(rect.left() - other.centre.x(), 0.0, other.centre.x() - rect.right())
            dy = max(rect.top() - other.centre.y(), 0.0, other.centre.y() - rect.bottom())
            if math.hypot(dx, dy) < reach:
                return True
        return False

    @staticmethod
    def _elide(painter: QPainter, text: str, width: float) -> str:
        metrics = QFontMetricsF(painter.font())
        if metrics.horizontalAdvance(text) <= width:
            return text
        return metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)

    # ------------------------------------------------------------------ helpers
    def screen_centre(self) -> QPointF:
        metrics = self._metrics()
        return QPointF(metrics["centre_x"], metrics["centre_y"])

    def hit_test(self, point: QPointF) -> Action | None:
        for placement in self._placements:
            rect = placement.button.geometry()
            if rect.contains(point.toPoint()):
                return placement.action
        return None


def _blend(colour_a: QColor, colour_b: QColor, ratio: float) -> QColor:
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(colour_a.red() + (colour_b.red() - colour_a.red()) * ratio),
        int(colour_a.green() + (colour_b.green() - colour_a.green()) * ratio),
        int(colour_a.blue() + (colour_b.blue() - colour_a.blue()) * ratio),
        colour_a.alpha(),
    )
