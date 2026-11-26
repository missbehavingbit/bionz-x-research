"""Render the 96x66 "MODS" tab icon and encode its RGBA4444 LE pixels as RLE."""
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

VISIBLE_W, H = 91, 66
W = 96  # Declare complete aligned rows and leave the right edge transparent.
BAND_TOP, BAND_BOTTOM = 40, 62
STATES = {
    "inactive": ((0, 40, 38, 0), (0, 132, 120, 255)),
    "active": ((0, 112, 103, 90), (12, 228, 201, 255)),
    "focused": ((0, 133, 122, 135), (94, 255, 228, 255)),
}
OUT = Path(__file__).resolve().parent


def render(top: tuple[int, ...], bottom: tuple[int, ...]) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(BAND_TOP, BAND_BOTTOM):
        t = (y - BAND_TOP) / (BAND_BOTTOM - BAND_TOP - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        d.line([(0, y), (VISIBLE_W - 1, y)], fill=color)

    label = Image.new("RGBA", img.size)
    d = ImageDraw.Draw(label)
    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 21)
    box = d.textbbox((0, 0), "MODS", font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((VISIBLE_W - tw) // 2 - box[0], 33 - th // 2 - box[1]),
           "MODS", font=font, fill="white")
    return Image.alpha_composite(img, label)


def to4444(img: Image.Image) -> list[int]:
    px = []
    for r, g, b, a in img.getdata():
        q = lambda v: (v * 15 + 127) // 255
        px.append((q(r) << 12) | (q(g) << 8) | (q(b) << 4) | q(a))
    return px


def rle(px: list[tuple[int, ...]]) -> tuple[list[tuple[int, ...]], bytes]:
    """One joint palette index and run stream for all three visual states."""
    pal: list[tuple[int, ...]] = []
    runs = bytearray()
    i = 0
    while i < len(px):
        v = px[i]
        n = 1
        while i + n < len(px) and px[i + n] == v and n < 256:
            n += 1
        if v not in pal:
            pal.append(v)
        runs += bytes((n - 1, pal.index(v)))
        i += n
    if len(pal) > 256:
        raise ValueError("palette overflow")
    return pal, bytes(runs)


def main() -> None:
    images = [render(*colors) for colors in STATES.values()]
    images[0].save(OUT / "mods_tab_icon.png")
    states = [to4444(img) for img in images]
    (OUT / "mods_tab_icon.rgba4444").write_bytes(struct.pack(f"<{W * H}H", *states[0]))
    pal, runs = rle(list(zip(*states, strict=True)))
    palettes = [struct.pack(f"<{len(pal)}H", *colors) for colors in zip(*pal, strict=True)]
    blob = struct.pack("<HH", len(pal), len(runs) // 2) + b"".join(palettes) + runs
    (OUT / "mods_tab_icon.rle").write_bytes(blob)
    print("pixels per state", W * H, "palette", len(pal), "runs", len(runs) // 2, "rle bytes", len(blob))


if __name__ == "__main__":
    sys.exit(main())
