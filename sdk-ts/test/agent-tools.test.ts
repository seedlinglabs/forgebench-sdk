/**
 * Agent tools (MCP) + trace_id correlation.
 *
 * Same real-Forgebench-client-through-a-captured-fetch pattern as keys.test.ts —
 * these assert the exact request shape that goes on the wire, and that a
 * refusal maps to the typed PermissionDeniedError rather than a value the
 * caller must remember to check.
 *
 * Run: `npm test` (builds first, runs against dist/).
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Forgebench, newTraceId } from "../dist/index.js";

const BASE_URL = "http://control-plane.test";

interface Captured {
  url: string;
  method: string;
  body: string | null;
}

function clientWithCapture(responseBody: unknown, status = 200) {
  const calls: Captured[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    calls.push({
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body: typeof init?.body === "string" ? init.body : null,
    });
    const nullBodyStatus = status === 204 || status === 205 || status === 304;
    return new Response(nullBodyStatus ? null : JSON.stringify(responseBody), {
      status,
      headers: nullBodyStatus ? undefined : { "content-type": "application/json" },
    });
  };
  const forgebench = new Forgebench({ baseUrl: BASE_URL, apiKey: "sk_test", fetch: fetchMock });
  return { forgebench, calls };
}

test("newTraceId() returns a 32-hex-char id, same format the server mints", () => {
  const id = newTraceId();
  assert.equal(id.length, 32);
  assert.match(id, /^[0-9a-f]{32}$/);
  assert.notEqual(newTraceId(), id, "must not be constant");
});

test("chat.create() sends trace_id when supplied", async () => {
  const { forgebench, calls } = clientWithCapture({
    id: "c1",
    object: "chat.completion",
    created: 1,
    model: "mock-gpt",
    choices: [],
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
  });
  await forgebench.chat.create({
    messages: [{ role: "user", content: "hi" }],
    trace_id: "shared-trace-1",
  });
  const body = JSON.parse(calls[0]!.body!);
  assert.equal(body.trace_id, "shared-trace-1");
});

test("chat.create() returns the server-resolved trace_id on the response", async () => {
  const { forgebench } = clientWithCapture({
    id: "c1",
    object: "chat.completion",
    created: 1,
    model: "mock-gpt",
    choices: [],
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    trace_id: "server-minted-trace",
  });
  const resp = await forgebench.chat.create({ messages: [{ role: "user", content: "hi" }] });
  assert.equal(resp.trace_id, "server-minted-trace");
});

test("chat.create() omits trace_id when not supplied", async () => {
  const { forgebench, calls } = clientWithCapture({
    id: "c1",
    object: "chat.completion",
    created: 1,
    model: "mock-gpt",
    choices: [],
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
  });
  await forgebench.chat.create({ messages: [{ role: "user", content: "hi" }] });
  const body = JSON.parse(calls[0]!.body!);
  assert.equal("trace_id" in body, false);
});

test("agentTools.list() GETs /v1/agent-tools and unwraps the tools array", async () => {
  const { forgebench, calls } = clientWithCapture({
    agent_id: "a1",
    tools: [{ server: "jira", tool: "search_tickets", description: "Search Jira" }],
  });
  const tools = await forgebench.agentTools.list();
  assert.equal(calls[0]!.url, `${BASE_URL}/v1/agent-tools`);
  assert.equal(calls[0]!.method, "GET");
  assert.equal(tools.length, 1);
  assert.equal(tools[0]!.server, "jira");
  assert.equal(tools[0]!.tool, "search_tickets");
});

