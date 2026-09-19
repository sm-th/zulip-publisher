"""Telegram artifact writer and sibling-state reader.

Artifact schema follows https://github.com/andysmith-ai/telegram main. The
Telegram repo's CI publishes on push under `posts/**`, then writes a sibling
`<slug>.state.json` and pushes it back. The publisher therefore MUST NOT mark its
artifact commit `[skip ci]` (that would suppress the very workflow it waits on),
and it MUST re-sync the clone before each state read so the CI-pushed state
becomes visible locally.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .git import GitRepo


class TelegramError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramState:
    state: str
    error: str | None = None
    message_id: int | None = None
    url: str | None = None

    @property
    def is_success(self) -> bool:
        return self.state == "published" and self.url is not None

    @property
    def is_failed(self) -> bool:
        return self.state == "failed"


@dataclass(frozen=True)
class TelegramRepoConfig:
    clone_dir: str
    posts_subdir: str
    site_url: str
    username: str


class TelegramRepo:
    """Writes Telegram artifacts and polls their sibling state files."""

    def __init__(self, cfg: TelegramRepoConfig, git: GitRepo):
        self.cfg = cfg
        self.git = git

    def artifact_path(self, slug: str, date_iso: str) -> str:
        date_prefix = date_iso[:10]
        return os.path.join(self.cfg.posts_subdir, f"{date_prefix}-{slug}.md")

    def state_path(self, slug: str, date_iso: str) -> str:
        date_prefix = date_iso[:10]
        return os.path.join(self.cfg.posts_subdir, f"{date_prefix}-{slug}.state.json")

    def tme_url(self, message_id: int) -> str:
        return f"https://t.me/{self.cfg.username}/{message_id}"

    def write_artifact(self, slug: str, date_iso: str, content: str) -> None:
        rel = self.artifact_path(slug, date_iso)
        self.git.write_file(rel, content)

    def commit_push_artifact(self, slug: str, date_iso: str, message: str) -> None:
        rel = self.artifact_path(slug, date_iso)
        self.git.commit_push(message, paths=[rel])

    def read_state(self, slug: str, date_iso: str) -> TelegramState | None:
        path = os.path.join(self.cfg.clone_dir, self.state_path(slug, date_iso))
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if "status" in raw:
            return TelegramState(
                state=raw["status"],
                error=raw.get("error"),
                message_id=raw.get("message_id"),
                url=raw.get("url"),
            )
        if "message_id" in raw and "url" in raw:
            return TelegramState(state="published", message_id=raw["message_id"], url=raw["url"])
        return TelegramState(state="unknown")

    def poll_state(self, slug: str, date_iso: str,
                   timeout: int, interval: int) -> TelegramState:
        """Poll sibling state until success, failure, or timeout.

        The sibling state file is produced by the Telegram repo's CI and pushed to
        origin, so each iteration re-syncs the local clone before reading.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.git.sync()
            except Exception:
                pass
            state = self.read_state(slug, date_iso)
            if state is not None and (state.is_success or state.is_failed):
                return state
            if time.monotonic() >= deadline:
                raise TelegramError(f"timed out waiting for Telegram state for {slug}")
            time.sleep(interval)


def check_url_ready(url: str, timeout: int = 30, interval: int = 5) -> bool:
    """Return True once a GET returns 200 within timeout seconds.

    The site auto-deploys after the post is pushed; a GET (not HEAD -- some static
    hosts do not serve HEAD for fresh pages) is retried on the not-ready statuses
    until the deadline, honouring the configured interval.
    """
    deadline = time.monotonic() + timeout
    interval = max(1, interval)
    while True:
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "zulip-publisher/0.1.0")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            if e.code not in (404, 425, 502, 503, 504):
                return False
        except Exception:
            pass
        if time.monotonic() + interval >= deadline:
            return False
        time.sleep(interval)


def parse_artifact(text: str) -> tuple[dict, str]:
    """Split `--- frontmatter --- body` from a Telegram artifact file."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text.strip()
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, m.group(2).strip()
