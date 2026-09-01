#!/usr/bin/env python3
"""Build docs/states.gif from the five state photos. Needs numpy and Pillow.

Input: assets/states/{running,permission,failure,completed,voice}.jpg
Output: docs/states.gif -- a labelled slideshow cycling through the five
states with cross-fades, aligned on the keyboard so the loop does not jitter.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent
OUT = ASSETS.parent / "docs" / "states.gif"

STATES = [
    ("running", "执行中", (0, 168, 255)),
    ("permission", "等待权限", (255, 176, 0)),
    ("failure", "工具失败", (255, 32, 32)),
    ("completed", "全部完成", (0, 224, 96)),
    ("voice", "语音输入", (168, 85, 247)),
]

WIDTH = 960            # README-friendly output width
ASPECT = 2.35          # fixed crop aspect so every frame lands the same size
MARGIN = 0.07          # extra room around the keyboard for the glow
STILL_MS, FADE_STEPS, FADE_MS = 1500, 4, 80


def keyboard_box(im):
    """Bounding box of the dark keyboard body, in full-resolution pixels.

    Rows and columns count only when at least 15% of their pixels are dark:
    a raw min/max over dark pixels latches onto scattered shadows and edge
    vignetting and blows the box up to the whole photo.
    """
    small = im.convert("L").resize((480, 270))
    mask = np.asarray(small) < 70
    rows = np.nonzero(mask.sum(axis=1) > 480 * 0.15)[0]
    cols = np.nonzero(mask.sum(axis=0) > 270 * 0.15)[0]
    sx, sy = im.width / 480, im.height / 270
    return cols.min() * sx, rows.min() * sy, (cols.max() + 1) * sx, (rows.max() + 1) * sy


def crop_aligned(im):
    """Crop to ASPECT around the keyboard centre, with MARGIN of breathing room."""
    x0, y0, x1, y1 = keyboard_box(im)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = (x1 - x0) * (1 + 2 * MARGIN)
    h = max((y1 - y0) * (1 + 2 * MARGIN), w / ASPECT)
    w = h * ASPECT
    box = (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2))
    return im.crop(box).resize((WIDTH, int(WIDTH / ASPECT)), Image.LANCZOS)


def load_font(size):
    for path in ("/System/Library/Fonts/PingFang.ttc",
                 "/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "/System/Library/Fonts/STHeiti Light.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def labelled(frame, text, colour):
    """A caption chip in the top-left: colour dot plus the state name."""
    im = frame.convert("RGBA")
    chip = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(chip)
    font = load_font(26)
    tw = draw.textlength(text, font=font)
    x, y, pad, dot = 18, 16, 14, 12
    w, h = int(tw + dot + pad * 3), 46
    draw.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=(20, 20, 24, 200))
    draw.ellipse([x + pad, y + h / 2 - dot / 2, x + pad + dot, y + h / 2 + dot / 2],
                 fill=colour + (255,))
    draw.text((x + pad + dot + pad * 0.7, y + h / 2), text, font=font,
              fill=(255, 255, 255, 240), anchor="lm")
    return Image.alpha_composite(im, chip).convert("RGB")


def main():
    stills = []
    for name, label, colour in STATES:
        im = Image.open(ASSETS / "states" / f"{name}.jpg")
        stills.append(labelled(crop_aligned(im), label, colour))

    frames, durations = [], []
    for i, still in enumerate(stills):
        frames.append(still)
        durations.append(STILL_MS)
        following = stills[(i + 1) % len(stills)]
        for step in range(1, FADE_STEPS + 1):
            frames.append(Image.blend(still, following, step / (FADE_STEPS + 1)))
            durations.append(FADE_MS)

    frames[0].save(OUT, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
