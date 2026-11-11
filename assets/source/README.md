# README chart source

```bash
python make_chart.py     # writes ../benchmark-light.svg and ../benchmark-dark.svg from benchmarks/results-*.jsonl
```

The brand artwork (Opa, the lockup, icons) lives in `docs/assets/brand/`.

# Opa's arrival (the homepage animation)

```bash
cd opa-arrival
npm ci            # three.js, esbuild, gltf-transform, meshoptimizer: build tools only, pinned in package-lock.json
npm run build     # writes docs/assets/opa/opa.glb and docs/assets/js/opa-arrival.js
```

See `opa-arrival/README.md`.
