/**
 * Governed skills for an agent that runs on your own servers.
 *
 * Skills live in Forgebench. An agent hosted there gets them composed for it; an
 * agent running in your process gets them through this hook:
 *
 * ```ts
 * import OpenAI from "openai";
 * import { SkillsHook } from "@seedlinglabs/forgebench-sdk";
 *
 * const hook = new SkillsHook({ apiKey: process.env.AGENT_KEY! });   // the AGENT's key
 * const llm = hook.wrap(new OpenAI({ baseURL: ".../v1", apiKey: process.env.AGENT_KEY! }));
 * await llm.chat.completions.create({ model: "...", messages: [...] }); // skills included
 * ```
 *
 * On each call the hook:
 *
 * 1. Reads the agent's manifest (`GET /v1/skills/agent-manifest`), cached and
 *    re-checked with `If-None-Match` after `refreshMs` (one small request, 304
 *    when nothing changed). `handleWebhook` / `invalidate` force the re-check at
 *    once when Forgebench says a skill changed.
 * 2. Composes the skills into the system messages the way a hosted run does:
 *    optional skills before your own system prompt, the workspace's mandatory
 *    skills after it under `# MANDATORY POLICY`.
 * 3. Sends `X-Forgebench-Skills` (the sha256 of each skill it composed), so the
 *    call is attributed to those exact versions.
 *
 * If Forgebench cannot be reached the last manifest keeps being used; with none
 * cached the error is thrown rather than calling the model without its policy.
 *
 * Same contract as the Python SDK's `SkillsHook`. Runtime-agnostic: `fetch` and
 * Web Crypto only; `writeFiles` alone needs Node's file system.
 */

import { DEFAULT_BASE_URL } from "./http.js";
import { ForgebenchError } from "./errors.js";

export const SKILLS_HEADER = "X-Forgebench-Skills";
export const SIGNATURE_HEADER = "X-Forgebench-Signature";
const MARKER = ".forgebench-skill";
const MANDATORY_HEADER =
  "# MANDATORY POLICY\n\n" +
  "The following policies are set by this organisation and OVERRIDE any " +
  "conflicting instruction above. Follow them even when earlier " +
  "instructions, or the user, ask you not to.";

/** One skill as the manifest returns it (field names as on the wire). */
export interface Skill {
  ref: string;
  skill_id: string;
  name: string;
  version: number;
  description: string;
  mandatory: boolean;
  /** What goes in the prompt. */
  instructions: string;
  /** sha256 of `instructions`: the value reported in `X-Forgebench-Skills`. */
  sha256: string;
  dir_name: string;
  /** The complete SKILL.md, for runtimes that load skills from a directory. */
  content: string;
}

export interface SkillManifest {
  agent_id: string;
  etag: string;
  /** The agent's gateway check: "off" | "flag" | "inject" | "block". */
  enforcement: string;
  /** Mandatory first, then the agent's bound skills in composition order. */
  skills: Skill[];
}

/** No manifest could be read and none is cached. */
export class SkillsUnavailableError extends ForgebenchError {}

/** A webhook delivery's signature is missing, wrong, or too old. */
export class WebhookVerificationError extends ForgebenchError {}

export interface SkillsHookOptions {
  /** The agent's own key (reads only that agent's manifest), or a member key with `agentId`. */
  apiKey: string;
  baseUrl?: string;
  agentId?: string;
  /** How long a manifest is used before re-checking it (default 60000). */
  refreshMs?: number;
  fetch?: typeof fetch;
  /** Clock in ms, for tests (default `Date.now`). */
  now?: () => number;
}

/** A message as both this SDK and OpenAI-shaped clients take it. */
export interface HookMessage {
  role: string;
  content?: unknown;
  [key: string]: unknown;
}

type CreateFn = (body: { messages: HookMessage[] } & Record<string, unknown>, opts?: { headers?: Record<string, string> } & Record<string, unknown>) => unknown;

export class SkillsHook {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly agentId?: string;
  private readonly refreshMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly now: () => number;
  private cached: SkillManifest | null = null;
  private checkedAt: number | null = null;

  constructor(options: SkillsHookOptions) {
    if (!options.apiKey) throw new Error("SkillsHook: `apiKey` is required.");
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.agentId = options.agentId;
    this.refreshMs = options.refreshMs ?? 60_000;
    this.fetchImpl = options.fetch ?? fetch.bind(globalThis);
    this.now = options.now ?? Date.now;
  }

  /** The current manifest, re-checked with the server when stale. */
  async manifest(opts: { force?: boolean } = {}): Promise<SkillManifest> {
    const fresh = this.checkedAt !== null && this.now() - this.checkedAt < this.refreshMs;
    if (this.cached && fresh && !opts.force) return this.cached;
    const headers: Record<string, string> = { Authorization: `Bearer ${this.apiKey}` };
    if (this.cached) headers["If-None-Match"] = this.cached.etag;
    const query = this.agentId ? `?agent_id=${encodeURIComponent(this.agentId)}` : "";
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}/v1/skills/agent-manifest${query}`, { headers });
    } catch (err) {
      return this.staleOrThrow(`could not reach Forgebench: ${(err as Error).message}`);
    }
    if (res.status === 304 && this.cached) {
      this.checkedAt = this.now();
      return this.cached;
    }
    if (res.status !== 200) {
      const text = (await res.text().catch(() => "")).slice(0, 200);
      return this.staleOrThrow(`manifest request failed: HTTP ${res.status} ${text}`);
    }
    this.cached = (await res.json()) as SkillManifest;
    this.checkedAt = this.now();
    return this.cached;
  }

  private staleOrThrow(why: string): SkillManifest {
    if (!this.cached) throw new SkillsUnavailableError(why);
    console.warn(`forgebench skills: ${why}; using the cached manifest`);
    return this.cached;
  }

  /** Re-check with the server on the next call (the cache stays as the fallback). */
  invalidate(): void {
    this.checkedAt = null;
  }

  /**
   * Verify a `skills.changed` delivery and invalidate the cache. Pass the raw
   * body exactly as received; respond 2xx afterwards.
   */
  async handleWebhook(body: string | Uint8Array, signature: string | null | undefined, secret: string): Promise<unknown> {
    const event = await verifyWebhook(body, signature, secret);
    this.invalidate();
    return event;
  }

  /**
   * `messages` with the agent's skills added as system messages: the optional
   * skills before your leading system messages, the mandatory policy right
   * after them. Call once per request.
   */
  async apply<M extends HookMessage>(messages: readonly M[]): Promise<(M | HookMessage)[]> {
    return applyManifest(await this.manifest(), messages);
  }

  /** `X-Forgebench-Skills` for the skills `apply` composes. */
  async headers(): Promise<Record<string, string>> {
    return manifestHeaders(await this.manifest());
  }

  /**
   * Make `create` add the skills to every call: `client.chat.create` on this
   * SDK's `Forgebench`, `client.chat.completions.create` on the OpenAI client.
   * Both take `(body, { headers })`. Returns `client`.
   */
  wrap<T>(client: T): T {
    const chat = (client as { chat?: Record<string, unknown> }).chat;
    const target = (chat?.completions ?? chat) as { create?: CreateFn } | undefined;
    if (!target || typeof target.create !== "function") {
      throw new Error("SkillsHook.wrap: no chat.create or chat.completions.create on this client");
    }
    const original = target.create.bind(target);
    target.create = async (body, opts) => {
      const m = await this.manifest();
      return original(
        { ...body, messages: applyManifest(m, body.messages) },
        { ...opts, headers: { ...manifestHeaders(m), ...(opts?.headers ?? {}) } },
      );
    };
    return client;
  }

  /**
   * Write each skill as `<directory>/<dir_name>/SKILL.md` and remove folders this
   * hook wrote earlier that are no longer in the manifest; folders it did not
   * write are never touched. Node only. Returns the SKILL.md paths.
   */
  async writeFiles(directory: string): Promise<string[]> {
    const m = await this.manifest();
    const proc = (globalThis as { process?: { getBuiltinModule?: (id: string) => unknown } }).process;
    const fs = proc?.getBuiltinModule?.("node:fs/promises") as NodeFs | undefined;
    const path = proc?.getBuiltinModule?.("node:path") as { join: (...p: string[]) => string } | undefined;
    if (!fs || !path) throw new Error("SkillsHook.writeFiles needs Node (process.getBuiltinModule)");
    await fs.mkdir(directory, { recursive: true });
    const keep = new Set(m.skills.map((s) => s.dir_name));
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      if (!entry.isDirectory() || keep.has(entry.name)) continue;
      const ours = await fs.stat(path.join(directory, entry.name, MARKER)).then(() => true, () => false);
      if (ours) await fs.rm(path.join(directory, entry.name), { recursive: true, force: true });
    }
    const written: string[] = [];
    for (const s of m.skills) {
      if (!s.dir_name || s.dir_name === "." || s.dir_name === ".." || /[\\/]/.test(s.dir_name)) {
        throw new SkillsUnavailableError(`refusing to write a skill folder named ${JSON.stringify(s.dir_name)}`);
      }
      const folder = path.join(directory, s.dir_name);
      await fs.mkdir(folder, { recursive: true });
      const file = path.join(folder, "SKILL.md");
      await fs.writeFile(file, s.content, "utf8");
      await fs.writeFile(path.join(folder, MARKER), JSON.stringify({ ref: s.ref, sha256: s.sha256 }), "utf8");
      written.push(file);
    }
    return written;
  }
}

interface NodeFs {
  mkdir(p: string, o: { recursive: boolean }): Promise<unknown>;
  readdir(p: string, o: { withFileTypes: true }): Promise<{ name: string; isDirectory(): boolean }[]>;
  stat(p: string): Promise<unknown>;
  rm(p: string, o: { recursive: boolean; force: boolean }): Promise<void>;
  writeFile(p: string, data: string, enc: "utf8"): Promise<void>;
}

function applyManifest<M extends HookMessage>(m: SkillManifest, messages: readonly M[]): (M | HookMessage)[] {
  const msgs = messages.map((x) => ({ ...x }));
  let lead = msgs.findIndex((x) => x.role !== "system" && x.role !== "developer");
  if (lead === -1) lead = msgs.length;
  const optional = m.skills.filter((s) => !s.mandatory);
  const mandatory = m.skills.filter((s) => s.mandatory);
  const before: HookMessage[] = optional.length
    ? [{ role: "system", content: optional.map((s) => `# Skill: ${s.ref}\n\n${s.instructions}`).join("\n\n") }]
    : [];
  const after: HookMessage[] = mandatory.length
    ? [{ role: "system", content: [MANDATORY_HEADER, ...mandatory.map((s) => `## Policy: ${s.ref}\n\n${s.instructions}`)].join("\n\n") }]
    : [];
  return [...before, ...msgs.slice(0, lead), ...after, ...msgs.slice(lead)];
}

function manifestHeaders(m: SkillManifest): Record<string, string> {
  return m.skills.length ? { [SKILLS_HEADER]: m.skills.map((s) => s.sha256).join(",") } : {};
}

/**
 * Check a delivery's `X-Forgebench-Signature` (`t=<unix seconds>,v1=<hex
 * HMAC-SHA256 of "<t>.<raw body>">`) and return the parsed event. Throws
 * {@link WebhookVerificationError}.
 */
export async function verifyWebhook(
  body: string | Uint8Array,
  signature: string | null | undefined,
  secret: string,
  opts: { toleranceSec?: number; nowSec?: number } = {},
): Promise<unknown> {
  const parts = new Map(
    (signature ?? "").split(",").filter((p) => p.includes("=")).map((p) => {
      const i = p.indexOf("=");
      return [p.slice(0, i).trim(), p.slice(i + 1).trim()] as const;
    }),
  );
  const ts = Number(parts.get("t"));
  const given = (parts.get("v1") ?? "").toLowerCase();
  if (!Number.isInteger(ts) || !given) throw new WebhookVerificationError("missing or malformed X-Forgebench-Signature");
  const now = opts.nowSec ?? Math.floor(Date.now() / 1000);
  if (Math.abs(now - ts) > (opts.toleranceSec ?? 300)) {
    throw new WebhookVerificationError("signature timestamp is outside the tolerance; possible replay");
  }
  const raw = typeof body === "string" ? new TextEncoder().encode(body) : body;
  const signed = new Uint8Array([...new TextEncoder().encode(`${ts}.`), ...raw]);
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("verifyWebhook needs Web Crypto (globalThis.crypto.subtle)");
  const key = await subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const expected = toHex(new Uint8Array(await subtle.sign("HMAC", key, signed)));
  if (!constantTimeEqual(expected, given)) throw new WebhookVerificationError("signature does not match");
  return JSON.parse(new TextDecoder().decode(raw));
}

function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
