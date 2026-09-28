"""M7 — the Python SDK only bounded individual reads on a streaming
response, never the stream as a whole. A connection that stays technically
alive but keeps trickling a little data every read -- never idle long
enough to trip httpx's own per-read timeout, but never actually finishing
either -- satisfies every per-read timeout forever and the stream never
ends.

These tests build a real streaming HTTP response (via httpx.MockTransport +
a custom byte stream) that yields many small chunks, each separated by a
short delay -- individually each delay is fine, but the CUMULATIVE time to
receive them all is long. They reproduce the pre-fix behavior by replaying
the OLD stream_sse body verbatim (no overall deadline at all -- it iterates
every chunk, however long the total takes) against the SAME trickling
response, then show the FIX (the current stream_sse, given a short
max_stream_seconds) cuts the stream off partway through, well before the
full trickle would otherwise finish.

Note on sync vs async precision (see stream_sse's own comments): the async
path can interrupt an in-flight wait via asyncio.wait_for and so cuts off
close to the deadline itself. The sync path has no such mechanism (no
threads, no cancellable read) -- it can only check the deadline BETWEEN
chunks, so it necessarily runs a little past the deadline (bounded by one
extra per-chunk delay), never past the full trickle. Both tests assert the
honest, correct behavior for their path rather than an unrealistic
identical bound.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from forgebench._exceptions import ForgebenchConnectionError
from forgebench._transport import AsyncTransport, SyncTransport

BASE = "http://testserver"
_CHUNK_COUNT = 10
_PER_CHUNK_DELAY_S = 0.08  # each individual delay is small and unremarkable
_TOTAL_TRICKLE_S = _CHUNK_COUNT * _PER_CHUNK_DELAY_S  # ~0.8s cumulative
_SHORT_DEADLINE_S = 0.25  # well under the full trickle, comfortably over a few chunks


def _sse_chunk(i: int) -> bytes:
    return f'data: {{"chunk": {i}}}\n\n'.encode()


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

class _TricklingSyncStream(httpx.SyncByteStream):
    """A connection that stays alive and keeps sending -- just slowly,
    forever. Never idle long enough to trip a per-read timeout; never
    actually done either."""

    def __iter__(self):
        for i in range(_CHUNK_COUNT):
            time.sleep(_PER_CHUNK_DELAY_S)
            yield _sse_chunk(i)
        yield b"data: [DONE]\n\n"

    def close(self):
        pass


def _sync_transport() -> SyncTransport:
    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_TricklingSyncStream()
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SyncTransport(
        api_key="sk_test", base_url=BASE, timeout=10, max_retries=0,
        default_headers=None, http_client=client,
    )


def _old_stream_sse(transport: SyncTransport, method: str, path: str):
    """Verbatim replay of the pre-fix SyncTransport.stream_sse body (git
    history before this fix): no overall deadline, iterates response.iter_
    lines() to exhaustion however long that takes."""
    from forgebench._transport import _parse_sse_data_line

    response = transport._send(method, path, json_body=None, params=None, stream=True)
    try:
        for raw_line in response.iter_lines():
            for data in _parse_sse_data_line(raw_line):
                yield data
    finally:
        response.close()


def test_reproduce_old_sync_stream_sse_has_no_overall_deadline():
    """Reproduction: the OLD stream_sse body, against the trickling
    response, takes the FULL cumulative trickle time and returns every
    chunk -- proving nothing bounded the stream as a whole, only
    (implicitly, via httpx) each individual read."""
    transport = _sync_transport()
    start = time.monotonic()
    chunks = list(_old_stream_sse(transport, "POST", "/v1/chat/completions"))
    elapsed = time.monotonic() - start

    assert len(chunks) == _CHUNK_COUNT
    assert elapsed >= _TOTAL_TRICKLE_S * 0.9, (
        f"reproduction failed -- expected the old code to take roughly the "
        f"full {_TOTAL_TRICKLE_S:.2f}s trickle, took {elapsed:.3f}s"
    )


def test_sync_stream_sse_enforces_overall_deadline():
    """The FIX: the REAL, current stream_sse, given a deadline shorter than
    the full trickle, cuts the stream off partway through -- well before
    every chunk would otherwise arrive -- instead of running to completion."""
    transport = _sync_transport()
    start = time.monotonic()
    received = []
    with pytest.raises(ForgebenchConnectionError, match="overall timeout"):
        for data in transport.stream_sse(
            "POST", "/v1/chat/completions", max_stream_seconds=_SHORT_DEADLINE_S
        ):
            received.append(data)
    elapsed = time.monotonic() - start

    # Cut off partway through -- not all 10 chunks, and comfortably (not
    # merely technically) before the full trickle would have finished. The
    # sync path can only check between chunks, so it may run a little past
    # the deadline itself (by at most one per-chunk delay) -- assert
    # against that honest bound, not the deadline exactly.
    assert len(received) < _CHUNK_COUNT
    assert elapsed < _TOTAL_TRICKLE_S * 0.7, (
        f"expected the deadline to cut the stream off well before the full "
        f"{_TOTAL_TRICKLE_S:.2f}s trickle, took {elapsed:.3f}s"
    )
    assert elapsed < _SHORT_DEADLINE_S + _PER_CHUNK_DELAY_S * 2, (
        "sync enforcement should overshoot the deadline by at most about "
        "one chunk's delay, not run substantially longer"
    )


def test_sync_stream_sse_with_generous_deadline_still_completes_normally():
    """Control: a deadline LONGER than the full trickle must not falsely
    trip -- the fix bounds a genuinely stuck-forever stream, it doesn't
    break a merely slow one that does eventually finish."""
    transport = _sync_transport()
    chunks = list(
        transport.stream_sse(
            "POST", "/v1/chat/completions", max_stream_seconds=_TOTAL_TRICKLE_S + 5.0
        )
    )
    assert len(chunks) == _CHUNK_COUNT
    assert chunks[0] == {"chunk": 0}
    assert chunks[-1] == {"chunk": _CHUNK_COUNT - 1}


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------

class _TricklingAsyncStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        for i in range(_CHUNK_COUNT):
            await asyncio.sleep(_PER_CHUNK_DELAY_S)
            yield _sse_chunk(i)
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        pass


def _async_transport() -> AsyncTransport:
    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_TricklingAsyncStream()
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncTransport(
        api_key="sk_test", base_url=BASE, timeout=10, max_retries=0,
        default_headers=None, http_client=client,
    )


async def _old_async_stream_sse(transport: AsyncTransport, method: str, path: str):
    """Verbatim replay of the pre-fix AsyncTransport.stream_sse body: no
    overall deadline, iterates response.aiter_lines() to exhaustion."""
    from forgebench._transport import _parse_sse_data_line

    response = await transport._send(method, path, json_body=None, params=None, stream=True)
    try:
        async for line in response.aiter_lines():
            for data in _parse_sse_data_line(line):
                yield data
    finally:
        await response.aclose()


async def test_reproduce_old_async_stream_sse_has_no_overall_deadline():
    transport = _async_transport()
    start = time.monotonic()
    chunks = [c async for c in _old_async_stream_sse(transport, "POST", "/v1/chat/completions")]
    elapsed = time.monotonic() - start
    await transport.aclose()

    assert len(chunks) == _CHUNK_COUNT
    assert elapsed >= _TOTAL_TRICKLE_S * 0.9, (
        f"reproduction failed -- expected the old async code to take "
        f"roughly the full {_TOTAL_TRICKLE_S:.2f}s trickle, took {elapsed:.3f}s"
    )


async def test_async_stream_sse_enforces_overall_deadline():
    """The async path CAN interrupt an in-flight wait (asyncio.wait_for),
    so it cuts off close to the deadline itself, not just between chunks."""
    transport = _async_transport()
    start = time.monotonic()
    received = []
    with pytest.raises(ForgebenchConnectionError, match="overall timeout"):
        async for data in transport.stream_sse(
            "POST", "/v1/chat/completions", max_stream_seconds=_SHORT_DEADLINE_S
        ):
            received.append(data)
    elapsed = time.monotonic() - start
    await transport.aclose()

    assert len(received) < _CHUNK_COUNT
    assert elapsed < _TOTAL_TRICKLE_S * 0.7
    # Tighter bound than the sync path: asyncio.wait_for actually cancels
    # the pending read, so this should land close to the deadline itself.
    assert elapsed < _SHORT_DEADLINE_S + _PER_CHUNK_DELAY_S


async def test_async_stream_sse_with_generous_deadline_still_completes_normally():
    transport = _async_transport()
    chunks = []
    async for c in transport.stream_sse(
        "POST", "/v1/chat/completions", max_stream_seconds=_TOTAL_TRICKLE_S + 5.0
    ):
        chunks.append(c)
    await transport.aclose()
    assert len(chunks) == _CHUNK_COUNT
    assert chunks[0] == {"chunk": 0}
    assert chunks[-1] == {"chunk": _CHUNK_COUNT - 1}
