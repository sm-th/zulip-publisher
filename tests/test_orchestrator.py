"""Tests for the publication orchestrator."""

from __future__ import annotations

import pytest

from zulip_publisher import render
from zulip_publisher.models import Candidate, PreparedDocument, Receipt, SourceNote
from zulip_publisher.publish import HoldError, Orchestrator, PUBLISHED_EMOJI, WORKING_EMOJI
from zulip_publisher.telegram import TelegramState
from zulip_publisher.zulip import to_source_note


def _candidate(topic="A Topic", body="Body.", mid=42, reactions=None):
    note = SourceNote(
        stream_id=1, topic_name=topic, message_id=mid,
        author_email="a@example.com", author_full_name="A",
        title=topic, body=body,
        timestamp=__import__("datetime").datetime(2026, 9, 19, 12, 0, 0, tzinfo=__import__("datetime").timezone.utc),
        reactions=reactions or [],
    )
    return Candidate(note=note, source_url="", author_timezone="Europe/Berlin")


def _prep(title=None, body="Prepared."):
    return PreparedDocument(fingerprint="fp", policy="faithful-en-v1", title=title, body=body)


def test_full_publication_writes_site_and_telegram(orchestrator, fake_zulip,
                                                    fake_preparer, site_git,
                                                    telegram_repo, receipt_store):
    fake_preparer.response = _prep(title="Prepared Title", body="Prepared body.")
    telegram_repo.set_state("prepared-title", "2026-09-19T14:00:00+02:00",
                            TelegramState(state="published", message_id=100, url="https://t.me/channel/100"))

    cand = _candidate()
    result = orchestrator.process(cand)

    assert result.success
    assert result.website_url == "https://site.example.com/2026/Sep/19/prepared-title/"
    assert result.telegram_url == "https://t.me/channel/100"

    # Site post written and contains source key.
    assert any("src/2026/Sep/19/prepared-title/index.md" in paths for _, paths in site_git.commits)
    post = site_git.files["src/2026/Sep/19/prepared-title/index.md"]
    assert "type: post" in post
    assert "source:" not in post
    assert "telegram_url:" not in post
    # Receipt is a durable JSON committed alongside the post, not post frontmatter.
    assert any(".zulip-publisher/receipts/zulip_1_42.json" in paths for _, paths in site_git.commits)

    # Telegram artifact written.
    assert any("posts/2026-09-19-prepared-title.md" in paths for _, paths in telegram_repo.git.commits)

    # Markers updated.
    assert PUBLISHED_EMOJI in fake_zulip.reactions.get(42, [])
    assert WORKING_EMOJI not in fake_zulip.reactions.get(42, [])


def test_single_progress_message_recovery(orchestrator, fake_zulip, fake_preparer, telegram_repo):
    fake_preparer.response = _prep(title="T", body="B")
    telegram_repo.set_state("t", "2026-09-19T14:00:00+02:00",
                            TelegramState(state="published", message_id=1, url="https://t.me/channel/1"))

    # Pre-existing progress message from a crashed run.
    fake_zulip.messages.append({
        "id": 500,
        "sender_email": "bot@example.com",
        "topic": "A Topic",
        "content": "<!-- zulip-publisher:progress -->\npreparing",
    })

    orchestrator.process(_candidate())

    # Existing message edited, no new message sent.
    assert len(fake_zulip.sent) == 0
    assert any(mid == 500 for mid, _ in fake_zulip.edits)


def test_partial_site_resume_skips_reprepare_website(orchestrator, fake_zulip,
                                                     fake_preparer, site_git,
                                                     telegram_repo, receipt_store):
    # Simulate an existing website post (its receipt is set below).
    post = render.build_post({"title": "Existing"}, "Body.", "2026-09-19T14:00:00+02:00")
    site_git.files["src/2026/Sep/19/existing/index.md"] = post
    receipt_store.receipts["zulip:1:42"] = Receipt(
        source_key="zulip:1:42", slug="existing", date_iso="2026-09-19T14:00:00+02:00",
        website_url="https://site.example.com/2026/Sep/19/existing/",
        telegram_url=None,
    )

    fake_preparer.response = _prep(title="Maybe Different", body="Telegram body.")
    telegram_repo.set_state("existing", "2026-09-19T14:00:00+02:00",
                            TelegramState(state="published", message_id=2, url="https://t.me/channel/2"))

    result = orchestrator.process(_candidate())

    assert result.success
    assert result.website_url == "https://site.example.com/2026/Sep/19/existing/"
    assert result.telegram_url == "https://t.me/channel/2"
    # Website slug should remain "existing".
    assert "src/2026/Sep/19/existing/index.md" in site_git.files


def test_dry_run_performs_no_writes(cfg, orchestrator, fake_zulip, fake_preparer,
                                    site_git, telegram_repo):
    cfg = __import__("dataclasses").replace(cfg, dry_run=True)
    orchestrator.cfg = cfg
    fake_preparer.response = _prep(title="T", body="B")

    result = orchestrator.process(_candidate())

    assert result.success
    assert not site_git.commits
    assert not telegram_repo.git.commits
    assert not fake_zulip.reactions


def test_private_link_blocks_until_target_published(orchestrator, fake_preparer):
    fake_preparer.response = _prep(title="Linker", body="See https://zulip.example.com/#narrow/channel/1-Other/near/99")
    cand = _candidate()
    result = orchestrator.process(cand)

    assert not result.success
    assert result.held
    assert "waiting for" in result.error.lower()


def test_private_link_rewritten_per_destination(orchestrator, fake_preparer, site_git,
                                                telegram_repo, receipt_store):
    # Target is fully published; its receipt carries both public URLs.
    receipt_store.receipts["zulip:1:99"] = Receipt(
        source_key="zulip:1:99", slug="target", date_iso="2026-09-18T14:00:00+02:00",
        website_url="https://site.example.com/2026/Sep/18/target/",
        telegram_url="https://t.me/channel/99",
    )

    fake_preparer.response = _prep(
        title="Linker",
        body="See https://zulip.example.com/#narrow/channel/1-Other/near/99",
    )
    telegram_repo.set_state("linker", "2026-09-19T14:00:00+02:00",
                            TelegramState(state="published", message_id=3, url="https://t.me/channel/3"))

    result = orchestrator.process(_candidate())

    assert result.success
    site_post = site_git.files["src/2026/Sep/19/linker/index.md"]
    # Website uses website URL.
    assert "https://site.example.com/2026/Sep/18/target/" in site_post
    # Telegram artifact uses t.me URL.
    tg_artifact = telegram_repo.git.files["posts/2026-09-19-linker.md"]
    assert "https://t.me/channel/99" in tg_artifact
    # Neither contains private Zulip URL.
    assert "zulip.example.com" not in site_post
    assert "zulip.example.com" not in tg_artifact


def test_telegram_failed_state_stops_and_reports(cfg, orchestrator, fake_preparer,
                                                 telegram_repo, fake_zulip):
    fake_preparer.response = _prep(title="Fail", body="B")
    telegram_repo.set_state("fail", "2026-09-19T14:00:00+02:00",
                            TelegramState(state="failed", error="Telegram blocked"))

    result = orchestrator.process(_candidate())

    assert not result.success
    assert "Telegram" in result.error or "failed" in result.error.lower()
    # Working marker stays so a later manual reset can retry.
    assert WORKING_EMOJI in fake_zulip.reactions.get(42, [])


def test_one_bad_candidate_does_not_abort_later(monkeypatch, cfg, fake_zulip,
                                                fake_preparer, site_git,
                                                telegram_repo, receipt_store):
    calls = []

    def fake_process(self, candidate, existing_receipts=None):
        calls.append(candidate.note.topic_name)
        if candidate.note.topic_name == "Bad":
            from zulip_publisher.publish import PublicationResult
            return PublicationResult(source_key=candidate.note.source_key, success=False, error="boom")
        return PublicationResult(source_key=candidate.note.source_key, success=True,
                                 website_url="https://site.example.com/x/",
                                 telegram_url="https://t.me/channel/x")

    monkeypatch.setattr(Orchestrator, "process", fake_process)
    lp = __import__("zulip_publisher.loop", fromlist=["Loop"]).Loop(cfg)
    lp.zulip_client = fake_zulip
    lp.site_repo = site_git
    lp.telegram_repo = telegram_repo
    lp._stream_id_cache = 1
    fake_zulip.topics = [{"name": "Bad", "max_id": 10}, {"name": "Good", "max_id": 20}]
    fake_zulip.messages = [
        {"id": 1, "sender_email": "a@example.com", "sender_full_name": "A",
         "topic": "Bad", "content": "bad", "timestamp": 0, "is_first": True, "reactions": []},
        {"id": 2, "sender_email": "a@example.com", "sender_full_name": "A",
         "topic": "Good", "content": "good", "timestamp": 0, "is_first": True, "reactions": []},
    ]
    lp.run_once()
    assert calls == ["Bad", "Good"]


def test_link_post_preserved(orchestrator, fake_preparer, site_git, telegram_repo):
    fake_preparer.response = _prep(title="Link Post", body="My commentary.")
    telegram_repo.set_state("link-post", "2026-09-19T14:00:00+02:00",
                            TelegramState(state="published", message_id=4, url="https://t.me/channel/4"))

    cand = _candidate(body="https://example.com/outbound\n\nMy commentary.")
    result = orchestrator.process(cand)

    assert result.success
    site_post = site_git.files["src/2026/Sep/19/link-post/index.md"]
    assert 'link: https://example.com/outbound' in site_post
    tg_artifact = telegram_repo.git.files["posts/2026-09-19-link-post.md"]
    assert 'link: https://example.com/outbound' in tg_artifact
