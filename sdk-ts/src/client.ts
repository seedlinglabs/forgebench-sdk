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

    this.chat = new ChatResource(this.transport);
    this.agents = new AgentsResource(this.transport);
    this.runs = new RunsResource(this.transport);
    this.keys = new KeysResource(this.transport);
    this.deployments = new DeploymentsResource(this.transport);
    this.audit = new AuditResource(this.transport);
    this.metering = new MeteringResource(this.transport);
    this.agentTools = new AgentToolsResource(this.transport);
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
