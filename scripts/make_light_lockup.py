"""Emit the light background lockup by swapping the wordmark fill."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for base in (ROOT / "brand", ROOT / "janus" / "static"):
    src = base / "lockup.svg"
    if not src.exists():
        continue
    svg = src.read_text(encoding="utf-8")
    light = svg.replace('fill="#F1F3F2"', 'fill="#0B0D12"')
    (base / "lockup-light.svg").write_text(light, encoding="utf-8")
    print("wrote", base / "lockup-light.svg")
