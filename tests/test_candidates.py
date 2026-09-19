"""Tests for candidate filtering and ordering."""

from __future__ import annotations

from datetime import datetime, timezone

from zulip_publisher.loop import Loop
from zulip_publisher.models import SourceNote
from zulip_publisher.publish import PUBLISHED_EMOJI


def _make_msg(topic: str, mid: int, first: bool = True, reactions: list[str] | None = None):
    return {
        "id": mid,
        "sender_email": "author@example.com",
        "sender_full_name": "Author",
        "topic": topic,
        "content": f"body {mid}",
        "timestamp": datetime(2026, 9, mid, 12, 0, 0, tzinfo=timezone.utc).timestamp(),
        "is_first": first,
        "reactions": [{"emoji_name": r} for r in (reactions or [])],
    }


def test_candidates_oldest_first_and_skip_general(cfg, fake_zulip):
    fake_zulip.topics = [
        {"name": "general", "max_id": 10},
        {"name": "Old Topic", "max_id": 20},
        {"name": "New Topic", "max_id": 30},
    ]
    fake_zulip.messages = [
        _make_msg("general", 1),
        _make_msg("Old Topic", 2),
        _make_msg("New Topic", 3),
    ]
    lp = Loop(cfg)
    lp.zulip_client = fake_zulip
    lp._stream_id_cache = 1
    cands = lp.candidates()
    assert [c.note.topic_name for c in cands] == ["Old Topic", "New Topic"]


def test_candidates_skip_published_marker(cfg, fake_zulip):
    fake_zulip.topics = [
        {"name": "Published", "max_id": 10},
        {"name": "Pending", "max_id": 20},
    ]
    fake_zulip.messages = [
        _make_msg("Published", 1, reactions=[PUBLISHED_EMOJI]),
        _make_msg("Pending", 2),
    ]
    lp = Loop(cfg)
    lp.zulip_client = fake_zulip
    lp._stream_id_cache = 1
    cands = lp.candidates()
    assert [c.note.topic_name for c in cands] == ["Pending"]


def test_candidates_skip_resolved(cfg, fake_zulip):
    fake_zulip.topics = [
        {"name": "✓ Resolved Topic", "max_id": 10},
        {"name": "Open Topic", "max_id": 20},
    ]
    fake_zulip.messages = [
        _make_msg("✓ Resolved Topic", 1),
        _make_msg("Open Topic", 2),
    ]
    lp = Loop(cfg)
    lp.zulip_client = fake_zulip
    lp._stream_id_cache = 1
    cands = lp.candidates()
    assert [c.note.topic_name for c in cands] == ["Open Topic"]
