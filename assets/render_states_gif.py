#!/usr/bin/env python3
"""Build docs/states.gif from the five state clips. Needs numpy, Pillow, ffmpeg.

Input: assets/states/{running,permission,failure,completed,voice}.mp4 --
short clips cut out of the phone's motion photos (the animations really move:
the comet orbits, the amber ring breathes, the red one strobes).
Output: docs/states.gif -- a labelled loop through the five states, aligned
on the keyboard so the cuts do not jitter.
"""
import subprocess
import tempfile
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

WIDTH = 840            # README-friendly output width
ASPECT = 2.35          # fixed crop aspect so every frame lands the same size
MARGIN = 0.07          # extra room around the keyboard for the glow
FPS = 10
SECONDS = 1.6          # of motion shown per state
FADE_FRAMES = 2        # cross-fade frames between states


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


def crop_box(im):
    """Crop window at ASPECT around the keyboard centre, MARGIN of room."""
    x0, y0, x1, y1 = keyboard_box(im)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = (x1 - x0) * (1 + 2 * MARGIN)
    h = max((y1 - y0) * (1 + 2 * MARGIN), w / ASPECT)
    w = h * ASPECT
    return (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2))


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
    font = load_font(24)
    tw = draw.textlength(text, font=font)
    x, y, pad, dot = 16, 14, 13, 11
    w, h = int(tw + dot + pad * 3), 42
    draw.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=(20, 20, 24, 200))
    draw.ellipse([x + pad, y + h / 2 - dot / 2, x + pad + dot, y + h / 2 + dot / 2],
                 fill=colour + (255,))
    draw.text((x + pad + dot + pad * 0.7, y + h / 2), text, font=font,
              fill=(255, 255, 255, 240), anchor="lm")
    return Image.alpha_composite(im, chip).convert("RGB")


def decode(clip, tmp):
    """All frames of a clip at FPS, decoded to 1280-wide PNGs."""
    frame_dir = tmp / clip.stem
    frame_dir.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip),
                    "-vf", f"fps={FPS},hqdn3d=3:2:6:4,scale=1280:-2",
                    str(frame_dir / "%03d.png")], check=True)
    return [Image.open(p).convert("RGB") for p in sorted(frame_dir.iterdir())]


def liveliest_window(frames, count):
    """Start index of the `count`-frame window with the most motion, so a
    clip's shaky lead-in and any still tail are skipped automatically."""
    diffs = []
    for a, b in zip(frames, frames[1:]):
        pa = np.asarray(a.resize((160, 90)), dtype=np.int16)
        pb = np.asarray(b.resize((160, 90)), dtype=np.int16)
        diffs.append(np.abs(pa - pb).mean())
    if len(diffs) < count:
        return 0
    scores = [sum(diffs[i:i + count]) for i in range(len(diffs) - count + 1)]
    return int(np.argmax(scores))


def main():
    per_state = int(FPS * SECONDS)
    sequences = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for name, label, colour in STATES:
            frames = decode(ASSETS / "states" / f"{name}.mp4", tmp)
            start = liveliest_window(frames, per_state + 1)
            box = crop_box(frames[start + per_state // 2])
            size = (WIDTH, int(WIDTH / ASPECT))
            picked = [f.crop(box).resize(size, Image.LANCZOS)
                      for f in frames[start:start + per_state]]
            sequences.append([labelled(f, label, colour) for f in picked])

        frames = []
        for i, seq in enumerate(sequences):
            frames.extend(seq)
            following = sequences[(i + 1) % len(sequences)][0]
            for step in range(1, FADE_FRAMES + 1):
                frames.append(Image.blend(seq[-1], following,
                                          step / (FADE_FRAMES + 1)))

        out_dir = tmp / "out"
        out_dir.mkdir()
        for i, frame in enumerate(frames):
            frame.save(out_dir / f"{i:04d}.png")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS),
             "-i", str(out_dir / "%04d.png"), "-filter_complex",
             "[0:v]split[a][b];[a]palettegen=max_colors=160[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
             "-loop", "0", str(OUT)], check=True)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
