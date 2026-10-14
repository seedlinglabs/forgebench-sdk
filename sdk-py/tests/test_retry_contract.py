"""Cross-SDK retry contract (packages/sdk-contract/retry.json): same table in ts and java."""
import json
from pathlib import Path

import httpx
import pytest

from forgebench import Forgebench
from forgebench import _transport as tr

CONTRACT = json.loads((Path(__file__).parents[2] / "sdk-contract" / "retry.json").read_text())
ERRORS = {
    "connect": httpx.ConnectError("refused"),
    "timeout": httpx.ReadTimeout("timed out"),
    "reset": httpx.ReadError("reset"),
}


def test_status_set_matches_contract():
    assert tr._RETRY_STATUSES == frozenset(CONTRACT["retryable_statuses"])
    assert tr._UNSAFE_METHODS == frozenset(CONTRACT["unsafe_methods"])
    assert tr.DEFAULT_MAX_RETRIES == CONTRACT["default_max_retries"]


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "POST", "PATCH"])
@pytest.mark.parametrize("idempotent", [False, True])
def test_every_status_follows_the_contract_rules(method, idempotent):
    unsafe = method in CONTRACT["unsafe_methods"] and not idempotent
    allowed = set(CONTRACT["unsafe_retry_statuses"] if unsafe else CONTRACT["retryable_statuses"])
    for status in range(100, 600):
        assert tr._retry_status(method, status, idempotent) is (status in allowed), status


@pytest.mark.parametrize("c", CONTRACT["status_cases"], ids=lambda c: f"{c['method']}-{c['idempotent']}-{c['status']}")
def test_status_table(c):
    assert tr._retry_status(c["method"], c["status"], c["idempotent"]) is c["retry"]


@pytest.mark.parametrize("c", CONTRACT["error_cases"], ids=lambda c: f"{c['method']}-{c['idempotent']}-{c['error']}")
def test_error_table(c):
    assert tr._retry_exc(c["method"], ERRORS[c["error"]], c["idempotent"]) is c["retry"]


def test_read_timeout_and_408_retry_end_to_end(monkeypatch):
    monkeypatch.setattr(tr.time, "sleep", lambda _s: None)
    seq = iter([httpx.ReadTimeout("t"), httpx.Response(408), httpx.Response(200, json={"ok": True})])

    def handler(req):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    client = Forgebench(api_key="sk_x", base_url="http://x", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=2)
    assert client._transport.request("GET", "/p") == {"ok": True}


def _backoff_bounds(attempt):
    b = CONTRACT["backoff"]
    base = min(b["base_ms"] * 2 ** attempt, b["cap_ms"]) / 1000
    return base / 2, base


@pytest.mark.parametrize("attempt", range(7))
def test_backoff_is_jittered_within_the_contract_bounds(attempt):
    lo, hi = _backoff_bounds(attempt)
    seen = {tr._backoff_seconds(attempt) for _ in range(200)}
    assert all(lo <= v <= hi for v in seen)
    assert len(seen) > 1  # jittered, not a constant


@pytest.mark.parametrize("c", CONTRACT["retry_after"]["cases"], ids=lambda c: repr(c["header"]))
def test_retry_after_table(c):
    got = tr._retry_after_seconds(httpx.Response(429, headers={"retry-after": c["header"]}), 1)
    if c["seconds"] is None:
        lo, hi = _backoff_bounds(1)
        assert lo <= got <= hi
    else:
        assert got == c["seconds"]
    assert CONTRACT["retry_after"]["max_seconds"] == 30


def test_a_stream_is_never_retried_once_it_has_started(monkeypatch):
    """Even a GET (retry-eligible) stream: a failure after the first event surfaces, nothing is resent."""
    monkeypatch.setattr(tr.time, "sleep", lambda _s: None)
    calls = []

    class Dying(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"x":1}\n\n'
            raise httpx.ReadError("reset mid-stream")

    def handler(req):
        calls.append(req)
        return httpx.Response(200, stream=Dying(), headers={"content-type": "text/event-stream"})

    client = Forgebench(api_key="sk_x", base_url="http://x", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=2)
    got = []
    with pytest.raises(httpx.ReadError):
        for ev in client._transport.stream_sse("GET", "/p"):
            got.append(ev)
    assert got == [{"x": 1}]
    assert len(calls) == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_retry_after_is_what_the_transport_sleeps(monkeypatch, method):
    sleeps = []
    monkeypatch.setattr(tr.time, "sleep", sleeps.append)
    seq = iter([httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(429, headers={"retry-after": "-1"}), httpx.Response(200, json={})])
    client = Forgebench(api_key="sk_x", base_url="http://x", http_client=httpx.Client(transport=httpx.MockTransport(lambda r: next(seq))), max_retries=2)
    client._transport.request(method, "/p")
    assert sleeps[0] == 3
    assert 0.5 <= sleeps[1] <= 1.0  # a negative value is ignored, not passed to sleep()


def test_path_ids_are_encoded_and_dot_segments_refused():
    """Review #594 #52: the same rules as the TS (encodeURIComponent) and Java (seg) SDKs."""
    from forgebench._transport import seg

    assert seg("a/b c") == "a%2Fb%20c"
    for bad in ("", ".", ".."):
        with pytest.raises(ValueError):
            seg(bad)
