"""Live preview of the radial menu used inside ORBIT Studio.

The preview embeds the real :class:`ui.radial_menu.RadialMenu`, so what the user
sees is exactly what the overlay draws — same layout, same painting, same hover
and keyboard behaviour.  Execution is switched off: clicking selects and reports,
it never launches anything.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.models import Config
from ui.radial_menu import RadialMenu
from ui.theme_manager import Theme, build_theme, ui_font
from ui.widgets import combo, muted_label


class PreviewPanel(QWidget):
    """Stage with a scaled, interactive radial menu preview."""

    actionSelected = Signal(object)

    def __init__(
        self,
        config_provider: Callable[[], Config],
        *,
        theme_provider: Callable[[], Theme] | None = None,
        executor=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewStage")
        self.setMinimumSize(330, 340)
        self._config_provider = config_provider
        self._theme_provider = theme_provider or (lambda: build_theme(config_provider().settings))
        self._executor = executor
        self._menu_id: str = ""
        self._config = config_provider()
        self._theme = self._theme_provider()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        caption = QLabel("Live preview")
        caption.setFont(ui_font(12, QFont.Weight.DemiBold))
        header.addWidget(caption)
        header.addStretch(1)
        self._menu_combo = combo([], "", on_change=self._on_menu_changed)
        self._menu_combo.setFixedWidth(170)
        header.addWidget(self._menu_combo)
        layout.addLayout(header)

        self.menu = RadialMenu(self)
        self.menu.set_execution_enabled(False)
        self.menu.previewClicked.connect(self._on_preview_click)
        layout.addWidget(self.menu, 1, Qt.AlignmentFlag.AlignCenter)

        footer = QHBoxLayout()
        self._status = muted_label("")
        footer.addWidget(self._status, 1)
        footer.addWidget(muted_label("Preview is interactive but never runs actions.", wrap=False))
        layout.addLayout(footer)

        self.reload_config()

    # ------------------------------------------------------------------ plumbing
    def reload_config(self) -> None:
        """Re-read the working configuration and rebuild the preview."""
        self._config = self._config_provider()
        self._theme = self._theme_provider()
        root_id = self._config.root_menu_id
        options = [
            (menu.id, f"{'★ ' if menu.id == root_id else ''}{menu.name}")
            for menu in self._config.menus
        ]
        current = self._menu_id if any(value == self._menu_id for value, _ in options) else root_id
        self._menu_combo.blockSignals(True)
        self._menu_combo.clear()
        for value, label in options:
            self._menu_combo.addItem(label, value)
        index = self._menu_combo.findData(current)
        self._menu_combo.setCurrentIndex(index if index >= 0 else 0)
        self._menu_combo.blockSignals(False)
        self._menu_id = self._menu_combo.currentData() or root_id
        self._rebuild()

    def set_menu(self, menu_id: str) -> None:
        index = self._menu_combo.findData(menu_id)
        if index >= 0:
            self._menu_combo.blockSignals(True)
            self._menu_combo.setCurrentIndex(index)
            self._menu_combo.blockSignals(False)
            self._menu_id = menu_id
            self._rebuild()

    def refresh(self) -> None:
        """Re-read the configuration without changing the selected menu."""
        self._config = self._config_provider()
        self._theme = self._theme_provider()
        self._rebuild()

    def _on_menu_changed(self, menu_id: str) -> None:
        self._menu_id = menu_id
        self._rebuild()

    def _rebuild(self) -> None:
        menu = self._config.menu_by_id(self._menu_id) or self._config.root_menu()
        self.menu.configure(
            self._config,
            self._theme,
            executor=self._executor,
            static_preview=True,
        )
        self.menu.set_menu(menu)
        self.menu.reveal = 1.0
        self._apply_scale()
        self.update()

    def _apply_scale(self) -> None:
        available_w = max(200, self.width() - 40)
        available_h = max(200, self.height() - 90)
        scale = 1.0
        for _attempt in range(3):
            self.menu.set_content_scale(scale)
            hint = self.menu.content_size_hint()
            over = max(hint.width() / available_w, hint.height() / available_h)
            if over <= 1.0:
                break
            scale = max(0.34, scale / over)
        self.menu.set_content_scale(scale)
        self.menu.move(
            int((self.width() - self.menu.width()) / 2),
            int((self.height() - self.menu.height()) / 2),
        )

    def _on_preview_click(self, action) -> None:
        summary = self.controller_summary(action)
        self._status.setText(f"Selected: {action.display_name()}  ·  {summary}")
        self.actionSelected.emit(action)

    def controller_summary(self, action) -> str:
        from core.action_registry import get_registry

        registry = getattr(self._executor, "registry", None) or get_registry()
        return registry.summary(action, self._config)

    # -------------------------------------------------------------------- events
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_scale()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        tint = QColor(self._theme.overlay_tint)
        base = QColor(self._theme.background)
        painter.fillPath(path, base)

        centre = QPointF(self.menu.geometry().center())
        radius = max(120.0, min(self.width(), self.height()) * 0.72)
        gradient = QRadialGradient(centre, radius)
        inner = QColor(tint)
        inner.setAlphaF(0.25)
        outer = QColor(tint)
        outer.setAlphaF(0.85)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(1.0, outer)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), gradient)

        # Faint monitor frame hint so the preview reads as a screen.
        painter.setClipping(False)
        painter.setPen(QPen(QColor(self._theme.border), 1))
        painter.drawPath(path)
        painter.end()
