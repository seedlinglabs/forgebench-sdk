/**
 * Client default-option tests: baseUrl must default to the production
 * control plane when omitted, so `new Forgebench({ apiKey })` works out of the
 * box against prod and only needs `baseUrl` overridden for local dev.
 *
 * Run: `npm test` (Node >=22 strips TS types natively).
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Forgebench } from "../dist/index.js";

test("Forgebench defaults baseUrl to the production control plane when omitted", async () => {
  const calls: string[] = [];
  const fetchMock: typeof fetch = async (input) => {
    calls.push(String(input));
    return new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  const forgebench = new Forgebench({ apiKey: "sk_test", fetch: fetchMock });
  await forgebench.health();

  assert.equal(calls.length, 1);
  assert.equal(calls[0], "https://api.forgebench.ai/health");
});

test("Forgebench still honors an explicit baseUrl override", async () => {
  const calls: string[] = [];
  const fetchMock: typeof fetch = async (input) => {
    calls.push(String(input));
    return new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  const forgebench = new Forgebench({
    baseUrl: "http://localhost:8000",
    apiKey: "sk_test",
    fetch: fetchMock,
  });
  await forgebench.health();

  assert.equal(calls[0], "http://localhost:8000/health");
});
