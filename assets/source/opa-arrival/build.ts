// npm run build: regenerates docs/assets/opa/opa.glb and docs/assets/js/opa-arrival.js from src/.
// The GLB is generated from the approved 2D rig (src/model.ts), then meshopt-compressed; the script is the
// homepage player (three.js, its glTF loader and the meshopt decoder are bundled in: no CDN).
import {mkdirSync, writeFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {Scene, Group, Matrix4, SkinnedMesh, BufferGeometry, BufferAttribute, MeshStandardMaterial} from 'three';
import {GLTFExporter} from 'three/examples/jsm/exporters/GLTFExporter.js';
import {NodeIO} from '@gltf-transform/core';
import {ALL_EXTENSIONS} from '@gltf-transform/extensions';
import {meshopt} from '@gltf-transform/functions';
import {MeshoptEncoder} from 'meshoptimizer';
import {buildOpa} from './src/model';

const here = dirname(fileURLToPath(import.meta.url)), docs = resolve(here, '../../../docs/assets');

// GLTFExporter reads its binary output with FileReader, which Node does not have
(globalThis as any).FileReader ??= class {
  result: unknown; onloadend?: () => void;
  readAsArrayBuffer(b: Blob) { b.arrayBuffer().then((a) => { this.result = a; this.onloadend?.(); }); }
  readAsDataURL(b: Blob) { b.arrayBuffer().then((a) => { this.result = `data:application/octet-stream;base64,${Buffer.from(a).toString('base64')}`; this.onloadend?.(); }); }
};

/** One skinned mesh per material (body, amber, pupil), each with only its own vertices, on one skeleton. */
async function exportGlb(): Promise<Uint8Array> {
  const opa = buildOpa('light'), g = opa.mesh.geometry, idx = g.index!.array, skel = opa.mesh.skeleton;
  const group = new Group(); group.name = 'Opa';
  const root = opa.bones[0]; opa.mesh.remove(root); group.add(root);
  ['body', 'amber', 'pupil'].forEach((name, mi) => {
    const grp = g.groups[mi], remap = new Map<number, number>(), order: number[] = [], tri: number[] = [];
    for (let i = grp.start; i < grp.start + grp.count; i++) {
      const v = idx[i]; if (!remap.has(v)) { remap.set(v, order.length); order.push(v); } tri.push(remap.get(v)!);
    }
    const pick = (attr: BufferAttribute, Ctor: any, norm = false, scale = 1) => {
      const out = new Ctor(order.length * attr.itemSize), isF = Ctor === Float32Array;
      order.forEach((v, j) => { for (let c = 0; c < attr.itemSize; c++) out[j * attr.itemSize + c] = isF ? Math.round(attr.array[v * attr.itemSize + c] * 1e6) / 1e6 : Math.round(attr.array[v * attr.itemSize + c] * scale); });
      return new BufferAttribute(out, attr.itemSize, norm);
    };
    const ng = new BufferGeometry();
    ng.setAttribute('position', pick(g.getAttribute('position') as BufferAttribute, Float32Array));
    ng.setAttribute('normal', pick(g.getAttribute('normal') as BufferAttribute, Float32Array));
    ng.setAttribute('skinIndex', pick(g.getAttribute('skinIndex') as BufferAttribute, Uint16Array));
    ng.setAttribute('skinWeight', pick(g.getAttribute('skinWeight') as BufferAttribute, Float32Array));
    if (mi === 0) ng.setAttribute('onormal', pick(g.getAttribute('onormal') as BufferAttribute, Int8Array, true, 127));
    ng.setIndex(tri);
    const mat = new MeshStandardMaterial({color: [0x2a3142, 0xf5b83d, 0x2a3142][mi], roughness: 1}); mat.name = name;
    const sm = new SkinnedMesh(ng, mat); sm.name = `Opa_${name}`;
    group.add(sm); sm.bind(skel, new Matrix4());
  });
  const scene = new Scene(); scene.add(group); scene.updateMatrixWorld(true);
  const raw = await new GLTFExporter().parseAsync(scene, {binary: true}) as ArrayBuffer;
  await MeshoptEncoder.ready;
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder': MeshoptEncoder});
  const doc = await io.readBinary(new Uint8Array(raw));
  await doc.transform(meshopt({encoder: MeshoptEncoder, level: 'medium', quantizePosition: 14, quantizeNormal: 8}));
  return io.writeBinary(doc);
}

const glb = await exportGlb();
mkdirSync(resolve(docs, 'opa'), {recursive: true});
writeFileSync(resolve(docs, 'opa/opa.glb'), glb);
await build({
  entryPoints: [resolve(here, 'src/arrival.ts')], outfile: resolve(docs, 'js/opa-arrival.js'),
  bundle: true, minify: true, format: 'iife', target: 'es2020', legalComments: 'eof', logLevel: 'warning',
  banner: {js: '/* Opa\'s arrival on the homepage. Built from assets/source/opa-arrival (npm run build); do not edit. */'},
});
console.log(`opa.glb ${glb.byteLength} bytes; opa-arrival.js written`);
