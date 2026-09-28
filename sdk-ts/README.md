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

Not published to npm — install a pinned release tag from the public SDK repo:

```bash
npm install "github:seedlinglabs/forgebench-sdk#ts-v0.1.0&path:/sdk-ts"
```

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
`ForgebenchTimeoutError`. Transient failures (429/5xx/network) are retried with
exponential backoff + jitter (`maxRetries`, default 2). Streaming requests are
never auto-retried.

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
