/**
 * Forgebench — the top-level SDK client.
 *
 * One client per credential (an API key `sk_...` or a control-plane session
 * JWT). The token is sent as `Authorization: Bearer <token>`; the server
 * resolves it to a tenant-scoped Principal, so the SDK never deals with
 * tenant ids directly — isolation is enforced server-side by RLS.
 *
 * @example
 * import { Forgebench } from "@seedlinglabs/forgebench-sdk";
 *
 * // baseUrl defaults to https://api.forgebench.ai; pass "http://localhost:8000" for local dev.
 * const forgebench = new Forgebench({
 *   apiKey: process.env.FORGEBENCH_API_KEY!, // the sk_... printed by `make seed`
 * });
 *
 * const res = await forgebench.chat.create({
 *   model: "mock-gpt",
 *   messages: [{ role: "user", content: "Hello" }],
 * });
 * console.log(res.choices[0]?.message.content);
 *
 * The client also carries the tracing API (see `tracing/`): steps are recorded
 * with this client's key and sent in the background.
 */

import { Transport } from "./http.js";
import type { Health, WhoAmI } from "./types.js";
import { ChatResource } from "./resources/chat.js";
import { AgentsResource } from "./resources/agents.js";
import { RunsResource } from "./resources/runs.js";
import { KeysResource } from "./resources/keys.js";
import { DeploymentsResource } from "./resources/deployments.js";
import { AuditResource, MeteringResource } from "./resources/governance.js";
import { AgentToolsResource } from "./resources/agent-tools.js";
import { resolveTracingConfig, type TracingOptions } from "./tracing/config.js";
import type { Observation, ObservationUpdate, TraceUpdate } from "./tracing/observation.js";
import {
  BATCH_PATH,
  Tracer,
  activeObservation,
  registerDefaultTracer,
  type BatchReply,
  type ObserveOptions,
  type StartOptions,
} from "./tracing/tracer.js";
import { VERSION } from "./version.js";

/** Sending steps is bookkeeping: a short timeout, never the agent's problem. */
const TRACING_TIMEOUT_MS = 10_000;

export interface ForgebenchOptions {
  /** Control-plane base URL (default https://api.forgebench.ai). */
  baseUrl?: string;
  /** API key secret (`sk_...`) — programmatic/server-side use. */
  apiKey?: string;
  /**
   * Control-plane session JWT (minted by the OIDC callback) — human/console
   * use. Mutually exclusive with `apiKey`; if both are given, `apiKey` wins.
   */
  token?: string;
  /** Per-request timeout in ms (default 60000). */
  timeoutMs?: number;
  /** Max retry attempts on transient failures (default 2). */
  maxRetries?: number;
  /** Extra headers attached to every request. */
  defaultHeaders?: Record<string, string>;
  /** Custom fetch (tests / runtimes without a global fetch). */
  fetch?: typeof fetch;
  /** Tracing settings; unset fields fall back to `FORGEBENCH_*` env vars. */
  tracing?: TracingOptions;
}

export class Forgebench {
  /** Governed chat completions (the chokepoint). */
  readonly chat: ChatResource;
  /** Agent definitions. */
  readonly agents: AgentsResource;
  /** Asynchronous runs. */
  readonly runs: RunsResource;
  /** API key management. */
  readonly keys: KeysResource;
  /** Agent deployments. */
  readonly deployments: DeploymentsResource;
  /** Tamper-evident audit log. */
  readonly audit: AuditResource;
  /** Usage + budget metering. */
  readonly metering: MeteringResource;
  /** The agent-facing governed tool call (MCP). Requires an agent credential. */
  readonly agentTools: AgentToolsResource;

  private readonly transport: Transport;
  private readonly tracer: Tracer;

  constructor(options: ForgebenchOptions) {
    const token = options.apiKey ?? options.token;
    if (!token) {
      throw new Error("Forgebench: one of `apiKey` or `token` is required.");
    }
    this.transport = new Transport({
      baseUrl: options.baseUrl,
      token,
      timeoutMs: options.timeoutMs,
      maxRetries: options.maxRetries,
      defaultHeaders: options.defaultHeaders,
      fetch: options.fetch,
    });

    this.agents = new AgentsResource(this.transport);
    this.runs = new RunsResource(this.transport);
    this.keys = new KeysResource(this.transport);
    this.deployments = new DeploymentsResource(this.transport);
    this.audit = new AuditResource(this.transport);
    this.metering = new MeteringResource(this.transport);
    this.agentTools = new AgentToolsResource(this.transport);

    const transport = this.transport;
    this.tracer = new Tracer(
      resolveTracingConfig(options.tracing),
      (payload) =>
        transport.request<BatchReply>(BATCH_PATH, { method: "POST", body: payload, timeoutMs: TRACING_TIMEOUT_MS }),
      { name: "forgebench-typescript", version: VERSION },
    );
    if (this.tracer.config.enabled) registerDefaultTracer(this.tracer, `${options.baseUrl ?? ""}\n${token}`);
    this.chat = new ChatResource(this.transport, this.tracer);
  }

  // --- tracing: see `tracing/index.ts` ------------------------------------

  /** `observe()`, recording with THIS client. */
  observe<F extends (...args: never[]) => unknown>(fn: F, options: ObserveOptions = {}): F {
    return this.tracer.observe(fn, options);
  }

  /** Run `fn` with a new active step; steps and model calls inside nest under it. */
  startActiveObservation<R>(name: string, fn: (observation: Observation) => R, options: StartOptions = {}): R {
    return this.tracer.startActiveObservation(name, fn, options);
  }

  /** A step that does NOT become active. Call `end()` on it, or it is never sent. */
  startObservation(name: string, options: StartOptions = {}): Observation {
    return this.tracer.startObservation(name, options);
  }

  updateActiveObservation(update: ObservationUpdate): void {
    this.tracer.updateActiveObservation(update);
  }

  /** Set trace-level fields; model calls in the trace inherit `userId` / `sessionId`. */
  updateActiveTrace(update: TraceUpdate): void {
    this.tracer.updateActiveTrace(update);
  }

  getActiveTraceId(): string | undefined {
    return activeObservation()?.traceId;
  }

  getActiveObservationId(): string | undefined {
    return activeObservation()?.id;
  }

  /**
   * Send every finished step now. Long-running apps never need this; await it
   * before a short script or serverless function returns.
   */
  flush(): Promise<void> {
    return this.tracer.flush();
  }

  /** Flush, then stop recording with this client. */
  shutdown(): Promise<void> {
    return this.tracer.shutdown();
  }

  /** Resolve the current credential to its Principal (`GET /v1/auth/whoami`). */
  async whoami(): Promise<WhoAmI> {
    return this.transport.request<WhoAmI>("/v1/auth/whoami", { method: "GET" });
  }

  /** Liveness probe — unauthenticated `GET /health`. */
  async health(): Promise<Health> {
    return this.transport.request<Health>("/health", { method: "GET" });
  }
}
