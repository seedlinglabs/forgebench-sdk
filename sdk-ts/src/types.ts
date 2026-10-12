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
  /** Null on an assistant turn that only carries `tool_calls`. */
  content: string | null;
  /** On an assistant turn: the tool calls the model made (echo them back in a tool loop). */
  tool_calls?: ToolCallLike[];
  /** On a `tool` turn: which call this is the result of. */
  tool_call_id?: string;
  /** On a `tool` turn: the tool's name. */
  name?: string;
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
  /** Your end-user id — becomes the trace's user. Never sent to the provider. */
  user?: string;
  /** Groups traces into a session. */
  session_id?: string;
  /** Extra trace tags (≤10; reserved prefixes like `tenant:`/`agent:` are dropped). */
  tags?: string[];
  /** Tools the model may call; each call is gated server-side against the agent's bindings. */
  tools?: OpenAITool[];
  tool_choice?: "auto" | "none" | "required" | { type: "function"; function: { name: string } };
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
   * it on the next governed call of the same turn to group them.
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
  /** OpenAI-style envelope on the compatible routes: `{error: {code, message}}` or `{error: "code"}`. */
  error?: string | { code?: string; message?: string } | unknown;
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



// ---------------------------------------------------------------------------
// Voice: Transcription, Speech Synthesis, governance admin
// ---------------------------------------------------------------------------

/** Audio input: a Blob/File, raw bytes, or bytes with an explicit filename. */
export type AudioInput =
  | Blob
  | Uint8Array
  | ArrayBuffer
  | { data: Blob | Uint8Array | ArrayBuffer; filename?: string; contentType?: string };

export interface TranscriptionRequest {
  file: AudioInput;
  model: string;
  language?: string;
  prompt?: string;
  response_format?: "json" | "text" | "verbose_json";
  metadata?: Record<string, unknown>;
  /** Voice Conversation key, sent as `X-Forgebench-Session`. */
  session?: string;
}

export interface Transcription {
  text: string;
  /** Full JSON body (segments, duration, ... for `verbose_json`); empty for plain text. */
  raw: Record<string, unknown>;
  /** From `X-Forgebench-Trace-Id`. */
  traceId: string | null;
}

export interface SpeechRequest {
  model: string;
  input: string;
  voice: string;
  response_format?: "mp3" | "opus" | "wav" | "pcm" | "flac";
  speed?: number;
  metadata?: Record<string, unknown>;
  /** Voice Conversation key, sent as `X-Forgebench-Session`. */
  session?: string;
}

export interface SpeechAudio {
  audio: ArrayBuffer;
  contentType: string | null;
  /** From `X-Forgebench-Trace-Id`. */
  traceId: string | null;
}

/** `POST /v1/agents/{id}/run` body (plus `session`, sent as a header). */
export interface AgentRunRequest {
  message: string;
  top_k?: number;
  dimensions?: Record<string, string>;
  stream?: boolean;
  session?: string;
}

/** Cited answer of a governed agent run. Extra server fields pass through. */
export interface AgentRunAnswer {
  answer?: string;
  trace_id?: string;
  [key: string]: unknown;
}

/** One parsed SSE event of a streaming agent run, in server order. */
export type AgentRunEvent = Record<string, unknown>;

export type DisclosureMode = "log" | "enforce" | "inject";

export interface VoicePolicy {
  disclosure_mode: DisclosureMode;
  disclosure_phrases: Record<string, string[]>;
  inject_text: Record<string, string>;
  default_language: string;
  marking_required: boolean;
  marking_min_level: "claimed" | "confirmed";
  transcript_guardrails: boolean;
  voice_assets_required_for: "cloned_brand" | "all" | "none";
  /**
   * Record transcripts, TTS text AND customer telemetry content on traces.
   * Default off: content is withheld.
   */
  voice_trace_content: boolean;
  updated_at?: string | null;
}

export type VoicePolicyUpdate = Partial<Omit<VoicePolicy, "updated_at">>;

export interface VoiceAsset {
  id: string;
  provider: string;
  provider_voice_id: string;
  display_name: string;
  kind: "stock" | "brand" | "cloned";
  consent_record_id: string | null;
  licensed_uses: string[];
  allowed_agent_ids: string[] | null;
  expires_at: string | null;
  status: "active" | "revoked";
  created_at: string;
  updated_at: string;
}

export interface CreateVoiceAssetRequest {
  provider: string;
  provider_voice_id: string;
  display_name: string;
  kind: "stock" | "brand" | "cloned";
  /** Required when `kind` is `cloned`. */
  consent_record_id?: string;
  licensed_uses?: string[];
  /** Omit (null on the server) for all agents. */
  allowed_agent_ids?: string[];
  expires_at?: string;
}

/** Send `null` to clear a field (e.g. `expires_at: null` = never expires). */
export interface UpdateVoiceAssetRequest {
  display_name?: string;
  consent_record_id?: string | null;
  licensed_uses?: string[] | null;
  allowed_agent_ids?: string[] | null;
  expires_at?: string | null;
}

export interface ConsentRecord {
  id: string;
  subject_name: string;
  subject_contact: string | null;
  purpose: "voice_clone" | "recording";
  scope: string;
  evidence_uri: string | null;
  granted_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  recorded_by: string | null;
  created_at: string;
}

export interface CreateConsentRecordRequest {
  subject_name: string;
  purpose: "voice_clone" | "recording";
  scope: string;
  subject_contact?: string;
  evidence_uri?: string;
  /** Defaults to now on the server. */
  granted_at?: string;
  expires_at?: string;
}
