# Zulip publisher

## Product contract

Build an unattended **Zulip -> translated English -> Eleventy/11ty site** publisher. Zulip replaces Discourse as the authoring source; the destination remains the git-backed 11ty site. Preserve the behavior of `~/reference/11ty-publisher` unless this file calls out a Zulip-specific decision.

A selected Zulip note is published without a per-post approval step. Translation preserves the author's meaning, order, terseness, links, images, and voice. It must not add facts, framing, examples, or conclusions.

## Reference implementation

Treat `~/reference/11ty-publisher` as read-only reference code. Read the relevant file before changing the corresponding behavior:

- `eleventy_publisher/loop.py`: orchestration, failure isolation, link gate, publish/checkpoint order.
- `eleventy_publisher/generate.py` and `prompts/translate.md`: file-based translation contract.
- `eleventy_publisher/render.py`: frontmatter, link posts, authoritative dates, slugs, output paths.
- `eleventy_publisher/images.py` and `imagekit.py`: source-image discovery and idempotent R2 renditions.
- `eleventy_publisher/gitrepo.py`: persistent clone, sync, signed commit, push.
- `eleventy_publisher/config.py`: TOML structure, environment overrides, secret handling.
- `eleventy_publisher/links.py`: private-source-link resolution and hold-back behavior.
- `eleventy_publisher/__main__.py`: `run`, `once`, `publish`, `show`, and dry-run operator surface.

The reference already has a translation layer. Reuse its behavior; adapt the source boundary rather than inventing a second generation pipeline. Do not modify either reference repository from this project.

## Pipeline

Keep these stages explicit and in this order:

1. Discover configured Zulip candidates and load their authoritative source content.
2. Normalize Zulip identity, title, body, author, timestamps, source URL, and attachments at the source-adapter boundary.
3. Reject already-published candidates and reconcile interrupted publications before translation.
4. Resolve links to other private Zulip notes. Hold the candidate when any target is not public; never publish a private Zulip URL.
5. Detect link posts: a bare URL on the first non-empty body line becomes `link:` frontmatter and only the remaining commentary is translated.
6. Translate and tag in one model session.
7. Parse model output, inject authoritative metadata in code, rewrite source images, and build the final `index.md`.
8. Sync the site clone, write one post, commit, and push.
9. Persist the publication receipt/checkpoint in the authoritative state only after the push succeeds.

Keep Zulip transport, translation, rendering, image storage, and git publication as separate modules. Zulip payload details must not leak into rendering or git code.

## Translation contract

Use the reference's boring file protocol:

- Write the source title and Markdown body to a temporary input file.
- Invoke the configured model CLI non-interactively with an editable prompt file.
- Require the model to write `out/index.md` and, when enabled, `out/tags.txt`.
- Read only those files. Model stdout/stderr must never become published content.
- Model output owns translated `title`, `description`, Markdown body, and proposed tags only.
- Code owns source identity, date, post type, output path, URL, checkpoint, and publication state.
- Normalize tags deterministically after generation.
- Remove a leading body H1 only when it duplicates the generated frontmatter title.
- Retry missing output at most three times. A nonzero model exit is a failed candidate and is retried by a later poll, not hidden with fallback prose.
- Always delete temporary work directories.

Start the prompt from `~/reference/11ty-publisher/prompts/translate.md`; tune the prompt globally instead of hand-editing generated posts.

## Publication and recovery invariants

- The source is authoritative; git is transport. Do not make an ephemeral in-memory queue the only record of progress.
- Publication is at-least-once internally but exactly-once from the reader's perspective.
- A successful site push happens before the source checkpoint is marked complete.
- A crash after push but before final checkpoint must reconcile the existing publication receipt and skip retranslation. A fresh translation can change the slug and create a duplicate.
- Writing identical target content is success, not an error.
- A failed push leaves the candidate eligible for a later poll.
- One bad candidate must not abort the remaining candidates. A pass-level failure must not terminate `run`.
- Processing stays sequential unless a durable concurrency protocol is designed first.
- Dry-run may read and translate, but must not mutate Zulip, git, or R2.
- Never leak credentials, private Zulip URLs, private upload URLs, or model instructions into the public artifact or logs.

Discourse used a marker block followed by a topic tag. Zulip has no direct equivalent in the current design. Keep the same recovery properties, not the Discourse mechanism. The durable receipt must bind a stable Zulip source key to the final site path and URL.

## Site output contract

Preserve the current 11ty output unless the site repository proves otherwise:

- File: `<posts_subdir>/<YYYY>/<Mon>/<D>/<slug>/index.md`.
- URL: `<site_url>/<YYYY>/<Mon>/<D>/<slug>/`.
- Frontmatter: translated `title`, explicit `type`, optional `link`, normalized `tags`, optional `description`, then authoritative offset-bearing `date`.
- Date source: the author's last edit time, captured before publisher-authored checkpoint changes. The reference code uses `updated_at` before `created_at`; its README's creation-time statement is stale.
- Convert the timestamp to the author's IANA timezone when Zulip exposes one; otherwise use the documented fallback deliberately.
- Slugs derive from the translated title using the existing deterministic ASCII rule.
- Images use content-addressed R2 keys and idempotent uploads. External hotlinks remain unchanged. If image processing fails, preserve a working source URL rather than emit a dead URL.

## Configuration and operator surface

Follow the reference precedence: environment override -> TOML -> code default. Secrets come from the environment/SecretSpec only; structural settings belong in TOML. Never commit live config, credentials, tokens, or local clone paths.

Keep parity with these commands unless a command is proven irrelevant:

- `run`: poll forever with a configured interval.
- `once`: one complete pass.
- `publish <stable-source-id>`: process one candidate now.
- `show <stable-source-id>`: read-only candidate/checkpoint preview.
- `PUBLISHER_DRY_RUN=1`: translate and print planned output with no writes.

Log concise per-candidate status to stdout. Include the stable source key in every status/error line. Do not log source bodies or secret-bearing URLs by default.

## Zulip decisions to settle before implementation

API facts come from current official Zulip documentation or an exercised server, not guesses. Product choices come from the user. Resolve these before fixing the source adapter or checkpoint schema:

1. Candidate unit and trigger: one message, a whole topic, a stream/topic convention, or an explicit reaction marker.
2. Title rule: Zulip topic name, first line, or another explicit field.
3. Durable publication receipt: bot reaction, edited source message marker, dedicated Zulip state message, or external durable state.
4. Publish-once semantics versus propagating later edits/deletes. The reference is publish-once.
5. Candidate ordering and whether an edit-age grace period is required.
6. Author timezone source and fallback.
7. Zulip upload authentication, public image hosting, and cross-note permalink format.
8. How a blocked candidate explains unresolved private links to the author.

Do not silently choose a user-visible workflow when these options have different behavior.

## Verification

A behavioral change is complete only after exercising its changed path. At minimum, the implementation must demonstrate:

- a selected Zulip candidate becomes the expected translated `index.md` in dry-run;
- replay after a simulated push-before-checkpoint failure produces no duplicate and performs no new translation;
- a private link to an unpublished candidate blocks publication, while a published target is rewritten to its public URL;
- dry-run performs no Zulip, git, or R2 writes;
- one malformed candidate does not prevent a later valid candidate in the same pass;
- link-post, timezone/date, and image fallback behavior match the contracts above.

Prefer observable contract tests for the recovery and link-gating invariants. Use a smoke run of the real CLI for the end-to-end path; tests alone are not delivery proof.
