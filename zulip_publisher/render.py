"""Turn a prepared document into a website edition.

Reuses the proven behaviour of ~/reference/11ty-publisher/eleventy_publisher/render.py.

The website post frontmatter is deliberately minimal: `title`, `type`, an
optional `link` for link posts, and the author-local `date` (an offset-bearing
ISO instant, so the offset itself carries the author's timezone). The publisher
never invents a description or tags, and it never writes bookkeeping fields
(source key, Telegram URL) into the public post -- that lives in a Receipt.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_H1 = re.compile(r"^\s*#\s+(.+?)\s*#*\s*$")
_HEADING_NORM = re.compile(r"[^a-z0-9]+")
_URL_ONLY = re.compile(r"^https?://\S+$")


class RenderError(RuntimeError):
    pass


def to_local(iso: str, tz: str | None = None) -> str:
    """Convert a UTC timestamp to the author's local time as an
    offset-bearing ISO string, e.g. `2026-08-11T20:36:05+07:00`.
    Falls back to the system local zone if `tz` is missing/invalid."""
    s = (iso or "").strip()
    if not s:
        raise RenderError("empty note date")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise RenderError(f"unparseable note date: {iso!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    target = None
    if tz and ZoneInfo is not None:
        try:
            target = ZoneInfo(tz)
        except Exception:
            target = None
    return dt.astimezone(target).isoformat(timespec="seconds")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split leading `--- ... ---` YAML-ish frontmatter from the body."""
    m = _FM.match(text)
    if not m:
        return {}, text.strip()
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = _unquote(v.strip())
    return fm, m.group(2).strip()


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _norm_heading(s: str) -> str:
    return _HEADING_NORM.sub(" ", s.lower()).strip()


def strip_title_heading(body: str, title: str) -> str:
    """Drop a leading H1 that merely repeats the frontmatter title."""
    lines = body.splitlines()
    if not lines:
        return body
    m = _H1.match(lines[0])
    if m and _norm_heading(m.group(1)) == _norm_heading(title):
        return "\n".join(lines[1:]).strip()
    return body


def split_link(body: str) -> tuple[str | None, str]:
    """Detect a link post. If the FIRST non-empty line of the note is a bare URL,
    return (url, rest). Otherwise (None, body)."""
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            return (m.group(0), "\n".join(lines[idx + 1:]).strip()) if (m := _URL_ONLY.match(stripped)) else (None, body)
    return None, body


def slugify(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "post"


def date_parts(iso: str) -> tuple[str, str, int]:
    """(YYYY, Mon, day) from an ISO-8601 timestamp."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        raise RenderError(f"unparseable date: {iso!r}")
    return str(dt.year), _MONTHS[dt.month - 1], dt.day


def rel_dir(year: str, mon: str, day: int, slug: str) -> str:
    return os.path.join(year, mon, str(day), slug)


def post_url(site_url: str, year: str, mon: str, day: int, slug: str) -> str:
    return f"{site_url}/{year}/{mon}/{day}/{slug}/"


_YAML_INDICATORS = set("!&*?|>%@`'\"#,[]{}")


def _yaml_scalar(s: str) -> str:
    """A frontmatter string value, double-quoted when a plain scalar would be
    ambiguous or multi-line."""
    if not s:
        return '""'
    if "\n" in s or any(c in s for c in _YAML_INDICATORS) or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def build_post(fm: dict, body: str, date_iso: str,
               post_type: str = "post", link: str | None = None) -> str:
    """Assemble the public website `index.md`.

    Frontmatter is intentionally minimal and public: `title`, `type`, optional
    `link`, and the author-local `date`. Never `source`, `telegram_url`,
    `description`, or `tags` -- the publisher adds no generated metadata and keeps
    its bookkeeping in a Receipt, not in the post.
    """
    if post_type == "link" and not link:
        raise RenderError("link post has no link URL")
    lines = ["---"]
    lines.append(f"title: {_yaml_scalar(fm.get('title', 'Untitled'))}")
    lines.append(f"type: {post_type}")
    if link:
        lines.append(f"link: {link}")
    lines.append(f"date: {date_iso}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def build_telegram_post(title: str, body: str, site_url: str,
                        link: str | None = None,
                        image_url: str | None = None) -> str:
    """Assemble the Telegram artifact markdown file."""
    lines = ["---"]
    lines.append(f"title: {_yaml_scalar(title)}")
    lines.append(f"site_url: {site_url}")
    if link:
        lines.append(f"link: {link}")
    if image_url:
        lines.append(f"image: {image_url}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"
