"""Zulip REST/event adapter."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .models import SourceNote, ZulipMessage


class ZulipError(RuntimeError):
    pass


class Zulip:
    """Minimal Zulip client for the Publisher's needs."""

    def __init__(self, url: str, api_key: str, api_username: str):
        self.url = url.rstrip("/")
        self.api_username = api_username
        self.api_key = api_key
        self._opener = self._build_opener()

    def _build_opener(self):
        password_mgr = urllib.request.HTTPPasswordMgrWithPriorAuth()
        password_mgr.add_password(None, self.url, self.api_username, self.api_key)
        handler = urllib.request.HTTPBasicAuthHandler(password_mgr)
        return urllib.request.build_opener(handler)

    def _request(self, method: str, path: str, data: dict | None = None,
                 headers: dict | None = None) -> dict:
        url = f"{self.url}{path}"
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("User-Agent", "zulip-publisher/0.1.0")
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise ZulipError(f"{method} {path} -> HTTP {e.code}: {detail}") from None

    def get_stream_id(self, stream_name: str) -> int:
        data = self._request("GET", f"/json/streams/{stream_name}")
        return data["stream_id"]

    def get_topics(self, stream_id: int) -> list[dict]:
        """Return topics newest-first; caller reverses for oldest-first."""
        out: list[dict] = []
        anchor = None
        while True:
            params: dict[str, Any] = {}
            if anchor is not None:
                params["anchor"] = anchor
            data = self._request("GET", f"/json/users/me/{stream_id}/topics?{urllib.parse.urlencode(params)}")
            topics = data.get("topics") or []
            if not topics:
                break
            out.extend(topics)
            anchor = topics[-1].get("max_id")
            if not data.get("more_topics", False):
                break
        return list(reversed(out))

    def get_messages(self, stream_id: int, topic_name: str, anchor: str = "oldest",
                     num_before: int = 0, num_after: int = 100) -> list[ZulipMessage]:
        narrow = [
            {"operator": "stream", "operand": stream_id},
            {"operator": "topic", "operand": topic_name},
        ]
        data = self._request("GET", "/json/messages?" + urllib.parse.urlencode({
            "anchor": anchor,
            "num_before": num_before,
            "num_after": num_after,
            "narrow": json.dumps(narrow),
            "apply_markdown": "false",
        }))
        return data.get("messages") or []

    def first_message(self, stream_id: int, topic_name: str) -> ZulipMessage | None:
        msgs = self.get_messages(stream_id, topic_name, anchor="oldest", num_after=1)
        return msgs[0] if msgs else None

    def source_url(self, stream_id: int, topic_name: str, message_id: int) -> str:
        return f"{self.url}/#narrow/channel/{stream_id}-{urllib.parse.quote(topic_name, safe='')}/near/{message_id}"

    def user_timezone(self, user_id: int | None = None, email: str | None = None) -> str | None:
        """Return the user's IANA timezone, or None."""
        if user_id is not None:
            data = self._request("GET", f"/json/users/{user_id}")
        elif email is not None:
            data = self._request("GET", f"/json/users/{urllib.parse.quote(email)}")
        else:
            return None
        user = data.get("user") or {}
        return user.get("timezone") or None

    def add_reaction(self, message_id: int, emoji: str) -> None:
        self._request("POST", f"/json/messages/{message_id}/reactions",
                      {"emoji_name": emoji})

    def remove_reaction(self, message_id: int, emoji: str) -> None:
        self._request("DELETE", f"/json/messages/{message_id}/reactions?emoji_name={urllib.parse.quote(emoji)}")

    def send_message(self, stream_id: int, topic_name: str, content: str) -> int:
        data = self._request("POST", "/json/messages", {
            "type": "stream",
            "to": str(stream_id),
            "topic": topic_name,
            "content": content,
        })
        return data["id"]

    def edit_message(self, message_id: int, content: str) -> None:
        self._request("PATCH", f"/json/messages/{message_id}", {"content": content})

    def delete_message(self, message_id: int) -> None:
        self._request("DELETE", f"/json/messages/{message_id}")

    def message_reactions(self, message_id: int) -> list[str]:
        data = self._request("GET", f"/json/messages/{message_id}")
        msg = data.get("message") or {}
        return [r.get("emoji_name") for r in msg.get("reactions", []) if r.get("emoji_name")]

    def register_event_queue(self, event_types: list[str] | None = None,
                             narrow: list[list[str]] | None = None) -> dict:
        payload = {}
        if event_types:
            payload["event_types"] = json.dumps(event_types)
        if narrow:
            payload["narrow"] = json.dumps(narrow)
        return self._request("POST", "/json/register", payload)

    def get_events(self, queue_id: str, last_event_id: int) -> tuple[list[dict], int]:
        data = self._request("GET", "/json/events?" + urllib.parse.urlencode({
            "queue_id": queue_id,
            "last_event_id": last_event_id,
            "dont_block": "false",
            "timeout": "60",
        }))
        events = data.get("events") or []
        new_last = data.get("last_event_id", last_event_id)
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
    return topic_name.startswith("✓ ") or "✓" in topic_name[:3]
