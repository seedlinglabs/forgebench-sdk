/**
 * Keys resource routing tests.
 *
 * Regression guard for the create() MISROUTE: key creation must POST to
 * `/v1/auth/keys` (the only server route that mints a key), NOT `/v1/keys`
 * (which has only GET list + POST {id}/revoke). These tests drive the REAL
 * Forgebench client + real Transport through a captured mock `fetch`, so they
 * assert the exact URL + method that actually goes on the wire.
 *
 * Run: `npm test` (Node >=22 strips TS types natively).
 */

import { test } from "node:test";
import assert from "node:assert/strict";

// Import the COMPILED artifact (what consumers actually load). `npm test` runs
// the build first, so dist/ is fresh. This also asserts the shipped output, not
// just the TS source. The src/ files use `.js` import specifiers for emitted
// ESM, which Node's runtime type-stripping cannot resolve against raw .ts.
import { Forgebench } from "../dist/index.js";

const BASE_URL = "http://control-plane.test";

interface Captured {
  url: string;
  method: string;
  body: string | null;
}

/** Build a Forgebench client whose Transport uses a fetch that records the call. */
function clientWithCapture(responseBody: unknown, status = 200) {
  const calls: Captured[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    calls.push({
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body: typeof init?.body === "string" ? init.body : null,
    });
    // Per the Fetch spec a 204/205/304 must carry a null body.
    const nullBodyStatus = status === 204 || status === 205 || status === 304;
    return new Response(nullBodyStatus ? null : JSON.stringify(responseBody), {
      status,
      headers: nullBodyStatus ? undefined : { "content-type": "application/json" },
    });
  };
  const forgebench = new Forgebench({ baseUrl: BASE_URL, apiKey: "sk_test", fetch: fetchMock });
  return { forgebench, calls };
}

test("keys.create() POSTs to /v1/auth/keys (the only create route)", async () => {
  const created = {
    id: "key_1",
    name: "ci",
    prefix: "sk_abcd",
    scopes: [],
    api_key: "sk_rawsecret",
    created_at: "2026-06-22T00:00:00Z",
  };
  const { forgebench, calls } = clientWithCapture(created, 201);

  const res = await forgebench.keys.create({ name: "ci" });

  assert.equal(calls.length, 1, "exactly one HTTP call");
  const call = calls[0]!;
  assert.equal(
    call.url,
    `${BASE_URL}/v1/auth/keys`,
    "create() must target POST /v1/auth/keys, not /v1/keys (which 404/405s)",
  );
  assert.equal(call.method, "POST");
  assert.deepEqual(JSON.parse(call.body!), { name: "ci" });
  assert.equal(res.api_key, "sk_rawsecret");
});

test("keys.create() never hits the bare /v1/keys collection", async () => {
  const { forgebench, calls } = clientWithCapture(
    { id: "k", name: "n", prefix: "p", scopes: [], api_key: "sk_x", created_at: "t" },
    201,
  );
  await forgebench.keys.create({ name: "n" });
  assert.equal(
    calls[0]!.url.endsWith("/v1/keys"),
    false,
    "POST /v1/keys has no server handler — the create misroute must stay fixed",
  );
});

test("keys.list() GETs /v1/keys", async () => {
  const { forgebench, calls } = clientWithCapture([]);
  await forgebench.keys.list();
  assert.equal(calls.length, 1);
  assert.equal(calls[0]!.url, `${BASE_URL}/v1/keys`);
  assert.equal(calls[0]!.method, "GET");
});

test("keys.revoke() POSTs /v1/keys/{id}/revoke (id url-encoded)", async () => {
  const { forgebench, calls } = clientWithCapture({}, 204);
  await forgebench.keys.revoke("key/with space");
  assert.equal(calls.length, 1);
  assert.equal(
    calls[0]!.url,
    `${BASE_URL}/v1/keys/key%2Fwith%20space/revoke`,
  );
  assert.equal(calls[0]!.method, "POST");
});
