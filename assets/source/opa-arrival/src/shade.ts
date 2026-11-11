// Opa's look: flat brand fills with two-tone cel shading, a page-coloured outline (the logo's gaps), distance haze
// toward the page colour, and the hand-over controls (flat fills, depth flattened toward the page plane).
import {BackSide, Color, FrontSide, ShaderMaterial, Vector3} from 'three';
import {Palette, PALETTES} from './palette';

export type {Palette} from './palette';
export {PALETTES} from './palette';

const VS = `#include <common>
#include <skinning_pars_vertex>
uniform float uOutline, uZScale;
#ifdef OUTLINE
attribute vec3 onormal;
#endif
varying vec3 vN;
void main() {
  #include <beginnormal_vertex>
  float ow = 1.0;
  #ifdef OUTLINE
  objectNormal = onormal; ow = length(onormal);   // its length is the outline width here (0 where a flap meets the body)
  #endif
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <defaultnormal_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
  transformed += normalize(objectNormal) * uOutline * ow;
  transformed.z *= uZScale;
  #include <project_vertex>
  vN = normalize(transformedNormal);
}`;
const FS = `uniform vec3 uLit, uShade, uBase, uBg, uL; uniform float uFlat, uHaze; varying vec3 vN;
void main() {
  float d = dot(normalize(vN), normalize(uL));
  vec3 c = mix(uShade, uLit, smoothstep(0.05, 0.12, d));
  c = mix(mix(c, uBase, uFlat), uBg, uHaze);
  gl_FragColor = vec4(c, 1.0);
  #include <colorspace_fragment>
}`;

const mat = (outline: boolean) => new ShaderMaterial({
  vertexShader: VS, fragmentShader: FS, side: outline ? BackSide : FrontSide, defines: outline ? {OUTLINE: 1} : {},
  uniforms: {uLit: {value: new Color()}, uShade: {value: new Color()}, uBase: {value: new Color()},
    uBg: {value: new Color()}, uL: {value: new Vector3(-0.4, 0.75, 0.55)}, uFlat: {value: 0}, uHaze: {value: 0},
    uOutline: {value: 0}, uZScale: {value: 1}},
});

/** One material per part kind; `set` recolours them for a theme, `frame` updates the per-frame controls. */
export const makeMaterials = () => {
  const m = {body: mat(false), amber: mat(false), pupil: mat(false), outline: mat(true)};
  const all = Object.values(m);
  const set = (p: Palette) => {
    const c = (s: string) => new Color(s), bg = c(p.bg);
    // mix in sRGB (as a designer would), not in linear light, where 16% toward white is already pale grey
    const tone = (base: Color, lift: number) => {
      const a = base.clone().convertLinearToSRGB(), b = new Color(lift > 0 ? '#ffffff' : p.bg).convertLinearToSRGB();
      return a.lerp(b, Math.abs(lift)).convertSRGBToLinear();
    };
    const body = c(p.body), amber = c(p.amber), pupil = c(p.pupil);
    const put = (x: ShaderMaterial, lit: Color, shade: Color, base: Color) => {
      x.uniforms.uLit.value.copy(lit); x.uniforms.uShade.value.copy(shade); x.uniforms.uBase.value.copy(base); x.uniforms.uBg.value.copy(bg);
    };
    if (p.dark) put(m.body, body, tone(body, -0.16), body); else put(m.body, tone(body, 0.16), body, body);
    put(m.amber, tone(amber, 0.22), amber, amber);
    put(m.pupil, pupil, pupil, pupil);
    put(m.outline, bg, bg, bg);
  };
  const frame = (u: {flat: number; haze: number; zScale: number; outline: number}) => {
    for (const x of all) { x.uniforms.uFlat.value = u.flat; x.uniforms.uHaze.value = x === m.outline ? 0 : u.haze; x.uniforms.uZScale.value = u.zScale; }
    m.outline.uniforms.uOutline.value = u.outline;
  };
  set(PALETTES.light);
  return {...m, set, frame};
};
