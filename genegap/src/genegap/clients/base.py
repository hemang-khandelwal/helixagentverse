"""Task 1.3: shared HTTP behaviour: cache, throttle, retry, provenance helper."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

import requests

from ..config import USER_AGENT
from ..schemas import Fact
from ..store import Store

RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    pass


class CacheMiss(ApiError):
    """Raised in offline mode when a request has not been cached yet."""


class ApiClient:
    name = "base"
    min_interval = 0.35      # seconds between calls
    max_retries = 4
    timeout = 30

    def __init__(self, store: Optional[Store] = None, session=None, offline: bool = False, sleep=time.sleep):
        self.store = store or Store()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.offline = offline
        self._sleep = sleep
        self._last_call = 0.0

    @staticmethod
    def _key(method: str, url: str, params: Any, body: Any) -> str:
        raw = json.dumps([method, url, params, body], sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()

    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.time()

    def request(self, method: str, url: str, *, params=None, json_body=None, headers=None, use_cache: bool = True):
        """Returns (parsed_json, final_url). Cached by request; errors are never cached."""
        key = self._key(method, url, params, json_body)
        if use_cache:
            hit = self.store.cache_get(key)
            if hit:
                return json.loads(hit["body"]), hit["url"]
        if self.offline:
            raise CacheMiss(f"{self.name}: offline mode and no cached response for {url}")

        last_err: Any = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.request(method, url, params=params, json=json_body,
                                            headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = e
            else:
                if resp.status_code in RETRY_STATUS:
                    last_err = f"HTTP {resp.status_code}"
                elif resp.status_code >= 400:
                    raise ApiError(f"{self.name}: HTTP {resp.status_code} for {resp.url}: {resp.text[:200]}")
                else:
                    data = resp.json()
                    self.store.cache_put(key, resp.url, resp.status_code, json.dumps(data))
                    return data, resp.url
            if attempt < self.max_retries:
                self._sleep(2 ** attempt)      # 1s, 2s, 4s, 8s
        raise ApiError(f"{self.name}: failed after {self.max_retries + 1} attempts: {last_err}")

    def fact(self, field: str, value: Any, record_id: str, url: str = "", method: str = "api", **kw) -> Fact:
        f = Fact(field=field, value=value, source=self.name, record_id=record_id, url=url, method=method, **kw)
        self.store.put_fact(f)
        return f
