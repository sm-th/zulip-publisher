"""Tests for image handling and non-image attachment blocking."""

from __future__ import annotations

import pytest

from zulip_publisher.images import ImageStore, ImageError


class RecordingImageStore(ImageStore):
    def __init__(self):
        self.blocked: list[str] = []
        self.rewritten: list[tuple[str, str]] = []

    def upload(self, url: str, slug: str, alt: str = "") -> str:
        if "bad" in url:
            raise ImageError("not an image")
        return f"https://img.example.com/{slug}/image.png"

    def rewrite_body(self, body: str, slug: str, warn=None):
        new, refs = super().rewrite_body(body, slug, warn)
        self.rewritten.append((body, new))
        return new, refs

    def block_attachments(self, body: str, warn=None):
        new = super().block_attachments(body, warn)
        if new != body:
            self.blocked.append(body)
        return new


def test_block_private_non_image_attachment():
    store = RecordingImageStore()
    body = '<a href="/user_uploads/1/2/file.pdf">report.pdf</a>'
    out = store.block_attachments(body)
    assert "[attachment omitted]" in out
    assert "file.pdf" not in out


def test_rewrites_zulip_image_to_public_url():
    store = RecordingImageStore()
    body = '<img src="/user_uploads/1/2/photo.png" alt="photo">'
    out, refs = store.rewrite_body(body, "slug")
    assert "![photo](https://img.example.com/slug/image.png)" in out
    assert len(refs) == 1
    assert refs[0].url == "https://img.example.com/slug/image.png"


def test_external_image_left_untouched():
    store = RecordingImageStore()
    body = '![alt](https://other.example.com/pic.png)'
    out, refs = store.rewrite_body(body, "slug")
    assert out == body


def test_image_upload_failure_falls_back_to_source():
    store = RecordingImageStore()
    body = '<img src="/user_uploads/1/2/bad.png">'
    warnings: list[str] = []
    out, refs = store.rewrite_body(body, "slug", warn=warnings.append)
    assert warnings
    # Body unchanged when upload fails.
    assert out == body
    assert len(refs) == 1
    assert refs[0].url == "/user_uploads/1/2/bad.png"
