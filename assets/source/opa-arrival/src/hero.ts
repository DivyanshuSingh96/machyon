// Where the homepage logo and wordmark are, read from the page (the renderer, possibly in a worker, gets the numbers).
import type {Measure} from './choreo';

/** Where the logo and wordmark are, from a document showing the homepage hero (viewport px). */
export const measureHero = (doc: Document, vw: number, vh: number): Measure | null => {
  const mark = doc.querySelector<SVGSVGElement>('.mx-hero-mark'), opaG = mark?.querySelector<SVGGElement>('.mx-opa > g'), word = mark?.querySelector<SVGGraphicsElement>('.mx-word');
  if (!mark || !opaG || !word) return null;
  const svgM = mark.getScreenCTM()!, own = opaG.transform.baseVal.consolidate(), ctm = own ? svgM.multiply(own.matrix) : svgM;
  const wt = word.transform.baseVal.consolidate(), wm = wt ? svgM.multiply(wt.matrix) : svgM, bb = word.getBBox();
  const P = (x: number, y: number) => new DOMPoint(x, y).matrixTransform(wm), a = P(bb.x, bb.y), b = P(bb.x + bb.width, bb.y + bb.height);
  return {vw, vh, word: {l: a.x, t: a.y, r: b.x, b: b.y}, ctm: {a: ctm.a, e: ctm.e, f: ctm.f}};
};
