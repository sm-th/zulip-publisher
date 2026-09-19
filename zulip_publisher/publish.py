"""Orchestrator that drives one Source Note through the full publication lifecycle."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Callable

from . import images, render, receipts, telegram, zulip
from .config import Config
from .git import GitRepo
from .images import ImageStore
from .models import Candidate, PreparedDocument, ProgressState, Receipt
from .prepare import PreparationClient
from .receipts import LinkTarget


WORKING_EMOJI = "hourglass_flowing_sand"   # ⏳ (work in progress)
PUBLISHED_EMOJI = "loudspeaker"            # 📢 (both editions public)
PROGRESS_MARKER = "<!-- zulip-publisher:progress -->"

STAGE_ACCEPTED = "Accepted"
STAGE_PREPARING = "Preparing"
STAGE_WEBSITE = "Publishing website"
STAGE_WAIT_WEBSITE = "Waiting for website"
STAGE_TELEGRAM = "Publishing Telegram"
STAGE_WAIT_TELEGRAM = "Waiting for Telegram"


class PublicationError(RuntimeError):
    pass


class HoldError(PublicationError):
    """Recoverable: candidate blocked waiting for another publication."""


class TelegramFailedError(PublicationError):
    """Telegram CI recorded a failed state; manual reset required."""


@dataclass(frozen=True)
class PublicationResult:
    source_key: str
    success: bool
    held: bool = False
    website_url: str | None = None
    telegram_url: str | None = None
    error: str | None = None


class Orchestrator:
    """Drive one Candidate from accepted to published.

    The caller supplies the adapter instances; the orchestrator hides HTTP,
    git, and R2 payload details behind their interfaces.
    """

    def __init__(self, cfg: Config, zulip_client: zulip.Zulip,
                 preparer: PreparationClient,
                 site_repo: GitRepo, telegram_repo: telegram.TelegramRepo,
                 image_store: ImageStore | None,
                 receipt_store: receipts.ReceiptStore):
        self.cfg = cfg
        self.zulip = zulip_client
        self.preparer = preparer
        self.site_repo = site_repo
        self.telegram_repo = telegram_repo
        self.image_store = image_store
        self.receipt_store = receipt_store

    # ------------------------------------------------------------------ #
    # Public entry
    # ------------------------------------------------------------------ #

    def process(self, candidate: Candidate,
                existing_receipts: dict[str, Receipt] | None = None) -> PublicationResult:
        recs = existing_receipts or self.receipt_store.scan()
        existing = recs.get(candidate.note.source_key)
        state = ProgressState(stage=STAGE_ACCEPTED)
        try:
            if existing is not None and existing.is_complete:
                # Publish-once: a completed Publication is never re-published, even
                # for a newer Source Revision. Just reconcile the receipt + markers.
                self._reconcile_complete(candidate, existing)
                return PublicationResult(
                    source_key=candidate.note.source_key, success=True,
                    website_url=existing.website_url, telegram_url=existing.telegram_url,
                )

            self._add_working_marker(candidate)
            state.progress_message_id = self._find_or_create_progress(candidate, state)

            if existing is not None:
                return self._resume_telegram(candidate, existing, state, recs)
            return self._full_publication(candidate, state, recs)

        except Exception as e:
            state.error = str(e)
            try:
                self._edit_progress(candidate, state)
            except Exception:
                pass
            return PublicationResult(
                source_key=candidate.note.source_key,
                success=False,
                held=isinstance(e, HoldError),
                error=str(e),
            )

    # ------------------------------------------------------------------ #
    # Flows
    # ------------------------------------------------------------------ #

    def _full_publication(self, candidate: Candidate, state: ProgressState,
                          recs: dict[str, Receipt]) -> PublicationResult:
        state.stage = STAGE_PREPARING
        self._edit_progress(candidate, state)
        prepared = self._prepare(candidate)
        state.prepared = prepared

        state.stage = STAGE_WEBSITE
        self._edit_progress(candidate, state)
        website_url, slug, date_iso = self._publish_website(candidate, prepared, recs)
        state.website_url = website_url

        state.stage = STAGE_WAIT_WEBSITE
        self._edit_progress(candidate, state)
        self._wait_site_ready(website_url)

        state.stage = STAGE_TELEGRAM
        self._edit_progress(candidate, state)
        self._write_telegram_artifact(candidate, slug, date_iso, state, recs)

        state.stage = STAGE_WAIT_TELEGRAM
        self._edit_progress(candidate, state)
        telegram_url, mid = self._await_telegram(slug, date_iso)

        self._commit_receipt(candidate, slug, date_iso, website_url, telegram_url, mid)
        self._finalize(candidate, state, website_url, telegram_url)
        return PublicationResult(
            source_key=candidate.note.source_key, success=True,
            website_url=website_url, telegram_url=telegram_url,
        )

    def _resume_telegram(self, candidate: Candidate, existing: Receipt,
                         state: ProgressState, recs: dict[str, Receipt]) -> PublicationResult:
        # Website already published; re-prepare (idempotent/cached) and finish Telegram.
        state.stage = STAGE_PREPARING
        self._edit_progress(candidate, state)
        prepared = self._prepare(candidate)
        state.prepared = prepared

        slug, date_iso, website_url = existing.slug, existing.date_iso, existing.website_url
        state.website_url = website_url

        state.stage = STAGE_TELEGRAM
        self._edit_progress(candidate, state)
        self._write_telegram_artifact(candidate, slug, date_iso, state, recs)

        state.stage = STAGE_WAIT_TELEGRAM
        self._edit_progress(candidate, state)
        telegram_url, mid = self._await_telegram(slug, date_iso)

        self._commit_receipt(candidate, slug, date_iso, website_url, telegram_url, mid)
        self._finalize(candidate, state, website_url, telegram_url)
        return PublicationResult(
            source_key=candidate.note.source_key, success=True,
            website_url=website_url, telegram_url=telegram_url,
        )

    # ------------------------------------------------------------------ #
    # Stages
    # ------------------------------------------------------------------ #

    def _prepare(self, candidate: Candidate) -> PreparedDocument:
        title = candidate.note.title
        body = candidate.note.body
        link, body = render.split_link(body)
        prepared = self.preparer.prepare(
            body=body, title=title,
            policy=self.cfg.prepare_policy, fmt=self.cfg.prepare_format,
        )
        object.__setattr__(prepared, "_link", link)
        return prepared

    def _publish_website(self, candidate: Candidate, prepared: PreparedDocument,
                         recs: dict[str, Receipt]) -> tuple[str, str, str]:
        link = getattr(prepared, "_link", None)
        title = prepared.title or candidate.note.title

        body, unresolved = receipts.resolve_links(
            prepared.body,
            zulip_host=urllib.parse.urlsplit(self.cfg.zulip_url).netloc,
            receipts=recs, for_telegram=False,
            resolve_source_key=self._resolver(recs),
        )
        if unresolved:
            raise HoldError(f"waiting for {', '.join(unresolved)}")

        images.assert_no_private_attachments(body)

        date_iso = render.to_local(
            candidate.note.timestamp.isoformat().replace("+00:00", "Z"),
            candidate.author_timezone or self.cfg.author_timezone_fallback,
        )
        year, mon, day = render.date_parts(date_iso)
        slug = render.slugify(title)

        if self.image_store and not self.cfg.dry_run:
            body, _ = self.image_store.rewrite_for_website(body, slug)

        body = render.strip_title_heading(body, title)
        post = render.build_post(
            {"title": title}, body, date_iso,
            post_type="link" if link else "post", link=link,
        )
        rel_path = render.rel_dir(year, mon, day, slug)
        post_rel = f"{self.cfg.site_posts_subdir}/{rel_path}/index.md"
        website_url = render.post_url(self.cfg.site_url, year, mon, day, slug)

        if not self.cfg.dry_run:
            self.site_repo.write_file(post_rel, post)
            receipt_rel = self.receipt_store.write(Receipt(
                source_key=candidate.note.source_key, slug=slug, date_iso=date_iso,
                website_url=website_url, telegram_url=None, telegram_message_id=None,
                revision=candidate.note.revision,
            ))
            self.site_repo.commit_push(f"publish: {slug}", paths=[post_rel, receipt_rel])

        return website_url, slug, date_iso

    def _wait_site_ready(self, website_url: str) -> None:
        if self.cfg.dry_run:
            return
        if not telegram.check_url_ready(
                website_url,
                timeout=self.cfg.site_ready_timeout,
                interval=self.cfg.site_ready_interval):
            raise PublicationError(f"site URL did not become ready: {website_url}")

    def _write_telegram_artifact(self, candidate: Candidate, slug: str, date_iso: str,
                                 state: ProgressState, recs: dict[str, Receipt]) -> None:
        prepared = state.prepared
        if prepared is None:
            raise PublicationError("telegram stage reached without prepared document")
        link = getattr(prepared, "_link", None)
        title = prepared.title or candidate.note.title

        body, unresolved = receipts.resolve_links(
            prepared.body,
            zulip_host=urllib.parse.urlsplit(self.cfg.zulip_url).netloc,
            receipts=recs, for_telegram=True,
            resolve_source_key=self._resolver(recs),
        )
        if unresolved:
            raise HoldError(f"waiting for telegram links: {', '.join(unresolved)}")

        images.assert_no_private_attachments(body)

        image_url: str | None = None
        if self.image_store and not self.cfg.dry_run:
            body, refs = self.image_store.rewrite_for_telegram(body, slug)
            if refs:
                image_url = refs[0].url

        body = render.strip_title_heading(body, title)
        artifact = render.build_telegram_post(
            title=title, body=body,
            site_url=state.website_url or "", link=link, image_url=image_url,
        )

        if not self.cfg.dry_run:
            self.telegram_repo.write_artifact(slug, date_iso, artifact)
            # No `[skip ci]`: this push is exactly what triggers the Telegram
            # publish workflow the poller then waits on.
            self.telegram_repo.commit_push_artifact(slug, date_iso, message=f"telegram: {slug}")

    def _await_telegram(self, slug: str, date_iso: str) -> tuple[str, int | None]:
        if self.cfg.dry_run:
            return "https://t.me/dry-run", None
        tg_state = self.telegram_repo.poll_state(
            slug, date_iso,
            timeout=self.cfg.telegram_state_timeout,
            interval=self.cfg.telegram_state_interval,
        )
        if tg_state.is_failed:
            raise TelegramFailedError(tg_state.error or "Telegram publish failed")
        if not tg_state.url:
            raise PublicationError("Telegram state has no url")
        return tg_state.url, tg_state.message_id

    def _commit_receipt(self, candidate: Candidate, slug: str, date_iso: str,
                        website_url: str, telegram_url: str, mid: int | None) -> None:
        if self.cfg.dry_run:
            return
        receipt_rel = self.receipt_store.write(Receipt(
            source_key=candidate.note.source_key, slug=slug, date_iso=date_iso,
            website_url=website_url, telegram_url=telegram_url,
            telegram_message_id=mid, revision=candidate.note.revision,
        ))
        self.site_repo.commit_push(f"receipt: {slug}", paths=[receipt_rel])

    def _finalize(self, candidate: Candidate, state: ProgressState,
                  website_url: str, telegram_url: str) -> None:
        final = f"{PROGRESS_MARKER}\n- Website: {website_url}\n- Telegram: {telegram_url}"
        if self.cfg.dry_run:
            return
        if state.progress_message_id:
            self.zulip.edit_message(state.progress_message_id, final)
        self.zulip.remove_reaction(candidate.note.message_id, WORKING_EMOJI)
        self.zulip.add_reaction(candidate.note.message_id, PUBLISHED_EMOJI)

    def _reconcile_complete(self, candidate: Candidate, receipt: Receipt) -> None:
        """A completed receipt exists but the note lacks its final state.

        Recreate/refresh the Progress Receipt with both links and set the markers,
        so a crash between "published" and "marked" self-heals without republishing.
        """
        if self.cfg.dry_run:
            return
        state = ProgressState()
        state.progress_message_id = self._find_or_create_progress(candidate, state)
        self._finalize(candidate, state, receipt.website_url, receipt.telegram_url)

    # ------------------------------------------------------------------ #
    # Link resolver
    # ------------------------------------------------------------------ #

    def _resolver(self, recs: dict[str, Receipt]) -> Callable[[LinkTarget], str | None]:
        def resolve(target: LinkTarget) -> str | None:
            # A near-id that is already a known Source Note key needs no lookup.
            if target.near_key in recs:
                return target.near_key
            try:
                near_id = int(target.near_key.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                return None
            try:
                return self.zulip.source_key_for_message(near_id)
            except Exception:
                return None
        return resolve

    # ------------------------------------------------------------------ #
    # Progress message helpers
    # ------------------------------------------------------------------ #

    def _find_or_create_progress(self, candidate: Candidate,
                                 state: ProgressState) -> int:
        if self.cfg.dry_run:
            return 0
        msgs = self.zulip.get_messages(
            candidate.note.stream_id, candidate.note.topic_name,
            anchor="oldest", num_after=200,
        )
        bot_email = self.cfg.zulip_api_username
        for msg in msgs:
            if msg.get("sender_email") != bot_email:
                continue
            if PROGRESS_MARKER in (msg.get("content") or ""):
                return msg["id"]
        content = f"{PROGRESS_MARKER}\n{state.to_text()}"
        return self.zulip.send_message(
            candidate.note.stream_id, candidate.note.topic_name, content,
        )

    def _edit_progress(self, candidate: Candidate, state: ProgressState) -> None:
        if self.cfg.dry_run or not state.progress_message_id:
            return
        content = f"{PROGRESS_MARKER}\n{state.to_text()}"
        self.zulip.edit_message(state.progress_message_id, content)

    def _add_working_marker(self, candidate: Candidate) -> None:
        if not self.cfg.dry_run and WORKING_EMOJI not in candidate.note.reactions:
            self.zulip.add_reaction(candidate.note.message_id, WORKING_EMOJI)
