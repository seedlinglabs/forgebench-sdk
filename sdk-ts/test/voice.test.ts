/**
 * Voice surface tests: Transcription, Speech Synthesis, streaming agent run,
 * and the voice governance admin helpers.
 *
 * Drives the real Forgebench client + Transport through a recording mock
 * `fetch`. Expected wire values are literals from the release plan's B and F
 * contracts, not derived from SDK constants.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  Forgebench,
  BudgetExceededError,
  ForgebenchStreamError,
  TRACE_ID_HEADER,
  AUDIO_TRACE_ID_HEADER,
  PermissionDeniedError,
  UnprocessableEntityError,
} from "../dist/index.js";

const BASE_URL = "http://control-plane.test";
const TRACE = "b".repeat(32);
const AGENT = "11111111-1111-1111-1111-111111111111";

interface Captured {
  url: string;
  method: string;
  headers: Headers;
  body: BodyInit | null | undefined;
}

function client(respond: (c: Captured) => Response) {
  const calls: Captured[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const c: Captured = {
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      headers: new Headers(init?.headers),
      body: init?.body,
    };
    calls.push(c);
    return respond(c);
  };
  const fb = new Forgebench({ baseUrl: BASE_URL, apiKey: "sk_test", fetch: fetchMock, maxRetries: 0 });
  return { fb, calls };
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

test("transcriptions.create sends multipart with session header", async () => {
  const { fb, calls } = client(() => json({ text: "hello world" }, 200, { "x-forgebench-trace-id": TRACE }));
  const out = await fb.audio.transcriptions.create({
    file: { data: new Uint8Array([1, 2, 3]), filename: "clip.wav", contentType: "audio/wav" },
    model: "gpt-4o-transcribe",
    language: "en",
    prompt: "Acme",
    response_format: "json",
    metadata: { channel: "phone" },
    session: "conv-1",
  });

  const c = calls[0]!;
  assert.equal(c.url, `${BASE_URL}/v1/audio/transcriptions`);
  assert.equal(c.method, "POST");
  assert.equal(c.headers.get("authorization"), "Bearer sk_test");
  assert.equal(c.headers.get("x-forgebench-session"), "conv-1");
  // fetch must set the multipart boundary itself: no JSON content-type.
  assert.equal(c.headers.get("content-type"), null);
  const form = c.body as FormData;
  assert.ok(form instanceof FormData);
  assert.equal(form.get("model"), "gpt-4o-transcribe");
  assert.equal(form.get("language"), "en");
  assert.equal(form.get("prompt"), "Acme");
  assert.equal(form.get("response_format"), "json");
  assert.deepEqual(JSON.parse(String(form.get("metadata"))), { channel: "phone" });
  const file = form.get("file") as File;
  assert.equal(file.name, "clip.wav");
  assert.equal(file.type, "audio/wav");
  assert.equal(file.size, 3);
  assert.equal(out.text, "hello world");
  assert.equal(out.traceId, TRACE);
});

test("transcriptions.create omits unset fields and accepts a Blob", async () => {
  const { fb, calls } = client(() => json({ text: "x" }));
  await fb.audio.transcriptions.create({ file: new Blob([new Uint8Array([9])]), model: "whisper-1" });
  const form = calls[0]!.body as FormData;
  assert.deepEqual([...form.keys()].sort(), ["file", "model"]);
  assert.equal(calls[0]!.headers.get("x-forgebench-session"), null);
});

test("transcriptions.create returns text for plain-text responses", async () => {
  const { fb } = client(
    () => new Response("plain words", { status: 200, headers: { "content-type": "text/plain" } }),
  );
  const out = await fb.audio.transcriptions.create({
    file: new Blob([new Uint8Array([1])]),
    model: "whisper-1",
    response_format: "text",
  });
  assert.equal(out.text, "plain words");
});

test("model_not_allowed nested under detail surfaces as code", async () => {
  const { fb } = client(() =>
    json({ detail: { error: "model_not_allowed", message: "model not allowed" } }, 403),
  );
  await assert.rejects(
    fb.audio.transcriptions.create({ file: new Blob([new Uint8Array([1])]), model: "whisper-1" }),
    (e: unknown) => e instanceof PermissionDeniedError && e.code === "model_not_allowed",
  );
});

test("top-level {error: {...}} envelope is parsed", async () => {
  const { fb } = client(() => json({ error: { code: "model_not_allowed", message: "nope" } }, 403));
  await assert.rejects(
    fb.audio.speech.create({ model: "tts-1", input: "hi", voice: "alloy" }),
    (e: unknown) =>
      e instanceof PermissionDeniedError && e.code === "model_not_allowed" && e.message.includes("nope"),
  );
});

test("budget exhaustion on transcription is BudgetExceededError", async () => {
  const { fb } = client(() => json({ detail: "budget exceeded", code: "budget_exceeded" }, 402));
  await assert.rejects(
    fb.audio.transcriptions.create({ file: new Blob([new Uint8Array([1])]), model: "whisper-1" }),
    BudgetExceededError,
  );
});

test("speech.create returns ArrayBuffer, content type and trace id", async () => {
  const { fb, calls } = client(
    () =>
      new Response(new Uint8Array([0x49, 0x44, 0x33]), {
        status: 200,
        headers: { "content-type": "audio/mpeg", "X-Forgebench-Trace-Id": TRACE },
      }),
  );
  const out = await fb.audio.speech.create({
    model: "gpt-4o-mini-tts",
    input: "I am an AI assistant.",
    voice: "alloy",
    response_format: "mp3",
    speed: 1.25,
    metadata: { k: "v" },
    session: "conv-1",
  });
  const c = calls[0]!;
  assert.equal(c.url, `${BASE_URL}/v1/audio/speech`);
  assert.equal(c.headers.get("x-forgebench-session"), "conv-1");
  assert.deepEqual(JSON.parse(String(c.body)), {
    model: "gpt-4o-mini-tts",
    input: "I am an AI assistant.",
    voice: "alloy",
    response_format: "mp3",
    speed: 1.25,
    metadata: { k: "v" },
  });
  assert.deepEqual([...new Uint8Array(out.audio)], [0x49, 0x44, 0x33]);
  assert.equal(out.contentType, "audio/mpeg");
  assert.equal(out.traceId, TRACE);
});

test("speech.create refusal codes surface (disclosure_missing is 422)", async () => {
  const { fb, calls } = client(() =>
    json({ detail: { code: "disclosure_missing", message: "say you are an AI" } }, 422),
  );
  await assert.rejects(
    fb.audio.speech.create({ model: "tts-1", input: "Hi", voice: "alloy" }),
    (e: unknown) => e instanceof UnprocessableEntityError && e.code === "disclosure_missing",
  );
  assert.deepEqual(JSON.parse(String(calls[0]!.body)), { model: "tts-1", input: "Hi", voice: "alloy" });
});

test("agents.run with stream:true yields SSE events", async () => {
  const sse =
    'data: {"type":"delta","text":"Hel"}\n\n' +
    'data: {"type":"delta","text":"lo"}\n\n' +
    `data: {"type":"done","trace_id":"${TRACE}"}\n\n` +
    "data: [DONE]\n\n";
  const { fb, calls } = client(
    () => new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }),
  );
  const events: Array<Record<string, unknown>> = [];
  for await (const e of fb.agents.run(AGENT, { message: "hi", stream: true, session: "conv-9" })) {
    events.push(e);
  }
  const c = calls[0]!;
  assert.equal(c.url, `${BASE_URL}/v1/agents/${AGENT}/run`);
  assert.deepEqual(JSON.parse(String(c.body)), { message: "hi", stream: true });
  assert.equal(c.headers.get("x-forgebench-session"), "conv-9");
  assert.deepEqual(events.map((e) => e.type), ["delta", "delta", "done"]);
  assert.equal(events[0]!.text, "Hel");
});

test("agents.run without stream returns the answer", async () => {
  const { fb, calls } = client(() => json({ answer: "yo", trace_id: TRACE }));
  const out = await fb.agents.run(AGENT, { message: "hi", top_k: 2 });
  assert.equal(out.answer, "yo");
  assert.deepEqual(JSON.parse(String(calls[0]!.body)), { message: "hi", top_k: 2 });
});

test("voice admin helpers hit F's routes", async () => {
  const { fb, calls } = client((c) => {
    if (c.method === "GET" && c.url.endsWith("/policy")) return json({ disclosure_mode: "log" });
    if (c.method === "PUT") return json({ disclosure_mode: "enforce" });
    if (c.method === "GET") return json([{ id: "x" }]);
    if (c.method === "PATCH") return json({ id: "a2", display_name: "B2" });
    if (c.url.endsWith("/revoke")) return json({ id: "a2", status: "revoked" });
    return json({ id: "new" }, 201);
  });
  assert.equal((await fb.voice.policy.get()).disclosure_mode, "log");
  assert.equal((await fb.voice.policy.update({ disclosure_mode: "enforce" })).disclosure_mode, "enforce");
  assert.deepEqual(await fb.voice.assets.list(), [{ id: "x" }]);
  await fb.voice.assets.create({
    provider: "elevenlabs",
    provider_voice_id: "v1",
    display_name: "Brand",
    kind: "cloned",
    consent_record_id: "c1",
  });
  await fb.voice.assets.update("a2", { display_name: "B2" });
  await fb.voice.assets.revoke("a2");
  await fb.voice.consents.list();
  await fb.voice.consents.create({ subject_name: "Jane", purpose: "voice_clone", scope: "Support agent" });
  await fb.voice.consents.revoke("c1");

  const seen = calls.map((c) => `${c.method} ${c.url.replace(BASE_URL, "")}`);
  assert.deepEqual(seen, [
    "GET /v1/voice/policy",
    "PUT /v1/voice/policy",
    "GET /v1/voice/assets",
    "POST /v1/voice/assets",
    "PATCH /v1/voice/assets/a2",
    "POST /v1/voice/assets/a2/revoke",
    "GET /v1/voice/consents",
    "POST /v1/voice/consents",
    "POST /v1/voice/consents/c1/revoke",
  ]);
  assert.deepEqual(JSON.parse(String(calls[1]!.body)), { disclosure_mode: "enforce" });
  assert.deepEqual(JSON.parse(String(calls[3]!.body)), {
    provider: "elevenlabs",
    provider_voice_id: "v1",
    display_name: "Brand",
    kind: "cloned",
    consent_record_id: "c1",
  });
});

const sseClient = (body: string) =>
  client(() => new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } }));
const drain = async (fb: ReturnType<typeof client>["fb"]) => {
  const out: Array<Record<string, unknown>> = [];
  for await (const e of fb.agents.run(AGENT, { message: "hi", stream: true })) out.push(e);
  return out;
};

test("agents.run stream: event:error frame raises ForgebenchStreamError", async () => {
  const { fb } = sseClient(
    'data: {"type":"delta","text":"a"}\n\n' +
      'event: error\ndata: {"error":{"code":"upstream_failed","message":"boom"}}\n\n',
  );
  await assert.rejects(drain(fb), (e: unknown) => e instanceof ForgebenchStreamError && e.code === "upstream_failed" && e.message === "boom");
});

test("agents.run stream: truncated stream raises, completed-without-DONE does not", async () => {
  await assert.rejects(
    drain(sseClient('data: {"type":"delta","text":"a"}\n\n').fb),
    (e: unknown) => e instanceof ForgebenchStreamError && e.code === "stream_truncated",
  );
  const ok = await drain(sseClient('data: {"type":"agent.run.completed","answer":"x"}\n\n').fb);
  assert.equal(ok.length, 1);
});

test("trace header constants: chat vs audio", () => {
  assert.equal(TRACE_ID_HEADER, "X-Trace-Id");
  assert.equal(AUDIO_TRACE_ID_HEADER, "X-Forgebench-Trace-Id");
});

test("voice.policy.update drops read-only updated_at; assets.update can send null", async () => {
  const { fb, calls } = client(() => json({}));
  await fb.voice.policy.update({ disclosure_mode: "log", updated_at: "2026-01-01" });
  await fb.voice.assets.update("a1", { expires_at: null });
  assert.deepEqual(JSON.parse(String(calls[0]!.body)), { disclosure_mode: "log" });
  assert.deepEqual(JSON.parse(String(calls[1]!.body)), { expires_at: null });
});
