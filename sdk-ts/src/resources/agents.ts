/**
 * Agents resource — CRUD over `/v1/agents`, plus agent → agent calls through
 * the door (A2A tasks): `call` opens a task on another agent you are bound
 * to; `serve` runs THIS agent as a callee with no inbound port.
 */

import type { Transport } from "../http.js";
import { parseSSE } from "../http.js";
import { ForgebenchConnectionError } from "../errors.js";
import { PARENT_CALL_HEADER } from "./chat.js";
import { SESSION_HEADER } from "./audio.js";
import type {
  AgentInfo,
  AgentRunAnswer,
  AgentRunEvent,
  AgentRunRequest,
  CreateAgentRequest,
  Task,
  TaskArtifact,
  TaskMessage,
  TaskPart,
  TaskReply,
} from "../types.js";

const BASE = "/v1/agents";

export interface CallOptions {
  text?: string;
  data?: Record<string, unknown>;
  parts?: TaskPart[];
  /** Continue a conversation — the `context_id` of an `input_required` task. */
  contextId?: string;
  /** Seconds to block for an answer (default 30, max 30). */
  wait?: number;
  /** Nest this task under the governed call that led to it. */
  parentCallId?: string | null;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export type TaskHandler = (task: Task, reply: ReplyBuilder) => TaskReply | unknown | Promise<TaskReply | unknown>;

/** Helpers a `serve()` handler uses to answer a task. */
export interface ReplyBuilder {
  done(out?: { text?: string; data?: Record<string, unknown>; artifacts?: TaskArtifact[]; name?: string }): TaskReply;
  ask(question: string): TaskReply;
  fail(error: string): TaskReply;
}

export function messageParts(opts: { text?: string; data?: Record<string, unknown>; parts?: TaskPart[] }): TaskPart[] {
  const parts: TaskPart[] = [...(opts.parts ?? [])];
  if (opts.text !== undefined) parts.unshift({ text: opts.text });
  if (opts.data !== undefined) parts.push({ data: opts.data });
  if (parts.length === 0) throw new Error("a task message needs text, data, or parts");
  return parts;
}

/** All text parts of a message, joined. */
export function messageText(message: TaskMessage | null | undefined): string {
  return (message?.parts ?? [])
    .map((p) => p.text)
    .filter((t): t is string => typeof t === "string")
    .join("\n");
}

/** Text parts of a task's artifacts (optionally one by name), joined. */
export function artifactText(task: Task, name?: string): string {
  return (task.artifacts ?? [])
    .filter((a) => name === undefined || a.name === name)
    .flatMap((a) => a.parts)
    .map((p) => p.text)
    .filter((t): t is string => typeof t === "string")
    .join("\n");
}

const replyBuilder: ReplyBuilder = {
  done(out = {}) {
    const artifacts: TaskArtifact[] = [...(out.artifacts ?? [])];
    if (out.text !== undefined || out.data !== undefined) {
      artifacts.unshift({ name: out.name ?? "response", parts: messageParts({ text: out.text, data: out.data }) });
    }
    return { state: "completed", artifacts };
  },
  ask(question) {
    return { state: "input_required", message: { role: "agent", parts: [{ text: question }] } };
  },
  fail(error) {
    return { state: "failed", error: error.slice(0, 4000) };
  },
};

function coerceReply(out: unknown): TaskReply {
  if (out && typeof out === "object" && "state" in (out as Record<string, unknown>)) return out as TaskReply;
  if (out === undefined || out === null) return replyBuilder.done({ text: "" });
  if (typeof out === "string") return replyBuilder.done({ text: out });
  if (Array.isArray(out)) return replyBuilder.done({ artifacts: out as TaskArtifact[] });
  if (typeof out === "object") return replyBuilder.done({ data: out as Record<string, unknown> });
  return replyBuilder.done({ text: String(out) });
}

export class AgentsResource {
  constructor(private readonly transport: Transport) {}

  /** Create an agent. Requires the `builder` role or higher. */
  async create(body: CreateAgentRequest): Promise<AgentInfo> {
    return this.transport.request<AgentInfo>(BASE, { method: "POST", body });
  }

  /** List the calling tenant's agents. */
  async list(): Promise<AgentInfo[]> {
    return this.transport.request<AgentInfo[]>(BASE, { method: "GET" });
  }

  /** Retrieve a single agent by id. */
  async retrieve(agentId: string): Promise<AgentInfo> {
    return this.transport.request<AgentInfo>(`${BASE}/${encodeURIComponent(agentId)}`, {
      method: "GET",
    });
  }

  /**
   * Run an agent once through the governed, grounded endpoint
   * (`POST /v1/agents/{id}/run`).
   *
   * With `stream: true` returns an async iterable of the server's SSE events
   * (parsed JSON, in order), like `chat.stream`; the budget gate runs first,
   * so a 402 throws before the stream opens and streams are not retried.
   * Without it, resolves to the cited answer. `session` sets
   * `X-Forgebench-Session` so the agent's calls group into one Voice
   * Conversation.
   *
   * @example
   * for await (const event of forgebench.agents.run(id, { message, stream: true })) {
   *   if (event.type === "delta") speak(String(event.text));
   * }
   */
  run(
    agentId: string,
    body: AgentRunRequest & { stream: true },
    opts?: { signal?: AbortSignal; timeoutMs?: number },
  ): AsyncGenerator<AgentRunEvent, void, void>;
  run(
    agentId: string,
    body: AgentRunRequest & { stream?: false },
    opts?: { signal?: AbortSignal; timeoutMs?: number },
  ): Promise<AgentRunAnswer>;
  run(
    agentId: string,
    body: AgentRunRequest,
    opts: { signal?: AbortSignal; timeoutMs?: number } = {},
  ): Promise<AgentRunAnswer> | AsyncGenerator<AgentRunEvent, void, void> {
    const { session, stream, ...payload } = body;
    const path = `${BASE}/${encodeURIComponent(agentId)}/run`;
    const headers = session ? { [SESSION_HEADER]: session } : undefined;
    if (stream) {
      return this.streamRun(path, { ...payload, stream: true }, headers, opts);
    }
    return this.transport.request<AgentRunAnswer>(path, {
      method: "POST",
      body: payload,
      headers,
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
  }

  private async *streamRun(
    path: string,
    body: unknown,
    headers: Record<string, string> | undefined,
    opts: { signal?: AbortSignal; timeoutMs?: number },
  ): AsyncGenerator<AgentRunEvent, void, void> {
    const res = await this.transport.stream(path, {
      method: "POST",
      body,
      headers,
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
    for await (const data of parseSSE(res, { requireTerminal: true })) {
      let event: AgentRunEvent;
      try {
        event = JSON.parse(data) as AgentRunEvent;
      } catch {
        throw new ForgebenchConnectionError(`Malformed SSE event: ${data.slice(0, 120)}`);
      }
      yield event;
    }
  }

  // --- Agent -> agent through the door -------------------------------------

  /**
   * Open a task on another agent (A2A `SendMessage`). Requires an AGENT
   * credential bound to `callee` (id or name) by an operator; the control
   * plane refuses otherwise with one collapsed `not_permitted`. Blocks up
   * to `wait` seconds for `completed` / `failed` / `input_required`.
   */
  async call(callee: string, opts: CallOptions): Promise<Task> {
    const body: Record<string, unknown> = {
      message: { role: "user", parts: messageParts(opts) },
    };
    if (opts.contextId) body.context_id = opts.contextId;
    return this.transport.request<Task>(`${BASE}/${encodeURIComponent(callee)}/tasks`, {
      method: "POST",
      body,
      query: { wait: opts.wait ?? 30 },
      headers: opts.parentCallId ? { [PARENT_CALL_HEADER]: opts.parentCallId } : undefined,
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
  }

  /** Read a task you opened or were asked to do (A2A `GetTask`). */
  async getTask(callee: string, taskId: string, opts: { wait?: number } = {}): Promise<Task> {
    return this.transport.request<Task>(
      `${BASE}/${encodeURIComponent(callee)}/tasks/${encodeURIComponent(taskId)}`,
      { method: "GET", query: { wait: opts.wait ?? 0 } },
    );
  }

  /** Cancel a task you opened (A2A `CancelTask`). Idempotent. */
  async cancelTask(callee: string, taskId: string): Promise<Task> {
    return this.transport.request<Task>(
      `${BASE}/${encodeURIComponent(callee)}/tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: "POST" },
    );
  }

  // --- The callee side: pull delivery ---------------------------------------

  /** Claim the next task opened on THIS agent, long-polling up to `wait` seconds. */
  async nextTask(opts: { wait?: number; signal?: AbortSignal } = {}): Promise<Task | null> {
    const res = await this.transport.request<Task | undefined>(`${BASE}/me/tasks/next`, {
      method: "GET",
      query: { wait: opts.wait ?? 20 },
      signal: opts.signal,
      timeoutMs: ((opts.wait ?? 20) + 15) * 1000,
    });
    return res ?? null;
  }

  /** Finish, pause, or fail a task this agent claimed. */
  async reply(taskId: string, reply: TaskReply): Promise<Task> {
    return this.transport.request<Task>(`${BASE}/me/tasks/${encodeURIComponent(taskId)}/result`, {
      method: "POST",
      body: reply,
    });
  }

  /**
   * Run this agent as a callee: claim tasks, hand each to `handler`, post
   * its reply. Resolves with the number handled when `signal` aborts or
   * `maxTasks` is reached. Inside the handler, make your governed calls
   * with `parentCallId: task.call_id` so they hang under the task.
   */
  async serve(
    handler: TaskHandler,
    opts: { wait?: number; maxTasks?: number; signal?: AbortSignal } = {},
  ): Promise<number> {
    let handled = 0;
    while (opts.maxTasks === undefined || handled < opts.maxTasks) {
      if (opts.signal?.aborted) break;
      let task: Task | null;
      try {
        task = await this.nextTask({ wait: opts.wait, signal: opts.signal });
      } catch (err) {
        if (opts.signal?.aborted) break;
        throw err;
      }
      if (!task) continue;
      let reply: TaskReply;
      try {
        reply = coerceReply(await handler(task, replyBuilder));
      } catch (err) {
        reply = replyBuilder.fail(err instanceof Error ? `${err.name}: ${err.message}` : String(err));
      }
      await this.reply(task.id, reply);
      handled += 1;
    }
    return handled;
  }
}
