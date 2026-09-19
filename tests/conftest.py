"""Fixtures and fakes for behavioral tests."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pytest

from zulip_publisher import render
from zulip_publisher.config import Config
from zulip_publisher.git import GitConfig, GitRepo
from zulip_publisher.images import ImageStore
from zulip_publisher.models import Candidate, PreparedDocument, Receipt, SourceNote
from zulip_publisher.prepare import PreparationClient
from zulip_publisher.publish import Orchestrator
from zulip_publisher.receipts import ReceiptStore
from zulip_publisher.telegram import TelegramRepo, TelegramRepoConfig, TelegramState
from zulip_publisher.zulip import Zulip


@pytest.fixture
def cfg(tmp_path):
    return Config(
        zulip_url="https://zulip.example.com",
        zulip_api_key="key",
        zulip_api_username="bot@example.com",
        zulip_stream="blog",
        zulip_general_topic="general",
        publisher_bot_name="Publisher",
        prepare_url="https://prepare.example.com",
        prepare_token="token",
        prepare_policy="faithful-en-v1",
        prepare_format="markdown",
        site_repo_url="git@example.com:site.git",
        site_clone_dir=str(tmp_path / "site"),
        site_branch="main",
        site_posts_subdir="src",
        site_url="https://site.example.com",
        telegram_repo_url="git@example.com:tg.git",
        telegram_clone_dir=str(tmp_path / "telegram"),
        telegram_branch="main",
        telegram_posts_subdir="posts",
        telegram_username="channel",
        r2_endpoint="",
        r2_bucket="",
        r2_access_key_id="",
        r2_secret_access_key="",
        image_media_host="",
        image_prefix="img",
        ssh_key="",
        git_user_name="Test",
        git_user_email="test@example.com",
        git_sign=False,
        git_signing_key="",
        allowed_signers="",
        push_token="",
        author_timezone_fallback="America/Los_Angeles",
        poll_interval=1,
        site_ready_timeout=1,
        site_ready_interval=1,
        telegram_state_timeout=1,
        telegram_state_interval=1,
        dry_run=False,
    )


class FakeZulip(Zulip):
    def __init__(self, url: str = "https://zulip.example.com"):
        self.url = url.rstrip("/")
        self.topics: list[dict] = []
        self.messages: list[dict] = []
        self.reactions: dict[int, list[str]] = {}
        self.timezones: dict[str, str] = {}
        self.sent: list[tuple[int, str, str]] = []
        self.edits: list[tuple[int, str]] = []

    def get_stream_id(self, stream_name: str) -> int:
        return 1

    def get_topics(self, stream_id: int) -> list[dict]:
        return list(self.topics)

    def get_messages(self, stream_id: int, topic_name: str, anchor: str = "oldest",
                     num_before: int = 0, num_after: int = 100) -> list[dict]:
        return [m for m in self.messages if m.get("topic") == topic_name]

    def first_message(self, stream_id: int, topic_name: str) -> dict | None:
        for m in self.messages:
            if m.get("topic") == topic_name and m.get("is_first"):
                return m
        return None

    def add_reaction(self, message_id: int, emoji: str) -> None:
        self.reactions.setdefault(message_id, []).append(emoji)

    def remove_reaction(self, message_id: int, emoji: str) -> None:
        self.reactions[message_id] = [r for r in self.reactions.get(message_id, []) if r != emoji]

    def send_message(self, stream_id: int, topic_name: str, content: str) -> int:
        mid = 1000 + len(self.sent)
        self.sent.append((stream_id, topic_name, content))
        self.messages.append({
            "id": mid,
            "sender_email": "bot@example.com",
            "topic": topic_name,
            "content": content,
        })
        return mid

    def edit_message(self, message_id: int, content: str) -> None:
        self.edits.append((message_id, content))
        for m in self.messages:
            if m["id"] == message_id:
                m["content"] = content

    def message_reactions(self, message_id: int) -> list[str]:
        return list(self.reactions.get(message_id, []))

    def user_timezone(self, user_id: int | None = None, email: str | None = None) -> str | None:
        return self.timezones.get(email)


class FakePreparer(PreparationClient):
    def __init__(self):
        self.calls: list[tuple[str, str | None, str, str]] = []
        self.response = PreparedDocument(
            fingerprint="fp1",
            policy="faithful-en-v1",
            title=None,
            body="Prepared body.",
            cache_hit=False,
        )

    def prepare(self, body: str, title: str | None = None,
                policy: str = "faithful-en-v1", fmt: str = "markdown") -> PreparedDocument:
        self.calls.append((body, title, policy, fmt))
        return self.response


class FakeImageStore(ImageStore):
    def __init__(self):
        self.website_calls: list[tuple[str, str]] = []
        self.telegram_calls: list[tuple[str, str]] = []

    def rewrite_for_website(self, body: str, slug: str, warn=None):
        self.website_calls.append((body, slug))
        return body, []

    def rewrite_for_telegram(self, body: str, slug: str, warn=None):
        self.telegram_calls.append((body, slug))
        return body, []


class FakeGitRepo(GitRepo):
    def __init__(self, clone_dir: str):
        self.clone_dir = clone_dir
        self.files: dict[str, str] = {}
        self.commits: list[tuple[str, list[str]]] = []

    def ensure_clone(self):
        os.makedirs(self.clone_dir, exist_ok=True)

    def sync(self):
        pass

    def read_file(self, rel_path: str) -> str | None:
        return self.files.get(rel_path)

    def write_file(self, rel_path: str, content: str):
        self.files[rel_path] = content

    def commit_push(self, message: str, paths: list[str] | None = None) -> bool:
        self.commits.append((message, paths or []))
        return True

    def abs_path(self, rel_path: str) -> str:
        return os.path.join(self.clone_dir, rel_path)


class FakeTelegramRepo(TelegramRepo):
    def __init__(self, cfg: TelegramRepoConfig, git: FakeGitRepo):
        super().__init__(cfg, git)
        self.states: dict[str, TelegramState] = {}

    def read_state(self, slug: str, date_iso: str) -> TelegramState | None:
        return self.states.get(f"{slug}:{date_iso}")

    def poll_state(self, slug: str, date_iso: str, timeout: int, interval: int) -> TelegramState:
        state = self.read_state(slug, date_iso)
        if state is None:
            raise RuntimeError("no state")
        return state

    def set_state(self, slug: str, date_iso: str, state: TelegramState):
        self.states[f"{slug}:{date_iso}"] = state


class FakeReceiptStore(ReceiptStore):
    def __init__(self, clone_dir: str, posts_subdir: str, site_url: str):
        self.clone_dir = clone_dir
        self.posts_subdir = posts_subdir
        self.site_url = site_url
        self.receipts: dict[str, Receipt] = {}

    def scan(self) -> dict[str, Receipt]:
        return dict(self.receipts)


@pytest.fixture
def fake_zulip():
    return FakeZulip()


@pytest.fixture
def fake_preparer():
    return FakePreparer()


@pytest.fixture
def fake_image_store():
    return FakeImageStore()


@pytest.fixture
def site_git(cfg):
    return FakeGitRepo(cfg.site_clone_dir)


@pytest.fixture
def telegram_git(cfg):
    return FakeGitRepo(cfg.telegram_clone_dir)


@pytest.fixture
def telegram_repo(cfg, telegram_git):
    return FakeTelegramRepo(TelegramRepoConfig(
        clone_dir=cfg.telegram_clone_dir,
        posts_subdir=cfg.telegram_posts_subdir,
        site_url=cfg.site_url,
        username=cfg.telegram_username,
    ), telegram_git)


@pytest.fixture
def receipt_store(cfg):
    return FakeReceiptStore(cfg.site_clone_dir, cfg.site_posts_subdir, cfg.site_url)


@pytest.fixture
def orchestrator(cfg, fake_zulip, fake_preparer, site_git, telegram_repo,
                 fake_image_store, receipt_store):
    return Orchestrator(
        cfg, fake_zulip, fake_preparer, site_git, telegram_repo,
        fake_image_store, receipt_store,
    )


@pytest.fixture(autouse=True)
def mock_url_ready(monkeypatch):
    """Site readiness checks are network-bound; tests fake them as immediate success."""
    monkeypatch.setattr("zulip_publisher.telegram.check_url_ready",
                        lambda url, timeout=30, interval=5: True)


@pytest.fixture
def source_note():
    return SourceNote(
        stream_id=1,
        topic_name="A Test Topic",
        message_id=42,
        author_email="author@example.com",
        author_full_name="Author",
        title="A Test Topic",
        body="This is the source body.",
        timestamp=datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc),
        reactions=[],
    )


@pytest.fixture
def candidate(source_note):
    return Candidate(note=source_note, source_url="https://zulip.example.com/#narrow/channel/1-A-Test-Topic/near/42",
                     author_timezone="Europe/Berlin")
