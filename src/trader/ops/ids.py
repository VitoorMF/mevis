"""Ids ordenáveis no tempo (formato ULID: 48 bits de ms + 80 bits aleatórios, base32 Crockford)."""

import secrets

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_session_id(now_ms: int) -> str:
    value = (now_ms << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))
