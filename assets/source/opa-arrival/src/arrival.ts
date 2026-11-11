// Opa's arrival on the homepage: the approved V2 flight, played at the approved half speed (about 14.5 s).
// Loaded on demand by docs/assets/js/opa-arrival-loader.js, which decides whether it plays at all. Nothing here
// blocks the page: the canvas takes no clicks, and every way out (Skip, Esc, leaving the page, a hidden tab, a
// window resized to another width, reduced motion switched on) leaves the logo exactly as it is without Opa.
import {Player, measureHero} from './player';
import {Palette, PALETTES} from './shade';
import {END} from './film';

const SPEED = 0.5;                       // the approved pace: the whole arrival takes END / SPEED seconds
const LEAD = 1.3;                        // seconds after the page appears: the wordmark's letters have settled by then

export type Controller = {stop: () => void};
type Options = {glb: string; t0: number; alive: () => boolean; onEnd: () => void};

const palette = (): Palette => {
  const cs = getComputedStyle(document.body), dark = document.body.getAttribute('data-md-color-scheme') === 'slate';
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d, base = dark ? PALETTES.dark : PALETTES.light;
  return {body: v('--mx-logo-body', base.body), amber: v('--mx-amber', base.amber), pupil: v('--mx-logo-slate', base.pupil),
    bg: v('--md-default-bg-color', base.bg), dark};
};
const idle = () => new Promise<void>((ok) => ('requestIdleCallback' in window ? requestIdleCallback(() => ok(), {timeout: 600}) : setTimeout(ok, 0)));

export async function run(o: Options): Promise<Controller> {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const canvas = document.createElement('canvas');
  canvas.className = 'mx-arrival'; canvas.setAttribute('aria-hidden', 'true');
  // in page coordinates, over the viewport as it is now: if the visitor scrolls, Opa scrolls with the logo
  const body = document.body, br = body.getBoundingClientRect(), positioned = getComputedStyle(body).position !== 'static';
  Object.assign(canvas.style, {left: `${positioned ? -br.left : scrollX}px`, top: `${positioned ? -br.top : scrollY}px`, width: `${vw}px`, height: `${vh}px`, opacity: '0'});
  body.append(canvas);

  let player: Player | null = null, raf = 0, done = false;
  const skip = document.createElement('button');
  const opa = () => document.querySelector<SVGGElement>('.mx-hero-mark .mx-opa');
  const width0 = vw;
  const finish = () => {
    if (done) return;
    done = true;
    cancelAnimationFrame(raf);
    canvas.remove(); skip.remove();
    opa()?.style.removeProperty('opacity');
    document.documentElement.classList.remove('mx-arriving');
    removeEventListener('keydown', onKey); removeEventListener('resize', onResize);
    document.removeEventListener('visibilitychange', onHide); motion.removeEventListener('change', onMotion); themeObs.disconnect();
    player?.dispose(); player = null;
    o.onEnd();
  };
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') finish(); };
  const onResize = () => { if (document.documentElement.clientWidth !== width0) finish(); };   // phones change height while scrolling
  const onHide = () => { if (document.hidden) finish(); };
  const motion = matchMedia('(prefers-reduced-motion: reduce)'), onMotion = () => { if (motion.matches) finish(); };
  const themeObs = new MutationObserver(() => player?.theme(palette()));
  const ctl: Controller = {stop: finish};

  try {
    player = await Player.load(canvas, o.glb);
    if (done || !o.alive()) { finish(); return ctl; }
    const m = measureHero(document, vw, vh);
    if (!m) { finish(); return ctl; }
    player.theme(palette());
    await idle();                                              // the flight is precomputed while the page is idle
    if (done || !o.alive()) { finish(); return ctl; }
    player.layout(m);
  } catch {
    finish();                                                  // no WebGL2, a failed download: the logo, as always
    return ctl;
  }

  skip.type = 'button'; skip.className = 'mx-arrival-skip'; skip.textContent = 'Skip intro';
  skip.addEventListener('click', finish);
  body.append(skip);
  addEventListener('keydown', onKey); addEventListener('resize', onResize);
  document.addEventListener('visibilitychange', onHide); motion.addEventListener('change', onMotion);
  themeObs.observe(document.body, {attributes: true, attributeFilter: ['data-md-color-scheme']});

  const begin = Math.max(performance.now(), o.t0 + LEAD * 1000);
  const tick = (now: number) => {
    if (done || !player) return;
    const t = Math.max(0, (now - begin) / 1000 * SPEED), f = player.render(Math.min(t, END));
    if (f) {
      canvas.style.opacity = String(f.opacity);
      if (f.logo > 0) {                                        // the cross-fade from Opa to the logo's own symbol
        document.documentElement.classList.remove('mx-arriving');
        opa()?.style.setProperty('opacity', String(f.logo), 'important');
      }
    }
    if (t >= END) finish(); else raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return ctl;
}

(window as any).MachyonOpaArrival = {run};
