/** A 402 with code `token_cap_exceeded` maps to TokenCapExceededError (a BudgetExceededError). */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Forgebench, BudgetExceededError, TokenCapExceededError } from "../dist/index.js";

function clientReturning(status: number, body: unknown) {
  const fetchImpl = (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
  return new Forgebench({ baseUrl: "http://control-plane.test", apiKey: "sk_test", fetch: fetchImpl, maxRetries: 0 });
}

test("token_cap_exceeded 402 is a TokenCapExceededError with parsed fields", async () => {
  const fb = clientReturning(402, {
    detail: {
      code: "token_cap_exceeded",
      message: "key daily token cap exceeded",
      level: "key",
      period: "daily",
      scope_id: "k1",
      limit_tokens: 1000,
      used_tokens: 990,
      attempted_tokens: 50,
    },
  });
  await assert.rejects(fb.keys.list(), (err: unknown) => {
    assert.ok(err instanceof TokenCapExceededError);
    assert.ok(err instanceof BudgetExceededError, "existing 402 handlers must still match");
    assert.equal(err.level, "key");
    assert.equal(err.period, "daily");
    assert.equal(err.scopeId, "k1");
    assert.equal(err.limitTokens, 1000);
    assert.equal(err.usedTokens, 990);
    assert.equal(err.attemptedTokens, 50);
    return true;
  });
});

test("a spend 402 stays a plain BudgetExceededError", async () => {
  const fb = clientReturning(402, { detail: { code: "budget_exceeded", message: "monthly budget exceeded" } });
  await assert.rejects(fb.keys.list(), (err: unknown) => {
    assert.ok(err instanceof BudgetExceededError);
    assert.ok(!(err instanceof TokenCapExceededError));
    return true;
  });
});
