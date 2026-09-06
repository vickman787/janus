"""Trace the Janus mark from the supplied raster into exact SVG assets.

The source art is symmetric on both axes, so the cobalt (right) lune is traced
once, made symmetric top to bottom, then mirrored to produce the steel (left)
lune. That way the two halves match exactly instead of inheriting JPEG noise.

Outputs, all written to janus/static:
  mark.svg           flat, steel + cobalt, the product mark
  mark-gradient.svg  chrome gradient on the steel lune, for hero art
  mark-mono.svg      single colour, currentColor, for monochrome use
  favicon.svg        the mark on a rounded Midnight tile
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand" / "source" / "janus-mark.jpg"
OUT_DIR = ROOT / "brand" / "out"
STATIC = ROOT / "janus" / "static"

MIDNIGHT = "#0B0D12"
IVORY = "#F1F3F2"
COBALT = "#4D6BFF"
STEEL = "#77808F"

# Target height of the mark in user units. Width follows the measured aspect.
TARGET_H = 200.0


# ------------------------------------------------------------------ masks
def segment() -> tuple[np.ndarray, np.ndarray]:
    """Tight colour segmentation. Loose thresholds pick up the antialias halo
    around the cobalt lune and misfile it as steel, so the bounds are strict."""
    a = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    sat = a.max(axis=2) - a.min(axis=2)
    lum = a.mean(axis=2)
    cobalt = (b > 140) & (b - r > 50) & (b - g > 50)
    steel = (lum > 55) & (lum < 210) & (sat < 50) & ~cobalt
    band = np.zeros_like(cobalt)
    band[260:800, :] = True  # exclude the wordmark and tagline
    return steel & band, cobalt & band


def close_mask(mask: np.ndarray) -> np.ndarray:
    img = Image.fromarray((mask * 255).astype(np.uint8))
    img = img.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    return np.asarray(img) > 127


def largest_component(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    seen = np.zeros_like(mask)
    best: list[tuple[int, int]] = []
    for sy in range(h):
        row = np.where(mask[sy] & ~seen[sy])[0]
        for sx in row:
            if seen[sy, sx]:
                continue
            q = deque([(sy, int(sx))])
            seen[sy, sx] = True
            comp = []
            while q:
                y, x = q.popleft()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            if len(comp) > len(best):
                best = comp
    out = np.zeros_like(mask)
    for y, x in best:
        out[y, x] = True
    return out


def fill_holes(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    bg = ~mask
    reach = np.zeros_like(mask)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not reach[ny, nx]:
                reach[ny, nx] = True
                q.append((ny, nx))
    return mask | (bg & ~reach)


# -------------------------------------------------------- boundary tracing
_N8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def trace(mask: np.ndarray) -> list[tuple[float, float]]:
    """Moore neighbour boundary trace. Returns an ordered closed ring of x, y."""
    h, w = mask.shape
    start = None
    for y in range(h):
        xs = np.where(mask[y])[0]
        if xs.size:
            start = (y, int(xs[0]))
            break
    if start is None:
        raise ValueError("empty mask")

    ring = [start]
    cur = start
    back = (start[0], start[1] - 1)
    while True:
        idx = _N8.index((back[0] - cur[0], back[1] - cur[1]))
        moved = False
        for step in range(1, 9):
            dy, dx = _N8[(idx + step) % 8]
            ny, nx = cur[0] + dy, cur[1] + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx]:
                pdy, pdx = _N8[(idx + step - 1) % 8]
                back = (cur[0] + pdy, cur[1] + pdx)
                cur = (ny, nx)
                moved = True
                break
        if not moved:
            break
        if cur == start and len(ring) > 2:
            break
        ring.append(cur)
        if len(ring) > 400_000:
            break
    return [(float(x), float(y)) for y, x in ring]


# ------------------------------------------------------ simplify and smooth
def rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return list(points)
    ax, ay = points[0]
    bx, by = points[-1]
    dx, dy = bx - ax, by - ay
    norm = (dx * dx + dy * dy) ** 0.5
    worst, index = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        if norm == 0:
            dist = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        else:
            dist = abs(dy * (px - ax) - dx * (py - ay)) / norm
        if dist > worst:
            worst, index = dist, i
    if worst <= eps:
        return [points[0], points[-1]]
    return rdp(points[: index + 1], eps)[:-1] + rdp(points[index:], eps)


def resample(points: list[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    pts = points + [points[0]]
    seg, total = [], 0.0
    for i in range(len(pts) - 1):
        d = ((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5
        seg.append(d)
        total += d
    out, target, acc, i = [], 0.0, 0.0, 0
    step = total / count
    for _ in range(count):
        while i < len(seg) and acc + seg[i] < target:
            acc += seg[i]
            i += 1
        if i >= len(seg):
            out.append(pts[-1])
            continue
        t = 0.0 if seg[i] == 0 else (target - acc) / seg[i]
        out.append((
            pts[i][0] + t * (pts[i + 1][0] - pts[i][0]),
            pts[i][1] + t * (pts[i + 1][1] - pts[i][1]),
        ))
        target += step
    return out


def polygon_area(points: list[tuple[float, float]]) -> float:
    """Absolute shoelace area. Used as a guard: any step that changes the area
    materially has corrupted the shape."""
    total = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def make_vertically_symmetric(ring: list[tuple[float, float]], axis_y: float) -> list[tuple[float, float]]:
    """Average the ring with its own reflection through the horizontal axis.

    Reflection reverses winding order, so the mirrored ring must be reversed
    before alignment. Without the reversal the correspondence search pairs
    outer edge points against inner edge points and the shape collapses to a
    hairline. The area guard below makes that failure loud instead of silent.
    """
    dense = resample(ring, 480)
    mirror = [(x, 2 * axis_y - y) for x, y in reversed(dense)]

    best_shift, best_cost = 0, float("inf")
    for shift in range(len(mirror)):
        cost = 0.0
        for i in range(0, len(dense), 16):
            ax, ay = dense[i]
            bx, by = mirror[(i + shift) % len(mirror)]
            cost += (ax - bx) ** 2 + (ay - by) ** 2
        if cost < best_cost:
            best_cost, best_shift = cost, shift

    merged = [
        ((ax + mirror[(i + best_shift) % len(mirror)][0]) / 2.0,
         (ay + mirror[(i + best_shift) % len(mirror)][1]) / 2.0)
        for i, (ax, ay) in enumerate(dense)
    ]

    before = polygon_area(dense)
    after = polygon_area(merged)
    if before == 0 or abs(after - before) / before > 0.04:
        print(f"  symmetry rejected: area {before:.1f} -> {after:.1f}, keeping raw trace")
        return dense
    print(f"  symmetry applied: area {before:.1f} -> {after:.1f}")
    return merged


def to_path(points: list[tuple[float, float]], nd: int = 2) -> str:
    parts = [f"M{round(points[0][0], nd)} {round(points[0][1], nd)}"]
    for x, y in points[1:]:
        parts.append(f"L{round(x, nd)} {round(y, nd)}")
    parts.append("Z")
    return "".join(parts)


# ----------------------------------------------------------------- writing
def svg_flat(w: float, right: str, left: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {TARGET_H:.0f}" '
        f'width="{w:.0f}" height="{TARGET_H:.0f}" role="img" aria-label="Janus">\n'
        f"  <title>Janus</title>\n"
        f'  <path d="{left}" fill="{STEEL}"/>\n'
        f'  <path d="{right}" fill="{COBALT}"/>\n'
        f"</svg>\n"
    )


def svg_gradient(w: float, right: str, left: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {TARGET_H:.0f}" '
        f'width="{w:.0f}" height="{TARGET_H:.0f}" role="img" aria-label="Janus">\n'
        f"  <title>Janus</title>\n"
        f"  <defs>\n"
        f'    <linearGradient id="janusSteel" x1="0.12" y1="0" x2="0.88" y2="1">\n'
        f'      <stop offset="0" stop-color="{IVORY}"/>\n'
        f'      <stop offset="0.42" stop-color="#B9C0CA"/>\n'
        f'      <stop offset="1" stop-color="#5B6373"/>\n'
        f"    </linearGradient>\n"
        f"  </defs>\n"
        f'  <path d="{left}" fill="url(#janusSteel)"/>\n'
        f'  <path d="{right}" fill="{COBALT}"/>\n'
        f"</svg>\n"
    )


def svg_mono(w: float, right: str, left: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.2f} {TARGET_H:.0f}" '
        f'width="{w:.0f}" height="{TARGET_H:.0f}" role="img" aria-label="Janus">\n'
        f"  <title>Janus</title>\n"
        f'  <path d="{left}" fill="currentColor"/>\n'
        f'  <path d="{right}" fill="currentColor"/>\n'
        f"</svg>\n"
    )


def svg_favicon(w: float, right: str, left: str) -> str:
    tile = 256.0
    pad = 0.19
    scale = (tile * (1 - 2 * pad)) / TARGET_H
    tx = (tile - w * scale) / 2.0
    ty = (tile - TARGET_H * scale) / 2.0
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {tile:.0f} {tile:.0f}" '
        f'width="{tile:.0f}" height="{tile:.0f}" role="img" aria-label="Janus">\n'
        f"  <title>Janus</title>\n"
        f'  <rect width="{tile:.0f}" height="{tile:.0f}" rx="56" fill="{MIDNIGHT}"/>\n'
        f'  <g transform="translate({tx:.2f} {ty:.2f}) scale({scale:.5f})">\n'
        f'    <path d="{left}" fill="{STEEL}"/>\n'
        f'    <path d="{right}" fill="{COBALT}"/>\n'
        f"  </g>\n"
        f"</svg>\n"
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    steel_m, cobalt_m = segment()

    cobalt = fill_holes(largest_component(close_mask(cobalt_m)))
    steel = fill_holes(largest_component(close_mask(steel_m)))

    cys, cxs = np.where(cobalt)
    sys_, sxs = np.where(steel)
    x0 = float(min(cxs.min(), sxs.min()))
    x1 = float(max(cxs.max(), sxs.max()))
    y0 = float(min(cys.min(), sys_.min()))
    y1 = float(max(cys.max(), sys_.max()))
    w_px, h_px = x1 - x0 + 1, y1 - y0 + 1
    aspect = w_px / h_px
    print(f"mark bbox x {x0:.0f}..{x1:.0f}  y {y0:.0f}..{y1:.0f}  size {w_px:.0f}x{h_px:.0f}  aspect {aspect:.4f}")
    print(f"steel  x {sxs.min()}..{sxs.max()}   cobalt x {cxs.min()}..{cxs.max()}"
          f"   channel {cxs.min() - sxs.max() - 1} px")

    scale = TARGET_H / h_px
    out_w = w_px * scale
    axis_x = out_w / 2.0
    axis_y = TARGET_H / 2.0

    ring = trace(cobalt)
    print("raw contour points", len(ring))
    norm = [((x - x0) * scale, (y - y0) * scale) for x, y in ring]
    sym = make_vertically_symmetric(norm, axis_y)
    simplified = rdp(sym, 0.10)
    if simplified[0] == simplified[-1]:
        simplified = simplified[:-1]
    print("output points", len(simplified))

    right_path = to_path(simplified)
    left_path = to_path([(2 * axis_x - x, y) for x, y in reversed(simplified)])

    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "mark.svg").write_text(svg_flat(out_w, right_path, left_path), encoding="utf-8")
    (STATIC / "mark-gradient.svg").write_text(svg_gradient(out_w, right_path, left_path), encoding="utf-8")
    (STATIC / "mark-mono.svg").write_text(svg_mono(out_w, right_path, left_path), encoding="utf-8")
    (STATIC / "favicon.svg").write_text(svg_favicon(out_w, right_path, left_path), encoding="utf-8")

    brand_copy = ROOT / "brand"
    for name in ("mark.svg", "mark-gradient.svg", "mark-mono.svg", "favicon.svg"):
        (brand_copy / name).write_text((STATIC / name).read_text(encoding="utf-8"), encoding="utf-8")

    meta = {
        "source": SRC.name,
        "bbox_px": {"x0": x0, "x1": x1, "y0": y0, "y1": y1},
        "size_px": [w_px, h_px],
        "aspect": round(aspect, 4),
        "viewBox": f"0 0 {out_w:.2f} {TARGET_H:.0f}",
        "points": len(simplified),
        "channel_px": int(cxs.min() - sxs.max() - 1),
    }
    (OUT_DIR / "mark_trace.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"viewBox 0 0 {out_w:.2f} {TARGET_H:.0f}")
    print("wrote mark.svg, mark-gradient.svg, mark-mono.svg, favicon.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
