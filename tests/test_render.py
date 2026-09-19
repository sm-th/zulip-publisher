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


def test_build_post_contains_source_and_telegram():
    fm = {"title": "Title", "description": None, "tags": ["one", "two"]}
    post = render.build_post(
        fm, "Body.", "2026-09-19T14:30:00+02:00",
        link="https://example.com",
        source_key="zulip:1:2",
        telegram_url="https://t.me/c/3",
    )
    assert "source: zulip:1:2" in post
    assert "telegram_url: https://t.me/c/3" in post
    assert 'link: https://example.com' in post
    assert "type: post" in post


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
