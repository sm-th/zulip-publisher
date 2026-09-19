# Publishing

This context turns authoring material from Zulip into public editions for the website and Telegram.

## Language

**Blog Topic**:
A Zulip topic in `#blog` whose name is not `general`. Its topic name is the publication title; replies are discussion, not publication content.
_Avoid_: Thread, post

**Source Note**:
The first message of a Blog Topic. It is the sole source body for a Publication.
_Avoid_: Post, article, source post

**Source Revision**:
The exact Blog Topic title and Source Note body at a point in time. Changing either creates a different Source Revision.
_Avoid_: Edit, version

**Draft**:
An optional title and required plain-text or Markdown body supplied by an authorized caller for preparation.
_Avoid_: Prompt, message payload

**Prepared Document**:
The single shared clean English Markdown rendering of one Draft under one Preparation Policy. It preserves meaning, order, terseness, links, code, images, names, and voice; it adds neither editorial content nor generated description or tags.
_Avoid_: Rewrite, adaptation, channel copy

**Preparation Client**:
An agent or application that obtains a Draft under its own access rights and requests a Prepared Document.
_Avoid_: Zulip bot

**Preparation Module**:
The shared preparer that returns one Prepared Document for the same Draft and Preparation Policy, regardless of which client asks.
_Avoid_: Per-agent cleaner

**Preparation Policy**:
The versioned fidelity, cleanup, language, and output rules under which a Prepared Document is created.
_Avoid_: Prompt

**Edition**:
A destination-specific representation derived from the Prepared Document. A Source Note has a Website Edition and a Telegram Edition.
_Avoid_: Copy, repost

**Website Edition**:
The Edition published on the Eleventy website.
_Avoid_: Site post

**Telegram Edition**:
The full-text Edition rendered as a Rich Post for Telegram, including a link to its Website Edition.
_Avoid_: Telegram post, teaser

**Rich Post**:
The structured Telegram publication format established by the legacy Telegram publisher, preserving full text, links, formatting, and media.
_Avoid_: Plain message

**Publication**:
The lifecycle that turns one Source Note into both required Editions. It is complete only when the Website Edition is live and the Telegram Edition has a public `t.me` URL.
_Avoid_: Sync, cross-post

**Progress Receipt**:
The Publisher bot's single reply in a Blog Topic, edited in place from accepted through each publication stage and finally replaced by the website and Telegram links.
_Avoid_: Status log, progress thread

**Working Marker**:
A reaction on the Source Note showing that the Publisher has accepted the Publication but has not completed it.
_Avoid_: Lock, claim

**Published Marker**:
The 📢 reaction added to the Source Note only after the Progress Receipt contains both final links.
_Avoid_: Done topic, resolved topic

**Internal Note Link**:
A private Zulip link from one Source Note to another. Each Edition replaces it with the target Edition's public link and waits while that link does not exist.
_Avoid_: Public link

**Publisher**:
The system responsible for a Publication from Zulip to the website and Telegram.
_Avoid_: Zulip bot, script
