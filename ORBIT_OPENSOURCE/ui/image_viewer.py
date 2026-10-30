"""A small built-in image viewer used by the "ORBIT viewer" image action.

Deliberately minimal: fit-to-window, wheel zoom, drag to pan, Escape to close.
The default system viewer remains the default choice for the image action.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.launcher import LaunchError, open_with_default
from ui.theme_manager import Theme, ui_font


class ImageViewer(QWidget):
    """Frameless viewer for a single image."""

    closed = Signal()

    def __init__(self, path: str, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.path = path
        self._image = QImage(path)
        self._zoom = 1.0
        self._fit_to_window = True
        self._offset = QPointF(0, 0)
        self._drag_origin: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(Path(path).name)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._title = QLabel(Path(self.path).name)
        self._title.setFont(ui_font(13.5, QFont.Weight.DemiBold))
        header.addWidget(self._title)
        self._meta = QLabel(self._describe())
        self._meta.setStyleSheet(f"color: {self.theme.text_muted}; font-size: 11.5px;")
        header.addWidget(self._meta)
        header.addStretch(1)
        fit = QPushButton("Fit")
        fit.clicked.connect(self._fit)
        header.addWidget(fit)
        actual = QPushButton("100%")
        actual.clicked.connect(self._actual_size)
        header.addWidget(actual)
        external = QPushButton("Open with system viewer")
        external.clicked.connect(self._open_external)
        header.addWidget(external)
        layout.addLayout(header)

        self._canvas = _Canvas(self)
        layout.addWidget(self._canvas, 1)
        self.setStyleSheet(_stylesheet(self.theme))

    def _describe(self) -> str:
        if self._image.isNull():
            return "This file could not be decoded as an image."
        return f"{self._image.width()} × {self._image.height()} px"

    # --------------------------------------------------------------------- API
    def show_on(self, geometry) -> None:
        width = int(min(geometry.width() * 0.82, 1600))
        height = int(min(geometry.height() * 0.82, 1000))
        self.resize(max(480, width), max(320, height))
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + (geometry.height() - self.height()) // 2
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._canvas.update()

    def _fit(self) -> None:
        self._fit_to_window = True
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._canvas.update()

    def _actual_size(self) -> None:
        self._fit_to_window = False
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._canvas.update()

    def _open_external(self) -> None:
        try:
            open_with_default(self.path)
        except LaunchError as exc:
            self._title.setText(str(exc).splitlines()[0])
        self.close()

    def zoom_by(self, factor: float) -> None:
        self._fit_to_window = False
        self._zoom = max(0.05, min(16.0, self._zoom * factor))
        self._canvas.update()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_by(1.2)
        elif key == Qt.Key.Key_Minus:
            self.zoom_by(1 / 1.2)
        elif key == Qt.Key.Key_0:
            self._fit()
        elif key == Qt.Key.Key_1:
            self._actual_size()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        backdrop = QColor(self.theme.background)
        backdrop.setAlphaF(0.97)
        painter.fillPath(path, backdrop)
        painter.setPen(QPen(QColor(self.theme.border), 1))
        painter.drawPath(path)
        painter.end()

    class _CanvasSentinel:
        pass


class _Canvas(QWidget):
    """Draws the image with zoom and pan."""

    def __init__(self, viewer: ImageViewer) -> None:
        super().__init__(viewer)
        self.viewer = viewer
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        board = QColor(self.viewer.theme.surface)
        board.setAlphaF(0.7)
        path = QPainterPath()
        path.addRoundedRect(rect, 11, 11)
        painter.fillPath(path, board)

        image = self.viewer._image
        if image.isNull():
            painter.setPen(QColor(self.viewer.theme.text_muted))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "This image cannot be displayed.")
            painter.end()
            return

        if self.viewer._fit_to_window:
            scale = min(
                (self.width() - 24) / max(1, image.width()),
                (self.height() - 24) / max(1, image.height()),
            )
            scale = max(0.02, min(scale, 8.0))
        else:
            scale = self.viewer._zoom
        width = image.width() * scale
        height = image.height() * scale
        x = (self.width() - width) / 2 + self.viewer._offset.x()
        y = (self.height() - height) / 2 + self.viewer._offset.y()
        painter.drawImage(QRectF(x, y, width, height), image)
        painter.end()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self.viewer.zoom_by(1.15 if delta > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        origin = getattr(self, "_origin", None)
        if origin is not None:
            delta = event.position() - origin
            self.viewer._offset += QPointF(delta.x(), delta.y())
            self._origin = event.position()
            self.viewer._fit_to_window = False
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._origin = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.viewer._fit_to_window = not self.viewer._fit_to_window
        self.update()


def _stylesheet(theme: Theme) -> str:
    return f"""
    QPushButton {{
        background: {theme.surface_alt};
        border: 1px solid {theme.border};
        border-radius: 7px;
        padding: 5px 11px;
        color: {theme.text};
        font-size: 12px;
    }}
    QPushButton:hover {{ background: {theme.surface}; border-color: {theme.accent}; }}
    QLabel {{ color: {theme.text}; }}
    """


_PIXMAP_SENTINEL: QPixmap | None = None
