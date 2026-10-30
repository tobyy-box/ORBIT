"""Lightweight feedback notifications.

Failures are never silent: every finished action produces a toast (and a tray
message when toasts are switched off).  Long-running actions show a toast with
a Cancel button while they run.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui import icons
from ui.theme_manager import Theme, ui_font, with_alpha

TOAST_WIDTH = 380
SUCCESS_MS = 4200
FAILURE_MS = 9000


class Toast(QWidget):
    """A single notification card."""

    dismissed = Signal(object)
    cancelRequested = Signal(object)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(TOAST_WIDTH)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._cancel_callback = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._glyph = QLabel()
        self._glyph.setFixedSize(20, 20)
        header.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        self._title = QLabel()
        self._title.setFont(ui_font(13.5, QFont.Weight.DemiBold))
        self._title.setWordWrap(True)
        text_column.addWidget(self._title)
        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setFont(ui_font(12))
        self._body.setStyleSheet(f"color: {self.theme.text_muted};")
        text_column.addWidget(self._body)
        header.addLayout(text_column, 1)
        layout.addLayout(header)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        self._actions.addStretch(1)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setObjectName("Ghost")
        self._cancel_button.clicked.connect(self._on_cancel)
        self._cancel_button.hide()
        self._actions.addWidget(self._cancel_button)
        self._close_button = QPushButton("Dismiss")
        self._close_button.setObjectName("Ghost")
        self._close_button.clicked.connect(self.dismiss)
        self._actions.addWidget(self._close_button)
        layout.addLayout(self._actions)
        self.setStyleSheet(_toast_stylesheet(self.theme))

    # -------------------------------------------------------------- public API
    def show_busy(self, title: str, body: str, cancel_callback=None) -> None:
        self._cancel_callback = cancel_callback
        self._set_content("activity", self.theme.accent, title, body, ok=None)
        self._cancel_button.setVisible(cancel_callback is not None)
        self._close_button.setText("Hide")
        self._timer.stop()

    def show_result(self, title: str, body: str, *, ok: bool) -> None:
        self._cancel_callback = None
        self._cancel_button.hide()
        self._close_button.setText("Dismiss")
        self._set_content(
            "check" if ok else "alert",
            self.theme.success if ok else self.theme.danger,
            title,
            body,
            ok=ok,
        )
        self._timer.start(SUCCESS_MS if ok else FAILURE_MS)

    def _set_content(self, glyph: str, colour: str, title: str, body: str, ok: bool | None) -> None:
        self._glyph.setPixmap(icons.icon_pixmap(glyph, 18, QColor(colour)))
        self._title.setText(title)
        self._body.setText(body or "")
        self._body.setVisible(bool(body))
        self.adjustSize()
        self.setFixedWidth(TOAST_WIDTH)

    # ------------------------------------------------------------------ events
    def _on_cancel(self) -> None:
        callback = self._cancel_callback
        self._cancel_callback = None
        self._cancel_button.hide()
        self._close_button.setText("Dismiss")
        self._title.setText("Cancelling…")
        if callback is not None:
            callback()

    def dismiss(self) -> None:
        self._timer.stop()
        self.dismissed.emit(self)
        self.hide()
        self.deleteLater()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 13, 13)
        painter.fillPath(path, _alpha(self.theme.elevated, 0.98))
        painter.setPen(QPen(_alpha(self.theme.border, 1.0), 1))
        painter.drawPath(path)
        # accent stripe
        accent = QPainterPath()
        accent.addRoundedRect(QRectF(rect.left(), rect.top(), 3.5, rect.height()), 2, 2)
        painter.fillPath(accent, QColor(self.theme.accent))
        painter.end()


def _alpha(colour: str, alpha: float) -> QColor:
    value = QColor(colour)
    value.setAlphaF(alpha)
    return value


def _toast_stylesheet(theme: Theme) -> str:
    return f"""
    QLabel {{ color: {theme.text}; }}
    QPushButton {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border};
        border-radius: 7px;
        padding: 4px 10px;
        color: {theme.text};
        font-size: 12px;
    }}
    QPushButton:hover {{ background: {theme.surface}; }}
    """


class ToastManager:
    """Keeps toasts stacked in the corner of the active screen."""

    def __init__(self, theme: Theme, screen_getter=None) -> None:
        self.theme = theme
        self._toasts: list[Toast] = []
        self._screen_getter = screen_getter
        self._running: dict[str, Toast] = {}

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme

    # ------------------------------------------------------------------- public
    def start(self, run_id: str, title: str, body: str, cancel_callback=None) -> Toast | None:
        toast = self._new_toast()
        toast.show_busy(title, body, cancel_callback)
        self._running[run_id] = toast
        return toast

    def finish(self, run_id: str, title: str, body: str, *, ok: bool) -> None:
        toast = self._running.pop(run_id, None)
        if toast is None:
            toast = self._new_toast()
        toast.show_result(title, body, ok=ok)

    def notify(self, title: str, body: str, *, ok: bool = True) -> None:
        toast = self._new_toast()
        toast.show_result(title, body, ok=ok)

    def hide_all(self) -> None:
        for toast in list(self._toasts):
            toast.hide()
            toast.deleteLater()
        self._toasts.clear()
        self._running.clear()

    # ---------------------------------------------------------------- internals
    def _new_toast(self) -> Toast:
        toast = Toast(self.theme)
        toast.dismissed.connect(self._on_dismissed)
        self._toasts.append(toast)
        toast.show()
        self._reposition()
        return toast

    def _on_dismissed(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        for key, value in list(self._running.items()):
            if value is toast:
                del self._running[key]
        self._reposition()

    def _reposition(self) -> None:
        screen = self._screen_getter() if self._screen_getter else None
        if screen is None:
            return
        area = screen.availableGeometry()
        margin = 16
        y = area.bottom() - margin
        for toast in reversed(self._toasts):
            if not toast.isVisible():
                continue
            toast.adjustSize()
            x = area.right() - toast.width() - margin
            y -= toast.height()
            toast.move(QPoint(x, y))
            y -= 10
