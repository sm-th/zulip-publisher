"""Domain models shared across the publisher."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SourceNote:
    """The first message of a Blog Topic."""

    stream_id: int
    topic_name: str
    message_id: int
    author_email: str
    author_full_name: str
    title: str
    body: str
    timestamp: datetime        # UTC
    avatar_url: str | None = None
    reactions: list[str] = field(default_factory=list)

    @property
    def source_key(self) -> str:
        return f"zulip:{self.stream_id}:{self.message_id}"

    @property
    def revision(self) -> str:
        """A fingerprint of the exact title and body of this Source Revision.

        Changing either the title or the body yields a different fingerprint, so a
        receipt can record which revision was published and the publisher can prove
        publish-once against a specific revision.
        """
        h = hashlib.sha256()
        h.update(self.title.encode("utf-8"))
        h.update(b"\x00")
        h.update(self.body.encode("utf-8"))
        return f"sha256:{h.hexdigest()}"


@dataclass(frozen=True)
class Candidate:
    """A Source Note selected for publication."""

    note: SourceNote
    source_url: str            # private Zulip permalink to the Source Note
    author_timezone: str | None


@dataclass(frozen=True)
class PreparedDocument:
    """Response from the preparation interface."""

    fingerprint: str
    policy: str
    title: str | None
    body: str
    cache_hit: bool | None = None


@dataclass(frozen=True)
class Receipt:
    """Durable record of a completed or partial publication.

    Receipts live in the website clone as standalone JSON, NOT in post
    frontmatter: a post's frontmatter is the public edition, a receipt is the
    publisher's private bookkeeping (which Zulip note produced which edition, at
    which revision, and whether Telegram has caught up).
    """

    source_key: str
    slug: str
    date_iso: str
    website_url: str
    telegram_url: str | None
    telegram_message_id: int | None = None
    revision: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.telegram_url is not None


@dataclass(frozen=True)
class EditionUrls:
    """Public URLs for a Publication."""

    website: str
    telegram: str | None


@dataclass
class ProgressState:
    """Mutable state carried through one publication attempt."""

    stage: str = "Accepted"
    progress_message_id: int | None = None
    prepared: PreparedDocument | None = None
    website_url: str | None = None
    telegram_url: str | None = None
    error: str | None = None

    def to_text(self) -> str:
        if self.error:
            return f"{self.stage}: {self.error}"
        return self.stage


# Type alias for raw Zulip message dicts.
ZulipMessage = dict[str, Any]
