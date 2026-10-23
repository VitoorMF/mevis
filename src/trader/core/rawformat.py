"""Envelope de linha da camada bruta (doc 06 §5.2).

Formato: ``{"r":<recv_ts_us>,"s":"<sessão>","q":<seq>,"c":"<canal>","m":<payload>}`` + ``\\n``.
O payload entra byte a byte, sem escape nem validação, para preservar exatamente o que foi
recebido — inclusive mensagens malformadas (RF-119).
"""

import json
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[A-Za-z0-9@:._-]{1,128}")
_PAYLOAD_KEY = b',"m":'


class RawFormatError(ValueError):
    """Linha ou campo incompatível com o envelope da camada bruta."""


@dataclass(frozen=True, slots=True)
class RawRecord:
    recv_ts_us: int
    session: str
    seq: int
    channel: str
    payload: bytes


def encode(record: RawRecord) -> bytes:
    for name, value in (("session", record.session), ("channel", record.channel)):
        if not _TOKEN.fullmatch(value):
            raise RawFormatError(f"{name} com caracteres não permitidos: {value!r}")
    if b"\n" in record.payload:
        raise RawFormatError("payload contém quebra de linha")
    header = json.dumps(
        {"r": record.recv_ts_us, "s": record.session, "q": record.seq, "c": record.channel},
        separators=(",", ":"),
    ).encode()
    return header[:-1] + _PAYLOAD_KEY + record.payload + b"}\n"


def decode(line: bytes) -> RawRecord:
    """Inverso de `encode`; não exige que o payload seja JSON válido."""
    line = line.removesuffix(b"\n")
    head, sep, tail = line.partition(_PAYLOAD_KEY)
    if not sep or not tail.endswith(b"}"):
        raise RawFormatError("linha sem o campo de payload")
    try:
        header = json.loads(head + b"}")
        record = RawRecord(
            recv_ts_us=header["r"],
            session=header["s"],
            seq=header["q"],
            channel=header["c"],
            payload=tail[:-1],
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise RawFormatError("cabeçalho inválido") from exc
    if not isinstance(record.recv_ts_us, int) or not isinstance(record.seq, int):
        raise RawFormatError("cabeçalho com tipos inválidos")
    return record
