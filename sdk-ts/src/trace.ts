/**
 * A single helper for correlating a model call with the tool calls it leads
 * to. MCP itself carries no notion of "which model call caused this tool
 * call" — `chat.create()`/`chat.stream()` accepts a caller-supplied
 * `trace_id` for exactly this reason. Generate ONE id per logical turn and
 * pass it to that call, then thread it into the agent's own MCP client
 * calls that follow; they will then nest under one trace in the trace view
 * instead of each starting an unrelated one.
 */

/**
 * A fresh 32-hex-char id — the same format the control plane mints
 * server-side (`app.langfuse_client.new_trace_id`) when a caller does not
 * supply one, so a trace id looks the same whichever side generated it.
 *
 * Uses `crypto.randomUUID()` (Node >=19, all modern browsers, Deno) and
 * strips the dashes rather than depending on a UUID library — this SDK has
 * no runtime dependencies and that stays true here.
 */
export function newTraceId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}
