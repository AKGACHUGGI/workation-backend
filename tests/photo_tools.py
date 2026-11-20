"""사진 업로드 테스트 도구. 가짜 R2 와 Pillow 로 만든 테스트 이미지.

쓰는 쪽: from tests.photo_tools import make_image, r2, upload  # noqa: F401
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from webapi import storage

PUBLIC = "https://pub.r2.example"


class FakeR2:
    """put 한 key → (bytes, content_type).

    실패 주입: fail_after 번 성공한 뒤의 put, fail_copy 면 copy, fail_delete 면 delete 가
    StorageError.
    """

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []
        self.puts = 0
        self.fail_after: int | None = None
        self.fail_copy = False
        self.fail_delete = False

    def put(self, key, data, content_type):
        if self.fail_after is not None and self.puts >= self.fail_after:
            raise storage.StorageError("가짜 R2 실패")
        self.puts += 1
        self.objects[key] = (data, content_type)

    def copy(self, src, dst):
        if self.fail_copy or src not in self.objects:
            raise storage.StorageError("가짜 R2 복사 실패")
        self.objects[dst] = self.objects[src]

    def delete(self, key):
        if self.fail_delete:
            raise storage.StorageError("가짜 R2 삭제 실패")
        self.deleted.append(key)
        self.objects.pop(key, None)  # S3 처럼 없는 key 도 성공


@pytest.fixture
def r2(settings, monkeypatch):
    settings.R2_PUBLIC_URL = PUBLIC
    fake = FakeR2()
    monkeypatch.setattr(storage, "put", fake.put)
    monkeypatch.setattr(storage, "copy", fake.copy)
    monkeypatch.setattr(storage, "delete", fake.delete)
    return fake


def make_image(fmt: str = "JPEG", size=(800, 600), exif: bool = False) -> bytes:
    """단색 이미지. exif=True 면 카메라 · GPS 태그를 넣는다 (JPEG 만)."""
    im = Image.new("RGB", size, (200, 120, 40))
    out = io.BytesIO()
    kw = {}
    if exif:
        tags = Image.Exif()
        tags[0x010F] = "TestCam"  # Make
        tags[0x8825] = {1: "N", 2: (37.0, 30.0, 0.0), 3: "E", 4: (127.0, 0.0, 0.0)}  # GPSInfo
        kw["exif"] = tags.tobytes()
    im.save(out, fmt, **kw)
    return out.getvalue()


def upload(data: bytes, name: str = "a.jpg") -> SimpleUploadedFile:
    # content_type 은 일부러 틀리게 둘 수 있다. 서버는 내용으로 판단한다
    return SimpleUploadedFile(name, data, content_type="image/jpeg")
