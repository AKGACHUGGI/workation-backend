"""사진 받기 · 검증 · WebP 변환 · R2 업로드. 노트 사진 · 이달의 사진 공통 (스펙 4-0).

- 파일 이름 · 요청의 Content-Type 은 믿지 않고 Pillow 로 열어 본 형식으로 판단한다.
- 가로 1600px 이하 WebP(quality 82)로 다시 인코딩한다. 메타데이터를 옮기지 않아
  EXIF(GPS 포함)가 사라진다.
- 모두 검증 · 변환한 뒤에 올린다. 올리다 실패하면 이번에 올린 것을 지운다.
"""

from __future__ import annotations

import io
import logging
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from webapi import storage

logger = logging.getLogger(__name__)

MAX_PHOTOS = 3
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000  # 압축 폭탄 방지
MAX_WIDTH = 1600  # 관광공사 사진(photos/)과 같은 규격
QUALITY = 82
# MPO = 다중 사진 JPEG (갤럭시 · 일부 카메라). Pillow 는 JPEG 하위 형식으로 열고 첫 장만 저장한다
FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}


class BadPhoto(ValueError):
    """받은 사진이 규칙에 맞지 않는다. 메시지는 그대로 400 INVALID_PHOTO 에 담긴다."""


class UploadFailed(RuntimeError):
    """R2 에 올리지 못했다 (502 UPLOAD_FAILED)."""


def process(files: list, *, min_count: int) -> list[bytes]:
    """업로드 파일들 → WebP 바이트 (들어온 순서). min_count ~ MAX_PHOTOS 장."""
    if not min_count <= len(files) <= MAX_PHOTOS:
        raise BadPhoto(f"사진은 {min_count}~{MAX_PHOTOS}장 올릴 수 있어요.")
    # 크기는 디코딩 전에 모두 본다 (큰 파일이 뒤에 있어도 앞 사진을 헛되이 변환하지 않게)
    for n, f in enumerate(files, 1):
        if f.size > MAX_BYTES:
            raise BadPhoto(f"{n}번째 사진이 {MAX_BYTES // (1024 * 1024)}MB 를 넘어요.")
    return [_to_webp(f, n) for n, f in enumerate(files, 1)]


def _to_webp(file, n: int) -> bytes:
    try:
        with Image.open(file) as src:
            if src.format not in FORMATS:
                raise BadPhoto(f"{n}번째 사진은 JPEG · PNG · WebP 만 올릴 수 있어요.")
            if src.width * src.height > MAX_PIXELS:
                raise BadPhoto(f"{n}번째 사진이 너무 커요.")
            im = ImageOps.exif_transpose(src)
            if im.width > MAX_WIDTH:
                height = round(im.height * MAX_WIDTH / im.width)
                im = im.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "transparency" in im.info or "A" in im.mode else "RGB")
            im.info = {}  # EXIF · XMP · ICC 를 옮기지 않는다
            out = io.BytesIO()
            im.save(out, "WEBP", quality=QUALITY, exif=b"")
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as ex:
        raise BadPhoto(f"{n}번째 파일은 사진이 아니에요.") from ex


def upload(kind: str, blobs: list[bytes]) -> list[tuple[str, str]]:
    """WebP 들을 uploads/{kind}/{uuid}.webp 로 올린다 → [(key, 공개 URL)].

    하나라도 실패하면 이번에 올린 것을 지우고 UploadFailed.
    """
    done: list[str] = []
    try:
        for blob in blobs:
            key = f"uploads/{kind}/{uuid.uuid4()}.webp"
            storage.put(key, blob, "image/webp")
            done.append(key)
    except storage.StorageError as ex:
        discard(done)
        raise UploadFailed(str(ex)) from ex
    return [(key, storage.public_url(key)) for key in done]


def discard(keys: list[str]) -> None:
    """올린 사진을 지운다 (DB 쓰기 실패 뒤 정리). 지우지 못해도 로그만 남긴다."""
    for key in keys:
        try:
            storage.delete(key)
        except storage.StorageError:
            logger.warning("R2 정리 실패: %s", key)
