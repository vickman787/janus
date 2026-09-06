"""Fit the Janus mark geometry analytically.

The mark is constructed from circular arcs, so a polygon trace is the wrong
output. This script measures the raster, fits circles to the outer arc and to
the inner concave arcs, locates the notch vertices, and prints the parameters
needed to rebuild the lune from pure SVG arc commands.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand" / "source" / "janus-mark.jpg"


def masks():
    a = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    sat = a.max(axis=2) - a.min(axis=2)
    lum = a.mean(axis=2)
    cobalt = (b > 120) & (b - r > 40) & (b - g > 40)
    band = np.zeros_like(cobalt)
    band[260:800, :] = True
    cobalt &= band
    img = Image.fromarray((cobalt * 255).astype(np.uint8))
    img = img.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    return np.asarray(img) > 127


def fit_circle(pts: np.ndarray) -> tuple[float, float, float]:
    """Algebraic circle fit. pts is an (n, 2) array of x, y."""
    x = pts[:, 0]
    y = pts[:, 1]
    A = np.column_stack([x, y, np.ones(len(x))])
    bvec = x**2 + y**2
    sol, *_ = np.linalg.lstsq(A, bvec, rcond=None)
    cx = sol[0] / 2.0
    cy = sol[1] / 2.0
    rad = float(np.sqrt(sol[2] + cx**2 + cy**2))
    return float(cx), float(cy), rad


def main() -> int:
    m = masks()
    ys, xs = np.where(m)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    print(f"cobalt bbox x {x0}..{x1} y {y0}..{y1} size {x1-x0+1}x{y1-y0+1}")

    cy_mark = (y0 + y1) / 2.0
    rows = range(int(y0), int(y1) + 1)

    left_edge = []
    right_edge = []
    for y in rows:
        r = np.where(m[y])[0]
        if r.size == 0:
            continue
        left_edge.append((float(r.min()), float(y)))
        right_edge.append((float(r.max()), float(y)))

    left = np.array(left_edge)
    right = np.array(right_edge)

    # Outer arc: the right edge, sampled away from the extreme tips.
    keep = (right[:, 1] > y0 + 12) & (right[:, 1] < y1 - 12)
    ocx, ocy, orad = fit_circle(right[keep])
    resid = np.abs(np.hypot(right[keep][:, 0] - ocx, right[keep][:, 1] - ocy) - orad)
    print(f"OUTER arc  centre ({ocx:.2f}, {ocy:.2f})  r {orad:.2f}  max resid {resid.max():.2f}  mean {resid.mean():.2f}")

    # Inner edge splits at the notch. Find the notch extent: rows where the
    # left edge sits far right of its minimum.
    inner_min = left[:, 0].min()
    notch_rows = left[left[:, 0] > inner_min + 6]
    print(f"inner left edge min x {inner_min:.1f}")
    if len(notch_rows):
        print(f"notch spans y {notch_rows[:,1].min():.0f}..{notch_rows[:,1].max():.0f}"
              f"  deepest x {notch_rows[:,0].max():.1f}")

    ny0 = notch_rows[:, 1].min() if len(notch_rows) else cy_mark
    ny1 = notch_rows[:, 1].max() if len(notch_rows) else cy_mark

    upper = left[(left[:, 1] < ny0 - 2) & (left[:, 1] > y0 + 12)]
    lower = left[(left[:, 1] > ny1 + 2) & (left[:, 1] < y1 - 12)]

    for name, seg in (("INNER upper", upper), ("INNER lower", lower)):
        if len(seg) < 8:
            print(name, "too few points")
            continue
        cx, cy, rad = fit_circle(seg)
        res = np.abs(np.hypot(seg[:, 0] - cx, seg[:, 1] - cy) - rad)
        print(f"{name} arc  centre ({cx:.2f}, {cy:.2f})  r {rad:.2f}  max resid {res.max():.2f}  mean {res.mean():.2f}")

    # Tip coordinates
    top_row = np.where(m[int(y0)])[0]
    bot_row = np.where(m[int(y1)])[0]
    print(f"top row y={y0} x {top_row.min()}..{top_row.max()}")
    print(f"bottom row y={y1} x {bot_row.min()}..{bot_row.max()}")

    # Waist: narrowest horizontal run
    widths = []
    for y in rows:
        r = np.where(m[y])[0]
        if r.size:
            widths.append((r.max() - r.min() + 1, y))
    widths.sort()
    print("narrowest rows", widths[:5])
    print("widest rows", widths[-3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
