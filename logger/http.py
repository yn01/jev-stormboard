"""HTTP 取得。条件付き GET と指数バックオフのリトライを担う。"""

from __future__ import annotations

import logging
import time

import httpx

from . import config

log = logging.getLogger(__name__)


class NotModified(Exception):
    """304 Not Modified。フィードに更新が無いことを示す。"""


class Fetcher:
    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT_SEC,
            follow_redirects=True,
        )
        # URL ごとの ETag / Last-Modified を覚えておく
        self._validators: dict[str, dict[str, str]] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ 取得

    def get_feed(self, url: str) -> bytes:
        """フィードを条件付き GET で取得する。更新が無ければ NotModified。"""
        headers = dict(self._validators.get(url, {}))
        response = self._request(url, headers)
        if response.status_code == 304:
            raise NotModified(url)

        validators: dict[str, str] = {}
        if etag := response.headers.get("etag"):
            validators["If-None-Match"] = etag
        if last_modified := response.headers.get("last-modified"):
            validators["If-Modified-Since"] = last_modified
        self._validators[url] = validators
        return response.content

    def get_document(self, url: str) -> bytes:
        """電文本体を取得する。"""
        return self._request(url, {}).content

    # ------------------------------------------------------------ 内部

    def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        """指数バックオフでリトライしながら 1 件取得する。

        404 などのクライアントエラーはリトライしても無駄なので即座に投げる。
        """
        last_error: Exception | None = None
        for attempt in range(config.RETRY_MAX):
            try:
                response = self._client.get(url, headers=headers)
                if response.status_code == 304:
                    return response
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                if response.status_code >= 500 or response.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if 400 <= status < 500 and status != 429:
                    raise
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc

            wait = config.RETRY_BASE_SEC * (2**attempt)
            log.warning("取得に失敗 (%s): %s / %.0f秒後に再試行", url, last_error, wait)
            time.sleep(wait)

        assert last_error is not None
        raise last_error
