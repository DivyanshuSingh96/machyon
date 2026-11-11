// Opa's procedural spline rig. A Pose is plain numbers; opaParts(pose) turns it into ordered, coloured parts.
// The presets CURL_FULL / CURL_MID / ARC_FULL reproduce opa_brand.curl("full"), curl("mid") and arc() exactly.
import {Pt, Shape, bez, pathSegs, measure, along, tube, xform, paddle, blade, lerp, lerpPt, lerpPts, resample, blendSpine} from './geom';

export type Key = 'body' | 'claw' | 'eyeball' | 'pupil' | 'glint';
export type Cut = {a: Pt; b: Pt; w: number};
export type Part = {id: string; key: Key; gap: number; shapes: Shape[]; cuts?: Cut[]};
export type Eye = {base: Pt; centre: Pt; r: number; stalk: number; lift: number; target: Pt};
export type Pose = {
  C: Pt[]; w0: number; w1: number;
  slots: number[];            // paddle positions along the body (fractions), one per paddle
  paddleLen: number; paddleW: number; sweep: number; wt: number; notchW: number;
  roll: number;               // -1: side view with the Curl's lobe row in front; +1: the Arc's row in front; 0: top view, both rows
  ripple: number; ripplePhase: number; farPeek: number;   // swimming-lobe wave (deg) and far-row peek (0..1)
  tailScale: number; tailSpread: number;
  gap: number;
  hose: {T: Pt[]; w0: number; w1: number; rings: number[]; ringW: number};
  claw: {x: number; y: number; ang: number; s: number; openUp: number; openLo: number; teethAmt: number; wdt: number};
  head: {x: number; y: number; s: number; tilt: number; pivot: Pt; facing?: number; yaw?: number};
  eyes: Eye[]; glint: number;
};

export const TAIL_PAIRS: [number, number, number, number][] = [[1.0, 16, 78, 34], [0.92, 38, 66, 34], [0.84, 58, 58, 34]];
const SHIELD = pathSegs([-58, -46], [[[-20, -47], [22, -47], [52, -46]], [[64, -46], [70, -38], [70, -26]],
  [[70, 6], [52, 36], [28, 50]], [[16, 57], [2, 56], [-10, 49]], [[-42, 32], [-72, 8], [-72, -26]],
  [[-72, -38], [-66, -46], [-58, -46]]], 30);

const CURL_C = [...bez([206, 214], [112, 246], [88, 388], [218, 430], 90).slice(0, -1), ...bez([218, 430], [320, 462], [392, 420], [436, 398], 90)];
const ARC_C = [...bez([214, 300], [110, 296], [70, 170], [176, 116], 90).slice(0, -1), ...bez([176, 116], [280, 66], [390, 110], [430, 78], 90)];
const CURL_T = bez([286, 224], [292, 272], [288, 304], [314, 314], 80);
const ARC_T = bez([296, 340], [300, 402], [348, 420], [396, 392], 80);
const slotsFor = (n: number) => Array.from({length: 11}, (_, i) => 0.05 + 0.73 * Math.round(i * (n - 1) / 10) / (n - 1));
const eyes = (defs: number[][], target: Pt): Eye[] => defs.map(([bx, by, ex, ey, r, st = 1]) =>
  ({base: [bx, by], centre: [ex, ey], r, stalk: st, lift: 1, target}));
const EYES_FULL = [[-44, -46, -60, -100, 22], [-18, -46, -16, -110, 26], [10, -46, 12, -64, 17, 0], [36, -46, 44, -88, 15], [56, -42, 76, -76, 15]];
const EYES_MID = [[-48, -46, -72, -104, 27], [-14, -46, -10, -120, 29], [16, -46, 18, -70, 21, 0], [42, -46, 58, -102, 20], [62, -40, 96, -70, 20]];

export const CURL_FULL: Pose = {
  C: CURL_C, w0: 50, w1: 24, slots: slotsFor(11), paddleLen: 72, paddleW: 42, sweep: 56, wt: 0.72, notchW: 3.4,
  roll: -1, ripple: 0, ripplePhase: 0, farPeek: 0, tailScale: 1.1, tailSpread: 1, gap: 4.5,
  hose: {T: CURL_T, w0: 27, w1: 18, rings: [0.42, 0.56, 0.68, 0.79], ringW: 2.8},
  claw: {x: 316, y: 313, ang: -34, s: 1.2, openUp: 18, openLo: 18, teethAmt: 1, wdt: 11},
  head: {x: 250, y: 192, s: 1, tilt: 0, pivot: [36, 32]}, eyes: eyes(EYES_FULL, [340, 240]), glint: 1,
};
export const CURL_MID: Pose = {
  ...CURL_FULL, w0: 58, w1: 30, slots: slotsFor(9), paddleLen: 76, paddleW: 48, notchW: 0, tailScale: 1.2, gap: 6.5,
  hose: {T: CURL_T, w0: 32, w1: 24, rings: [0.42, 0.56, 0.68, 0.79], ringW: 0},
  claw: {x: 316, y: 313, ang: -34, s: 1.45, openUp: 18, openLo: 18, teethAmt: 0, wdt: 14},
  eyes: eyes(EYES_MID, [340, 240]), glint: 0,
};
export const ARC_FULL: Pose = {
  ...CURL_FULL, C: ARC_C, w0: 64, w1: 26, paddleLen: 62, paddleW: 42, sweep: 44, wt: 0.8, roll: 1, tailScale: 1,
  hose: {T: ARC_T, w0: 28, w1: 18, rings: [0.32, 0.44, 0.55, 0.65, 0.74], ringW: 2.8},
  claw: {x: 398, y: 390, ang: -32, s: 1.25, openUp: 17, openLo: 17, teethAmt: 1, wdt: 11},
  head: {x: 262, y: 318, s: 1, tilt: 0, pivot: [36, 32]}, eyes: eyes(EYES_FULL, [470, 360]),
};

/** Blend two poses point for point (centrelines resampled to equal arc length first). */
export const blend = (a: Pose, b: Pose, t: number, lag = 0): Pose => {
  if (t <= 0) return a;
  if (t >= 1) return b;
  const m = Math.max(a.hose.T.length, b.hose.T.length);
  const L = (k: 'w0' | 'w1' | 'paddleLen' | 'paddleW' | 'sweep' | 'wt' | 'notchW' | 'roll' | 'ripple' | 'ripplePhase' | 'farPeek' | 'tailScale' | 'tailSpread' | 'gap' | 'glint') => lerp(a[k], b[k], t);
  return {
    C: a.C === b.C ? a.C : blendSpine(a.C, b.C, t, Math.max(a.C.length, b.C.length), lag), slots: a.slots.map((s, i) => lerp(s, b.slots[i], t)),
    w0: L('w0'), w1: L('w1'), paddleLen: L('paddleLen'), paddleW: L('paddleW'), sweep: L('sweep'), wt: L('wt'), notchW: L('notchW'),
    roll: L('roll'), ripple: L('ripple'), ripplePhase: L('ripplePhase'), farPeek: L('farPeek'), tailScale: L('tailScale'),
    tailSpread: lag ? lerp(a.tailSpread, b.tailSpread, clamp01(t * (1 + lag) - lag)) : L('tailSpread'), gap: L('gap'), glint: L('glint'),
    hose: {T: a.hose.T === b.hose.T ? a.hose.T : blendSpine(a.hose.T, b.hose.T, t, m),
      w0: lerp(a.hose.w0, b.hose.w0, t), w1: lerp(a.hose.w1, b.hose.w1, t),
      rings: a.hose.rings.length === b.hose.rings.length ? a.hose.rings.map((r, i) => lerp(r, b.hose.rings[i], t)) : (t < 0.5 ? a : b).hose.rings,
      ringW: lerp(a.hose.ringW, b.hose.ringW, t)},
    claw: {x: lerp(a.claw.x, b.claw.x, t), y: lerp(a.claw.y, b.claw.y, t), ang: lerp(a.claw.ang, b.claw.ang, t), s: lerp(a.claw.s, b.claw.s, t),
      openUp: lerp(a.claw.openUp, b.claw.openUp, t), openLo: lerp(a.claw.openLo, b.claw.openLo, t), teethAmt: lerp(a.claw.teethAmt, b.claw.teethAmt, t), wdt: lerp(a.claw.wdt, b.claw.wdt, t)},
    head: {x: lerp(a.head.x, b.head.x, t), y: lerp(a.head.y, b.head.y, t), s: lerp(a.head.s, b.head.s, t), tilt: lerp(a.head.tilt, b.head.tilt, t), pivot: lerpPt(a.head.pivot, b.head.pivot, t),
      yaw: lerp(yawOf(a), yawOf(b), t)},
    eyes: a.eyes.map((e, i) => ({base: lerpPt(e.base, b.eyes[i].base, t), centre: lerpPt(e.centre, b.eyes[i].centre, t), r: lerp(e.r, b.eyes[i].r, t),
      stalk: t < 0.5 ? e.stalk : b.eyes[i].stalk, lift: lerp(e.lift, b.eyes[i].lift, t), target: lerpPt(e.target, b.eyes[i].target, t)})),
  };
};

const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
/** Head orientation in degrees: 0 = facing right (the approved art), 180 = facing left, 90 = facing the viewer. */
export const yawOf = (p: Pose) => p.head.yaw ?? ((p.head.facing ?? 1) < 0 ? 180 : 0);
// pseudo-3D depth of each eye across the head (rear pair outermost, median on the centre line, front pair inside)
const EYE_Z = [-30, 30, 0, -18, 18];

/** Ordered parts, back to front, exactly as opa_brand builds them (body, paddles, tail, hose, claw, head, eyes). */
export const opaParts = (p: Pose): Part[] => {
  const parts: Part[] = [], cum = measure(p.C), g = p.gap;
  const frame = (t: number) => {
    const [[x, y], [tx, ty]] = along(p.C, cum, t);
    return {x, y, tx, ty, w: (p.w0 + (p.w1 - p.w0) * t) / 2};
  };
  // lobe rows: side -1 = the Curl's visible row, side +1 = the Arc's; both exist (bilateral flaps)
  const eNeg = clamp01(1 - p.roll), ePos = clamp01(1 + p.roll), front = p.roll <= 0 ? -1 : 1;
  const row = (side: number, ext: number, phase: number, layer: string, angOff = 0): Part[] => {
    if (ext < 0.02) return [];
    const out: Part[] = [];
    const order = p.slots.map((t, i) => [t, i] as [number, number]).sort((u, v) => v[0] - u[0]);   // posterior first
    for (const [t, i] of order) {
      const {x, y, tx, ty, w} = frame(t), nx = side * ty, ny = -side * tx;
      const wave = p.ripple * Math.sin(p.ripplePhase + phase - i * 0.7);
      const ang = Math.atan2(ty, tx) * 180 / Math.PI - side * (p.sweep + wave + angOff);
      const L = p.paddleLen * (1 - 0.3 * t), wb = p.paddleW * (1 - 0.3 * t);
      const shape = xform(paddle(L, wb, p.wt), ang, x + nx * w * 0.15 - tx * 6, y + ny * w * 0.15 - ty * 6, ext);
      out.push({id: `lobe-${layer}-${i}`, key: 'body', gap: g, shapes: [{t: 'poly', p: shape}]});
    }
    return out;
  };
  // far-side flaps: in a side view they point the same way as the near row and sit behind it (hidden at rest,
  // peeking between near flaps when farPeek > 0); during a roll the far row swings out on the opposite edge
  if (p.farPeek > 0.01) parts.push(...row(front, 1, Math.PI * 0.9, 'farside', -14 * p.farPeek));
  parts.push(...row(-front, front === -1 ? ePos : eNeg, Math.PI / 2, 'far'));
  // core + end cap, notches on the front side
  const left: Pt[] = [], right: Pt[] = [];
  for (let i = 0; i < p.C.length; i++) {
    const {x, y, tx, ty, w} = frame(i / (p.C.length - 1));
    left.push([x - ty * w, y + tx * w]); right.push([x + ty * w, y - tx * w]);
  }
  const cuts: Cut[] = [];
  if (p.notchW > 0.05) {
    const ts = [...p.slots].sort((u, v) => u - v);
    for (let k = 0; k < ts.length - 1; k++) {
      if (ts[k + 1] - ts[k] < 1e-4) continue;
      const {x, y, tx, ty, w} = frame((ts[k] + ts[k + 1]) / 2), nx = front * ty, ny = -front * tx;
      cuts.push({a: [x + nx * w * 0.25, y + ny * w * 0.25], b: [x + nx * w * 1.2, y + ny * w * 1.2], w: p.notchW});
    }
  }
  parts.push({id: 'core', key: 'body', gap: g, shapes: [{t: 'poly', p: [...left, ...right.reverse()]}, {t: 'circle', c: p.C[p.C.length - 1], r: p.w1 / 2}], cuts});
  parts.push(...row(front, front === -1 ? eNeg : ePos, 0, 'near'));
  // tail: three mirrored pairs on one shared axis
  const end = frame(1), axis = Math.atan2(end.ty, end.tx) * 180 / Math.PI, blen = cum[cum.length - 1];
  TAIL_PAIRS.forEach(([t, half, L, wb], k) => {
    const d = (1 - t) * blen * 0.55 + 4;
    for (const side of [-1, 1]) {
      parts.push({id: `tail-${k}-${side < 0 ? 'a' : 'b'}`, key: 'body', gap: g * 0.75,
        shapes: [{t: 'poly', p: xform(paddle(L * p.tailScale, wb * p.tailScale, 0.42), axis + side * half * p.tailSpread, end.x - end.tx * d, end.y - end.ty * d)}]});
    }
  });
  // hose with rings
  const hc = measure(p.hose.T), rings: Cut[] = [];
  if (p.hose.ringW > 0.05) for (const t of p.hose.rings) {
    const [[x, y], [tx, ty]] = along(p.hose.T, hc, t), w = p.hose.w0 + (p.hose.w1 - p.hose.w0) * t;
    rings.push({a: [x - ty * w, y + tx * w], b: [x + ty * w, y - tx * w], w: p.hose.ringW});
  }
  parts.push({id: 'hose', key: 'body', gap: g, shapes: tube(p.hose.T, p.hose.w0, p.hose.w1), cuts: rings});
  // forceps: two blades on one knuckle
  const c = p.claw, teeth = 5, bl = blade(66, c.wdt, teeth, 3.4, c.teethAmt);
  const up = xform(bl, -c.openUp, 0, 0), lo = xform(bl.map(([x, y]) => [x, -y] as Pt), c.openLo, 0, 0);
  const place = (q: Pt[]) => xform(q, c.ang, c.x, c.y, c.s);
  parts.push({id: 'claw', key: 'claw', gap: g, shapes: [{t: 'poly', p: place(up)}, {t: 'poly', p: place(lo)},
    {t: 'circle', c: place([[-3, 0]])[0], r: 10 * c.s}]});
  // head: shield, then each eye (stalk, eyeball, pupil, glint). Yaw turns the head through a front view:
  // x' = x cos(yaw) + z sin(yaw); exact approved geometry at yaw 0 and 180.
  const h = p.head, yaw = yawOf(p) * Math.PI / 180, cy = Math.cos(yaw), sy = Math.sin(yaw), turning = Math.abs(sy) > 0.02;
  const tiltSign = cy >= 0 ? 1 : -1, tilt = tiltSign * h.tilt * Math.PI / 180, ct = Math.cos(tilt), st = Math.sin(tilt);
  const pv: Pt = [h.x + h.pivot[0] * cy * h.s, h.y + h.pivot[1] * h.s];
  const P = ([x, y]: Pt, z = 0): Pt => {
    const wx = h.x + (x * cy + z * sy) * h.s - pv[0], wy = h.y + y * h.s - pv[1];
    return [pv[0] + wx * ct - wy * st, pv[1] + wx * st + wy * ct];
  };
  const shield: Shape[] = [{t: 'poly', p: SHIELD.map((q) => P(q))}];
  if (turning) {                                  // the head's own width, seen as it turns toward the viewer
    const e: Pt[] = [];
    for (let i = 0; i < 48; i++) { const a = i / 48 * 2 * Math.PI; e.push(P([0, 4 + Math.sin(a) * 50], Math.cos(a) * 62)); }
    shield.push({t: 'poly', p: e});
  }
  parts.push({id: 'shield', key: 'body', gap: 0, shapes: shield});
  const order = p.eyes.map((_, k) => k);
  if (turning) order.sort((u, v) => p.eyes[u].centre[0] * sy - p.eyes[v].centre[0] * sy);   // far eyes first
  for (const k of order) {
    const e = p.eyes[k];
    const base = P(e.base, EYE_Z[k] * 0.8), full = P(e.centre, EYE_Z[k]), cen = lerpPt(base, full, e.lift), r = e.r * h.s;
    if (e.stalk > 0.5) parts.push({id: `stalk-${k}`, key: 'body', gap: 0, shapes: tube([base, cen], r * 0.78)});
    parts.push({id: `eye-${k}`, key: 'eyeball', gap: g * h.s, shapes: [{t: 'circle', c: cen, r}]});
    const dx = e.target[0] - cen[0], dy = e.target[1] - cen[1], m = Math.hypot(dx, dy) || 1, pc: Pt = [cen[0] + dx / m * r * 0.3, cen[1] + dy / m * r * 0.3];
    parts.push({id: `pupil-${k}`, key: 'pupil', gap: 0, shapes: [{t: 'circle', c: pc, r: r * 0.46}]});
    if (p.glint > 0.02) parts.push({id: `glint-${k}`, key: 'glint', gap: 0, shapes: [{t: 'circle', c: [pc[0] - r * 0.16, pc[1] - r * 0.18], r: r * 0.13 * p.glint}]});
  }
  return parts;
};

/** Mirror a whole pose about the vertical line x = a (the character turns to face the other way). */
export const mirrorPose = (p: Pose, a: number): Pose => {
  const m = ([x, y]: Pt): Pt => [2 * a - x, y];
  return {
    ...p, C: p.C.map(m), roll: -p.roll,
    hose: {...p.hose, T: p.hose.T.map(m)},
    claw: {...p.claw, x: 2 * a - p.claw.x, ang: 180 - p.claw.ang, openUp: p.claw.openLo, openLo: p.claw.openUp},
    head: {...p.head, x: 2 * a - p.head.x, yaw: 180 - yawOf(p), facing: undefined},
    eyes: p.eyes.map((e) => ({...e, target: m(e.target)})),
  };
};

/** A gentle arc of a given length leaving `start` along `heading` (deg), bending by `bend` deg in total. */
export const arcCurve = (start: Pt, heading: number, length: number, bend: number, n = 181): Pt[] => {
  const out: Pt[] = [start], seg = length / (n - 1);
  for (let i = 1; i < n; i++) {
    const h = (heading + bend * (i - 0.5) / (n - 1)) * Math.PI / 180, q = out[i - 1];
    out.push([q[0] + Math.cos(h) * seg, q[1] + Math.sin(h) * seg]);
  }
  return out;
};
const CURL_LEN = measure(CURL_FULL.C).slice(-1)[0];
/** Swimming: body straightened behind the head (top-view roll, both lobe rows), hose relaxed forward. */
export const SWIM: Pose = {
  ...CURL_FULL, C: arcCurve([206, 214], 176, CURL_LEN, -14), roll: 0, sweep: 50, ripple: 10,
  hose: {...CURL_FULL.hose, T: bez([286, 224], [298, 258], [322, 276], [352, 280], 80)},
  claw: {...CURL_FULL.claw, x: 354, y: 280, ang: 6, openUp: 7, openLo: 7},
};
/** Coiled inside the Y box: flaps tucked, tail folded, hose down. */
export const BOXED: Pose = {
  ...CURL_FULL,
  C: [...bez([206, 214], [150, 226], [138, 322], [214, 336], 90).slice(0, -1), ...bez([214, 336], [292, 348], [324, 286], [282, 262], 90)],
  sweep: 74, tailSpread: 0.45,
};
