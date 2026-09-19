"""Tests for Zulip image rewriting and private-attachment blocking."""

from __future__ import annotations

import pytest

from zulip_publisher.images import (
    AttachmentError,
    ImageStore,
    Variants,
    assert_no_private_attachments,
    find_private_attachments,
)


class StubStore(ImageStore):
    """Exercises the body-rewriting logic without R2 or Pillow.

    `ensure` is the only I/O seam; overriding it lets the tests assert how images
    become website lightbox markup vs Telegram JPEG references.
    """

    def __init__(self, fail: bool = False):
        self.public_host = "https://img.example.com"
        self.prefix = "img"
        self.fail = fail
        self.ensured: list[tuple[str, str]] = []

    def ensure(self, source_url, name, warn=None):
        self.ensured.append((source_url, name))
        if self.fail:
            if warn:
                warn(f"upload failed: {source_url}")
            return None
        base = f"{self.public_host}/{self.prefix}/abc123/{name}"
        return Variants(f"{base}.webp", f"{base}.png", f"{base}.jpg", 800, 600)


def test_website_rewrite_makes_lightbox_anchor():
    store = StubStore()
    body = "![photo](/user_uploads/1/2/photo.png)"
    out, refs = store.rewrite_for_website(body, "slug")
    assert '<a class="lightbox" href="https://img.example.com/img/abc123/slug.png">' in out
    assert '<img src="https://img.example.com/img/abc123/slug.webp"' in out
    assert 'alt="photo"' in out
    assert 'width="800" height="600"' in out
    assert len(refs) == 1
    assert refs[0].url.endswith("/slug.webp")


def test_telegram_rewrite_uses_jpg():
    store = StubStore()
    body = "![photo](/user_uploads/1/2/photo.png)"
    out, refs = store.rewrite_for_telegram(body, "slug")
    assert "![photo](https://img.example.com/img/abc123/slug.jpg)" in out
    assert refs[0].url.endswith("/slug.jpg")


def test_html_img_tag_is_handled():
    store = StubStore()
    body = '<img src="/user_uploads/1/2/photo.png" alt="x">'
    out, _ = store.rewrite_for_website(body, "slug")
    assert '<a class="lightbox"' in out
    assert 'alt="x"' in out


def test_external_image_left_untouched():
    store = StubStore()
    body = "![a](https://other.example.com/pic.png)"
    out, refs = store.rewrite_for_website(body, "slug")
    assert out == body
    assert refs[0].url == "https://other.example.com/pic.png"
    assert store.ensured == []


def test_multiple_images_get_distinct_names():
    store = StubStore()
    body = ("![one](/user_uploads/1/a.png)\n\n"
            "![two](/user_uploads/1/b.png)")
    store.rewrite_for_website(body, "slug")
    assert [name for _, name in store.ensured] == ["slug-1", "slug-2"]


def test_upload_failure_falls_back_to_source():
    store = StubStore(fail=True)
    body = "![photo](/user_uploads/1/2/photo.png)"
    warnings: list[str] = []
    out, refs = store.rewrite_for_website(body, "slug", warn=warnings.append)
    assert warnings
    assert out == body
    assert refs[0].url == "/user_uploads/1/2/photo.png"


def test_private_non_image_attachment_blocks():
    with pytest.raises(AttachmentError):
        assert_no_private_attachments('<a href="/user_uploads/1/2/report.pdf">report.pdf</a>')
    with pytest.raises(AttachmentError):
        assert_no_private_attachments("[report](/user_uploads/1/2/report.pdf)")


def test_private_image_attachment_is_allowed():
    # A private IMAGE is fine (it gets minted to R2); only non-image attachments block.
    assert_no_private_attachments("[pic](/user_uploads/1/2/photo.png)")
    assert find_private_attachments("![pic](/user_uploads/1/2/photo.png)") == []
