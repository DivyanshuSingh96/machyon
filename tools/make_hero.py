"""Build docs/assets/brand/machyon-hero.svg: the primary lockup recomposed from its parts (Opa symbol + the
seven wordmark glyphs) so the homepage can animate them, with theme-aware classes instead of fixed fills. Animated groups carry no transform
attribute (a CSS transform would replace it), so placement lives on an inner group.
At rest it must equal brand/svg/lockup/machyon-lockup-light.svg; tools/check_hero.py verifies that."""
import json
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit("usage: python tools/make_hero.py BRAND_PROJECT_DIR   (the folder holding assets/lockup_map.json)")
FILM = Path(sys.argv[1])
OUT = Path(__file__).resolve().parents[1] / "docs/assets/brand/machyon-hero.svg"
M = json.loads((FILM / "assets/lockup_map.json").read_text())
L = json.loads((FILM / "assets/letters.json").read_text())
sym = (FILM / "brand/svg/symbol/opa-symbol-light.svg").read_text()
paths = dict(re.findall(r'<path id="(\w+)" fill="#[0-9A-Fa-f]+" fill-rule="evenodd" d="([^"]+)"', sym))
s, tx = M["s"], M["dxw"] - 0.3 * M["hs"] - M["s"] * (M["vx"] + M["vw"])
ty = M["cy"] - M["hs"] / 2 - M["s"] * M["vy"]
vb = " ".join(f"{v:.3f}" for v in M["view"])
glyphs = "".join(f'<path class="mx-l" style="--i:{k}" d="{g["d"]}"/>' for k, g in enumerate(L["glyphs"]))
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" class="mx-hero-mark" viewBox="{vb}" role="img" aria-label="Machyon">'
       f'<g class="mx-opa"><g transform="translate({tx:.4f},{ty:.4f}) scale({s:.6f})">'
       f'<path class="mx-ink" fill-rule="evenodd" d="{paths["body"]}"/>'
       f'<path class="mx-amber mx-claw" fill-rule="evenodd" d="{paths["claw"]}"/>'
       f'<g class="mx-eyes"><path class="mx-amber" fill-rule="evenodd" d="{paths["eyeball"]}"/>'
       f'<path class="mx-pupil" fill-rule="evenodd" d="{paths["pupil"]}"/></g></g></g>'
       f'<g class="mx-word mx-ink" transform="translate({M["dxw"]:.4f},0)">{glyphs}</g></svg>')
OUT.write_text(svg)
print(OUT, len(svg), "bytes; symbol parts:", sorted(paths))
