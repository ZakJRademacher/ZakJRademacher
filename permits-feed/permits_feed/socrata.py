"""A small client for the Socrata Open Data API (SODA), used by many city, county and state portals."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings

log = logging.getLogger(__name__)


class SocrataError(RuntimeError):
    pass


class SchemaError(SocrataError):
    pass


class SocrataClient:
    def __init__(self, domain: str, settings: Settings):
        self.base = f"{settings.scheme}://{domain}"
        self.settings = settings
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.user_agent
        # An app token raises the rate limit. It's optional for small pulls.
        token = os.environ.get("SOCRATA_APP_TOKEN")
        if token:
            self.session.headers["X-App-Token"] = token
        retry = Retry(
            total=settings.max_retries,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
            raise_on_status=False,  # return the last response so the error shows its status and message
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def close(self) -> None:
        self.session.close()

    def _get(self, path: str, params: dict | None = None):
        url = self.base + path
        try:
            resp = self.session.get(url, params=params, timeout=self.settings.timeout_seconds)
        except requests.RequestException as exc:
            raise SocrataError(f"{type(exc).__name__} for {url}: {exc}") from exc
        if resp.status_code != 200:
            # SODA returns a JSON body with a message for bad queries.
            detail = resp.text[:300].replace("\n", " ")
            raise SocrataError(f"HTTP {resp.status_code} for {resp.url}: {detail}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SocrataError(f"{resp.url} did not return JSON") from exc

    def columns(self, dataset: str) -> dict[str, str]:
        """fieldName -> dataTypeName, from the dataset's metadata."""
        meta = self._get(f"/api/views/{dataset}.json")
        return {c["fieldName"]: c.get("dataTypeName", "") for c in meta.get("columns", [])}

    def check_fields(self, dataset: str, needed: list[str]) -> None:
        available = self.columns(dataset)
        missing = [f for f in needed if f not in available]
        if missing:
            raise SchemaError(
                f"Dataset {dataset} has no field(s) {', '.join(missing)}. "
                f"Available fields: {', '.join(sorted(available))}"
            )

    def rows(self, dataset: str, select: list[str], where: str, order: list[str]) -> Iterator[dict]:
        """Yield every matching row, one page at a time.

        Paging with $offset is only stable with a deterministic $order, so the
        order always ends with the unique id field.
        """
        offset = 0
        page_size = self.settings.page_size
        while True:
            params = {
                "$select": ", ".join(select),
                "$where": where,
                "$order": ", ".join(order),
                "$limit": page_size,
                "$offset": offset,
            }
            page = self._get(f"/resource/{dataset}.json", params)
            log.info("page at offset %d: %d rows", offset, len(page))
            yield from page
            if len(page) < page_size:
                return
            offset += page_size
            time.sleep(self.settings.delay_seconds)
