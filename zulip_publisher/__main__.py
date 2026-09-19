"""CLI.

  python -m zulip_publisher run              poll forever
  python -m zulip_publisher once             one pass, then exit
  python -m zulip_publisher publish <id>     process one candidate now
  python -m zulip_publisher show <id>        read-only candidate preview

Set PUBLISHER_DRY_RUN=1 to prepare + print without touching Zulip, git, or R2.
"""

from __future__ import annotations

import sys

from . import config, loop, publish, receipts, telegram, zulip
from .git import GitRepo
from .images import ImageStore
from .models import Candidate
from .prepare import PreparationClient


def _cmd_show(cfg: config.Config, source_id: str) -> int:
    zc = zulip.Zulip(cfg.zulip_url, cfg.zulip_api_key, cfg.zulip_api_username)
    stream_id = zc.get_stream_id(cfg.zulip_stream)
    topics = zc.get_topics(stream_id)
    for topic in topics:
        name = topic.get("name", "")
        msg = zc.first_message(stream_id, name)
        if msg is None:
            continue
        note = zulip.to_source_note(stream_id, name, msg)
        if note.source_key == source_id or str(note.message_id) == source_id:
            print(f"source_key: {note.source_key}")
            print(f"topic: {name}")
            print(f"author: {note.author_full_name} <{note.author_email}>")
            print(f"reactions: {note.reactions}")
            print(f"body ({len(note.body)} chars):\n---\n{note.body[:800]}")
            return 0
    print(f"candidate not found: {source_id}", file=sys.stderr)
    return 1


def _cmd_publish(cfg: config.Config, source_id: str) -> int:
    lp = loop.Loop(cfg)
    candidates = [c for c in lp.candidates() if c.note.source_key == source_id or str(c.note.message_id) == source_id]
    if not candidates:
        print(f"candidate not found or already published: {source_id}", file=sys.stderr)
        return 1
    candidate = candidates[0]
    result = lp.orchestrator.process(candidate)
    print(result)
    return 0 if result.success else 1


def main() -> int:
    argv = sys.argv[1:]
    cfg = config.load()
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "run":
        loop.Loop(cfg).run()
        return 0
    if cmd == "once":
        loop.Loop(cfg).run_once()
        return 0
    if cmd == "publish":
        if len(argv) < 2:
            print("usage: publish <source-key-or-message-id>", file=sys.stderr)
            return 2
        return _cmd_publish(cfg, argv[1])
    if cmd == "show":
        if len(argv) < 2:
            print("usage: show <source-key-or-message-id>", file=sys.stderr)
            return 2
        return _cmd_show(cfg, argv[1])
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
