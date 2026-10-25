"""HTTP transport shared by the sync and async clients.

Centralizes: base-URL handling, the ``Authorization: Bearer sk_...`` header
(the SDK is the permanent programmatic/api-key path), JSON (de)serialization,
typed error mapping, bounded retries on transient failures, and Server-Sent
Events parsing for streamed chat completions.

Retries (contract: packages/sdk-contract/retry.json): reads (GET/PUT/DELETE)
retry on 408/425/429/5xx, read timeouts and transport errors. Writes
(POST/PATCH — a chat call bills and audits) retry ONLY when the server cannot
have processed them: a 429, or a failure to connect. Never on a 5xx or a
dropped connection mid-request — the control plane has no idempotency key, so
a replay would mean a second billed, traced call. A stream follows the same
rule before it opens. A write the caller marks ``idempotent`` (sending it
twice has the same effect as once — trace batches, stored by step id)
retries like a read.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Callable, Dict, Iterator, Mapping, Optional
from urllib.parse import quote, urljoin

import httpx

from ._exceptions import (
    ForgebenchConnectionError,
    StreamError,
    raise_for_response,
)
from .version import __version__

DEFAULT_BASE_URL = "https://api.forgebench.ai"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# httpx's ``timeout`` only bounds each individual socket read -- a connection
# that stays open and technically alive, but trickles (or stops sending)
# data slower than that per-read window never trips it and the stream never
# ends. This is a SEPARATE, cumulative wall-clock deadline over the WHOLE
# streamed call, so a stalled-but-alive stream still gets closed out.
DEFAULT_STREAM_TIMEOUT = 300.0


def _normalize_base_url(base_url: str) -> str:
    # Ensure a single trailing slash so urljoin treats it as a directory.
    return base_url.rstrip("/") + "/"


def _build_headers(api_key: Optional[str], extra: Optional[Mapping[str, str]]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": f"forgebench-sdk-python/{__version__}",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra:
        headers.update(extra)
    return headers


def _full_url(base_url: str, path: str) -> str:
    return urljoin(base_url, path.lstrip("/"))


_UNSAFE_METHODS = frozenset({"POST", "PATCH"})
# Raised before a single request byte reached the server.
_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


def _retry_status(method: str, status: int, idempotent: bool = False) -> bool:
    if method.upper() in _UNSAFE_METHODS and not idempotent:
        return status == 429
    return status in _RETRY_STATUSES


def _retry_exc(method: str, exc: BaseException, idempotent: bool = False) -> bool:
    return idempotent or method.upper() not in _UNSAFE_METHODS or isinstance(exc, _CONNECT_ERRORS)


# Backoff and Retry-After: the same numbers in all three SDKs (retry.json).
_RETRY_AFTER_MAX_S = 30.0


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff, 0.5s doubling to 8s, with equal jitter (half to full)."""
    base = min(0.5 * (2 ** attempt), 8.0)
    return base / 2 + random.random() * base / 2


def seg(value: Any) -> str:
    """One percent-encoded URL path segment; refuses ids that would change the route (``""``, ``.``, ``..``)."""
    text = str(value)
    if text in ("", ".", ".."):
        raise ValueError("id must be a non-empty path segment (not '.' or '..')")
    return quote(text, safe="")


# retry.json: plain non-negative decimal seconds only ("1_0", "0x10", "5d", "1e1" are not).
_RETRY_AFTER_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    ra = (response.headers.get("retry-after") or "").strip()
    if _RETRY_AFTER_DECIMAL.fullmatch(ra):  # anything else (an HTTP-date, ...): fall back to backoff
        return min(float(ra), _RETRY_AFTER_MAX_S)
    return _backoff_seconds(attempt)


class SyncTransport:
    def __init__(
        self,
        *,
        api_key: Optional[str],
        base_url: str,
        timeout: float,
        max_retries: int,
        default_headers: Optional[Mapping[str, str]],
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._base_url = _normalize_base_url(base_url)
        self._api_key = api_key
        self._max_retries = max(0, int(max_retries))
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout,
            headers=_build_headers(api_key, default_headers),
        )
        if http_client is not None:
            # Caller-supplied client: merge our auth/UA without clobbering theirs.
            for k, v in _build_headers(api_key, default_headers).items():
                self._client.headers.setdefault(k, v)

    @property
    def base_url(self) -> str:
        return self._base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        idempotent: bool = False,
    ) -> Any:
        response = self._send(
            method, path, json_body=json_body, params=params, headers=headers, stream=False,
            idempotent=idempotent,
        )
        try:
            raise_for_response(response)
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        finally:
            response.close()

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        data: Optional[Mapping[str, Any]] = None,
        files: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> httpx.Response:
        """Like :meth:`request` but returns the (fully read) response, for
        multipart uploads and binary bodies (audio) whose headers matter."""
        response = self._send(
            method,
            path,
            json_body=json_body,
            params=params,
            headers=headers,
            stream=False,
            data=data,
            files=files,
        )
        try:
            raise_for_response(response)
            response.read()
            return response
        finally:
            response.close()

    def stream_sse(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        max_stream_seconds: float = DEFAULT_STREAM_TIMEOUT,
        require_terminal: bool = False,
        on_response: Optional[Callable[[httpx.Response], None]] = None,
    ) -> Iterator[Dict[str, Any]]:
        response = self._send(
            method, path, json_body=json_body, params=params, headers=headers, stream=True
        )
        try:
            if not response.is_success:
                response.read()
                raise_for_response(response)
            if on_response is not None:
                on_response(response)
            reader = _SseReader(require_terminal)
            deadline = time.monotonic() + max_stream_seconds
            for raw_line in response.iter_lines():
                if time.monotonic() >= deadline:
                    raise ForgebenchConnectionError(
                        f"stream exceeded overall timeout of {max_stream_seconds}s "
                        "(connection stayed open but stopped making progress)"
                    )
                for data in reader.feed(raw_line):
                    yield data
            reader.finish()
        finally:
            response.close()

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any],
        params: Optional[Mapping[str, Any]],
        stream: bool,
        headers: Optional[Mapping[str, str]] = None,
        data: Optional[Mapping[str, Any]] = None,
        files: Optional[Any] = None,
        idempotent: bool = False,
    ) -> httpx.Response:
        url = _full_url(self._base_url, path)
        last_exc: Optional[BaseException] = None
        for attempt in range(self._max_retries + 1):
            try:
                req = self._client.build_request(
                    method,
                    url,
                    json=json_body,
                    data=data,
                    files=files,
                    params=_clean_params(params),
                    headers=_clean_headers(headers),
                )
                response = self._client.send(req, stream=stream)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self._max_retries and _retry_exc(method, exc, idempotent):
                    time.sleep(_backoff_seconds(attempt))
                    continue
                raise ForgebenchConnectionError(
                    f"failed to reach control plane at {url}: {exc}", cause=exc
                ) from exc

            if _retry_status(method, response.status_code, idempotent) and attempt < self._max_retries:
                delay = _retry_after_seconds(response, attempt)
                response.close()
                time.sleep(delay)
                continue
            return response
        # Unreachable, but keeps type-checkers happy.
        raise ForgebenchConnectionError(  # pragma: no cover
            f"failed to reach control plane at {url}", cause=last_exc
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SyncTransport":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncTransport:
    def __init__(
        self,
        *,
        api_key: Optional[str],
        base_url: str,
        timeout: float,
        max_retries: int,
        default_headers: Optional[Mapping[str, str]],
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._base_url = _normalize_base_url(base_url)
        self._api_key = api_key
        self._max_retries = max(0, int(max_retries))
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout,
            headers=_build_headers(api_key, default_headers),
        )
        if http_client is not None:
            for k, v in _build_headers(api_key, default_headers).items():
                self._client.headers.setdefault(k, v)

    @property
    def base_url(self) -> str:
        return self._base_url

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        idempotent: bool = False,
    ) -> Any:
        response = await self._send(
            method, path, json_body=json_body, params=params, headers=headers, stream=False,
            idempotent=idempotent,
        )
        try:
            raise_for_response(response)
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        finally:
            await response.aclose()

    async def request_raw(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        data: Optional[Mapping[str, Any]] = None,
        files: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> httpx.Response:
        """Async twin of :meth:`SyncTransport.request_raw`."""
        response = await self._send(
            method,
            path,
            json_body=json_body,
            params=params,
            headers=headers,
            stream=False,
            data=data,
            files=files,
        )
        try:
            raise_for_response(response)
            await response.aread()
            return response
        finally:
            await response.aclose()

    async def stream_sse(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        max_stream_seconds: float = DEFAULT_STREAM_TIMEOUT,
        require_terminal: bool = False,
        on_response: Optional[Callable[[httpx.Response], None]] = None,
    ):
        response = await self._send(
            method, path, json_body=json_body, params=params, headers=headers, stream=True
        )
        try:
            if not response.is_success:
                await response.aread()
                raise_for_response(response)
            if on_response is not None:
                on_response(response)
            reader = _SseReader(require_terminal)
            deadline = time.monotonic() + max_stream_seconds
            line_iter = response.aiter_lines()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ForgebenchConnectionError(
                        f"stream exceeded overall timeout of {max_stream_seconds}s "
                        "(connection stayed open but stopped making progress)"
                    )
                try:
                    line = await asyncio.wait_for(line_iter.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    raise ForgebenchConnectionError(
                        f"stream exceeded overall timeout of {max_stream_seconds}s "
                        "(connection stayed open but stopped making progress)"
                    ) from exc
                for data in reader.feed(line):
                    yield data
            reader.finish()
        finally:
            await response.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Any],
        params: Optional[Mapping[str, Any]],
        stream: bool,
        headers: Optional[Mapping[str, str]] = None,
        data: Optional[Mapping[str, Any]] = None,
        files: Optional[Any] = None,
        idempotent: bool = False,
    ) -> httpx.Response:
        url = _full_url(self._base_url, path)
        last_exc: Optional[BaseException] = None
        for attempt in range(self._max_retries + 1):
            try:
                req = self._client.build_request(
                    method,
                    url,
                    json=json_body,
                    data=data,
                    files=files,
                    params=_clean_params(params),
                    headers=_clean_headers(headers),
                )
                response = await self._client.send(req, stream=stream)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self._max_retries and _retry_exc(method, exc, idempotent):
                    await asyncio.sleep(_backoff_seconds(attempt))
                    continue
                raise ForgebenchConnectionError(
                    f"failed to reach control plane at {url}: {exc}", cause=exc
                ) from exc

            if _retry_status(method, response.status_code, idempotent) and attempt < self._max_retries:
                delay = _retry_after_seconds(response, attempt)
                await response.aclose()
                await asyncio.sleep(delay)
                continue
            return response
        raise ForgebenchConnectionError(  # pragma: no cover
            f"failed to reach control plane at {url}", cause=last_exc
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncTransport":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


def _clean_params(params: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _clean_headers(headers: Optional[Mapping[str, str]]) -> Optional[Dict[str, str]]:
    """Per-request headers with unset values dropped, so a caller can pass
    ``{"X-Parent-Call": parent}`` unconditionally and an absent parent simply
    sends no header (the server treats a missing header as "this call is a
    root"). Merged by httpx over the client-level defaults (auth, user-agent)."""
    if not headers:
        return None
    return {k: v for k, v in headers.items() if v is not None}


# ---------------------------------------------------------------------------
# Server-Sent Events parsing.
#
# The chat chokepoint passes the gateway's SSE stream through unchanged: each
# event is a ``data: <json>`` line, terminated by ``data: [DONE]``. We parse
# only the ``data:`` field (OpenAI/LiteLLM stream format).
# ---------------------------------------------------------------------------
class _SseReader:
    """Line-by-line SSE reader. Yields each ``data:`` JSON object; raises
    :class:`StreamError` on an ``event: error`` frame. With ``require_terminal``
    (agent runs) :meth:`finish` raises if the stream ended without
    ``agent.run.completed`` or ``[DONE]``."""

    def __init__(self, require_terminal: bool = False) -> None:
        self._event: Optional[str] = None
        self._require_terminal = require_terminal
        self._terminal = False

    def feed(self, raw: Optional[str]):
        line = raw.rstrip("\r\n") if raw is not None else ""
        if not line:
            self._event = None
            return
        if line.startswith(":"):
            return
        if line.startswith("event:"):
            self._event = line[len("event:"):].strip()
            return
        if not line.startswith("data:"):
            return
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            self._terminal = True
            return
        if not payload:
            return
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            if self._event == "error":
                raise StreamError("stream error: " + payload[:200])
            return
        if self._event == "error":
            err = obj.get("error") if isinstance(obj, dict) else None
            err = err if isinstance(err, dict) else {}
            raise StreamError(
                str(err.get("message") or "stream error"), code=err.get("code")
            )
        if isinstance(obj, dict):
            if obj.get("type") == "agent.run.completed":
                self._terminal = True
            yield obj

    def finish(self) -> None:
        if self._require_terminal and not self._terminal:
            raise StreamError(
                "stream ended without agent.run.completed or [DONE] (truncated)",
                code="stream_truncated",
            )


def _parse_sse_data_line(raw: str):
    """Stateless single-line parse (no ``event:`` tracking); kept for callers
    that replay a transport loop line by line."""
    return _SseReader().feed(raw)
