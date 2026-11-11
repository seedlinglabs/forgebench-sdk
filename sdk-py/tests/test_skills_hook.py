"""SkillsHook: an agent on its own servers gets its governed skills.

Offline: the control plane is mocked with respx. The server side of the same
contract is pinned in apps/control-plane/tests/test_skill_hooks.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest
import respx

from forgebench import Forgebench, SkillsHook, SkillsUnavailableError, WebhookVerificationError, verify_webhook
from forgebench.skills import SKILLS_HEADER

BASE = "http://testserver"
KEY = "sk_agent_test"
URL = f"{BASE}/v1/skills/agent-manifest"


def _skill(name: str, *, mandatory: bool, text: str) -> dict:
    return {
        "ref": f"skill:acme/{name}@1", "skill_id": f"id-{name}", "name": name, "version": 1,
        "description": f"{name} description", "mandatory": mandatory, "instructions": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(), "dir_name": f"acme-{name}",
        "content": f"---\nname: acme-{name}\n---\n\n{text}\n",
    }


MANIFEST = {
    "agent_id": "a-1", "etag": '"v1"', "enforcement": "inject",
    "skills": [
        _skill("pii-policy", mandatory=True, text="Never output a customer's card number."),
        _skill("expense-review", mandatory=False, text="Check the receipt date before approving."),
    ],
}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@respx.mock
def test_the_manifest_is_cached_then_revalidated_with_its_etag():
    route = respx.get(URL).mock(side_effect=[
        httpx.Response(200, json=MANIFEST),
        httpx.Response(304),
    ])
    clock = Clock()
    hook = SkillsHook(api_key=KEY, base_url=BASE, refresh_seconds=60, clock=clock)

    m = hook.manifest()
    assert [s.name for s in m.mandatory] == ["pii-policy"] and [s.name for s in m.optional] == ["expense-review"]
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    assert "if-none-match" not in route.calls[0].request.headers

    hook.manifest()  # inside the refresh window: no request
    assert route.call_count == 1

    clock.t += 61
    assert hook.manifest() is m  # 304: same object kept
    assert route.call_count == 2 and route.calls[1].request.headers["if-none-match"] == '"v1"'


@respx.mock
def test_an_outage_keeps_the_last_manifest_and_a_cold_start_refuses():
    respx.get(URL).mock(side_effect=[httpx.Response(200, json=MANIFEST), httpx.ConnectError("down")])
    hook = SkillsHook(api_key=KEY, base_url=BASE, refresh_seconds=0)
    first = hook.manifest()
    assert hook.manifest() is first  # network error, cached copy used

    respx.get(URL).mock(return_value=httpx.Response(503, text="maintenance"))
    cold = SkillsHook(api_key=KEY, base_url=BASE)
    with pytest.raises(SkillsUnavailableError, match="503"):
        cold.manifest()  # never call the model without its policy


@respx.mock
def test_apply_composes_like_a_hosted_run():
    respx.get(URL).mock(return_value=httpx.Response(200, json=MANIFEST))
    hook = SkillsHook(api_key=KEY, base_url=BASE)
    out = hook.apply([
        {"role": "system", "content": "You are the finance assistant."},
        {"role": "user", "content": "Approve this taxi receipt."},
    ])
    assert [m["role"] for m in out] == ["system", "system", "system", "user"]
    assert out[0]["content"].startswith("# Skill: skill:acme/expense-review@1")
    assert out[1]["content"] == "You are the finance assistant."
    assert out[2]["content"].startswith("# MANDATORY POLICY") and "Never output a customer's card number." in out[2]["content"]
    assert out[3] == {"role": "user", "content": "Approve this taxi receipt."}
    assert hook.headers() == {SKILLS_HEADER: ",".join(s["sha256"] for s in MANIFEST["skills"])}


@respx.mock
def test_wrap_adds_skills_to_a_forgebench_client_call():
    respx.get(URL).mock(return_value=httpx.Response(200, json=MANIFEST))
    chat = respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 0, "model": "mock-gpt",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }))
    client = SkillsHook(api_key=KEY, base_url=BASE).wrap(Forgebench(api_key=KEY, base_url=BASE))
    client.chat.completions.create(model="mock-gpt", messages=[{"role": "user", "content": "hi"}],
                                   extra_headers={"X-Mine": "1"})
    sent = chat.calls[0].request
    body = json.loads(sent.content)
    assert [m["role"] for m in body["messages"]] == ["system", "system", "user"]
    assert "Never output a customer's card number." in body["messages"][1]["content"]
    assert sent.headers[SKILLS_HEADER].count(",") == 1 and sent.headers["x-mine"] == "1"


def test_wrap_works_with_an_openai_shaped_client():
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return "response"

    class Client:
        class chat:  # noqa: N801 -- mirrors openai.OpenAI().chat
            completions = Completions()

    from forgebench.skills import Skill, SkillManifest

    hook = SkillsHook(api_key=KEY, base_url=BASE)
    # Pre-seeded so the test stays offline.
    hook._manifest = SkillManifest("a-1", '"v1"', "off", tuple(Skill.from_dict(s) for s in MANIFEST["skills"]))
    hook._checked_at = hook._clock()
    assert hook.wrap(Client()).chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}]) == "response"
    assert len(calls[0]["messages"]) == 3 and SKILLS_HEADER in calls[0]["extra_headers"]


def _signed(body: bytes, secret: str, ts: int) -> str:
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


@respx.mock
def test_a_webhook_is_verified_and_forces_a_recheck():
    route = respx.get(URL).mock(side_effect=[httpx.Response(200, json=MANIFEST), httpx.Response(304)])
    hook = SkillsHook(api_key=KEY, base_url=BASE, refresh_seconds=3600)
    hook.manifest()
    body = json.dumps({"type": "skills.changed", "changes": [{"change": "skill.publish"}]}).encode()
    secret = "whsec_test"
    now = int(time.time())

    with pytest.raises(WebhookVerificationError, match="does not match"):
        hook.handle_webhook(body, _signed(body, "whsec_wrong", now), secret)
    with pytest.raises(WebhookVerificationError, match="tolerance"):
        verify_webhook(body, _signed(body, secret, now - 3600), secret)
    with pytest.raises(WebhookVerificationError, match="malformed"):
        verify_webhook(body, None, secret)
    hook.manifest()
    assert route.call_count == 1  # a rejected delivery changes nothing

    event = hook.handle_webhook(body, _signed(body, secret, now), secret)
    assert event["type"] == "skills.changed"
    hook.manifest()
    assert route.call_count == 2  # re-checked at once, despite the hour-long window


@respx.mock
def test_write_files_installs_skills_and_removes_only_its_own(tmp_path):
    respx.get(URL).mock(side_effect=[
        httpx.Response(200, json=MANIFEST),
        httpx.Response(200, json={**MANIFEST, "etag": '"v2"', "skills": MANIFEST["skills"][:1]}),
    ])
    (tmp_path / "someone-elses").mkdir()
    hook = SkillsHook(api_key=KEY, base_url=BASE, refresh_seconds=0)
    paths = hook.write_files(tmp_path)
    assert sorted(p.parent.name for p in paths) == ["acme-expense-review", "acme-pii-policy"]
    assert (tmp_path / "acme-pii-policy" / "SKILL.md").read_text().startswith("---\nname: acme-pii-policy")

    hook.write_files(tmp_path)  # expense-review was detached
    assert sorted(p.name for p in tmp_path.iterdir()) == ["acme-pii-policy", "someone-elses"]


# ---------------------------------------------------------------------------
# AsyncSkillsHook: the same contract under asyncio.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_async_hook_caches_revalidates_and_survives_an_outage():
    from forgebench import AsyncSkillsHook

    route = respx.get(URL).mock(side_effect=[
        httpx.Response(200, json=MANIFEST), httpx.Response(304), httpx.ConnectError("down")])
    clock = Clock()
    hook = AsyncSkillsHook(api_key=KEY, base_url=BASE, refresh_seconds=60, clock=clock)
    m = await hook.manifest()
    assert [s.name for s in m.mandatory] == ["pii-policy"]
    await hook.manifest()
    assert route.call_count == 1
    clock.t += 61
    assert await hook.manifest() is m and route.calls[1].request.headers["if-none-match"] == '"v1"'
    clock.t += 61
    assert await hook.manifest() is m  # outage: the cached copy keeps working
    await hook.aclose()

    respx.get(URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(SkillsUnavailableError, match="401"):
        await AsyncSkillsHook(api_key="sk_wrong", base_url=BASE).manifest()


@pytest.mark.asyncio
@respx.mock
async def test_async_wrap_adds_skills_to_an_async_forgebench_call():
    from forgebench import AsyncForgebench, AsyncSkillsHook

    respx.get(URL).mock(return_value=httpx.Response(200, json=MANIFEST))
    chat = respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 0, "model": "mock-gpt",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }))
    hook = AsyncSkillsHook(api_key=KEY, base_url=BASE)
    client = hook.wrap(AsyncForgebench(api_key=KEY, base_url=BASE))
    resp = await client.chat.completions.create(model="mock-gpt", messages=[
        {"role": "system", "content": "You are the finance assistant."}, {"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "ok"
    body = json.loads(chat.calls[0].request.content)
    assert [m["role"] for m in body["messages"]] == ["system", "system", "system", "user"]
    assert body["messages"][1]["content"] == "You are the finance assistant."
    assert body["messages"][2]["content"].startswith("# MANDATORY POLICY")
    assert chat.calls[0].request.headers[SKILLS_HEADER] == ",".join(s["sha256"] for s in MANIFEST["skills"])
    assert await hook.headers() == {SKILLS_HEADER: chat.calls[0].request.headers[SKILLS_HEADER]}


@pytest.mark.asyncio
@respx.mock
async def test_async_webhook_forces_a_recheck_and_files_follow(tmp_path):
    from forgebench import AsyncSkillsHook

    route = respx.get(URL).mock(side_effect=[
        httpx.Response(200, json=MANIFEST),
        httpx.Response(200, json={**MANIFEST, "etag": '"v2"', "skills": MANIFEST["skills"][:1]}),
    ])
    hook = AsyncSkillsHook(api_key=KEY, base_url=BASE, refresh_seconds=3600)
    assert len(await hook.write_files(tmp_path)) == 2
    body = b'{"type":"skills.changed","changes":[{"change":"skill.detach"}]}'
    hook.handle_webhook(body, _signed(body, "whsec_a", int(time.time())), "whsec_a")
    assert [p.parent.name for p in await hook.write_files(tmp_path)] == ["acme-pii-policy"]
    assert route.call_count == 2 and sorted(p.name for p in tmp_path.iterdir()) == ["acme-pii-policy"]
