"""사진 받기 · 검증 · WebP 변환 · R2 업로드 (스펙 4-0)."""

import io

import pytest
from PIL import Image

from tests.photo_tools import make_image, r2, upload  # noqa: F401
from webapi import photo_upload


def opened(blob: bytes) -> Image.Image:
    return Image.open(io.BytesIO(blob))


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_formats_become_webp(fmt):
    (blob,) = photo_upload.process([upload(make_image(fmt), "x.bin")], min_count=1)
    im = opened(blob)
    assert im.format == "WEBP" and im.size == (800, 600)


def test_mpo_phone_photo_accepted():
    """갤럭시 · 일부 카메라의 다중 사진 JPEG(MPO). 첫 장만 WebP 로."""
    out = io.BytesIO()
    first, second = Image.new("RGB", (640, 480), "red"), Image.new("RGB", (320, 240), "blue")
    first.save(out, "MPO", save_all=True, append_images=[second])
    assert opened(out.getvalue()).format == "MPO"
    (blob,) = photo_upload.process([upload(out.getvalue())], min_count=1)
    assert opened(blob).format == "WEBP" and opened(blob).size == (640, 480)


def test_wide_photo_is_resized_to_1600():
    (blob,) = photo_upload.process([upload(make_image(size=(3200, 1000)))], min_count=1)
    assert opened(blob).size == (1600, 500)


def test_exif_and_gps_removed():
    src = make_image(exif=True)
    assert len(opened(src).getexif()) > 0  # 원본에는 EXIF 가 있다
    (blob,) = photo_upload.process([upload(src)], min_count=1)
    im = opened(blob)
    assert len(im.getexif()) == 0 and "exif" not in im.info


def test_order_kept():
    files = [upload(make_image(size=(100 + i, 100))) for i in range(3)]
    blobs = photo_upload.process(files, min_count=1)
    assert [opened(b).size[0] for b in blobs] == [100, 101, 102]


@pytest.mark.parametrize(
    ("files", "min_count"),
    [
        ([], 1),
        ([b"x"] * 4, 0),
        ([b"not an image"], 0),
        ([b"GIF89a" + b"\0" * 20], 0),
    ],
)
def test_bad_photos(files, min_count):
    with pytest.raises(photo_upload.BadPhoto):
        photo_upload.process([upload(f) for f in files], min_count=min_count)


def test_gif_rejected():
    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(out, "GIF")
    with pytest.raises(photo_upload.BadPhoto, match="1번째"):
        photo_upload.process([upload(out.getvalue())], min_count=1)


def test_too_big_rejected(monkeypatch):
    monkeypatch.setattr(photo_upload, "MAX_BYTES", 100)
    with pytest.raises(photo_upload.BadPhoto, match="2번째"):
        photo_upload.process([upload(b"x" * 50), upload(make_image())], min_count=1)


def test_no_photos_ok_when_optional():
    assert photo_upload.process([], min_count=0) == []


def test_upload_keys_and_urls(r2):  # noqa: F811
    done = photo_upload.upload("tip", [b"a", b"b"])
    assert len(done) == 2
    for key, url in done:
        assert key.startswith("uploads/tip/") and key.endswith(".webp")
        assert url == f"https://pub.r2.example/{key}"
        assert r2.objects[key][1] == "image/webp"
    assert [r2.objects[k][0] for k, _ in done] == [b"a", b"b"]


def test_upload_failure_cleans_up(r2):  # noqa: F811
    r2.fail_after = 1
    with pytest.raises(photo_upload.UploadFailed):
        photo_upload.upload("photo", [b"a", b"b"])
    assert r2.objects == {} and len(r2.deleted) == 1
