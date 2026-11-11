// The approved film's turn, curl, settle and eye-stalk dip (film frames 498-540 and the dip at 578), unchanged:
// the same poses, blends, easing and frame timings as the 2D film and V1. The 3D player reads them as curves.
import {Pt, lerp, lerpPt} from './geom';
import {Pose, blend, CURL_FULL, CURL_MID, SWIM, arcCurve, mirrorPose} from './rig';
import {TURN} from './poses';
import {clamp01, smooth, inOut, prog, FPS, T, turnA, curl, KEY, tailA, tailB, detailA, exact, DIP, DIP_LEN, END} from './timeline';

export {clamp01, smooth, inOut, prog, FPS, T, turnA, curl, KEY, tailA, tailB, detailA, exact, DIP, DIP_LEN, END, FADE} from './timeline';

const add = (a: Pt, b: Pt): Pt => [a[0] + b[0], a[1] + b[1]];
const mul = (a: Pt, s: number): Pt => [a[0] * s, a[1] * s];
const cbez = (a: Pt, b: Pt, c: Pt, d: Pt, t: number): Pt => {
  const u = 1 - t, k = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
  return [k[0] * a[0] + k[1] * b[0] + k[2] * c[0] + k[3] * d[0], k[0] * a[1] + k[1] * b[1] + k[2] * c[1] + k[3] * d[1]];
};

/** The film's swim pose, facing left (the lane). */
export const SWIM_L = mirrorPose(SWIM, 250);

export type FilmFrame = {
  body: Pose;            // the film's pose as drawn (spine curve, flap sweep, roll, tail fan, head yaw): screen-space rig units
  head: Pose;            // the same blend from the un-mirrored poses: proboscis, claw and eyes in the head's own frame
  H: Pt;                 // head centre, page px
  lift: number;          // eye-stalk length (the dip)
};

/** The film from turnA on. E = lane end, HF = final head centre (page px), k = page px per rig unit. */
export const filmAt = (t: number, E: Pt, HF: Pt, k: number): FilmFrame => {
  const ph = Math.floor(t * 12) / 12 * 13.2;
  const tu = prog(t, turnA, curl, (u) => u);
  const u1 = tu < KEY ? 1 - Math.cos(tu / KEY * Math.PI / 2) : Math.sin((tu - KEY) / (1 - KEY) * Math.PI / 2);
  let body = tu < KEY ? blend(SWIM_L, TURN, u1, 0.45) : blend(TURN, CURL_FULL, u1, 0.45);
  let head = tu < KEY ? blend(SWIM, TURN, u1) : blend(TURN, CURL_FULL, u1);
  const H = cbez(E, add(E, mul([-24 / 0.6294, 40 / 0.6294], k)), add(HF, mul([-30 / 0.6294, -20 / 0.6294], k)), HF,
    inOut(clamp01((t - T.laneEnd) / (32 / FPS))));
  const fan = 0.16 * Math.sin(Math.PI * prog(t, tailA, tailB, (u) => u));
  const fd = (t - detailA) * FPS, lobes = fd >= 0 ? 5 * Math.exp(-fd * 0.25) * Math.sin(fd * 0.9) : 0;
  body = {...body, ripplePhase: t < curl ? ph : body.ripplePhase, tailSpread: body.tailSpread + fan, sweep: body.sweep + lobes};
  const d = prog(t, detailA, exact);
  if (d > 0) {
    body = {...blend(body, CURL_MID, d), tailSpread: lerp(body.tailSpread, CURL_MID.tailSpread, d) + fan, sweep: lerp(body.sweep, CURL_MID.sweep, d) + lobes};
    head = blend(head, CURL_MID, d);
  }
  if (t >= exact) { body = CURL_MID; head = CURL_MID; }
  const lift = t >= DIP && t < DIP + DIP_LEN ? 1 - 0.3 * Math.sin(Math.PI * (t - DIP) / DIP_LEN) : 1;
  return {body, head, H, lift};
};

/** Only what the head needs from the film (position, yaw, roll): cheap enough to sample at the bake rate. */
export const filmHeadAt = (t: number, E: Pt, HF: Pt, k: number) => {
  const tu = prog(t, turnA, curl, (u) => u);
  const u1 = tu < KEY ? 1 - Math.cos(tu / KEY * Math.PI / 2) : Math.sin((tu - KEY) / (1 - KEY) * Math.PI / 2);
  let yaw = tu < KEY ? lerp(180, 90, u1) : lerp(90, 0, u1), roll = tu < KEY ? 0 : -u1;
  const d = prog(t, detailA, exact);
  yaw = lerp(yaw, 0, d); roll = lerp(roll, -1, d);
  if (t >= exact) { yaw = 0; roll = -1; }
  const H = cbez(E, add(E, mul([-24 / 0.6294, 40 / 0.6294], k)), add(HF, mul([-30 / 0.6294, -20 / 0.6294], k)), HF,
    inOut(clamp01((t - T.laneEnd) / (32 / FPS))));
  return {H, yaw, roll};
};

/** The film's swim pose for the lane (before turnA), so the spine can settle onto it. */
export const laneBody = (): Pose => SWIM_L;
export {arcCurve, lerpPt};
