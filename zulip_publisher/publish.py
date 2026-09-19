"""Orchestrator that drives one Source Note through the full publication lifecycle."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from . import render, receipts, telegram, zulip
from .config import Config
from .git import GitRepo
from .images import ImageStore
from .models import Candidate, PreparedDocument, ProgressState, Receipt
from .prepare import PreparationClient


WORKING_EMOJI = "hourglass"
PUBLISHED_EMOJI = "loudspeaker"
PROGRESS_MARKER = "<!-- zulip-publisher:progress -->"


class PublicationError(RuntimeError):
    pass


class HoldError(PublicationError):
    """Recoverable: candidate blocked waiting for another publication."""
    pass


class TelegramFailedError(PublicationError):
    """Telegram CI recorded a failed state; manual reset required."""
    pass


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

        state = ProgressState()
        try:
            if existing is not None and existing.is_complete:
                self._ensure_markers(candidate)
                return PublicationResult(
                    source_key=candidate.note.source_key,
                    success=True,
                    website_url=existing.website_url,
                    telegram_url=existing.telegram_url,
                )

            self._add_working_marker(candidate)
            state.progress_message_id = self._find_or_create_progress(candidate, state)

            if existing is not None:
                # Resume: website exists, finish Telegram. Re-prepare because the
                # preparation interface is idempotent/cached for the same draft.
                state.stage = "preparing"
                self._edit_progress(candidate, state)
                prepared = self._prepare(candidate)
                state.prepared = prepared

                state.stage = "publishing telegram"
                state.website_url = existing.website_url
                slug = existing.slug
                date_iso = existing.date_iso
                self._edit_progress(candidate, state)
                telegram_url = self._publish_telegram(candidate, slug, date_iso, state)
                self._update_site_with_telegram(slug, date_iso, telegram_url)
                self._finalize(candidate, state, existing.website_url, telegram_url)
                return PublicationResult(
                    source_key=candidate.note.source_key,
                    success=True,
                    website_url=existing.website_url,
                    telegram_url=telegram_url,
                )

            # Full flow.
            state.stage = "preparing"
            self._edit_progress(candidate, state)
            prepared = self._prepare(candidate)
            state.prepared = prepared

            state.stage = "publishing website"
            self._edit_progress(candidate, state)
            website_url, slug, date_iso = self._publish_website(candidate, prepared)
            state.website_url = website_url

            state.stage = "publishing telegram"
            self._edit_progress(candidate, state)
            telegram_url = self._publish_telegram(candidate, slug, date_iso, state)

            self._update_site_with_telegram(slug, date_iso, telegram_url)
            self._finalize(candidate, state, website_url, telegram_url)
            return PublicationResult(
                source_key=candidate.note.source_key,
                success=True,
                website_url=website_url,
                telegram_url=telegram_url,
            )

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
    # Stages
    # ------------------------------------------------------------------ #

    def _prepare(self, candidate: Candidate) -> PreparedDocument:
        title = candidate.note.title
        body = candidate.note.body
        link, body = render.split_link(body)
        prepared = self.preparer.prepare(
            body=body,
            title=title,
            policy=self.cfg.prepare_policy,
            fmt=self.cfg.prepare_format,
        )
        # Attach link post metadata for downstream renderers.
        object.__setattr__(prepared, "_link", link)
        return prepared

    def _publish_website(self, candidate: Candidate,
                         prepared: PreparedDocument) -> tuple[str, str, str]:
        link = getattr(prepared, "_link", None)
        title = prepared.title or candidate.note.title

        receipts_map = self.receipt_store.scan()
        body, unresolved = receipts.resolve_links(
            prepared.body,
            zulip_host=urllib.parse.urlsplit(self.cfg.zulip_url).netloc,
            receipts=receipts_map,
            for_telegram=False,
        )
        if unresolved:
            raise HoldError(f"waiting for {', '.join(unresolved)}")

        if self.image_store:
            body = self.image_store.block_attachments(body)
            body, image_refs = self.image_store.rewrite_body(body, slug=render.slugify(title))
        else:
            image_refs = []

        body = render.strip_title_heading(body, title)

        date_iso = render.to_local(
            candidate.note.timestamp.isoformat().replace("+00:00", "Z"),
            candidate.author_timezone or self.cfg.author_timezone_fallback,
        )
        year, mon, day = render.date_parts(date_iso)
        slug = render.slugify(title)

        fm = {
            "title": title,
            "description": None,
            "tags": [],
        }
        post = render.build_post(
            fm, body, date_iso,
            link=link,
            source_key=candidate.note.source_key,
        )

        rel_path = render.rel_dir(year, mon, day, slug)
        full_rel = f"{self.cfg.site_posts_subdir}/{rel_path}/index.md"

        if not self.cfg.dry_run:
            self.site_repo.write_file(full_rel, post)
            self.site_repo.commit_push(
                f"publish: {slug}",
                paths=[full_rel],
            )

        website_url = render.post_url(self.cfg.site_url, year, mon, day, slug)

        if not self.cfg.dry_run:
            if not telegram.check_url_ready(website_url, timeout=self.cfg.site_ready_timeout):
                raise PublicationError(f"site URL did not become ready: {website_url}")

        return website_url, slug, date_iso

    def _publish_telegram(self, candidate: Candidate, slug: str,
                          date_iso: str, state: ProgressState) -> str:
        prepared = state.prepared
        if prepared is None:
            raise PublicationError("telegram stage reached without prepared document")
        link = getattr(prepared, "_link", None)
        title = prepared.title or candidate.note.title

        receipts_map = self.receipt_store.scan()
        body, unresolved = receipts.resolve_links(
            prepared.body,
            zulip_host=urllib.parse.urlsplit(self.cfg.zulip_url).netloc,
            receipts=receipts_map,
            for_telegram=True,
        )
        if unresolved:
            raise HoldError(f"waiting for telegram links: {', '.join(unresolved)}")

        image_url: str | None = None
        if self.image_store:
            body, image_refs = self.image_store.rewrite_body(body, slug=slug)
            if image_refs:
                image_url = image_refs[0].url
        else:
            image_refs = []

        body = render.strip_title_heading(body, title)

        telegram_body = render.build_telegram_post(
            title=title,
            body=body,
            site_url=state.website_url or "",
            link=link,
            image_url=image_url,
        )

        if not self.cfg.dry_run:
            self.telegram_repo.write_artifact(slug, date_iso, telegram_body)
            self.telegram_repo.commit_push_artifact(
                slug, date_iso,
                message=f"telegram: {slug} [skip ci]",
            )
            tg_state = self.telegram_repo.poll_state(
                slug, date_iso,
                timeout=self.cfg.telegram_state_timeout,
                interval=self.cfg.telegram_state_interval,
            )
            if tg_state.is_failed:
                raise TelegramFailedError(tg_state.error or "Telegram publish failed")
            if not tg_state.url:
                raise PublicationError("Telegram state has no url")
            return tg_state.url
        return "https://t.me/dry-run"

    def _update_site_with_telegram(self, slug: str, date_iso: str,
                                   telegram_url: str) -> None:
        if self.cfg.dry_run:
            return
        year, mon, day = render.date_parts(date_iso)
        rel_path = render.rel_dir(year, mon, day, slug)
        full_rel = f"{self.cfg.site_posts_subdir}/{rel_path}/index.md"
        text = self.site_repo.read_file(full_rel)
        if text is None:
            raise PublicationError(f"site post missing for telegram update: {full_rel}")
        fm, body = render.parse_frontmatter(text)
        fm["telegram_url"] = telegram_url
        new_text = render.build_post(
            fm, body, fm.get("date", date_iso),
            link=fm.get("link"),
            source_key=fm.get("source"),
            telegram_url=fm.get("telegram_url"),
        )
        self.site_repo.write_file(full_rel, new_text)
        self.site_repo.commit_push(
            f"telegram: {slug}",
            paths=[full_rel],
        )

    def _finalize(self, candidate: Candidate, state: ProgressState,
                  website_url: str, telegram_url: str) -> None:
        final = f"{PROGRESS_MARKER}\n- Website: {website_url}\n- Telegram: {telegram_url}"
        if not self.cfg.dry_run:
            if state.progress_message_id:
                self.zulip.edit_message(state.progress_message_id, final)
            self.zulip.remove_reaction(candidate.note.message_id, WORKING_EMOJI)
            self.zulip.add_reaction(candidate.note.message_id, PUBLISHED_EMOJI)

    # ------------------------------------------------------------------ #
    # Progress message helpers
    # ------------------------------------------------------------------ #

    def _find_or_create_progress(self, candidate: Candidate,
                                 state: ProgressState) -> int:
        if self.cfg.dry_run:
            return 0
        msgs = self.zulip.get_messages(
            candidate.note.stream_id,
            candidate.note.topic_name,
            anchor="oldest",
            num_after=200,
        )
        bot_email = self.cfg.zulip_api_username
        for msg in msgs:
            if msg.get("sender_email") != bot_email:
                continue
            if PROGRESS_MARKER in (msg.get("content") or ""):
                return msg["id"]
        content = f"{PROGRESS_MARKER}\n{state.to_text()}"
        return self.zulip.send_message(
            candidate.note.stream_id,
            candidate.note.topic_name,
            content,
        )

    def _edit_progress(self, candidate: Candidate, state: ProgressState) -> None:
        if self.cfg.dry_run or not state.progress_message_id:
            return
        content = f"{PROGRESS_MARKER}\n{state.to_text()}"
        self.zulip.edit_message(state.progress_message_id, content)

    def _add_working_marker(self, candidate: Candidate) -> None:
        if not self.cfg.dry_run and WORKING_EMOJI not in candidate.note.reactions:
            self.zulip.add_reaction(candidate.note.message_id, WORKING_EMOJI)

    def _ensure_markers(self, candidate: Candidate) -> None:
        if self.cfg.dry_run:
            return
        current = self.zulip.message_reactions(candidate.note.message_id)
        if WORKING_EMOJI in current:
            self.zulip.remove_reaction(candidate.note.message_id, WORKING_EMOJI)
        if PUBLISHED_EMOJI not in current:
            self.zulip.add_reaction(candidate.note.message_id, PUBLISHED_EMOJI)
