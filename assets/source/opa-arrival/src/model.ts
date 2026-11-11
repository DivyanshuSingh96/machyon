// Opa as a skinned 3D character, generated from the approved 2D rig (geom.ts / rig.ts are the logo's own maths).
// Model space: rig units; x = rig x - 250 (head faces +x), y = 192 - rig y (up), z toward the viewer of the logo.
// Every part's side projection in the curl pose is the logo's own shape; depth is added across the body only.
import * as THREE from 'three';
import {Pt, measure, along, paddle as paddle0, blade as blade0, xform, pathSegs, resample} from './geom';
import {CURL_FULL, TAIL_PAIRS, arcCurve} from './rig';
import {SPINE, M, P, BODY_LEN, HOSE_LEN, HOSE_N, SLOTS, B, BONES, v3, frameOn, BIND_C, BIND_CUM, SEG, EYE_DEF, PUPIL_DIR, hoseFrame, BIND, setWorld} from './rigdata';
export * from './rigdata';
const paddle = (L: number, wb: number, r?: number) => resample(paddle0(L, wb, r), 30);
const blade = (...a: Parameters<typeof blade0>) => resample(blade0(...a), 40);
export const DIHEDRAL = 32;              // flaps raised this far above horizontal
const FIN_TILT = 28;                     // tail blades turned off the centre plane
const PITCH = 0.85;                      // flap twist about its long axis (tan of the angle)
const BODY_W = 1.2;                      // body width / height

// ---------------------------------------------------------------- geometry builder
type Buf = {pos: number[]; sk: number[]; sw: number[]; idx: number[][]; on: Map<number, THREE.Vector3>; ow: Map<number, number>};
const buf: Buf = {pos: [], sk: [], sw: [], idx: [[], [], []], on: new Map(), ow: new Map()};   // materials: 0 body, 1 amber, 2 pupil
const vert = (p: THREE.Vector3, bones: number[], w: number[]) => {
  buf.pos.push(p.x, p.y, p.z);
  const b = [0, 0, 0, 0], ww = [0, 0, 0, 0];
  bones.forEach((bi, i) => { b[i] = bi; ww[i] = w[i]; });
  const sum = ww.reduce((a, c) => a + c, 0) || 1;
  buf.sk.push(...b); buf.sw.push(...ww.map((x) => x / sum));
  return buf.pos.length / 3 - 1;
};
const quad = (m: number, a: number, b: number, c: number, d: number) => buf.idx[m].push(a, b, c, a, c, d);

/** A tube along a chain of rings: ring(i) returns [points, bones, weights]. */
const gridTube = (m: number, rings: {pts: THREE.Vector3[]; b: number[]; w: number[]}[], closeStart: boolean, closeEnd: boolean) => {
  const n = rings[0].pts.length, ids = rings.map((r) => r.pts.map((p) => vert(p, r.b, r.w)));
  for (let i = 0; i < rings.length - 1; i++) for (let j = 0; j < n; j++) quad(m, ids[i][j], ids[i][(j + 1) % n], ids[i + 1][(j + 1) % n], ids[i + 1][j]);
  const cap = (ring: number[], c: THREE.Vector3, r: {b: number[]; w: number[]}, flip: boolean) => {
    const ci = vert(c, r.b, r.w);
    for (let j = 0; j < n; j++) flip ? buf.idx[m].push(ci, ring[(j + 1) % n], ring[j]) : buf.idx[m].push(ci, ring[j], ring[(j + 1) % n]);
  };
  const mid = (r: THREE.Vector3[]) => r.reduce((a, p) => a.add(p), v3(0, 0)).multiplyScalar(1 / r.length);
  if (closeStart) cap(ids[0], mid(rings[0].pts), rings[0], true);
  if (closeEnd) cap(ids[rings.length - 1], mid(rings[rings.length - 1].pts), rings[rings.length - 1], false);
};

/** A flat polygon given as 3D outline points, extruded ±th/2 along nrm (fan-triangulated from its centroid). */
const plate = (m: number, outline: THREE.Vector3[], _hint: THREE.Vector3, th: number, b: number[], w: number[], width?: number[]) => {
  const n = outline.length, c = outline.reduce((a, p) => a.add(p), v3(0, 0)).multiplyScalar(1 / n);
  const nrm = v3(0, 0);                                                     // Newell's normal of the outline
  for (let j = 0; j < n; j++) { const p = outline[j], q = outline[(j + 1) % n]; nrm.x += (p.y - q.y) * (p.z + q.z); nrm.y += (p.z - q.z) * (p.x + q.x); nrm.z += (p.x - q.x) * (p.y + q.y); }
  const off = nrm.normalize().multiplyScalar(th / 2);
  // bevel: the faces are inset slightly so the rim reads rounded
  const top = outline.map((p) => vert(p.clone().lerp(c, 0.04).add(off), b, w)), bot = outline.map((p) => vert(p.clone().lerp(c, 0.04).sub(off), b, w));
  const rimA = outline.map((p) => vert(p.clone().add(off.clone().multiplyScalar(0.35)), b, w)), rimB = outline.map((p) => vert(p.clone().sub(off.clone().multiplyScalar(0.35)), b, w));
  const ct = vert(c.clone().add(off), b, w), cb = vert(c.clone().sub(off), b, w);
  if (width) [top, bot, rimA, rimB].forEach((ring) => ring.forEach((vi, j) => buf.ow.set(vi, width[j])));
  for (let j = 0; j < n; j++) {
    const k = (j + 1) % n;
    buf.idx[m].push(ct, top[j], top[k], cb, bot[k], bot[j]);
    quad(m, top[j], rimA[j], rimA[k], top[k]); quad(m, rimA[j], rimB[j], rimB[k], rimA[k]); quad(m, rimB[j], bot[j], bot[k], rimB[k]);
  }
};
const sphere = (m: number, c: THREE.Vector3, r: number, b: number[], w: number[], seg = 16) => {
  const rings: {pts: THREE.Vector3[]; b: number[]; w: number[]}[] = [];
  for (let i = 1; i < seg; i++) {
    const ph = Math.PI * i / seg, pts: THREE.Vector3[] = [];
    for (let j = 0; j < seg * 2; j++) { const th = Math.PI * j / seg; pts.push(v3(c.x + r * Math.sin(ph) * Math.cos(th), c.y + r * Math.cos(ph), c.z + r * Math.sin(ph) * Math.sin(th))); }
    rings.push({pts, b, w});
  }
  // poles
  const top = vert(v3(c.x, c.y + r, c.z), b, w), bot = vert(v3(c.x, c.y - r, c.z), b, w);
  const n = seg * 2, ids = rings.map((rr) => rr.pts.map((p) => vert(p, b, w)));
  for (let i = 0; i < ids.length - 1; i++) for (let j = 0; j < n; j++) quad(m, ids[i][j], ids[i][(j + 1) % n], ids[i + 1][(j + 1) % n], ids[i + 1][j]);
  for (let j = 0; j < n; j++) { buf.idx[m].push(top, ids[0][(j + 1) % n], ids[0][j]); buf.idx[m].push(bot, ids[ids.length - 1][j], ids[ids.length - 1][(j + 1) % n]); }
};

// spine weights: a tent around each bone's midpoint (the smooth gradient of a weight-painted tail)
const spineW = (s: number): [number[], number[]] => {
  const f = s / SEG - 0.5, i0 = Math.floor(f), a = f - i0;
  const idx = (i: number) => B.spine[Math.max(0, Math.min(SPINE - 1, i))];   // the neck rides on Spine_00, which is pinned to the head
  if (i0 >= SPINE - 1) return [[B.spine[SPINE - 1]], [1]];
  return [[idx(i0), idx(i0 + 1)], [1 - a, a]];
};

export const BODY_RINGS = {start: 0, count: 0, per: 0};
// ---------------------------------------------------------------- body core (elliptical tube + round tail cap)
{
  const width = (s: number) => P.w0 + (P.w1 - P.w0) * Math.max(0, Math.min(1, s / BODY_LEN));
  const rings: {pts: THREE.Vector3[]; b: number[]; w: number[]}[] = [];
  const NS = 18, ringAt = (s: number, sc = 1) => {
    const f = frameOn(BIND_C, BIND_CUM, Math.min(s, BODY_LEN)), hh = width(s) / 2 * sc, pts: THREE.Vector3[] = [];
    const o = s > BODY_LEN ? f.o.clone().addScaledVector(f.x, s - BODY_LEN) : s < 0 ? f.o.clone().addScaledVector(f.x, s) : f.o;
    for (let j = 0; j < NS; j++) { const th = 2 * Math.PI * j / NS; pts.push(o.clone().addScaledVector(f.y, Math.cos(th) * hh).addScaledVector(f.z, Math.sin(th) * hh * BODY_W)); }
    const [b, w] = spineW(Math.max(0, Math.min(BODY_LEN - 1e-3, s)));
    rings.push({pts, b, w});
  };
  for (let i = 0; i <= 64; i++) ringAt(BODY_LEN * i / 64);              // starts flat at the neck, as the logo's core does
  for (let i = 1; i <= 6; i++) { const ph = Math.PI / 2 * i / 6.4; ringAt(BODY_LEN + Math.sin(ph) * P.w1 / 2, Math.cos(ph)); }
  BODY_RINGS.start = buf.pos.length / 3; BODY_RINGS.count = rings.length; BODY_RINGS.per = NS;
  gridTube(0, rings, true, true);
}

// ---------------------------------------------------------------- flaps: the rig's own leaves, raised into a V
const cot = 1 / Math.tan(DIHEDRAL * Math.PI / 180);
{
  const rigStraight: Pt[] = arcCurve(P.C[0], 180, BODY_LEN, 0), rc = measure(rigStraight);
  SLOTS.forEach((t, i) => {
    const [[x, y], [tx, ty]] = along(rigStraight, rc, t), w = (P.w0 + (P.w1 - P.w0) * t) / 2, side = -1;
    const nx = side * ty, ny = -side * tx, ang = Math.atan2(ty, tx) * 180 / Math.PI - side * P.sweep;
    const L = P.paddleLen * (1 - 0.3 * t), wb = P.paddleW * (1 - 0.3 * t);
    const local = paddle(L, wb, P.wt), leaf = xform(local, ang, x + nx * w * 0.15 - tx * 6, y + ny * w * 0.15 - ty * 6).map(M);
    const f = frameOn(BIND_C, BIND_CUM, t * BODY_LEN);
    for (const [zs, bi] of [[1, B.flapsL[i]], [-1, B.flapsR[i]]] as [number, number][]) {
      // in the bone frame: u along the body, v up; the leaf leaves the body at DIHEDRAL above horizontal
      // pitch: the leaf turns about its own long axis, so it shows its face from the front (side view unchanged)
      const pts = leaf.map(([px, py], j) => {
        const d = v3(px, py).sub(f.o), u = d.dot(f.x), v = d.dot(f.y), across = local[j][1] * Math.min(1, local[j][0] / 18);
        return f.o.clone().addScaledVector(f.x, u).addScaledVector(f.y, v).addScaledVector(f.z, -zs * (Math.max(0, v) * cot + across * PITCH));
      });
      const nrm = f.x.clone().cross(f.y.clone().addScaledVector(f.z, -zs * cot)).normalize();
      // no outline where the flap leaves the body (it would draw a seam across the back); full width beyond
      const halfW = (P.w0 + (P.w1 - P.w0) * t) / 2, width = leaf.map(([px, py]) => {
        const v = v3(px, py).sub(f.o).dot(f.y), u = (v - halfW * 0.6) / (halfW * 0.8);
        return Math.max(0, Math.min(1, u * u * (3 - 2 * u)));
      });
      plate(0, pts, nrm, 6, [bi], [1], width);
    }
  });
}

// ---------------------------------------------------------------- tail fan
{
  const rigStraight: Pt[] = arcCurve(P.C[0], 180, BODY_LEN, 0), rc = measure(rigStraight);
  const [[ex, ey], [etx, ety]] = along(rigStraight, rc, 1), axis = Math.atan2(ety, etx) * 180 / Math.PI;
  const f = frameOn(BIND_C, BIND_CUM, BODY_LEN);
  TAIL_PAIRS.forEach(([t, half, L, wb], k) => {
    const d = (1 - t) * BODY_LEN * 0.55 + 4;
    [-1, 1].forEach((side, j) => {
      const blade2 = xform(paddle(L * P.tailScale, wb * P.tailScale, 0.42), axis + side * half * P.tailSpread, ex - etx * d, ey - ety * d).map(M);
      const tilt = Math.tan(FIN_TILT * Math.PI / 180) * (k % 2 ? 1 : -1) * side;
      const pts = blade2.map(([px, py]) => {
        const dd = v3(px, py).sub(f.o), u = dd.dot(f.x), v = dd.dot(f.y);
        return f.o.clone().addScaledVector(f.x, u).addScaledVector(f.y, v).addScaledVector(f.z, Math.abs(v) * tilt);
      });
      const vdir = side > 0 ? -1 : 1;     // which way the blade leaves the axis
      const nrm = f.x.clone().cross(f.y.clone().multiplyScalar(vdir).addScaledVector(f.z, tilt)).normalize();
      plate(0, pts, nrm, 5, [B.fins[k * 2 + j]], [1]);
    });
  });
}

// ---------------------------------------------------------------- head: lofted from the shield outline
export const SHIELD: Pt[] = pathSegs([-58, -46], [[[-20, -47], [22, -47], [52, -46]], [[64, -46], [70, -38], [70, -26]],
  [[70, 6], [52, 36], [28, 50]], [[16, 57], [2, 56], [-10, 49]], [[-42, 32], [-72, 8], [-72, -26]],
  [[-72, -38], [-66, -46], [-58, -46]]], 30).map(([x, y]) => [x, -y] as Pt);   // model: y up
{
  const xs = SHIELD.map((p) => p[0]), x0 = Math.min(...xs), x1 = Math.max(...xs), xc = (x0 + x1) / 2, hx = (x1 - x0) / 2;
  const cutY = (x: number) => {
    const ys: number[] = [];
    for (let i = 0; i < SHIELD.length - 1; i++) {
      const [a, b] = [SHIELD[i], SHIELD[i + 1]];
      if ((a[0] - x) * (b[0] - x) <= 0 && a[0] !== b[0]) ys.push(a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]));
    }
    return [Math.min(...ys), Math.max(...ys)];
  };
  const rings: {pts: THREE.Vector3[]; b: number[]; w: number[]}[] = [], NS = 22, NX = 28;
  for (let i = 0; i <= NX; i++) {
    const u = -Math.cos(Math.PI * i / NX), x = xc + u * hx * 0.999;
    const [lo, hi] = cutY(x), yc = (lo + hi) / 2, hh = (hi - lo) / 2;
    const hw = 62 * Math.pow(Math.max(0, 1 - Math.pow(Math.abs(u), 2.6)), 1 / 2.6), pts: THREE.Vector3[] = [];
    for (let j = 0; j < NS; j++) {
      const th = 2 * Math.PI * j / NS, c = Math.cos(th), s = Math.sin(th), e = 2.4;
      const cy = Math.sign(c) * Math.pow(Math.abs(c), 2 / e), sz = Math.sign(s) * Math.pow(Math.abs(s), 2 / e);
      pts.push(v3(x, yc + cy * hh, sz * hw));
    }
    rings.push({pts, b: [B.head], w: [1]});
  }
  gridTube(0, rings, true, true);
}

// ---------------------------------------------------------------- eyes: stalks, eyeballs, pupils facing the viewer
{
  const target = M(P.eyes[0].target);
  EYE_DEF.forEach((e, k) => {
    if (e.stalk) {
      const rings: {pts: THREE.Vector3[]; b: number[]; w: number[]}[] = [], ax = e.centre.clone().sub(e.base), L = ax.length();
      ax.normalize();
      const a1 = new THREE.Vector3(0, 0, 1).cross(ax).normalize(), a2 = ax.clone().cross(a1);
      for (let i = 0; i <= 6; i++) {
        const c = e.base.clone().addScaledVector(ax, L * i / 6), rr = e.r * 0.78 / 2, pts: THREE.Vector3[] = [];
        for (let j = 0; j < 14; j++) { const th = 2 * Math.PI * j / 14; pts.push(c.clone().addScaledVector(a1, Math.cos(th) * rr).addScaledVector(a2, Math.sin(th) * rr)); }
        rings.push({pts, b: [B.stalks[k]], w: [1]});
      }
      gridTube(0, rings, true, false);
    }
    sphere(1, e.centre, e.r, [B.eyes[k]], [1], 11);
    // pupil: a cap whose projection is the logo's pupil (0.46 r, offset 0.3 r toward the target)
    const dx = target[0] - e.centre.x, dy = target[1] - e.centre.y, mm = Math.hypot(dx, dy) || 1;
    const g = v3(dx / mm * 0.3, dy / mm * 0.3, Math.sqrt(1 - 0.09)).normalize();
    const a1 = new THREE.Vector3(0, 1, 0).cross(g).normalize(), a2 = g.clone().cross(a1), cap = Math.asin(0.46), n = 18, R = e.r * 1.008;
    const c0 = vert(e.centre.clone().addScaledVector(g, R), [B.eyes[k]], [1]);
    const rings = [1, 2, 3].map((q) => Array.from({length: n}, (_, j) => {
      const th = 2 * Math.PI * j / n, a = cap * q / 3, dir = g.clone().multiplyScalar(Math.cos(a)).addScaledVector(a1, Math.sin(a) * Math.cos(th)).addScaledVector(a2, Math.sin(a) * Math.sin(th));
      return vert(e.centre.clone().addScaledVector(dir, R), [B.eyes[k]], [1]);
    }));
    for (let j = 0; j < n; j++) buf.idx[2].push(c0, rings[0][j], rings[0][(j + 1) % n]);
    for (let q = 0; q < 2; q++) for (let j = 0; j < n; j++) quad(2, rings[q][j], rings[q + 1][j], rings[q + 1][(j + 1) % n], rings[q][(j + 1) % n]);
  });
}

// ---------------------------------------------------------------- proboscis (round tube) and claw
{
  // the approved mascot's hose rings (curl('full'): at 42/56/68/79% of the tube, 2.8 units wide), kept as shallow
  // grooves: 6% of the radius, so they show as a faint line in the shading and never break the silhouette
  const RINGS = CURL_FULL.hose.rings, RW = CURL_FULL.hose.ringW;
  const ts: number[] = [];
  for (let i = 0; i <= 40; i++) ts.push(i / 40);
  RINGS.forEach((c) => [-1.5, -0.75, 0, 0.75, 1.5].forEach((d) => ts.push(c + d * RW / HOSE_LEN)));
  ts.sort((a, b) => a - b);
  const groove = (u: number) => RINGS.reduce((m, c) => Math.max(m, Math.exp(-Math.pow((u - c) * HOSE_LEN / (RW * 0.6), 2))), 0);
  const rings: {pts: THREE.Vector3[]; b: number[]; w: number[]}[] = [], hs = HOSE_LEN / HOSE_N, radial: THREE.Vector3[][] = [];
  for (const u of ts) {
    const s = HOSE_LEN * u, f = hoseFrame(s), r = (P.hose.w0 + (P.hose.w1 - P.hose.w0) * u) / 2 * (1 - 0.06 * groove(u)), pts: THREE.Vector3[] = [];
    for (let j = 0; j < 14; j++) { const th = 2 * Math.PI * j / 14; pts.push(f.o.clone().addScaledVector(f.y, Math.cos(th) * r).addScaledVector(f.z, Math.sin(th) * r)); }
    radial.push(pts.map((p) => p.clone().sub(f.o).normalize()));
    const ff = s / hs - 0.5, i0 = Math.floor(ff), a = ff - i0, bi = (q: number) => (q < 0 ? B.head : B.hose[Math.min(HOSE_N - 1, q)]);
    rings.push(i0 >= HOSE_N - 1 ? {pts, b: [B.hose[HOSE_N - 1]], w: [1]} : {pts, b: [bi(i0), bi(i0 + 1)], w: [1 - a, a]});
  }
  const first = buf.pos.length / 3;
  gridTube(0, rings, true, true);
  radial.forEach((ring, i) => ring.forEach((n, j) => buf.on.set(first + i * ring.length + j, n)));
  const c = P.claw, bl = blade(66, c.wdt, 5, 3.4, c.teethAmt);
  const up = xform(bl, -c.openUp, 0, 0), lo = xform(bl.map(([x, y]) => [x, -y] as Pt), c.openLo, 0, 0);
  const place = (q: Pt[]) => xform(q, c.ang, c.x, c.y, c.s).map(M);
  plate(1, place(up).map(([x, y]) => v3(x, y)), v3(0, 0, 1), 10, [B.clawUp], [1]);
  plate(1, place(lo).map(([x, y]) => v3(x, y)), v3(0, 0, 1), 10, [B.clawLo], [1]);
  const [kx, ky] = place([[-3, 0]])[0];
  sphere(1, v3(kx, ky), 10 * c.s, [B.clawUp, B.clawLo], [0.5, 0.5], 10);
}

// ---------------------------------------------------------------- the three.js objects
export const COLORS = {light: {body: 0x2a3142, lit: 0x4b5878, amber: 0xf5b83d, amberLit: 0xffcf6b, pupil: 0x2a3142, bg: 0xfffaf2},
  dark: {body: 0xffffff, lit: 0xffffff, amber: 0xf5b83d, amberLit: 0xffcf6b, pupil: 0x2a3142, bg: 0x1e2230}};
const VS = `#include <common>
#include <skinning_pars_vertex>
uniform float uOutline;
#ifdef OUTLINE
attribute vec3 onormal;
#endif
varying vec3 vN;
void main() {
  #include <beginnormal_vertex>
  float ow = 1.0;
  #ifdef OUTLINE
  objectNormal = onormal; ow = length(onormal);
  #endif
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <defaultnormal_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
  transformed += normalize(objectNormal) * uOutline * ow;
  #include <project_vertex>
  vN = normalize(transformedNormal);
}`;
const FS = `uniform vec3 uLit, uShade, uL; uniform float uFlat; varying vec3 vN;
void main() { float d = dot(normalize(vN), normalize(uL)); float k = smoothstep(0.05, 0.12, d) * (1.0 - uFlat);
  gl_FragColor = vec4(mix(uShade, uLit, k), 1.0);
  #include <colorspace_fragment>
}`;
export const toon = (lit: number, shade: number, outline = 0) => new THREE.ShaderMaterial({
  vertexShader: VS, fragmentShader: FS, side: outline ? THREE.BackSide : THREE.FrontSide, defines: outline ? {OUTLINE: 1} : {},
  uniforms: {uLit: {value: new THREE.Color(lit)}, uShade: {value: new THREE.Color(shade)}, uL: {value: new THREE.Vector3(-0.4, 0.75, 0.55)},
    uFlat: {value: 0}, uOutline: {value: outline}},
});

export const buildOpa = (theme: 'light' | 'dark' = 'light') => {
  const col = COLORS[theme];
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(buf.pos, 3));
  g.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(buf.sk, 4));
  g.setAttribute('skinWeight', new THREE.Float32BufferAttribute(buf.sw, 4));
  const all: number[] = [];
  buf.idx.forEach((ix, m) => { g.addGroup(all.length, ix.length, m); all.push(...ix); });
  g.setIndex(all);
  g.computeVertexNormals();
  const on = Float32Array.from(g.getAttribute('normal').array as Float32Array);
  buf.on.forEach((n, i) => { on[i * 3] = n.x; on[i * 3 + 1] = n.y; on[i * 3 + 2] = n.z; });
  buf.ow.forEach((w, i) => { on[i * 3] *= w; on[i * 3 + 1] *= w; on[i * 3 + 2] *= w; });
  g.setAttribute('onormal', new THREE.BufferAttribute(on, 3));
  const bones = BONES.map((d) => { const b = new THREE.Bone(); b.name = d.name; return b; });
  BONES.forEach((d, i) => { if (d.parent >= 0) bones[d.parent].add(bones[i]); });
  const mats = [toon(col.lit, col.body), toon(col.amberLit, col.amber), toon(col.pupil, col.pupil)];
  const mesh = new THREE.SkinnedMesh(g, mats);
  mesh.add(bones[0]);
  setWorld(bones, BIND);
  mesh.updateMatrixWorld(true);
  const skel = new THREE.Skeleton(bones, BIND.map((m) => m.clone().invert()));
  mesh.bind(skel, new THREE.Matrix4());
  const om = toon(col.bg, col.bg, 5), none = new THREE.MeshBasicMaterial({visible: false});
  const outline = new THREE.SkinnedMesh(g, [om, om, none]);
  outline.bind(skel, new THREE.Matrix4());
  mesh.frustumCulled = outline.frustumCulled = false;
  return {mesh, outline, bones, skel, tris: all.length / 3, verts: buf.pos.length / 3};
};

