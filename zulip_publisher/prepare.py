"""HTTP client for the shared preparation interface."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .models import PreparedDocument


class PrepareError(RuntimeError):
    pass


class PreparationClient:
    """Client for POST /v1/prepare."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def prepare(self, body: str, title: str | None = None,
                policy: str = "faithful-en-v1",
                fmt: str = "markdown") -> PreparedDocument:
        url = f"{self.base_url}/v1/prepare"
        payload = {"policy": policy, "format": fmt, "body": body}
        if title is not None:
            payload["title"] = title
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "zulip-publisher/0.1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise PrepareError(f"prepare -> HTTP {e.code}: {detail}") from None
        except json.JSONDecodeError as e:
            raise PrepareError(f"prepare -> invalid JSON: {e}") from None

        if "error" in doc:
            raise PrepareError(f"prepare -> {doc['error']}")

        return PreparedDocument(
            fingerprint=doc.get("fingerprint") or doc.get("id") or "",
            policy=doc.get("policy", policy),
            title=doc.get("title"),
            body=doc.get("prepared_body") or doc.get("body") or doc.get("prepared", ""),
            cache_hit=doc.get("cache_hit"),
        )
