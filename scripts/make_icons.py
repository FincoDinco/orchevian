"""Draw Orchevian's app icon and write every platform format into packaging/icons/.

    uv run python scripts/make_icons.py

The artwork follows macOS icon proportions: a rounded square filling 824 of 1024
pixels with a soft shadow, so it sits naturally in the Dock, Windows and Linux.
"""

from __future__ import annotations

import io
import math
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageFilter  # noqa: E402
from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

OUT = Path(__file__).resolve().parents[1] / "packaging" / "icons"
SIZE = 1024
TILE = QRectF(100, 100, 824, 824)
RADIUS = 185  # macOS icon corner proportion (~22% of the tile).


def _qimage_to_pil(image: QImage) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def draw_tile() -> Image.Image:
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    tile = QPainterPath()
    tile.addRoundedRect(TILE, RADIUS, RADIUS)

    background = QLinearGradient(TILE.topLeft(), TILE.bottomRight())
    background.setColorAt(0.0, QColor("#7466FF"))
    background.setColorAt(0.55, QColor("#4B38D6"))
    background.setColorAt(1.0, QColor("#24196B"))
    p.fillPath(tile, background)
    # A soft light from above gives the surface depth.
    glow = QRadialGradient(QPointF(512, 170), 620)
    glow.setColorAt(0.0, QColor(255, 255, 255, 70))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(tile, glow)

    center = QPointF(512, 520)
    # The "O": a bright ring.
    ring = QLinearGradient(QPointF(512, 250), QPointF(512, 790))
    ring.setColorAt(0.0, QColor("#FFFFFF"))
    ring.setColorAt(1.0, QColor("#D5CEFF"))
    p.setPen(QPen(QBrush(ring), 78, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(center, 238, 238)
    # An orbit crossing the ring, and the node travelling on it.
    p.save()
    p.translate(center)
    p.rotate(-28)
    p.setPen(QPen(QColor(255, 255, 255, 120), 16, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(-345, -120, 690, 240), 200 * 16, 300 * 16)
    angle = math.radians(-20)
    node = QPointF(345 * math.cos(angle), -120 * math.sin(angle))
    halo = QRadialGradient(node, 90)
    halo.setColorAt(0.0, QColor(120, 240, 255, 150))
    halo.setColorAt(1.0, QColor(120, 240, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(halo)
    p.drawEllipse(node, 90, 90)
    p.setBrush(QColor("#8CF4FF"))
    p.drawEllipse(node, 46, 46)
    p.restore()
    p.end()
    return _qimage_to_pil(image)


def with_shadow(tile: Image.Image) -> Image.Image:
    alpha = tile.getchannel("A")
    shadow = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.35)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    canvas.alpha_composite(shadow, (0, 12))
    canvas.alpha_composite(tile)
    return canvas


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    del app
    OUT.mkdir(parents=True, exist_ok=True)
    master = with_shadow(draw_tile())
    master.save(OUT / "orchevian-1024.png", optimize=True)
    for size in (16, 32, 48, 64, 128, 256, 512):
        master.resize((size, size), Image.LANCZOS).save(OUT / f"orchevian-{size}.png",
                                                        optimize=True)
    master.save(OUT / "orchevian.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128),
                                               (256, 256), (512, 512), (1024, 1024)])
    master.save(OUT / "orchevian.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                              (64, 64), (128, 128), (256, 256)])
    print(f"Wrote icons to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
