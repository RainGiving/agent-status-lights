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
TAIL_SWEEP = 300.0
W_HEAD, W_TAIL = 148.0 * 2, 30.0 * 2
EDGE = 2.4                # soft-edge half-width in supersampled pixels

STOPS = [
    (0.00, (0xC9, 0x43, 0xDE)),   # magenta, fades out
    (0.28, (0x8B, 0x53, 0xF7)),   # violet
    (0.52, (0x47, 0x6B, 0xFF)),   # blue
    (0.82, (0x00, 0xA8, 0xFF)),   # azure (the app's running colour)
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

TRACK_W = 18.0 * 2

# --- tail alpha, computed first so the track can yield to it -----------------
# Over the last tenth the band narrows into the track and fades to nothing,
# so the tip merges instead of ending in a squared-off cut.
width = W_TAIL + (W_HEAD - W_TAIL) * tc ** 1.4
k_tip = np.clip(tc / 0.10, 0.0, 1.0)
k_tip = k_tip * k_tip * (3 - 2 * k_tip)
width = TRACK_W + (width - TRACK_W) * k_tip
dist = np.abs(r - R)
coverage = np.clip((width / 2 + EDGE - dist) / (2 * EDGE), 0.0, 1.0)
fade = (0.13 + 0.87 * tc ** 1.3) * k_tip ** 0.7
tail_a = np.where(in_tail, coverage * fade, 0.0)

# --- orbit track: a faint complete ring under the comet ----------------------
# It closes the circle visually where the tail has faded out, which is what
# keeps the icon from reading as a lone crescent. Where the tail actually
# covers it the track must vanish, or its extra alpha shows as a darker
# stripe running inside the band.
track_a = np.clip((TRACK_W / 2 + EDGE - dist) / (2 * EDGE), 0.0, 1.0) * 0.30
track_a = track_a * np.clip(1.0 - tail_a / 0.22, 0.0, 1.0)
track_rgb = np.zeros((S, S, 3))
track_rgb[..., 0] = 0xAA
track_rgb[..., 1] = 0xC2
track_rgb[..., 2] = 0xF2

rgb = track_rgb.copy()
alpha = track_a.copy()


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


def colour_at(t):
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            k = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * k for a, b in zip(c0, c1))
    return STOPS[-1][1]


def angle(t):
    return HEAD_DEG - TAIL_SWEEP * (1 - t)


def disc_at(cx, cy, radius, colour, a_max=1.0):
    d = np.hypot(xx - cx, yy - cy)
    a = np.clip((radius + EDGE - d) / (2 * EDGE), 0.0, 1.0) * a_max
    over(a, [np.full((S, S), c) for c in colour])


# --- sparkles in the wake ----------------------------------------------------
# (position along the tail, radial offset from the ring, radius, opacity).
# Colours follow the tail at that point, lightened toward white.
SPARKS = [
    (0.93,  102.0, 14.0, 0.95),
    (0.85,  -96.0, 10.0, 0.80),
    (0.76,  112.0,  8.0, 0.65),
    (0.66,  -88.0,  6.5, 0.50),
]
for st, off, rad, a_max in SPARKS:
    ang = np.radians(angle(st))
    scx = CX + (R + off * 2) * np.cos(ang)
    scy = CY + (R + off * 2) * np.sin(ang)
    c = tuple(v + (255 - v) * 0.25 for v in colour_at(st))
    disc_at(scx, scy, rad * 2, c, a_max)

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

inner = layer.resize((790, 790), Image.LANCZOS)
card.alpha_composite(inner, ((1024 - 790) // 2, (1024 - 790) // 2))
flat_path = ASSETS / "icon.png"
card.save(flat_path)
print("wrote", flat_path)
