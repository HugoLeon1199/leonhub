"""Polite HTTP client shared by every collector.

Design constraints that drove this module:
- Sources are third-party and unpaid. Sequential-by-default with a per-host
  delay keeps us a good citizen and keeps us un-blocked.
- Actions runners have US IPs; some sources geo-block them. Collectors that hit
  such a source must declare it (see BROWSER_ONLY_HOSTS) so we fail loudly at
  import time in CI rather than silently collecting nothing.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)

# Hosts that geo-block US datacenter IPs (HTTP 451/403). GitHub Actions runners
# are US-based, so these may only be called from the browser or a local run.
BROWSER_ONLY_HOSTS = {
    "api.binance.com",
    "fapi.binance.com",
}

# Identifies us honestly and stays contactable. The trailing library token that
# requests would otherwise append is deliberately absent: some sources (Vietcap
# among them) reject any agent containing "python-requests" with a bare 400,
# which reads as a broken endpoint rather than a refused client.
DEFAULT_UA = "LeonHub/0.1 (+https://leonquant.com; research aggregator; contact via site)"


class SourceBlocked(RuntimeError):
    """Raised when a source refuses us — geo-block, rate limit, or ban."""


class SourceUnavailable(RuntimeError):
    """Raised when a source fails after exhausting retries."""


@dataclass
class HttpClient:
    """Session wrapper with per-host throttling and bounded retries.

    Args:
        delay: minimum seconds between requests to the same host.
        timeout: per-request timeout in seconds.
        retries: how many times to retry a retryable failure.
        allow_browser_only: permit calls to BROWSER_ONLY_HOSTS (local runs only).
    """

    delay: float = 1.0
    timeout: float = 30.0
    retries: int = 3
    allow_browser_only: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": DEFAULT_UA, **self.headers})
        self._last_call: dict[str, float] = {}
        if os.environ.get("GITHUB_ACTIONS") == "true":
            self.allow_browser_only = False

    def _throttle(self, host: str) -> None:
        last = self._last_call.get(host)
        if last is not None:
            wait = self.delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_call[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        host = requests.utils.urlparse(url).hostname or ""
        if host in BROWSER_ONLY_HOSTS and not self.allow_browser_only:
            raise SourceBlocked(
                f"{host} geo-blocks US datacenter IPs; it must be fetched from the "
                "browser or a local run, not from CI. Pass allow_browser_only=True "
                "if you are certain this run is local."
            )

        kwargs.setdefault("timeout", self.timeout)
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            self._throttle(host)
            try:
                resp = self._session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code in (403, 451):
                    raise SourceBlocked(
                        f"{host} returned {resp.status_code} for {url} — likely a "
                        "geo-block or ban, not a transient error."
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = SourceUnavailable(
                        f"{host} returned {resp.status_code}"
                    )
                else:
                    resp.raise_for_status()
                    return resp

            if attempt < self.retries:
                backoff = (2**attempt) + random.uniform(0, 0.5)
                log.warning(
                    "%s attempt %d/%d failed (%s); retrying in %.1fs",
                    url, attempt + 1, self.retries + 1, last_error, backoff,
                )
                time.sleep(backoff)

        raise SourceUnavailable(f"{url} failed after {self.retries + 1} attempts") from last_error

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs).json()

    def post_json(self, url: str, json: Any, **kwargs: Any) -> Any:
        return self.request("POST", url, json=json, **kwargs).json()

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.request("GET", url, **kwargs).text
