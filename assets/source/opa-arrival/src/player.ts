// The runtime: loads the GLB, lays Opa's choreography out against the homepage logo, and draws one frame at a time.
// This is the whole production renderer (three.js + its glTF loader + the meshopt decoder); the prototype adds
// debug views on top of it. It needs no DOM: the canvas can be an OffscreenCanvas in a worker.
import {WebGLRenderer, Scene, PerspectiveCamera, SkinnedMesh, Bone, Object3D, Camera, Matrix4} from 'three';
import {GLTFLoader} from 'three/examples/jsm/loaders/GLTFLoader.js';
import {MeshoptDecoder} from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import {Choreo, Measure, Frame} from './choreo';
import {BONES} from './rigdata';
import {makeMaterials, Palette, PALETTES} from './shade';

export type Target = HTMLCanvasElement | OffscreenCanvas;
/** How a frame is drawn. The approved look is antialias on, pixel ratio min(2, devicePixelRatio). */
export type Quality = {antialias: boolean; pixelRatio: number};

const makeRenderer = (canvas: Target, q: Quality) => {
  const r = new WebGLRenderer({canvas: canvas as HTMLCanvasElement, alpha: true, antialias: q.antialias, premultipliedAlpha: true});
  r.setClearColor(0x000000, 0);
  r.setPixelRatio(q.pixelRatio);
  return r;
};

export class Player {
  readonly scene = new Scene(); readonly camera = new PerspectiveCamera(30, 1, 1, 1e6);
  readonly mats = makeMaterials(); readonly bones: Bone[] = []; readonly root = new Object3D();
  renderer: WebGLRenderer | null = null; ch: Choreo | null = null; cpuMs = 0;

  private constructor(gltfScene: Object3D) {
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

  static async load(url: string) {
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    const gltf = await loader.loadAsync(url);
    return new Player(gltf.scene);
  }

  layout(m: Measure) {
    this.ch = new Choreo(m);
    const c = this.ch;
    this.camera.aspect = m.vw / m.vh; this.camera.near = 1; this.camera.far = c.D * (c.lam0 + 4);
    this.camera.position.copy(c.cam); this.camera.lookAt(m.vw / 2, -m.vh / 2, 0); this.camera.updateProjectionMatrix();
    this.renderer?.setSize(m.vw, m.vh, false);
    return c;
  }
  theme(p: Palette = PALETTES.light) { this.mats.set(p); }

  /** Draw into `canvas` from now on, at quality `q` (after layout). */
  attach(canvas: Target, q: Quality) {
    this.renderer = makeRenderer(canvas, q);
    if (this.ch) this.renderer.setSize(this.ch.m.vw, this.ch.m.vh, false);
    return this.renderer;
  }
  /** Fewer pixels per CSS pixel: the canvas keeps its size on the page, its drawing buffer shrinks. */
  setPixelRatio(pr: number) {
    if (!this.renderer || !this.ch) return;
    this.renderer.setPixelRatio(pr);
    this.renderer.setSize(this.ch.m.vw, this.ch.m.vh, false);
  }

  /** Pose Opa at time t (seconds). */
  pose(t: number): Frame | null {
    if (!this.ch) return null;
    const f = this.ch.frame(t);
    f.bones.forEach((W: Matrix4, i: number) => this.bones[i].matrixWorld.copy(W));
    this.mats.frame(f);
    this.scene.updateMatrixWorld();
    return f;
  }
  /** Pose and draw time t (seconds) through the visitor's camera, or another camera for debugging. */
  render(t: number, cam: Camera = this.camera): Frame | null {
    if (!this.ch || !this.renderer) return null;
    const t0 = performance.now(), f = this.pose(t)!;
    this.renderer.render(this.scene, cam);
    this.cpuMs = performance.now() - t0;
    return f;
  }

  /** Time (ms) to draw each of `times` with the approved look (antialiased, at `pixelRatio`) and read it back, on a
      separate canvas the visitor never sees. The first one includes compiling the shaders. */
  probe(canvas: Target, pixelRatio: number, times: number[], stopAboveMs = Infinity): number[] {
    if (!this.ch) return [];
    const r = makeRenderer(canvas, {antialias: true, pixelRatio}), gl = r.getContext(), out: number[] = [];
    const px = new Uint8Array(4);
    try {
      r.setSize(this.ch.m.vw, this.ch.m.vh, false);
      for (const t of times) {
        const a = performance.now();
        this.pose(t);
        r.render(this.scene, this.camera);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);   // waits for the frame, antialiasing resolved
        out.push(performance.now() - a);
        if (out.length > 1 && out[out.length - 1] > stopAboveMs) break;     // clearly too slow: no need to go on
      }
    } finally {
      r.dispose(); r.forceContextLoss();
    }
    return out;
  }

  dispose() {
    const r = this.renderer;
    this.renderer = null;
    if (!r) return;
    r.dispose();
    if (!r.getContext().isContextLost()) r.forceContextLoss();        // frees the GPU memory now, not at garbage collection
  }
}
