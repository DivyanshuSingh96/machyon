# Build your first image dataset

A beginner Machyon project: download the images listed in `manifest.csv` into folders by category, save the
`Report` as `report.json`, and write `gallery.html` to browse the result.

```bash
pip install machyon
python build_dataset.py
```

The full walkthrough is in the documentation:
<https://divyanshusingh96.github.io/machyon/cookbook/first-dataset/>

| File | What it is |
|---|---|
| `build_dataset.py` | The project. |
| `manifest.csv` | What readers download: 12 photos from [Picsum Photos](https://picsum.photos/) plus 2 deliberately broken links. |
| `manifest_offline.csv` | The same rows pointing at Machyon's local test server (`{base}` is filled in by the test), so the tests never need the internet. |

The photos are by the photographers named in each row of `manifest.csv`, shared on Unsplash under the
[Unsplash License](https://unsplash.com/license). This folder contains only links to them, never the photos.
