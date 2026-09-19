"""Tests for Telegram artifact and sibling-state handling."""

from __future__ import annotations

import json
import os

from zulip_publisher.telegram import TelegramRepo, TelegramRepoConfig, TelegramState, parse_artifact
from zulip_publisher.git import GitConfig
from tests.conftest import FakeGitRepo


def make_tg_repo(tmp_path):
    clone = str(tmp_path / "tg")
    git = FakeGitRepo(clone)
    git.ensure_clone()
    cfg = TelegramRepoConfig(clone_dir=clone, posts_subdir="posts",
                             site_url="https://site.example.com",
                             username="channel")
    return TelegramRepo(cfg, git)


def test_artifact_path_and_state_path():
    cfg = TelegramRepoConfig(clone_dir="/x", posts_subdir="posts",
                             site_url="https://site.example.com", username="c")
    repo = TelegramRepo(cfg, None)
    assert repo.artifact_path("slug", "2026-09-19T14:30:00+02:00") == "posts/2026-09-19-slug.md"
    assert repo.state_path("slug", "2026-09-19T14:30:00+02:00") == "posts/2026-09-19-slug.state.json"


def test_read_state_success(tmp_path):
    repo = make_tg_repo(tmp_path)
    path = repo.state_path("slug", "2026-09-19T12:00:00+02:00")
    full = os.path.join(repo.cfg.clone_dir, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump({"message_id": 144, "url": "https://t.me/channel/144"}, f)
    state = repo.read_state("slug", "2026-09-19T12:00:00+02:00")
    assert state.is_success
    assert state.url == "https://t.me/channel/144"


def test_read_state_failed(tmp_path):
    repo = make_tg_repo(tmp_path)
    path = repo.state_path("slug", "2026-09-19T12:00:00+02:00")
    full = os.path.join(repo.cfg.clone_dir, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump({"status": "failed", "error": "blocked"}, f)
    state = repo.read_state("slug", "2026-09-19T12:00:00+02:00")
    assert state.is_failed
    assert state.error == "blocked"


def test_poll_state_returns_success(tmp_path):
    repo = make_tg_repo(tmp_path)
    path = repo.state_path("slug", "2026-09-19T12:00:00+02:00")
    full = os.path.join(repo.cfg.clone_dir, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump({"status": "published", "message_id": 7, "url": "https://t.me/channel/7"}, f)
    state = repo.poll_state("slug", "2026-09-19T12:00:00+02:00", timeout=1, interval=1)
    assert state.is_success


def test_parse_artifact():
    text = '---\ntitle: "Hello"\nsite_url: https://site.example.com/x/\n---\n\nBody.'
    fm, body = parse_artifact(text)
    assert fm["title"] == "Hello"
    assert fm["site_url"] == "https://site.example.com/x/"
    assert body == "Body."
