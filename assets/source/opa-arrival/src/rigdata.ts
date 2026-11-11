// Opa's skeleton and rest data, from the approved 2D rig. Shared by the mesh generator and the runtime player.
// Model space: rig units; x = rig x - 250 (head faces +x), y = 192 - rig y (up), z toward the viewer of the logo.
// Every part's side projection in the curl pose is the logo's own shape; depth is added across the body only.
import {Bone, Matrix4, Quaternion, Vector3} from 'three';
import {Pt, measure, along, xform, bez} from './geom';
import {CURL_MID, CURL_FULL, TAIL_PAIRS, arcCurve} from './rig';

export const SPINE = 16;                 // spine bones, head to tail tip
export type V3 = [number, number, number];

export const M = ([x, y]: Pt): [number, number] => [x - 250, 192 - y];           // rig -> model (2D, z = 0)
export const P = CURL_MID;
export const CURVE_C: Pt[] = P.C.map(M);
export const BODY_LEN = measure(P.C).slice(-1)[0];
export const HOSE_C: Pt[] = P.hose.T.map(M);
export const HOSE_LEN = measure(P.hose.T).slice(-1)[0];
export const SLOTS = [...new Set(P.slots.map((s) => +s.toFixed(6)))].sort((a, b) => a - b);   // 9 distinct flap stations
export const FLAPS = SLOTS.length;

// ---------------------------------------------------------------- bones
export type BoneDef = {name: string; parent: number; group: string};
export const BONES: BoneDef[] = [];
const bone = (name: string, parent: number, group: string) => (BONES.push({name, parent, group}), BONES.length - 1);
export const B = {root: bone('Opa_Root', -1, 'root'), head: 0, spine: [] as number[], fins: [] as number[], stalks: [] as number[],
  eyes: [] as number[], hose: [] as number[], clawUp: 0, clawLo: 0, flapsL: [] as number[], flapsR: [] as number[]};
B.head = bone('Head', B.root, 'head');
for (let i = 0; i < SPINE; i++) B.spine.push(bone(`Spine_${String(i).padStart(2, '0')}`, i ? B.spine[i - 1] : B.head, 'spine'));
for (let k = 0; k < 3; k++) for (const s of ['L', 'R']) B.fins.push(bone(`TailFin_${s}${k + 1}`, B.spine[SPINE - 1], 'tail'));
for (let k = 0; k < 5; k++) { B.stalks.push(bone(`EyeStalk_${k + 1}`, B.head, 'eyes')); B.eyes.push(bone(`Eye_${k + 1}`, B.stalks[k], 'eyes')); }
export const HOSE_N = 5;
for (let i = 0; i < HOSE_N; i++) B.hose.push(bone(`Proboscis_${i + 1}`, i ? B.hose[i - 1] : B.head, 'proboscis'));
B.clawUp = bone('Claw_Upper', B.hose[HOSE_N - 1], 'proboscis');
B.clawLo = bone('Claw_Lower', B.hose[HOSE_N - 1], 'proboscis');
for (let i = 0; i < FLAPS; i++) {
  const sp = B.spine[Math.min(SPINE - 1, Math.floor(SLOTS[i] * SPINE))];
  B.flapsL.push(bone(`Flap_L${i + 1}`, sp, 'flaps')); B.flapsR.push(bone(`Flap_R${i + 1}`, sp, 'flaps'));
}

// ---------------------------------------------------------------- frames along a 2D model-space curve (side plane)
export type Frame = {o: Vector3; x: Vector3; y: Vector3; z: Vector3};
export const v3 = (x: number, y: number, z = 0) => new Vector3(x, y, z);
/** Point and frame at arc length s along a planar curve: x = tangent (toward the tail), y = dorsal, z = x × y. */
export const frameOn = (c: Pt[], cum: number[], s: number): Frame => {
  const L = cum[cum.length - 1], [p, t] = along(c, cum, Math.max(0, Math.min(1, s / L)));
  const x = v3(t[0], t[1]).normalize(), y = v3(t[1], -t[0]);
  return {o: v3(p[0], p[1]), x, y, z: x.clone().cross(y)};
};
export const mat = (f: Frame) => new Matrix4().makeBasis(f.x, f.y, f.z).setPosition(f.o);

/** The straight bind pose: body along -x from the neck (the curl's own start point). */
export const BIND_C: Pt[] = arcCurve(P.C[0], 180, BODY_LEN, 0).map(M);
export const BIND_CUM = measure(BIND_C);
export const SEG = BODY_LEN / SPINE;
export const FLAP_S: number[] = SLOTS.map((t) => t * BODY_LEN);
export const NECK = v3(...M(P.C[0]));
// ---------------------------------------------------------------- eyes: stalks, eyeballs, pupils facing the viewer
// Eye depth across the head. The 2D art fixes each eye's side-view position; depth is free. The film's pseudo-3D
// turn used rear +-30 / front +-18; the model splays them wider (rear +-50, front +-38) so the five read apart from the front.
export const EYE_Z = [-50, 50, 0, -38, 38];
const EYE_BASE_Z = 0.6;
const EYES = [[-48, -46, -72, -104, 27], [-14, -46, -10, -120, 29], [16, -46, 18, -70, 21, 0], [42, -46, 58, -102, 20], [62, -40, 96, -70, 20]];
export const PUPIL_DIR: Vector3[] = [];
export const EYE_DEF = EYES.map(([bx, by, ex, ey, r, st = 1], k) => ({
  base: v3(bx, -by, -EYE_Z[k] * EYE_BASE_Z), centre: v3(ex, -ey, -EYE_Z[k]), r, stalk: st > 0.5,
}));
{
  const target = M(P.eyes[0].target);
  EYE_DEF.forEach((e) => {
    const dx = target[0] - e.centre.x, dy = target[1] - e.centre.y, mm = Math.hypot(dx, dy) || 1;
    PUPIL_DIR.push(v3(dx / mm * 0.3, dy / mm * 0.3, Math.sqrt(1 - 0.09)).normalize());   // the logo's pupil: 0.3 r toward the target
  });
}
export const HOSE_CUM = measure(HOSE_C);
export const hoseFrame = (s: number) => frameOn(HOSE_C, HOSE_CUM, s);
// ---------------------------------------------------------------- bind matrices (world, model space)
export const BIND: Matrix4[] = BONES.map(() => new Matrix4());
const spineBind = (j: number) => {
  const a = frameOn(BIND_C, BIND_CUM, j * SEG);
  return mat(a);
};
B.spine.forEach((bi, j) => BIND[bi].copy(spineBind(j)));
const endF = frameOn(BIND_C, BIND_CUM, BODY_LEN);
B.fins.forEach((bi) => BIND[bi].copy(mat(endF)));
FLAP_S.forEach((s, i) => { const m = mat(frameOn(BIND_C, BIND_CUM, s)); BIND[B.flapsL[i]].copy(m); BIND[B.flapsR[i]].copy(m); });
EYE_DEF.forEach((e, k) => {
  const x = e.centre.clone().sub(e.base).normalize(), z = new Vector3(0, 0, 1), y = z.clone().cross(x).normalize(), zz = x.clone().cross(y);
  BIND[B.stalks[k]].makeBasis(x, y, zz).setPosition(e.base);
  BIND[B.eyes[k]].makeBasis(x, y, zz).setPosition(e.centre);
});
B.hose.forEach((bi, i) => BIND[bi].copy(mat(hoseFrame(i * HOSE_LEN / HOSE_N))));
{
  const c = P.claw, [kx, ky] = M(xform([[-3, 0]], c.ang, c.x, c.y, c.s)[0]), a = -c.ang * Math.PI / 180;
  const m = new Matrix4().makeRotationZ(a).setPosition(kx, ky, 0);
  BIND[B.clawUp].copy(m); BIND[B.clawLo].copy(m);
}
export const CLAW_BIND_ANG = -P.claw.ang;

/** Pose: world (model-space) matrices for every bone -> local transforms down the hierarchy. */
export const setWorld = (bones: Bone[], W: Matrix4[]) => {
  BONES.forEach((d, i) => {
    const local = d.parent >= 0 ? W[d.parent].clone().invert().multiply(W[i]) : W[i].clone();
    local.decompose(bones[i].position, bones[i].quaternion, bones[i].scale);
  });
};

/** Bone world matrices for a pose: the spine laid along a planar curve, proboscis along a curve, claw placed. */
export const poseOnCurves = (spineC: Pt[], hoseC: Pt[] = HOSE_C, claw = {x: P.claw.x, y: P.claw.y, ang: P.claw.ang, s: P.claw.s},
  flap = (_i: number, _side: number) => new Matrix4()): Matrix4[] => {
  const W = BIND.map((m) => m.clone()), cum = measure(spineC);
  W[B.root].identity(); W[B.head].identity();
  B.spine.forEach((bi, j) => W[bi].copy(mat(frameOn(spineC, cum, j * SEG))));
  // each bone points along the chord to the next joint (keeps the skinned tube on the curve)
  B.spine.forEach((bi, j) => {
    // the first bone has no neighbour before it to average with, so it aims along the first half-segment
    const a = frameOn(spineC, cum, j * SEG).o, b = frameOn(spineC, cum, (j + (j ? 1 : 0.5)) * SEG).o, x = b.clone().sub(a).normalize();
    const y = v3(x.y, -x.x), z = x.clone().cross(y);
    W[bi].makeBasis(x, y, z).setPosition(a);
  });
  const ef = frameOn(spineC, cum, BODY_LEN);
  B.fins.forEach((bi) => W[bi].copy(mat(ef)));
  FLAP_S.forEach((s, i) => {
    const m = mat(frameOn(spineC, cum, s));
    W[B.flapsL[i]].copy(m).multiply(flap(i, 1)); W[B.flapsR[i]].copy(m).multiply(flap(i, -1));
  });
  const hc = measure(hoseC), hl = hc[hc.length - 1];
  B.hose.forEach((bi, i) => W[bi].copy(mat(frameOn(hoseC, hc, i * hl / HOSE_N * 1))));
  const [kx, ky] = M(xform([[-3, 0]], claw.ang, claw.x, claw.y, claw.s)[0]);
  const cm = new Matrix4().makeRotationZ(-claw.ang * Math.PI / 180).setPosition(kx, ky, 0);
  W[B.clawUp].copy(cm); W[B.clawLo].copy(cm);
  return W;
};
export {M as toModel, bez};

/** Turn every eye so its pupil faces a point (model space): eyes on the viewer. */
export const gaze = (W: Matrix4[], at: Vector3, amount = 1) => {
  EYE_DEF.forEach((e, k) => {
    const c = e.centre.clone().applyMatrix4(W[B.head]);
    const from = PUPIL_DIR[k].clone().transformDirection(W[B.head]), to = at.clone().sub(c).normalize();
    const q = new Quaternion().setFromUnitVectors(from, to);
    q.slerp(new Quaternion(), 1 - amount);
    const R = new Matrix4().makeRotationFromQuaternion(q);
    const T = new Matrix4().makeTranslation(c.x, c.y, c.z).multiply(R).multiply(new Matrix4().makeTranslation(-c.x, -c.y, -c.z));
    W[B.eyes[k]] = T.multiply(W[B.head].clone().multiply(BIND[B.eyes[k]]));
  });
};
