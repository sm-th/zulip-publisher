"""Receipt lookup and cross-note link resolution.

Receipts live in the website clone. Each post's frontmatter contains a stable
`source:` key; completed posts also contain a `telegram_url:`. This module scans
the clone to build a source-key -> receipt map.
"""

from __future__ import annotations

import glob
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from . import render
from .models import Receipt


_NARROW_CHANNEL = re.compile(r"narrow/channel/(\d+)-[^/]+/near/(\d+)")
_NARROW_STREAM = re.compile(r"narrow/stream/(\d+)-[^/]+/topic/([^/]+)/near/(\d+)")
_ZULIP_LINK = re.compile(r"https?://[^\s)\]]+|/near/\d+|/channel/\d+[^\s)\]]*")


@dataclass(frozen=True)
class LinkTarget:
    source_key: str
    url: str


class ReceiptStore:
    """Reads receipts from the website clone."""

    def __init__(self, clone_dir: str, posts_subdir: str, site_url: str):
        self.clone_dir = clone_dir
        self.posts_subdir = posts_subdir
        self.site_url = site_url.rstrip("/")

    def scan(self) -> dict[str, Receipt]:
        """Build a source-key -> Receipt map from existing site posts."""
        out: dict[str, Receipt] = {}
        pattern = os.path.join(self.clone_dir, self.posts_subdir,
                               "[0-9][0-9][0-9][0-9]", "*", "*", "*", "index.md")
        for path in sorted(glob.glob(pattern)):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            fm, _ = render.parse_frontmatter(text)
            source_key = fm.get("source")
            if not source_key:
                continue
            rel = os.path.relpath(path, self.clone_dir)
            parts = rel.split(os.sep)
            if len(parts) < 6:
                continue
            slug = parts[-2]
            year, mon, day = parts[-5], parts[-4], int(parts[-3])
            url = render.post_url(self.site_url, year, mon, day, slug)
            date_iso = fm.get("date", "")
            telegram_url = fm.get("telegram_url")
            out[source_key] = Receipt(
                source_key=source_key,
                slug=slug,
                date_iso=date_iso,
                website_url=url,
                telegram_url=telegram_url,
            )
        return out


def extract_zulip_links(text: str, zulip_host: str) -> list[LinkTarget]:
    """Find all private Zulip links in text and return their source keys."""
    out: list[LinkTarget] = []
    seen: set[str] = set()
    for m in _ZULIP_LINK.finditer(text):
        url = m.group(0)
        parsed = urllib.parse.urlsplit(url)
        host = parsed.netloc or zulip_host
        if host != zulip_host:
            continue
        source_key = _source_key_from_fragment(parsed.fragment) or _source_key_from_path(parsed.path)
        if source_key and source_key not in seen:
            seen.add(source_key)
            out.append(LinkTarget(source_key=source_key, url=url))
    return out


def _source_key_from_fragment(fragment: str) -> str | None:
    m = _NARROW_CHANNEL.search(fragment)
    if m:
        return f"zulip:{m.group(1)}:{m.group(2)}"
    m = _NARROW_STREAM.search(fragment)
    if m:
        return f"zulip:{m.group(1)}:{m.group(3)}"
    return None


def _source_key_from_path(path: str) -> str | None:
    # Older /near/<id> links without stream require message lookup; unsupported.
    return None


def resolve_links(text: str, zulip_host: str, receipts: dict[str, Receipt],
                  for_telegram: bool = False,
                  warn: Callable[[str], None] | None = None) -> tuple[str, list[str]]:
    """Rewrite private Zulip links to public URLs.

    Returns (rewritten_text, unresolved_source_keys). For Telegram, use the
    target's telegram_url; for the website, use website_url. If the target has
    no telegram_url yet, it counts as unresolved.
    """
    links = extract_zulip_links(text, zulip_host)
    unresolved: list[str] = []
    mapping: dict[str, str] = {}
    for target in links:
        rec = receipts.get(target.source_key)
        if rec is None:
            unresolved.append(target.source_key)
            continue
        if for_telegram:
            if rec.telegram_url is None:
                unresolved.append(target.source_key)
                continue
            mapping[target.url] = rec.telegram_url
        else:
            mapping[target.url] = rec.website_url
    if unresolved:
        return text, unresolved

    out = text
    for old, new in mapping.items():
        out = out.replace(old, new)
    return out, []
