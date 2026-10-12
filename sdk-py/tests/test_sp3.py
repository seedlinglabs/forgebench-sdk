"""SDK 1.1 (SP3): stream ids, trace params, retries, dispatch."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from forgebench import AsyncForgebench, Forgebench, ServerError, __version__

BASE = "http://testserver"
KEY = "sk_test_abc123"
CHAT = f"{BASE}/v1/chat/completions"

_SSE = (
    'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[],'
    '"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
    'data: {"id":"call-9","object":"chat.completion.chunk","created":1,"model":"m","choices":[],'
    '"trace_id":"t-body","call_id":"call-9"}\n\n'
    "data: [DONE]\n\n"
)


def _sse_response(request=None):
    return httpx.Response(
        200,
        content=_SSE.encode(),
        headers={"content-type": "text/event-stream", "x-trace-id": "t-hdr", "x-call-id": "call-9"},
    )


def test_version_is_1_2_0():
    assert __version__ == "1.2.0"


@respx.mock
def test_stream_exposes_ids_and_consumes_meta_chunk():
    respx.post(CHAT).mock(side_effect=_sse_response)
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        stream = client.chat.completions.create(messages=[{"role": "user", "content": "x"}], stream=True)
        first = next(stream)
        assert first.choices[0].delta.content == "hi"
        assert stream.trace_id == "t-hdr" and stream.call_id == "call-9"
        rest = list(stream)
    assert len(rest) == 1 and rest[0].choices == []  # usage chunk only; meta chunk consumed
    assert stream.trace_id == "t-body"


@respx.mock
async def test_async_stream_exposes_ids():
    respx.post(CHAT).mock(side_effect=_sse_response)
    async with AsyncForgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        stream = await client.chat.completions.create(messages=[{"role": "user", "content": "x"}], stream=True)
        chunks = [c async for c in stream]
    assert len(chunks) == 2 and stream.call_id == "call-9" and stream.trace_id == "t-body"


@respx.mock
def test_trace_params_are_first_class():
    route = respx.post(CHAT).mock(
        return_value=httpx.Response(200, json={"id": "1", "created": 1, "model": "m", "choices": [], "usage": {}})
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        client.chat.completions.create(
            messages=[{"role": "user", "content": "x"}],
            user="u1",
            session_id="s1",
            tags=["a"],
            metadata={"k": "v", "dimensions": {"store": "1"}},
            dimensions={"region": "eu"},
        )
    body = json.loads(route.calls.last.request.content)
    assert body["user"] == "u1" and body["session_id"] == "s1" and body["tags"] == ["a"]
    assert body["metadata"] == {"k": "v", "dimensions": {"store": "1", "region": "eu"}}


def test_trace_id_validated_locally():
    with Forgebench(api_key=KEY, base_url=BASE) as client:
        with pytest.raises(ValueError):
            client.chat.completions.create(messages=[{"role": "user", "content": "x"}], trace_id="x" * 65)
        with pytest.raises(ValueError):
            client.chat.completions.create(messages=[{"role": "user", "content": "x"}], trace_id="")


@respx.mock
def test_post_not_retried_on_5xx_but_get_is():
    post = respx.post(CHAT).mock(return_value=httpx.Response(500, json={"detail": "boom"}))
    get = respx.get(f"{BASE}/v1/agents").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=[])]
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=2) as client:
        with pytest.raises(ServerError):
            client.chat.completions.create(messages=[{"role": "user", "content": "x"}])
        assert client.agents.list() == []
    assert post.call_count == 1 and get.call_count == 2


@respx.mock
def test_post_retried_on_429_and_connect_error_not_on_read_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    ok = httpx.Response(200, json={"id": "1", "created": 1, "model": "m", "choices": [], "usage": {}})
    route = respx.post(CHAT).mock(side_effect=[httpx.Response(429), httpx.ConnectError("refused"), ok])
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=2) as client:
        client.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert route.call_count == 3

    route.mock(side_effect=[httpx.ReadError("reset"), ok])
    route.calls.clear()
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=2) as client:
        with pytest.raises(Exception):
            client.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert route.call_count == 1


@respx.mock
def test_dispatch_reports_once_and_survives_report_failure(caplog):
    report = respx.post(f"{BASE}/v1/agent-tools/report").mock(return_value=httpx.Response(500))
    calls = [{"id": "t1", "function": {"name": "lookup", "arguments": "{}"}}]
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client, caplog.at_level(logging.WARNING):
        msgs = client.agent_tools.dispatch(calls, lambda name, args: {"ok": True}, call_id="c1")
    assert report.call_count == 1
    assert json.loads(msgs[0]["content"]) == {"ok": True}
    assert "report for tool lookup failed" in caplog.text


@respx.mock
def test_dispatch_tool_error_reported_once():
    report = respx.post(f"{BASE}/v1/agent-tools/report").mock(return_value=httpx.Response(200, json={}))

    def boom(name, args):
        raise RuntimeError("down")

    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        msgs = client.agent_tools.dispatch([{"id": "t1", "function": {"name": "x"}}], boom, call_id="c1")
    assert report.call_count == 1
    assert json.loads(report.calls.last.request.content)["error"] == "down"
    assert json.loads(msgs[0]["content"]) == {"error": "down"}
