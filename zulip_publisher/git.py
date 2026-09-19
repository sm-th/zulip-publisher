"""Generic git clone adapter used by both website and Telegram repositories.

Reuses a proven persistent-clone git adapter approach.

Authentication is explicit and isolated: when a `push_token` is supplied, clone
and push go over HTTPS using that token via an in-memory `http.extraheader` (the
token is never written to the on-disk remote config); otherwise, when an
`ssh_key` is set, SSH is used; otherwise git falls back to the ambient config.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import dataclass


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitConfig:
    repo_url: str
    clone_dir: str
    branch: str
    ssh_key: str
    git_user_name: str
    git_user_email: str
    git_sign: bool
    git_signing_key: str
    allowed_signers: str
    push_token: str = ""


def https_url(url: str) -> str:
    """Normalise a GitHub remote to a credential-free HTTPS URL.

    `git@github.com:owner/repo(.git)` and `https://[creds@]github.com/owner/repo`
    both become `https://github.com/owner/repo.git`.
    """
    m = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}.git"
    m = re.match(r"^https://(?:[^@/]+@)?(.+?)(?:\.git)?$", url)
    if m:
        return f"https://{m.group(1)}.git"
    return url


class GitRepo:
    def __init__(self, cfg: GitConfig):
        self.cfg = cfg

    def _env(self) -> dict:
        env = dict(os.environ)
        if self.cfg.push_token:
            # Inject the bearer via env-config so it never lands in argv or the
            # persisted remote URL. GitHub accepts Basic x-access-token:<PAT>.
            token = base64.b64encode(
                f"x-access-token:{self.cfg.push_token}".encode("utf-8")).decode("ascii")
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraheader"
            env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {token}"
            env["GIT_TERMINAL_PROMPT"] = "0"
        elif self.cfg.ssh_key:
            env["GIT_SSH_COMMAND"] = f"ssh -i {self.cfg.ssh_key} -o IdentitiesOnly=yes"
        return env

    def _remote_url(self) -> str:
        return https_url(self.cfg.repo_url) if self.cfg.push_token else self.cfg.repo_url

    def _git(self, *args: str, check: bool = True,
             cwd: str | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.cfg.clone_dir,
            env=self._env(),
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} -> {proc.stderr.strip()}")
        return proc

    def _configure(self) -> None:
        # Identity, signing, and SSH are environment concerns: only override the
        # ambient git config when the operator has explicitly supplied a value.
        if self.cfg.git_user_name:
            self._git("config", "user.name", self.cfg.git_user_name)
        if self.cfg.git_user_email:
            self._git("config", "user.email", self.cfg.git_user_email)
        if self.cfg.git_sign and self.cfg.git_signing_key:
            self._git("config", "commit.gpgsign", "true")
            self._git("config", "gpg.format", "ssh")
            self._git("config", "user.signingkey", self.cfg.git_signing_key)
            if self.cfg.allowed_signers:
                self._git("config", "gpg.ssh.allowedsignersfile", self.cfg.allowed_signers)

    def ensure_clone(self) -> None:
        if not os.path.isdir(os.path.join(self.cfg.clone_dir, ".git")):
            os.makedirs(self.cfg.clone_dir, exist_ok=True)
            parent = os.path.dirname(self.cfg.clone_dir)
            self._git("clone", self._remote_url(), self.cfg.clone_dir, cwd=parent)
        self._configure()

    def sync(self) -> None:
        self._git("fetch", "origin")
        self._git("reset", "--hard", f"origin/{self.cfg.branch}")

    def read_file(self, rel_path: str) -> str | None:
        path = os.path.join(self.cfg.clone_dir, rel_path)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write_file(self, rel_path: str, content: str) -> None:
        path = os.path.join(self.cfg.clone_dir, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def commit_push(self, message: str, paths: list[str] | None = None) -> bool:
        if paths:
            self._git("add", *paths)
        else:
            self._git("add", "-A")
        proc = self._git("commit", "-m", message, check=False)
        if proc.returncode != 0:
            if "nothing to commit" in (proc.stdout + proc.stderr):
                return True
            raise GitError(proc.stderr.strip())
        self._git("push", "origin", self.cfg.branch)
        return True

    def abs_path(self, rel_path: str) -> str:
        return os.path.join(self.cfg.clone_dir, rel_path)
