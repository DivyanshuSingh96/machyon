// The arrival's clock, without three.js or the rig: the controller on the page (arrival.ts) and the renderer
// (render.ts, in a worker or in the page) both read it, so the page's fades and the drawn frames agree.
export const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
export const smooth = (t: number) => { const u = clamp01(t); return u * u * (3 - 2 * u); };
export const inOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const prog = (t: number, a: number, b: number, e: (u: number) => number = inOut) => e(clamp01((t - a) / (b - a)));

// ---------------------------------------------------------------- timeline (seconds from the first visible frame)
export const FPS = 24;
export const T = {appear: 0.25, approachEnd: 2.0, plane: 2.6, laneEnd: 4.3};
export const turnA = T.laneEnd - 2 / FPS, curl = T.laneEnd + 32 / FPS, KEY = 14 / 34;    // film frames 498, 532 (lane ends at 500)
export const tailA = T.laneEnd + 22 / FPS, tailB = T.laneEnd + 36 / FPS, detailA = T.laneEnd + 24 / FPS, exact = T.laneEnd + 40 / FPS;
export const DIP = exact + 0.5, DIP_LEN = 10 / FPS, END = DIP + DIP_LEN + 0.35;
export const FADE = 0.25;                                        // the cross-fade from the 3D render to the hero logo

export const SPEED = 0.5;                // the approved pace: the whole arrival takes END / SPEED seconds
export const LEAD = 1.3;                 // seconds after the page appears: the wordmark's letters have settled by then

/** The canvas's opacity and the logo symbol's, at time t: Opa fades in, then cross-fades into the logo. */
export const fadeAt = (t: number) => {
  const logo = prog(t, END - FADE, END, smooth);
  return {opacity: smooth(t / T.appear) * (1 - logo), logo};
};
/** Wall-clock time shared by the page and a worker (each has its own performance.timeOrigin). */
export const wallMs = () => performance.timeOrigin + performance.now();
/** Arrival time t (seconds) at wall-clock `ms`, for an arrival whose first frame is at `begin`. */
export const timeAt = (ms: number, begin: number) => (ms - begin) / 1000 * SPEED;
