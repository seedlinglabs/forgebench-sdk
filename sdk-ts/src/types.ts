/**
 * Wire types mirroring the control-plane Pydantic v2 DTOs (`app.schemas`).
 *
 * These are kept in 1:1 correspondence with the server contract so the SDK is a
 * faithful, typed client. Field names match the JSON the API emits/accepts.
 * If `app/schemas.py` changes, this file must change with it.
 */

// ---------------------------------------------------------------------------
// Auth / identity
// ---------------------------------------------------------------------------

/** Response of `GET /v1/auth/whoami`. */
export interface WhoAmI {
  tenant_id: string;
  identity_id: string | null;
  email: string | null;
  roles: string[];
  scopes: string[];
  auth_method: string;
}

/** Body for `POST /v1/auth/keys` / `POST /v1/keys`. */
export interface CreateApiKeyRequest {
  name: string;
  scopes?: string[];
}

/**
 * Response when an API key is created. `api_key` (the raw secret) is returned
 * exactly ONCE and is never retrievable again — persist it immediately.
 */
export interface ApiKeyCreated {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  /** Raw secret (`sk_...`). Shown once. */
  api_key: string;
  created_at: string;
}

/** Safe API-key view — never includes the raw key or its hash. */
export interface ApiKeyInfo {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  last_used_at: string | null;
  revoked: boolean;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Chat (OpenAI-shaped) — the governed chokepoint contract
// ---------------------------------------------------------------------------

export type ChatRole = "system" | "user" | "assistant" | "tool";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

/**
 * OpenAI-shaped chat request.
 *
 * NOTE: any client-supplied `api_key` / `api_base` are STRIPPED at the
 * server chokepoint (`extra=ignore`). The SDK never sends them; provider
 * credentials are injected per-tenant from the server-side vault.
 */
export interface ChatCompletionRequest {
  /** Defaults to "mock-gpt" server-side when omitted. */
  model?: string;
  messages: ChatMessage[];
  stream?: boolean;
  temperature?: number | null;
  max_tokens?: number | null;
  /**
   * Correlation id (1-64 chars). Reuse the SAME value across the governed
   * calls of one turn to group them under one trace (`newTraceId()` makes
   * one). Omit for the server to mint one — read it back from the response
   * (`trace_id`) or, for a stream, `stream.traceId`. The server also honours
   * a W3C `traceparent` header when this is absent.
   */
  trace_id?: string;
  /** Free-form trace metadata. `metadata.dimensions` = key:value filter dimensions. */
  metadata?: Record<string, unknown>;
  /**
   * SDK convenience: merged into `metadata.dimensions` before sending.
   */
  dimensions?: Record<string, string>;
  /** Your end-user id — becomes the Langfuse trace user. Never sent to the provider. */
  user?: string;
  /** Groups traces into a Langfuse session. */
  session_id?: string;
  /** Extra Langfuse tags (≤10; reserved prefixes like `tenant:`/`agent:` are dropped). */
  tags?: string[];
}

export interface ChatChoiceMessage {
  role: string;
  content: string | null;
  /**
   * OpenAI-shaped `tool_calls` the model emitted, passed through by the
   * control plane AFTER its response gates ran — every entry here was
   * allowed for this agent. Feed them to `agentTools.dispatch`.
   */
  tool_calls?: ToolCallLike[] | null;
}

/** One OpenAI-shaped tool_call as it appears on a governed response. */
export interface ToolCallLike {
  id?: string;
  type?: string;
  function: { name: string; arguments?: string | Record<string, unknown> };
}

/** One entry of an OpenAI `tools` array (`type: "function"`). */
export interface OpenAITool {
  type: "function";
  function: { name: string; description: string; parameters: Record<string, unknown> };
}

export interface ChatChoice {
  index: number;
  message: ChatChoiceMessage;
  finish_reason: string | null;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ChatCompletionResponse {
  id: string;
  object: string;
  created: number;
  model: string;
  choices: ChatChoice[];
  usage: Usage;
  /**
   * The trace_id this call was correlated under: the value you passed to
   * `trace_id` above, or the id the server minted if you omitted it. Reuse
   * it on the next governed call of the same turn to group them, or list
   * them with `traces.list({ traceId })`.
   */
  trace_id?: string | null;
  /**
   * This call's id on the control plane's ledger. Pass it as `parentCallId`
   * on the governed calls this one causes (a sub-agent's turn, the next step
   * of a tool loop) and they are recorded as its children — the ledger then
   * holds the run as a tree, not a flat set of rows under one trace. Also
   * sent as the `X-Call-Id` response header, the only place it can ride on
   * the streaming path.
   */
  call_id?: string | null;
}

/**
 * One streamed Server-Sent-Events chunk (OpenAI `chat.completion.chunk`
 * shape). The gateway passes provider SSE through verbatim, so the `delta`
 * carries incremental content. The terminal `[DONE]` sentinel is consumed by
 * the SDK and never surfaced as a chunk.
 */
export interface ChatCompletionChunk {
  id: string;
  object: string;
  created: number;
  model: string;
  choices: Array<{
    index: number;
    delta: { role?: string; content?: string | null };
    finish_reason: string | null;
  }>;
  /** Only on the server's terminal correlation chunk (consumed by `chat.stream`). */
  trace_id?: string;
  call_id?: string;
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export type RunStatus = "queued" | "running" | "succeeded" | "failed";

export interface CreateRunRequest {
  agent_id?: string | null;
  model?: string;
  input?: Record<string, unknown>;
}

export interface RunInfo {
  id: string;
  agent_id: string | null;
  status: string;
  model: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

// ---------------------------------------------------------------------------
// Agents / deployments
// ---------------------------------------------------------------------------

export interface CreateAgentRequest {
  name: string;
  /**
   * The accountable person for this agent (F1). REQUIRED — the control
   * plane rejects an agent with no named owner. Resolve it from
   * `forgebench.account.whoami()` rather than hardcoding a value.
   */
  owner_identity_id: string;
  description?: string | null;
  model?: string;
  system_prompt?: string | null;
  config?: Record<string, unknown>;
  /** Free-form feature labels; spend and refusals roll up by tag. */
  tags?: string[];
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string | null;
  model: string;
  system_prompt: string | null;
  config: Record<string, unknown>;
  created_at: string;
}

export type DeploymentStatus = "active" | "paused" | "archived";

export interface CreateDeploymentRequest {
  agent_id: string;
  name: string;
  config?: Record<string, unknown>;
}

export interface DeploymentInfo {
  id: string;
  agent_id: string;
  name: string;
  status: string;
  config: Record<string, unknown>;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Governance views: audit, metering
// ---------------------------------------------------------------------------

export interface AuditEntry {
  id: string;
  seq: number;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  actor_identity_id: string | null;
  prev_hash: string;
  row_hash: string;
  status: string;
  created_at: string;
}

export interface MeteringSummary {
  tenant_id: string;
  total_events: number;
  total_tokens: number;
  total_cost_usd: number;
  /** model -> cost_usd */
  by_model: Record<string, number>;
  monthly_limit_usd: number;
  spent_usd: number;
  remaining_usd: number;
}

// ---------------------------------------------------------------------------
// Generic
// ---------------------------------------------------------------------------

export interface Health {
  status: string;
  service: string;
  environment: string;
}

/** Body of a non-2xx response (FastAPI `ErrorResponse` / default detail shape). */
export interface ErrorBody {
  detail?: string | unknown;
  code?: string | null;
}

// ---------------------------------------------------------------------------
// Agent tools (MCP) — the agent-facing governed tool call
// ---------------------------------------------------------------------------

/** One tool the calling agent may currently call — an entry from
 * `GET /v1/agent-tools`, resolved server-side from its allowlist. */
export interface ToolBinding {
  server: string;
  tool: string;
  description: string;
}

// --- Agent -> agent through the door (A2A tasks) -----------------------------

/** One A2A message part: exactly one of `text`, `data`, `file`. */
export interface TaskPart {
  text?: string;
  data?: Record<string, unknown>;
  file?: { uri: string; name?: string; mime?: string };
}

export interface TaskMessage {
  role: "user" | "agent";
  parts: TaskPart[];
}

export type TaskState =
  | "submitted"
  | "working"
  | "input_required"
  | "completed"
  | "failed"
  | "canceled";

export interface TaskArtifact {
  name?: string;
  parts: TaskPart[];
}

/** A task as the control plane records it (`TaskInfo`). */
export interface Task {
  id: string;
  context_id: string;
  caller_agent_id: string | null;
  callee_agent_id: string | null;
  status: { state: TaskState; message?: TaskMessage | null };
  input: { message?: TaskMessage };
  artifacts: TaskArtifact[] | null;
  error: string | null;
  delivery: "push" | "pull";
  /**
   * The task's own row on the ledger. A callee serving this task passes it
   * as `parentCallId` on every governed call it makes, so those calls hang
   * under the task in the tree.
   */
  call_id: string | null;
  parent_call_id: string | null;
  root_call_id: string | null;
  trace_id: string | null;
  claimed_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

/** What a `serve()` handler returns: build with `done`, `ask`, or `fail`. */
export interface TaskReply {
  state: "completed" | "input_required" | "failed";
  artifacts?: TaskArtifact[] | null;
  message?: TaskMessage | null;
  error?: string | null;
}

/** Acknowledgement of `POST /v1/agent-tools/report`. */
export interface ToolOutcomeReceipt {
  event_id: string;
  audit_id: string | null;
  reported: boolean;
}

