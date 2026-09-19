"""Tests for git remote authentication selection (HTTPS token vs SSH)."""

from __future__ import annotations

import base64

from zulip_publisher.git import GitConfig, GitRepo, https_url


def _cfg(**kw) -> GitConfig:
    base = dict(
        repo_url="git@github.com:owner/repo.git", clone_dir="/tmp/x", branch="main",
        ssh_key="", git_user_name="", git_user_email="", git_sign=False,
        git_signing_key="", allowed_signers="", push_token="",
    )
    base.update(kw)
    return GitConfig(**base)


def test_https_url_normalizes_ssh_and_https():
    assert https_url("git@github.com:owner/repo.git") == "https://github.com/owner/repo.git"
    assert https_url("git@github.com:owner/repo") == "https://github.com/owner/repo.git"
    assert https_url("https://github.com/owner/repo.git") == "https://github.com/owner/repo.git"
    # An already-tokenised URL is stripped back to a credential-free form.
    assert https_url("https://x-access-token:SEKRET@github.com/owner/repo.git") \
        == "https://github.com/owner/repo.git"


def test_token_auth_uses_https_extraheader_not_ssh():
    # Even with a host SSH key present, an explicit push token wins and the SSH
    # key is never used -- the isolation guarantee.
    repo = GitRepo(_cfg(push_token="SEKRET", ssh_key="/home/k/id_ed25519"))
    env = repo._env()
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    expected = base64.b64encode(b"x-access-token:SEKRET").decode()
    assert expected in env["GIT_CONFIG_VALUE_0"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_SSH_COMMAND" not in env
    # The raw token never appears in cleartext in the header.
    assert "SEKRET" not in env["GIT_CONFIG_VALUE_0"]
    assert repo._remote_url() == "https://github.com/owner/repo.git"


def test_ssh_path_when_no_token():
    repo = GitRepo(_cfg(ssh_key="/home/k/id_ed25519"))
    env = repo._env()
    assert "GIT_SSH_COMMAND" in env
    assert "GIT_CONFIG_KEY_0" not in env
    assert repo._remote_url() == "git@github.com:owner/repo.git"


def test_ambient_when_nothing_set():
    repo = GitRepo(_cfg())
    env = repo._env()
    assert "GIT_SSH_COMMAND" not in env
    assert "GIT_CONFIG_KEY_0" not in env
