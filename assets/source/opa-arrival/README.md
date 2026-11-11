# Opa's arrival

The homepage animation: Opa flies in from far behind the page, toward the visitor, banks, crosses the screen, turns
above the "n", flies the film's N-to-M lane and curls into the logo, then dips its eye stalks. It plays at the
approved half speed (about 14.5 s, starting 1.3 s after the page appears).

## What runs where

| File | Role |
| --- | --- |
| `docs/assets/js/opa-arrival-loader.js` | On every page (about 3 KB, hand-written). Plays only on the homepage, once per visit (again after a reload), never with reduced motion, data saver or without WebGL2. Fetches the files below only when it will play. |
| `docs/assets/js/opa-arrival.js` | Built from `src/arrival.ts` by `npm run build`: the page's side (about 5 KB, no three.js). Places the canvas, measures the logo, runs the fades and the cross-fade, Skip and every other way out, and starts the renderer. |
| `docs/assets/js/opa-arrival-render.js` | Built from `src/render.ts`: the renderer (three.js r187 with its glTF loader and the meshopt decoder, bundled; no CDN). It runs in a worker, drawing on the canvas handed over with `transferControlToOffscreen`, so drawing never occupies the page's main thread; where a browser can't do that, the same script runs in the page. |
| `docs/assets/opa/opa.glb` | Built from `src/model.ts`: the rigged model (mesh, 59-bone skeleton, skin weights, outline normals), meshopt-compressed. Opens in Blender (File > Import > glTF 2.0). |

## Quality

The approved look is drawn antialiased at `min(2, devicePixelRatio)`, and that is what a computer with working
graphics hardware gets. Before the flight starts, the renderer draws a few of the largest frames on a canvas nobody
sees and times them, read back so the antialiasing is included. If those frames take more than 25 ms (median), as
they do where WebGL is drawn in software, the flight is drawn without antialiasing. During the flight, if frames
keep arriving more than 50 ms apart (median of 24), the drawing resolution is lowered by a quarter, at most three
times and never below 0.75. The path, timing, pose and colours never change, and the arrival is never skipped for
being slow. The browser's GPU name is not used for any of this.

After an arrival, `MachyonOpaArrival.last` in the browser console shows what was used: `mode` (`worker` or
`page`), `antialias`, `pixelRatio`, `probeMs`, `frames`, `medianFrameMs` and `steps`.

## Sources

- `geom.ts`, `rig.ts`, `poses.ts`: the film rig, copied unchanged; the logo's own maths
- `rigdata.ts`: the skeleton and its rest pose; `model.ts`: the mesh generator
- `film.ts`: the approved film's turn, curl, settle and eye-stalk dip (frames 498-540, dip at frame 578)
- `choreo.ts`: the flight in camera space (Z approach, bank, path, follow-the-leader spine, springs)
- `shade.ts`: cel shading, page-coloured outline, haze, hand-over; `player.ts`: loading and drawing
- `timeline.ts`: the clock both sides share (easing, timeline, fades); `palette.ts`: the colours; `hero.ts`: measuring the logo
- `arrival.ts`: the homepage entry (canvas, skip, Esc, leaving the page, theme changes, cross-fade to the logo)
- `render.ts`: the renderer's side (worker or page), the quality probe and the frame-rate governor

Rebuild after changing anything in `src/`, and commit the three generated files with the change.
The build is deterministic: the same sources give byte-identical output.
