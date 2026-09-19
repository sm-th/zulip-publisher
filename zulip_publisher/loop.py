"""Poll loop: backlog scan + Zulip event queue."""

from __future__ import annotations

import time

from . import publish, receipts, zulip
from .config import Config
from .git import GitRepo
from .images import ImageStore
from .models import Candidate
from .prepare import PreparationClient
from .telegram import TelegramRepo, TelegramRepoConfig


def log(msg: str) -> None:
    print(f"[zulip-publisher] {msg}", flush=True)


class Loop:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.zulip_client = zulip.Zulip(
            cfg.zulip_url, cfg.zulip_api_key, cfg.zulip_api_username)
        self.preparer = PreparationClient(cfg.prepare_url, cfg.prepare_token)
        self.site_repo = _site_repo(cfg)
        self.telegram_repo = _telegram_repo(cfg)
        # Private Zulip uploads need the bot's credentials to download.
        self.image_store = ImageStore.from_config(cfg, fetch=self.zulip_client.download)
        self.receipt_store = receipts.ReceiptStore(
            cfg.site_clone_dir, cfg.site_posts_subdir, cfg.site_url)
        self.orchestrator = publish.Orchestrator(
            cfg, self.zulip_client, self.preparer,
            self.site_repo, self.telegram_repo,
            self.image_store, self.receipt_store,
        )
        self._stream_id_cache: int | None = None

    def _get_stream_id(self) -> int:
        if self._stream_id_cache is None:
            self._stream_id_cache = self.zulip_client.get_stream_id(self.cfg.zulip_stream)
        return self._stream_id_cache

    def candidates(self) -> list[Candidate]:
        """Un-published Source Notes, oldest-first by Source Note timestamp.

        A note is a candidate unless it already carries the Published Marker: an
        incomplete receipt resumes, and a complete-but-unmarked receipt is included
        so the orchestrator can reconcile its markers.
        """
        stream_id = self._get_stream_id()
        topics = self.zulip_client.get_topics(stream_id)
        out: list[Candidate] = []
        for topic in topics:
            name = topic.get("name", "")
            if zulip.topic_is_general(name, self.cfg.zulip_general_topic):
                continue
            if zulip.topic_is_resolved(name):
                continue
            msg = self.zulip_client.first_message(stream_id, name)
            if msg is None:
                continue
            note = zulip.to_source_note(stream_id, name, msg)
            if publish.PUBLISHED_EMOJI in note.reactions:
                continue
            source_url = self.zulip_client.source_url(stream_id, name, note.message_id)
            tz = self.zulip_client.user_timezone(email=note.author_email)
            out.append(Candidate(note=note, source_url=source_url, author_timezone=tz))
        out.sort(key=lambda c: c.note.timestamp)
        return out

    def run_once(self) -> None:
        if not self.cfg.dry_run:
            self.site_repo.ensure_clone()
            self.site_repo.sync()
            self.telegram_repo.git.ensure_clone()
            self.telegram_repo.git.sync()
        recs = self.receipt_store.scan()
        candidates = self.candidates()
        log(f"found {len(candidates)} candidate(s)")
        for candidate in candidates:
            try:
                result = self.orchestrator.process(candidate, recs)
                if result.success:
                    log(f"{candidate.note.source_key}: published website={result.website_url} telegram={result.telegram_url}")
                elif result.held:
                    log(f"{candidate.note.source_key}: held ({result.error})")
                else:
                    log(f"{candidate.note.source_key}: error ({result.error})")
            except Exception as e:
                log(f"{candidate.note.source_key}: fatal: {e}")

    def run(self) -> None:
        self.run_once()
        if self.cfg.dry_run:
            return
        while True:
            try:
                queue = self.zulip_client.register_event_queue(
                    event_types=["message", "reaction"],
                    narrow=[["stream", self.cfg.zulip_stream]],
                )
                queue_id = queue["queue_id"]
                last_event_id = queue["last_event_id"]
                log("entering event queue")
                last_scan = time.monotonic()
                while True:
                    # The event queue long-polls: this returns the moment a
                    # message/reaction arrives. Process events immediately; re-scan
                    # the backlog only every poll_interval so held candidates retry.
                    events, last_event_id = self.zulip_client.get_events(queue_id, last_event_id)
                    if events:
                        log(f"{len(events)} event(s) received")
                    if events or (time.monotonic() - last_scan) >= self.cfg.poll_interval:
                        self.run_once()
                        last_scan = time.monotonic()
            except Exception as e:
                # An invalid/expired queue or any transient error: re-register and
                # keep going rather than exit the process.
                log(f"event queue error, re-registering: {e}")
                time.sleep(self.cfg.poll_interval)


def _site_repo(cfg: Config) -> GitRepo:
    return GitRepo(cfg_to_git_cfg(cfg, cfg.site_repo_url, cfg.site_clone_dir, cfg.site_branch))


def _telegram_repo(cfg: Config) -> TelegramRepo:
    git = GitRepo(cfg_to_git_cfg(cfg, cfg.telegram_repo_url, cfg.telegram_clone_dir, cfg.telegram_branch))
    return TelegramRepo(
        TelegramRepoConfig(
            clone_dir=cfg.telegram_clone_dir,
            posts_subdir=cfg.telegram_posts_subdir,
            site_url=cfg.site_url,
            username=cfg.telegram_username,
        ),
        git,
    )


def cfg_to_git_cfg(cfg: Config, repo_url: str, clone_dir: str, branch: str):
    from .git import GitConfig
    return GitConfig(
        repo_url=repo_url,
        clone_dir=clone_dir,
        branch=branch,
        ssh_key=cfg.ssh_key,
        git_user_name=cfg.git_user_name,
        git_user_email=cfg.git_user_email,
        git_sign=cfg.git_sign,
        git_signing_key=cfg.git_signing_key,
        allowed_signers=cfg.allowed_signers,
        push_token=cfg.push_token,
    )
