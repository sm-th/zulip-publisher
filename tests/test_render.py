"""Tests for rendering, link posts, author timezone, and dates."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from zulip_publisher import render
from zulip_publisher.render import RenderError


def test_link_post_detection():
    body = "https://example.com/page\n\nSome commentary."
    link, rest = render.split_link(body)
    assert link == "https://example.com/page"
    assert rest == "Some commentary."


def test_non_link_post():
    body = "Some commentary.\n\nhttps://example.com/page"
    link, rest = render.split_link(body)
    assert link is None
    assert rest == body


def test_author_timezone_conversion():
    iso = "2026-09-19T12:00:00Z"
    local = render.to_local(iso, "Europe/Berlin")
    assert "+02:00" in local or "+01:00" in local
    assert "2026-09-19T14:00:00" in local or "2026-09-19T13:00:00" in local


def test_fallback_timezone():
    iso = "2026-09-19T12:00:00Z"
    local = render.to_local(iso, "Invalid/Timezone")
    # Should fall back to system local; just ensure it parses.
    assert "2026-09-19" in local


def test_date_parts_and_slug():
    iso = "2026-09-19T14:30:00+02:00"
    year, mon, day = render.date_parts(iso)
    assert year == "2026"
    assert mon == "Sep"
    assert day == 19
    assert render.slugify("Hello World!") == "hello-world"


def test_build_post_minimal_public_frontmatter():
    post = render.build_post(
        {"title": "Title"}, "Body.", "2026-09-19T14:30:00+02:00",
        post_type="link", link="https://example.com",
    )
    assert "title: Title" in post
    assert "type: link" in post
    assert "link: https://example.com" in post
    assert "date: 2026-09-19T14:30:00+02:00" in post
    # Bookkeeping and generated metadata never appear in the public post.
    assert "source:" not in post
    assert "telegram_url:" not in post
    assert "description:" not in post
    assert "tags:" not in post


def test_build_post_defaults_to_post_type():
    post = render.build_post({"title": "T"}, "B", "2026-09-19T14:30:00+02:00")
    assert "type: post" in post


def test_link_post_requires_link():
    with pytest.raises(RenderError):
        render.build_post({"title": "T"}, "B", "2026-09-19T14:30:00+02:00", post_type="link")


def test_strip_duplicate_title_heading():
    body = "# Title\n\nBody text."
    assert render.strip_title_heading(body, "Title") == "Body text."


def test_telegram_artifact_build():
    text = render.build_telegram_post(
        "Title", "Body.", "https://site.example.com/2026/Sep/19/slug/",
        link="https://example.com",
        image_url="https://img.example.com/x.png",
    )
    assert "site_url: https://site.example.com/2026/Sep/19/slug/" in text
    assert 'link: https://example.com' in text
    assert "image: https://img.example.com/x.png" in text


def test_invalid_date_raises():
    with pytest.raises(RenderError):
        render.to_local("not-a-date", None)
