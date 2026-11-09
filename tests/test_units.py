import asyncio
import csv

import pytest

from machyon import AdaptiveLimiter, ImageRenamer, generate_hashed_name, run
from machyon.utils import safe_filename, sniff_image

# ------------------------------------------------------------------ renamer

def write_csv(path, rows, header=("image_link", "group_id", "entity_name")):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_hashed_names_match_previous_version_and_are_not_capped(tmp_path):
    p = tmp_path / "t.csv"
    write_csv(p, [(f"https://x/{i}.jpg", 1, "item_weight") for i in range(1000)])
    links, names = ImageRenamer(entity_col="entity_name").hashed_names(str(p))
    assert len(names) == 1000                                  # old version stopped at 300
    # identical to the previous implementation's formula: f"{i}_{blake2b(f'{entity}_{i}')}.png"
    assert names[0] == f"0_{generate_hashed_name('item_weight_0', 16)}.png"
    links2, names2 = run(ImageRenamer(entity_col="entity_name").generate_hashed_names(str(p)))
    assert names2 == names


def test_process_csv_keeps_original_row_index_and_sanitizes(tmp_path):
    p = tmp_path / "t.csv"
    write_csv(p, [("https://x/a.jpg?w=1", "748919", "item/weight"),
                  ("", "1", "depth"),                         # dropped: no link
                  ("https://x/b.jpg", "", "width")])          # blank group
    links, names = ImageRenamer(group_col="group_id").process_csv(str(p))
    assert list(links) == ["https://x/a.jpg?w=1", "https://x/b.jpg"]
    assert names == ["0_a_748919_item_weight.png", "2_b_unknown_width.png"]


def test_bad_name_format_and_missing_column(tmp_path):
    with pytest.raises(ValueError):
        ImageRenamer(name_format="{index}_{nope}.png")
    p = tmp_path / "t.csv"
    write_csv(p, [("https://x/a.jpg", 1, "w")], header=("url", "group_id", "entity_name"))
    with pytest.raises(KeyError):
        ImageRenamer().process_csv(str(p))


# ------------------------------------------------------------------ utils

@pytest.mark.parametrize("raw,expected", [
    ("a/b\\c:d*e?.png", "a_b_c_d_e_.png"), ("..", "_"), ("CON.png", "_CON.png"),
    ("name. ", "name"), ("ok.png", "ok.png"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_length_limit():
    out = safe_filename("é" * 300 + ".png")
    assert len(out.encode()) <= 200 and out.endswith(".png")


@pytest.mark.parametrize("head,fmt", [
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"), (b"\x89PNG\r\n\x1a\n0000", "png"), (b"GIF89a000", "gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"), (b"\x00\x00\x00\x1cftypavif0000", "avif"),
    (b"<!DOCTYPE html><html>", None), (b"{\"error\":1}", None),
])
def test_sniff_image(head, fmt):
    assert sniff_image(head) == fmt


# ------------------------------------------------------------------ limiter

def test_limiter_slow_start_then_revert_when_no_gain():
    lim = AdaptiveLimiter(initial=10, maximum=200, window=0.0)
    lim._w_attempts = 100
    lim._adjust(1.0)  # rate 0 -> prev set, grow
    lim._w_done = 0
    assert lim.limit == 20
    lim._skip_next = False
    lim._prev = (10, 100.0)
    lim._w_done = 101
    lim._adjust(1.0)                                          # +1% for 2x sockets -> revert
    assert lim.limit == 10


def test_limiter_grows_on_gain_and_cuts_on_throttle():
    lim = AdaptiveLimiter(initial=10, maximum=200, window=0.0)
    lim._prev, lim._w_done, lim.limit = (10, 100.0), 0, 10
    lim.limit = 20
    lim._w_done, lim._w_attempts = 190, 190
    lim._adjust(1.0)
    assert lim.limit == 40
    lim._w_throttle, lim._w_attempts = 5, 100
    lim._adjust(1.0)
    assert lim.limit == 28 and lim.ceiling == 36


def test_limiter_respects_limit_and_cancellation():
    async def main():
        lim = AdaptiveLimiter(initial=2, minimum=1, maximum=4, adaptive=False)
        await lim.acquire()
        await lim.acquire()
        waiter = asyncio.ensure_future(lim.acquire())
        await asyncio.sleep(0)
        assert not waiter.done()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        lim.release()
        assert lim.in_use == 1
        lim.limit = 3
        lim._wake()
        await lim.acquire()
        await lim.acquire()
        assert lim.in_use == 3
    run(main())


def test_renamer_keeps_rows_with_blank_entity(tmp_path):
    p = tmp_path / "t.csv"
    write_csv(p, [("https://x/a.jpg", "1", ""), ("https://x/b.jpg", "2", "width")])
    links, names = ImageRenamer(name_format="{index}_{image_name}.{extension}").process_csv(str(p))
    assert names == ["0_a.png", "1_b.png"]
    links, names = ImageRenamer().hashed_names(str(p))
    assert names[0] == f"0_{generate_hashed_name('nan_0', 16)}.png"   # same as earlier versions
