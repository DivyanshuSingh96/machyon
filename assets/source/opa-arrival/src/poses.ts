// Film-only poses built from the approved presets. Nothing here changes CURL_FULL, CURL_MID or ARC_FULL.
import {Pose, CURL_FULL, ARC_FULL, SWIM, BOXED, mirrorPose, arcCurve} from './rig';
import {Pt, bez, measure} from './geom';

const arcLen = (p: Pt[]) => measure(p)[p.length - 1];
export const BODY_LEN = arcLen(CURL_FULL.C);
/** Proboscis lengths along the tube (rig units): the Curl's rest length and the approved Arc's, the longest approved reach. */
export const HOSE_REST = arcLen(CURL_FULL.hose.T);
export const HOSE_ARC = arcLen(ARC_FULL.hose.T);
export const hoseLen = (p: Pose) => arcLen(p.hose.T);
export const HEAD_C: Pt = [250, 192];            // head centre in rig units, the same in every pose below

/** Swimming left: the whole swim pose seen from the other side (head yaw 180, never a flip at render time). */
export const SWIM_L: Pose = mirrorPose(SWIM, HEAD_C[0]);

/** Turn key: head facing the viewer (yaw 90), body swung down beneath it in one C, top view, proboscis hanging. */
export const TURN: Pose = {
  ...CURL_FULL, C: arcCurve([250, 214], 95, BODY_LEN, -135), roll: 0, sweep: 52, ripple: 3, tailSpread: 0.9,
  hose: {...CURL_FULL.hose, T: bez([250, 224], [252, 252], [258, 286], [262, 312], 80)},
  claw: {...CURL_FULL.claw, x: 263, y: 314, ang: 84, openUp: 10, openLo: 10},
  head: {...CURL_FULL.head, yaw: 90},
};

/** Standing in the box: body hanging straight down out of sight, lobes and tail tucked. */
export const STAND: Pose = {...BOXED, C: arcCurve([214, 222], 96, BODY_LEN, -8)};
