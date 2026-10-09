/**
 * Chat completions — the governed chokepoint.
 *
 * Every call here flows through the server's single tenant transaction:
 * budget pre-gate (Valkey) -> provider key injection from vault -> gateway
 * call -> one metering_event + one hash-chained audit_log row. The SDK never
 * sends a provider `api_key`/`api_base`; the server strips them anyway.
 */

import type { Transport } from "../http.js";
import { parseSSE } from "../http.js";
import { ForgebenchConnectionError } from "../errors.js";
import { validateTraceId } from "../trace.js";
import type {
  ChatCompletionChunk,
  ChatCompletionRequest,
  ChatCompletionResponse,
} from "../types.js";

const CHAT_PATH = "/v1/chat/completions";

/**
 * Request header that nests a governed call under a previous one. Its value
 * is that call's `call_id` (see `ChatCompletionResponse.call_id`).
 */
export const PARENT_CALL_HEADER = "X-Parent-Call";

export interface ChatCallOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  /**
   * The `call_id` of the governed call that CAUSED this one — the
   * orchestrating turn whose answer led here, or the previous turn of a tool
   * loop. The server records this call as its child, so the ledger holds the
   * workflow as a tree (cost rolls up to the root, the console draws who
   * called what). Never affects whether the call is allowed or what it costs;
   * omit it and this call is a root.
   */
  parentCallId?: string | null;
}

function lineageHeaders(opts: ChatCallOptions): Record<string, string> | undefined {
  return opts.parentCallId ? { [PARENT_CALL_HEADER]: opts.parentCallId } : undefined;
}

/**
 * A streamed completion: iterate it with `for await`. `traceId` / `callId`
 * are set once the response headers arrive (first iteration) and confirmed by
 * the server's terminal correlation chunk, which is consumed, not yielded.
 */
export type ChatStream = AsyncGenerator<ChatCompletionChunk, void, void> & {
  traceId: string | null;
  callId: string | null;
};

/** Wire body: validates `trace_id`, folds `dimensions` into `metadata.dimensions`. */
function wireBody(body: ChatCompletionRequest, stream: boolean): Record<string, unknown> {
  validateTraceId(body.trace_id);
  const { dimensions, ...rest } = body;
  const out: Record<string, unknown> = { ...rest, stream };
  if (dimensions) {
    const meta = { ...(body.metadata ?? {}) };
    meta.dimensions = { ...((meta.dimensions as Record<string, string> | undefined) ?? {}), ...dimensions };
    out.metadata = meta;
  }
  return out;
}

export class ChatResource {
  constructor(private readonly transport: Transport) {}

  /**
   * Create a chat completion. Defaults to the offline `mock-gpt` model, which
   * requires no provider key.
   *
   * @throws BudgetExceededError (402) when the tenant is over budget — raised
   *   before any provider call, so no spend occurs.
   */
  async create(
    body: ChatCompletionRequest,
    opts: ChatCallOptions = {},
  ): Promise<ChatCompletionResponse> {
    return this.transport.request<ChatCompletionResponse>(CHAT_PATH, {
      method: "POST",
      body: wireBody(body, false),
      headers: lineageHeaders(opts),
      signal: opts.signal,
      timeoutMs: opts.timeoutMs,
    });
  }

  /**
   * Create a streaming chat completion. Returns an async iterable of parsed
   * `chat.completion.chunk` objects. Iterate with `for await`.
   *
   * The budget gate still runs first, so a 402 is thrown before the stream
   * opens. Streamed requests are not auto-retried.
   *
   * @example
   * for await (const chunk of forgebench.chat.stream({ messages })) {
   *   process.stdout.write(chunk.choices[0]?.delta.content ?? "");
   * }
   */
  stream(body: ChatCompletionRequest, opts: ChatCallOptions = {}): ChatStream {
    const wire = wireBody(body, true);
    const transport = this.transport;
    async function* run(): AsyncGenerator<ChatCompletionChunk, void, void> {
      const res = await transport.stream(CHAT_PATH, {
        method: "POST",
        body: wire,
        headers: lineageHeaders(opts),
        signal: opts.signal,
        timeoutMs: opts.timeoutMs,
      });
      s.traceId = res.headers.get("x-trace-id") ?? s.traceId;
      s.callId = res.headers.get("x-call-id") ?? s.callId;
      for await (const data of parseSSE(res)) {
        let chunk: ChatCompletionChunk;
        try {
          chunk = JSON.parse(data) as ChatCompletionChunk;
        } catch {
          throw new ForgebenchConnectionError(`Malformed SSE chunk: ${data.slice(0, 120)}`);
        }
        if (!chunk.choices?.length && chunk.trace_id !== undefined && chunk.call_id !== undefined) {
          s.traceId = chunk.trace_id;
          s.callId = chunk.call_id;
          continue;
        }
        yield chunk;
      }
    }
    const s = Object.assign(run(), { traceId: null as string | null, callId: null as string | null });
    return s;
  }

  /**
   * Convenience: stream a completion and accumulate it into the full text of
   * the first choice. Useful when you want streaming transport semantics (early
   * budget rejection, lower latency to first byte) but a single final string.
   */
  async streamToText(
    body: ChatCompletionRequest,
    opts: ChatCallOptions & { onToken?: (t: string) => void } = {},
  ): Promise<string> {
    let acc = "";
    for await (const chunk of this.stream(body, opts)) {
      const piece = chunk.choices[0]?.delta.content ?? "";
      if (piece) {
        acc += piece;
        opts.onToken?.(piece);
      }
    }
    return acc;
  }
}
