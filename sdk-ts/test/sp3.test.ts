/**
 * SDK 1.1 (SP3): stream ids, trace params, write-retry policy, dispatch
 * report failure, newTraceId fallback.
 *
 * Run: `npm test` (builds first, runs against dist/).
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Forgebench, newTraceId, InternalServerError } from "../dist/index.js";

const BASE_URL = "http://control-plane.test";

interface Captured {
  url: string;
  method: string;
  body: string | null;
}

type Reply = Response | (() => Response) | Error;

function client(replies: Reply[], maxRetries = 0) {
  const calls: Captured[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    calls.push({
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body: typeof init?.body === "string" ? init.body : null,
    });
    const r = replies[Math.min(calls.length - 1, replies.length - 1)]!;
    if (r instanceof Error) throw r;
    return typeof r === "function" ? r() : r.clone();
  };
  const forgebench = new Forgebench({ baseUrl: BASE_URL, apiKey: "sk_test", fetch: fetchMock, maxRetries });
  return { forgebench, calls };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const SSE =
  'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n' +
  'data: {"id":"call-9","object":"chat.completion.chunk","created":1,"model":"m","choices":[],"trace_id":"t-body","call_id":"call-9"}\n\n' +
  "data: [DONE]\n\n";

test("chat.stream exposes traceId/callId and consumes the terminal meta chunk", async () => {
  const { forgebench } = client([
    () =>
      new Response(SSE, {
        status: 200,
        headers: { "content-type": "text/event-stream", "x-trace-id": "t-hdr", "x-call-id": "call-9" },
      }),
  ]);
  const stream = forgebench.chat.stream({ messages: [{ role: "user", content: "x" }] });
  assert.equal(stream.traceId, null);
  const first = await stream.next();
  assert.equal(first.done, false);
  assert.equal(stream.traceId, "t-hdr");
  assert.equal(stream.callId, "call-9");
  const rest = [];
  for await (const c of stream) rest.push(c);
  assert.equal(rest.length, 0);
  assert.equal(stream.traceId, "t-body");
});

test("trace params are sent; dimensions merge into metadata; trace_id validated", async () => {
  const { forgebench, calls } = client([json({ id: "1", choices: [], usage: {} })]);
  await forgebench.chat.create({
    messages: [{ role: "user", content: "x" }],
    user: "u1",
    session_id: "s1",
    tags: ["a"],
    metadata: { k: "v", dimensions: { store: "1" } },
    dimensions: { region: "eu" },
  });
  const body = JSON.parse(calls[0]!.body!);
  assert.equal(body.user, "u1");
  assert.equal(body.session_id, "s1");
  assert.deepEqual(body.tags, ["a"]);
  assert.deepEqual(body.metadata, { k: "v", dimensions: { store: "1", region: "eu" } });
  assert.equal("dimensions" in body, false);
  await assert.rejects(
    forgebench.chat.create({ messages: [{ role: "user", content: "x" }], trace_id: "x".repeat(65) }),
    RangeError,
  );
});

test("POST is not retried on 5xx; GET is", async () => {
  const post = client([json({ detail: "boom" }, 500), json({ id: "1" })], 2);
  await assert.rejects(post.forgebench.chat.create({ messages: [{ role: "user", content: "x" }] }), InternalServerError);
  assert.equal(post.calls.length, 1);

  const get = client([json({}, 503), json([])], 2);
  assert.deepEqual(await get.forgebench.agents.list(), []);
  assert.equal(get.calls.length, 2);
});

test("POST is retried on 429 and on a connect failure, not on a mid-request network error", async () => {
  const refused = Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNREFUSED" } });
  const ok = json({ id: "1", choices: [], usage: {} });
  const a = client([json({}, 429), refused, ok], 2);
  await a.forgebench.chat.create({ messages: [{ role: "user", content: "x" }] });
  assert.equal(a.calls.length, 3);

  const reset = Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNRESET" } });
  const b = client([reset, ok], 2);
  await assert.rejects(b.forgebench.chat.create({ messages: [{ role: "user", content: "x" }] }));
  assert.equal(b.calls.length, 1);
});

test("dispatch reports once and survives a failing report", async () => {
  const { forgebench, calls } = client([json({ detail: "down" }, 500)]);
  const warn = console.warn;
  console.warn = () => {};
  try {
    const msgs = await forgebench.agentTools.dispatch(
      [{ id: "t1", function: { name: "lookup", arguments: "{}" } }],
      async () => ({ ok: true }),
      { callId: "c1" },
    );
    assert.equal(calls.length, 1);
    assert.deepEqual(JSON.parse(msgs[0]!.content as string), { ok: true });
  } finally {
    console.warn = warn;
  }
});

test("newTraceId falls back when crypto.randomUUID is missing (Node 18)", () => {
  const orig = globalThis.crypto.randomUUID;
  Object.defineProperty(globalThis.crypto, "randomUUID", { value: undefined, configurable: true });
  try {
    const id = newTraceId();
    assert.match(id, /^[0-9a-f]{32}$/);
    assert.notEqual(id, newTraceId());
  } finally {
    Object.defineProperty(globalThis.crypto, "randomUUID", { value: orig, configurable: true });
  }
});

test("array query values are appended per element", async () => {
  const { forgebench, calls } = client([() => json({ data: [] })]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await (forgebench as any).transport.request("/v1/agents", { query: { tags: ["a", "b"], limit: 5, skip: undefined } });
  assert.equal(new URL(calls[0]!.url).search, "?tags=a&tags=b&limit=5");
});

test("a stream is retried on a 429 before it opens, never on a 5xx", async () => {
  const sse = () => new Response(SSE, { status: 200, headers: { "content-type": "text/event-stream" } });
  const a = client([json({}, 429), sse], 2);
  const chunks = [];
  for await (const c of a.forgebench.chat.stream({ messages: [{ role: "user", content: "x" }] })) chunks.push(c);
  assert.equal(a.calls.length, 2);
  assert.equal(chunks.length, 1);

  const b = client([json({ detail: "boom" }, 503), sse], 2);
  await assert.rejects(async () => {
    for await (const _ of b.forgebench.chat.stream({ messages: [{ role: "user", content: "x" }] })) void _;
  }, InternalServerError);
  assert.equal(b.calls.length, 1);
});

test("an idempotent write (a trace batch) is retried on a 5xx", async () => {
  const { forgebench, calls } = client([json({}, 502), json({ accepted: 1 })], 2);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await (forgebench as any).transport.request("/v1/observations/batch", { method: "POST", body: {}, idempotent: true });
  assert.equal(calls.length, 2);
});
