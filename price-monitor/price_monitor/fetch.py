"""Polite HTTP fetching: robots.txt, per-host delays, retries and a page cache."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


class DisallowedByRobots(FetchError):
    pass


class Fetcher:
    def __init__(self, settings: Settings, cache_dir: str | Path | None = None):
        self.settings = settings
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._robots_errors: dict[str, FetchError] = {}

        retry = Retry(
            total=settings.max_retries,
            backoff_factor=2,  # 2s, 4s, 8s ...
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # -- public ---------------------------------------------------------------

    def get(self, url: str) -> str:
        cached = self._read_cache(url)
        if cached is not None:
            log.debug("cache hit %s", url)
            return cached

        if self.settings.respect_robots and not self._allowed(url):
            raise DisallowedByRobots(f"robots.txt disallows {url}")

        response = self._request(url)
        if response.status_code != 200:
            raise FetchError(f"HTTP {response.status_code} for {url}")
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            # requests defaults text/html without a charset to Latin-1
            response.encoding = response.apparent_encoding
        html = response.text
        self._write_cache(url, html)
        return html

    # -- internals ------------------------------------------------------------

    def _request(self, url: str) -> requests.Response:
        host = urlparse(url).netloc
        wait = self._last_request.get(host, 0) + self.settings.delay_seconds - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return self.session.get(url, timeout=self.settings.timeout_seconds)
        except requests.RequestException as exc:
            raise FetchError(f"{type(exc).__name__} for {url}: {exc}") from exc
        finally:
            self._last_request[host] = time.monotonic()

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots_errors:
            raise self._robots_errors[origin]
        parser = self._robots.get(origin)
        if parser is None:
            parser = robotparser.RobotFileParser()
            try:
                resp = self._request(f"{origin}/robots.txt")
            except FetchError as exc:
                # Site unreachable: skip every page on it this run without retrying.
                err = FetchError(f"could not reach {origin} (robots.txt check failed): {exc}")
                self._robots_errors[origin] = err
                raise err from exc
            else:
                if resp.status_code in (401, 403):
                    parser.disallow_all = True
                elif resp.status_code >= 400:
                    parser.allow_all = True  # no robots.txt means no restrictions
                else:
                    parser.parse(resp.text.splitlines())
            self._robots[origin] = parser
        return parser.can_fetch(self.settings.user_agent, url)

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir or self.settings.cache_hours <= 0:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".html")

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours > self.settings.cache_hours:
            return None
        return path.read_text(encoding="utf-8")

    def _write_cache(self, url: str, html: str) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
