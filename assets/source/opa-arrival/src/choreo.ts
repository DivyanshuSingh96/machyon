// Opa's arrival, choreographed in camera space. World units are CSS pixels: x right, y up, z toward the visitor;
// the page is the plane z = 0 and the camera (the visitor) sits at distance D in front of the viewport's centre.
//
//   0 - 2.0 s   along the camera's own ray through the wordmark centre, from 20x the page distance to the foreground
//   2.0 - 4.3   one 3D path: the bank out of the foreground, the swoop, the climb, the turn at the top (through depth),
//               the N-to-M lane; the body follows the trail of its own neck, the bank comes from the path's curvature
//   4.2 - end   the approved film's turn, curl, settle and eye-stalk dip, unchanged, then the hand-over to the logo
import {Matrix4, Quaternion, Vector3} from 'three';
import {Pt, measure, along, lerp} from './geom';
import {Pose, blend, SWIM} from './rig';
import {TURN} from './poses';
import {B, BONES, BIND, SEG, SPINE, BODY_LEN, FLAP_S, NECK, EYE_DEF, PUPIL_DIR, HOSE_N, P} from './rigdata';
import {T, turnA, curl, exact, END, FADE, filmAt, filmHeadAt, SWIM_L, smooth, prog, clamp01} from './film';

export type Measure = {vw: number; vh: number; word: {l: number; t: number; r: number; b: number}; ctm: {a: number; e: number; f: number}};
const V = (x = 0, y = 0, z = 0) => new Vector3(x, y, z);
const X = V(1, 0, 0), Y = V(0, 1, 0), Z = V(0, 0, 1);
const FOV = 30, HZ = 120;
const NECK_D = Math.hypot(NECK.x, NECK.y);       // how far the neck sits behind the head centre (rig units)
const TILT = 15;                                 // in flight the back is turned a little toward the visitor: both flap rows read
const RAD = Math.PI / 180;

/** Cubic Hermite through (t, value, slope) knots. */
const hermite = (k: [number, number, number][], t: number) => {
  if (t <= k[0][0]) return k[0][1] + (t - k[0][0]) * k[0][2];
  const last = k[k.length - 1];
  if (t >= last[0]) return last[1];
  let i = 0;
  while (t > k[i + 1][0]) i++;
  const [t0, p0, m0] = k[i], [t1, p1, m1] = k[i + 1], h = t1 - t0, s = (t - t0) / h, s2 = s * s, s3 = s2 * s;
  return (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * h * m0 + (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * h * m1;
};
/** Centripetal Catmull-Rom through 3D points (the first and last only shape the ends), densely sampled. */
const catmull3 = (Q: Vector3[], per = 40) => {
  const pts: Vector3[] = [];
  for (let i = 1; i < Q.length - 2; i++) {
    const [p0, p1, p2, p3] = [Q[i - 1], Q[i], Q[i + 1], Q[i + 2]];
    const tj = (a: Vector3, b: Vector3, t: number) => t + Math.sqrt(Math.max(1e-6, a.distanceTo(b)));
    const t0 = 0, t1 = tj(p0, p1, t0), t2 = tj(p1, p2, t1), t3 = tj(p2, p3, t2);
    for (let k = i === 1 ? 0 : 1; k <= per; k++) {
      const t = t1 + (t2 - t1) * k / per, L = (a: Vector3, b: Vector3, u: number) => a.clone().lerp(b, u);
      const A1 = L(p0, p1, (t - t0) / (t1 - t0)), A2 = L(p1, p2, (t - t1) / (t2 - t1)), A3 = L(p2, p3, (t - t2) / (t3 - t2));
      const B1 = L(A1, A2, (t - t0) / (t2 - t0)), B2 = L(A2, A3, (t - t1) / (t3 - t1));
      pts.push(L(B1, B2, (t - t1) / (t2 - t1)));
    }
  }
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + pts[i].distanceTo(pts[i - 1]));
  return {pts, cum};
};
const onPath = (path: {pts: Vector3[]; cum: number[]}, s: number) => {
  const {pts, cum} = path, L = cum[cum.length - 1], d = Math.max(0, Math.min(L, s));
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (cum[m] <= d) lo = m; else hi = m; }
  const f = (d - cum[lo]) / ((cum[hi] - cum[lo]) || 1);
  const p = pts[lo].clone().lerp(pts[hi], f), a = pts[Math.max(0, lo - 1)], b = pts[Math.min(pts.length - 1, hi + 1)];
  return {p, t: b.clone().sub(a).normalize()};
};
const basisQ = (f: Vector3, u: Vector3) => {
  const z = f.clone().cross(u).normalize(), y = z.clone().cross(f).normalize();
  return new Quaternion().setFromRotationMatrix(new Matrix4().makeBasis(f, y, z));
};
const ortho = (v: Vector3, n: Vector3) => v.clone().addScaledVector(n, -v.dot(n)).normalize();

export type Frame = {bones: Matrix4[]; head: Vector3; flat: number; haze: number; zScale: number; outline: number; opacity: number; logo: number};

export class Choreo {
  readonly k: number; readonly D: number; readonly cam: Vector3; readonly A: Pt; readonly HF: Pt; readonly E: Pt;
  readonly lam0 = 20; readonly lamF: number; readonly route: {pts: Vector3[]; cum: number[]};
  readonly knots: [number, number, number][];
  private n: number; private P: Float32Array; private Q: Float32Array; private N: Float32Array; private U: Float32Array;
  private NS: Float32Array; private spr: Float32Array; private CH!: Float32Array; private CU!: Float32Array;

  constructor(readonly m: Measure) {
    const k = this.k = m.ctm.a, vw = m.vw, vh = m.vh;
    this.D = vh / 2 / Math.tan(FOV / 2 * RAD);
    this.cam = V(vw / 2, -vh / 2, this.D);
    const W = {l: m.word.l, r: m.word.r, t: m.word.t, b: m.word.b, w: m.word.r - m.word.l, h: m.word.b - m.word.t};
    const HF: Pt = this.HF = [m.ctm.a * 250 + m.ctm.e, m.ctm.a * 192 + m.ctm.f];
    const A: Pt = this.A = [W.l + W.w * 0.5, W.t + W.h * 0.52];
    const E: Pt = this.E = [HF[0] - 169 * k, HF[1] - 247.5 * k];
    this.lamF = vw >= 900 ? 0.45 : 0.32;
    // the agreed route on screen (page px), each point placed at its depth so that it still projects onto that spot
    const margin = Math.max(50, 230 * k), right = Math.min(W.r + W.w * 0.1, vw - margin), low = W.b + Math.max(W.h * 0.62, 70 * k);
    const climbTop = E[1] + 40 * k, D = this.D;
    const scr: [number, number, number][] = [
      [A[0] + W.w * 0.32, lerp(A[1], low, 0.9), 0.42 * D],                // the bank: a pass across the visitor, down-right
      [lerp(A[0], right, 0.74), low + W.h * 0.06, 0.18 * D],               // the swoop, settling back toward the page
      [right - 120 * k, low - W.h * 0.02, 0.04 * D],                         // the bottom-right corner, rounded to the body's length
      [right, lerp(low, climbTop, 0.45), 0],                                  // the climb up the right side
      [right - 10 * k, climbTop, 0.07 * D],                                   // the top of the climb, already leaning out of the page
      [right - 150 * k, E[1] - 26 * k, 0.11 * D],                            // over the top: a wide turn toward the visitor
      [lerp(right, E[0], 0.4), E[1] - 10 * k, 0.06 * D],                      // the N-to-M lane, easing back onto the page
      [E[0], E[1], 0],
    ];
    const atDepth = ([x, y, z]: [number, number, number]) => { const f = 1 - z / D; return V(vw / 2 + (x - vw / 2) * f, -(vh / 2 + (y - vh / 2) * f), z); };
    const F = this.ray(this.lamF), ghost = this.ray(this.lamF + 0.3);
    const way = [ghost, F, ...scr.map(atDepth)];
    way.push(way[way.length - 1].clone().multiplyScalar(2).sub(way[way.length - 2]));
    const route = this.route = catmull3(way, 48);
    const sOf = (i: number) => { const q = way[i + 1]; let best = 0, bd = Infinity; route.pts.forEach((p, j) => { const d = p.distanceTo(q); if (d < bd) { bd = d; best = j; } }); return route.cum[best]; };
    const L = route.cum[route.cum.length - 1], s3 = sOf(3), s5 = sOf(5), s7 = sOf(7);
    const v = (a: number, b: number, ta: number, tb: number) => (b - a) / (tb - ta);
    const vF = (this.lamAt(T.approachEnd - 1e-3) - this.lamAt(T.approachEnd)) / 1e-3 * this.cam.distanceTo(this.ray(1));
    // speed through the route: keeps the approach's momentum into the bank, eases over the top, stops at the lane end
    const s1 = sOf(1), tk = [T.approachEnd, 2.5, 3.05, 3.45, 3.85, T.laneEnd], sk = [0, s1, s3, s5, s7, L];
    const slope = (i: number) => (i === 0 ? vF : i === tk.length - 1 ? 0 : (sk[i + 1] - sk[i - 1]) / (tk[i + 1] - tk[i - 1]) * (i === 3 ? 0.7 : 1));
    this.knots = tk.map((t, i) => [t, sk[i], slope(i)] as [number, number, number]);
    this.n = Math.ceil(END * HZ) + 2;
    this.P = new Float32Array(this.n * 3); this.Q = new Float32Array(this.n * 4); this.N = new Float32Array(this.n * 3);
    this.U = new Float32Array(this.n * 3); this.NS = new Float32Array(this.n); this.spr = new Float32Array(this.n * 3);
    this.bake();
  }

  /** The point on the camera's ray through the wordmark centre, lam = 1 on the page. */
  ray(lam: number) { return this.cam.clone().lerp(V(this.A[0], -this.A[1], 0), lam); }
  lamAt(t: number) {
    const u = clamp01(t / T.approachEnd), m = 0.32, g = Math.pow(1 - u, 1.6) * (1 - m) + (1 - u) * m;
    return this.lamF * Math.exp(Math.log(this.lam0 / this.lamF) * g);
  }
  page(p: Pt, z = 0) { return V(p[0], -p[1], z); }

  // ---------------------------------------------------------------- the head's path and orientation, before springs
  private approachQ(t: number) {
    const f0 = this.cam.clone().sub(this.ray(1)).normalize(), u0 = ortho(Y, f0), th = 18 * RAD;
    const f = f0.clone().multiplyScalar(Math.cos(th)).addScaledVector(u0, -Math.sin(th)), u = f0.clone().multiplyScalar(Math.sin(th)).addScaledVector(u0, Math.cos(th));
    const q = basisQ(f, u);
    const yaw = new Quaternion().setFromAxisAngle(u, 6 * RAD * Math.sin(1.3 * t)), roll = new Quaternion().setFromAxisAngle(f, 3 * RAD * Math.sin(2.1 * t));
    return roll.multiply(yaw).multiply(q);
  }
  private sAt(t: number) { return hermite(this.knots, t); }
  private flightPos(t: number) { return onPath(this.route, this.sAt(t)).p; }
  private filmQ(yaw: number, roll: number) {
    const psi = yaw * RAD, f = V(Math.cos(psi), 0, Math.sin(psi)), a = TILT * (1 - Math.abs(roll)) * RAD;
    const c = Z.clone().addScaledVector(f, -Z.dot(f)), up = Y.clone();
    if (c.length() > 1e-4) up.addScaledVector(c.normalize(), Math.tan(a));
    return basisQ(f, up.normalize());
  }

  /** Sample the whole run at 120 Hz: head path and orientation (with a sprung bank), neck trail, secondary springs. */
  private bake() {
    const dt = 1 / HZ, n = this.n, up = Y.clone(), upV = V(), gStar = 15000 * this.k, tilt = Math.tan(TILT * RAD);
    const p = V(), q = new Quaternion(), fCur = V(), fVel = V();
    let fInit = false;
    for (let i = 0; i < n; i++) {
      const t = i * dt;
      if (t <= T.approachEnd) { p.copy(this.ray(this.lamAt(t))); q.copy(this.approachQ(t)); }
      else {
        const s = this.sAt(t), here = onPath(this.route, s);
        // the facing direction is sprung too: a tight spot in the path can turn the head only so fast (no flick)
        if (!fInit) { fCur.copy(here.t); fInit = true; }
        const wf = 11, fa = here.t.clone().sub(fCur).multiplyScalar(wf * wf).addScaledVector(fVel, -2 * wf);
        fVel.addScaledVector(fa, dt); fCur.addScaledVector(fVel, dt).normalize();
        const f = fCur.clone();
        // lateral acceleration 0.15 s ahead (the bank leads the turn), as a coordinated turn would bank
        const ta = Math.min(t + 0.15, T.laneEnd), sa = this.sAt(ta), va = (this.sAt(ta + 1e-3) - sa) / 1e-3;
        const t1 = onPath(this.route, sa - 4).t, t2 = onPath(this.route, sa + 4).t, kap = t2.clone().sub(t1).multiplyScalar(1 / 8);
        const aLat = kap.multiplyScalar(va * va), cap = gStar * Math.tan(55 * RAD);
        if (aLat.length() > cap) aLat.setLength(cap);
        const want = ortho(Y.clone().multiplyScalar(gStar).add(aLat).addScaledVector(Z, gStar * tilt), f);
        // critically damped spring on the up vector
        const w = 14, acc = want.clone().sub(up).multiplyScalar(w * w).addScaledVector(upV, -2 * w);
        upV.addScaledVector(acc, dt); up.addScaledVector(upV, dt);
        const u = ortho(up, f); up.copy(u);
        p.copy(here.p); q.copy(basisQ(f, u));
        const ab = smooth((t - T.approachEnd) / 0.35);
        if (ab < 1) q.copy(this.approachQ(t).slerp(q, ab));
        if (t >= turnA - 0.1) {
          const fl = filmHeadAt(Math.max(t, turnA), this.E, this.HF, this.k), wf = smooth((t - (turnA - 0.1)) / 0.2);
          p.lerp(this.page(fl.H), smooth((t - turnA) / 0.12));
          q.slerp(this.filmQ(fl.yaw, fl.roll), wf);
        }
      }
      if (i === 0) up.copy(Y).applyQuaternion(q);
      p.toArray(this.P, i * 3); q.toArray(this.Q, i * 4);
      const neck = p.clone();
      neck.toArray(this.N, i * 3); Y.clone().applyQuaternion(q).toArray(this.U, i * 3);
      this.NS[i] = i ? this.NS[i - 1] + neck.distanceTo(V().fromArray(this.N, (i - 1) * 3)) : 0;
    }
    // the spine: follow-the-leader from the neck, each joint keeping its bone length, bending at most MAXB a joint,
    // and held to the head's own body axis while the approach is head-on (no snapping between the two)
    const J = SPINE + 1, seg = SEG * this.k, MAXB = 21 * RAD, cur: Vector3[] = [];
    this.CH = new Float32Array(n * J * 3); this.CU = new Float32Array(n * J * 3);
    const rot = (from: Vector3, to: Vector3, maxA: number) => {
      const a = from.angleTo(to); if (a <= maxA) return to.clone();
      const axis = from.clone().cross(to); if (axis.lengthSq() < 1e-12) return from.clone();
      return from.clone().applyAxisAngle(axis.normalize(), maxA);
    };
    for (let i = 0; i < n; i++) {
      const t = i * dt, q = new Quaternion().fromArray(this.Q, i * 4), p0 = V().fromArray(this.P, i * 3);
      const back = X.clone().applyQuaternion(q).negate(), wr = 1 - smooth((t - (T.approachEnd - 0.2)) / 0.6);
      const neck = NECK.clone().multiplyScalar(this.k).applyQuaternion(q).add(p0);
      if (i === 0) for (let j = 0; j < J; j++) cur.push(neck.clone().addScaledVector(back, j * seg));
      cur[0].copy(neck);
      let prevDir = back.clone();
      for (let j = 1; j < J; j++) {
        const follow = cur[j].clone().sub(cur[j - 1]).normalize();
        let dir = follow.multiplyScalar(1 - wr).addScaledVector(back, wr).normalize();
        dir = rot(prevDir, dir, j === 1 ? MAXB * 1.6 : MAXB);
        cur[j].copy(cur[j - 1]).addScaledVector(dir, seg); prevDir = dir;
      }
      // up vectors: carried down the chain by parallel transport (no twist of its own), drawn toward the head's up
      // as it was 12 ms per joint earlier (the roll ripples down the body), at most 9 degrees of twist a joint
      let upPrev = V().fromArray(this.U, i * 3), dPrev = back.clone();
      for (let j = 0; j < J; j++) {
        cur[j].toArray(this.CH, (i * J + j) * 3);
        const d = j < J - 1 ? cur[j + 1].clone().sub(cur[j]).normalize() : dPrev.clone();
        const carried = upPrev.clone().applyQuaternion(new Quaternion().setFromUnitVectors(dPrev, d));
        const lagged = V().fromArray(this.U, Math.max(0, i - Math.round(j * 0.012 * HZ)) * 3);
        let u = ortho(carried.clone().lerp(ortho(lagged, d), 0.35), d);
        const c0 = ortho(carried, d);
        if (c0.angleTo(u) > 9 * RAD) u = rot(c0, u, 9 * RAD);
        u.toArray(this.CU, (i * J + j) * 3); upPrev = u; dPrev = d;
      }
    }
    // secondary motion: eye stalks and proboscis swing on damped springs driven by the head's acceleration
    const st = [0, 0, 0], sv = [0, 0, 0], w = [17, 17, 12], z = [0.42, 0.42, 0.5], gain = [0.0024, 0.0024, 0.003], lim = [0.4, 0.4, 0.45];
    const pos = (i: number) => V().fromArray(this.P, Math.max(0, Math.min(n - 1, i)) * 3);
    for (let i = 0; i < n; i++) {
      const a = pos(i + 2).add(pos(i - 2)).addScaledVector(pos(i), -2).multiplyScalar(1 / (4 * dt * dt));
      const qi = new Quaternion().fromArray(this.Q, i * 4).invert(), al = a.applyQuaternion(qi).multiplyScalar(1 / this.k);
      const fade = smooth((i * dt - 1.4) / 0.6) * (1 - smooth((i * dt - (turnA - 0.3)) / 0.5)), drive = [al.x, al.z, al.x];
      for (let j = 0; j < 3; j++) {
        const acc = -w[j] * w[j] * st[j] - 2 * z[j] * w[j] * sv[j] - gain[j] * drive[j] * fade * w[j] * w[j] / 100;
        sv[j] += acc * dt; st[j] += sv[j] * dt;
        this.spr[i * 3 + j] = lim[j] * Math.tanh(st[j] / lim[j]) * fade;   // a soft limit: no hard stop to bounce off
      }
    }
  }

  private sample(arr: Float32Array, size: number, t: number, out: number[]) {
    const x = Math.max(0, Math.min(this.n - 1.001, t * HZ)), i = Math.floor(x), f = x - i;
    for (let c = 0; c < size; c++) out[c] = arr[i * size + c] * (1 - f) + arr[(i + 1) * size + c] * f;
    return out;
  }
  head(t: number) {
    const p = V().fromArray(this.sample(this.P, 3, t, [0, 0, 0]));
    const x = Math.max(0, Math.min(this.n - 1.001, t * HZ)), i = Math.floor(x);
    const q = new Quaternion().fromArray(this.Q, i * 4).slerp(new Quaternion().fromArray(this.Q, (i + 1) * 4), x - i);
    return {p, q};
  }
  /** The neck's trail at arc length `back` behind where the neck is at time t (with the head's up at that moment). */
  private trail(t: number, back: number) {
    const x = Math.max(0, Math.min(this.n - 1.001, t * HZ)), i = Math.floor(x), f = x - i;
    const now = this.NS[i] * (1 - f) + this.NS[i + 1] * f, s = now - back;
    if (s <= 0) {
      const n0 = V().fromArray(this.N, 0), q0 = new Quaternion().fromArray(this.Q, 0);
      return {p: n0.addScaledVector(X.clone().applyQuaternion(q0), s), up: Y.clone().applyQuaternion(q0)};
    }
    let lo = 0, hi = i + 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (this.NS[m] <= s) lo = m; else hi = m; }
    const g = (s - this.NS[lo]) / ((this.NS[hi] - this.NS[lo]) || 1);
    return {p: V().fromArray(this.N, lo * 3).lerp(V().fromArray(this.N, hi * 3), g), up: V().fromArray(this.U, lo * 3).lerp(V().fromArray(this.U, hi * 3), g).normalize()};
  }

  // ---------------------------------------------------------------- one frame
  frame(t: number): Frame {
    const k = this.k, {p, q} = this.head(t), R = new Matrix4().makeRotationFromQuaternion(q);
    const HeadW = new Matrix4().compose(p, q, V(k, k, k));
    const film = t >= turnA ? filmAt(t, this.E, this.HF, k) : null;
    const wFilm = film ? 1 : smooth((t - (T.laneEnd - 0.45)) / 0.4), wChain = 1 - wFilm;
    const body: Pose = film ? film.body : SWIM_L;
    // the film curve, placed on the head as it is now (screen plane), its first joints easing out of the neck's depth
    const fc = body.C.map(([x, y]) => [x - 250, 192 - y] as Pt), fcum = measure(fc);
    const neckW = NECK.clone().applyMatrix4(HeadW), psi = (body.head.yaw ?? 0) * RAD, cw = Math.cos(psi);
    const aTilt = Math.tan(TILT * (1 - Math.abs(body.roll)) * RAD);
    const f0 = along(fc, fcum, 0)[0], shift = V(neckW.x - (p.x + f0[0] * k), neckW.y - (p.y + f0[1] * k), 0);
    const filmJoint = (s: number) => {
      const [[x, y], [tx, ty]] = along(fc, fcum, Math.min(1, s / BODY_LEN));
      const pp = V(p.x + (x) * k, p.y + (y) * k, 0).addScaledVector(shift, 1 - smooth(s / (4 * SEG)));
      pp.z = neckW.z * (1 - smooth(s / (3 * SEG)));
      const up = V(ty, -tx, 0).multiplyScalar(cw).addScaledVector(Z, 1 - Math.abs(cw)).normalize().addScaledVector(Z, aTilt).normalize();
      return {p: pp, up};
    };
    const undAmp = 5 * k * wChain * smooth((t - 2.3) / 0.4) * (1 - smooth((t - (T.laneEnd - 0.7)) / 0.4));
    const x = Math.max(0, Math.min(this.n - 1.001, t * HZ)), i0 = Math.floor(x), g = x - i0, NJ = SPINE + 1;
    const chainAt = (j: number) => {
      const o0 = (i0 * NJ + j) * 3, o1 = ((i0 + 1) * NJ + j) * 3;
      return {p: V().fromArray(this.CH, o0).lerp(V().fromArray(this.CH, o1), g), up: V().fromArray(this.CU, o0).lerp(V().fromArray(this.CU, o1), g).normalize()};
    };
    const chainJ = (j: number) => {
      const c = chainAt(Math.min(SPINE, j)), s = j * SEG;
      if (undAmp > 0 && j > 0) {
        const prev = chainAt(j - 1).p, side = c.p.clone().sub(prev).normalize().cross(c.up).normalize();
        c.p.addScaledVector(side, undAmp * (s / BODY_LEN) * Math.sin(2 * Math.PI * 1.3 * t - 6.3 * s / BODY_LEN));
      }
      return c;
    };
    const out: Matrix4[] = BONES.map(() => new Matrix4());
    const frameM = (o: Vector3, xdir: Vector3, up: Vector3) => {
      const x = xdir.clone().normalize(), y = ortho(up, x), z = x.clone().cross(y);
      return new Matrix4().makeBasis(x.multiplyScalar(k), y.multiplyScalar(k), z.multiplyScalar(k)).setPosition(o);
    };
    // chain and film blended, then re-spaced along their own polyline so every bone keeps its length (the film's
    // curves and the neck's depth would otherwise stretch the first bones by up to 20%)
    const segL = SEG * k;
    const B0 = Array.from({length: SPINE + 1}, (_, j) => {
      if (wFilm <= 0) return chainJ(j);
      const fj = filmJoint(j * SEG);
      if (wChain <= 0) return fj;
      const c = chainJ(j);
      return {p: c.p.lerp(fj.p, wFilm), up: c.up.lerp(fj.up, wFilm).normalize()};
    });
    B0[0].p.copy(neckW);
    const J: {p: Vector3; up: Vector3}[] = [B0[0]];
    {
      let a = 0, from = B0[0].p.clone(), left = segL;
      for (let j = 1; j <= SPINE; j++) {
        let found = false;
        while (a < SPINE) {
          const nb = B0[a + 1].p, d = from.distanceTo(nb);
          if (d >= left - 1e-6) { const u = Math.min(1, left / d); from = from.clone().lerp(nb, u); J.push({p: from.clone(), up: B0[a].up.clone().lerp(B0[a + 1].up, u).normalize()}); left = segL; if (u >= 1) a++; found = true; break; }
          left -= d; from = nb.clone(); a++;
        }
        if (!found) { const last = J[J.length - 1], dir = B0[SPINE].p.clone().sub(B0[SPINE - 1].p).normalize(); J.push({p: last.p.clone().addScaledVector(dir, segL), up: B0[SPINE].up.clone()}); }
      }
    }
    J[0].p.copy(neckW);
    // the first bone aims along the first half-segment of the film's curve (the logo's neck), along the chain otherwise
    const d0 = J[1].p.clone().sub(J[0].p).normalize().multiplyScalar(wChain);
    if (wFilm > 0) d0.addScaledVector(filmJoint(0.5 * SEG).p.sub(filmJoint(0).p).normalize(), wFilm);
    for (let j = 0; j < SPINE; j++) out[B.spine[j]] = frameM(J[j].p, j ? J[j + 1].p.clone().sub(J[j].p) : d0, J[j].up);
    out[B.root].copy(HeadW); out[B.head].copy(HeadW);
    // flaps: the swimming wave (wing beats in the approach), the film's sweep and ripple
    const ph = film ? body.ripplePhase : 2 * Math.PI * 1.9 * t, sweep = body.sweep - P.sweep;
    const beatAmp = lerp(9, 22, 1 - smooth((t - 1.5) / 0.6)) * (1 - wFilm), ripple = film ? body.ripple : 4;
    FLAP_S.forEach((s, i) => {
      const j = Math.min(SPINE - 1, Math.floor(s / SEG)), base = out[B.spine[j]].clone().multiply(new Matrix4().makeTranslation(s - j * SEG, 0, 0));
      const inPlane = (sweep + ripple * Math.sin(ph + Math.PI / 2 - i * 0.7)) * RAD;
      const beat = beatAmp * Math.sin(2 * Math.PI * (film ? 0 : 2.3) * t - i * 0.55) * RAD;
      for (const [bi, side] of [[B.flapsL[i], 1], [B.flapsR[i], -1]] as [number, number][]) {
        out[bi] = base.clone().multiply(new Matrix4().makeRotationZ(inPlane)).multiply(new Matrix4().makeRotationX(side * beat));
      }
    });
    const tailM = out[B.spine[SPINE - 1]].clone().multiply(new Matrix4().makeTranslation(SEG, 0, 0));
    const spread = body.tailSpread + (film ? 0 : 0.06 * Math.sin(2 * Math.PI * 1.1 * t));
    [16, 38, 58].forEach((half, kk) => [-1, 1].forEach((side, jj) => {
      out[B.fins[kk * 2 + jj]] = tailM.clone().multiply(new Matrix4().makeRotationZ(side * half * (spread - 1) * RAD));
    }));
    // head parts in the head's own frame: proboscis and claw from the un-mirrored film poses, sprung
    const hp: Pose = film ? film.head : t < T.approachEnd ? TURN : blend(TURN, SWIM, smooth((t - T.approachEnd) / 0.5));
    const sp = this.sample(this.spr, 3, t, [0, 0, 0]);
    const hc0 = hp.hose.T.map(([x, y]) => [x - 250, 192 - y] as Pt), o = hc0[0], hcum0 = measure(hc0), hl = hcum0[hcum0.length - 1];
    const bend = (pt: Pt, frac: number): Pt => {
      const a = sp[2] * Math.pow(frac, 1.4), c = Math.cos(a), s = Math.sin(a), dx = pt[0] - o[0], dy = pt[1] - o[1];
      return [o[0] + dx * c - dy * s, o[1] + dx * s + dy * c];
    };
    const hc = hc0.map((pt, i) => bend(pt, hcum0[i] / hl)), hcum = measure(hc);
    for (let i = 0; i < HOSE_N; i++) {
      const [[x, y], [tx, ty]] = along(hc, hcum, i / HOSE_N);
      out[B.hose[i]] = HeadW.clone().multiply(new Matrix4().makeBasis(V(tx, ty), V(ty, -tx), V(tx, ty).cross(V(ty, -tx))).setPosition(x, y, 0));
    }
    {
      const c = hp.claw, kn = bend([(c.x - 250) + Math.cos(c.ang * RAD) * -3 * c.s, 192 - (c.y + Math.sin(c.ang * RAD) * -3 * c.s)], 1);
      const base = HeadW.clone().multiply(new Matrix4().makeRotationZ(-c.ang * RAD + sp[2]).setPosition(kn[0], kn[1], 0));
      out[B.clawUp] = base.clone().multiply(new Matrix4().makeRotationZ((c.openUp - P.claw.openUp) * RAD));
      out[B.clawLo] = base.clone().multiply(new Matrix4().makeRotationZ(-(c.openLo - P.claw.openLo) * RAD));
    }
    // eyes: stalk length (the dip), sway (springs), gaze (the visitor in the approach, ahead in flight, the logo's at the end)
    const lift = film ? film.lift : 1, toModel = HeadW.clone().invert();
    const camM = this.cam.clone().applyMatrix4(toModel);
    const gazeW = film ? 1 - prog(t, turnA, exact) : 1;
    EYE_DEF.forEach((e, kk) => {
      const sway = new Matrix4().makeRotationZ(-sp[0] * (e.stalk ? 1 : 0.2)).multiply(new Matrix4().makeRotationX(sp[1] * (e.stalk ? 1 : 0.2)));
      const pivot = (m: Matrix4) => new Matrix4().makeTranslation(e.base.x, e.base.y, e.base.z).multiply(m).multiply(new Matrix4().makeTranslation(-e.base.x, -e.base.y, -e.base.z));
      const S = BIND[B.stalks[kk]].clone().multiply(new Matrix4().makeScale(lift, 1, 1));
      out[B.stalks[kk]] = HeadW.clone().multiply(pivot(sway)).multiply(S);
      const cen = e.base.clone().lerp(e.centre, lift).applyMatrix4(pivot(sway));
      const ahead = V(1, 0.05, 0.35).normalize(), toCam = camM.clone().sub(cen).normalize();
      const look = toCam.clone().lerp(ahead.clone().lerp(toCam, 0.35), smooth((t - T.approachEnd) / 0.6)).normalize();
      const qg = new Quaternion().setFromUnitVectors(PUPIL_DIR[kk], look).slerp(new Quaternion(), 1 - gazeW);
      out[B.eyes[kk]] = HeadW.clone().multiply(new Matrix4().compose(cen, qg, V(1, 1, 1))).multiply(new Matrix4().makeTranslation(-e.centre.x, -e.centre.y, -e.centre.z)).multiply(BIND[B.eyes[kk]]);
    });
    const lam = t < T.approachEnd ? this.lamAt(t) : 1;
    const flat = prog(t, exact, exact + 0.3, smooth);
    return {
      bones: out, head: p, flat, zScale: 1 - 0.9 * flat, outline: 6.5 * k,                // the logo's gap width
      haze: lam > 1 ? 0.65 * clamp01(Math.log(lam) / Math.log(this.lam0)) : 0,
      opacity: smooth(t / T.appear) * (1 - prog(t, END - FADE, END, smooth)), logo: prog(t, END - FADE, END, smooth),
    };
  }
  get duration() { return END; }
  /** Bone world matrices at rest: the GLB's bind pose (model space), for checks. */
  static bind() { return BIND; }
}
