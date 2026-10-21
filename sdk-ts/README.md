# @seedlinglabs/forgebench-sdk

Official **TypeScript SDK** for the [Forgebench](../../README.md) enterprise AI control plane.

Typed, runtime-agnostic client over the governed chokepoint. Every chat call your
code makes flows through the server's single tenant transaction — **budget pre-gate
→ per-tenant provider key injection → gateway call → one metering event + one
hash-chained audit row**. The SDK never sends provider credentials (`api_key` /
`api_base`); they are injected server-side from the vault and stripped from any
client-supplied request.

- **Model-agnostic.** Defaults to the offline `mock-gpt` model (no keys required).
- **Tenant isolation is server-side.** You authenticate with one credential; the
  control plane resolves it to a tenant-scoped Principal enforced by Postgres RLS.
  The SDK never handles tenant ids.
- **No runtime dependencies.** Built on the platform `fetch`. Runs on Node ≥18,
  Deno, browsers, and edge runtimes.

## Install

Not published to npm. Every release on the public SDK repo carries a
prebuilt package, which installs with npm, pnpm or yarn (always the latest
stable release):

```bash
npm install https://github.com/seedlinglabs/forgebench-sdk/releases/latest/download/forgebench-sdk-ts.tgz
```

To pin an exact version, use
`releases/download/ts-vX.Y.Z/forgebench-sdk-ts.tgz` instead (previews end in
`-preview`) — see [Releases](https://github.com/seedlinglabs/forgebench-sdk/releases).
Installing from git (`github:…#path:/sdk-ts`) does not work: npm cannot
install a subfolder of a repository, and pnpm 10+ refuses to build a git
dependency.

## Workflows

A workflow is a named group of agents (steps) an app runs in order. Register it once, then drive it with
one key per step:

```ts
const me = await forgebench.whoami();
const created = await forgebench.workflows.register({
  name: "Question paper",
  owner_identity_id: me.identity_id,
  steps: [{ name: "grading", model: "mock-gpt" }, { name: "retry", model: "mock-gpt" }],
  edges: [{ from: "grading", to: "retry", kind: "sequence" }],
});
const keys = Object.fromEntries(created.credentials.map((c) => [c.step, c.credential!.api_key])); // shown once

const run = forgebench.workflow("question-paper", keys).run();
await run.step("grading").chat({ model: "mock-gpt", messages });  // uses grading's key
await run.step("retry").chat({ model: "mock-gpt", messages });    // parentCallId = grading's call_id
console.log(run.rootCallId);

await forgebench.workflows.checks("question-paper");              // wired? threaded? seeing traffic?
```

Each step call uses that step's key and names the previous call of the run as its parent (`X-Parent-Call`);
the first call is a root. A streamed step call (`step.stream`) becomes the next call's parent once its
`callId` is known, so iterate it to the end first. Also `workflows.list/get/update/archive/addStep/removeStep/addEdge/removeEdge`,
`workflows.suggestions()` (groups of existing agents that look like a workflow, from scanned code and observed calls),
`workflows.acceptSuggestion(fingerprint, { name, edges })` (existing agents become steps; no key is created), `workflows.dismissSuggestion(fingerprint)`
and `forgebench.agents.register(...)` (`issue_credential: false` registers sealed).

## Authentication

Use **either**:

- `apiKey` — an `sk_...` secret (programmatic / server-side). Created via
  `forgebench.keys.create(...)`, or from the [forgebench.ai](https://forgebench.ai)
  console (Settings → API Keys).
- `token` — a control-plane **session JWT** minted by the OIDC login callback
  (human / console use).

Both map to `Authorization: Bearer <credential>` and resolve to the same Principal.

```ts
import { Forgebench } from "@seedlinglabs/forgebench-sdk";

// baseUrl defaults to https://api.forgebench.ai; pass "http://localhost:8000" for local dev.
const forgebench = new Forgebench({
  apiKey: process.env.FORGEBENCH_API_KEY!, // sk_... from the console or `keys.create(...)`
});
```

## Chat (the governed chokepoint)

```ts
const res = await forgebench.chat.create({
  model: "mock-gpt", // omit to use the server default
  messages: [{ role: "user", content: "Hello, Forgebench." }],
});

console.log(res.choices[0]?.message.content);
console.log("tokens:", res.usage.total_tokens);
```

### Streaming

```ts
for await (const chunk of forgebench.chat.stream({
  messages: [{ role: "user", content: "Stream me a reply." }],
})) {
  process.stdout.write(chunk.choices[0]?.delta.content ?? "");
}

// …or accumulate to a single string:
const text = await forgebench.chat.streamToText(
  { messages: [{ role: "user", content: "hi" }] },
  { onToken: (t) => process.stdout.write(t) },
);
```

The SDK parses the SSE stream and consumes the terminal `[DONE]` sentinel for you.
`chat.stream()` also carries `traceId` / `callId` (set once the response starts):

```ts
const stream = forgebench.chat.stream({ messages });
for await (const chunk of stream) process.stdout.write(chunk.choices[0]?.delta.content ?? "");
console.log(stream.traceId, stream.callId);
```

### Tracing your agent

Mark the steps you want to see and every run becomes one nested trace — the
agent, its functions and tools, and each model call (with tokens and cost)
under the step that made it. Steps are sent in the background with the same
API key; no other keys or setup. The SDK only writes traces — read them in the
console.

```ts
import { Forgebench, observe, updateActiveTrace } from "@seedlinglabs/forgebench-sdk";

const forgebench = new Forgebench({ apiKey: process.env.FORGEBENCH_API_KEY! });

const getWeather = observe(async (city: string) => ({ city, temp: 18 }), { name: "get_weather", asType: "tool" });

const run = observe(async (question: string) => {
  updateActiveTrace({ userId: "customer-42", sessionId: "chat-7", tags: ["support"] });
  await forgebench.startActiveObservation("plan", (plan) => plan.update({ output: { steps: 2 } }), { asType: "chain" });
  const weather = await getWeather("sf");
  const reply = await forgebench.chat.create({ messages: [{ role: "user", content: question }] }); // nested
  return reply.choices[0]?.message.content;
}, { name: "support-agent", asType: "agent" });

await run("what is the weather in sf?");
await forgebench.flush(); // only in short scripts / serverless; long-running apps send in the background
```

| API | What it does |
|---|---|
| `observe(fn, { name, asType, captureInput, captureOutput })` | Wraps a function (sync or async): arguments, result, duration. An error is recorded and re-thrown. |
| `startActiveObservation(name, fn, { asType, input, metadata })` | Runs `fn(observation)` as the active step; returns what `fn` returns. |
| `startObservation(name, opts)` / `observation.end()` | A step that is not active, for work that starts and ends in different places. |
| `observation.update({ output, metadata, level, statusMessage })` | Change a step before it ends. |
| `updateActiveObservation({...})` / `updateActiveTrace({ userId, sessionId, tags, metadata, name })` | Update the active step / the trace. |
| `getActiveTraceId()` / `getActiveObservationId()` / `await createTraceId(seed?)` | Ids for logs and linking. |
| `forgebench.flush()` / `forgebench.shutdown()` | Send now / send and stop. |

The module-level functions record with the most recently created client;
`forgebench.observe(...)` etc. pin one. `asType` is one of `span`, `agent`,
`tool`, `chain`, `retriever`, `embedding`, `evaluator`, `guardrail`, `event`;
names are yours to choose. Configure with `new Forgebench({ tracing: { enabled,
sampleRate, environment, release, mask, flushAt, flushIntervalMs, maxQueueSize } })`
or the `FORGEBENCH_TRACING_ENABLED`, `FORGEBENCH_SAMPLE_RATE`,
`FORGEBENCH_ENVIRONMENT` and `FORGEBENCH_RELEASE` environment variables.
Concurrent async steps keep their own parents on Node ≥20.16 / 22 and Bun
(`AsyncLocalStorage`); other runtimes nest sequential code correctly. Tracing
never fails your agent: a failed send is logged and dropped. Create one client
and reuse it.

### Trace attribution

Inside a traced step these are filled in for you. Outside one, set them per call:

```ts
const res = await forgebench.chat.create({
  messages,
  trace_id: newTraceId(),     // group the calls of one turn (1-64 chars)
  user: "customer-42",        // the trace's user (your end user)
  session_id: "checkout-7",   // groups traces into a session
  tags: ["checkout"],         // extra trace tags
  dimensions: { region: "eu" },
});
```

None of these reach the model provider. The server also honours a W3C
`traceparent` header when no `trace_id` is sent.

### Call lineage (who called what)

Every governed response carries a `call_id` — this call's row on the control
plane's ledger. Pass it as `parentCallId` on the calls it *causes* and the
ledger records them as children: cost rolls up to the root, and the console
draws the run as a tree instead of a flat set of rows sharing a trace.
`trace_id` groups; `parentCallId` structures.

```ts
const plan = await forgebench.chat.create({ messages: [...] });

// A follow-up turn caused by `plan` (a sub-agent's turn, the next step of a
// tool loop) — recorded as its child, inheriting its root.
const step = await forgebench.chat.create(
  { messages: [...] },
  { parentCallId: plan.call_id },
);
```

The header is correlation only — it never affects whether a call is allowed or
what it costs, and an unknown or malformed value simply starts a new root. On
the streaming path the id rides the `X-Call-Id` response header instead.

### Tools, through the door

```ts
const tools = await forgebench.agentTools.openaiSchema();
const turn = await forgebench.chat.create({ model: "mock-gpt", messages, tools });
messages.push(
  ...(await forgebench.agentTools.dispatch(
    turn.choices[0].message.tool_calls,
    (name, args) => myMcp.call(name, args),
    { callId: turn.call_id! },
  )),
);
```

### Agent → agent (A2A tasks)

```ts
const task = await forgebench.agents.call("qp-research", {
  text: "10 MCQs on photosynthesis",
  data: { grade: 9 },
  parentCallId: plan.call_id,
});
const notes = artifactText(task);

// Be a callee with no inbound port (pull delivery):
await forgebench.agents.serve(async (task, reply) => {
  const turn = await forgebench.chat.create(
    { model: "mock-gpt", messages: [{ role: "user", content: messageText(task.input.message) }] },
    { parentCallId: task.call_id },
  );
  return reply.done({ text: turn.choices[0].message.content ?? "" });
});
```

### Voice (1.3.0)

```ts
import { Forgebench, voiceHeaders, ForgebenchStreamError } from "@seedlinglabs/forgebench-sdk";

const fb = new Forgebench();
// Speech to text / text to speech (governed; `session` groups the Voice Conversation)
const t = await fb.audio.transcriptions.create({ file: { data, filename: "clip.wav" }, model: "gpt-4o-transcribe", session: "conv-1" });
const s = await fb.audio.speech.create({ model: "tts-1", input: "Hello", voice: "alloy", session: "conv-1" }); // s.traceId

// Tie your own pipeline's calls to one conversation (X-Forgebench-Session / -Turn / traceparent)
const headers = voiceHeaders("conv-1", { turn: 2 });

// Streaming agent run: events in server order
try {
  for await (const event of fb.agents.run(agentId, { message: "hi", stream: true, session: "conv-1" })) { /* ... */ }
} catch (e) {
  if (e instanceof ForgebenchStreamError) { /* e.code, e.message */ }
}

// Voice admin (writes need admin; `update(await get())` round-trips)
const policy = await fb.voice.policy.get();
await fb.voice.policy.update({ ...policy, voice_trace_content: false });
await fb.voice.assets.update(assetId, { expires_at: null }); // null clears a field
```

- **Errors:** pre-stream failures throw the usual typed errors (402
  `BudgetExceededError`, 403, 422 `UnprocessableEntityError` for refusals such as
  `disclosure_missing`). Once a stream is open, an SSE `event: error` frame, or a
  stream that ends without `agent.run.completed` / `[DONE]`, throws
  `ForgebenchStreamError` (`code: "stream_truncated"` for the latter).
- **Trace headers:** `TRACE_ID_HEADER` (`X-Trace-Id`) is the chat response header;
  `AUDIO_TRACE_ID_HEADER` (`X-Forgebench-Trace-Id`) is the `/v1/audio/*` one.
- **`voice_trace_content`** (policy, default off) gates whether gateway
  transcripts/TTS text AND your telemetry content are recorded on traces.

## Budget enforcement (HTTP 402)

The chokepoint rejects over-budget tenants **before** any provider call, so no
spend occurs on a rejected request. The SDK surfaces this as a typed error:

```ts
import { BudgetExceededError } from "@seedlinglabs/forgebench-sdk";

try {
  await forgebench.chat.create({ messages: [{ role: "user", content: "again" }] });
} catch (err) {
  if (err instanceof BudgetExceededError) {
    console.error("Tenant is over budget:", err.code, err.requestId);
  } else {
    throw err;
  }
}
```

The seeded demo tenant has a `monthly_limit_usd` of `0.01`, so a short loop of
`mock-gpt` calls trips a 402 — exactly what `scripts/demo.sh` demonstrates.

## Agents, runs, deployments

```ts
const agent = await forgebench.agents.create({
  name: "support-bot",
  model: "mock-gpt",
  system_prompt: "You are concise.",
});

const run = await forgebench.runs.create({ agent_id: agent.id, input: { topic: "refunds" } });
const finished = await forgebench.runs.waitForCompletion(run.id, { intervalMs: 1000 });
console.log(finished.status, finished.output);

const deployment = await forgebench.deployments.create({ agent_id: agent.id, name: "prod" });
```

`waitForCompletion` has no total polling deadline unless `timeoutMs` is supplied. A timeout or `AbortSignal` stops waiting without cancelling the run; retrieve the retained run ID or wait again. Individual HTTP requests retain the configured transport timeout and bounded retries. Polling deadlines are checked between those requests.


## Governance: audit + metering

```ts
const summary = await forgebench.metering.summary();
console.log(`spent $${summary.spent_usd} of $${summary.monthly_limit_usd}`);

const entries = await forgebench.audit.list({ limit: 20 });
// entries[i].prev_hash / row_hash form a per-tenant tamper-evident hash chain.
```

## Identity

```ts
const me = await forgebench.whoami(); // GET /v1/auth/whoami
console.log(me.tenant_id, me.roles, me.auth_method);

await forgebench.health(); // unauthenticated liveness
```

## API keys

```ts
const created = await forgebench.keys.create({ name: "ci", scopes: ["chat:write"] });
console.log(created.api_key); // ⚠️ shown ONCE — store it now

const keys = await forgebench.keys.list();      // safe view, no secrets
await forgebench.keys.revoke(created.id);
```

## Error handling

All non-2xx responses map to a specific subclass of `ForgebenchAPIError`
(`status`, `code`, `body`, `requestId`):

| HTTP | Error |
| ---- | ----- |
| 400  | `BadRequestError` |
| 401  | `AuthenticationError` |
| 402  | `BudgetExceededError` |
| 403  | `PermissionDeniedError` (RBAC) |
| 404  | `NotFoundError` |
| 409  | `ConflictError` |
| 422  | `UnprocessableEntityError` |
| 429  | `RateLimitError` |
| 5xx  | `InternalServerError` |

Network/abort failures raise `ForgebenchConnectionError`; per-request timeouts raise
`ForgebenchTimeoutError`. Reads retry transient failures (408/425/429/5xx, read timeouts, network errors; shared contract: `packages/sdk-contract/RETRY.md`) with
exponential backoff + jitter (`maxRetries`, default 2). Writes (`POST`/`PATCH`,
incl. chat) retry only on 429 or a failed connect — never on a 5xx or a dropped
connection, since the call may already have run (and been billed); the control
plane has no idempotency key. A stream follows the same rule before it opens;
once events are flowing nothing is retried.

> **Behaviour change in 1.1.0:** `POST`/`PATCH` are no longer retried on 5xx or connection loss
> (they were in 1.0.x); only 429 and failed-to-connect are retried.

## Client options

```ts
new Forgebench({
  baseUrl: "https://api.forgebench.ai",  // default; omit for prod, or pass "http://localhost:8000" for local dev
  apiKey: "sk_...",        // or `token: "<control-plane JWT>"`
  timeoutMs: 60_000,       // per-request timeout
  maxRetries: 2,           // transient-failure retries
  defaultHeaders: {},      // merged into every request
  fetch: customFetch,      // override for tests / non-global-fetch runtimes
});
```

## Build

```bash
npm install
npm run build      # emits dist/ (ESM + .d.ts)
npm run typecheck  # strict type-check, no emit
```

## License

Apache-2.0. Pairs with the [Python SDK](../sdk-py).
