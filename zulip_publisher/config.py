"""Configuration.

Same split as the sibling tools:
  - Secrets come from the ENVIRONMENT only.
  - Structure is declared in a TOML file, each key overridable by an env var.
"""

import os
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Zulip source
    zulip_url: str
    zulip_api_key: str
    zulip_api_username: str
    zulip_stream: str           # stream name to publish from (e.g. "blog")
    zulip_general_topic: str    # topic name to skip, case-insensitive (default "general")
    publisher_bot_name: str     # bot display name used to identify own messages
    # Preparation interface
    prepare_url: str
    prepare_token: str
    prepare_policy: str
    prepare_format: str         # "text" or "markdown"
    # Website repo (git)
    site_repo_url: str
    site_clone_dir: str
    site_branch: str
    site_posts_subdir: str      # where post folders live under the clone
    site_url: str
    # Telegram repo (git)
    telegram_repo_url: str
    telegram_clone_dir: str
    telegram_branch: str
    telegram_posts_subdir: str  # usually "posts"
    telegram_username: str      # channel username for t.me links
    # Image store (R2)
    r2_endpoint: str
    r2_bucket: str
    r2_access_key_id: str
    r2_secret_access_key: str
    image_media_host: str
    image_prefix: str
    # Git identity + auth
    ssh_key: str
    git_user_name: str
    git_user_email: str
    git_sign: bool
    git_signing_key: str
    allowed_signers: str
    # Behaviour
    author_timezone_fallback: str
    poll_interval: int
    site_ready_timeout: int      # seconds to wait for site URL 200
    site_ready_interval: int     # seconds between site URL checks
    telegram_state_timeout: int  # seconds to wait for sibling state success
    telegram_state_interval: int # seconds between state polls
    dry_run: bool


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _load_toml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _req_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Missing required env var: {name}")
    return v


def _expand(path: str) -> str:
    return os.path.expanduser(path) if path else ""


def load() -> Config:
    doc = _load_toml(os.environ.get("PUBLISHER_CONFIG", "zulip_publisher.toml"))

    zulip_url = _req_env("ZULIP_URL").rstrip("/")
    zulip_key = _req_env("ZULIP_API_KEY")
    zulip_user = os.environ.get("ZULIP_API_USERNAME", "zulip-publisher@example.com")

    prepare_url = (os.environ.get("PREPARE_URL") or "").rstrip("/")
    prepare_token = os.environ.get("PREPARE_TOKEN", "")

    r2_endpoint = (os.environ.get("PUBLISHER_R2_ENDPOINT")
                   or doc.get("r2_endpoint", "")).rstrip("/")

    return Config(
        zulip_url=zulip_url,
        zulip_api_key=zulip_key,
        zulip_api_username=zulip_user,
        zulip_stream=os.environ.get("PUBLISHER_ZULIP_STREAM", doc.get("zulip_stream", "blog")),
        zulip_general_topic=os.environ.get("PUBLISHER_GENERAL_TOPIC", doc.get("zulip_general_topic", "general")),
        publisher_bot_name=os.environ.get("PUBLISHER_BOT_NAME", doc.get("publisher_bot_name", "Publisher")),
        prepare_url=prepare_url,
        prepare_token=prepare_token,
        prepare_policy=os.environ.get("PUBLISHER_PREPARE_POLICY", doc.get("prepare_policy", "faithful-en-v1")),
        prepare_format=os.environ.get("PUBLISHER_PREPARE_FORMAT", doc.get("prepare_format", "markdown")),
        site_repo_url=os.environ.get("PUBLISHER_SITE_REPO_URL", doc.get("site_repo_url", "git@github.com:you/your-site.git")),
        site_clone_dir=_expand(os.environ.get("PUBLISHER_SITE_CLONE_DIR", doc.get("site_clone_dir", "~/site"))),
        site_branch=os.environ.get("PUBLISHER_SITE_BRANCH", doc.get("site_branch", "main")),
        site_posts_subdir=os.environ.get("PUBLISHER_SITE_POSTS_SUBDIR", doc.get("site_posts_subdir", "src")),
        site_url=(os.environ.get("PUBLISHER_SITE_URL", doc.get("site_url", "https://example.com"))).rstrip("/"),
        telegram_repo_url=os.environ.get("PUBLISHER_TELEGRAM_REPO_URL", doc.get("telegram_repo_url", "git@github.com:you/telegram.git")),
        telegram_clone_dir=_expand(os.environ.get("PUBLISHER_TELEGRAM_CLONE_DIR", doc.get("telegram_clone_dir", "~/telegram"))),
        telegram_branch=os.environ.get("PUBLISHER_TELEGRAM_BRANCH", doc.get("telegram_branch", "main")),
        telegram_posts_subdir=os.environ.get("PUBLISHER_TELEGRAM_POSTS_SUBDIR", doc.get("telegram_posts_subdir", "posts")),
        telegram_username=os.environ.get("PUBLISHER_TELEGRAM_USERNAME", doc.get("telegram_username", "your_channel")).lstrip("@"),
        r2_endpoint=r2_endpoint,
        r2_bucket=os.environ.get("PUBLISHER_R2_BUCKET", doc.get("r2_bucket", "")),
        r2_access_key_id=os.environ.get("R2_ACCESS_KEY_ID", ""),
        r2_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", ""),
        image_media_host=(os.environ.get("PUBLISHER_IMAGE_MEDIA_HOST", doc.get("image_media_host", ""))).rstrip("/"),
        image_prefix=os.environ.get("PUBLISHER_IMAGE_PREFIX", doc.get("image_prefix", "img")),
        ssh_key=_expand(os.environ.get("PUBLISHER_SSH_KEY", doc.get("ssh_key", "~/.ssh/id_ed25519"))),
        git_user_name=os.environ.get("PUBLISHER_GIT_USER_NAME", doc.get("git_user_name", "Publisher")),
        git_user_email=os.environ.get("PUBLISHER_GIT_USER_EMAIL", doc.get("git_user_email", "publisher@example.com")),
        git_sign=bool(doc.get("git_sign", True)),
        git_signing_key=_expand(os.environ.get("PUBLISHER_GIT_SIGNING_KEY", doc.get("git_signing_key", "~/.ssh/id_ed25519.pub"))),
        allowed_signers=_expand(os.environ.get("PUBLISHER_ALLOWED_SIGNERS", doc.get("allowed_signers", "~/.ssh/allowed_signers"))),
        author_timezone_fallback=os.environ.get("PUBLISHER_AUTHOR_TZ_FALLBACK", doc.get("author_timezone_fallback", "America/Los_Angeles")),
        poll_interval=int(os.environ.get("PUBLISHER_POLL_INTERVAL", doc.get("poll_interval", 60))),
        site_ready_timeout=int(os.environ.get("PUBLISHER_SITE_READY_TIMEOUT", doc.get("site_ready_timeout", 300))),
        site_ready_interval=int(os.environ.get("PUBLISHER_SITE_READY_INTERVAL", doc.get("site_ready_interval", 5))),
        telegram_state_timeout=int(os.environ.get("PUBLISHER_TG_STATE_TIMEOUT", doc.get("telegram_state_timeout", 300))),
        telegram_state_interval=int(os.environ.get("PUBLISHER_TG_STATE_INTERVAL", doc.get("telegram_state_interval", 5))),
        dry_run=_flag("PUBLISHER_DRY_RUN"),
    )
