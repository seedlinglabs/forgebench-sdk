/** Workflows: resource routes/bodies and client.workflow(...).run() key + parent threading. */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Forgebench } from "../dist/index.js";

const BASE_URL = "http://control-plane.test";

interface Seen {
  url: string;
  method: string;
  auth: string | null;
  parent: string | null;
  body: any;
}

function client(reply: (n: number, seen: Seen) => Response) {
  const calls: Seen[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const h = new Headers(init?.headers);
    const seen: Seen = {
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      auth: h.get("authorization"),
      parent: h.get("x-parent-call"),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    };
    calls.push(seen);
    return reply(calls.length, seen);
  };
  return { forgebench: new Forgebench({ baseUrl: BASE_URL, apiKey: "sk_default", fetch: fetchMock, maxRetries: 0 }), calls };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const chatReply = (n: number) => json({ id: "c", created: 1, model: "m", choices: [], usage: {}, call_id: `call-${n}` });
const KEYS = { grading: "sk_grade", retry: "sk_retry", report: "sk_report" };
const msgs = [{ role: "user" as const, content: "x" }];

test("run uses each step's key and threads the previous call as parent", async () => {
  const { forgebench, calls } = client((n) => chatReply(n));
  const wf = forgebench.workflow("qp", KEYS);
  const run = wf.run();
  const a = await run.step("grading").chat({ model: "m", messages: msgs });
  await run.step("retry").chat({ model: "m", messages: msgs });
  await run.step("report").chat({ model: "m", messages: msgs });
  assert.equal(run.rootCallId, a.call_id);
  assert.deepEqual(run.callIds, ["call-1", "call-2", "call-3"]);
  await wf.run().step("retry").chat({ model: "m", messages: msgs }); // a new run: a new root
  assert.deepEqual(
    calls.map((c) => [c.auth, c.parent]),
    [
      ["Bearer sk_grade", null],
      ["Bearer sk_retry", "call-1"],
      ["Bearer sk_report", "call-2"],
      ["Bearer sk_retry", null],
    ],
  );
});

test("unknown step and empty key are refused before any call", () => {
  const { forgebench, calls } = client((n) => chatReply(n));
  assert.throws(() => forgebench.workflow("qp", KEYS).run().step("nope"), /no api key for step nope/);
  assert.throws(() => forgebench.workflow("qp", { grading: "" }), /no api key/);
  assert.equal(calls.length, 0);
});

test("resource routes and bodies", async () => {
  const { forgebench, calls } = client((_n, s) =>
    s.method === "DELETE" ? json({ binding_retained: true, binding_revoked: s.url.includes("revoke_binding") }) : json({}),
  );
  const w = forgebench.workflows;
  await w.list();
  await w.get("qp", { days: 7 });
  await w.checks("qp");
  await w.register({
    name: "QP", owner_identity_id: "o", issue_credential: false,
    steps: [{ name: "grading", model: "m" }], edges: [{ from: "grading", to: "retry", kind: "sequence" }],
  });
  await w.update("qp", { name: "New" });
  await w.addStep("qp", { name: "retry", model: "m" });
  await w.addEdge("qp", { from: "grading", to: "retry", kind: "task" });
  const rm = await w.removeEdge("qp", "e1", { revokeBinding: true });
  await w.removeStep("qp", "retry");
  await w.archive("qp");
  await forgebench.agents.register({ name: "a", owner_identity_id: "o", allowed_models: null, issue_credential: false });
  assert.equal(rm.binding_revoked, true);
  assert.deepEqual(
    calls.map((c) => `${c.method} ${c.url.replace(BASE_URL, "")}`),
    [
      "GET /v1/workflows", "GET /v1/workflows/qp?days=7", "GET /v1/workflows/qp/checks", "POST /v1/workflows",
      "PATCH /v1/workflows/qp", "POST /v1/workflows/qp/steps", "POST /v1/workflows/qp/edges",
      "DELETE /v1/workflows/qp/edges/e1?revoke_binding=true", "DELETE /v1/workflows/qp/steps/retry",
      "DELETE /v1/workflows/qp", "POST /v1/agents/register",
    ],
  );
  assert.equal(calls[3]!.body.issue_credential, false);
  assert.equal(calls[3]!.body.edges[0].from, "grading");
  assert.equal(calls[10]!.body.allowed_models, null);
  assert.equal(calls[10]!.body.issue_credential, false);
});

test("a call without an id does not break the chain", async () => {
  const ids = ["call-1", null, "call-3"];
  const { forgebench, calls } = client((n) =>
    json({ id: "c", created: 1, model: "m", choices: [], usage: {}, call_id: ids[n - 1] }),
  );
  const run = forgebench.workflow("qp", KEYS).run();
  for (const s of ["grading", "retry", "report"]) await run.step(s).chat({ model: "m", messages: msgs });
  assert.equal(run.lastCallId, "call-3");
  assert.deepEqual(run.callIds, ["call-1", "call-3"]);
  assert.deepEqual(calls.map((c) => c.parent), [null, "call-1", "call-1"]);
});
