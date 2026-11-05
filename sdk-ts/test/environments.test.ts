/** Environments: the resource, agents.* environment methods, workflows.* environment methods. */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Forgebench } from "../dist/index.js";

interface Seen {
  url: string;
  method: string;
  body: any;
}

function client(reply: Response | (() => Response) = new Response("{}", { status: 200, headers: { "content-type": "application/json" } })) {
  const calls: Seen[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    calls.push({
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    });
    return typeof reply === "function" ? reply() : reply.clone();
  };
  return { fb: new Forgebench({ baseUrl: "http://cp.test", apiKey: "sk", fetch: fetchMock, maxRetries: 0 }), calls };
}

test("environments CRUD routes and bodies", async () => {
  const { fb, calls } = client();
  await fb.environments.list();
  await fb.environments.create({ name: "Dev", is_production: false });
  await fb.environments.update("dev", { is_production: true });
  await fb.environments.reorder(["prod", "dev"]);
  assert.deepEqual(
    calls.map((c) => [c.method, c.url.replace("http://cp.test", ""), c.body]),
    [
      ["GET", "/v1/environments", null],
      ["POST", "/v1/environments", { name: "Dev", is_production: false }],
      ["PATCH", "/v1/environments/dev", { is_production: true }],
      ["PUT", "/v1/environments/order", { order: ["prod", "dev"] }],
    ],
  );
});

test("environments.delete resolves on 204", async () => {
  const { fb, calls } = client(new Response(null, { status: 204 }));
  assert.equal(await fb.environments.delete("dev"), undefined);
  assert.equal(calls[0]!.method, "DELETE");
  assert.ok(calls[0]!.url.endsWith("/v1/environments/dev"));
});

test("agent environment methods", async () => {
  const { fb, calls } = client();
  await fb.agents.list({ environment: "dev" });
  await fb.agents.environments("a1");
  await fb.agents.addToEnvironment("a1", { environment: "staging", monthly_budget_usd: 5, issue_credential: false });
  await fb.agents.promote("a1", { to: "prod", dry_run: true });
  await fb.agents.register({ name: "n", owner_identity_id: "o", environment: "dev" } as any);
  const rows = calls.map((c) => [c.method, c.url.replace("http://cp.test", ""), c.body]);
  assert.deepEqual(rows.slice(0, 4), [
    ["GET", "/v1/agents?environment=dev", null],
    ["GET", "/v1/agents/a1/environments", null],
    ["POST", "/v1/agents/a1/environments", { environment: "staging", monthly_budget_usd: 5, issue_credential: false }],
    ["POST", "/v1/agents/a1/promote", { to: "prod", dry_run: true }],
  ]);
  assert.equal(calls[4]!.body.environment, "dev");
});

test("workflow environment methods", async () => {
  const { fb, calls } = client();
  await fb.workflows.get("qp", { environment: "dev" });
  await fb.workflows.checks("qp", { environment: "dev" });
  await fb.workflows.placeEnvironment("qp", "dev");
  await fb.workflows.promote("qp", { from: "dev", to: "prod", dry_run: true });
  await fb.workflows.register({ name: "QP", steps: [{ name: "a", model: "m" }], environment: "dev" } as any);
  const rows = calls.map((c) => [c.method, c.url.replace("http://cp.test", ""), c.body]);
  assert.deepEqual(rows.slice(0, 4), [
    ["GET", "/v1/workflows/qp?environment=dev", null],
    ["GET", "/v1/workflows/qp/checks?environment=dev", null],
    ["POST", "/v1/workflows/qp/environment", { environment: "dev" }],
    ["POST", "/v1/workflows/qp/promote", { add_missing: false, dry_run: true, from: "dev", to: "prod" }],
  ]);
  assert.equal(calls[4]!.body.environment, "dev");
});
