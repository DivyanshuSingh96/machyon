# Opa's arrival

The homepage animation: Opa flies in from far behind the page, toward the visitor, banks, crosses the screen, turns
above the "n", flies the film's N-to-M lane and curls into the logo, then dips its eye stalks. It plays at the
approved half speed (about 14.5 s, starting 1.3 s after the page appears).

## What runs where

| File | Role |
| --- | --- |
| `docs/assets/js/opa-arrival-loader.js` | On every page (about 3 KB, hand-written). Plays only on the homepage, once per visit (again after a reload), never with reduced motion, data saver or without WebGL2. Fetches the two files below only when it will play. |
| `docs/assets/js/opa-arrival.js` | Built from `src/` by `npm run build`: the player (three.js r187 with its glTF loader and the meshopt decoder, bundled; no CDN). |
| `docs/assets/opa/opa.glb` | Built from `src/model.ts`: the rigged model (mesh, 59-bone skeleton, skin weights, outline normals), meshopt-compressed. Opens in Blender (File > Import > glTF 2.0). |

## Sources

- `geom.ts`, `rig.ts`, `poses.ts`: the film rig, copied unchanged; the logo's own maths
- `rigdata.ts`: the skeleton and its rest pose; `model.ts`: the mesh generator
- `film.ts`: the approved film's turn, curl, settle and eye-stalk dip (frames 498-540, dip at frame 578)
- `choreo.ts`: the flight in camera space (Z approach, bank, path, follow-the-leader spine, springs)
- `shade.ts`: cel shading, page-coloured outline, haze, hand-over; `player.ts`: loading and drawing
- `arrival.ts`: the homepage entry (canvas, skip, Esc, leaving the page, theme changes, cross-fade to the logo)

Rebuild after changing anything in `src/`, and commit the two generated files with the change.
The build is deterministic: the same sources give byte-identical output.
