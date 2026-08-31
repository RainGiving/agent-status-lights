#!/usr/bin/env python3
"""Render the comet-orbit icon artwork. Needs numpy and Pillow.

Outputs:
  assets/HALO.icon/Assets/comet.png  -- transparent artwork layer for the
                                        liquid-glass icon (glass, specular and
                                        the white card come from icon.json)
  assets/icon.png                    -- flat fallback: white rounded card with
                                        the same artwork, for machines without
                                        actool
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent

S = 2048                  # supersampled square, downsampled 2x at the end
CX = CY = S / 2
R = 298.0 * 2
HEAD_DEG = -45.0
TAIL_SWEEP = 300.0
W_HEAD, W_TAIL = 128.0 * 2, 8.0 * 2
EDGE = 2.4                # soft-edge half-width in supersampled pixels

STOPS = [
    (0.00, (0xC3, 0x4C, 0xD6)),   # magenta, fades out
    (0.30, (0x8A, 0x5C, 0xF6)),   # violet
    (0.55, (0x4C, 0x6F, 0xFF)),   # blue
    (0.85, (0x00, 0xA8, 0xFF)),   # azure (the app's running colour)
    (1.00, (0x2E, 0xBE, 0xFF)),   # sky blue into the head
]

yy, xx = np.mgrid[0:S, 0:S].astype(np.float64)
dx, dy = xx - CX, yy - CY
r = np.hypot(dx, dy)
theta = np.degrees(np.arctan2(dy, dx))          # y grows downward, like SVG

# Position along the tail: t = 1 at the head, 0 at the tip.
delta = np.mod(HEAD_DEG - theta, 360.0)
t = 1.0 - delta / TAIL_SWEEP
in_tail = delta <= TAIL_SWEEP
tc = np.clip(t, 0.0, 1.0)

width = W_TAIL + (W_HEAD - W_TAIL) * tc ** 1.5
dist = np.abs(r - R)
coverage = np.clip((width / 2 + EDGE - dist) / (2 * EDGE), 0.0, 1.0)
fade = 0.06 + 0.94 * tc ** 1.5
tail_a = np.where(in_tail, coverage * fade, 0.0)

# Colour along the tail from the gradient stops.
tail_rgb = np.zeros((S, S, 3))
for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
    k = np.clip((tc - t0) / (t1 - t0), 0.0, 1.0)
    seg = (tc >= t0) & (tc <= t1) if t1 < 1.0 else (tc >= t0)
    for ch in range(3):
        tail_rgb[..., ch] = np.where(seg, c0[ch] + (c1[ch] - c0[ch]) * k,
                                     tail_rgb[..., ch])

rgb = tail_rgb.copy()
alpha = tail_a.copy()


def over(a_new, rgb_new):
    """Composite a premultiplied-style layer over the accumulated image."""
    global rgb, alpha
    out_a = a_new + alpha * (1 - a_new)
    safe = np.where(out_a == 0, 1, out_a)
    for ch in range(3):
        rgb[..., ch] = (rgb_new[ch] * a_new
                        + rgb[..., ch] * alpha * (1 - a_new)) / safe
    alpha = out_a


hx = CX + R * np.cos(np.radians(HEAD_DEG))
hy = CY + R * np.sin(np.radians(HEAD_DEG))
hd = np.hypot(xx - hx, yy - hy)

# Glow: 0 -> 0.95, 0.45 -> 0.5, 1 -> 0 over radius 160, colour shifting
# from pale to saturated cyan.
gr = 160.0 * 2
k = np.clip(hd / gr, 0.0, 1.0)
glow_a = np.where(k < 0.45, 0.95 + (0.5 - 0.95) * (k / 0.45),
                  0.5 * (1 - (k - 0.45) / 0.55))
glow_a = np.clip(glow_a, 0.0, 1.0)
mix = np.clip(k / 0.45, 0.0, 1.0)
glow_rgb = [np.array(0xBF + (0x59 - 0xBF) * mix),
            np.array(0xEF + (0xC8 - 0xEF) * mix),
            np.full_like(mix, 0xFF)]
over(glow_a, glow_rgb)


def disc(radius, colour):
    a = np.clip((radius * 2 + EDGE - hd) / (2 * EDGE), 0.0, 1.0)
    over(a, [np.full((S, S), c) for c in colour])


disc(74.0, (0xCD, 0xF2, 0xFF))
disc(50.0, (0xFF, 0xFF, 0xFF))

art = np.dstack([rgb, alpha[..., None] * 255]).astype(np.uint8)
layer = Image.fromarray(art).resize((1024, 1024), Image.LANCZOS)
layer_path = ASSETS / "HALO.icon" / "Assets" / "comet.png"
layer.save(layer_path)
print("wrote", layer_path)

# --- flat fallback: white rounded card on Apple's grid -----------------------
CARD, RADIUS = 824, 185
big = Image.new("RGBA", (4096, 4096), (0, 0, 0, 0))
draw = ImageDraw.Draw(big)
margin = (4096 - CARD * 4) // 2
draw.rounded_rectangle([margin, margin, 4096 - margin, 4096 - margin],
                       radius=RADIUS * 4, fill=(255, 255, 255, 255))
card = big.resize((1024, 1024), Image.LANCZOS)

inner = layer.resize((760, 760), Image.LANCZOS)
card.alpha_composite(inner, ((1024 - 760) // 2, (1024 - 760) // 2))
flat_path = ASSETS / "icon.png"
card.save(flat_path)
print("wrote", flat_path)
