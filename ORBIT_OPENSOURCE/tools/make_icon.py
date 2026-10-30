"""Generate ``assets/orbit.ico`` from the built-in ORBIT glyph.

The icon is drawn with the same SVG renderer the app uses, so it always matches
the hub icon.  A multi-resolution PNG-compressed ICO is written by hand: no
Pillow, no extra dependency, and Windows Explorer accepts it from Vista onward.

Run it with::

    python tools/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from ui import icons  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
ACCENT = "#6C8CFF"
BACKGROUND = "#101318"


def render(size: int) -> QImage:
    """Draw the app icon at ``size`` pixels."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    radius = size * 0.22
    gradient = QRadialGradient(size / 2, size / 2, size / 2)
    gradient.setColorAt(0.0, QColor("#1E2430"))
    gradient.setColorAt(1.0, QColor(BACKGROUND))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    inset = size * 0.16
    glyph = icons._svg_document("orbit", ACCENT, 1.55)
    renderer = QSvgRenderer(QByteArray(glyph))
    renderer.render(painter, QRectF(inset, inset, size - inset * 2, size - inset * 2))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def write_ico(path: Path, images: list[QImage]) -> None:
    """Write a PNG-compressed ICO containing every image."""
    payloads = [(image.width(), png_bytes(image)) for image in images]
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = len(header) + 16 * len(payloads)
    entries = b""
    body = b""
    for width, payload in payloads:
        dimension = 0 if width >= 256 else width
        entries += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,
            0,
            1,
            32,
            len(payload),
            offset,
        )
        body += payload
        offset += len(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + body)


def main() -> int:
    target = ROOT / "assets" / "orbit.ico"
    images = [render(size) for size in SIZES]
    write_ico(target, images)
    print(f"Wrote {target} ({target.stat().st_size} bytes, {len(images)} sizes)")
    preview = ROOT / "assets" / "orbit.png"
    images[-1].save(str(preview), "PNG")
    print(f"Wrote {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
