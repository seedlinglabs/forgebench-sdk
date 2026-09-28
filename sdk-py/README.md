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

```bash
pip install forgebench-sdk
# or, from this repo:
pip install -e packages/sdk-py
```

Requires Python 3.9+.

## Get an API key

From the repo root, bring up the stack and seed a demo tenant (this prints a raw
`sk_...` key exactly once):

```bash
make up
make seed     # prints: API key: sk_...
```

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
for chunk in client.chat.completions.create(
    model="mock-gpt",
    messages=[{"role": "user", "content": "Stream me a sentence."}],
    stream=True,
):
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
print()
```

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
| `ServerError`             | 5xx  | Control-plane error (auto-retried)             |
| `ForgebenchConnectionError`   | —    | Network failure reaching the control plane     |

Each `APIError` carries `.status_code`, `.code`, `.message`, `.body`, and
`.request_id`.

## Configuration

`Forgebench(...)` / `AsyncForgebench(...)` accept:

- `api_key` — bearer `sk_...` key (default: `$FORGEBENCH_API_KEY`).
- `base_url` — control-plane URL (default: `$FORGEBENCH_BASE_URL` or `https://api.forgebench.ai`; use `http://localhost:8000` for local dev).
- `timeout` — per-request timeout in seconds (default `60`).
- `max_retries` — bounded retries on `429`/`5xx`/network errors with backoff (default `2`).
- `default_headers` — extra headers merged into every request.
- `http_client` — bring your own `httpx.Client` / `httpx.AsyncClient`.

Client-supplied `api_key` / `api_base` are never manufactured by the SDK; the
chokepoint strips them server-side regardless. Provider credentials live only in
Forgebench's per-tenant encrypted vault.

## Full example

A complete, runnable script lives at
[`examples/quickstart.py`](examples/quickstart.py):

```bash
make up && make seed              # from repo root; prints the sk_... key
export FORGEBENCH_API_KEY=sk_...
python packages/sdk-py/examples/quickstart.py
```

## Development

```bash
pip install -e "packages/sdk-py[dev]"
pytest packages/sdk-py          # offline unit tests (HTTP mocked with respx)
```

## License

Apache-2.0 — permissive, passes the Forgebench dependency license gate. The sole
runtime dependency, `httpx`, is BSD-3-Clause.
