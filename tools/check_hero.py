"""The recomposed hero must equal the approved primary lockup: render both with librsvg and compare pixels."""
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
hero = (ROOT / "docs/assets/brand/machyon-hero.svg").read_text()
static = hero.replace('class="mx-ink"', 'fill="#2A3142"').replace('class="mx-word mx-ink"', 'fill="#2A3142"')
static = re.sub(r'class="mx-amber[^"]*"', 'fill="#F5B83D"', static).replace('class="mx-pupil"', 'fill="#2A3142"')
Path("/tmp/hero_static.svg").write_text(static)
W = 2400
for src, out in (("/tmp/hero_static.svg", "/tmp/hero.png"), (str(ROOT / "docs/assets/brand/machyon-lockup-light.svg"), "/tmp/lockup.png")):
    subprocess.run(["rsvg-convert", "-w", str(W), "-b", "#F4F1EA", src, "-o", out], check=True)
a = np.asarray(Image.open("/tmp/hero.png").convert("RGB")).astype(int)
b = np.asarray(Image.open("/tmp/lockup.png").convert("RGB")).astype(int)
if a.shape != b.shape:
    sys.exit(f"FAIL size {a.shape} vs {b.shape}")
d = np.abs(a - b).max(axis=2)
strong = (d > 64).mean() * 100
print(f"hero vs approved lockup at {W}px: {strong:.4f}% pixels differ strongly, mean |diff| {d.mean():.3f}/255")
sys.exit(0 if strong < 0.05 else 1)
