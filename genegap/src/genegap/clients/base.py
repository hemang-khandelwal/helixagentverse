"""Base API client providing caching, request rate-throttling, retries, and Fact creation."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

import requests

from ..config import USER_AGENT
from ..schemas import Fact
from ..store import Store

# HTTP status codes that indicate transient issues worthy of an automated retry:
# 429 = Rate limited (Too Many Requests)
# 500, 502, 503, 504 = Temporary server errors or gateway timeouts
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """Raised when an API request fails permanently after all retries or receives a 4xx error."""
    pass


class CacheMiss(ApiError):
    """Raised in offline mode when a requested endpoint has not been pre-cached."""
    pass


class ApiClient:
    """
    Base client for all external data connectors (gnomAD, ClinicalTrials.gov, ClinVar, etc.).
    Handles rate-limiting, retries, caching, and automatic Fact provenance recording.
    """

    name = "base"
    min_interval = 0.35      # Minimum seconds between requests to avoid triggering server rate limits
    max_retries = 4          # Number of retry attempts on transient network or server errors
    timeout = 30             # Socket timeout in seconds

    def __init__(
        self,
        store: Optional[Store] = None,
        session: Optional[requests.Session] = None,
        offline: bool = False,
        sleep=time.sleep,
    ) -> None:
        self.store = store or Store()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.offline = offline
        self._sleep = sleep
        self._last_call = 0.0

    @staticmethod
    def _key(method: str, url: str, params: Any, body: Any) -> str:
        """Create a deterministic SHA-1 hash of the request parameters to use as the cache key."""
        raw = json.dumps([method, url, params, body], sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()

    def _throttle(self) -> None:
        """Sleep if needed to ensure at least min_interval seconds have passed since the previous request."""
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.time()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        json_body: Any = None,
        headers: Any = None,
        use_cache: bool = True,
    ) -> tuple[Any, str]:
        """
        Send an HTTP request with caching, throttling, and exponential backoff retry.
        
        Returns:
            (parsed_json_body, final_url)
        """
        key = self._key(method, url, params, json_body)

        # 1. Return cached response if available
        if use_cache:
            hit = self.store.cache_get(key)
            if hit:
                return json.loads(hit["body"]), hit["url"]

        # 2. In offline mode, do not attempt network calls; raise CacheMiss instead
        if self.offline:
            raise CacheMiss(f"{self.name}: offline mode and no cached response for {url}")

        # 3. Attempt network request with retries on transient errors
        last_err: Any = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_err = e
            else:
                if resp.status_code in RETRY_STATUS:
                    # Retry on server overload (503) or rate limits (429)
                    last_err = f"HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    # Client errors (e.g. 404, 400) should fail immediately without retrying or caching
                    raise ApiError(f"{self.name}: HTTP {resp.status_code} for {resp.url}: {resp.text[:200]}")
                else:
                    # Success: parse JSON and save to SQLite cache for future offline runs
                    data = resp.json()
                    self.store.cache_put(key, resp.url, resp.status_code, json.dumps(data))
                    return data, resp.url

            # Exponential backoff sleep before next attempt: 1s, 2s, 4s, 8s
            if attempt < self.max_retries:
                self._sleep(2 ** attempt)

        raise ApiError(f"{self.name}: failed after {self.max_retries + 1} attempts: {last_err}")

    def fact(
        self,
        field: str,
        value: Any,
        record_id: str,
        url: str = "",
        method: str = "api",
        **kw: Any,
    ) -> Fact:
        """
        Create, persist, and return a verified Fact record.
        Automatically links the datum to this client's source name and current timestamp.
        """
        f = Fact(
            field=field,
            value=value,
            source=self.name,
            record_id=record_id,
            url=url,
            method=method,
            **kw,
        )
        self.store.put_fact(f)
        return f


