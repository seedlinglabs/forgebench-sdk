/**
 * The agent-facing MCP surface (`/v1/agent-tools*`). Requires an AGENT
 * credential — the API key this client was constructed with must have been
 * issued to a registered agent (`api_keys.agent_id` set server-side); a
 * human/developer key has no allowlist and gets a 403 here.
 *
 * The control plane GATES tool calls (it decides whether a model's
 * `tool_calls` pick may reach you, and records that decision); your own MCP
 * client EXECUTES them. `openaiSchema` builds the model's tool list from the
 * live allowlist, `dispatch` runs the calls the gate let through and
 * `report`s each outcome back onto the decision row it was gated on.
 */

import type { Transport } from "../http.js";
import type { OpenAITool, ToolBinding, ToolCallLike, ToolOutcomeReceipt } from "../types.js";

const BASE = "/v1/agent-tools";

type CallOpts = { signal?: AbortSignal; timeoutMs?: number };

export type ToolExecutor = (name: string, args: Record<string, unknown>) => unknown | Promise<unknown>;

export class AgentToolsResource {
  constructor(private readonly transport: Transport) {}

  /**
   * Every tool the calling agent may currently call, resolved server-side
   * from its allowlist — build a tool list from this rather than hardcoding
   * one, so a central revoke actually reaches this agent.
   */
  async list(opts: CallOpts = {}): Promise<ToolBinding[]> {
    const res = await this.transport.request<{ agent_id: string; tools: ToolBinding[] }>(BASE, {
      method: "GET",
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
    return res.tools;
  }

  /**
   * The agent's CURRENT allowlist as an OpenAI `tools` array, ready to send
   * as `tools` on `chat.create`. Built from {@link list} on every call, so a
   * binding revoked centrally drops out of the next turn's tool list. The
   * control plane records only a tool's name and description; pass
   * `parameters` (`{ [toolName]: jsonSchema }`) for the tools you want the
   * model to see typed arguments for. Unlisted tools get a permissive object
   * schema.
   */
  async openaiSchema(
    opts: CallOpts & { parameters?: Record<string, Record<string, unknown>> } = {},
  ): Promise<OpenAITool[]> {
    const bindings = await this.list(opts);
    return bindings.map((b) => ({
      type: "function",
      function: {
        name: b.tool,
        description: b.description ?? "",
        parameters: opts.parameters?.[b.tool] ?? {
          type: "object",
          properties: {},
          additionalProperties: true,
        },
      },
    }));
  }

  /**
   * Record what a tool call the gate allowed actually returned. `callId` is
   * the chat response that carried the tool_call. Client-asserted, stored as
   * such — it never changes the decision.
   */
  async report(
    body: {
      callId: string;
      toolName: string;
      result?: unknown;
      error?: string | null;
      latencyMs?: number | null;
    },
    opts: CallOpts = {},
  ): Promise<ToolOutcomeReceipt> {
    return this.transport.request<ToolOutcomeReceipt>(`${BASE}/report`, {
      method: "POST",
      body: {
        call_id: body.callId,
        tool_name: body.toolName,
        result: body.result ?? null,
        error: body.error ?? null,
        latency_ms: body.latencyMs ?? null,
      },
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
  }

  /**
   * Run every tool_call in a governed response with `execute` (your MCP
   * client), report each outcome against `callId`, and return the
   * `role: "tool"` messages to append before the next model turn. A throwing
   * executor is reported as the tool's error and surfaced to the model as
   * `{ error }` rather than aborting the loop. A failed `report` is logged
   * (console.warn) and never changes the returned messages.
   */
  async dispatch(
    toolCalls: ToolCallLike[] | null | undefined,
    execute: ToolExecutor,
    opts: CallOpts & { callId: string },
  ): Promise<Array<Record<string, unknown>>> {
    const messages: Array<Record<string, unknown>> = [];
    for (const tc of toolCalls ?? []) {
      const name = tc.function?.name;
      if (!name) throw new Error("tool_call has no function.name");
      const args = parseArguments(tc.function.arguments);
      const started = Date.now();
      let content: unknown;
      let result: unknown;
      let error: string | undefined;
      try {
        result = await execute(name, args);
        content = result;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        error = message.slice(0, 4000);
        content = { error: message };
      }
      // Reported exactly once, outside the executor's try: a failed report is
      // bookkeeping, never the tool's outcome, and never aborts the loop.
      try {
        await this.report(
          { callId: opts.callId, toolName: name, result, error, latencyMs: Date.now() - started },
          opts,
        );
      } catch (err) {
        console.warn(`forgebench: report for tool ${name} failed:`, err);
      }
      const msg: Record<string, unknown> = {
        role: "tool",
        name,
        content: typeof content === "string" ? content : JSON.stringify(content ?? null),
      };
      if (tc.id) msg.tool_call_id = tc.id;
      messages.push(msg);
    }
    return messages;
  }
}

function parseArguments(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object") return raw as Record<string, unknown>;
  if (raw === undefined || raw === null || raw === "") return {};
  try {
    const parsed: unknown = JSON.parse(String(raw));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : { _raw: parsed };
  } catch {
    return { _raw: raw };
  }
}
