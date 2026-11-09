---
description: Name downloaded files with templates built from the URL, the row number and your own columns, with callables or with explicit lists.
---

# Filenames

<p class="lead">Every row gets a filename, relative to the save folder. The same row must get the same name every
run: that's what makes resuming work.</p>

## Templates

A template is a Python format string. The default is `{hash}{ext}`.

| Key | Value | Example |
|---|---|---|
| `{hash}` | 16 hex characters of a BLAKE2b hash of the URL: unique per URL, stable across runs and machines | `67ba57b74cee05cc` |
| `{index}` | The row's position in the input, from 0 (also when you use `start`) | `41` |
| `{ext}` | The URL's image extension, or, when the URL has none, the real format detected from the bytes | `.jpg`, `.webp` |
| `{stem}` | The URL's filename without its extension (`image` if there is none) | `IMG_2041` |
| `{host}` | The URL's host name | `cdn.example.com` |
| `{column}` | Any column of your table, for example `{label}` or `{entity_name}` | `cats` |

A `/` creates subfolders. Standard format specs work, so `{index:06d}` pads and `{hash:.2}` takes the first two
characters.

```python
names="{index}_{hash}{ext}"            # 41_67ba57b74cee05cc.jpg
names="{label}/{index}{ext}"           # cats/41.jpg
names="{host}/{stem}{ext}"             # cdn.example.com/IMG_2041.jpg
names="{hash:.2}/{hash}{ext}"          # 67/67ba57b74cee05cc.jpg  (spreads files over 256 folders)
```

**Preview before you download.** `machyon names` prints the mapping without fetching anything:

{{ cast("names") }}

A key that is neither built in nor a column stops the run before anything is downloaded, with a list of the keys
you can use. A column that is blank in one row records that row as
[`bad_name`](../reference/failure-reasons.md#bad-name) and carries on.

## Automatic extensions

`{ext}` is resolved per row:

- URL ends in an image extension (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.avif`, `.heic`, `.jxl`, `.tif`,
  `.tiff`, `.bmp`, `.ico`, `.svg`, `.jfif`, `.apng`): that extension is used.
- Anything else (`/photo?id=42`, `/img/42`, `.php`): the extension comes from the
  [downloaded bytes](../reliability/image-validation.md), so a WebP becomes `.webp`.

On resume, a name with an automatic extension matches whichever image extension is on disk.

## Callables

For logic a template can't express, pass a function `(url, index, row) -> name`. `row` is the dict of columns, or
`None` for plain URL lists.

```python title="naming_callable.py"
--8<-- "naming_callable.py"
```

Return a literal `{ext}` (written `{% raw %}{{ext}}{% endraw %}` inside an f-string) at the end of the name to get an automatic
extension.

## Explicit names

Give Machyon the names directly, either as pairs or as a second list the same length as the URLs:

```python
machyon.download([("https://…/a.jpg", "cover.jpg"), ("https://…/b.png", "back.png")], "images/")
machyon.download(urls, "images/", names_list)     # len(names_list) must equal len(urls)
```

## Safety rules

Before anything is written:

- **Characters.** Names built from a template are cleaned part by part: characters that are invalid on Windows,
  macOS or Linux become `_`, trailing dots and spaces are removed, reserved Windows names (`CON`, `NUL`, …) get a
  leading `_`, and over-long parts are shortened, keeping the extension. A `..` part becomes `_`, so a template
  can never leave the save folder. On Windows, names from every source are cleaned the same way.
- **Containment.** A name you supply directly (pairs, a list, `name_col` or a callable) that would land outside
  the save folder, such as `../x.jpg` or an absolute path, is refused and recorded as
  [`unsafe_name`](../reference/failure-reasons.md#unsafe-name).
- **Conflicts.** If two *different* URLs map to the same file, the first row keeps the name and the other is
  recorded as [`duplicate_name`](../reference/failure-reasons.md#duplicate-name). Nothing is overwritten. On
  Windows, names that differ only in case count as the same file.
- **Duplicates.** The *same* URL on several rows with different names is downloaded once and written to every
  name. The same URL and name repeated is simply counted as skipped.

!!! tip "Use `{hash}` for uniqueness"
    Names built only from columns (`{label}{ext}`) collide easily. Adding `{hash}` or `{index}` guarantees one
    file per URL or per row.
