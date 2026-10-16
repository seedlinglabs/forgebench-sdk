import { test } from "node:test";
import assert from "node:assert/strict";
import { Forgebench, ForgebenchTimeoutError } from "../dist/index.js";

const json = (status: string) => new Response(JSON.stringify({
  id: "retained-run", status, model: "mock-gpt", input: {},
}), { headers: { "content-type": "application/json" } });

test("default run polling survives past two minutes", async (t) => {
  let now = 0;
  let polls = 0;
  t.mock.method(Date, "now", () => now);
  const fetchMock: typeof fetch = async () => {
    now += 65_000;
    polls++;
    return json(polls === 3 ? "succeeded" : "running");
  };
  const client = new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 });
  const result = await client.runs.waitForCompletion("retained-run", { intervalMs: 0 });
  assert.equal(result.status, "succeeded");
  assert.equal(polls, 3);
  assert.equal(now, 195_000);
});

test("explicit wait deadline leaves the run retrievable and running", async (t) => {
  let now = 0;
  const calls: string[] = [];
  t.mock.method(Date, "now", () => now);
  const fetchMock: typeof fetch = async (_input, init) => {
    now += 1_000;
    calls.push(init?.method ?? "GET");
    return json("running");
  };
  const client = new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 });
  await assert.rejects(client.runs.waitForCompletion("retained-run", { timeoutMs: 500 }), (error: unknown) => {
    assert.ok(error instanceof ForgebenchTimeoutError);
    assert.match(error.message, /retained-run.*Polling stopped; the run was not cancelled/);
    return true;
  });
  assert.equal((await client.runs.retrieve("retained-run")).status, "running");
  assert.deepEqual(calls, ["GET", "GET"]);
});

test("already aborted polling makes no request", async () => {
  const controller = new AbortController();
  controller.abort(new Error("stop waiting"));
  const fetchMock: typeof fetch = async () => { throw new Error("must not fetch"); };
  const client = new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 });
  await assert.rejects(client.runs.waitForCompletion("retained-run", { signal: controller.signal }), /stop waiting/);
});

test("abort reaches an in-flight poll without cancelling the run", async () => {
  const controller = new AbortController();
  let calls = 0;
  const fetchMock: typeof fetch = async (_input, init) => {
    calls++;
    return new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("stop waiting")), { once: true });
      queueMicrotask(() => controller.abort(new Error("stop waiting")));
    });
  };
  const client = new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 });
  await assert.rejects(client.runs.waitForCompletion("retained-run", { signal: controller.signal }), /stop waiting/);
  assert.equal(calls, 1);
});
