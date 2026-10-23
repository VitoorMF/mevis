from pathlib import Path

import zstandard

from trader.core.rawformat import RawRecord, encode
from trader.storage.objectstore import LocalDirectoryStore
from trader.storage.rawfiles import (
    BadLine,
    compress_file,
    content_digest,
    file_sha256,
    iter_records,
)


def write_lines(path: Path, count: int, tail: bytes = b"") -> bytes:
    content = b"".join(
        encode(RawRecord(1_792_764_000_000_000 + i, "S1", i, "btcusdt@trade", b'{"i":%d}' % i))
        for i in range(count)
    )
    path.write_bytes(content + tail)
    return content + tail


def test_compress_verifies_and_replaces_original(tmp_path: Path) -> None:
    original = write_lines(tmp_path / "S1.jsonl", 100)
    result = compress_file(tmp_path / "S1.jsonl", level=9)
    assert result.path == tmp_path / "S1.jsonl.zst"
    assert not (tmp_path / "S1.jsonl").exists()
    assert zstandard.ZstdDecompressor().decompress(result.path.read_bytes()) == original
    assert result.original_size == len(original)
    assert content_digest(result.path) == (result.sha256, len(original))


def test_iter_records_reads_compressed_files(tmp_path: Path) -> None:
    write_lines(tmp_path / "S1.jsonl", 3)
    compressed = compress_file(tmp_path / "S1.jsonl", level=3).path
    records = list(iter_records(compressed))
    assert [r.seq for r in records if isinstance(r, RawRecord)] == [0, 1, 2]


def test_truncated_and_invalid_lines_become_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "S1.jsonl"
    write_lines(path, 2, tail=b'garbage\n{"r":1,"s":"S1","q":9,"c":"btcusdt@trade","m":{"tru')
    items = list(iter_records(path))
    assert [type(i).__name__ for i in items] == ["RawRecord", "RawRecord", "BadLine", "BadLine"]
    last = items[-1]
    assert isinstance(last, BadLine) and last.reason == "linha truncada"


def test_local_store_copies_and_verifies(tmp_path: Path) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(b"conteudo")
    store = LocalDirectoryStore(tmp_path / "replica")
    store.put(source, "raw/x/a.bin", file_sha256(source))
    assert (tmp_path / "replica" / "raw/x/a.bin").read_bytes() == b"conteudo"
