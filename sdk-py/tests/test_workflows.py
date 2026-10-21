"""Workflows: the resource, agents.register, and client.workflow(...).run() threading."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from forgebench import AsyncForgebench, Forgebench

BASE = "http://testserver"
CHAT = f"{BASE}/v1/chat/completions"
KEYS = {"grading": "sk_grade", "retry": "sk_retry", "report": "sk_report"}


def _chat(counter=[0]):
    def handler(request: httpx.Request) -> httpx.Response:
        counter[0] += 1
        cid = f"call-{counter[0]}"
        return httpx.Response(
            200,
            json={"id": "c", "created": 1, "model": "m", "choices": [], "usage": {}, "call_id": cid},
            headers={"x-call-id": cid},
        )

    return handler


def _seen(route):
    return [(c.request.headers.get("authorization"), c.request.headers.get("x-parent-call")) for c in route.calls]


@respx.mock
def test_run_uses_each_steps_key_and_threads_parent():
    route = respx.post(CHAT).mock(side_effect=_chat([0]))
    with Forgebench(api_key="sk_default", base_url=BASE, max_retries=0) as client:
        wf = client.workflow("qp", keys=KEYS)
        with wf.run() as run:
            a = run.step("grading").chat(messages=[{"role": "user", "content": "x"}])
            run.step("retry").chat(messages=[{"role": "user", "content": "x"}])
            run.step("report").chat(messages=[{"role": "user", "content": "x"}])
            assert run.root_call_id == a.call_id == "call-1"
            assert run.call_ids == ["call-1", "call-2", "call-3"]
        with wf.run() as second:  # a new run starts a new root
            second.step("retry").chat(messages=[{"role": "user", "content": "x"}])
    assert _seen(route) == [
        ("Bearer sk_grade", None),
        ("Bearer sk_retry", "call-1"),
        ("Bearer sk_report", "call-2"),
        ("Bearer sk_retry", None),
    ]


@respx.mock
def test_unknown_step_and_missing_key_are_refused_before_any_call():
    route = respx.post(CHAT).mock(side_effect=_chat([0]))
    with Forgebench(api_key="k", base_url=BASE, max_retries=0) as client:
        with pytest.raises(ValueError, match="no api key for step 'nope'"):
            client.workflow("qp", keys=KEYS).run().step("nope")
        with pytest.raises(ValueError, match="no api key for step"):
            client.workflow("qp", keys={"grading": ""})
    assert not route.called


@respx.mock
async def test_async_run_threads_parent_and_keys():
    route = respx.post(CHAT).mock(side_effect=_chat([10]))
    async with AsyncForgebench(api_key="sk_default", base_url=BASE, max_retries=0) as client:
        async with client.workflow("qp", keys=KEYS).run() as run:
            await run.step("grading").chat(messages=[{"role": "user", "content": "x"}])
            await run.step("retry").chat(messages=[{"role": "user", "content": "x"}])
            assert run.root_call_id == "call-11" and run.last_call_id == "call-12"
    assert _seen(route) == [("Bearer sk_grade", None), ("Bearer sk_retry", "call-11")]


@respx.mock
def test_workflows_resource_routes_and_bodies():
    wf = f"{BASE}/v1/workflows"
    respx.get(wf).mock(return_value=httpx.Response(200, json=[{"slug": "qp"}]))
    respx.get(f"{wf}/qp").mock(return_value=httpx.Response(200, json={"slug": "qp"}))
    respx.get(f"{wf}/qp/checks").mock(return_value=httpx.Response(200, json={"checks": []}))
    reg = respx.post(wf).mock(return_value=httpx.Response(201, json={"credentials": []}))
    respx.patch(f"{wf}/qp").mock(return_value=httpx.Response(200, json={"slug": "qp"}))
    respx.delete(f"{wf}/qp").mock(return_value=httpx.Response(200, json={"binding_retained": False}))
    step = respx.post(f"{wf}/qp/steps").mock(return_value=httpx.Response(201, json={}))
    respx.delete(f"{wf}/qp/steps/retry").mock(return_value=httpx.Response(200, json={"binding_retained": True}))
    edge = respx.post(f"{wf}/qp/edges").mock(return_value=httpx.Response(201, json={}))
    deledge = respx.delete(f"{wf}/qp/edges/e1").mock(return_value=httpx.Response(200, json={"binding_revoked": True}))
    with Forgebench(api_key="k", base_url=BASE, max_retries=0) as c:
        assert c.workflows.list() == [{"slug": "qp"}]
        assert c.workflows.get("qp")["slug"] == "qp"
        assert c.workflows.checks("qp") == {"checks": []}
        c.workflows.register(
            name="QP", owner_identity_id="o", steps=[{"name": "grading", "model": "m"}],
            edges=[{"from": "grading", "to": "retry", "kind": "sequence"}], issue_credential=False,
        )
        c.workflows.update("qp", name="New")
        assert c.workflows.archive("qp") == {"binding_retained": False}
        c.workflows.add_step("qp", name="retry", model="m")
        assert c.workflows.remove_step("qp", "retry") == {"binding_retained": True}
        c.workflows.add_edge("qp", from_step="grading", to_step="retry", kind="task")
        assert c.workflows.remove_edge("qp", "e1", revoke_binding=True)["binding_revoked"] is True
    body = json.loads(reg.calls[0].request.content)
    assert body["issue_credential"] is False and body["edges"][0]["from"] == "grading"
    assert json.loads(step.calls[0].request.content) == {"name": "retry", "model": "m"}
    assert json.loads(edge.calls[0].request.content) == {"from": "grading", "to": "retry", "kind": "task"}
    assert deledge.calls[0].request.url.params["revoke_binding"] == "true"


@respx.mock
def test_agents_register_allowed_models_omitted_vs_null():
    route = respx.post(f"{BASE}/v1/agents/register").mock(return_value=httpx.Response(201, json={"agent": {}}))
    with Forgebench(api_key="k", base_url=BASE, max_retries=0) as c:
        c.agents.register(name="a", owner_identity_id="o")
        c.agents.register(name="b", owner_identity_id="o", allowed_models=None, issue_credential=False)
        c.agents.register(name="c", owner_identity_id="o", allowed_models=["m"])
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert "allowed_models" not in bodies[0] and bodies[0]["issue_credential"] is True
    assert bodies[1]["allowed_models"] is None and bodies[1]["issue_credential"] is False
    assert bodies[2]["allowed_models"] == ["m"]


@respx.mock
def test_a_call_without_an_id_does_not_break_the_chain():
    ids = iter(["call-1", None, "call-3"])

    def handler(request):
        cid = next(ids)
        return httpx.Response(200, json={"id": "c", "created": 1, "model": "m", "choices": [], "usage": {}, "call_id": cid})

    route = respx.post(CHAT).mock(side_effect=handler)
    with Forgebench(api_key="k", base_url=BASE, max_retries=0) as client:
        run = client.workflow("qp", keys=KEYS).run()
        for name in ("grading", "retry", "report"):
            run.step(name).chat(messages=[{"role": "user", "content": "x"}])
        assert run.last_call_id == "call-3" and run.call_ids == ["call-1", "call-3"]
    assert [p for _, p in _seen(route)] == [None, "call-1", "call-1"]


@respx.mock
async def test_async_a_call_without_an_id_does_not_break_the_chain():
    ids = iter(["call-1", None, "call-3"])

    def handler(request):
        return httpx.Response(200, json={"id": "c", "created": 1, "model": "m", "choices": [], "usage": {}, "call_id": next(ids)})

    route = respx.post(CHAT).mock(side_effect=handler)
    async with AsyncForgebench(api_key="k", base_url=BASE, max_retries=0) as client:
        run = client.workflow("qp", keys=KEYS).run()
        for name in ("grading", "retry", "report"):
            await run.step(name).chat(messages=[{"role": "user", "content": "x"}])
    assert [p for _, p in _seen(route)] == [None, "call-1", "call-1"]


FP = "a" * 64


@respx.mock
def test_workflow_suggestions_routes_and_bodies():
    base = f"{BASE}/v1/workflows/suggestions"
    respx.get(base).mock(return_value=httpx.Response(200, json=[{"fingerprint": FP}]))
    acc = respx.post(f"{base}/{FP}/accept").mock(return_value=httpx.Response(201, json={"workflow": {"slug": "w"}}))
    dis = respx.post(f"{base}/{FP}/dismiss").mock(return_value=httpx.Response(200, json={"status": "dismissed"}))
    with Forgebench(api_key="k", base_url=BASE, max_retries=0) as c:
        assert c.workflows.suggestions() == [{"fingerprint": FP}]
        c.workflows.accept_suggestion(FP)
        c.workflows.accept_suggestion(
            FP, name="N", slug="n", owner_identity_id="o", edges=[{"from_agent_id": "a", "to_agent_id": "b", "kind": "task"}]
        )
        assert c.workflows.dismiss_suggestion(FP) == {"status": "dismissed"}
    assert json.loads(acc.calls[0].request.content) == {}
    assert json.loads(acc.calls[1].request.content) == {
        "name": "N", "slug": "n", "owner_identity_id": "o",
        "edges": [{"from_agent_id": "a", "to_agent_id": "b", "kind": "task"}],
    }
    assert dis.called


@respx.mock
async def test_async_workflow_suggestions():
    base = f"{BASE}/v1/workflows/suggestions"
    respx.get(base).mock(return_value=httpx.Response(200, json=[]))
    acc = respx.post(f"{base}/{FP}/accept").mock(return_value=httpx.Response(201, json={}))
    respx.post(f"{base}/{FP}/dismiss").mock(return_value=httpx.Response(200, json={"status": "dismissed"}))
    async with AsyncForgebench(api_key="k", base_url=BASE, max_retries=0) as c:
        assert await c.workflows.suggestions() == []
        await c.workflows.accept_suggestion(FP, edges=[])
        assert (await c.workflows.dismiss_suggestion(FP))["status"] == "dismissed"
    assert json.loads(acc.calls[0].request.content) == {"edges": []}
