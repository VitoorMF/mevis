"""Compressão verificada e leitura dos arquivos brutos (RF-137; doc 06 §5.2)."""

import hashlib
import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import zstandard

from trader.core.rawformat import RawFormatError, RawRecord, decode
from trader.storage.layout import StorageError, replace_atomically

_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class CompressionResult:
    path: Path
    original_size: int
    compressed_size: int
    sha256: str
    """Hash do conteúdo original (descomprimido)."""


@dataclass(frozen=True, slots=True)
class BadLine:
    """Linha que não respeita o envelope (por exemplo, a última de um arquivo truncado)."""

    line: bytes
    reason: str


def compress_file(path: Path, level: int) -> CompressionResult:
    """Comprime `x.jsonl` em `x.jsonl.zst`, confere o resultado e só então apaga o original."""
    target = path.with_name(path.name + ".zst")
    tmp = path.with_name(path.name + ".zst.tmp")
    digest = hashlib.sha256()
    size = 0
    compressor = zstandard.ZstdCompressor(level=level, write_checksum=True)
    try:
        with path.open("rb") as source, tmp.open("wb") as sink:
            # O tamanho vai no cabeçalho do frame, o que facilita ler com outras ferramentas.
            expected = path.stat().st_size
            with compressor.stream_writer(sink, size=expected, closefd=False) as writer:
                while chunk := source.read(_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    writer.write(chunk)
            sink.flush()
        if content_digest(tmp, compressed=True)[0] != digest.hexdigest():
            tmp.unlink()
            raise StorageError(f"verificação da compressão falhou: {path}")
        replace_atomically(tmp, target)
        path.unlink()
    except OSError as exc:
        raise StorageError(f"falha ao comprimir {path}: {exc}") from exc
    return CompressionResult(target, size, target.stat().st_size, digest.hexdigest())


def content_digest(path: Path, *, compressed: bool | None = None) -> tuple[str, int]:
    """SHA-256 e tamanho do conteúdo lógico (descomprimido, se for `.zst`)."""
    digest = hashlib.sha256()
    size = 0
    with _open_lines(path, compressed) as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def file_sha256(path: Path) -> str:
    """SHA-256 dos bytes do arquivo como está no disco."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def iter_records(path: Path) -> Iterator[RawRecord | BadLine]:
    """Registros de um arquivo bruto, comprimido ou não, na ordem de gravação."""
    with _open_lines(path) as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                yield BadLine(line, "linha truncada")
                continue
            try:
                yield decode(line)
            except RawFormatError as exc:
                yield BadLine(line, str(exc))


def _open_lines(path: Path, compressed: bool | None = None) -> io.BufferedReader:
    if compressed is None:
        compressed = path.name.endswith(".zst")
    if compressed:
        reader = zstandard.ZstdDecompressor().stream_reader(path.open("rb"), closefd=True)
        return io.BufferedReader(reader)
    return path.open("rb")
