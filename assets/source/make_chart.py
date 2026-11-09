"""Benchmark chart for the README (light + dark SVG), in the docs palette (Sky on Honey paper / Aurora on brand slate) with Machyon in the
logo's slate. Data: benchmarks/results-2026-11-07.jsonl (byte-verified runs)."""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RESULTS = os.path.join(OUT, "..", "benchmarks", "results-2026-11-07.jsonl")
rows = [json.loads(line) for line in open(RESULTS) if line.strip().startswith("{")]
get = {(r["variant"], r["scenario"]): r for r in rows}

SCEN = [("clean", "Clean network"), ("realistic", "Real-world faults"), ("ratelimited", "Rate-limited CDN"),
        ("bandwidth", "Slow link (2.3 MB/s)"), ("highlatency", "High latency (350 ms)")]
THEMES = {
    "light": dict(bg="#FFFBF0", ink="#2B3C52", ink2="#4F637D", muted="#4F637D", grid="#EFDDB0",
                  a="#2A3142", b="#8DBDEB"),
    "dark": dict(bg="#2A3142", ink="#ECF1FF", ink2="#AEB9DD", muted="#AEB9DD", grid="#454F6B",
                 a="#ECF1FF", b="#8EA2FF"),
}
W, LEFT, RIGHT, TOP, GROUP, BAR, GAP = 900, 190, 210, 108, 62, 16, 2
VMAX = 900


def bar(x, y, w, h, color, r=4):
    w = max(w, 2)
    r = min(r, w, h / 2)
    return (f'<path d="M{x} {y} H{x + w - r} A{r} {r} 0 0 1 {x + w} {y + r} V{y + h - r} '
            f'A{r} {r} 0 0 1 {x + w - r} {y + h} H{x} Z" fill="{color}"/>')


def chart(t):
    H = TOP + GROUP * len(SCEN) + 46
    plot_w = W - LEFT - RIGHT
    sx = lambda v: LEFT + plot_w * v / VMAX
    font = 'font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif"'
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" {font}>',
         f'<rect width="{W}" height="{H}" rx="12" fill="{t["bg"]}"/>',
         f'<text x="24" y="38" font-size="20" font-weight="700" fill="{t["ink"]}">Images per second, every file verified byte-for-byte</text>',
         f'<text x="24" y="62" font-size="13" fill="{t["ink2"]}">Higher is better. Same fault-injecting CDN, same machine (2 vCPU), default settings for both tools.</text>']
    # legend
    lx = 24
    for color, label in ((t["a"], "Machyon 1.0"), (t["b"], "img2dataset 1.47 (defaults)")):
        s.append(f'<rect x="{lx}" y="76" width="12" height="12" rx="3" fill="{color}"/>')
        s.append(f'<text x="{lx + 18}" y="86.5" font-size="13" fill="{t["ink2"]}">{label}</text>')
        lx += 18 + len(label) * 7.2 + 24
    # grid
    for v in range(0, VMAX + 1, 200):
        x = sx(v)
        s.append(f'<line x1="{x}" y1="{TOP - 4}" x2="{x}" y2="{TOP + GROUP * len(SCEN) - 8}" stroke="{t["grid"]}" stroke-width="1"/>')
        s.append(f'<text x="{x}" y="{TOP + GROUP * len(SCEN) + 10}" font-size="11" fill="{t["muted"]}" text-anchor="middle">{v}</text>')
    for i, (key, label) in enumerate(SCEN):
        y0 = TOP + i * GROUP
        m, d = get[("machyon", key)], get[("i2d-default", key)]
        s.append(f'<text x="{LEFT - 14}" y="{y0 + BAR + 5}" font-size="14" fill="{t["ink"]}" text-anchor="end">{label}</text>')
        for j, (r, color) in enumerate(((m, t["a"]), (d, t["b"]))):
            y = y0 + j * (BAR + GAP)
            v = r["img_per_s"]
            s.append(f'<g><title>{label} - {"Machyon" if j == 0 else "img2dataset"}: {v:g} img/s, '
                     f'{r["correct"]}/{r["healthy"]} files correct</title>{bar(sx(0), y, sx(v) - sx(0), BAR, color)}</g>')
            lost = r["healthy"] - r["correct"]
            txt = f"{v:,.0f}" if v >= 10 else f"{v:g}"
            issues = ([f'{lost:,} of {r["healthy"]:,} lost'] if lost else []) + \
                     ([f'{r["bogus"]} HTML pages saved as images'] if r["bogus"] else [])
            note = "· " + " · ".join(issues) if issues else ""
            weight = "700" if j == 0 else "400"
            s.append(f'<text x="{sx(v) + 8}" y="{y + BAR - 3}" font-size="12.5" font-weight="{weight}" '
                     f'fill="{t["ink"] if j == 0 else t["ink2"]}">{txt}<tspan dx="8" fill="{t["muted"]}">{note}</tspan></text>')
    s.append(f'<text x="24" y="{H - 14}" font-size="11.5" fill="{t["muted"]}">img2dataset hand-tuned to 24 in flight reaches 222 img/s on the rate-limited CDN; '
             f'Machyon finds its limit itself. Reproduce: python benchmarks/harness.py</text>')
    s.append("</svg>")
    return "\n".join(s)


for name, t in THEMES.items():
    with open(os.path.join(OUT, f"benchmark-{name}.svg"), "w") as f:
        f.write(chart(t))
print("ok")
