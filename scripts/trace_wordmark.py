"""Trace the JANUS wordmark from the supplied raster into outline SVG.

The letterforms are traced rather than matched to a font, so the lockup is exact
and does not depend on any typeface being installed. None of the five glyphs has
an enclosed counter (the A has no crossbar), so no hole handling is needed.

Outputs to janus/static:
  wordmark.svg   JANUS in currentColor, baseline normalised
  lockup.svg     mark plus wordmark, horizontal, for the app header
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trace_mark import close_mask, fill_holes, rdp, to_path, trace  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand" / "source" / "janus-mark.jpg"
STATIC = ROOT / "janus" / "static"
BRAND = ROOT / "brand"

IVORY = "#F1F3F2"
STEEL = "#77808F"
COBALT = "#4D6BFF"

CAP_HEIGHT = 100.0  # wordmark glyphs normalise to this cap height


def components(mask: np.ndarray, min_px: int = 400) -> list[np.ndarray]:
    """Every 4 connected component, ordered left to right."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    found: list[tuple[int, np.ndarray]] = []
    for sy in range(h):
        for sx in np.where(mask[sy] & ~seen[sy])[0]:
            if seen[sy, sx]:
                continue
            q = deque([(sy, int(sx))])
            seen[sy, sx] = True
            cells = []
            while q:
                y, x = q.popleft()
                cells.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            if len(cells) < min_px:
                continue
            comp = np.zeros_like(mask)
            xs = []
            for y, x in cells:
                comp[y, x] = True
                xs.append(x)
            found.append((min(xs), comp))
    found.sort(key=lambda item: item[0])
    return [c for _, c in found]


def main() -> int:
    a = np.asarray(Image.open(SRC).convert("RGB")).astype(int)
    lum = a.mean(axis=2)

    # The wordmark band measured from the row profile of bright pixels.
    band = np.zeros(lum.shape, dtype=bool)
    band[780:895, :] = True
    letters_mask = close_mask((lum > 140) & band)

    glyphs = components(letters_mask)
    print(f"found {len(glyphs)} glyph components")
    if len(glyphs) != 5:
        print("  expected 5 (J A N U S). check the band bounds before trusting output.")

    # Global metrics so all glyphs share one baseline and one scale.
    ys, xs = np.where(letters_mask)
    top, bottom = float(ys.min()), float(ys.max())
    left, right = float(xs.min()), float(xs.max())
    cap_px = bottom - top + 1
    scale = CAP_HEIGHT / cap_px
    out_w = (right - left + 1) * scale
    print(f"wordmark bbox x {left:.0f}..{right:.0f}  y {top:.0f}..{bottom:.0f}"
          f"  cap {cap_px:.0f}px  output {out_w:.1f}x{CAP_HEIGHT:.0f}")

    paths = []
    for i, glyph in enumerate(glyphs):
        filled = fill_holes(glyph)
        ring = trace(filled)
        norm = [((x - left) * scale, (y - top) * scale) for x, y in ring]
        simple = rdp(norm, 0.12)
        if simple[0] == simple[-1]:
            simple = simple[:-1]
        gy, gx = np.where(glyph)
        print(f"  glyph {i}: x {gx.min()}..{gx.max()}  {len(ring)} -> {len(simple)} points")
        paths.append(to_path(simple))

    # Two wordmark files. currentColor does not resolve when an SVG is loaded
    # through an img tag, so the default file carries an explicit ivory fill and
    # the mono file keeps currentColor for inline use.
    def wordmark_svg(fill: str) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {out_w:.2f} {CAP_HEIGHT:.0f}" '
            f'width="{out_w:.0f}" height="{CAP_HEIGHT:.0f}" role="img" aria-label="JANUS">\n'
            f"  <title>JANUS</title>\n"
            + "".join(f'  <path d="{p}" fill="{fill}"/>\n' for p in paths)
            + "</svg>\n"
        )

    for base in (STATIC, BRAND):
        (base / "wordmark.svg").write_text(wordmark_svg(IVORY), encoding="utf-8")
        (base / "wordmark-dark.svg").write_text(wordmark_svg("#0B0D12"), encoding="utf-8")
        (base / "wordmark-mono.svg").write_text(wordmark_svg("currentColor"), encoding="utf-8")

    # Horizontal lockup: mark at left, wordmark optically centred beside it.
    mark_svg = (STATIC / "mark.svg").read_text(encoding="utf-8")
    mark_vb = mark_svg.split('viewBox="')[1].split('"')[0].split()
    mark_w, mark_h = float(mark_vb[2]), float(mark_vb[3])

    lock_h = 100.0
    m_scale = lock_h / mark_h
    m_w = mark_w * m_scale
    gap = lock_h * 0.34
    w_scale = (lock_h * 0.50) / CAP_HEIGHT
    w_w = out_w * w_scale
    total_w = m_w + gap + w_w
    w_y = (lock_h - CAP_HEIGHT * w_scale) / 2.0

    mark_body = "\n".join(
        line for line in mark_svg.splitlines()
        if line.strip().startswith("<path")
    )

    lockup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w:.2f} {lock_h:.0f}" '
        f'width="{total_w:.0f}" height="{lock_h:.0f}" role="img" aria-label="Janus">\n'
        f"  <title>Janus</title>\n"
        f'  <g transform="scale({m_scale:.5f})">\n{mark_body}\n  </g>\n'
        f'  <g transform="translate({m_w + gap:.2f} {w_y:.2f}) scale({w_scale:.5f})" fill="{IVORY}">\n'
        + "".join(f'    <path d="{p}"/>\n' for p in paths)
        + f"  </g>\n</svg>\n"
    )
    (STATIC / "lockup.svg").write_text(lockup, encoding="utf-8")
    (BRAND / "lockup.svg").write_text(lockup, encoding="utf-8")

    print(f"wrote wordmark.svg ({out_w:.0f}x{CAP_HEIGHT:.0f}) and lockup.svg ({total_w:.0f}x{lock_h:.0f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
