/**
 * Tracing safety (its own file: the module-level default client is process
 * state, and node:test runs each file in its own process):
 *   - two credentials: module-level observe() refuses to guess a client;
 *   - another client's step never lends its trace, ids or user;
 *   - error messages go through the mask;
 *   - huge values are cut while converting;
 *   - a bad FORGEBENCH_SAMPLE_RATE never breaks the client.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Forgebench, observe } from "../dist/index.js";
import { toJsonable } from "../dist/tracing/serialize.js";
import { resolveTracingConfig } from "../dist/tracing/config.js";

const BASE_URL = "http://control-plane.test";
const COMPLETION = {
  id: "c1",
  object: "chat.completion",
  created: 1,
  model: "m",
  choices: [{ index: 0, message: { role: "assistant", content: "hi" }, finish_reason: "stop" }],
  usage: {},
};

interface Sent {
  url: string;
  headers: Headers;
  body: any; // eslint-disable-line @typescript-eslint/no-explicit-any
}

const sent: Sent[] = [];
const fetchMock: typeof fetch = async (input, init) => {
  const url = String(input);
  sent.push({ url, headers: new Headers(init?.headers), body: init?.body ? JSON.parse(String(init.body)) : null });
  const reply = url.endsWith("/v1/observations/batch") ? { accepted: 1 } : COMPLETION;
  return new Response(JSON.stringify(reply), { status: 200, headers: { "content-type": "application/json" } });
};
const client = (apiKey: string, tracing = {}) =>
  new Forgebench({ baseUrl: BASE_URL, apiKey, fetch: fetchMock, maxRetries: 0, tracing });
const batchesBy = (key: string) =>
  sent.filter((s) => s.url.endsWith("/v1/observations/batch") && s.headers.get("authorization") === `Bearer ${key}`);

test("another client's step never lends its trace, ids or user", async () => {
  const tenantA = client("sk_tenant_a");
  const tenantB = client("sk_tenant_b");
  await tenantA.startActiveObservation("a-turn", async () => {
    tenantA.updateActiveTrace({ userId: "alice@tenant-a" });
    await tenantB.chat.create({ messages: [{ role: "user", content: "x" }] });
    tenantB.startActiveObservation("b-step", () => undefined);
  });
  await tenantA.flush();
  await tenantB.flush();

  const call = sent.find((s) => s.url.endsWith("/v1/chat/completions"))!;
  assert.equal(call.headers.get("X-Parent-Observation"), null);
  assert.equal(call.body.user, undefined);
  assert.equal(call.body.trace_id, undefined);
  const [bStep] = batchesBy("sk_tenant_b")[0]!.body.observations;
  const [aTurn] = batchesBy("sk_tenant_a")[0]!.body.observations;
  assert.equal(bStep.parent_observation_id, null);
  assert.notEqual(bStep.trace_id, aTurn.trace_id);

  // ...and with two credentials, module-level observe() no longer guesses.
  const warnings: string[] = [];
  const warn = console.warn;
  console.warn = (m: string) => warnings.push(m);
  try {
    const before = sent.length;
    const unbound = observe(() => "ran", { name: "unbound" });
    assert.equal(unbound(), "ran");
    await tenantA.flush();
    await tenantB.flush();
    assert.equal(sent.length, before);
  } finally {
    console.warn = warn;
  }
  assert.ok(warnings.some((w) => w.includes("use forgebench.observe")));
  await tenantA.shutdown();
  await tenantB.shutdown();
});

test("error messages go through the mask", async () => {
  const masked = client("sk_tenant_a", { mask: (d: unknown) => (typeof d === "string" ? d.replace("4111-1111", "****") : d) });
  const charge = masked.observe(() => {
    throw new Error("card 4111-1111 declined");
  });
  assert.throws(() => charge());
  await masked.flush();
  const last = batchesBy("sk_tenant_a").at(-1)!.body.observations[0];
  assert.equal(last.status_message, "Error: card **** declined");
  await masked.shutdown();
});

test("huge values are cut while converting", () => {
  const big = toJsonable(Array.from({ length: 1_000_000 }, (_, i) => i)) as unknown[];
  assert.ok(big.length <= 10_001);
  assert.equal(big.at(-1), "<truncated>");
  const wide = toJsonable(Object.fromEntries(Array.from({ length: 50_000 }, (_, i) => [`k${i}`, i]))) as Record<string, unknown>;
  assert.equal(wide["<truncated>"], true);
  assert.equal((toJsonable("y".repeat(10_000_000)) as string).length, 64_001);
});

test("a bad FORGEBENCH_SAMPLE_RATE warns and falls back, never throws or silently disables", () => {
  const warn = console.warn;
  const warnings: string[] = [];
  console.warn = (m: string) => warnings.push(m);
  try {
    for (const raw of ["abc", "5", "", "  "]) {
      process.env.FORGEBENCH_SAMPLE_RATE = raw;
      assert.equal(resolveTracingConfig().sampleRate, 1);
    }
    process.env.FORGEBENCH_SAMPLE_RATE = "0.25";
    assert.equal(resolveTracingConfig().sampleRate, 0.25);
  } finally {
    delete process.env.FORGEBENCH_SAMPLE_RATE;
    console.warn = warn;
  }
  assert.equal(warnings.length, 2);
  assert.throws(() => resolveTracingConfig({ sampleRate: 5 }), RangeError); // explicit options still validate
});

test("shutting a client down lets the next credential become the default", async () => {
  // Every client of the earlier tests was shut down, so only these count.
  const a = client("sk_tenant_c");
  await a.shutdown();
  const b = client("sk_tenant_d");
  const before = sent.length;
  observe(() => "ran", { name: "after-shutdown" })();
  await b.flush();
  const posted = sent.slice(before).find((s) => s.url.endsWith("/v1/observations/batch"));
  assert.ok(posted, "module-level observe traced with the remaining client");
  assert.equal(posted!.headers.get("authorization"), "Bearer sk_tenant_d");
  assert.equal(posted!.body.observations[0].name, "after-shutdown");
  await b.shutdown();
});
