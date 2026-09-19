"""R2 image uploads and body rewriting.

Reuses the content-addressed R2 approach of
~/reference/11ty-publisher/eleventy_publisher/imagekit.py, adapted
for Zulip's HTML message content.
"""

from __future__ import annotations

import hashlib
import io
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover
    boto3 = None
    BotoConfig = None
    ClientError = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


_IMG_TAG = re.compile(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)
_IMG_ALT = re.compile(r'\balt=["\']([^"\']*)["\']', re.IGNORECASE)
_ATTACH = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_ZULIP_UPLOAD = re.compile(r"/user_uploads/")
_MEDIA_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
                "image/webp": "webp", "image/svg+xml": "svg"}
_UA = "Mozilla/5.0 (compatible; zulip-publisher imagebot)"


class ImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageRef:
    alt: str
    url: str


class ImageStore:
    """Content-addressed R2 image store."""

    def __init__(self, endpoint: str, bucket: str, access_key_id: str,
                 secret_access_key: str, public_host: str, prefix: str):
        if boto3 is None:
            raise ImageError("boto3 is required for image uploads")
        self.bucket = bucket
        self.public_host = public_host.rstrip("/")
        self.prefix = prefix.strip("/")
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=BotoConfig(signature_version="s3v4"),
        )

    def _key(self, sha: str, slug: str, ext: str) -> str:
        name = f"{slug}.{ext}" if ext else slug
        return f"{self.prefix}/{sha}/{name}" if self.prefix else f"{sha}/{name}"

    def _public_url(self, key: str) -> str:
        return f"{self.public_host}/{key}"

    def upload(self, url: str, slug: str, alt: str = "") -> str:
        """Download an image, verify it, and upload it to R2. Return public URL."""
        data, content_type = _download(url)
        if Image is not None:
            try:
                im = Image.open(io.BytesIO(data))
                im.verify()
                ext = (im.format or "").lower()
                if ext == "jpeg":
                    ext = "jpg"
            except Exception as e:
                raise ImageError(f"not a processable image: {e}") from None
        else:
            ext = _ext_from_content_type(content_type)
        sha = hashlib.sha1(data).hexdigest()
        key = self._key(sha, slug, ext or "img")
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] != "404":
                raise
            self._s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
            )
        return self._public_url(key)

    def rewrite_body(self, body: str, slug: str,
                     warn: Callable[[str], None] | None = None) -> tuple[str, list[ImageRef]]:
        """Rewrite <img> tags to public R2 URLs and collect references."""
        refs: list[ImageRef] = []

        def repl(m: re.Match) -> str:
            url = m.group(1)
            alt_m = _IMG_ALT.search(m.group(0))
            alt = alt_m.group(1) if alt_m else ""
            if not _is_zulip_hosted(url):
                refs.append(ImageRef(alt=alt, url=url))
                return m.group(0)
            try:
                public = self.upload(url, slug, alt)
            except ImageError as e:
                if warn:
                    warn(f"image {url}: {e}")
                refs.append(ImageRef(alt=alt, url=url))
                return m.group(0)
            refs.append(ImageRef(alt=alt, url=public))
            return f'![{alt}]({public})'

        new_body = _IMG_TAG.sub(repl, body)
        return new_body, refs

    def block_attachments(self, body: str,
                          warn: Callable[[str], None] | None = None) -> str:
        """Replace private non-image attachment links with a placeholder."""
        def repl(m: re.Match) -> str:
            url = m.group(1)
            if not _is_zulip_hosted(url):
                return m.group(0)
            if _looks_like_image(url):
                return m.group(0)
            if warn:
                warn(f"blocked attachment: {url}")
            return "[attachment omitted]"
        return _ATTACH.sub(repl, body)

    @classmethod
    def from_config(cls, cfg) -> ImageStore | None:
        """Build an ImageStore from a Config, or None when the bucket isn't configured."""
        if not (cfg.r2_endpoint and cfg.r2_bucket and cfg.image_media_host):
            return None
        return cls(
            endpoint=cfg.r2_endpoint,
            bucket=cfg.r2_bucket,
            access_key_id=cfg.r2_access_key_id,
            secret_access_key=cfg.r2_secret_access_key,
            public_host=cfg.image_media_host,
            prefix=cfg.image_prefix,
        )


def store_from_config(cfg) -> ImageStore | None:
    """Compatibility alias."""
    return ImageStore.from_config(cfg)


def _download(url: str) -> tuple[bytes, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read(), resp.headers.get("Content-Type")
    except Exception as e:
        raise ImageError(f"download failed: {e}") from None


def _is_zulip_hosted(url: str) -> bool:
    return bool(_ZULIP_UPLOAD.search(url)) or url.startswith("/user_uploads/")


def _looks_like_image(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))


def _ext_from_content_type(ct: str | None) -> str | None:
    if not ct:
        return None
    return _MEDIA_TYPES.get(ct.split(";")[0].strip().lower())
