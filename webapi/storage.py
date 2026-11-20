"""Cloudflare R2 (S3 호환 API) 쓰기. R2 는 이 모듈에서만 부른다.

다른 코드는 boto3 를 직접 쓰지 않는다.

버킷은 데이터 담당 소유라 사용자 사진은 uploads/ 아래에만 쓴다.
테스트는 put · copy · delete 를 가짜로 바꾼다 (tests/photo_tools.py 의 r2 픽스처).
"""

from functools import cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


class StorageError(RuntimeError):
    """R2 에 쓰거나 지우지 못했다 (설정 없음 포함)."""


@cache
def _client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            connect_timeout=3,
            read_timeout=10,
            retries={"max_attempts": 2},
        ),
    )


def _bucket() -> str:
    if not (settings.R2_ACCOUNT_ID and settings.R2_BUCKET):
        raise StorageError("R2 설정이 없습니다.")
    return settings.R2_BUCKET


def public_url(key: str) -> str:
    return f"{settings.R2_PUBLIC_URL}/{key}"


def put(key: str, data: bytes, content_type: str) -> None:
    bucket = _bucket()
    try:
        # 가린 사진이 브라우저 · CDN 캐시에 오래 남지 않게 1시간만 (스펙 2절)
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=3600",
        )
    except (BotoCoreError, ClientError) as ex:
        raise StorageError(str(ex)) from ex


def copy(src: str, dst: str) -> None:
    """같은 버킷 안에서 복사 (격리 · 복구). Content-Type · Cache-Control 도 따라간다."""
    bucket = _bucket()
    try:
        _client().copy_object(Bucket=bucket, Key=dst, CopySource={"Bucket": bucket, "Key": src})
    except (BotoCoreError, ClientError) as ex:
        raise StorageError(str(ex)) from ex


def delete(key: str) -> None:
    bucket = _bucket()
    try:
        _client().delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError) as ex:
        raise StorageError(str(ex)) from ex
