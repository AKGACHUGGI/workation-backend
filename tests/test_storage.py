"""R2 저장소 래퍼. 실제 R2 대신 boto3 클라이언트를 가짜로 바꿔 넘기는 값만 본다."""

import pytest
from botocore.exceptions import ClientError

from webapi import storage


class _Client:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def put_object(self, **kw):
        self.calls.append(("put", kw))
        if self.fail:
            raise ClientError({"Error": {"Code": "500", "Message": "boom"}}, "PutObject")

    def delete_object(self, **kw):
        self.calls.append(("delete", kw))

    def copy_object(self, **kw):
        self.calls.append(("copy", kw))


@pytest.fixture
def r2_settings(settings):
    settings.R2_ACCOUNT_ID = "acc"
    settings.R2_ACCESS_KEY_ID = "key"
    settings.R2_SECRET_ACCESS_KEY = "secret"
    settings.R2_BUCKET = "bucket"
    settings.R2_PUBLIC_URL = "https://pub.r2.example"


def test_put_and_delete_pass_bucket_and_key(r2_settings, monkeypatch):
    client = _Client()
    monkeypatch.setattr(storage, "_client", lambda: client)
    storage.put("uploads/photo/x.webp", b"abc", "image/webp")
    storage.delete("uploads/photo/x.webp")
    (op1, put), (op2, delete) = client.calls
    assert (op1, op2) == ("put", "delete")
    assert put["Bucket"] == "bucket" and put["Key"] == "uploads/photo/x.webp"
    assert put["Body"] == b"abc" and put["ContentType"] == "image/webp"
    assert put["CacheControl"] == "public, max-age=3600"  # 가린 사진이 캐시에 오래 남지 않게
    assert delete == {"Bucket": "bucket", "Key": "uploads/photo/x.webp"}


def test_copy_within_bucket(r2_settings, monkeypatch):
    client = _Client()
    monkeypatch.setattr(storage, "_client", lambda: client)
    storage.copy("uploads/photo/a.webp", "uploads/quarantine/b.webp")
    ((op, kw),) = client.calls
    assert op == "copy"
    assert kw["Bucket"] == "bucket" and kw["Key"] == "uploads/quarantine/b.webp"
    assert kw["CopySource"] == {"Bucket": "bucket", "Key": "uploads/photo/a.webp"}


def test_client_error_becomes_storage_error(r2_settings, monkeypatch):
    monkeypatch.setattr(storage, "_client", lambda: _Client(fail=True))
    with pytest.raises(storage.StorageError):
        storage.put("uploads/photo/x.webp", b"abc", "image/webp")


def test_not_configured_is_storage_error():
    with pytest.raises(storage.StorageError):  # settings_test 는 R2 설정이 비어 있다
        storage.put("uploads/photo/x.webp", b"abc", "image/webp")


def test_public_url(r2_settings):
    assert storage.public_url("uploads/tip/a.webp") == "https://pub.r2.example/uploads/tip/a.webp"
