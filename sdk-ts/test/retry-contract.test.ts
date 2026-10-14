/** Cross-SDK retry contract (packages/sdk-contract/retry.json): same table in py and java. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  backoffMs,
  DEFAULT_MAX_RETRIES,
  parseSSE,
  shouldRetryError,
  shouldRetryStatus,
  Transport,
} from "../dist/http.js";
import { ForgebenchConnectionError, ForgebenchTimeoutError } from "../dist/errors.js";

const contract = JSON.parse(
  readFileSync(new URL("../../sdk-contract/retry.json", import.meta.url), "utf8"),
);

const errorOf = (kind: string): Error => {
  if (kind === "connect") return new ForgebenchConnectionError("refused", { code: "ECONNREFUSED" });
  if (kind === "timeout") return new ForgebenchTimeoutError("timed out");
  return new ForgebenchConnectionError("socket hang up", { code: "ECONNRESET" });
};

test("status table matches the contract", () => {
  for (const c of contract.status_cases) {
    assert.equal(
      shouldRetryStatus(c.method, c.status, c.idempotent),
      c.retry,
      `${c.method} idempotent=${c.idempotent} status=${c.status}`,
    );
  }
});

test("transport-error table matches the contract", () => {
  for (const c of contract.error_cases) {
    assert.equal(
      shouldRetryError(c.method, errorOf(c.error), c.idempotent),
      c.retry,
      `${c.method} idempotent=${c.idempotent} error=${c.error}`,
    );
  }
});

test("a read that times out is retried; a POST that times out is not", async () => {
  const hang = (calls: { n: number }): typeof fetch => (_u, init) => {
    calls.n++;
    return new Promise((_res, rej) => init!.signal!.addEventListener("abort", () => rej(new Error("aborted"))));
  };
  for (const [method, expected] of [["GET", 2], ["POST", 1]] as const) {
    const calls = { n: 0 };
    const t = new Transport({ baseUrl: "http://x", fetch: hang(calls), maxRetries: 1, timeoutMs: 20 });
    await assert.rejects(t.request("/p", { method, body: method === "POST" ? {} : undefined }), ForgebenchTimeoutError);
    assert.equal(calls.n, expected, method);
  }
});

test("408 is retried on a read", async () => {
  let n = 0;
  const f: typeof fetch = async () =>
    n++ === 0
      ? new Response("{}", { status: 408, headers: { "retry-after": "0" } })
      : new Response("{}", { status: 200 });
  const t = new Transport({ baseUrl: "http://x", fetch: f, maxRetries: 1 });
  await t.request("/p");
  assert.equal(n, 2);
});

test("every status follows the contract rules", () => {
  assert.equal(DEFAULT_MAX_RETRIES, contract.default_max_retries);
  for (const method of ["GET", "PUT", "DELETE", "POST", "PATCH"]) {
    for (const idempotent of [false, true]) {
      const unsafe = contract.unsafe_methods.includes(method) && !idempotent;
      const allowed = new Set<number>(unsafe ? contract.unsafe_retry_statuses : contract.retryable_statuses);
      for (let status = 100; status < 600; status++) {
        assert.equal(shouldRetryStatus(method, status, idempotent), allowed.has(status), `${method} ${idempotent} ${status}`);
      }
    }
  }
});

const bounds = (attempt: number): [number, number] => {
  const base = Math.min(contract.backoff.base_ms * 2 ** attempt, contract.backoff.cap_ms);
  return [base / 2, base];
};

test("backoff is jittered within the contract bounds", () => {
  for (let attempt = 0; attempt < 7; attempt++) {
    const [lo, hi] = bounds(attempt);
    const seen = new Set<number>();
    for (let i = 0; i < 200; i++) {
      const v = backoffMs(attempt, null);
      assert.ok(v >= lo && v <= hi, `attempt ${attempt}: ${v}`);
      seen.add(v);
    }
    assert.ok(seen.size > 1, "jittered, not constant");
  }
});

test("Retry-After table matches the contract", () => {
  assert.equal(contract.retry_after.max_seconds, 30);
  for (const c of contract.retry_after.cases) {
    const got = backoffMs(1, c.header);
    if (c.seconds === null) {
      const [lo, hi] = bounds(1);
      assert.ok(got >= lo && got <= hi, `${JSON.stringify(c.header)} -> ${got}`);
    } else {
      assert.equal(got, c.seconds * 1000, JSON.stringify(c.header));
    }
  }
});

test("a caller abort is never retried, even on a read", async () => {
  for (const early of [true, false]) {
    let calls = 0;
    const ac = new AbortController();
    const f: typeof fetch = (_u, init) => {
      calls++;
      return new Promise((_res, rej) => {
        // Like real fetch: an already-aborted signal rejects at once.
        if (init!.signal!.aborted) return rej(new Error("aborted"));
        init!.signal!.addEventListener("abort", () => rej(new Error("aborted")));
        if (!early) setTimeout(() => ac.abort(), 5);
      });
    };
    if (early) ac.abort();
    const t = new Transport({ baseUrl: "http://x", fetch: f, maxRetries: 2, timeoutMs: 5_000 });
    await assert.rejects(t.request("/p", { signal: ac.signal }), (e: unknown) => e instanceof ForgebenchConnectionError && !(e instanceof ForgebenchTimeoutError));
    assert.ok(calls <= 1, `early=${early}: ${calls} calls`);
  }
});

test("a stream is never retried once it has started, even a GET", async () => {
  let calls = 0;
  const f: typeof fetch = async () => {
    calls++;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('data: {"x":1}\n\n'));
      },
      pull(c) {
        c.error(new Error("reset mid-stream"));
      },
    });
    return new Response(body, { status: 200 });
  };
  const t = new Transport({ baseUrl: "http://x", fetch: f, maxRetries: 2 });
  const res = await t.stream("/p", { method: "GET" });
  const got: string[] = [];
  await assert.rejects(async () => {
    for await (const d of parseSSE(res)) got.push(d);
  });
  assert.deepEqual(got, ['{"x":1}']);
  assert.equal(calls, 1);
});
