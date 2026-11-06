import base64
import hashlib
import io
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

from trader.storage.layout import StorageError
from trader.storage.objectstore import S3ObjectStore


def client() -> object:
    return boto3.session.Session(
        aws_access_key_id="teste", aws_secret_access_key="teste", region_name="ap-northeast-1"
    ).client("s3")


def test_put_sends_sha256_for_server_side_verification(tmp_path: Path) -> None:
    local = tmp_path / "a.jsonl.zst"
    local.write_bytes(b"conteudo")
    sha = hashlib.sha256(b"conteudo").hexdigest()
    s3 = client()
    with Stubber(s3) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "trader-data",
                "Key": "raw/x/a.jsonl.zst",
                "Body": ANY,
                "ChecksumSHA256": base64.b64encode(bytes.fromhex(sha)).decode(),
            },
        )
        S3ObjectStore(s3, "trader-data").put(local, "raw/x/a.jsonl.zst", sha)
        stub.assert_no_pending_responses()


def test_s3_errors_become_storage_errors(tmp_path: Path) -> None:
    local = tmp_path / "a.bin"
    local.write_bytes(b"x")
    s3 = client()
    with Stubber(s3) as stub:
        stub.add_client_error("put_object", service_error_code="BadDigest", http_status_code=400)
        with pytest.raises(StorageError):
            S3ObjectStore(s3, "trader-data").put(local, "k", hashlib.sha256(b"x").hexdigest())


def _body(data: bytes) -> object:
    from botocore.response import StreamingBody

    return StreamingBody(io.BytesIO(data), len(data))


def test_list_keys_pages_and_sorts() -> None:
    s3 = client()
    with Stubber(s3) as stub:
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "p/b"}, {"Key": "p/a"}], "IsTruncated": False},
            {"Bucket": "trader-data", "Prefix": "p/"},
        )
        assert S3ObjectStore(s3, "trader-data").list_keys("p/") == ["p/a", "p/b"]


def test_fetch_checks_the_stored_sha256(tmp_path: Path) -> None:
    data = b"parquet"
    good = base64.b64encode(hashlib.sha256(data).digest()).decode()
    bad = base64.b64encode(hashlib.sha256(b"outro").digest()).decode()
    s3 = client()
    expected = {"Bucket": "trader-data", "Key": "k", "ChecksumMode": "ENABLED"}
    with Stubber(s3) as stub:
        stub.add_response("get_object", {"Body": _body(data), "ChecksumSHA256": good}, expected)
        stub.add_response("get_object", {"Body": _body(data), "ChecksumSHA256": bad}, expected)
        store = S3ObjectStore(s3, "trader-data")
        assert store.fetch("k", tmp_path / "a") == hashlib.sha256(data).hexdigest()
        assert (tmp_path / "a").read_bytes() == data
        with pytest.raises(StorageError, match="corrompida"):
            store.fetch("k", tmp_path / "b")
    assert not (tmp_path / "b").exists()
    assert not (tmp_path / "b.tmp").exists()
