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
R = 304.0 * 2
HEAD_DEG = -45.0
TAIL_SWEEP = 360.0
W_BAND = 150.0 * 2
EDGE = 2.4                # soft-edge half-width in supersampled pixels

# The first icon's colour wheel, saturated a touch: hues spaced evenly
# around the full circle so no side of the ring outweighs another. t runs
# from the thin tip (just ahead of the head) back to the head itself.
STOPS = [
    (0.000, (0x52, 0xC4, 0xC0)),   # teal, in front of the head under the glow
    (0.125, (0x6E, 0xC9, 0x83)),   # green
    (0.250, (0xE8, 0xC8, 0x60)),   # gold
    (0.375, (0xF5, 0xA8, 0x68)),   # apricot
    (0.500, (0xEE, 0x7E, 0x92)),   # rose
    (0.625, (0xD0, 0x7A, 0xC4)),   # orchid
    (0.750, (0x9E, 0x7F, 0xE6)),   # violet
    (0.875, (0x6F, 0x9B, 0xE8)),   # periwinkle
    (1.000, (0x2E, 0xBE, 0xFF)),   # azure into the head (the running colour)
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

# --- ring band ---------------------------------------------------------------
# Constant width around the full circle; where t wraps from 1 back to 0 the
# opacity steps, but that seam sits exactly at the head's angular position
# and the opaque head disc covers it.
dist = np.abs(r - R)
coverage = np.clip((W_BAND / 2 + EDGE - dist) / (2 * EDGE), 0.0, 1.0)
fade = 0.72 + 0.28 * tc ** 1.3
tail_a = np.where(in_tail, coverage * fade, 0.0)

rgb = np.zeros((S, S, 3))
alpha = np.zeros((S, S))


def over(a_new, rgb_new):
    """Composite a straight-alpha layer over the accumulated image."""
    global rgb, alpha
    out_a = a_new + alpha * (1 - a_new)
    safe = np.where(out_a == 0, 1, out_a)
    for ch in range(3):
        rgb[..., ch] = (rgb_new[ch] * a_new
                        + rgb[..., ch] * alpha * (1 - a_new)) / safe
    alpha = out_a


# --- tail --------------------------------------------------------------------
# Colour along the tail from the gradient stops.
tail_rgb = np.zeros((S, S, 3))
for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
    k = np.clip((tc - t0) / (t1 - t0), 0.0, 1.0)
    seg = (tc >= t0) & (tc <= t1) if t1 < 1.0 else (tc >= t0)
    for ch in range(3):
        tail_rgb[..., ch] = np.where(seg, c0[ch] + (c1[ch] - c0[ch]) * k,
                                     tail_rgb[..., ch])

over(tail_a, [tail_rgb[..., 0], tail_rgb[..., 1], tail_rgb[..., 2]])


def disc_at(cx, cy, radius, colour):
    d = np.hypot(xx - cx, yy - cy)
    a = np.clip((radius + EDGE - d) / (2 * EDGE), 0.0, 1.0)
    over(a, [np.full((S, S), c) for c in colour])


# --- head --------------------------------------------------------------------
hx = CX + R * np.cos(np.radians(HEAD_DEG))
hy = CY + R * np.sin(np.radians(HEAD_DEG))
hd = np.hypot(xx - hx, yy - hy)

# Glow: 0 -> 0.95, 0.45 -> 0.55, 1 -> 0 over the glow radius, colour shifting
# from pale to saturated cyan.
gr = 190.0 * 2
k = np.clip(hd / gr, 0.0, 1.0)
glow_a = np.where(k < 0.45, 0.95 + (0.55 - 0.95) * (k / 0.45),
                  0.55 * (1 - (k - 0.45) / 0.55))
glow_a = np.clip(glow_a, 0.0, 1.0)
mix = np.clip(k / 0.45, 0.0, 1.0)
glow_rgb = [np.array(0xBF + (0x59 - 0xBF) * mix),
            np.array(0xEF + (0xC8 - 0xEF) * mix),
            np.full_like(mix, 0xFF)]
over(glow_a, glow_rgb)

disc_at(hx, hy, 86.0 * 2, (0xC4, 0xEF, 0xFF))
disc_at(hx, hy, 58.0 * 2, (0xFF, 0xFF, 0xFF))

# Stay in float through the downsample and dither at the final resolution:
# noise added before averaging gets averaged away again, and the slow angular
# gradient then shows its 8-bit steps as radial bands.
art = np.dstack([rgb, alpha[..., None] * 255])
art = art.reshape(1024, 2, 1024, 2, 4).mean(axis=(1, 3))
rng = np.random.default_rng(20260831)
art = art + (rng.random(art.shape) - rng.random(art.shape)) * 0.75
layer = Image.fromarray(np.clip(art, 0, 255).astype(np.uint8))
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

inner = layer.resize((790, 790), Image.LANCZOS)
card.alpha_composite(inner, ((1024 - 790) // 2, (1024 - 790) // 2))
flat_path = ASSETS / "icon.png"
card.save(flat_path)
print("wrote", flat_path)
