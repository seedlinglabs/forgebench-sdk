# Forgebench Python SDK

Official Python client for the **Forgebench** control plane. A thin,
dependency-light wrapper (only [`httpx`](https://www.python-httpx.org/)) over the
api-key (`sk_...`) surface — Forgebench's permanent programmatic path.

Every call rides the **governed chokepoint**: the request is authenticated,
tenant-isolated via Postgres Row-Level Security, **budget-gated before any
provider call** (an over-budget request returns HTTP `402` *before* tokens are
spent), then metered and written to a per-tenant hash-chained audit log. The SDK
surfaces that 402 as a typed `BudgetExceededError`.

The chat surface is OpenAI-shaped, so existing tooling maps cleanly — but the
default model is `mock-gpt`, which needs **no external API key**, so the whole
thing runs offline against a local stack.

## Install

Not published to PyPI — install from the public SDK repo (always the latest
stable release):

```bash
pip install "git+https://github.com/seedlinglabs/forgebench-sdk.git#subdirectory=sdk-py"
```

Pin an exact version instead with `@py-vX.Y.Z` — see
[Releases](https://github.com/seedlinglabs/forgebench-sdk/releases).

Requires Python 3.9+.

## Get an API key

Sign up at [forgebench.ai](https://forgebench.ai) and create an `sk_...` key
from the console (Settings → API Keys) — shown once at creation time.

## Quick start

```python
from forgebench import Forgebench, BudgetExceededError

# api_key defaults to $FORGEBENCH_API_KEY; base_url to $FORGEBENCH_BASE_URL
# (falling back to the production control plane, https://api.forgebench.ai).
# Pass base_url="http://localhost:8000" to run against a local stack instead.
client = Forgebench(api_key="sk_...")

# Who am I? (verifies auth + tenant)
me = client.whoami()
print(me.tenant_id, me.roles)

# Chat completion through the governed chokepoint.
resp = client.chat.completions.create(
    model="mock-gpt",
    messages=[{"role": "user", "content": "Prove the governed path works."}],
)
print(resp.choices[0].message.content)
print(resp.usage.total_tokens)
```

### Streaming

```python
stream = client.chat.completions.create(
    model="mock-gpt",
    messages=[{"role": "user", "content": "Stream me a sentence."}],
    stream=True,
)
for chunk in stream:
    delta = chunk.choices[0].delta.content if chunk.choices else None
    if delta:
        print(delta, end="", flush=True)
print(stream.trace_id, stream.call_id)  # set once the response starts
```

### Tracing your agent

Mark the steps you want to see and every run becomes one nested trace — the
agent, its functions and tools, and each model call (with tokens and cost)
under the step that made it. Steps are sent in the background with the same
API key; no other keys or setup. The SDK only writes traces — read them in the
console.

```python
from forgebench import Forgebench, observe

fb = Forgebench()

@observe(as_type="tool")
def get_weather(city: str) -> dict:
    return {"city": city, "temp": 18}

@observe(as_type="agent", name="support-agent")
def run(question: str) -> str:
    fb.update_current_trace(user_id="customer-42", session_id="chat-7", tags=["support"])
    with fb.start_as_current_observation("plan", as_type="chain") as plan:
        plan.update(output={"steps": 2})
    weather = get_weather("sf")
    reply = fb.chat.completions.create(model="mock-gpt", messages=[...])  # nests under "support-agent"
    return reply.choices[0].message.content

run("what is the weather in sf?")
fb.flush()  # only in short scripts; long-running apps send in the background
```

```
support-agent  (agent)
├── plan  (chain)
├── get_weather  (tool)      input {"city": "sf"}  output {"city": "sf", "temp": 18}
└── mock-gpt  (generation)   tokens, cost
```

| API | What it does |
|---|---|
| `@observe(name=None, as_type="span", capture_input=True, capture_output=True)` | Records each call of a function (sync or async): arguments, return value, duration. An exception is recorded as an error and re-raised. |
| `fb.start_as_current_observation(name, as_type=..., input=..., metadata=...)` | Context manager for a block of code; returns the `Observation`. |
| `fb.start_observation(...)` / `observation.end()` | A step that is not current, for work that starts and ends in different places. |
| `observation.update(output=..., metadata=..., level=..., status_message=...)` | Change a step before it ends. |
| `fb.update_current_observation(...)` / `fb.update_current_trace(user_id=, session_id=, tags=, metadata=, name=)` | Update the current step / the trace. |
| `fb.get_current_trace_id()` / `fb.get_current_observation_id()` / `fb.create_trace_id(seed=None)` | Ids for logs and linking. |
| `bind_context(fn)` | Keeps the current step when `fn` runs in a thread pool (`asyncio` needs nothing). |
| `fb.flush()` / `fb.close()` | Send now / send and stop. |

`as_type` is one of `span`, `agent`, `tool`, `chain`, `retriever`,
`embedding`, `evaluator`, `guardrail`, `event`; names are yours to choose.
Configure with `Forgebench(tracing=TracingConfig(...))` — `enabled`,
`sample_rate`, `environment`, `release`, `mask` (runs on every input/output
before it leaves), `flush_at`, `flush_interval`, `max_queue_size` — or the
`FORGEBENCH_TRACING_ENABLED`, `FORGEBENCH_SAMPLE_RATE`,
`FORGEBENCH_ENVIRONMENT` and `FORGEBENCH_RELEASE` environment variables.
Tracing never fails your agent: a failed send is logged and dropped. Create one
client and reuse it: each client runs its own background sender.

### Trace attribution

Inside a traced step these are filled in for you. Outside one, set them per call:

```python
resp = client.chat.completions.create(
    model="mock-gpt",
    messages=[...],
    trace_id=forgebench.new_trace_id(),  # group the calls of one turn (1-64 chars)
    user="customer-42",                   # the trace's user (your end user)
    session_id="checkout-7",              # groups traces into a session
    tags=["checkout"],                    # extra trace tags
    dimensions={"region": "eu"},          # key:value filter dimensions
    metadata={"experiment": "b"},
)
```

None of these reach the model provider. The server also honours a W3C
`traceparent` header when no `trace_id` is sent.

### Call lineage (who called what)

Every governed response carries a `call_id` — this call's row on the control
plane's ledger. Pass it as `parent_call_id` on the calls it *causes* and the
ledger records them as children: cost rolls up to the root, and the console
draws the run as a tree instead of a flat set of rows sharing a trace.
`trace_id` groups; `parent_call_id` structures.

```python
plan = client.chat.completions.create(model="mock-gpt", messages=[...])

# A follow-up turn caused by `plan` (a sub-agent's turn, the next step of a
# tool loop) — recorded as its child, inheriting its root.
step = client.chat.completions.create(
    model="mock-gpt",
    messages=[...],
    parent_call_id=plan.call_id,
)
```

The header is correlation only — it never affects whether a call is allowed or
what it costs, and an unknown or malformed value simply starts a new root. On
the streaming path the id rides the `X-Call-Id` response header instead.

### Tools, through the door

The control plane gates a model's `tool_calls` against this agent's allowlist;
your own MCP client executes them. Build the model's tool list from the live
allowlist, run what the gate let through, and report each outcome onto the
decision row it was gated on:

```python
tools = client.agent_tools.openai_schema()           # from GET /v1/agent-tools
turn = client.chat.completions.create(model="mock-gpt", messages=msgs,
                                      extra_body={"tools": tools})
msgs += client.agent_tools.dispatch(
    turn.choices[0].message.tool_calls,
    execute=lambda name, args: my_mcp.call(name, args),
    call_id=turn.call_id,
)
```

### Agent → agent (A2A tasks)

An agent bound to another by an operator can open a task on it through the
door — the callee runs wherever it runs, and its own calls hang under the task:

```python
task = client.agents.call("qp-research", text="10 MCQs on photosynthesis",
                          data={"grade": 9}, parent_call_id=plan.call_id)
if task.state == "input_required":
    task = client.agents.call("qp-research", text="CBSE", context_id=task.context_id)
notes = task.artifact_text()
```

And to BE a callee with no inbound port (pull delivery):

```python
def handle(task):
    answer = client.chat.completions.create(model="mock-gpt",
        messages=[{"role": "user", "content": task.text}],
        parent_call_id=task.call_id)              # nests under the task
    return task.done(text=answer.choices[0].message.content)

client.agents.serve(handle)   # blocks; long-polls the door for this agent's tasks
```

### Agents

```python
agents = client.agents.list()
for a in agents:
    print(a.id, a.name, a.model)

# Create an agent.
agent = client.agents.create(
    name="Support bot",
    model="mock-gpt",
    system_prompt="You are a helpful support agent.",
)

# Run an agent. Agents execute through the runs subsystem, which routes back
# through the same governed chokepoint (budget re-gated per hop, metered,
# audited). wait=True blocks until the run reaches a terminal state.
run = client.agents.run(agent.id, input={"messages": [
    {"role": "user", "content": "Help me reset my password."},
]}, wait=True, timeout=30)
print(run.status, run.output)
```

### Runs

```python
run = client.runs.create(model="mock-gpt", input={"prompt": "hello"})
run = client.runs.get(run.id)
run = client.runs.wait(run.id, timeout=30)   # poll until succeeded/failed
print(run.status)
```

### The budget gate (HTTP 402)

The seed sets a deliberately low monthly limit so the pre-call gate trips after
a few calls — proving the budget is enforced *before* the provider is touched:

```python
from forgebench import BudgetExceededError

try:
    client.chat.completions.create(
        model="mock-gpt",
        messages=[{"role": "user", "content": "again"}],
    )
except BudgetExceededError as e:
    print("Budget gate fired before any provider call:", e.message, e.status_code)  # 402
```

### Metering & budget state

```python
s = client.metering_summary()
print(f"spent ${s.spent_usd} / limit ${s.monthly_limit_usd} "
      f"(remaining ${s.remaining_usd})")
print(s.by_model)
```

### Voice (1.3.0)

```python
from forgebench import Forgebench, voice_headers, StreamError

client = Forgebench()
# Speech to text / text to speech (governed; session groups the Voice Conversation)
t = client.audio.transcriptions.create(open("clip.wav", "rb"), "gpt-4o-transcribe", session="conv-1")
s = client.audio.speech.create("tts-1", "Hello", "alloy", session="conv-1")   # s.audio, s.trace_id

# Tie your own pipeline's calls to one conversation (X-Forgebench-Session / -Turn / traceparent)
headers = voice_headers("conv-1", turn=2)

# Streaming agent run: dict events in server order
try:
    for event in client.agents.run(agent_id, message="hi", stream=True, session="conv-1"):
        ...
except StreamError as e:   # e.code, e.message
    ...

# Voice admin (policy needs admin to write; `update(**get())` round-trips)
policy = client.voice.policy.get()
client.voice.policy.update(**{**policy, "voice_trace_content": False})
client.voice.assets.update(asset_id, expires_at=None)   # explicit None clears a field
```

- **Errors:** pre-stream failures raise the usual typed errors (402
  `BudgetExceededError`, 403, 422 `ValidationError` for refusals such as
  `disclosure_missing`). Once a stream is open, an SSE `event: error` frame, or a
  stream that ends without `agent.run.completed` / `[DONE]`, raises `StreamError`
  (`code="stream_truncated"` for the latter) instead of ending silently.
- **Trace headers:** `TRACE_ID_HEADER` (`X-Trace-Id`) is the chat response header;
  `AUDIO_TRACE_ID_HEADER` (`X-Forgebench-Trace-Id`) is the `/v1/audio/*` one.
- **`voice_trace_content`** (policy, default off) gates whether gateway
  transcripts/TTS text AND your telemetry content are recorded on traces.

## Async

```python
import asyncio
from forgebench import AsyncForgebench

async def main():
    async with AsyncForgebench(api_key="sk_...") as client:
        resp = await client.chat.completions.create(
            model="mock-gpt",
            messages=[{"role": "user", "content": "hello"}],
        )
        print(resp.choices[0].message.content)

        stream = await client.chat.completions.create(
            model="mock-gpt",
            messages=[{"role": "user", "content": "stream"}],
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="")

asyncio.run(main())
```

## Errors

All errors derive from `forgebench.ForgebenchError`:

| Exception                 | HTTP | Meaning                                        |
| ------------------------- | ---- | ---------------------------------------------- |
| `AuthenticationError`     | 401  | Missing / invalid / revoked API key            |
| `BudgetExceededError`     | 402  | Pre-call budget gate fired (no tokens spent)   |
| `PermissionDeniedError`   | 403  | Authenticated but lacking role/scope           |
| `NotFoundError`           | 404  | Resource not found / not visible to tenant     |
| `ConflictError`           | 409  | Conflicting state                              |
| `ValidationError`         | 422  | Request body rejected by the control plane     |
| `RateLimitError`          | 429  | Too many requests (auto-retried up to `max_retries`) |
| `ServerError`             | 5xx  | Control-plane error (reads auto-retried; writes never) |
| `ForgebenchConnectionError`   | —    | Network failure reaching the control plane     |

Each `APIError` carries `.status_code`, `.code`, `.message`, `.body`, and
`.request_id`.

## Configuration

`Forgebench(...)` / `AsyncForgebench(...)` accept:

- `api_key` — bearer `sk_...` key (default: `$FORGEBENCH_API_KEY`).
- `base_url` — control-plane URL (default: `$FORGEBENCH_BASE_URL` or `https://api.forgebench.ai`; use `http://localhost:8000` for local dev).
- `timeout` — per-request timeout in seconds (default `60`).
- `max_retries` — bounded retries with backoff (default `2`). Reads retry on
  `429`/`5xx`/network errors. Writes (`POST`/`PATCH`, incl. chat and streams)
  retry only on `429` or a failed connect — never on a `5xx` or a dropped
  connection, since the call may already have run (and been billed); the
  control plane has no idempotency key. **Behaviour change in 1.1.0:** writes
  were previously also retried on `5xx`/connection loss.
- `default_headers` — extra headers merged into every request.
- `http_client` — bring your own `httpx.Client` / `httpx.AsyncClient`.

Client-supplied `api_key` / `api_base` are never manufactured by the SDK; the
chokepoint strips them server-side regardless. Provider credentials live only in
Forgebench's per-tenant encrypted vault.

## Full example

A complete, runnable script lives at
[`examples/quickstart.py`](examples/quickstart.py). From within this directory:

```bash
export FORGEBENCH_API_KEY=sk_...
python examples/quickstart.py
```

## Development

From within this directory:

```bash
pip install -e ".[dev]"
pytest          # offline unit tests (HTTP mocked with respx)
```

## License

Apache-2.0 — permissive, passes the Forgebench dependency license gate. The sole
runtime dependency, `httpx`, is BSD-3-Clause.
