# Zulip publisher

Turn Zulip `#blog` topics into public website posts and Telegram editions.

## Product contract

The Publisher is one sequential Python instance. On startup it reconciles the
website clone against Zulip, then enters a Zulip event queue loop. It processes
the oldest un-published Source Note first, publishing each exactly once.

A Source Note is accepted when it has no 📢 reaction. The Publisher adds ⏳,
creates one editable Progress Receipt in the topic, and drives it through the
stages: Accepted → Preparing → Publishing website → Waiting for website →
Publishing Telegram → Waiting for Telegram. On completion the Progress Receipt
is replaced by the website and `t.me` links, ⏳ is removed, and 📢 is added.

The Publisher calls the shared preparation interface once per exact draft and
policy. It never adds generated descriptions or tags. Link posts preserve a bare
first-line URL. Dates reflect the author's configured timezone. Private links to
other Blog Topics are rewritten to the target's public links and block until the
target receipt exists.

## Glossary

See [CONTEXT.md](CONTEXT.md).

## Reference repositories

Treat these as read-only behaviour references. Read the listed file before
changing the corresponding local behaviour:

- `~/reference/11ty-publisher/eleventy_publisher/render.py`: site
  frontmatter, slugs, date handling, link-post detection.
- `~/reference/11ty-publisher/eleventy_publisher/images.py` and
  `imagekit.py`: content-addressed R2 image renditions.
- `~/reference/11ty-publisher/eleventy_publisher/gitrepo.py`:
  persistent clone, sync, signed commit, push.
- `~/reference/11ty-publisher/eleventy_publisher/__main__.py`: CLI
  shape (`run`, `once`, `publish`, `show`, dry-run).
- `https://github.com/andysmith-ai/telegram/main/publish/publish.py` and
  `richmessage.py`: Telegram artifact schema and Rich Post payload shape.

## Pipeline invariants

- The source is authoritative; git is transport.
- A successful site push happens before the Telegram artifact is written.
- The Telegram artifact push happens before sibling-state polling begins.
- Reconcile deterministic artifacts after crashes; never duplicate a publication.
- One bad candidate must not abort the remaining pass.
- Dry-run may read and call the preparation interface, but must not mutate
  Zulip, git, R2, or Telegram state.
- Never leak credentials, private Zulip URLs, or private upload URLs into public
  artifacts or logs.

## Module seams

Keep these seams deep. Callers of the orchestrator must not know HTTP, git, or
R2 payload details.

- `zulip`: Zulip REST/event adapter (topics, messages, reactions, user timezone).
- `prepare`: HTTP client for the shared preparation interface.
- `render`: website edition rendering (frontmatter, slug, date, link posts).
- `telegram`: Telegram artifact writer and sibling-state reader.
- `images`: R2 image uploads and URL rewriting.
- `git`: generic clone adapter used by both website and Telegram repositories.
- `publish`: orchestrator that drives one Source Note through the full lifecycle.
- `loop`: backlog scan + event queue that feeds the orchestrator.
- `config`: TOML + environment configuration; secrets from environment only.

## Operational surface

```
python -m zulip_publisher run         poll forever
python -m zulip_publisher once        one pass, then exit
python -m zulip_publisher publish <id>  process one candidate now
python -m zulip_publisher show <id>     read-only candidate preview
PUBLISHER_DRY_RUN=1                   translate and print, no writes
```

## Leading words

- **Source Note**: first message of a Blog Topic; sole source body.
- **Progress Receipt**: the Publisher bot's single editable reply in a topic.
- **Working Marker**: ⏳ on the Source Note while work is in progress.
- **Published Marker**: 📢 on the Source Note after both links are public.
- **Blog Topic**: any topic in `#blog` except trimmed/case-insensitive `general`.
- **Receipt**: durable record in the website clone binding a source key to its
  public website and Telegram URLs.
