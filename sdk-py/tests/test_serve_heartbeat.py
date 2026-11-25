"""serve() keeps a long task alive with heartbeats, and survives a server without them."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
import respx

from forgebench import Forgebench

BASE = "http://testserver"
TASK = {
    "id": "t1", "context_id": "c1", "status": {"state": "working"}, "input": {"message": {"role": "user", "parts": [{"text": "go"}]}},
    "delivery": "pull", "created_at": "2026-11-10T00:00:00Z", "updated_at": "2026-11-10T00:00:00Z",
}


def _client() -> Forgebench:
    return Forgebench(api_key="sk_test", base_url=BASE, max_retries=0)


@respx.mock
def test_long_handler_sends_heartbeats_then_the_reply():
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(return_value=httpx.Response(200, json=TASK))
    posts: list[dict] = []

    def result(request):
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={**TASK, "status": {"state": posts[-1]["state"]}})

    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(side_effect=result)

    def slow(task):
        time.sleep(0.35)
        return task.done(text="ok")

    assert _client().agents.serve(slow, max_tasks=1, heartbeat=0.1) == 1
    states = [p["state"] for p in posts]
    assert states[-1] == "completed"
    assert states.count("working") >= 2, states


@respx.mock
def test_server_without_heartbeats_does_not_break_serve():
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(return_value=httpx.Response(200, json=TASK))
    seen: list[str] = []

    def result(request):
        state = json.loads(request.content)["state"]
        seen.append(state)
        if state == "working":
            return httpx.Response(422, json={"detail": "a pull result must be completed, failed, or input_required"})
        return httpx.Response(200, json={**TASK, "status": {"state": state}})

    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(side_effect=result)

    def slow(task):
        time.sleep(0.3)
        return task.done(text="ok")

    assert _client().agents.serve(slow, max_tasks=1, heartbeat=0.1) == 1
    assert seen.count("working") == 1, "one refused heartbeat, then it stops trying"
    assert seen[-1] == "completed"


@respx.mock
def test_heartbeats_continue_after_a_transient_server_error():
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(return_value=httpx.Response(200, json=TASK))
    seen: list[str] = []

    def result(request):
        state = json.loads(request.content)["state"]
        seen.append(state)
        if state == "working" and seen.count("working") == 1:
            return httpx.Response(502, json={"detail": "bad gateway"})
        return httpx.Response(200, json={**TASK, "status": {"state": state}})

    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(side_effect=result)

    def slow(task):
        time.sleep(0.45)
        return "ok"

    assert _client().agents.serve(slow, max_tasks=1, heartbeat=0.1) == 1
    assert seen.count("working") >= 3, seen
    assert seen[-1] == "completed"


@pytest.mark.asyncio
@respx.mock
async def test_async_heartbeats_continue_after_a_transient_server_error():
    from forgebench import AsyncForgebench

    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(return_value=httpx.Response(200, json=TASK))
    seen: list[str] = []

    def result(request):
        state = json.loads(request.content)["state"]
        seen.append(state)
        if state == "working" and seen.count("working") == 1:
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(200, json={**TASK, "status": {"state": state}})

    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(side_effect=result)

    async def slow(task):
        await asyncio.sleep(0.45)
        return "ok"

    async with AsyncForgebench(api_key="sk_test", base_url=BASE, max_retries=0) as client:
        assert await client.agents.serve(slow, max_tasks=1, heartbeat=0.1) == 1
    assert seen.count("working") >= 3, seen


@respx.mock
def test_heartbeat_off():
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(return_value=httpx.Response(200, json=TASK))
    route = respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(
        return_value=httpx.Response(200, json={**TASK, "status": {"state": "completed"}})
    )

    def slow(task):
        time.sleep(0.2)
        return "ok"

    _client().agents.serve(slow, max_tasks=1, heartbeat=0)
    assert route.call_count == 1


@respx.mock
def test_first_heartbeat_goes_out_when_the_task_is_claimed():
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(return_value=httpx.Response(200, json=TASK))
    posts: list[str] = []

    def result(request):
        posts.append(json.loads(request.content)["state"])
        return httpx.Response(200, json={**TASK, "status": {"state": posts[-1]}})

    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(side_effect=result)

    def quick(task):
        time.sleep(0.05)
        return "ok"

    _client().agents.serve(quick, max_tasks=1, heartbeat=60)
    assert posts == ["working", "completed"], "the door learns at claim time that this worker heartbeats"


@respx.mock
def test_a_reply_refused_because_the_task_ended_does_not_stop_serving():
    tasks = [dict(TASK, id="t1"), dict(TASK, id="t2")]
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(side_effect=[httpx.Response(200, json=t) for t in tasks])
    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(
        return_value=httpx.Response(409, json={"detail": {"code": "task_not_working", "message": "task is canceled"}})
    )
    respx.post(f"{BASE}/v1/agents/me/tasks/t2/result").mock(
        return_value=httpx.Response(200, json={**TASK, "id": "t2", "status": {"state": "completed"}})
    )
    assert _client().agents.serve(lambda task: "ok", max_tasks=2, heartbeat=0) == 2


@pytest.mark.asyncio
@respx.mock
async def test_async_serve_heartbeats_and_survives_a_refused_reply():
    from forgebench import AsyncForgebench

    tasks = [dict(TASK, id="t1"), dict(TASK, id="t2")]
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(side_effect=[httpx.Response(200, json=t) for t in tasks])
    states: dict[str, list[str]] = {"t1": [], "t2": []}

    def result_for(tid, final_status):
        def handler(request):
            state = json.loads(request.content)["state"]
            states[tid].append(state)
            if state != "working" and final_status != 200:
                return httpx.Response(final_status, json={"detail": "task is canceled"})
            return httpx.Response(200, json={**TASK, "id": tid, "status": {"state": state}})
        return handler

    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(side_effect=result_for("t1", 409))
    respx.post(f"{BASE}/v1/agents/me/tasks/t2/result").mock(side_effect=result_for("t2", 200))

    async def slow(task):
        await asyncio.sleep(0.25)
        return "ok"

    async with AsyncForgebench(api_key="sk_test", base_url=BASE, max_retries=0) as client:
        assert await client.agents.serve(slow, max_tasks=2, heartbeat=0.1) == 2
    assert states["t1"][0] == "working" and states["t1"].count("working") >= 2
    assert states["t2"][-1] == "completed"


@respx.mock
def test_a_call_made_inside_a_handler_carries_the_task_as_its_parent():
    respx.get(f"{BASE}/v1/agents/me/tasks/next").mock(return_value=httpx.Response(200, json={**TASK, "call_id": "call-of-t1"}))
    respx.post(f"{BASE}/v1/agents/me/tasks/t1/result").mock(
        return_value=httpx.Response(200, json={**TASK, "status": {"state": "completed"}})
    )
    other = respx.post(f"{BASE}/v1/agents/other/tasks").mock(return_value=httpx.Response(201, json={**TASK, "id": "t9"}))
    client = _client()

    def handler(task):
        client.agents.call("other", text="help", wait=0)
        client.agents.call("other", text="unrelated", wait=0, parent_call_id="")
        return "ok"

    client.agents.serve(handler, max_tasks=1, heartbeat=0)
    parents = [c.request.headers.get("x-parent-call") for c in other.calls]
    assert parents == ["call-of-t1", None]
    client.agents.call("other", text="outside serve", wait=0)
    assert other.calls[-1].request.headers.get("x-parent-call") is None
