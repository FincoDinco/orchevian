"""Draw Orchevian's app icon and write every platform format into packaging/icons/.

    uv run python scripts/make_icons.py

The artwork follows macOS icon proportions: a rounded square filling 824 of 1024
pixels with a soft shadow, so it sits naturally in the Dock, Windows and Linux.
The same shapes also go into `orchevian.icon`, an Icon Composer document whose
separate layers macOS 26 and later draw as Liquid Glass, including the clear and
tinted icon styles. It also draws the background of the macOS disk image window.
"""

from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import installers  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402
from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QFont,
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
# The "O" ring, and an orbit crossing it with a node travelling on it.
CENTER = (512, 520)
RING_RADIUS, RING_WIDTH = 238, 78
ORBIT_RX, ORBIT_RY, ORBIT_WIDTH, ORBIT_TILT = 345, 120, 16, -28
ORBIT_START, ORBIT_SPAN = 200, 300  # Degrees, counter-clockwise from 3 o'clock.
NODE_ANGLE, NODE_RADIUS = -20, 46


def _qimage_to_pil(image: QImage) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def orbit_point(degrees: float, scale: float = 1.0) -> tuple[float, float]:
    """A point on the untilted orbit, centred on the origin, as Qt measures arc angles."""
    angle = math.radians(degrees)
    return scale * ORBIT_RX * math.cos(angle), -scale * ORBIT_RY * math.sin(angle)


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

    center = QPointF(*CENTER)
    # The "O": a bright ring.
    ring = QLinearGradient(QPointF(512, 250), QPointF(512, 790))
    ring.setColorAt(0.0, QColor("#FFFFFF"))
    ring.setColorAt(1.0, QColor("#D5CEFF"))
    p.setPen(QPen(QBrush(ring), RING_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(center, RING_RADIUS, RING_RADIUS)
    # An orbit crossing the ring, and the node travelling on it.
    p.save()
    p.translate(center)
    p.rotate(ORBIT_TILT)
    p.setPen(QPen(QColor(255, 255, 255, 120), ORBIT_WIDTH, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(-ORBIT_RX, -ORBIT_RY, 2 * ORBIT_RX, 2 * ORBIT_RY), ORBIT_START * 16,
              ORBIT_SPAN * 16)
    node = QPointF(*orbit_point(NODE_ANGLE))
    halo = QRadialGradient(node, 90)
    halo.setColorAt(0.0, QColor(120, 240, 255, 150))
    halo.setColorAt(1.0, QColor(120, 240, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(halo)
    p.drawEllipse(node, 90, 90)
    p.setBrush(QColor("#8CF4FF"))
    p.drawEllipse(node, NODE_RADIUS, NODE_RADIUS)
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


def write_icon_composer(folder: Path) -> None:
    """Write the artwork as an Icon Composer document with one layer per shape.

    macOS masks Icon Composer layers to the whole 1024-point canvas, so the shapes
    scale up from the 824-pixel tile. The system adds glass, lighting and shadows, and
    redraws the layers for dark, clear and tinted icon styles.
    """
    scale = SIZE / TILE.width()
    cx = SIZE / 2 + (CENTER[0] - TILE.center().x()) * scale
    cy = SIZE / 2 + (CENTER[1] - TILE.center().y()) * scale
    tilted = f'transform="translate({cx:.1f} {cy:.1f}) rotate({ORBIT_TILT})"'
    start = orbit_point(ORBIT_START, scale)
    end = orbit_point(ORBIT_START + ORBIT_SPAN, scale)
    node = orbit_point(NODE_ANGLE, scale)
    shapes = {
        "ring": f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{RING_RADIUS * scale:.1f}" '
                f'fill="none" stroke="#FFFFFF" stroke-width="{RING_WIDTH * scale:.1f}"/>',
        # Sweep flag 0 runs counter-clockwise on screen, as Qt's positive arc angles do.
        "orbit": f'<g {tilted}><path d="M {start[0]:.1f} {start[1]:.1f} '
                 f'A {ORBIT_RX * scale:.1f} {ORBIT_RY * scale:.1f} 0 1 0 '
                 f'{end[0]:.1f} {end[1]:.1f}" fill="none" stroke="#FFFFFF" '
                 f'stroke-width="{ORBIT_WIDTH * scale:.1f}" stroke-linecap="round"/></g>',
        "node": f'<g {tilted}><circle cx="{node[0]:.1f}" cy="{node[1]:.1f}" '
                f'r="{NODE_RADIUS * scale:.1f}" fill="#8CF4FF"/></g>',
    }
    assets = folder / "Assets"
    assets.mkdir(parents=True, exist_ok=True)
    for name, shape in shapes.items():
        (assets / f"{name}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" '
            f'viewBox="0 0 {SIZE} {SIZE}">{shape}</svg>\n', encoding="utf-8")

    def color(code: str) -> str:
        red, green, blue = (int(code[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return f"extended-srgb:{red:.5f},{green:.5f},{blue:.5f},1.00000"

    def group(name: str, **layer) -> dict:
        return {"layers": [{"image-name": f"{name}.svg", "name": name, "glass": True,
                            **layer}],
                "shadow": {"kind": "neutral", "opacity": 0.5},
                "translucency": {"enabled": True, "value": 0.4}}

    document = {
        # macOS draws glass icon backgrounds top to bottom.
        "fill": {"linear-gradient": [color("#7466FF"), color("#24196B")]},
        # Front to back: the node rides on the orbit, which crosses the ring.
        "groups": [group("node"), group("orbit", opacity=0.6), group("ring")],
        "supported-platforms": {"squares": ["macOS"]},
    }
    (folder / "icon.json").write_text(json.dumps(document, indent=2) + "\n",
                                      encoding="utf-8")


def draw_dmg_background(scale: int) -> QImage:
    """The disk image window: a heading, and an arrow from the app to Applications."""
    width = installers.DMG_WINDOW[0]
    height = installers.DMG_WINDOW[1] + installers.DMG_BACKGROUND_SPARE
    image = QImage(width * scale, height * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)  # Draw in points; Retina gets twice the pixels.
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    # Light, so Finder's dark icon labels stay readable.
    wash = QLinearGradient(0, 0, 0, height)
    wash.setColorAt(0.0, QColor("#FBFAFF"))
    wash.setColorAt(1.0, QColor("#ECE9FF"))
    p.fillRect(QRectF(0, 0, width, height), wash)
    heading = QFont()
    heading.setPixelSize(22)
    heading.setWeight(QFont.Weight.DemiBold)
    p.setFont(heading)
    p.setPen(QColor("#1E1950"))
    p.drawText(QRectF(0, 28, width, 32), Qt.AlignmentFlag.AlignHCenter, "Install Orchevian")
    body = QFont()
    body.setPixelSize(13)
    p.setFont(body)
    p.setPen(QColor("#5A5680"))
    p.drawText(QRectF(0, 64, width, 20), Qt.AlignmentFlag.AlignHCenter,
               "Drag Orchevian onto the Applications folder.")
    (app_x, y), (folder_x, _) = installers.DMG_APP_AT, installers.DMG_APPLICATIONS_AT
    gap = installers.DMG_ICON_SIZE / 2 + 30
    start, end = QPointF(app_x + gap, y), QPointF(folder_x - gap, y)
    p.setPen(QPen(QColor("#6B5BFF"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.drawLine(start, end)
    p.drawPolyline([end + QPointF(-15, -15), end, end + QPointF(-15, 15)])
    p.end()
    return image


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
    write_icon_composer(OUT / "orchevian.icon")
    # dmgbuild combines the pair into one Retina-ready background.
    draw_dmg_background(1).save(str(OUT / "dmg-background.png"))
    draw_dmg_background(2).save(str(OUT / "dmg-background@2x.png"))
    print(f"Wrote icons to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
