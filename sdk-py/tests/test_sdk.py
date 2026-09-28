"""Offline unit tests for the Forgebench Python SDK.

These mock the control plane HTTP surface with ``respx`` so the SDK can be
verified without the full docker stack. They assert: request shaping (auth
header, client api_key/api_base never leak), response parsing, typed error
mapping (notably 402 -> BudgetExceededError), SSE streaming, run polling, and
the async client.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from forgebench import (
    AsyncForgebench,
    AuthenticationError,
    BudgetExceededError,
    NotFoundError,
    PermissionDeniedError,
    Forgebench,
    new_trace_id,
)

BASE = "http://testserver"
KEY = "sk_test_abc123"


def _chat_response_body(model: str = "mock-gpt") -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from the Forgebench mock model."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }


@respx.mock
def test_chat_completion_sync_and_auth_header():
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response_body())

    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=responder)

    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        resp = client.chat.completions.create(
            model="mock-gpt",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert captured["auth"] == f"Bearer {KEY}"
    assert captured["body"]["model"] == "mock-gpt"
    assert captured["body"]["stream"] is False
    assert resp.choices[0].message.content == "Hello from the Forgebench mock model."
    assert resp.usage.total_tokens == 12


@respx.mock
def test_client_credentials_in_extra_body_are_sent_but_server_strips():
    # The SDK passes extra_body through; the chokepoint uses extra='ignore' and
    # strips api_key/api_base. We assert the SDK doesn't *invent* credentials.
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response_body())

    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=responder)
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])

    # No api_key / api_base manufactured by the SDK in the request body.
    assert "api_key" not in captured["body"]
    assert "api_base" not in captured["body"]


@respx.mock
def test_budget_gate_maps_to_402_exception():
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            402, json={"detail": "monthly budget exceeded", "code": "budget_exceeded"}
        )
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        with pytest.raises(BudgetExceededError) as ei:
            client.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert ei.value.status_code == 402
    assert ei.value.code == "budget_exceeded"
    assert "budget" in ei.value.message


@respx.mock
def test_bad_key_maps_to_401():
    respx.get(f"{BASE}/v1/auth/whoami").mock(
        return_value=httpx.Response(401, json={"detail": "invalid api key"})
    )
    with Forgebench(api_key="sk_bad", base_url=BASE, max_retries=0) as client:
        with pytest.raises(AuthenticationError) as ei:
            client.whoami()
    assert ei.value.status_code == 401


@respx.mock
def test_streaming_chat_completion():
    sse = (
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"mock-gpt",'
        '"choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}\n\n'
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"mock-gpt",'
        '"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}\n\n'
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"mock-gpt",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        chunks = list(
            client.chat.completions.create(
                messages=[{"role": "user", "content": "hi"}], stream=True
            )
        )
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "Hello world"
    assert chunks[-1].choices[0].finish_reason == "stop"


@respx.mock
def test_streaming_chat_completion_with_parent_call_id_sends_header():
    """Regression test: SyncTransport.stream_sse previously had no ``headers``
    parameter, so stream=True + parent_call_id raised NameError. Also asserts
    the header actually reaches the wire, not just that the call succeeds."""
    sse = (
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"mock-gpt",'
        '"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        chunks = list(
            client.chat.completions.create(
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
                parent_call_id="call_root_123",
            )
        )
    assert chunks[0].choices[0].delta.content == "hi"
    assert route.calls.last.request.headers["X-Parent-Call"] == "call_root_123"


def test_default_base_url_is_production(monkeypatch):
    monkeypatch.delenv("FORGEBENCH_BASE_URL", raising=False)
    client = Forgebench(api_key=KEY)
    try:
        assert client.base_url == "https://api.forgebench.ai/"
    finally:
        client.close()


@respx.mock
def test_agents_list_and_create():
    respx.get(f"{BASE}/v1/agents").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "a1", "name": "Support", "model": "mock-gpt", "config": {}},
                {"id": "a2", "name": "Sales", "model": "mock-gpt", "config": {}},
            ],
        )
    )
    respx.post(f"{BASE}/v1/agents").mock(
        return_value=httpx.Response(
            201, json={"id": "a3", "name": "New", "model": "mock-gpt", "config": {}}
        )
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        agents = client.agents.list()
        assert [a.name for a in agents] == ["Support", "Sales"]
        created = client.agents.create(name="New", owner_identity_id="own-1")
        assert created.id == "a3"


@respx.mock
def test_runs_create_get_and_wait_polls_to_terminal():
    respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(
            201, json={"id": "r1", "status": "queued", "model": "mock-gpt", "input": {}}
        )
    )
    states = iter(
        [
            {"id": "r1", "status": "running", "model": "mock-gpt", "input": {}},
            {"id": "r1", "status": "succeeded", "model": "mock-gpt", "input": {},
             "output": {"ok": True}},
        ]
    )
    respx.get(f"{BASE}/v1/runs/r1").mock(
        side_effect=lambda req: httpx.Response(200, json=next(states))
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        run = client.runs.create(model="mock-gpt", input={"x": 1})
        assert run.status == "queued"
        final = client.runs.wait("r1", timeout=5, poll_interval=0.0)
        assert final.status == "succeeded"
        assert final.is_terminal


@respx.mock
def test_agent_run_posts_agent_id_to_runs():
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"id": "r9", "status": "succeeded", "model": "mock-gpt",
                  "agent_id": "a1", "input": {}},
        )

    respx.post(f"{BASE}/v1/runs").mock(side_effect=responder)
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        run = client.agents.run("a1", input={"q": "hello"})
    assert captured["body"]["agent_id"] == "a1"
    assert captured["body"]["input"] == {"q": "hello"}
    assert run.agent_id == "a1"


@respx.mock
def test_runs_get_missing_maps_to_404():
    respx.get(f"{BASE}/v1/runs/nope").mock(
        return_value=httpx.Response(404, json={"detail": "run not found"})
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        with pytest.raises(NotFoundError):
            client.runs.get("nope")


@respx.mock
def test_metering_summary_parsing():
    respx.get(f"{BASE}/v1/metering/summary").mock(
        return_value=httpx.Response(
            200,
            json={
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "total_events": 3,
                "total_tokens": 36,
                "total_cost_usd": 0.009,
                "by_model": {"mock-gpt": 0.009},
                "monthly_limit_usd": 0.01,
                "spent_usd": 0.009,
                "remaining_usd": 0.001,
            },
        )
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        s = client.metering_summary()
    assert s.total_events == 3
    assert s.by_model["mock-gpt"] == pytest.approx(0.009)
    assert s.remaining_usd == pytest.approx(0.001)


@pytest.mark.asyncio
@respx.mock
async def test_async_chat_completion():
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_response_body())
    )
    async with AsyncForgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        resp = await client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}]
        )
    assert resp.choices[0].message.content == "Hello from the Forgebench mock model."


@pytest.mark.asyncio
@respx.mock
async def test_async_streaming():
    sse = (
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"mock-gpt",'
        '"choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )
    )
    async with AsyncForgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        out = []
        stream = await client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}], stream=True
        )
        async for chunk in stream:
            for ch in chunk.choices:
                if ch.delta.content:
                    out.append(ch.delta.content)
    assert "".join(out) == "Hi"


def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("FORGEBENCH_BASE_URL", "http://env-host:9000")
    monkeypatch.setenv("FORGEBENCH_API_KEY", "sk_env")
    client = Forgebench()
    try:
        assert client.base_url == "http://env-host:9000/"
    finally:
        client.close()


def test_new_trace_id_is_32_hex_chars():
    """Same format the control plane mints server-side (uuid4().hex) so a
    trace id looks identical whichever side generated it."""
    tid = new_trace_id()
    assert len(tid) == 32
    int(tid, 16)  # must be valid hex
    assert new_trace_id() != tid  # not a constant


@respx.mock
def test_chat_completion_trace_id_is_sent_when_supplied():
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response_body())

    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=responder)

    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}], trace_id="abc123trace"
        )

    assert captured["body"]["trace_id"] == "abc123trace"


@respx.mock
def test_chat_completion_omits_trace_id_when_not_supplied():
    """The server mints its own trace_id when the field is absent — the SDK
    must not send a null/empty placeholder that could shadow that."""
    captured = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response_body())

    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=responder)

    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])

    assert "trace_id" not in captured["body"]


@respx.mock
def test_chat_completion_returns_server_resolved_trace_id():
    """Whether or not the caller supplied trace_id, the response echoes back
    whatever id the call was actually correlated under, so a caller who
    omitted it can still thread it into a later correlated call."""
    body = _chat_response_body()
    body["trace_id"] = "server-minted-trace"
    respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(200, json=body))

    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        resp = client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])

    assert resp.trace_id == "server-minted-trace"


@respx.mock
def test_agent_tools_list():
    respx.get(f"{BASE}/v1/agent-tools").mock(
        return_value=httpx.Response(
            200,
            json={
                "agent_id": "22222222-2222-2222-2222-222222222222",
                "tools": [
                    {"server": "jira", "tool": "search_tickets", "description": "Search Jira"},
                ],
            },
        )
    )
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as client:
        tools = client.agent_tools.list()
    assert len(tools) == 1
    assert tools[0].server == "jira"
    assert tools[0].tool == "search_tickets"


