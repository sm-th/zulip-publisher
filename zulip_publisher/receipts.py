"""Receipt storage and cross-note link resolution.

A Receipt is the publisher's durable, private record of one Publication: which
Zulip Source Note produced which website slug/URL, at which Source Revision, and
whether the Telegram edition has caught up. Receipts live in the website clone
under `.zulip-publisher/receipts/<key>.json` -- co-located with the site so a
fresh checkout reconstructs full publication state, but kept OUT of the public
post frontmatter.
"""

from __future__ import annotations

import glob
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from .models import Receipt


RECEIPTS_SUBDIR = os.path.join(".zulip-publisher", "receipts")

_NARROW_CHANNEL = re.compile(r"narrow/channel/(\d+)-[^/]+/near/(\d+)")
_NARROW_STREAM = re.compile(r"narrow/stream/(\d+)-[^/]+/topic/([^/]+)/near/(\d+)")
_ZULIP_LINK = re.compile(r"https?://[^\s)\]]+")


@dataclass(frozen=True)
class LinkTarget:
    """A private Zulip link found in body text.

    `near_key` is a `zulip:<stream>:<near-id>` key built from the link's `near/`
    id -- which may point at a REPLY, not the Source Note. `url` is the exact text
    matched, used for verbatim replacement.
    """
    near_key: str
    url: str


class ReceiptStore:
    """Reads and writes receipts in the website clone."""

    def __init__(self, clone_dir: str, posts_subdir: str, site_url: str):
        self.clone_dir = clone_dir
        self.posts_subdir = posts_subdir
        self.site_url = site_url.rstrip("/")

    def rel_path(self, source_key: str) -> str:
        safe = source_key.replace(":", "_")
        return os.path.join(RECEIPTS_SUBDIR, f"{safe}.json")

    def _abs(self, source_key: str) -> str:
        return os.path.join(self.clone_dir, self.rel_path(source_key))

    def scan(self) -> dict[str, Receipt]:
        """Build a source-key -> Receipt map from the receipt JSON files."""
        out: dict[str, Receipt] = {}
        pattern = os.path.join(self.clone_dir, RECEIPTS_SUBDIR, "*.json")
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            key = raw.get("source_key")
            if not key:
                continue
            out[key] = Receipt(
                source_key=key,
                slug=raw.get("slug", ""),
                date_iso=raw.get("date_iso", ""),
                website_url=raw.get("website_url", ""),
                telegram_url=raw.get("telegram_url"),
                telegram_message_id=raw.get("telegram_message_id"),
                revision=raw.get("revision"),
            )
        return out

    def load(self, source_key: str) -> Receipt | None:
        return self.scan().get(source_key)

    def write(self, receipt: Receipt) -> str:
        """Write a receipt JSON to the clone and return its clone-relative path."""
        rel = self.rel_path(receipt.source_key)
        path = os.path.join(self.clone_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "source_key": receipt.source_key,
            "slug": receipt.slug,
            "date_iso": receipt.date_iso,
            "website_url": receipt.website_url,
            "telegram_url": receipt.telegram_url,
            "telegram_message_id": receipt.telegram_message_id,
            "revision": receipt.revision,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return rel


def extract_zulip_links(text: str, zulip_host: str) -> list[LinkTarget]:
    """Find all private Zulip links in text, keyed by their `near/` id."""
    out: list[LinkTarget] = []
    seen: set[str] = set()
    for m in _ZULIP_LINK.finditer(text):
        url = m.group(0)
        parsed = urllib.parse.urlsplit(url)
        if parsed.netloc and parsed.netloc != zulip_host:
            continue
        near_key = _near_key(parsed.fragment) or _near_key(parsed.path)
        if near_key and url not in seen:
            seen.add(url)
            out.append(LinkTarget(near_key=near_key, url=url))
    return out


def _near_key(fragment: str) -> str | None:
    m = _NARROW_CHANNEL.search(fragment)
    if m:
        return f"zulip:{m.group(1)}:{m.group(2)}"
    m = _NARROW_STREAM.search(fragment)
    if m:
        return f"zulip:{m.group(1)}:{m.group(3)}"
    return None


def resolve_links(text: str, zulip_host: str, receipts: dict[str, Receipt],
                  for_telegram: bool = False,
                  resolve_source_key: Callable[[LinkTarget], str | None] | None = None,
                  ) -> tuple[str, list[str]]:
    """Rewrite private Zulip links to public edition URLs.

    A link's `near/` id may point at a reply, not the Source Note, so each target
    is first mapped to its topic's Source Note key via `resolve_source_key`
    (falling back to the raw near key). For Telegram, the target's `telegram_url`
    is used and a missing one counts as unresolved; for the website, the
    `website_url` is used. Returns (rewritten_text, unresolved_keys).
    """
    links = extract_zulip_links(text, zulip_host)
    unresolved: list[str] = []
    mapping: dict[str, str] = {}
    for target in links:
        key = None
        if resolve_source_key is not None:
            key = resolve_source_key(target)
        key = key or target.near_key
        rec = receipts.get(key)
        if rec is None:
            unresolved.append(key)
            continue
        if for_telegram:
            if rec.telegram_url is None:
                unresolved.append(key)
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
