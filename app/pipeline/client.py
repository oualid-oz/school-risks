"""HTTP client for the source APIs, with an on-disk cache and retries.

Every response is stored under ``cache_dir`` (one folder per run date by default), so a
re-run on the same day is fast and the raw data behind each build is kept for audit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """A source API could not be read after retries."""


class Fetcher:
    def __init__(
        self,
        cache_dir: Path,
        offline: bool = False,
        retries: int = 6,  # backoff 1+2+4+8+16 s: rides out a burst of resets in a long run
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        backoff: float = 1.0,
    ) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.retries = retries
        self.backoff = backoff
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": "school-risk-tracker-pipeline"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _cache_path(self, url: str, params: dict[str, Any]) -> Path:
        key = json.dumps([url, sorted(params.items())], ensure_ascii=False, default=str)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON document. Returns None for a 404."""
        params = params or {}
        path = self._cache_path(url, params)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["body"]
        if self.offline:
            raise SourceError(f"Not in cache (offline mode): {url} {params}")

        body = self._request(url, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"url": url, "params": params, "body": body}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
        return body

    def _request(self, url: str, params: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            if attempt:
                time.sleep(self.backoff * 2 ** (attempt - 1))
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = exc
                continue
            if response.status_code in _RETRY_STATUS:
                last_error = SourceError(f"HTTP {response.status_code}")
                wait = response.headers.get("Retry-After", "")
                if wait.isdigit():  # rate limited: wait as long as the server asks (capped)
                    time.sleep(min(int(wait), 120))
                continue
            if response.status_code in (204, 404):
                return None
            if response.status_code >= 400:
                raise SourceError(
                    f"HTTP {response.status_code} for {url} {params}: {response.text[:200]}"
                )
            if not response.content.strip():
                return None  # Géorisques answers an empty 200 for unmapped points
            try:
                return response.json()
            except ValueError:  # a gateway under load sometimes answers 200 with another body
                last_error = SourceError(f"not JSON: {response.text[:80]!r}")
                continue
        raise SourceError(f"Giving up on {url} {params}: {last_error}")
