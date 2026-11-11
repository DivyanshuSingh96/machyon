// Opa's arrival on the homepage: the approved V2 flight, played at the approved half speed (about 14.5 s).
// Loaded on demand by docs/assets/js/opa-arrival-loader.js, which decides whether it plays at all. Nothing here
// blocks the page: the canvas takes no clicks, and every way out (Skip, Esc, leaving the page, a hidden tab, a
// window resized to another width, reduced motion switched on, a lost WebGL context) leaves the logo exactly as it
// is without Opa.
//
// This is the page's side. The drawing (three.js) is in opa-arrival-render.js: in a worker, on the canvas handed to
// it with transferControlToOffscreen, when the browser supports that, so its work never runs on the page's main
// thread; in the page otherwise. Either way it draws the same frames at the same times.
import {measureHero} from './hero';
import {Palette, PALETTES} from './palette';
import {END, FADE, LEAD, SPEED, T, fadeAt, timeAt, wallMs} from './timeline';
import type {In, Out, Report, Session} from './render';

export type Controller = {stop: () => void};
type Options = {glb: string; t0: number; alive: () => boolean; onEnd: () => void};
type Link = {post: (m: In) => void; close: () => void};

const SCRIPT = (document.currentScript as HTMLScriptElement | null)?.src || location.href;
const RENDER = new URL('opa-arrival-render.js', SCRIPT).href;
const API: {run: typeof run; last: Report | null} = {run, last: null};

const palette = (): Palette => {
  const cs = getComputedStyle(document.body), dark = document.body.getAttribute('data-md-color-scheme') === 'slate';
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d, base = dark ? PALETTES.dark : PALETTES.light;
  return {body: v('--mx-logo-body', base.body), amber: v('--mx-amber', base.amber), pupil: v('--mx-logo-slate', base.pupil),
    bg: v('--md-default-bg-color', base.bg), dark};
};
const canWork = () => typeof Worker === 'function' && typeof OffscreenCanvas === 'function'
  && typeof HTMLCanvasElement.prototype.transferControlToOffscreen === 'function';

let pageScript: Promise<void> | null = null;
const loadPageRenderer = () => pageScript ??= new Promise<void>((ok, fail) => {
  const s = document.createElement('script');
  s.src = RENDER; s.async = true; s.onload = () => ok(); s.onerror = () => { pageScript = null; fail(new Error('renderer')); };
  document.head.append(s);
});

/** Starts the renderer in a worker. Returns null if no worker can be started; `failed` is called if the worker
    can't load or draw here (the page then tries itself); afterwards every message goes to `onOut`. */
function inWorker(canvas: HTMLCanvasElement, init: In, onOut: (m: Out) => void, failed: () => void): Link | null {
  let worker: Worker, off: OffscreenCanvas;
  try {
    off = canvas.transferControlToOffscreen();
    worker = new Worker(RENDER);
  } catch { return null; }
  let loaded = false;
  const giveUp = () => { worker.terminate(); if (!loaded) { loaded = true; failed(); } };
  worker.onerror = (e) => { e.preventDefault(); if (loaded) onOut({type: 'failed', message: 'worker'}); else giveUp(); };
  worker.onmessage = (e: MessageEvent<Out>) => {
    if (!loaded) {
      if (e.data.type === 'failed') { giveUp(); return; }
      loaded = true;
    }
    onOut(e.data);
  };
  worker.postMessage({...init, canvas: off}, [off]);
  return {post: (m) => worker.postMessage(m), close: () => worker.terminate()};
}

async function inPage(canvas: HTMLCanvasElement, init: In, onOut: (m: Out) => void): Promise<Link> {
  await loadPageRenderer();
  const s: Session = (window as any).MachyonOpaRender.session(canvas, onOut);
  s.handle(init);
  return {post: (m) => s.handle(m), close: () => s.handle({type: 'stop'})};
}

export async function run(o: Options): Promise<Controller> {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  let canvas = document.createElement('canvas');
  canvas.className = 'mx-arrival'; canvas.setAttribute('aria-hidden', 'true');
  // in page coordinates, over the viewport as it is now: if the visitor scrolls, Opa scrolls with the logo
  const body = document.body, br = body.getBoundingClientRect(), positioned = getComputedStyle(body).position !== 'static';
  Object.assign(canvas.style, {left: `${positioned ? -br.left : scrollX}px`, top: `${positioned ? -br.top : scrollY}px`, width: `${vw}px`, height: `${vh}px`, opacity: '0'});
  body.append(canvas);

  let link: Link | null = null, raf = 0, timer = 0, done = false, ended = false, begin = NaN, shown = -1;
  const skip = document.createElement('button');
  const opa = () => document.querySelector<SVGGElement>('.mx-hero-mark .mx-opa');
  const width0 = vw;
  const finish = () => {
    if (done) return;
    done = true;
    cancelAnimationFrame(raf); clearTimeout(timer);
    link?.close(); link = null;
    canvas.remove(); skip.remove();
    opa()?.style.removeProperty('opacity');
    document.documentElement.classList.remove('mx-arriving');
    removeEventListener('keydown', onKey); removeEventListener('resize', onResize);
    document.removeEventListener('visibilitychange', onHide); motion.removeEventListener('change', onMotion); themeObs.disconnect();
    o.onEnd();
  };
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') finish(); };
  const onResize = () => { if (document.documentElement.clientWidth !== width0) finish(); };   // phones change height while scrolling
  const onHide = () => { if (document.hidden) finish(); };
  const motion = matchMedia('(prefers-reduced-motion: reduce)'), onMotion = () => { if (motion.matches) finish(); };
  const themeObs = new MutationObserver(() => link?.post({type: 'theme', palette: palette()}));
  const ctl: Controller = {stop: finish};

  // Messages from the renderer. Until it is ready they settle `started`; afterwards they only end or report.
  let settle: (ok: boolean) => void = () => {};
  const started = new Promise<boolean>((ok) => { settle = ok; });
  const onOut = (m: Out) => {
    if (done) return;
    if (m.type === 'loaded') {                                  // measure the logo once the model is here, as before
      const meas = o.alive() ? measureHero(document, vw, vh) : null;
      if (meas) link?.post({type: 'layout', measure: meas}); else settle(false);
    } else if (m.type === 'ready') { API.last = m.report; settle(true); }
    else if (m.type === 'report') API.last = m.report;
    else if (m.type === 'done') { API.last = m.report; if (ended) finish(); }
    else { settle(false); finish(); }                           // 'failed' or 'lost': the logo, as always
  };
  const page = async () => {
    try { link = await inPage(canvas, init, onOut); } catch { settle(false); }
  };

  const init: In = {type: 'init', glb: o.glb, palette: palette(), pixelRatio: Math.min(2, window.devicePixelRatio || 1)};
  link = canWork() ? inWorker(canvas, init, onOut, () => {
    const fresh = canvas.cloneNode() as HTMLCanvasElement;       // the worker can't draw here: a fresh canvas, in the page
    canvas.replaceWith(fresh); canvas = fresh;
    void page();
  }) : null;
  if (!link) {
    if (canWork()) {                                            // the transfer or the worker failed outright
      const fresh = canvas.cloneNode() as HTMLCanvasElement;
      canvas.replaceWith(fresh); canvas = fresh;
    }
    void page();
  }
  if (!(await started) || done || !o.alive() || !link) { finish(); return ctl; }

  skip.type = 'button'; skip.className = 'mx-arrival-skip'; skip.textContent = 'Skip intro';
  skip.addEventListener('click', finish);
  body.append(skip);
  addEventListener('keydown', onKey); addEventListener('resize', onResize);
  document.addEventListener('visibilitychange', onHide); motion.addEventListener('change', onMotion);
  themeObs.observe(document.body, {attributes: true, attributeFilter: ['data-md-color-scheme']});

  begin = Math.max(wallMs(), performance.timeOrigin + o.t0 + LEAD * 1000);
  link.post({type: 'go', begin});

  // The page only changes the canvas's opacity (fading in) and the logo's (the cross-fade at the end): it wakes for
  // those moments and sleeps through the flight in between.
  const at = (t: number) => begin + t / SPEED * 1000;          // wall-clock ms of arrival time t
  const tick = () => {
    if (done) return;
    const t = timeAt(wallMs(), begin), f = fadeAt(Math.min(Math.max(t, 0), END));
    if (f.opacity !== shown) { canvas.style.opacity = String(f.opacity); shown = f.opacity; }
    if (f.logo > 0) {                                          // the cross-fade from Opa to the logo's own symbol
      document.documentElement.classList.remove('mx-arriving');
      opa()?.style.setProperty('opacity', String(f.logo), 'important');
    }
    if (t >= END) { ended = true; timer = window.setTimeout(finish, 300); return; }   // or sooner, when the renderer is done
    const wake = t < 0 ? at(0) : t >= T.appear && t < END - FADE ? at(END - FADE) : 0;
    if (wake) timer = window.setTimeout(() => { raf = requestAnimationFrame(tick); }, Math.max(0, wake - wallMs() - 20));
    else raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return ctl;
}

(window as any).MachyonOpaArrival = API;
