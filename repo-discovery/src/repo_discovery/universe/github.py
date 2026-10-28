"""Small GitHub REST, Search and GraphQL client built on the standard library."""

from __future__ import annotations

import collections
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"
USER_AGENT = "repo-discovery-universe"


def default_token() -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHub:
    """Thread-safe client that waits out primary and secondary rate limits.

    Search is paced to one request per ``search_interval`` seconds because the
    search endpoint allows 30 authenticated requests per minute.
    """

    def __init__(
        self, token: str | None = None, search_interval: float = 2.1, max_retries: int = 8
    ):
        self.token = token if token is not None else default_token()
        self.search_interval = search_interval
        self.max_retries = max_retries
        self.calls: collections.Counter[str] = collections.Counter()
        self._lock = threading.Lock()
        self._search_lock = threading.Lock()
        self._last_search = 0.0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _count(self, kind: str) -> None:
        with self._lock:
            self.calls[kind] += 1

    def _request(
        self, method: str, url: str, body: Any = None, kind: str = "rest"
    ) -> tuple[Any, dict[str, str]]:
        data = json.dumps(body).encode() if body is not None else None
        last: Exception | None = None
        for attempt in range(self.max_retries):
            self._count(kind)
            req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    raw = resp.read()
                    return (json.loads(raw) if raw else None), dict(resp.headers)
            except urllib.error.HTTPError as err:
                detail = err.read()[:400].decode(errors="replace")
                wait = _retry_wait(err, attempt, detail)
                last = GitHubError(f"{method} {url}: HTTP {err.code} {detail}", err.code)
                if wait is None:
                    raise last from err
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                last = err
                wait = min(60.0, 2.0**attempt)
            time.sleep(wait)
        raise GitHubError(f"{method} {url}: giving up after {self.max_retries} attempts: {last}")

    def rest(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._request("GET", url)[0]

    def rest_paginate(
        self, path: str, params: dict[str, Any] | None = None, per_page: int = 100
    ) -> list[Any]:
        items: list[Any] = []
        page = 1
        while True:
            batch = self.rest(path, {**(params or {}), "per_page": per_page, "page": page})
            items.extend(batch)
            if len(batch) < per_page:
                return items
            page += 1

    def search_repositories(self, query: str, page: int = 1, per_page: int = 100) -> dict[str, Any]:
        with self._search_lock:
            delay = self._last_search + self.search_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last_search = time.monotonic()
        params = {"q": query, "per_page": per_page, "page": page}
        url = f"{API}/search/repositories?" + urllib.parse.urlencode(params)
        return self._request("GET", url, kind="search")[0]

    def search_count(self, query: str) -> int:
        return int(self.search_repositories(query, per_page=1)["total_count"])

    def graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], list[dict]]:
        """Return ``(data, errors)``; NOT_FOUND errors come back with partial data."""
        for attempt in range(self.max_retries):
            payload, _ = self._request(
                "POST",
                f"{API}/graphql",
                {"query": query, "variables": variables or {}},
                kind="graphql",
            )
            errors = payload.get("errors") or []
            if any(e.get("type") == "RATE_LIMITED" for e in errors):
                time.sleep(min(600.0, 30.0 * (attempt + 1)))
                continue
            if payload.get("data") is None:
                raise GitHubError(f"GraphQL failed: {errors[:3]}")
            return payload["data"], errors
        raise GitHubError("GraphQL rate limited too many times")


def _retry_wait(err: urllib.error.HTTPError, attempt: int, detail: str = "") -> float | None:
    if err.code in (500, 502, 503, 504):
        return min(120.0, 2.0 ** (attempt + 1))
    if err.code in (403, 429):
        retry_after = err.headers.get("Retry-After")
        if retry_after:
            return float(retry_after) + 1.0
        if err.headers.get("X-RateLimit-Remaining") == "0":
            reset = float(err.headers.get("X-RateLimit-Reset", time.time() + 60))
            return max(1.0, reset - time.time() + 2.0)
        if err.code == 429 or "rate limit" in detail.lower():
            return 60.0
    return None
