// Draws the arrival: in a worker on an OffscreenCanvas when the browser can, so drawing never holds up the page,
// otherwise in the page. The page's controller (arrival.ts) sends the layout, the palette and the start time, and
// keeps the canvas's opacity, the cross-fade to the logo and every way out; this side only draws.
//
// Quality. The approved look is antialiased, at min(2, devicePixelRatio). Before the flight starts, a few of its
// largest frames are drawn on a canvas nobody sees and timed. Only if those frames are clearly too slow for smooth
// motion (a computer drawing WebGL without graphics hardware, say) is antialiasing left off. During the flight,
// only a sustained run of slow frames lowers the drawing resolution, a step at a time, never below 0.75: Opa keeps
// flying the same path at the same speed, with fewer pixels. Nothing here ever skips the arrival.
import {Player, Target} from './player';
import type {Measure} from './choreo';
import type {Palette} from './palette';
import {END, timeAt, wallMs} from './timeline';

export type Init = {type: 'init'; glb: string; palette: Palette; pixelRatio: number; canvas?: OffscreenCanvas};
export type In = Init | {type: 'layout'; measure: Measure} | {type: 'go'; begin: number} | {type: 'theme'; palette: Palette} | {type: 'stop'};
export type Report = {mode: 'worker' | 'page'; antialias: boolean; pixelRatio: number; probeMs: number[];
  frames: number; medianFrameMs: number; steps: number};
export type Out = {type: 'loaded'} | {type: 'ready' | 'report' | 'done'; report: Report} | {type: 'lost'} | {type: 'failed'; message: string};
export type Session = {handle: (m: In) => void};

// The probe: the foreground pass (around t = 2 s) is where Opa covers the most screen. The first frame compiles the
// shaders and isn't counted.
export const PROBE_T = [2.0, 2.0, 1.8, 2.2, 2.6];
export const AA_BUDGET_MS = 25;          // an antialiased frame slower than this (median) can't move smoothly
// The governor: frames are "slow" when the median gap over the last WINDOW frames exceeds SLOW_MS (under 20 fps).
// 50 ms stays clear of browsers that pace animation at 30 fps to save power (33 ms).
export const SLOW_MS = 50, WINDOW = 24, WARMUP = 8, STEP = 0.75, MIN_RATIO = 0.75, MAX_STEPS = 3;

export const median = (xs: number[]) => {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b), h = s.length >> 1;
  return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
};
/** Antialias unless the timed probe frames (after the first) are clearly too slow. */
export const chooseAntialias = (probeMs: number[]) => !(probeMs.length > 1 && median(probeMs.slice(1)) > AA_BUDGET_MS);

/** One step of the frame-rate governor. Returns the new pixel ratio, or null to keep the current one. */
export const governor = () => {
  const gaps: number[] = [];
  let seen = 0, steps = 0;
  return {
    get steps() { return steps; },
    gap(ms: number, ratio: number): number | null {
      if (++seen <= WARMUP) return null;
      gaps.push(ms);
      if (gaps.length > WINDOW) gaps.shift();
      if (gaps.length < WINDOW || median(gaps) <= SLOW_MS || steps >= MAX_STEPS || ratio <= MIN_RATIO) return null;
      gaps.length = 0; steps++;
      return Math.max(MIN_RATIO, Math.round(ratio * STEP * 100) / 100);
    },
  };
};

type Env = {mode: 'worker' | 'page'; probeCanvas: () => Target; raf: (f: () => void) => number; caf: (h: number) => void;
  idle: () => Promise<void>};

export function session(canvas: Target, env: Env, emit: (m: Out) => void): Session {
  let player: Player | null = null, stopped = false, handle = 0, begin = NaN, last = 0;
  const gov = governor(), gaps: number[] = [];
  const report: Report = {mode: env.mode, antialias: true, pixelRatio: 1, probeMs: [], frames: 0, medianFrameMs: 0, steps: 0};
  const snapshot = (): Report => ({...report, medianFrameMs: Math.round(median(gaps) * 10) / 10, probeMs: [...report.probeMs]});

  const stop = () => { stopped = true; env.caf(handle); player?.dispose(); player = null; };
  const lost = (e: Event) => { e.preventDefault(); if (!stopped) { stop(); emit({type: 'lost'}); } };

  const fail = (e: unknown) => { stop(); emit({type: 'failed', message: e instanceof Error ? e.message : String(e)}); };

  async function init(m: Init) {
    try {
      if (env.mode === 'worker') {                              // some browsers hand over the canvas but can't draw on it here
        const gl = (env.probeCanvas() as OffscreenCanvas).getContext('webgl2');
        if (!gl) throw new Error('no WebGL2 in workers');
        gl.getExtension('WEBGL_lose_context')?.loseContext();
      }
      player = await Player.load(m.glb);
      if (stopped) return;
      player.theme(m.palette);
      report.pixelRatio = m.pixelRatio;
      emit({type: 'loaded'});                                   // the page measures the logo now, as it is
    } catch (e) { fail(e); }
  }

  async function layout(measure: Measure) {
    try {
      await env.idle();                                         // in the page, the flight is precomputed while idle
      if (stopped || !player) return;
      player.layout(measure);
      try {
        const ms = player.probe(env.probeCanvas(), report.pixelRatio, PROBE_T, AA_BUDGET_MS * 4);
        report.probeMs = ms.map((x) => Math.round(x * 10) / 10);
        report.antialias = chooseAntialias(ms);
      } catch { /* the probe couldn't run: keep the approved look */ }
      if (stopped) return;
      canvas.addEventListener('webglcontextlost', lost as EventListener);
      const r = player.attach(canvas, {antialias: report.antialias, pixelRatio: report.pixelRatio});
      r.compile(player.scene, player.camera);
      emit({type: 'ready', report: snapshot()});
    } catch (e) { fail(e); }
  }

  function frame() {
    if (stopped || !player) return;
    const now = wallMs(), t = timeAt(now, begin);
    if (t >= 0) {
      player.render(Math.min(t, END));
      report.frames++;
      if (last) {
        const gap = now - last;
        if (report.frames > WARMUP) gaps.push(gap);
        const next = gov.gap(gap, report.pixelRatio);
        if (next !== null) {
          report.pixelRatio = next; report.steps = gov.steps; player.setPixelRatio(next);
          emit({type: 'report', report: snapshot()});
        }
      }
      last = now;
    }
    if (t >= END) { emit({type: 'done', report: snapshot()}); return; }
    handle = env.raf(frame);
  }

  return {
    handle(m: In) {
      if (stopped) return;
      if (m.type === 'init') void init(m);
      else if (m.type === 'layout') void layout(m.measure);
      else if (m.type === 'go') { begin = m.begin; handle = env.raf(frame); }
      else if (m.type === 'theme') player?.theme(m.palette);
      else if (m.type === 'stop') stop();
    },
  };
}

// ---------------------------------------------------------------- where this script runs
declare const WorkerGlobalScope: any;
const scope: any = globalThis;
if (typeof WorkerGlobalScope !== 'undefined' && scope instanceof WorkerGlobalScope) {
  const raf = typeof scope.requestAnimationFrame === 'function'
    ? (f: () => void) => scope.requestAnimationFrame(f) as number : (f: () => void) => setTimeout(f, 16) as unknown as number;
  const caf = typeof scope.cancelAnimationFrame === 'function' ? (h: number) => scope.cancelAnimationFrame(h) : (h: number) => clearTimeout(h);
  let s: Session | null = null;
  scope.onmessage = (e: MessageEvent<In>) => {
    const m = e.data;
    if (m.type === 'init' && !s && m.canvas) {
      s = session(m.canvas, {mode: 'worker', probeCanvas: () => new OffscreenCanvas(1, 1), raf, caf, idle: async () => {}},
        (o) => scope.postMessage(o));
    }
    s?.handle(m);
  };
} else {
  const idle = () => new Promise<void>((ok) => ('requestIdleCallback' in window ? requestIdleCallback(() => ok(), {timeout: 600}) : setTimeout(ok, 0)));
  (window as any).MachyonOpaRender = {
    session: (canvas: HTMLCanvasElement, emit: (m: Out) => void) => session(canvas,
      {mode: 'page', probeCanvas: () => document.createElement('canvas'), raf: requestAnimationFrame.bind(window),
        caf: cancelAnimationFrame.bind(window), idle}, emit),
    // the quality rules, for tests (tests/test_site.py runs them under node)
    tuning: {chooseAntialias, governor, PROBE_T, AA_BUDGET_MS, SLOW_MS, WINDOW, WARMUP, STEP, MIN_RATIO, MAX_STEPS},
  };
}
