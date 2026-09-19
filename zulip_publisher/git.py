"""Generic git clone adapter used by both website and Telegram repositories.

Reuses the behaviour of ~/reference/11ty-publisher/eleventy_publisher/gitrepo.py.
"""

from __future__ import annotations

import os
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


class GitRepo:
    def __init__(self, cfg: GitConfig):
        self.cfg = cfg

    def _ssh_command(self) -> str | None:
        if not self.cfg.ssh_key:
            return None
        return f"ssh -i {self.cfg.ssh_key} -o IdentitiesOnly=yes"

    def _env(self) -> dict:
        env = dict(os.environ)
        ssh = self._ssh_command()
        if ssh:
            env["GIT_SSH_COMMAND"] = ssh
        return env

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
            self._git("clone", self.cfg.repo_url, self.cfg.clone_dir, cwd=parent)
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
