"""Icon system.

ORBIT ships a small library of stroked SVG glyphs instead of an emoji font or
bitmap assets, so icons stay crisp at any size and always take the theme's
colour.  Users may override any icon with:

* a library name (``folder``),
* a custom image file (``file:C:\\art\\logo.png`` or just a path) in **any
  format Qt can decode** — PNG, JPEG, WEBP, GIF, BMP, TIFF, ICO, SVG, XPM…
* any short text (rendered as text — this is how an emoji still works if the
  user really wants one).

Custom images are fitted into the round button and masked to a circle, so a
photo or logo looks deliberate rather than like a pasted square.

Nothing here needs the network or an icon theme installed on the machine.

Note on scaling: a ``QPainter`` on a pixmap with ``devicePixelRatio > 1`` works
in *logical* pixels — the pixmap's device pixels are ``ratio`` times larger.
Every renderer below therefore paints into ``size`` logical pixels, never
``size * ratio``; getting that wrong silently clips the right/bottom quarter of
every icon on a 125 %/150 % display.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer

# --------------------------------------------------------------------- library
ICONS: dict[str, str] = {
    # --- ORBIT & navigation
    "orbit": "<circle cx='12' cy='12' r='3.4'/><path d='M12 3.4a8.6 8.6 0 0 1 0 17.2 8.6 8.6 0 0 1 0-17.2' opacity='.45'/>"
             "<circle cx='20.6' cy='12' r='1.5'/><circle cx='3.4' cy='12' r='1.5'/>",
    "circle-dot": "<circle cx='12' cy='12' r='8.5'/><circle cx='12' cy='12' r='2.6'/>",
    "grid": "<rect x='3.5' y='3.5' width='7' height='7' rx='1.6'/><rect x='13.5' y='3.5' width='7' height='7' rx='1.6'/>"
            "<rect x='3.5' y='13.5' width='7' height='7' rx='1.6'/><rect x='13.5' y='13.5' width='7' height='7' rx='1.6'/>",
    "layers": "<path d='M12 3.5 20.5 8 12 12.5 3.5 8z'/><path d='M3.5 12 12 16.5 20.5 12'/><path d='M3.5 16 12 20.5 20.5 16'/>",
    "compass": "<circle cx='12' cy='12' r='8.6'/><path d='M15.5 8.5l-2 5-5 2 2-5z'/>",
    "map-pin": "<path d='M12 21s6.5-6.1 6.5-11a6.5 6.5 0 0 0-13 0C5.5 14.9 12 21 12 21z'/><circle cx='12' cy='10' r='2.4'/>",
    "home": "<path d='M4 11 12 4.5 20 11v8.5h-5.5V14h-5v5.5H4z'/>",
    # --- launch
    "app-window": "<rect x='3' y='4.5' width='18' height='15' rx='2.5'/><path d='M3 9h18'/>"
                  "<path d='M6.2 6.8h.01M8.8 6.8h.01'/>",
    "globe": "<circle cx='12' cy='12' r='8.6'/><path d='M3.4 12h17.2'/>"
             "<path d='M12 3.4c3 3.6 3 13.6 0 17.2-3-3.6-3-13.6 0-17.2z'/>",
    "folder": "<path d='M3 7.4A2.4 2.4 0 0 1 5.4 5h3.8l2 2.6h7.4A2.4 2.4 0 0 1 21 10v7.6a2.4 2.4 0 0 1-2.4 2.4H5.4A2.4 2.4 0 0 1 3 17.6z'/>",
    "folder-open": "<path d='M3 18.5V7.5A2 2 0 0 1 5 5.5h4l2 2.5h6a2 2 0 0 1 2 2v1'/>"
                   "<path d='M3 18.5l2.6-7.5h15.9l-2.6 7.5z'/>",
    "file": "<path d='M6 3.5h7.5L19 9v11.5H6z'/><path d='M13.5 3.5V9H19'/>",
    "file-text": "<path d='M6 3.5h7.5L19 9v11.5H6z'/><path d='M13.5 3.5V9H19'/><path d='M9 13h6M9 16.2h6'/>",
    "image": "<rect x='3.5' y='4.5' width='17' height='15' rx='2.5'/><circle cx='9' cy='10' r='1.7'/>"
             "<path d='M4.2 17.2l4.4-4.2 4.2 4 3-2.7 4 3.4'/>",
    "monitor": "<rect x='3' y='4.5' width='18' height='12' rx='2.5'/><path d='M9 20h6M12 16.5V20'/>",
    "terminal": "<rect x='3' y='4.5' width='18' height='15' rx='2.5'/><path d='M7 10l3 2.4-3 2.4M13.5 15h4'/>",
    "calculator": "<rect x='5' y='3.5' width='14' height='17' rx='2.5'/><path d='M8 8h8'/>"
                  "<path d='M8.5 12h.01M12 12h.01M15.5 12h.01M8.5 15.6h.01M12 15.6h.01M15.5 15.6h.01'/>",
    "code": "<path d='M9 7.5 4.5 12 9 16.5M15 7.5 19.5 12 15 16.5'/>",
    "link": "<path d='M9.5 14.5 14.5 9.5'/><path d='M11.5 6.6l1.4-1.4a4 4 0 0 1 5.7 5.7l-1.5 1.4'/>"
            "<path d='M12.5 17.4l-1.4 1.4a4 4 0 0 1-5.7-5.7l1.5-1.4'/>",
    "external-link": "<path d='M14 4.5h5.5V10'/><path d='M19.5 4.5 12 12'/><path d='M18 14.5v4.5H5.5V6.5H10'/>",
    "cloud": "<path d='M7 18.5h10.4a3.5 3.5 0 0 0 .5-7 5 5 0 0 0-9.7 1.2A3.6 3.6 0 0 0 7 18.5z'/>",
    "database": "<path d='M4.5 7c0-1.7 3.4-3 7.5-3s7.5 1.3 7.5 3-3.4 3-7.5 3-7.5-1.3-7.5-3z'/>"
                "<path d='M4.5 7v5c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V7'/>"
                "<path d='M4.5 12v5c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-5'/>",
    # --- media
    "play": "<path d='M8.6 5.6 18.4 12 8.6 18.4z'/>",
    "pause": "<path d='M9.4 5.8v12.4M14.6 5.8v12.4'/>",
    "stop": "<rect x='6.5' y='6.5' width='11' height='11' rx='2.4'/>",
    "next": "<path d='M6.5 5.8 15 12l-8.5 6.2z'/><path d='M18 5.8v12.4'/>",
    "previous": "<path d='M17.5 5.8 9 12l8.5 6.2z'/><path d='M6 5.8v12.4'/>",
    "music": "<path d='M9 17V6l10-2v11'/><circle cx='6.8' cy='17.2' r='2.2'/><circle cx='16.8' cy='15.2' r='2.2'/>",
    "film": "<rect x='3' y='4.5' width='18' height='15' rx='2.5'/><path d='M8 4.5v15M16 4.5v15M3 9h5M3 15h5M16 9h5M16 15h5'/>",
    "video": "<rect x='3' y='6' width='12.5' height='12' rx='2.5'/><path d='M15.5 12l5.5-3.5v7z'/>",
    "volume": "<path d='M4.5 10h3l4-3.5v11l-4-3.5h-3z'/><path d='M14.8 9.6a4 4 0 0 1 0 4.8M17.4 7.4a7 7 0 0 1 0 9.2'/>",
    "volume-mute": "<path d='M4.5 10h3l4-3.5v11l-4-3.5h-3z'/><path d='M15 10.5l4 4M19 10.5l-4 4'/>",
    "headphones": "<path d='M5 14v-2a7 7 0 0 1 14 0v2'/><path d='M3.6 15.5a2 2 0 0 1 2-2h1.2v4.8H5.6a2 2 0 0 1-2-2z'/>"
                  "<path d='M20.4 15.5a2 2 0 0 0-2-2h-1.2v4.8h1.2a2 2 0 0 0 2-2z'/>",
    # --- system & tools
    "settings": "<circle cx='12' cy='12' r='3'/><path d='M12 3.5v2.3M12 18.2v2.3M4.9 7.9l2 1.1M17.1 15l2 1.1'/>"
                "<path d='M4.9 16.1l2-1.1M17.1 9l2-1.1'/>",
    "sliders": "<path d='M5 8h8.5M18.5 8h.5M5 16h3.5M13 16h6.5'/><circle cx='16' cy='8' r='2.1'/>"
               "<circle cx='10.5' cy='16' r='2.1'/>",
    "wrench": "<path d='M15.3 4.6a5.1 5.1 0 0 0-6 7.2L4 17.2v2.4h2.4l5.4-5.3a5.1 5.1 0 0 0 7.2-6l-3 3-2.7-2.7z'/>",
    "keyboard": "<rect x='2.5' y='6.5' width='19' height='11' rx='2.5'/><path d='M6 10h.01M9 10h.01M12 10h.01M15 10h.01M18 10h.01M7 13.6h10'/>",
    "mouse-pointer": "<path d='M6.5 3.6 18 12.4h-4.9l3 6-2.5 1.2-3-6.1-4.1 3.7z'/>",
    "cpu": "<rect x='6.5' y='6.5' width='11' height='11' rx='2.5'/><path d='M10 3.5v3M14 3.5v3M10 17.5v3M14 17.5v3M3.5 10h3M3.5 14h3M17.5 10h3M17.5 14h3'/>",
    "activity": "<path d='M3 12h3.4L9 6.4l3 11.2 2.5-5.6H21'/>",
    "zap": "<path d='M13.4 3 6 13.6h5.2L10.6 21 18 10.4h-5.2z'/>",
    "shield": "<path d='M12 3.5 19 6v6.2c0 4.2-3 7.3-7 9.3-4-2-7-5.1-7-9.3V6z'/>",
    "lock": "<rect x='5' y='10.5' width='14' height='9.5' rx='2.5'/><path d='M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5'/>",
    "key": "<circle cx='8' cy='15.5' r='3.5'/><path d='M10.6 13.2 19.5 4.3M17 6.8l2.4 2.4M14.6 9.2l2.4 2.4'/>",
    "power": "<path d='M12 4v8'/><path d='M7.4 6.9a7 7 0 1 0 9.2 0'/>",
    "refresh": "<path d='M20 12a8 8 0 1 1-2.5-5.8'/><path d='M20 4.6V10h-5.4'/>",
    "rotate": "<path d='M20 12a8 8 0 1 1-2.5-5.8'/><path d='M4.6 5.6V11H10'/>",
    "save": "<path d='M4.5 5.5h11l4 4v9h-15z'/><path d='M8.5 5.5v5h7v-5M8.5 18.5v-5h7v5'/>",
    "download": "<path d='M12 4v11'/><path d='M7.6 11.6 12 16l4.4-4.4'/><path d='M4.5 19.5h15'/>",
    "upload": "<path d='M12 20V9'/><path d='M7.6 12.4 12 8l4.4 4.4'/><path d='M4.5 4.5h15'/>",
    "printer": "<path d='M7 8.5V3.5h10v5'/><rect x='3.5' y='8.5' width='17' height='7.5' rx='2.5'/>"
               "<path d='M7 14.5h10v6H7z'/>",
    "scissors": "<circle cx='6.5' cy='18' r='2.4'/><circle cx='6.5' cy='6' r='2.4'/><path d='M8.6 7.4 20 18.5M8.6 16.6 20 5.5'/>",
    "package": "<path d='M12 3.2 20.5 8v8L12 20.8 3.5 16V8z'/><path d='M3.5 8 12 12.8 20.5 8M12 12.8v8'/>",
    "map": "<path d='M9 4.5 3.5 6.8v12.7L9 17.2l6 2.3 5.5-2.3V4.5L15 6.8z'/><path d='M9 4.5v12.7M15 6.8v12.7'/>",
    "camera": "<path d='M4.5 8.5h3l1.4-2h6.2l1.4 2h3v9.5h-15z'/><circle cx='12' cy='13' r='3.1'/>",
    "mic": "<rect x='9.5' y='3.5' width='5' height='10' rx='2.5'/><path d='M6 11.5a6 6 0 0 0 12 0M12 17.5V21'/>",
    "bell": "<path d='M6.5 16.5h11l-1.3-2.1V10a4.2 4.2 0 0 0-8.4 0v4.4z'/><path d='M10 19a2 2 0 0 0 4 0'/>",
    "wifi": "<path d='M3.5 9.5a13 13 0 0 1 17 0'/><path d='M6.6 12.8a8.5 8.5 0 0 1 10.8 0'/>"
            "<path d='M9.7 16a4 4 0 0 1 4.6 0'/><path d='M12 19h.01'/>",
    "battery": "<rect x='2.5' y='8' width='16' height='8' rx='2.5'/><path d='M21 11v2'/><path d='M6 11v2'/>",
    # --- content
    "search": "<circle cx='11' cy='11' r='6.2'/><path d='M15.6 15.6 20.5 20.5'/>",
    "star": "<path d='M12 4l2.5 5.4 5.5.7-4 3.9 1 5.6-5-3-5 3 1-5.6-4-3.9 5.5-.7z'/>",
    "heart": "<path d='M12 20S4.5 15.4 4.5 10.6A4.1 4.1 0 0 1 12 8a4.1 4.1 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z'/>",
    "bookmark": "<path d='M6.5 4.5h11v16l-5.5-3.5L6.5 20.5z'/>",
    "book": "<path d='M4.5 5.5A2 2 0 0 1 6.5 4H11v16H6.5a2 2 0 0 1-2-2z'/>"
            "<path d='M19.5 5.5A2 2 0 0 0 17.5 4H13v16h4.5a2 2 0 0 0 2-2z'/>",
    "mail": "<rect x='3' y='5.5' width='18' height='13' rx='2.5'/><path d='M4 7.5l8 5.5 8-5.5'/>",
    "message": "<path d='M20 15.4a2.5 2.5 0 0 1-2.5 2.5H8L4 21.5V6.6a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20 6.6z'/>",
    "send": "<path d='M20.5 3.5 3.6 10.2l6.4 2.9 2.9 6.4z'/><path d='M10 13.1l10.5-9.6'/>",
    "calendar": "<rect x='3.5' y='5.5' width='17' height='14' rx='2.5'/><path d='M3.5 10h17M8 3.5v4M16 3.5v4'/>",
    "clock": "<circle cx='12' cy='12' r='8.6'/><path d='M12 7.4V12l3.4 2.2'/>",
    "user": "<circle cx='12' cy='9' r='3.6'/><path d='M5 20.5c1.4-3.4 4-5.1 7-5.1s5.6 1.7 7 5.1'/>",
    "users": "<circle cx='9.5' cy='9' r='3.2'/><path d='M3.5 20c1.2-3 3.4-4.6 6-4.6s4.8 1.6 6 4.6'/>"
             "<path d='M16 6.2a3.2 3.2 0 0 1 0 6.2M17.5 15.8c2 .5 3.4 2 4 4.2'/>",
    "tag": "<path d='M11.5 3.5H20v8.5l-8.6 8.6a2 2 0 0 1-2.8 0l-5.7-5.7a2 2 0 0 1 0-2.8z'/>"
            "<path d='M16.2 7.8h.01'/>",
    "flag": "<path d='M6 20.5V4.5'/><path d='M6 5.5h11l-2.2 3.6L17 12.7H6z'/>",
    "filter": "<path d='M4 6h16l-6 6.6V19l-4-2v-4.4z'/>",
    "eye": "<path d='M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z'/><circle cx='12' cy='12' r='2.7'/>",
    "gift": "<rect x='3.5' y='8.5' width='17' height='11.5' rx='2'/><path d='M3.5 12.5h17M12 8.5V20'/>"
            "<path d='M12 8.5C12 6 10.8 4.5 9 4.5a2.4 2.4 0 0 0 0 4zM12 8.5c0-2.5 1.2-4 3-4a2.4 2.4 0 0 1 0 4z'/>",
    "briefcase": "<rect x='3.5' y='7.5' width='17' height='12' rx='2.5'/>"
                 "<path d='M9 7.5V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8v1.7M3.5 12.5h17'/>",
    "coffee": "<path d='M4.5 8.5h12v5.8a4.2 4.2 0 0 1-4.2 4.2H8.7a4.2 4.2 0 0 1-4.2-4.2z'/>"
              "<path d='M16.5 10h1.6a2.5 2.5 0 0 1 0 5h-1.6'/>",
    "trending-up": "<path d='M3.5 16.5 9.5 10.5l3.5 3.5 7-7'/><path d='M15.5 7h4.5v4.5'/>",
    "chart": "<path d='M4 20V4'/><path d='M8 20v-7M13 20V8M18 20v-4'/>",
    "palette": "<path d='M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 2-.9 2-2 0-1.2-1-1.6-1-2.7 0-.9.7-1.6 1.6-1.6h1.9A3 3 0 0 0 19.5 11c0-4.1-3.4-7.5-7.5-7.5z'/>"
               "<path d='M8 9.4h.01M12 7.8h.01M15.8 9.8h.01M7.6 13.4h.01'/>",
    "wand": "<path d='M5 19.5 15 9.5'/><path d='M13 7.5 16.5 4l4 4L17 11.5z'/><path d='M5 19.5l1.6-4.6 3 3z'/>",
    "edit": "<path d='M15.5 4.5 19.5 8.5 8.5 19.5H4.5v-4z'/><path d='M13.4 6.6 17.4 10.6'/>",
    "trash": "<path d='M4.5 7h15'/><path d='M9.5 7V4.8h5V7'/><path d='M6.5 7l1 12.5h9L17.5 7'/>",
    "plus": "<path d='M12 5v14M5 12h14'/>",
    "minus": "<path d='M5 12h14'/>",
    "check": "<path d='M5 12.6 9.6 17 19 7.4'/>",
    "x": "<path d='M6 6l12 12M18 6 6 18'/>",
    "chevron-right": "<path d='M9.5 5.5 16 12l-6.5 6.5'/>",
    "chevron-left": "<path d='M14.5 5.5 8 12l6.5 6.5'/>",
    "chevron-down": "<path d='M5.5 9.5 12 16l6.5-6.5'/>",
    "chevron-up": "<path d='M5.5 14.5 12 8l6.5 6.5'/>",
    "arrow-up": "<path d='M12 19.5V5'/><path d='M6 11l6-6 6 6'/>",
    "arrow-down": "<path d='M12 4.5V19'/><path d='M6 13l6 6 6-6'/>",
    "arrow-right": "<path d='M4.5 12H19'/><path d='M13 6l6 6-6 6'/>",
    "arrow-left": "<path d='M19.5 12H5'/><path d='M11 6l-6 6 6 6'/>",
    "info": "<circle cx='12' cy='12' r='8.6'/><path d='M12 11v5.6M12 7.9h.01'/>",
    "help": "<circle cx='12' cy='12' r='8.6'/><path d='M9.6 9.8a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1.1.9-1.1 1.7v.4'/>"
            "<path d='M12 17h.01'/>",
    "alert": "<path d='M12 3.8 3.2 19.7h17.6z'/><path d='M12 10v4.4M12 17.2h.01'/>",
    "moon": "<path d='M20 14.6A8.6 8.6 0 0 1 9.4 4 8.6 8.6 0 1 0 20 14.6z'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2.6v2.3M12 19.1v2.3M2.6 12h2.3M19.1 12h2.3'/>"
           "<path d='M5.4 5.4 7 7M17 17l1.6 1.6M5.4 18.6 7 17M17 7l1.6-1.6'/>",
    "screenshot": "<path d='M4.5 9V6.5a2 2 0 0 1 2-2H9M15 4.5h2.5a2 2 0 0 1 2 2V9M19.5 15v2.5a2 2 0 0 1-2 2H15'/>"
                  "<path d='M9 19.5H6.5a2 2 0 0 1-2-2V15'/><path d='M8.5 12h7'/>",
    "maximize": "<path d='M4.5 9V4.5H9M15 4.5h4.5V9M19.5 15v4.5H15M9 19.5H4.5V15'/>",
    "minimize": "<path d='M4.5 12h15'/>",
    "rocket": "<path d='M12 3.5c3.4 2.4 5 5.6 5 9.4l-2.6 3.4H9.6L7 12.9c0-3.8 1.6-7 5-9.4z'/>"
              "<circle cx='12' cy='10' r='1.7'/><path d='M9.6 16.8 8 21l4-1.6 4 1.6-1.6-4.2'/>",
    "brain": "<path d='M9.5 4.5A3 3 0 0 0 6.5 7.5 2.8 2.8 0 0 0 5 12a2.9 2.9 0 0 0 1.6 2.6A3 3 0 0 0 10 19.5V4.9a2 2 0 0 0-.5-.4z'/>"
             "<path d='M14.5 4.5a3 3 0 0 1 3 3 2.8 2.8 0 0 1 1.5 4.5 2.9 2.9 0 0 1-1.6 2.6A3 3 0 0 1 14 19.5V4.9a2 2 0 0 1 .5-.4z'/>",
    "shopping-cart": "<circle cx='9.5' cy='19.5' r='1.6'/><circle cx='17.5' cy='19.5' r='1.6'/>"
                     "<path d='M2.8 4.5h2.4l2.4 10.5h10.2l2.2-7.5H6.2'/>",
    "tv": "<rect x='2.5' y='6.5' width='19' height='12.5' rx='2.5'/><path d='M8 3.5 12 6.5l4-3'/>",
    "smartphone": "<rect x='6.5' y='2.5' width='11' height='19' rx='2.8'/><path d='M10.5 18.5h3'/>",
    "type": "<path d='M5 6.5V4.5h14v2'/><path d='M12 4.5v15M8.5 19.5h7'/>",
    "wind": "<path d='M3.5 8.5h11a3 3 0 1 0-3-3'/><path d='M3.5 15.5h13a3 3 0 1 1-3 3'/><path d='M3.5 12h7'/>",
    "droplet": "<path d='M12 3.5c3 3.8 5.5 6.6 5.5 9.6a5.5 5.5 0 0 1-11 0c0-3 2.5-5.8 5.5-9.6z'/>",
    "anchor": "<circle cx='12' cy='5.5' r='2.2'/><path d='M12 7.7V20'/><path d='M5 12.5a7 7 0 0 0 14 0'/>",
    "award": "<circle cx='12' cy='9' r='4.6'/><path d='M8.6 13 7.5 20.5l4.5-2.4 4.5 2.4L15.4 13'/>",
    "box": "<rect x='3.5' y='6.5' width='17' height='13' rx='2.5'/><path d='M3.5 11h17M8.5 6.5V11M15.5 11v3'/>",
    "git-branch": "<circle cx='6.5' cy='5.5' r='2.2'/><circle cx='6.5' cy='18.5' r='2.2'/><circle cx='17.5' cy='8.5' r='2.2'/>"
                  "<path d='M6.5 7.7v8.6M8.7 5.5h4.6a4.2 4.2 0 0 1 4.2 4.2v.4'/>",
    "git-commit": "<circle cx='12' cy='12' r='3.4'/><path d='M3.5 12h5M15.5 12h5'/>",
    "server": "<rect x='3' y='4' width='18' height='7' rx='2.2'/><rect x='3' y='13' width='18' height='7' rx='2.2'/>"
              "<path d='M7 7.5h.01M7 16.5h.01'/>",
    "sidebar": "<rect x='3' y='4.5' width='18' height='15' rx='2.5'/><path d='M9.5 4.5v15'/>",
    "crosshair": "<circle cx='12' cy='12' r='8.4'/><path d='M12 4v4M12 16v4M4 12h4M16 12h4'/>",
    "sparkle": "<path d='M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z'/>"
               "<path d='M18.5 16.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z'/>",
}

ICON_GROUPS: dict[str, tuple[str, ...]] = {
    "Popular": (
        "orbit", "circle-dot", "grid", "globe", "folder", "folder-open", "file", "file-text",
        "play", "pause", "terminal", "settings", "wrench", "star", "heart", "zap",
    ),
    "Launch": (
        "app-window", "monitor", "image", "code", "link", "external-link", "cloud", "database",
        "map", "compass", "home", "map-pin",
    ),
    "Media": (
        "play", "pause", "stop", "next", "previous", "music", "film", "video", "volume",
        "volume-mute", "headphones", "mic",
    ),
    "System": (
        "settings", "sliders", "cpu", "activity", "shield", "lock", "key", "power", "refresh",
        "rotate", "save", "download", "upload", "printer", "scissors", "package", "camera",
        "bell", "wifi", "battery", "screenshot", "mouse-pointer", "keyboard",
    ),
    "Content": (
        "search", "star", "heart", "bookmark", "book", "mail", "message", "send", "calendar",
        "clock", "user", "users", "tag", "flag", "filter", "eye", "gift", "briefcase", "coffee",
        "trending-up", "chart", "box", "server", "git-branch", "git-commit", "tv", "smartphone",
        "type", "wind", "droplet", "anchor", "award", "rocket", "brain", "shopping-cart",
        "sidebar", "crosshair", "sparkle", "palette", "wand", "edit", "trash",
    ),
    "Interface": (
        "plus", "minus", "check", "x", "chevron-up", "chevron-down", "chevron-left",
        "chevron-right", "arrow-up", "arrow-down", "arrow-left", "arrow-right", "info", "help",
        "alert", "moon", "sun", "maximize", "minimize",
    ),
}

DEFAULT_ICON = "circle-dot"
STROKE_WIDTH = 1.65


def available_icons() -> list[str]:
    return sorted(ICONS)


def is_library_icon(value: str) -> bool:
    return str(value or "").strip() in ICONS


# Formats we always accept for a custom icon, even before Qt's plugin list is
# consulted (useful in stripped-down environments).
IMAGE_SUFFIX_FALLBACK = (
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".bmp", ".dib", ".webp",
    ".svg", ".svgz", ".ico", ".icns", ".tif", ".tiff", ".tga", ".cur",
    ".wbmp", ".pbm", ".pgm", ".ppm", ".xbm", ".xpm",
)


@lru_cache(maxsize=1)
def supported_image_suffixes() -> tuple[str, ...]:
    """Every image suffix this Qt build can actually decode."""
    suffixes = set(IMAGE_SUFFIX_FALLBACK)
    try:
        for raw in QImageReader.supportedImageFormats():
            name = bytes(raw).decode("ascii", "ignore").strip().lower()
            if name == "pdf":
                continue  # Qt cannot rasterise PDF without an optional plugin
            if name:
                suffixes.add("." + name)
    except Exception:  # pragma: no cover - defensive, plugin list is optional
        pass
    return tuple(sorted(suffixes))


def image_file_filter() -> str:
    """A file-dialog filter covering every supported format."""
    patterns = " ".join(f"*{suffix}" for suffix in supported_image_suffixes())
    return f"Images ({patterns})"


def resolve_icon(value: str | None) -> tuple[str, str]:
    """Classify an icon value as ``("library" | "image" | "text", payload)``."""
    text = str(value or "").strip()
    if not text:
        return "library", DEFAULT_ICON
    if text.startswith("file:"):
        return "image", text[5:].strip()
    if text in ICONS:
        return "library", text
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return "text", "🔗"
    candidate = Path(expand_icon_path(text))
    if candidate.suffix.lower() in supported_image_suffixes():
        return "image", text
    if candidate.is_file():
        # An odd extension on a real file: let Qt decide whether it can decode it.
        return "image", text
    return "text", text


def _svg_document(name: str, color: str, stroke: float) -> bytes:
    body = ICONS.get(name, ICONS[DEFAULT_ICON])
    slack = stroke
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {24 + slack * 2} {24 + slack * 2}'>"
        f"<g transform='translate({slack} {slack})' fill='none' stroke='{color}' "
        f"stroke-width='{stroke}' stroke-linecap='round' stroke-linejoin='round'>{body}</g></svg>"
    ).encode("utf-8")


def _new_pixmap(size: float, ratio: float) -> QPixmap:
    """An empty transparent pixmap of ``size`` *logical* pixels."""
    width = max(8, int(math.ceil(size * ratio)))
    pixmap = QPixmap(width, width)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


@lru_cache(maxsize=512)
def _render_svg(name: str, size: int, color: str, stroke: float, ratio: float, opacity: float) -> QPixmap:
    # ``color`` is a plain 6-digit hex: Qt's SVG parser does not understand
    # 8-digit ARGB, so alpha is applied through the painter instead.
    pixmap = _new_pixmap(size, ratio)
    renderer = QSvgRenderer(QByteArray(_svg_document(name, color, stroke)))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setOpacity(max(0.0, min(1.0, opacity)))
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon_pixmap(
    value: str | None,
    size: int,
    color: QColor | str = "#FFFFFF",
    *,
    stroke: float = STROKE_WIDTH,
    circular: bool = True,
) -> QPixmap:
    """Render any icon value into a pixmap of ``size`` logical pixels.

    ``circular`` masks a custom *image* icon into a circle, which is how it is
    shown inside a round button.  It has no effect on library glyphs or text.
    """
    kind, payload = resolve_icon(value)
    if color is not None and not isinstance(color, QColor):
        color = QColor(color)
    if color is None:
        color = QColor("#FFFFFF")
    ratio = 2.0
    app = QGuiApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            ratio = max(1.0, float(screen.devicePixelRatio()))

    size = max(8, int(size))
    if kind == "library":
        return _render_svg(
            payload,
            size,
            color.name(QColor.NameFormat.HexRgb),
            stroke,
            ratio,
            round(color.alphaF(), 3),
        )
    if kind == "image":
        path = expand_icon_path(payload)
        return _render_image_cached(path, size, ratio, circular, _image_signature(path))
    return _render_text(payload, size, color, ratio)


def _image_signature(path: str) -> float:
    """Cheap cache key component so a re-edited file is re-rendered."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


@lru_cache(maxsize=256)
def _render_image_cached(path: str, size: int, ratio: float, circular: bool, _mtime: float) -> QPixmap:
    return _render_image(path, size, ratio, circular)


def clear_image_cache() -> None:
    _render_image_cached.cache_clear()


def _load_image(path: str, max_pixels: float) -> QImage:
    """Decode any Qt-readable image, honouring EXIF orientation and SVG."""
    suffix = Path(path).suffix.lower()
    if suffix in {".svg", ".svgz"}:
        renderer = QSvgRenderer(QByteArray(_read_bytes(path)))
        if not renderer.isValid():
            return QImage()
        dim = max(32, int(math.ceil(max_pixels)))
        image = QImage(dim, dim, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, dim, dim))
        painter.end()
        return image

    reader = QImageReader(path)
    reader.setAutoTransform(True)  # rotates/flattens phone photos correctly
    size = reader.size()
    if size.isValid() and max_pixels > 0:
        if size.width() > max_pixels or size.height() > max_pixels:
            target = QSize(int(max_pixels), int(max_pixels))
            reader.setScaledSize(size.scaled(target, Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return b""


def _render_image(path: str, size: int, ratio: float, circular: bool) -> QPixmap:
    pixmap = _new_pixmap(size, ratio)
    source = _load_image(path, size * ratio * 1.5)
    if source.isNull():
        return _render_text("?", size, QColor("#9AA1B2"), ratio)

    # Fit inside the icon box, keeping the aspect ratio, with a little padding
    # so a photo never touches the edge of the round surface.
    box = size * (0.98 if circular else 0.94)
    scaled = source.scaled(
        max(1, int(box * ratio)),
        max(1, int(box * ratio)),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    # ``scaled`` is in device pixels; convert back to logical for the painter.
    draw_w = scaled.width() / ratio
    draw_h = scaled.height() / ratio
    target = QRectF((size - draw_w) / 2.0, (size - draw_h) / 2.0, draw_w, draw_h)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if circular:
        clip = QPainterPath()
        clip.addEllipse(QRectF(0, 0, size, size))
        painter.setClipPath(clip)
    painter.drawImage(target, scaled)
    painter.end()
    return pixmap


def _render_text(text: str, size: int, color: QColor, ratio: float) -> QPixmap:
    pixmap = _new_pixmap(size, ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    font = QFont()
    font.setPixelSize(max(9, int(size * 0.86)))
    painter.setFont(font)
    painter.setPen(color)
    glyph = text[:2]
    rect = painter.fontMetrics().boundingRect(glyph)
    painter.drawText(
        int((size - rect.width()) / 2) - rect.x(),
        int((size - rect.height()) / 2) - rect.y(),
        glyph,
    )
    painter.end()
    return pixmap


def expand_icon_path(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
