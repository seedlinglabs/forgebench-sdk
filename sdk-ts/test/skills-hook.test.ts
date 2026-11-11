/**
 * SkillsHook: an agent on its own servers gets its governed skills.
 *
 * Same contract as packages/sdk-py/tests/test_skills_hook.py; the server side is
 * pinned in apps/control-plane/tests/test_skill_hooks.py. Drives the compiled
 * client (dist/) through a mock fetch.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { mkdtemp, mkdir, readdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  Forgebench,
  SkillsHook,
  SkillsUnavailableError,
  WebhookVerificationError,
  verifyWebhook,
  SKILLS_HEADER,
} from "../dist/index.js";

const BASE = "http://control-plane.test";
const KEY = "sk_agent_test";

function skill(name: string, mandatory: boolean, text: string) {
  return {
    ref: `skill:acme/${name}@1`, skill_id: `id-${name}`, name, version: 1, description: `${name} description`,
    mandatory, instructions: text, sha256: createHash("sha256").update(text).digest("hex"),
    dir_name: `acme-${name}`, content: `---\nname: acme-${name}\n---\n\n${text}\n`,
  };
}

const MANIFEST = {
  agent_id: "a-1", etag: '"v1"', enforcement: "inject",
  skills: [
    skill("pii-policy", true, "Never output a customer's card number."),
    skill("expense-review", false, "Check the receipt date before approving."),
  ],
};

interface Seen { url: string; method: string; headers: Headers; body: string | null }

/** A fetch that answers from a queue per path prefix and records every call. */
function mockFetch(routes: Record<string, Array<() => Response>>) {
  const seen: Seen[] = [];
  const impl: typeof fetch = async (input, init) => {
    const url = String(input);
    seen.push({ url, method: (init?.method ?? "GET").toUpperCase(), headers: new Headers(init?.headers),
                body: typeof init?.body === "string" ? init.body : null });
    const key = Object.keys(routes).find((p) => url.startsWith(BASE + p));
    const next = key ? routes[key]!.shift() : undefined;
    if (!next) throw new TypeError(`no mock for ${url}`);
    return next();
  };
  return { impl, seen };
}

const json = (body: unknown, status = 200) => () =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const notModified = () => () => new Response(null, { status: 304 });
const down = () => () => { throw new TypeError("fetch failed"); };

test("the manifest is cached, then revalidated with its ETag", async () => {
  const { impl, seen } = mockFetch({ "/v1/skills/agent-manifest": [json(MANIFEST), notModified()] });
  let now = 1_000_000;
  const hook = new SkillsHook({ apiKey: KEY, baseUrl: BASE, refreshMs: 60_000, fetch: impl, now: () => now });

  const m = await hook.manifest();
  assert.deepEqual(m.skills.filter((s) => s.mandatory).map((s) => s.name), ["pii-policy"]);
  assert.equal(seen[0]!.headers.get("authorization"), `Bearer ${KEY}`);
  assert.equal(seen[0]!.headers.get("if-none-match"), null);

  await hook.manifest();
  assert.equal(seen.length, 1, "inside the refresh window: no request");

  now += 61_000;
  assert.equal(await hook.manifest(), m, "304 keeps the same manifest");
  assert.equal(seen[1]!.headers.get("if-none-match"), '"v1"');
});

test("an outage keeps the last manifest; a cold start refuses", async () => {
  const { impl } = mockFetch({ "/v1/skills/agent-manifest": [json(MANIFEST), down()] });
  const hook = new SkillsHook({ apiKey: KEY, baseUrl: BASE, refreshMs: 0, fetch: impl });
  const first = await hook.manifest();
  assert.equal(await hook.manifest(), first);

  const cold = new SkillsHook({
    apiKey: KEY, baseUrl: BASE,
    fetch: mockFetch({ "/v1/skills/agent-manifest": [() => new Response("maintenance", { status: 503 })] }).impl,
  });
  await assert.rejects(cold.manifest(), (e: unknown) => e instanceof SkillsUnavailableError && /503/.test(String(e)));
});

test("apply composes like a hosted run, and headers name the skills", async () => {
  const hook = new SkillsHook({ apiKey: KEY, baseUrl: BASE, fetch: mockFetch({ "/v1/skills/agent-manifest": [json(MANIFEST)] }).impl });
  const out = await hook.apply([
    { role: "system", content: "You are the finance assistant." },
    { role: "user", content: "Approve this taxi receipt." },
  ]);
  assert.deepEqual(out.map((m) => m.role), ["system", "system", "system", "user"]);
  assert.match(String(out[0]!.content), /^# Skill: skill:acme\/expense-review@1/);
  assert.equal(out[1]!.content, "You are the finance assistant.");
  assert.match(String(out[2]!.content), /^# MANDATORY POLICY/);
  assert.ok(String(out[2]!.content).includes("Never output a customer's card number."));
  assert.deepEqual(await hook.headers(), { [SKILLS_HEADER]: MANIFEST.skills.map((s) => s.sha256).join(",") });
});

test("wrap adds the skills to a Forgebench client call", async () => {
  const { impl, seen } = mockFetch({
    "/v1/skills/agent-manifest": [json(MANIFEST)],
    "/v1/chat/completions": [json({
      id: "c1", object: "chat.completion", created: 0, model: "mock-gpt",
      choices: [{ index: 0, message: { role: "assistant", content: "ok" }, finish_reason: "stop" }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    })],
  });
  const hook = new SkillsHook({ apiKey: KEY, baseUrl: BASE, fetch: impl });
  const client = hook.wrap(new Forgebench({ apiKey: KEY, baseUrl: BASE, fetch: impl, maxRetries: 0 }));
  const res = await client.chat.create({ model: "mock-gpt", messages: [{ role: "user", content: "hi" }] },
                                       { headers: { "X-Mine": "1" } });
  assert.equal(res.choices[0]!.message.content, "ok");
  const call = seen.find((s) => s.url.endsWith("/v1/chat/completions"))!;
  const body = JSON.parse(call.body!);
  assert.deepEqual(body.messages.map((m: { role: string }) => m.role), ["system", "system", "user"]);
  assert.ok(body.messages[1].content.includes("Never output a customer's card number."));
  assert.equal(call.headers.get(SKILLS_HEADER)!.split(",").length, 2);
  assert.equal(call.headers.get("x-mine"), "1");
});

test("wrap works with an OpenAI-shaped client", async () => {
  const calls: Array<{ body: { messages: unknown[] }; opts?: { headers?: Record<string, string>; timeout?: number } }> = [];
  const client = {
    chat: {
      completions: {
        create: async (body: { messages: unknown[] }, opts?: { headers?: Record<string, string>; timeout?: number }) => {
          calls.push({ body, opts });
          return "response";
        },
      },
    },
  };
  const hook = new SkillsHook({ apiKey: KEY, baseUrl: BASE, fetch: mockFetch({ "/v1/skills/agent-manifest": [json(MANIFEST)] }).impl });
  const wrapped = hook.wrap(client);
  assert.equal(await wrapped.chat.completions.create({ messages: [{ role: "user", content: "x" }] }, { timeout: 5 }), "response");
  assert.equal(calls[0]!.body.messages.length, 3);
  assert.ok(calls[0]!.opts!.headers![SKILLS_HEADER]);
  assert.equal(calls[0]!.opts!.timeout, 5, "the caller's other options pass through");
  assert.throws(() => hook.wrap({}), /no chat.create/);
});

function signed(body: string, secret: string, ts: number): string {
  return `t=${ts},v1=` + createHmac("sha256", secret).update(`${ts}.${body}`).digest("hex");
}

test("a webhook is verified and forces a re-check", async () => {
  const { impl, seen } = mockFetch({ "/v1/skills/agent-manifest": [json(MANIFEST), notModified()] });
  const hook = new SkillsHook({ apiKey: KEY, baseUrl: BASE, refreshMs: 3_600_000, fetch: impl });
  await hook.manifest();
  const body = JSON.stringify({ type: "skills.changed", changes: [{ change: "skill.publish" }] });
  const secret = "whsec_test";
  const now = Math.floor(Date.now() / 1000);

  await assert.rejects(hook.handleWebhook(body, signed(body, "whsec_wrong", now), secret), /does not match/);
  await assert.rejects(verifyWebhook(body, signed(body, secret, now - 3600), secret), /tolerance/);
  await assert.rejects(verifyWebhook(body, undefined, secret), (e: unknown) => e instanceof WebhookVerificationError);
  await hook.manifest();
  assert.equal(seen.length, 1, "a rejected delivery changes nothing");

  const event = (await hook.handleWebhook(new TextEncoder().encode(body), signed(body, secret, now), secret)) as { type: string };
  assert.equal(event.type, "skills.changed");
  await hook.manifest();
  assert.equal(seen.length, 2, "re-checked at once, despite the hour-long window");
});

test("writeFiles installs skills and removes only its own folders", async () => {
  const dir = await mkdtemp(join(tmpdir(), "fb-skills-"));
  await mkdir(join(dir, "someone-elses"));
  const { impl } = mockFetch({
    "/v1/skills/agent-manifest": [json(MANIFEST), json({ ...MANIFEST, etag: '"v2"', skills: MANIFEST.skills.slice(0, 1) })],
  });
  const hook = new SkillsHook({ apiKey: KEY, baseUrl: BASE, refreshMs: 0, fetch: impl });
  const paths = await hook.writeFiles(dir);
  assert.equal(paths.length, 2);
  assert.match(await readFile(join(dir, "acme-pii-policy", "SKILL.md"), "utf8"), /^---\nname: acme-pii-policy/);

  await hook.writeFiles(dir);
  assert.deepEqual((await readdir(dir)).sort(), ["acme-pii-policy", "someone-elses"]);
});
