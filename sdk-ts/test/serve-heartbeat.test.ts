import { test } from "node:test";
import assert from "node:assert/strict";
import { Forgebench } from "../dist/index.js";

const TASK = {
  id: "t1", context_id: "c1", status: { state: "working" }, input: { message: { role: "user", parts: [{ text: "go" }] } },
  delivery: "pull", created_at: "2026-11-10T00:00:00Z", updated_at: "2026-11-10T00:00:00Z",
};

function server(heartbeatStatus: number) {
  const posted: string[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input);
    if (url.includes("/me/tasks/next")) return new Response(JSON.stringify(TASK), { status: 200, headers: { "content-type": "application/json" } });
    const state = JSON.parse(String(init?.body)).state;
    posted.push(state);
    const status = state === "working" ? heartbeatStatus : 200;
    return new Response(JSON.stringify({ ...TASK, status: { state } }), { status, headers: { "content-type": "application/json" } });
  };
  return { posted, client: new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 }) };
}

const slow = async () => { await new Promise((r) => setTimeout(r, 350)); return "ok"; };

test("serve sends heartbeats while a long handler runs, then the reply", async () => {
  const { posted, client } = server(200);
  assert.equal(await client.agents.serve(slow, { maxTasks: 1, heartbeat: 0.1 }), 1);
  assert.equal(posted.at(-1), "completed");
  assert.ok(posted.filter((s) => s === "working").length >= 2, posted.join(","));
});

test("a server without heartbeats doesn't break serve", async () => {
  const { posted, client } = server(422);
  assert.equal(await client.agents.serve(slow, { maxTasks: 1, heartbeat: 0.1 }), 1);
  assert.equal(posted.filter((s) => s === "working").length, 1);
  assert.equal(posted.at(-1), "completed");
});

test("heartbeats continue after a transient server error", async () => {
  const posted: string[] = [];
  let beats = 0;
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    if (url.includes("/me/tasks/next")) return json(TASK);
    const state = JSON.parse(String(init?.body)).state;
    posted.push(state);
    if (state === "working" && ++beats === 1) return json({ detail: "bad gateway" }, 502);
    return json({ ...TASK, status: { state } });
  };
  const client = new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 });
  const slower = async () => { await new Promise((r) => setTimeout(r, 450)); return "ok"; };
  assert.equal(await client.agents.serve(slower, { maxTasks: 1, heartbeat: 0.1 }), 1);
  assert.ok(posted.filter((s) => s === "working").length >= 3, posted.join(","));
  assert.equal(posted.at(-1), "completed");
});

test("heartbeat 0 is off", async () => {
  const { posted, client } = server(200);
  await client.agents.serve(slow, { maxTasks: 1, heartbeat: 0 });
  assert.deepEqual(posted, ["completed"]);
});

test("the first heartbeat goes out when the task is claimed", async () => {
  const { posted, client } = server(200);
  await client.agents.serve(async () => "ok", { maxTasks: 1, heartbeat: 60 });
  assert.deepEqual(posted, ["working", "completed"]);
});

test("a reply refused because the task ended does not stop serving", async () => {
  const ids = ["t1", "t2"];
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    if (url.includes("/me/tasks/next")) return json({ ...TASK, id: ids.shift() });
    if (url.includes("/t1/") && JSON.parse(String(init?.body)).state !== "working") return json({ detail: "task is canceled" }, 409);
    return json({ ...TASK, status: { state: "completed" } });
  };
  const client = new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 });
  assert.equal(await client.agents.serve(async () => "ok", { maxTasks: 2, heartbeat: 0 }), 2);
});

test("a call made inside a serve() handler carries the task as its parent", async () => {
  const parents: (string | null)[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    if (url.includes("/me/tasks/next")) return json({ ...TASK, call_id: "call-of-t1" });
    if (url.includes("/other/tasks")) {
      parents.push(new Headers(init?.headers).get("x-parent-call"));
      return json({ ...TASK, id: "t9" });
    }
    return json({ ...TASK, status: { state: "completed" } });
  };
  const client = new Forgebench({ apiKey: "fixture-key", fetch: fetchMock, maxRetries: 0 });
  await client.agents.serve(
    async () => {
      await client.agents.call("other", { text: "help", wait: 0 });
      await client.agents.call("other", { text: "unrelated", wait: 0, parentCallId: null });
      return "ok";
    },
    { maxTasks: 1, heartbeat: 0 },
  );
  assert.deepEqual(parents, ["call-of-t1", null]);
});
