/**
 * Trace correlation helpers. `trace_id` GROUPS the governed calls of one
 * turn/run: pass the same id to each `chat.create()` / `chat.stream()` of that
 * turn and they share one Langfuse trace and one ledger `trace_id`
 * (`traces.list({ traceId })`). MCP tool calls the model makes are attributed
 * to the model call automatically — nothing to thread into your MCP client.
 */

/** The control plane stores trace ids in a 64-char column and 422s longer ones. */
export const MAX_TRACE_ID_LENGTH = 64;

/**
 * A fresh 32-hex-char id — the same format the control plane mints
 * server-side when a caller does not supply one. Uses `crypto.randomUUID()`
 * where available, else `crypto.getRandomValues()` (Node 18 without the
 * global `crypto.randomUUID`, older browsers), else `Math.random()` — a
 * correlation id, never a secret. No runtime dependencies.
 */
export function newTraceId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID().replace(/-/g, "");
  const bytes = new Uint8Array(16);
  if (typeof c?.getRandomValues === "function") c.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Throws on an id the server would 422 — before spending a round trip. */
export function validateTraceId(traceId: string | undefined | null): void {
  if (traceId === undefined || traceId === null) return;
  if (typeof traceId !== "string" || traceId.length < 1 || traceId.length > MAX_TRACE_ID_LENGTH) {
    throw new RangeError(`trace_id must be a 1-${MAX_TRACE_ID_LENGTH} character string`);
  }
}
