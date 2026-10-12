"""SDK tracing: observe / observations, nesting, the background sender, and
model calls nesting under the step they were made from."""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import respx

from forgebench import AsyncForgebench, Forgebench, TracingConfig, bind_context, observe
from forgebench.tracing import _tracer as tracer_mod
from forgebench.tracing._exporter import BatchExporter
from forgebench.tracing._serialize import MAX_FIELD_CHARS, TRUNCATED

BASE = "http://testserver"
KEY = "sk_test_abc123"
CHAT = f"{BASE}/v1/chat/completions"
BATCH = f"{BASE}/v1/observations/batch"
COMPLETION = {
    "id": "c1",
    "created": 1,
    "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.fixture(autouse=True)
def no_default_tracer(monkeypatch):
    monkeypatch.setattr(tracer_mod, "_live", {})


@pytest.fixture
def sent():
    """A tracer whose batches land in a list instead of the network."""
    batches = []

    def make(**config):
        tracer = tracer_mod.Tracer(TracingConfig(**config), lambda payload: batches.append(payload) or {})
        tracer_mod.register_default_tracer(tracer, "test-credential")
        return tracer

    return make, batches


def observations(batches):
    return [o for b in batches for o in b["observations"]]


def traces(batches):
    return [t for b in batches for t in b["traces"]]


# --- recording ----------------------------------------------------------------


def test_observe_nests_steps_and_captures_arguments_and_result(sent):
    make, batches = sent
    tracer = make(environment="staging", release="2.3.0")

    @observe(as_type="tool")
    def get_weather(city, unit="c"):
        return {"city": city, "temp": 18}

    @observe(as_type="agent", name="support-agent")
    def run(question):
        return get_weather("sf")

    assert run("weather?") == {"city": "sf", "temp": 18}
    assert tracer.flush()

    tool, agent = observations(batches)
    assert (agent["name"], agent["type"], agent["parent_observation_id"]) == ("support-agent", "agent", None)
    assert (tool["name"], tool["type"], tool["parent_observation_id"]) == ("get_weather", "tool", agent["id"])
    assert tool["trace_id"] == agent["trace_id"] and len(agent["id"]) == 16
    assert tool["input"] == {"city": "sf"} and tool["output"] == {"city": "sf", "temp": 18}
    assert agent["input"] == {"question": "weather?"}
    assert agent["start_time"] <= tool["start_time"] <= tool["end_time"] <= agent["end_time"]
    (trace,) = traces(batches)
    assert trace == {"id": agent["trace_id"], "name": "support-agent", "environment": "staging", "release": "2.3.0"}


def test_exception_is_recorded_as_error_and_reraised(sent):
    make, batches = sent
    tracer = make()

    @observe
    def boom():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError, match="down"):
        boom()
    tracer.flush()
    (obs,) = observations(batches)
    assert (obs["level"], obs["status_message"]) == ("ERROR", "RuntimeError: down")


def test_methods_drop_self_and_capture_can_be_turned_off(sent):
    make, batches = sent
    tracer = make()

    class Bot:
        @observe(capture_input=False)
        def secret(self, password):
            return "ok"

        @observe(capture_output=False)
        def lookup(self, key):
            return "big"

    Bot().secret("hunter2")
    Bot().lookup("k")
    tracer.flush()
    secret, lookup = observations(batches)
    assert secret["input"] is None and secret["output"] == "ok"
    assert lookup["input"] == {"key": "k"} and lookup["output"] is None


async def test_async_steps_and_tasks_inherit_the_current_step(sent):
    make, batches = sent
    tracer = make()

    @observe(as_type="tool")
    async def fetch(n):
        await asyncio.sleep(0)
        return n

    @observe(as_type="agent")
    async def run():
        return await asyncio.gather(fetch(1), fetch(2))

    assert await run() == [1, 2]
    tracer.flush()
    *tools, agent = observations(batches)
    assert {t["parent_observation_id"] for t in tools} == {agent["id"]}


def test_blocks_trace_fields_and_manual_steps(sent):
    make, batches = sent
    tracer = make()
    with tracer.start_as_current_observation("turn", as_type="chain", input={"q": 1}) as turn:
        tracer.update_current_trace(user_id="u_42", session_id="chat_7", tags=["support"], metadata={"plan": "pro"})
        tracer.update_current_trace(tags=["support", "vip"])
        turn.update(output="done", metadata={"step": 1})
        manual = tracer.start_observation("background-job")
        child = manual.start_observation("sub-step", as_type="retriever")
        child.end()
        manual.end()
        manual.end()  # a second end is ignored
    tracer.flush()
    child_obs, manual_obs, turn_obs = observations(batches)
    assert manual_obs["parent_observation_id"] == turn_obs["id"]  # parented, though never current
    assert child_obs["parent_observation_id"] == manual_obs["id"]
    assert (turn_obs["input"], turn_obs["output"], turn_obs["metadata"]) == ({"q": 1}, "done", {"step": 1})
    (trace,) = traces(batches)
    assert trace["user_id"] == "u_42" and trace["session_id"] == "chat_7"
    assert trace["tags"] == ["support", "vip"] and trace["metadata"] == {"plan": "pro"}


def test_trace_id_starts_a_root_in_a_given_trace(sent):
    make, batches = sent
    tracer = make()
    trace_id = tracer.create_trace_id("request-123")
    assert trace_id == tracer.create_trace_id("request-123") and len(trace_id) == 32
    with tracer.start_as_current_observation("resume", trace_id=trace_id):
        pass
    tracer.flush()
    assert observations(batches)[0]["trace_id"] == trace_id


def test_update_outside_any_step_warns_once_and_does_nothing(sent, caplog):
    make, batches = sent
    tracer = make()
    with caplog.at_level(logging.WARNING, logger="forgebench.tracing"):
        tracer.update_current_trace(user_id="u")
        tracer.update_current_trace(user_id="u")
    assert caplog.text.count("outside any observation") == 1


def test_unknown_type_and_generators_are_refused(sent):
    make, _ = sent
    tracer = make()
    with pytest.raises(ValueError):
        tracer.start_observation("x", as_type="generation")
    with pytest.raises(TypeError):

        @observe
        def gen():
            yield 1


# --- what is sent -------------------------------------------------------------


@pytest.mark.parametrize("config", [{"enabled": False}, {"sample_rate": 0.0}])
def test_disabled_or_sampled_out_sends_nothing(sent, config):
    make, batches = sent
    tracer = make(**config)

    @observe
    def step():
        return 1

    assert step() == 1
    tracer.flush()
    assert batches == []


def test_mask_runs_before_anything_leaves_and_a_failing_mask_withholds(sent):
    make, batches = sent

    def mask(data):
        if data == "explode":
            raise ValueError("bad mask")
        return json.loads(json.dumps(data).replace("hunter2", "***"))

    tracer = make(mask=mask)
    with tracer.start_as_current_observation("login", input={"password": "hunter2"}) as obs:
        obs.update(output="explode")
    tracer.flush()
    (obs,) = observations(batches)
    assert obs["input"] == {"password": "***"} and obs["output"] == "[withheld: mask failed]"


def test_unserialisable_and_huge_values_are_made_safe(sent):
    make, batches = sent
    tracer = make()

    class Opaque:
        def __repr__(self):
            return "<Opaque>"

    with tracer.start_as_current_observation("s", input={"obj": Opaque(), "nan": float("nan")}) as obs:
        obs.update(output="x" * (MAX_FIELD_CHARS + 5))
    tracer.flush()
    (obs,) = observations(batches)
    assert obs["input"] == {"obj": "<Opaque>", "nan": "nan"}
    assert obs["output"].endswith(TRUNCATED)
    json.dumps(obs)


def test_server_rejections_are_logged_once_per_reason(caplog):
    reply = {"accepted": 0, "rejected": [{"id": "a", "reason": "trace_not_owned"}]}
    tracer = tracer_mod.Tracer(TracingConfig(), lambda payload: reply)
    with caplog.at_level(logging.WARNING, logger="forgebench.tracing"):
        for _ in range(2):
            with tracer.start_as_current_observation("s"):
                pass
            tracer.flush()
    assert caplog.text.count("trace_not_owned") == 1


def test_a_failing_send_never_reaches_the_agent(caplog):
    def down(payload):
        raise ConnectionError("control plane down")

    tracer = tracer_mod.Tracer(TracingConfig(), down)
    with caplog.at_level(logging.WARNING, logger="forgebench.tracing"):
        with tracer.start_as_current_observation("s"):
            pass
        assert tracer.flush()
    assert "control plane down" in caplog.text


def test_queue_overflow_drops_the_oldest_with_one_warning(caplog):
    exporter = BatchExporter(lambda o, t: None, flush_at=100, flush_interval=60, max_queue_size=2)
    with caplog.at_level(logging.WARNING, logger="forgebench.tracing"):
        for i in range(4):
            exporter.add({"id": str(i)})
    assert exporter.dropped == 2 and [o["id"] for o in exporter._queue] == ["2", "3"]
    assert caplog.text.count("queue full") == 1
    exporter.shutdown(timeout=1)


def test_batches_never_exceed_the_server_limit():
    sizes = []
    exporter = BatchExporter(lambda o, t: sizes.append(len(o)), flush_at=500, flush_interval=60, max_queue_size=1000)
    for i in range(250):
        exporter.add({"id": str(i)})
    assert exporter.flush(timeout=5)
    assert sizes == [100, 100, 50]


def test_a_trace_record_travels_with_steps_of_its_own_trace():
    batches = []
    exporter = BatchExporter(lambda o, t: batches.append((o, t)), flush_at=500, flush_interval=60, max_queue_size=1000)
    for i in range(150):
        exporter.add({"id": str(i), "trace_id": "early"})
    exporter.add({"id": "root", "trace_id": "late"}, {"id": "late", "name": "run"})
    assert exporter.flush(timeout=5)
    (first_obs, first_traces), (second_obs, second_traces) = batches
    assert first_traces == [] and {o["trace_id"] for o in first_obs} == {"early"}
    assert second_traces == [{"id": "late", "name": "run"}] and second_obs[-1]["id"] == "root"


def test_bind_context_carries_the_step_into_a_thread_pool(sent):
    make, batches = sent
    tracer = make()

    @observe(as_type="tool")
    def work(n):
        return n

    with tracer.start_as_current_observation("fan-out") as parent:
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(bind_context(work), [1, 2])) == [1, 2]
    tracer.flush()
    *workers, root = observations(batches)
    assert [w["parent_observation_id"] for w in workers] == [parent.id, parent.id]


def test_observe_without_any_client_just_calls_the_function():
    @observe
    def plain(x):
        return x * 2

    assert plain(2) == 4


# --- end to end through the client ------------------------------------------------


@respx.mock
def test_model_call_inside_a_step_nests_under_it_and_steps_are_posted():
    chat = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))
    batch = respx.post(BATCH).mock(return_value=httpx.Response(202, json={"accepted": 1, "rejected": []}))
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as fb:

        @fb.observe(as_type="agent")
        def run():
            fb.update_current_trace(user_id="u_42", session_id="chat_7")
            fb.chat.completions.create(messages=[{"role": "user", "content": "x"}])
            return fb.get_current_observation_id()

        step_id = run()
        assert fb.flush()

    sent_call = chat.calls.last.request
    body = json.loads(sent_call.content)
    assert sent_call.headers["X-Parent-Observation"] == step_id
    assert (body["user"], body["session_id"]) == ("u_42", "chat_7")
    posted = json.loads(batch.calls.last.request.content)
    assert body["trace_id"] == posted["observations"][0]["trace_id"]
    assert batch.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"
    assert posted["sdk"]["name"] == "forgebench-python"


@respx.mock
def test_model_call_outside_a_step_or_in_another_trace_is_not_nested():
    chat = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))
    respx.post(BATCH).mock(return_value=httpx.Response(202, json={"accepted": 1}))
    with Forgebench(api_key=KEY, base_url=BASE, max_retries=0) as fb:
        fb.chat.completions.create(messages=[{"role": "user", "content": "x"}])
        assert "X-Parent-Observation" not in chat.calls.last.request.headers
        with fb.start_as_current_observation("s"):
            fb.chat.completions.create(messages=[{"role": "user", "content": "x"}], trace_id="other-trace")
        assert "X-Parent-Observation" not in chat.calls.last.request.headers
        assert json.loads(chat.calls.last.request.content)["trace_id"] == "other-trace"


@respx.mock
async def test_async_client_nests_and_flushes():
    chat = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))
    batch = respx.post(BATCH).mock(return_value=httpx.Response(202, json={"accepted": 1}))
    async with AsyncForgebench(api_key=KEY, base_url=BASE, max_retries=0) as fb:
        with fb.start_as_current_observation("agent", as_type="agent") as step:
            await fb.chat.completions.create(messages=[{"role": "user", "content": "x"}])
        assert await fb.flush()
    assert chat.calls.last.request.headers["X-Parent-Observation"] == step.id
    assert json.loads(batch.calls.last.request.content)["observations"][0]["id"] == step.id


# --- review fixes ------------------------------------------------------------


def test_a_bad_sample_rate_in_the_environment_never_breaks_the_client(monkeypatch, caplog):
    for raw in ("abc", "5", ""):
        monkeypatch.setenv("FORGEBENCH_SAMPLE_RATE", raw)
        with caplog.at_level(logging.WARNING, logger="forgebench.tracing"):
            assert TracingConfig.from_env().sample_rate == 1.0
    assert "FORGEBENCH_SAMPLE_RATE='abc'" in caplog.text
    monkeypatch.setenv("FORGEBENCH_SAMPLE_RATE", "0.25")
    assert TracingConfig.from_env().sample_rate == 0.25


@respx.mock
def test_two_credentials_make_module_level_observe_refuse_to_guess(caplog):
    batch = respx.post(BATCH).mock(return_value=httpx.Response(202, json={"accepted": 1}))
    tenant_a = Forgebench(api_key="sk_tenant_a", base_url=BASE, max_retries=0)
    tenant_b = Forgebench(api_key="sk_tenant_b", base_url=BASE, max_retries=0)

    @observe
    def unbound():
        return "ran"

    with caplog.at_level(logging.WARNING, logger="forgebench.tracing"):
        assert unbound() == "ran"  # still runs, just untraced
    assert "use client.observe" in caplog.text

    bound = tenant_a.observe(lambda: "ran", name="bound")
    assert bound() == "ran"
    tenant_a.flush()
    assert [o["name"] for o in json.loads(batch.calls.last.request.content)["observations"]] == ["bound"]
    assert batch.calls.last.request.headers["Authorization"] == "Bearer sk_tenant_a"
    tenant_a.close()
    tenant_b.close()


@respx.mock
def test_another_clients_step_never_lends_its_trace_or_user():
    chat = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))
    batch = respx.post(BATCH).mock(return_value=httpx.Response(202, json={"accepted": 1}))
    tenant_a = Forgebench(api_key="sk_tenant_a", base_url=BASE, max_retries=0)
    tenant_b = Forgebench(api_key="sk_tenant_b", base_url=BASE, max_retries=0)
    with tenant_a.start_as_current_observation("a-turn"):
        tenant_a.update_current_trace(user_id="alice@tenant-a")
        tenant_b.chat.completions.create(messages=[{"role": "user", "content": "x"}])
        with tenant_b.start_as_current_observation("b-step"):
            pass
    tenant_a.flush()
    tenant_b.flush()
    call = chat.calls.last.request
    body = json.loads(call.content)
    assert "X-Parent-Observation" not in call.headers and "user" not in body and "trace_id" not in body
    posted = {r.headers["Authorization"]: json.loads(r.content)["observations"] for r, _ in batch.calls}
    (b_step,) = posted["Bearer sk_tenant_b"]
    (a_turn,) = posted["Bearer sk_tenant_a"]
    assert b_step["parent_observation_id"] is None and b_step["trace_id"] != a_turn["trace_id"]
    tenant_a.close()
    tenant_b.close()


def test_error_messages_go_through_the_mask(sent):
    make, batches = sent
    tracer = make(mask=lambda data: data.replace("4111-1111", "****") if isinstance(data, str) else data)

    @observe
    def charge():
        raise ValueError("card 4111-1111 declined")

    with pytest.raises(ValueError):
        charge()
    tracer.flush()
    assert observations(batches)[0]["status_message"] == "ValueError: card **** declined"


def test_huge_values_are_cut_while_converting(sent):
    make, batches = sent
    tracer = make()
    with tracer.start_as_current_observation("s") as obs:
        obs.update(input=list(range(1_000_000)), output={"text": "y" * 10_000_000})
    tracer.flush()
    (obs,) = observations(batches)
    assert obs["input"][-1] == "<truncated>" and len(obs["input"]) <= 10_001  # stopped walking
    assert obs["output"].endswith(TRUNCATED)


# --- review round 2 (PR #603) --------------------------------------------------


def test_update_current_trace_never_raises_when_the_mask_returns_a_string(sent):
    make, batches = sent
    tracer = make(mask=lambda data: "masked")
    with tracer.start_as_current_observation("s"):
        tracer.update_current_trace(metadata={"customer": "acme"}, user_id="u1")
    tracer.flush()
    (trace,) = traces(batches)
    assert trace["metadata"] == {"value": "masked"} and trace["user_id"] == "u1"


def test_update_current_trace_with_oversized_metadata_keeps_a_mapping(sent):
    make, batches = sent
    tracer = make()
    with tracer.start_as_current_observation("s"):
        tracer.update_current_trace(metadata={"blob": "x" * (MAX_FIELD_CHARS + 10)})
    tracer.flush()
    (trace,) = traces(batches)
    assert isinstance(trace["metadata"], dict) and trace["metadata"]["value"].endswith(TRUNCATED)


@respx.mock
def test_closing_a_client_lets_the_next_credential_become_the_default():
    batch = respx.post(BATCH).mock(return_value=httpx.Response(202, json={"accepted": 1}))
    Forgebench(api_key="sk_tenant_a", base_url=BASE, max_retries=0).close()
    b = Forgebench(api_key="sk_tenant_b", base_url=BASE, max_retries=0)

    @observe(name="after-close")
    def unbound():
        return "ran"

    assert unbound() == "ran"
    b.flush()
    assert batch.calls.last.request.headers["Authorization"] == "Bearer sk_tenant_b"
    assert json.loads(batch.calls.last.request.content)["observations"][0]["name"] == "after-close"
    b.close()


def test_closing_one_of_two_same_key_clients_keeps_the_other_as_default():
    c1 = Forgebench(api_key="sk_same", base_url=BASE, max_retries=0)
    c2 = Forgebench(api_key="sk_same", base_url=BASE, max_retries=0)
    c2.close()
    assert tracer_mod.resolve_tracer() is c1._tracer
    c1.close()
    assert tracer_mod.resolve_tracer() is None


@respx.mock
def test_a_trace_batch_is_retried_on_a_5xx_because_resending_it_is_harmless(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    batch = respx.post(BATCH).mock(side_effect=[httpx.Response(502), httpx.Response(202, json={"accepted": 1})])
    chat = respx.post(CHAT).mock(return_value=httpx.Response(502))
    fb = Forgebench(api_key=KEY, base_url=BASE)  # default max_retries=2
    with fb.start_as_current_observation("s"):
        with pytest.raises(Exception):
            fb.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    assert fb.flush()
    assert chat.call_count == 1  # a chat call is still never replayed after a 5xx
    assert batch.call_count == 2  # the trace batch is
    fb.close()
