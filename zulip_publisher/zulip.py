"""Zulip adapter built on the official Zulip Python SDK.

Wraps `zulip.Client` so the rest of the publisher never sees HTTP, auth, or
event-queue details. Authentication, narrows, and the event queue are handled by
the SDK; only `/user_uploads` binary downloads use an authenticated `requests`
call (the SDK has no binary-download helper).
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests
import zulip

from .models import SourceNote, ZulipMessage


class ZulipError(RuntimeError):
    pass


class Zulip:
    """Minimal Zulip client for the Publisher's needs, backed by the SDK."""

    def __init__(self, url: str, api_key: str, api_username: str):
        self.url = url.rstrip("/")
        self.api_username = api_username
        self.api_key = api_key
        self._client_obj: "zulip.Client | None" = None

    @property
    def _client(self) -> "zulip.Client":
        # Lazy: constructing the SDK client performs a network call, so defer it
        # until first use (tests swap in a fake before any real call).
        if self._client_obj is None:
            self._client_obj = zulip.Client(
                email=self.api_username, api_key=self.api_key, site=self.url)
        return self._client_obj

    def _ok(self, resp: dict) -> dict:
        if resp.get("result") != "success":
            raise ZulipError(resp.get("msg") or str(resp))
        return resp

    def get_stream_id(self, stream_name: str) -> int:
        return self._ok(self._client.get_stream_id(stream_name))["stream_id"]

    def get_topics(self, stream_id: int) -> list[dict]:
        return self._ok(self._client.get_stream_topics(stream_id)).get("topics", [])

    def get_messages(self, stream_id: int, topic_name: str, anchor: str = "oldest",
                     num_before: int = 0, num_after: int = 100) -> list[ZulipMessage]:
        resp = self._ok(self._client.get_messages({
            "anchor": anchor,
            "num_before": num_before,
            "num_after": num_after,
            "narrow": [
                {"operator": "stream", "operand": stream_id},
                {"operator": "topic", "operand": topic_name},
            ],
            "apply_markdown": False,
        }))
        return resp.get("messages", [])

    def first_message(self, stream_id: int, topic_name: str) -> ZulipMessage | None:
        msgs = self.get_messages(stream_id, topic_name, anchor="oldest", num_after=1)
        return msgs[0] if msgs else None

    def get_message(self, message_id: int) -> ZulipMessage | None:
        resp = self._ok(self._client.get_messages({
            "anchor": message_id,
            "num_before": 0,
            "num_after": 0,
            "narrow": [{"operator": "id", "operand": message_id}],
            "apply_markdown": False,
        }))
        msgs = resp.get("messages", [])
        return msgs[0] if msgs else None

    def source_url(self, stream_id: int, topic_name: str, message_id: int) -> str:
        return (f"{self.url}/#narrow/channel/{stream_id}-"
                f"{urllib.parse.quote(topic_name, safe='')}/near/{message_id}")

    def source_key_for_message(self, message_id: int) -> str | None:
        """Map any message id (possibly a reply) to its topic's Source Note key."""
        msg = self.get_message(message_id)
        if not msg:
            return None
        stream_id = msg.get("stream_id")
        topic_name = msg.get("subject")
        if stream_id is None or topic_name is None:
            return None
        first = self.first_message(stream_id, topic_name)
        if not first:
            return None
        return f"zulip:{stream_id}:{first['id']}"

    def user_timezone(self, user_id: int | None = None, email: str | None = None) -> str | None:
        if email is not None:
            resp = self._client.call_endpoint(f"users/{urllib.parse.quote(email)}", method="GET")
        elif user_id is not None:
            resp = self._client.call_endpoint(f"users/{user_id}", method="GET")
        else:
            return None
        if resp.get("result") != "success":
            return None
        return (resp.get("user") or {}).get("timezone") or None

    def download(self, url: str) -> bytes:
        """Download a (possibly private) upload, authenticating same-host fetches."""
        if url.startswith("/"):
            url = f"{self.url}{url}"
        auth = None
        if urllib.parse.urlsplit(url).netloc == urllib.parse.urlsplit(self.url).netloc:
            auth = (self.api_username, self.api_key)
        resp = requests.get(url, auth=auth, timeout=60,
                            headers={"User-Agent": "zulip-publisher/0.1.0"})
        if resp.status_code != 200:
            raise ZulipError(f"download {url} -> HTTP {resp.status_code}")
        return resp.content

    def add_reaction(self, message_id: int, emoji: str) -> None:
        self._ok(self._client.add_reaction({"message_id": message_id, "emoji_name": emoji}))

    def remove_reaction(self, message_id: int, emoji: str) -> None:
        self._ok(self._client.remove_reaction({"message_id": message_id, "emoji_name": emoji}))

    def send_message(self, stream_id: int, topic_name: str, content: str) -> int:
        resp = self._ok(self._client.send_message({
            "type": "stream",
            "to": stream_id,
            "topic": topic_name,
            "content": content,
        }))
        return resp["id"]

    def edit_message(self, message_id: int, content: str) -> None:
        self._ok(self._client.update_message({"message_id": message_id, "content": content}))

    def delete_message(self, message_id: int) -> None:
        self._ok(self._client.call_endpoint(f"messages/{message_id}", method="DELETE"))

    def message_reactions(self, message_id: int) -> list[str]:
        msg = self.get_message(message_id) or {}
        return [r.get("emoji_name") for r in msg.get("reactions", []) if r.get("emoji_name")]

    def register_event_queue(self, event_types: list[str] | None = None,
                             narrow: list[list[str]] | None = None) -> dict:
        return self._ok(self._client.register(event_types=event_types, narrow=narrow))

    def get_events(self, queue_id: str, last_event_id: int) -> tuple[list[dict], int]:
        resp = self._client.get_events(
            queue_id=queue_id, last_event_id=last_event_id, dont_block=False)
        if resp.get("result") != "success":
            raise ZulipError(resp.get("msg") or str(resp))
        events = resp.get("events") or []
        new_last = last_event_id
        for e in events:
            if isinstance(e.get("id"), int):
                new_last = max(new_last, e["id"])
        return events, new_last


def to_source_note(stream_id: int, topic_name: str, msg: ZulipMessage) -> SourceNote:
    """Normalize a Zulip message into a SourceNote."""
    ts = msg.get("timestamp")
    if isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    elif isinstance(ts, str):
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    else:
        dt = datetime.now(timezone.utc)
    reactions = [r.get("emoji_name") for r in msg.get("reactions", []) if r.get("emoji_name")]
    return SourceNote(
        stream_id=stream_id,
        topic_name=topic_name,
        message_id=msg["id"],
        author_email=msg.get("sender_email", ""),
        author_full_name=msg.get("sender_full_name", ""),
        title=topic_name,
        body=msg.get("content") or "",
        timestamp=dt,
        avatar_url=msg.get("avatar_url"),
        reactions=reactions,
    )


def topic_is_general(topic_name: str, general_name: str) -> bool:
    return topic_name.strip().lower() == general_name.strip().lower()


def topic_is_resolved(topic_name: str) -> bool:
    """A resolved Zulip topic is prefixed with the check mark Zulip inserts
    (U+2714 `✔ `); older/manual conventions use U+2713 (`✓ `)."""
    stripped = topic_name.lstrip()
    return stripped.startswith("✔") or stripped.startswith("✓")


# Type alias re-export for callers that imported it from here historically.
__all__ = ["Zulip", "ZulipError", "to_source_note", "topic_is_general", "topic_is_resolved"]
