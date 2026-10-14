/**
 * SDK tracing: observe / observations, nesting (incl. concurrent async), the
 * background sender, and model calls nesting under the step they were made from.
 *
 * Run: `npm test` (builds first, runs against dist/).
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  Forgebench,
  createTraceId,
  observe,
  startActiveObservation,
  updateActiveTrace,
  type TracingOptions,
} from "../dist/index.js";
import { BatchExporter } from "../dist/tracing/exporter.js";
import { MAX_FIELD_CHARS, TRUNCATED } from "../dist/tracing/serialize.js";

const BASE_URL = "http://control-plane.test";
const COMPLETION = {
  id: "c1",
  object: "chat.completion",
  created: 1,
  model: "m",
  choices: [{ index: 0, message: { role: "assistant", content: "hi" }, finish_reason: "stop" }],
  usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
};

interface Sent {
  url: string;
  headers: Headers;
  body: any; // eslint-disable-line @typescript-eslint/no-explicit-any
}

function setup(tracing: TracingOptions = {}, batchReply: unknown = { accepted: 1, rejected: [] }) {
  const sent: Sent[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input);
    sent.push({ url, headers: new Headers(init?.headers), body: init?.body ? JSON.parse(String(init.body)) : null });
    const reply = url.endsWith("/v1/observations/batch") ? batchReply : COMPLETION;
    return new Response(JSON.stringify(reply), { status: 200, headers: { "content-type": "application/json" } });
  };
  const forgebench = new Forgebench({ baseUrl: BASE_URL, apiKey: "sk_test", fetch: fetchMock, maxRetries: 0, tracing });
  const batches = () => sent.filter((s) => s.url.endsWith("/v1/observations/batch")).map((s) => s.body);
  const observations = () => batches().flatMap((b) => b.observations);
  const traces = () => batches().flatMap((b) => b.traces);
  const chats = () => sent.filter((s) => s.url.endsWith("/v1/chat/completions"));
  return { forgebench, batches, observations, traces, chats };
}

function quietly<T>(fn: () => Promise<T>): Promise<T> {
  const warn = console.warn;
  const messages: string[] = [];
  console.warn = (m: string) => messages.push(m);
  return fn().finally(() => {
    console.warn = warn;
  });
}

test("observe nests steps, captures arguments and results, and posts with the API key", async () => {
  const { forgebench, observations, traces, batches } = setup({ environment: "staging", release: "2.3.0" });
  const getWeather = observe(async (city: string) => ({ city, temp: 18 }), { name: "get_weather", asType: "tool" });
  const run = observe(async (question: string) => getWeather("sf"), { name: "support-agent", asType: "agent" });

  assert.deepEqual(await run("weather?"), { city: "sf", temp: 18 });
  await forgebench.flush();

  const [tool, agent] = observations();
  assert.equal(agent.parent_observation_id, null);
  assert.equal(agent.type, "agent");
  assert.equal(tool.parent_observation_id, agent.id);
  assert.equal(tool.trace_id, agent.trace_id);
  assert.match(agent.id, /^[0-9a-f]{16}$/);
  assert.equal(tool.input, "sf");
  assert.deepEqual(tool.output, { city: "sf", temp: 18 });
  assert.deepEqual(traces(), [{ id: agent.trace_id, name: "support-agent", environment: "staging", release: "2.3.0" }]);
  assert.equal(batches()[0].sdk.name, "forgebench-typescript");
  await forgebench.shutdown();
});

test("sync functions and multi-argument inputs", async () => {
  const { forgebench, observations } = setup();
  const add = forgebench.observe((a: number, b: number) => a + b, { name: "add" });
  assert.equal(add(2, 3), 5);
  await forgebench.flush();
  const [obs] = observations();
  assert.deepEqual([obs.input, obs.output], [[2, 3], 5]);
  await forgebench.shutdown();
});

test("errors are recorded and re-thrown, sync and async", async () => {
  const { forgebench, observations } = setup();
  const boom = observe(() => {
    throw new TypeError("bad input");
  });
  const asyncBoom = observe(async () => {
    throw new Error("down");
  });
  assert.throws(() => boom(), TypeError);
  await assert.rejects(asyncBoom(), /down/);
  await forgebench.flush();
  const [a, b] = observations();
  assert.deepEqual([a.level, a.status_message], ["ERROR", "TypeError: bad input"]);
  assert.deepEqual([b.level, b.status_message], ["ERROR", "Error: down"]);
  await forgebench.shutdown();
});

test("concurrent async steps keep their own parents", async () => {
  const { forgebench, observations } = setup();
  const fetchOne = observe(async (n: number) => {
    await new Promise((r) => setTimeout(r, 5 * (3 - n)));
    return n;
  }, { name: "fetch", asType: "tool" });
  const branch = observe(async (n: number) => fetchOne(n), { name: "branch", asType: "chain" });
  const root = observe(async () => Promise.all([branch(1), branch(2)]), { name: "root", asType: "agent" });

  await root();
  await forgebench.flush();
  const byId = new Map(observations().map((o) => [o.id, o]));
  for (const tool of observations().filter((o) => o.name === "fetch")) {
    const parent = byId.get(tool.parent_observation_id)!;
    assert.equal(parent.name, "branch");
    assert.equal(parent.input, tool.input); // its own branch, not the other one
  }
  await forgebench.shutdown();
});

test("a model call inside a step nests under it and inherits the trace's user and session", async () => {
  const { forgebench, chats, observations } = setup();
  const stepId = await forgebench.startActiveObservation("agent", async (step) => {
    updateActiveTrace({ userId: "u_42", sessionId: "chat_7", tags: ["support"] });
    await forgebench.chat.create({ messages: [{ role: "user", content: "x" }] });
    return step.id;
  }, { asType: "agent" });
  await forgebench.flush();

  const call = chats()[0]!;
  assert.equal(call.headers.get("X-Parent-Observation"), stepId);
  assert.equal(call.body.trace_id, observations()[0].trace_id);
  assert.deepEqual([call.body.user, call.body.session_id], ["u_42", "chat_7"]);
  await forgebench.shutdown();
});

test("a model call outside a step, or in another trace, is not nested", async () => {
  const { forgebench, chats } = setup();
  await forgebench.chat.create({ messages: [{ role: "user", content: "x" }] });
  await startActiveObservation("s", () =>
    forgebench.chat.create({ messages: [{ role: "user", content: "x" }], trace_id: "other-trace" }),
  );
  const [outside, other] = chats();
  assert.equal(outside!.headers.get("X-Parent-Observation"), null);
  assert.equal(other!.headers.get("X-Parent-Observation"), null);
  assert.equal(other!.body.trace_id, "other-trace");
  await forgebench.shutdown();
});

test("manual steps, metadata and trace fields", async () => {
  const { forgebench, observations, traces } = setup();
  await forgebench.startActiveObservation("turn", (turn) => {
    turn.update({ output: "done", metadata: { step: 1 } });
    const job = forgebench.startObservation("background-job");
    const child = job.startObservation("sub-step", { asType: "retriever" });
    child.end();
    job.end();
    job.end(); // a second end is ignored
    forgebench.updateActiveTrace({ tags: ["a"], metadata: { plan: "pro" } });
    forgebench.updateActiveTrace({ tags: ["a", "b"] });
  }, { asType: "chain", input: { q: 1 } });
  await forgebench.flush();
  const [child, job, turn] = observations();
  assert.equal(job.parent_observation_id, turn.id);
  assert.equal(child.parent_observation_id, job.id);
  assert.deepEqual([turn.input, turn.output, turn.metadata], [{ q: 1 }, "done", { step: 1 }]);
  assert.deepEqual(traces()[0].tags, ["a", "b"]);
  assert.deepEqual(traces()[0].metadata, { plan: "pro" });
  await forgebench.shutdown();
});

for (const tracing of [{ enabled: false }, { sampleRate: 0 }]) {
  test(`nothing is sent with ${JSON.stringify(tracing)}`, async () => {
    const { forgebench, batches, chats } = setup(tracing);
    const step = forgebench.observe(async () => forgebench.chat.create({ messages: [{ role: "user", content: "x" }] }));
    await step();
    await forgebench.flush();
    assert.equal(batches().length, 0);
    assert.equal(chats()[0]!.headers.get("X-Parent-Observation"), null);
    await forgebench.shutdown();
  });
}

test("mask runs before anything leaves; a failing mask withholds", async () => {
  const mask = (data: unknown) => {
    if (data === "explode") throw new Error("bad mask");
    return JSON.parse(JSON.stringify(data).replace("hunter2", "***"));
  };
  const { forgebench, observations } = setup({ mask });
  await quietly(async () => {
    forgebench.startActiveObservation("login", (s) => s.update({ output: "explode" }), { input: { password: "hunter2" } });
    await forgebench.flush();
  });
  const [obs] = observations();
  assert.deepEqual(obs.input, { password: "***" });
  assert.equal(obs.output, "[withheld: mask failed]");
  await forgebench.shutdown();
});

test("unserialisable and huge values are made safe", async () => {
  const { forgebench, observations } = setup();
  const loop: Record<string, unknown> = { n: 1n, nan: NaN, fn: function helper() {} };
  loop.self = loop;
  forgebench.startActiveObservation("s", (s) => s.update({ input: loop, output: "x".repeat(MAX_FIELD_CHARS + 5) }));
  await forgebench.flush();
  const [obs] = observations();
  assert.deepEqual(obs.input, { n: "1", nan: "NaN", fn: "[function helper]", self: "<circular>" });
  assert.ok(obs.output.endsWith(TRUNCATED));
  await forgebench.shutdown();
});

test("server rejections and failed sends are logged once and never thrown", async () => {
  const { forgebench } = setup({}, { accepted: 0, rejected: [{ id: "a", reason: "trace_not_owned" }] });
  const warnings: string[] = [];
  const warn = console.warn;
  console.warn = (m: string) => warnings.push(m);
  try {
    for (let i = 0; i < 2; i++) {
      forgebench.startActiveObservation("s", () => undefined);
      await forgebench.flush();
    }
  } finally {
    console.warn = warn;
  }
  assert.equal(warnings.filter((w) => w.includes("trace_not_owned")).length, 1);
  await forgebench.shutdown();

  const failed: string[] = [];
  const exporter = new BatchExporter(async () => {
    throw new Error("control plane down");
  }, { flushAt: 50, flushIntervalMs: 1000, maxQueueSize: 10 });
  console.warn = (m: string) => failed.push(m);
  try {
    exporter.add({ id: "1", trace_id: "t" } as never);
    await exporter.flush();
  } finally {
    console.warn = warn;
  }
  assert.match(failed[0]!, /control plane down/);
});

test("batches never exceed 100 and a trace record travels with its own steps", async () => {
  const sent: [number, string[]][] = [];
  const exporter = new BatchExporter(async (o, t) => {
    sent.push([o.length, t.map((r) => r.id)]);
  }, { flushAt: 500, flushIntervalMs: 60_000, maxQueueSize: 1000 });
  for (let i = 0; i < 150; i++) exporter.add({ id: String(i), trace_id: "early" } as never);
  exporter.add({ id: "root", trace_id: "late" } as never, { id: "late", name: "run" });
  await exporter.flush();
  assert.deepEqual(sent, [[100, []], [51, ["late"]]]);
});

test("batches stay under the server byte limit", async () => {
  const sent: number[] = [];
  const exporter = new BatchExporter(async (o) => {
    sent.push(JSON.stringify(o).length);
  }, { flushAt: 500, flushIntervalMs: 60_000, maxQueueSize: 1000 });
  for (let i = 0; i < 30; i++) exporter.add({ id: String(i), input: "x".repeat(64_000) } as never);
  await exporter.flush();
  assert.ok(sent.length > 1 && Math.max(...sent) <= 1_000_000);
});

test("queue overflow drops the oldest with one warning", async () => {
  const exporter = new BatchExporter(async () => undefined, { flushAt: 100, flushIntervalMs: 60_000, maxQueueSize: 2 });
  const warnings: string[] = [];
  const warn = console.warn;
  console.warn = (m: string) => warnings.push(m);
  try {
    for (let i = 0; i < 4; i++) exporter.add({ id: String(i), trace_id: "t" } as never);
  } finally {
    console.warn = warn;
  }
  assert.equal(exporter.dropped, 2);
  assert.equal(warnings.length, 1);
  await exporter.shutdown();
});

test("createTraceId is deterministic with a seed", async () => {
  const a = await createTraceId("request-123");
  assert.equal(a, await createTraceId("request-123"));
  assert.match(a, /^[0-9a-f]{32}$/);
  assert.notEqual(await createTraceId(), await createTraceId());
});

test("module-level observe with no client just calls the function", () => {
  const plain = observe((x: number) => x * 2);
  assert.equal(plain(2), 4);
});

test("an unknown step type is refused", () => {
  const { forgebench } = setup();
  assert.throws(() => forgebench.startObservation("x", { asType: "generation" as never }), RangeError);
  void forgebench.shutdown();
});
