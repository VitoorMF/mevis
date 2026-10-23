import json

import pytest

from trader.core.rawformat import RawFormatError, RawRecord, decode, encode

PAYLOAD = b'{"stream":"btcusdt@trade","data":{"e":"trade","p":"81524.01000000", "x":"\xc3\xa9"}}'


def record(payload: bytes = PAYLOAD, channel: str = "btcusdt@trade") -> RawRecord:
    return RawRecord(
        recv_ts_us=1_792_779_042_700_000,
        session="01JBT2X9Q0ABCDEF",
        seq=42,
        channel=channel,
        payload=payload,
    )


def test_round_trip_preserves_payload_bytes() -> None:
    line = encode(record())
    assert line.endswith(b"}\n")
    assert decode(line) == record()


def test_line_is_valid_json_when_payload_is() -> None:
    parsed = json.loads(encode(record()))
    assert parsed["r"] == 1_792_779_042_700_000
    assert parsed["c"] == "btcusdt@trade"
    assert parsed["m"]["data"]["p"] == "81524.01000000"


def test_malformed_payload_is_preserved() -> None:
    broken = b'{"stream":"btcusdt@trade","data":{"e":'
    assert decode(encode(record(broken))).payload == broken


def test_rest_channel_names_are_allowed() -> None:
    assert decode(encode(record(channel="rest:depth"))).channel == "rest:depth"


def test_rejects_newline_in_payload() -> None:
    with pytest.raises(RawFormatError):
        encode(record(b'{"a":\n1}'))


def test_rejects_unsafe_channel_name() -> None:
    with pytest.raises(RawFormatError):
        encode(record(channel='bad","m":1'))


@pytest.mark.parametrize("line", [b"", b"garbage\n", b'{"r":1,"s":"a","q":1,"c":"x"}\n'])
def test_decode_rejects_lines_without_envelope(line: bytes) -> None:
    with pytest.raises(RawFormatError):
        decode(line)
