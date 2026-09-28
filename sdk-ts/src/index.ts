/**
 * @seedlinglabs/forgebench-sdk — Official TypeScript client for the
 * Forgebench control plane.
 *
 * Typed access to the governed chokepoint (chat), agents, runs, deployments,
 * keys, and governance views (audit + metering). Runtime-agnostic: built on
 * the platform `fetch`, runs on Node >=18, Deno, browsers, and edge runtimes.
 */

export { Forgebench } from "./client.js";
export type { ForgebenchOptions } from "./client.js";

// Trace correlation helper.
export { newTraceId } from "./trace.js";

// Streaming/transport helpers (advanced use).
export { parseSSE } from "./http.js";
export type { Transport, TransportOptions, RequestOptions } from "./http.js";

// Resource classes (for typing / advanced composition).
export { ChatResource, PARENT_CALL_HEADER } from "./resources/chat.js";
export type { ChatCallOptions } from "./resources/chat.js";
export type { ToolExecutor } from "./resources/agent-tools.js";
export { AgentsResource, messageParts, messageText, artifactText } from "./resources/agents.js";
export type { CallOptions, TaskHandler, ReplyBuilder } from "./resources/agents.js";
export { RunsResource } from "./resources/runs.js";
export { KeysResource } from "./resources/keys.js";
export { DeploymentsResource } from "./resources/deployments.js";
export { AuditResource, MeteringResource } from "./resources/governance.js";
export { AgentToolsResource } from "./resources/agent-tools.js";

// Error hierarchy.
export {
  ForgebenchError,
  ForgebenchConnectionError,
  ForgebenchTimeoutError,
  ForgebenchAPIError,
  BadRequestError,
  AuthenticationError,
  PermissionDeniedError,
  NotFoundError,
  ConflictError,
  BudgetExceededError,
  UnprocessableEntityError,
  RateLimitError,
  InternalServerError,
} from "./errors.js";

// Wire types — the full server contract.
export type * from "./types.js";
