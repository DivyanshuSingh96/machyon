// The runtime: loads the GLB, lays Opa's choreography out against the homepage logo, and draws one frame at a time.
// This is the whole production renderer (three.js + its glTF loader + the meshopt decoder); the prototype adds
// debug views on top of it.
import {WebGLRenderer, Scene, PerspectiveCamera, SkinnedMesh, Bone, Object3D, Camera, Matrix4} from 'three';
import {GLTFLoader} from 'three/examples/jsm/loaders/GLTFLoader.js';
import {MeshoptDecoder} from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import {Choreo, Measure, Frame} from './choreo';
import {BONES} from './rigdata';
import {makeMaterials, Palette, PALETTES} from './shade';

/** Where the logo and wordmark are, from a document showing the homepage hero (viewport px). */
export const measureHero = (doc: Document, vw: number, vh: number): Measure | null => {
  const mark = doc.querySelector<SVGSVGElement>('.mx-hero-mark'), opaG = mark?.querySelector<SVGGElement>('.mx-opa > g'), word = mark?.querySelector<SVGGraphicsElement>('.mx-word');
  if (!mark || !opaG || !word) return null;
  const svgM = mark.getScreenCTM()!, own = opaG.transform.baseVal.consolidate(), ctm = own ? svgM.multiply(own.matrix) : svgM;
  const wt = word.transform.baseVal.consolidate(), wm = wt ? svgM.multiply(wt.matrix) : svgM, bb = word.getBBox();
  const P = (x: number, y: number) => new DOMPoint(x, y).matrixTransform(wm), a = P(bb.x, bb.y), b = P(bb.x + bb.width, bb.y + bb.height);
  return {vw, vh, word: {l: a.x, t: a.y, r: b.x, b: b.y}, ctm: {a: ctm.a, e: ctm.e, f: ctm.f}};
};

export class Player {
  readonly renderer: WebGLRenderer; readonly scene = new Scene(); readonly camera = new PerspectiveCamera(30, 1, 1, 1e6);
  readonly mats = makeMaterials(); readonly bones: Bone[] = []; readonly root = new Object3D();
  ch: Choreo | null = null; cpuMs = 0;

  private constructor(readonly canvas: HTMLCanvasElement, gltfScene: Object3D) {
    this.renderer = new WebGLRenderer({canvas, alpha: true, antialias: true, premultipliedAlpha: true});
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    const meshes: SkinnedMesh[] = [];
    gltfScene.traverse((o) => { if ((o as SkinnedMesh).isSkinnedMesh) meshes.push(o as SkinnedMesh); });
    const byName = new Map<string, Bone>();
    meshes[0].skeleton.bones.forEach((b) => byName.set(b.name, b));
    BONES.forEach((d) => {
      const b = byName.get(d.name)!;
      b.matrixAutoUpdate = false; b.matrixWorldAutoUpdate = false;
      this.bones.push(b);
    });
    for (const m of meshes) {
      const kind = m.name.replace(/^Opa_/, '') as 'body' | 'amber' | 'pupil';
      const g = m.geometry;
      if (g.getAttribute('_onormal')) { g.setAttribute('onormal', g.getAttribute('_onormal')); g.deleteAttribute('_onormal'); }
      m.material = this.mats[kind]; m.frustumCulled = false;
      if (kind !== 'pupil') {
        if (!g.getAttribute('onormal')) g.setAttribute('onormal', g.getAttribute('normal'));
        const o = new SkinnedMesh(g, this.mats.outline); o.frustumCulled = false;
        o.bind(m.skeleton, m.bindMatrix); m.parent!.add(o);
      }
    }
    this.root.add(gltfScene); this.scene.add(this.root);
  }

  static async load(canvas: HTMLCanvasElement, url: string) {
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    const gltf = await loader.loadAsync(url);
    return new Player(canvas, gltf.scene);
  }

  layout(m: Measure) {
    this.ch = new Choreo(m);
    const c = this.ch;
    this.camera.aspect = m.vw / m.vh; this.camera.near = 1; this.camera.far = c.D * (c.lam0 + 4);
    this.camera.position.copy(c.cam); this.camera.lookAt(m.vw / 2, -m.vh / 2, 0); this.camera.updateProjectionMatrix();
    this.renderer.setSize(m.vw, m.vh, false);
    return c;
  }
  theme(p: Palette = PALETTES.light) { this.mats.set(p); }

  /** Pose and draw time t (seconds) through the visitor's camera, or another camera for debugging. */
  render(t: number, cam: Camera = this.camera): Frame | null {
    if (!this.ch) return null;
    const t0 = performance.now(), f = this.ch.frame(t);
    f.bones.forEach((W: Matrix4, i: number) => this.bones[i].matrixWorld.copy(W));
    this.mats.frame(f);
    this.scene.updateMatrixWorld();
    this.renderer.render(this.scene, cam);
    this.cpuMs = performance.now() - t0;
    return f;
  }
  dispose() { this.renderer.dispose(); this.renderer.forceContextLoss(); }
}
