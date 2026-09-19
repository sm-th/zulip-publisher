"""R2 image uploads and body rewriting for Zulip Markdown.

Zulip message bodies are Markdown, so images arrive as `![alt](/user_uploads/...)`
or occasionally as raw `<img src=...>`. Each Zulip-hosted image is minted into
three content-addressed R2 renditions using a proven scheme:

    <prefix>/<sha1>/<name>.webp   compressed, transparent  -> inline on the page
    <prefix>/<sha1>/<name>.png    full-res,  transparent   -> opened by the lightbox
    <prefix>/<sha1>/<name>.jpg    full-res,  #EEEEEE bg     -> Telegram / social

Private `/user_uploads/` downloads are authenticated through the Zulip client.
External images are left exactly as written. A private, non-image `/user_uploads/`
attachment is never rewritten or leaked -- it blocks the Publication.
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
except ImportError:  # pragma: no cover
    boto3 = None
    BotoConfig = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


_IMG_ANY = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<mdurl>[^)\s]+)\)"
    r"|<img[^>]+src=[\"'](?P<tagurl>[^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
_IMG_ALT = re.compile(r'\balt=["\']([^"\']*)["\']', re.IGNORECASE)
_MD_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")
_A_TAG = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_UPLOAD = "/user_uploads/"

_MEDIA_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
                "image/webp": "webp", "image/svg+xml": "svg"}
_CTYPE = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
          "webp": "image/webp", "gif": "image/gif", "svg": "image/svg+xml"}
_UA = "Mozilla/5.0 (compatible; zulip-publisher imagebot)"
_SHA_RE = re.compile(r"^[0-9a-f]{16,}$")

INLINE_MAX_W = 1280
INLINE_QUALITY = 82
SOCIAL_QUALITY = 95
SOCIAL_BG = (0xEE, 0xEE, 0xEE)


class ImageError(RuntimeError):
    pass


class AttachmentError(RuntimeError):
    """A private, non-image Zulip attachment cannot be published safely."""


@dataclass(frozen=True)
class ImageRef:
    alt: str
    url: str


@dataclass(frozen=True)
class Variants:
    inline: str
    full: str
    social: str
    width: int = 0
    height: int = 0


def is_zulip_hosted(url: str) -> bool:
    return url.startswith(_UPLOAD) or _UPLOAD in urllib.parse.urlsplit(url).path


def looks_like_image(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    return any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))


def find_private_attachments(body: str) -> list[str]:
    """Private, non-image `/user_uploads/` links (HTML `<a>` or Markdown link)."""
    found: list[str] = []
    for m in _A_TAG.finditer(body):
        url = m.group(1)
        if is_zulip_hosted(url) and not looks_like_image(url):
            found.append(url)
    for m in _MD_LINK.finditer(body):
        url = m.group(2)
        if is_zulip_hosted(url) and not looks_like_image(url):
            found.append(url)
    return found


def assert_no_private_attachments(body: str) -> None:
    """Raise if the body carries a private, non-image Zulip attachment.

    Such an attachment must never be silently dropped (loses content) or copied
    verbatim (leaks a private URL), so the Publication is blocked until the author
    removes or inlines it.
    """
    private = find_private_attachments(body)
    if private:
        raise AttachmentError(
            "refusing to publish private non-image attachment(s): " + ", ".join(private)
        )


class ImageStore:
    """Content-addressed R2 image store minting three renditions per image."""

    def __init__(self, endpoint: str, bucket: str, access_key_id: str,
                 secret_access_key: str, public_host: str, prefix: str,
                 fetch: Callable[[str], bytes] | None = None):
        if boto3 is None:
            raise ImageError("boto3 is required for image uploads")
        self.bucket = bucket
        self.public_host = public_host.rstrip("/")
        self.prefix = prefix.strip("/")
        self.fetch = fetch
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=BotoConfig(signature_version="s3v4"),
        )

    def set_fetcher(self, fetch: Callable[[str], bytes]) -> None:
        self.fetch = fetch

    # -- rendition minting ----------------------------------------------------

    def variant_urls(self, sha1: str, name: str) -> Variants:
        base = f"{self.public_host}/{self.prefix}/{sha1}/{name}"
        return Variants(f"{base}.webp", f"{base}.png", f"{base}.jpg")

    def ensure(self, source_url: str, name: str,
               warn: Callable[[str], None] | None = None) -> Variants | None:
        """Ensure the three renditions exist and return their public URLs, or None
        when the source can't be fetched or decoded (caller leaves the ref as-is)."""
        warn = warn or (lambda _m: None)
        sha1 = _sha_from_url(source_url)
        if sha1 and self._all_present(sha1, name):
            return self.variant_urls(sha1, name)
        try:
            data = self._download(source_url)
        except Exception as e:
            warn(f"image download failed ({source_url}): {e}")
            return None
        if sha1 is None:
            sha1 = hashlib.sha1(data).hexdigest()
        try:
            renditions, (iw, ih) = _render_all(data)
        except _Unprocessable:
            return self._host_original(sha1, source_url, data, name)
        except Exception as e:
            warn(f"image processing failed ({source_url}): {e}")
            return None
        keys = self._keys(sha1, name)
        for kind, (payload, ctype) in renditions.items():
            key = keys[kind]
            if not self._exists(key):
                self._put(key, payload, ctype)
        v = self.variant_urls(sha1, name)
        return Variants(v.inline, v.full, v.social, iw, ih)

    # -- body rewriting -------------------------------------------------------

    def rewrite_for_website(self, body: str, slug: str,
                            warn: Callable[[str], None] | None = None) -> tuple[str, list[ImageRef]]:
        return self._rewrite(body, slug, "website", warn)

    def rewrite_for_telegram(self, body: str, slug: str,
                             warn: Callable[[str], None] | None = None) -> tuple[str, list[ImageRef]]:
        return self._rewrite(body, slug, "telegram", warn)

    def _rewrite(self, body: str, slug: str, kind: str,
                 warn: Callable[[str], None] | None) -> tuple[str, list[ImageRef]]:
        warn = warn or (lambda _m: None)
        refs: list[ImageRef] = []
        total = self._count_hosted(body)
        seen = [0]

        def emit(alt: str, url: str, original: str) -> str:
            if not is_zulip_hosted(url):
                refs.append(ImageRef(alt=alt, url=url))
                return original
            seen[0] += 1
            name = slug if total <= 1 else f"{slug}-{seen[0]}"
            variants = self.ensure(url, name, warn)
            if variants is None:
                refs.append(ImageRef(alt=alt, url=url))
                return original
            if kind == "telegram":
                refs.append(ImageRef(alt=alt, url=variants.social))
                return f"![{alt}]({variants.social})"
            refs.append(ImageRef(alt=alt, url=variants.inline))
            dim = f' width="{variants.width}" height="{variants.height}"' if variants.width and variants.height else ""
            return (f'<a class="lightbox" href="{variants.full}">'
                    f'<img src="{variants.inline}" alt="{_esc(alt)}"{dim} '
                    f'loading="lazy" decoding="async"></a>')

        def repl(m: re.Match) -> str:
            if m.group("mdurl") is not None:
                return emit(m.group("alt") or "", m.group("mdurl"), m.group(0))
            alt_m = _IMG_ALT.search(m.group(0))
            return emit(alt_m.group(1) if alt_m else "", m.group("tagurl"), m.group(0))

        # One pass over both Markdown and HTML images so generated markup (a
        # Markdown image emits `<img>`, an HTML image emits an anchor) is never
        # re-matched and double-counted.
        out = _IMG_ANY.sub(repl, body)
        return out, refs

    def _count_hosted(self, body: str) -> int:
        urls = [m.group("mdurl") or m.group("tagurl") for m in _IMG_ANY.finditer(body)]
        return sum(1 for u in urls if u and is_zulip_hosted(u))

    # -- internals ------------------------------------------------------------

    def _download(self, url: str) -> bytes:
        if self.fetch is not None:
            return self.fetch(url)
        return _download(url)

    def _keys(self, sha1: str, name: str) -> dict:
        base = f"{self.prefix}/{sha1}/{name}"
        return {"inline": f"{base}.webp", "full": f"{base}.png", "social": f"{base}.jpg"}

    def _all_present(self, sha1: str, name: str) -> bool:
        keys = self._keys(sha1, name)
        return all(self._exists(k) for k in keys.values())

    def _host_original(self, sha1: str, source_url: str, data: bytes, name: str) -> Variants:
        ext = (_ext_from_url(source_url) or "bin").lower()
        ctype = _CTYPE.get(ext, "application/octet-stream")
        key = f"{self.prefix}/{sha1}/{name}.{ext}"
        if not self._exists(key):
            self._put(key, data, ctype)
        url = f"{self.public_host}/{key}"
        return Variants(url, url, url)

    def _exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=body,
                            ContentType=content_type or "application/octet-stream")

    @classmethod
    def from_config(cls, cfg, fetch: Callable[[str], bytes] | None = None) -> "ImageStore | None":
        if not (cfg.r2_endpoint and cfg.r2_bucket and cfg.image_media_host):
            return None
        return cls(
            endpoint=cfg.r2_endpoint,
            bucket=cfg.r2_bucket,
            access_key_id=cfg.r2_access_key_id,
            secret_access_key=cfg.r2_secret_access_key,
            public_host=cfg.image_media_host,
            prefix=cfg.image_prefix,
            fetch=fetch,
        )


class _Unprocessable(Exception):
    """Not a still raster image (e.g. animated); host the original bytes."""


def _render_all(data: bytes) -> tuple[dict, tuple[int, int]]:
    if Image is None:
        raise ImageError("Pillow is required for image processing")
    im = Image.open(io.BytesIO(data))
    if getattr(im, "n_frames", 1) > 1:
        raise _Unprocessable()
    im.load()
    rgba = im.convert("RGBA")
    inline_im = _fit_width(rgba, INLINE_MAX_W)
    inline = _encode(inline_im, "WEBP", quality=INLINE_QUALITY, method=6)
    full = _encode(rgba, "PNG", optimize=True)
    flat = Image.new("RGB", rgba.size, SOCIAL_BG)
    flat.paste(rgba, mask=rgba.split()[-1])
    social = _encode(flat, "JPEG", quality=SOCIAL_QUALITY)
    return (
        {"inline": (inline, "image/webp"),
         "full": (full, "image/png"),
         "social": (social, "image/jpeg")},
        inline_im.size,
    )


def _fit_width(im, max_w: int):
    if im.width <= max_w:
        return im
    h = max(1, round(im.height * max_w / im.width))
    return im.resize((max_w, h), Image.LANCZOS)


def _encode(im, fmt: str, **params) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format=fmt, **params)
    return buf.getvalue()


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _sha_from_url(url: str) -> str | None:
    tail = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    stem = tail.rsplit(".", 1)[0]
    return stem if _SHA_RE.match(stem) else None


def _ext_from_url(url: str) -> str | None:
    tail = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[1] if "." in tail else None


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace('"', "&quot;")
             .replace("<", "&lt;").replace(">", "&gt;"))
