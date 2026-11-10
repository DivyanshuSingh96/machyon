"""machyon.sources.inspect: the first rows of a source, read exactly the way download() reads them."""
import json

import pytest

from machyon import read_rows
from machyon.sources import SourceInspection, inspect

U = [f"https://example.com/img/{i}.jpg" for i in range(30)]


def same_as_download(path, url_col=None, limit=20):
    """inspect() must show the URLs download() would read, in the same order."""
    r = inspect(path, url_col, limit)
    assert r.urls == [u for u, _ in read_rows(path, url_col)][:limit]
    return r


def test_csv_guesses_the_url_column(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("id,label,image_url\n" + "".join(f"{i},cat,{u}\n" for i, u in enumerate(U)))
    r = same_as_download(p)
    assert (r.format, r.columns, r.url_col, r.url_col_guessed, r.ok) == ("csv", ["id", "label", "image_url"],
                                                                          "image_url", True, True)
    assert len(r.rows) == 20 and r.rows[0] == {"id": "0", "label": "cat", "image_url": U[0]} and r.more


def test_tsv_and_an_explicit_column(tmp_path):
    p = tmp_path / "m.tsv"
    p.write_text("a\tb\n" + "".join(f"{u}\t{u}?big\n" for u in U[:5]))
    r = same_as_download(p, url_col="b")
    assert (r.format, r.url_col, r.url_col_guessed, r.more) == ("tsv", "b", False, False)
    assert r.urls[0] == U[0] + "?big"


def test_a_csv_without_a_header_is_a_list_of_urls(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("".join(f"{u},x\n" for u in U[:3]))
    r = same_as_download(p)
    assert r.columns is None and r.url_col is None and r.rows[0] == {"url": U[0]}


def test_txt_skips_comments_and_blank_lines(tmp_path):
    p = tmp_path / "m.txt"
    p.write_text("# my images\n\n" + "\n".join(U[:4]) + "\n")
    r = same_as_download(p)
    assert (r.format, r.columns, r.urls) == ("txt", None, U[:4])


@pytest.mark.parametrize("data", [U[:5], [{"src": u, "tag": "t"} for u in U[:5]], {"images": U[:5]},
                                  [[u, f"{i}.jpg"] for i, u in enumerate(U[:5])]],
                         ids=["urls", "objects", "object-holding-a-list", "pairs"])
def test_json_shapes(tmp_path, data):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(data))
    r = same_as_download(p)
    assert r.format == "json" and r.urls == U[:5]
    if isinstance(data, list) and isinstance(data[0], list):
        assert r.rows[0] == {"url": U[0], "name": "0.jpg"}


def test_jsonl(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_text("".join(json.dumps({"link": u}) + "\n" for u in U[:3]))
    r = same_as_download(p)
    assert (r.format, r.url_col) == ("jsonl", "link")


def test_parquet(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    p = tmp_path / "m.parquet"
    pq.write_table(pa.table({"url": U[:25], "n": list(range(25))}), p)
    r = same_as_download(p)
    assert (r.format, r.columns, r.url_col, r.more) == ("parquet", ["url", "n"], "url", True)


def test_no_guessable_url_column_still_shows_the_columns(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("a,b\n1,2\n3,4\n")
    r = inspect(p)
    assert not r.ok and r.error[0] == "no_url_column"
    assert r.columns == ["a", "b"] and r.rows == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}] and r.url_col is None


def test_an_unknown_column_is_reported_with_the_real_ones(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("url\n" + U[0] + "\n")
    r = inspect(p, url_col="nope")
    assert r.error[0] == "unknown_column" and r.columns == ["url"] and r.rows


@pytest.mark.parametrize("name,content,kind", [
    ("empty.csv", "", "empty"), ("empty.txt", "# nothing\n", "empty"), ("bad.json", "{not json", "unreadable"),
    ("bad.jsonl", '{"url": 1}\n{oops\n', "unreadable"), ("number.json", "42", "unreadable")])
def test_problems_are_reported_not_raised(tmp_path, name, content, kind):
    p = tmp_path / name
    p.write_text(content)
    r = inspect(p)
    assert isinstance(r, SourceInspection) and r.error and r.error[0] == kind, r.error


def test_a_missing_file_and_a_binary_file(tmp_path):
    assert inspect(tmp_path / "gone.csv").error[0] == "not_found"
    p = tmp_path / "sheet.xlsx"          # not a supported format: read as one URL per line, as download() does
    p.write_bytes(b"PK\x03\x04\xff\xfe\x00\x81" * 100)
    r = inspect(p)
    assert r.format == "txt" and r.error[0] == "unreadable"


def test_missing_parquet_support_is_named(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__

    def no_pyarrow(name, *a, **kw):
        if name.startswith("pyarrow"):
            raise ImportError("No module named 'pyarrow'")
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    p = tmp_path / "m.parquet"
    p.write_bytes(b"PAR1")
    r = inspect(p)
    assert r.error[0] == "missing_dependency" and "machyon[parquet]" in r.error[1]


def test_a_large_source_is_not_read_past_the_preview(tmp_path):
    # 200,000 good rows, then bytes that aren't UTF-8. Reading the whole file would fail; a preview must stop long
    # before, so it succeeds (and says there is more).
    p = tmp_path / "big.csv"
    with open(p, "wb") as f:
        f.write(b"url\n")
        f.write(b"".join(f"https://example.com/{i}.jpg\n".encode() for i in range(200_000)))
        f.write(b"\xff\xfe\xfa broken\n")
    r = inspect(p, limit=20)
    assert r.ok and len(r.rows) == 20 and r.more


def test_the_limit_is_respected(tmp_path):
    p = tmp_path / "m.txt"
    p.write_text("\n".join(U))
    assert len(inspect(p, limit=5).urls) == 5 and inspect(p, limit=30).more is False
    assert inspect(p, limit=0).error[0] == "empty" or inspect(p, limit=0).more
