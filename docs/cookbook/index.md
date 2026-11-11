---
description: Small, complete projects built with Machyon that you can run, read and change.
---

# Cookbook

<p class="lead">Complete little projects built with Machyon's real API. Each one runs as it is, explains the
calls it makes, and shows the output of a real run.</p>

<div class="mx-grid" markdown>

<div class="mx-card" markdown>

## [Build your first image dataset](first-dataset.md)

Beginner. Turn a CSV of image links into folders sorted by category, a report, and a gallery to browse. One
`machyon.download()` call does the downloading.

</div>

<div class="mx-card" markdown>

## [Amazon ML Challenge](../getting-started/amazon-ml-challenge.md)

The same idea for a real competition dataset: 263,859 rows, the starter kit's file names, and resuming when a
notebook disconnects.

</div>

<div class="mx-card" markdown>

## [Download images in Google Colab](https://colab.research.google.com/github/DivyanshuSingh96/machyon/blob/main/notebooks/machyon-colab.ipynb)

Beginner, nothing to install. A notebook with a form: paste links or upload a CSV, pick how files are named,
then browse a gallery, see why anything failed, retry, and take the images as a ZIP or to Google Drive.

[:simple-googlecolab: Open in Colab](https://colab.research.google.com/github/DivyanshuSingh96/machyon/blob/main/notebooks/machyon-colab.ipynb){ .md-button }

</div>

</div>

Every project's code is in
[`examples/cookbook/`](https://github.com/DivyanshuSingh96/machyon/tree/main/examples/cookbook) and is tested
offline against Machyon's local test server, so the code you read here is the code that runs.

To see *why* Machyon behaves the way it does, try the [Interactive Lab](../lab/index.md).
