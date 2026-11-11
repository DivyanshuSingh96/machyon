// Pure geometry, ported 1:1 from brand/source/geo.py and opa_brand.py (same formulas, same sampling),
// so the rig's rest pose is the logo's own maths. No booleans: gaps are drawn as paper halos at render time.
export type Pt = [number, number];
export type Shape = {t: 'poly'; p: Pt[]} | {t: 'circle'; c: Pt; r: number};

export const bez = (p0: Pt, p1: Pt, p2: Pt, p3: Pt, n = 48): Pt[] => {
  const out: Pt[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    out.push([a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]]);
  }
  return out;
};

/** geo.path: start point, then [c1, c2, end] segments. */
export const pathSegs = (start: Pt, segs: [Pt, Pt, Pt][], n = 40): Pt[] => {
  const pts: Pt[] = [start];
  let cur = start;
  for (const [c1, c2, end] of segs) {
    pts.push(...bez(cur, c1, c2, end, n).slice(1));
    cur = end;
  }
  return pts;
};

const dist = (a: Pt, b: Pt) => Math.hypot(b[0] - a[0], b[1] - a[1]);

/** Cumulative lengths for repeated interpolation along one polyline. */
export const measure = (pts: Pt[]) => {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + dist(pts[i - 1], pts[i]));
  return cum;
};

const interp = (pts: Pt[], cum: number[], d: number): Pt => {
  const L = cum[cum.length - 1];
  if (d <= 0) return pts[0];
  if (d >= L) return pts[pts.length - 1];
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (cum[m] <= d) lo = m; else hi = m;
  }
  const seg = cum[hi] - cum[lo] || 1, f = (d - cum[lo]) / seg;
  return [pts[lo][0] + (pts[hi][0] - pts[lo][0]) * f, pts[lo][1] + (pts[hi][1] - pts[lo][1]) * f];
};

/** geo.along: point and unit tangent at fraction t of the length (tangent from d-1 to d+1, like shapely). */
export const along = (pts: Pt[], cum: number[], t: number): [Pt, Pt] => {
  const L = cum[cum.length - 1], d = L * t;
  const p = interp(pts, cum, d), q = interp(pts, cum, Math.min(L, d + 1)), r = interp(pts, cum, Math.max(0, d - 1));
  const dx = q[0] - r[0], dy = q[1] - r[1], m = Math.hypot(dx, dy) || 1;
  return [p, [dx / m, dy / m]];
};

/** geo.tube: variable-width stroke polygon + optional round caps. */
export const tube = (pts: Pt[], w0: number, w1 = w0, cap = true): Shape[] => {
  const n = pts.length, left: Pt[] = [], right: Pt[] = [];
  for (let i = 0; i < n; i++) {
    const a = pts[Math.max(i - 1, 0)], b = pts[Math.min(i + 1, n - 1)];
    const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1, nx = -dy / L, ny = dx / L;
    const w = (w0 + (w1 - w0) * i / (n - 1)) / 2;
    left.push([pts[i][0] + nx * w, pts[i][1] + ny * w]);
    right.push([pts[i][0] - nx * w, pts[i][1] - ny * w]);
  }
  const out: Shape[] = [{t: 'poly', p: [...left, ...right.reverse()]}];
  if (cap) out.push({t: 'circle', c: pts[0], r: w0 / 2}, {t: 'circle', c: pts[n - 1], r: w1 / 2});
  return out;
};

export const xform = (p: Pt[], ang: number, x: number, y: number, s = 1, sy = s): Pt[] => {
  const a = ang * Math.PI / 180, c = Math.cos(a), sn = Math.sin(a);
  return p.map(([px, py]) => {
    const qx = px * s, qy = py * sy;
    return [qx * c - qy * sn + x, qx * sn + qy * c + y];
  });
};

/** opa_brand.paddle: broad base, gentle taper, rounded end, along +x. */
export const paddle = (L: number, wb: number, ratio = 0.62): Pt[] => {
  const wt = wb * ratio;
  const up = bez([0, -wb / 2], [L * 0.35, -wb / 2], [L * 0.7, -wt / 2 - 1], [L - wt / 2, -wt / 2], 20);
  const down = up.map(([x, y]) => [x, -y] as Pt).reverse();
  const tip: Pt[] = [];
  for (let i = 0; i <= 16; i++) {
    const a = (-90 + 180 * i / 16) * Math.PI / 180;
    tip.push([L - wt / 2 + wt / 2 * Math.cos(a), wt / 2 * Math.sin(a)]);
  }
  return [...up, ...tip, ...down];
};

/** opa_brand.blade with a continuous tooth amount: at 0 the tooth vertices lie on the toothless inner line. */
export const blade = (L = 66, wdt = 11, teeth = 5, tooth = 3.4, amount = 1): Pt[] => {
  const outer = bez([0, -3], [L * 0.3, -wdt * 1.25], [L * 0.75, -wdt * 1.05], [L, 1.5], 40);
  const a: Pt = [L * 0.92, -1.2], b: Pt = [0, 3];
  const onLine = (x: number): number => a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]);
  const inner: Pt[] = [[L, 1.5], a];
  for (let i = 0; i < teeth; i++) {
    const x = L * (0.80 - 0.13 * i);
    for (const [px, py] of [[x + 3.2, -1.6], [x, -1.6 + tooth], [x - 3.2, -1.6]] as Pt[]) {
      inner.push([px, onLine(px) + (py - onLine(px)) * amount]);
    }
  }
  inner.push(b);
  return [...outer, ...inner];
};

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const lerpPt = (a: Pt, b: Pt, t: number): Pt => [lerp(a[0], b[0], t), lerp(a[1], b[1], t)];
export const lerpPts = (a: Pt[], b: Pt[], t: number): Pt[] => a.map((p, i) => lerpPt(p, b[i], t));

/** Blend two curves like a spine: equal-arc-length samples, interpolated start point, length and the heading of
 * every segment (unwrapped), then re-integrated. The body keeps its length and bends through the change
 * instead of collapsing the way a point-for-point blend does. Exact at t = 0 and t = 1. */
/** lag > 0: follow-through. The head end leads and each segment arrives later toward the tail (t = 0 and 1 stay exact). */
export const blendSpine = (a: Pt[], b: Pt[], t: number, n = 181, lag = 0): Pt[] => {
  const ra = resample(a, n), rb = resample(b, n);
  const ca = measure(ra), cb = measure(rb), La = ca[n - 1], Lb = cb[n - 1];
  const head = (r: Pt[]) => {
    const h: number[] = [];
    for (let i = 0; i < n - 1; i++) h.push(Math.atan2(r[i + 1][1] - r[i][1], r[i + 1][0] - r[i][0]));
    for (let i = 1; i < h.length; i++) {
      while (h[i] - h[i - 1] > Math.PI) h[i] -= 2 * Math.PI;
      while (h[i] - h[i - 1] < -Math.PI) h[i] += 2 * Math.PI;
    }
    return h;
  };
  const ha = head(ra), hb = head(rb);
  let off = 0;                                     // keep the two heading tracks within one turn of each other
  while (hb[0] + off - ha[0] > Math.PI) off -= 2 * Math.PI;
  while (hb[0] + off - ha[0] < -Math.PI) off += 2 * Math.PI;
  const seg = lerp(La, Lb, t) / (n - 1), out: Pt[] = [lerpPt(ra[0], rb[0], t)];
  for (let i = 0; i < n - 1; i++) {
    const ti = lag ? Math.max(0, Math.min(1, t * (1 + lag) - lag * i / (n - 2))) : t;
    const h = lerp(ha[i], hb[i] + off, ti), p = out[i];
    out.push([p[0] + Math.cos(h) * seg, p[1] + Math.sin(h) * seg]);
  }
  return out;
};

/** Resample a polyline to n points at equal arc length (so any two poses blend point for point). */
export const resample = (pts: Pt[], n: number): Pt[] => {
  const cum = measure(pts), L = cum[cum.length - 1], out: Pt[] = [];
  for (let i = 0; i < n; i++) out.push(interp(pts, cum, L * i / (n - 1)));
  return out;
};
