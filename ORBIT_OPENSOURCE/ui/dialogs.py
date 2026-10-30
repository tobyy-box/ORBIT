"""Frameless, themed dialogs used by the overlay and the studio."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.action_registry import ActionRegistry
from core.models import Action, Config
from ui import icons
from ui.theme_manager import Theme, dialog_stylesheet, ui_font, with_alpha


class _CardDialog(QDialog):
    """Common frameless card chrome with a painted rounded surface."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setStyleSheet(dialog_stylesheet(theme))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        painter.fillPath(path, QColor(self.theme.elevated))
        painter.setPen(QPen(QColor(self.theme.border), 1))
        painter.drawPath(path)
        painter.end()


class ConfirmActionDialog(_CardDialog):
    """Confirmation for an action the user marked as needing one."""

    def __init__(
        self,
        action: Action,
        risk_label: str,
        theme: Theme,
        *,
        detail: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, parent)
        self.setWindowTitle("Confirm action")
        self.setFixedWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        badge = QLabel()
        badge.setPixmap(icons.icon_pixmap("alert", 22, QColor(theme.warning)))
        badge.setFixedWidth(24)
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        title = QLabel("Run this action?")
        title.setObjectName("Title")
        header.addWidget(title, 1, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        name = QLabel(f"“{action.display_name()}”")
        font = ui_font(14, QFont.Weight.DemiBold)
        name.setFont(font)
        layout.addWidget(name)

        if risk_label:
            risk = QLabel(risk_label)
            risk.setWordWrap(True)
            risk.setStyleSheet(f"color: {theme.warning}; font-size: 12px;")
            layout.addWidget(risk)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setObjectName("Muted")
            layout.addWidget(detail_label)

        layout.addSpacing(4)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        run = QPushButton("Run action")
        run.setObjectName("Primary")
        run.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(run)
        layout.addLayout(buttons)

        self._run_button = run

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._run_button.setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Escape must always cancel a destructive action.
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class ActionSummaryDialog(_CardDialog):
    """Result of running a single action, with full output available."""

    def __init__(
        self,
        title: str,
        message: str,
        detail: str,
        theme: Theme,
        *,
        ok: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, parent)
        self.setFixedWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(
            icons.icon_pixmap("check" if ok else "alert", 22,
                              QColor(theme.success if ok else theme.danger))
        )
        glyph.setFixedWidth(24)
        header.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        heading = QLabel(title)
        heading.setObjectName("Title")
        heading.setWordWrap(True)
        header.addWidget(heading, 1)
        layout.addLayout(header)

        body = QLabel(message)
        body.setWordWrap(True)
        layout.addWidget(body)

        if detail:
            from PySide6.QtWidgets import QPlainTextEdit

            output = QPlainTextEdit(detail)
            output.setReadOnly(True)
            output.setFixedHeight(190)
            output.setFont(ui_font(12, mono=True))
            output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(output)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)


def confirm_action(
    action: Action,
    registry: ActionRegistry,
    config: Config,
    theme: Theme,
    parent: QWidget | None = None,
) -> bool:
    """Show the confirmation dialog for a destructive action."""
    detail = registry.summary(action, config)
    return (
        ConfirmActionDialog(
            action,
            registry.risk_label(action),
            theme,
            detail=detail,
            parent=parent,
        ).exec()
        == QDialog.DialogCode.Accepted
    )
