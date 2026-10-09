/**
 * Low-level HTTP transport built on the platform `fetch` (Node >=18, Deno,
 * browsers, edge runtimes — no node-only deps). Handles:
 *   - auth header injection (`Authorization: Bearer <apiKey | jwt>`)
 *   - JSON encode/decode + typed error mapping
 *   - per-request timeout via AbortController (composed with caller signals)
 *   - bounded retries with exponential backoff + jitter on 429/5xx/network
 *     (writes: only 429 and connect failures — see UNSAFE_METHODS)
 *   - Server-Sent-Events line parsing for streaming chat
 */

import {
  ForgebenchConnectionError,
  ForgebenchTimeoutError,
  errorFromResponse,
} from "./errors.js";
import type { ErrorBody } from "./types.js";

type FetchLike = typeof fetch;

export interface TransportOptions {
  /** Control-plane base URL (default https://api.forgebench.ai). Trailing slash trimmed. */
  baseUrl?: string;
  /** Bearer token: an API key (`sk_...`) or a control-plane session JWT. */
  token?: string;
  /** Per-request timeout in ms (default 60000). */
  timeoutMs?: number;
  /** Max retry attempts for transient failures (default 2 => up to 3 tries). */
  maxRetries?: number;
  /** Extra headers merged into every request. */
  defaultHeaders?: Record<string, string>;
  /** Custom fetch implementation (tests / non-global-fetch runtimes). */
  fetch?: FetchLike;
}

export interface RequestOptions {
  method?: string;
  /** JSON-serializable body. */
  body?: unknown;
  /** Query parameters; undefined/null values are skipped. */
  query?: Record<
    string,
    string | number | boolean | undefined | null | ReadonlyArray<string | number | boolean>
  >;
  headers?: Record<string, string>;
  /** Per-call timeout override (ms). */
  timeoutMs?: number;
  /** Caller abort signal, composed with the internal timeout signal. */
  signal?: AbortSignal;
}

const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
const USER_AGENT = "forgebench-sdk-ts/1.1.0";
/**
 * Writes (a chat call bills and audits) are retried only when the server
 * cannot have processed them: a 429, or a failure to connect. Never on a 5xx
 * or a dropped connection — the control plane has no idempotency key.
 */
const UNSAFE_METHODS = new Set(["POST", "PATCH"]);
const CONNECT_ERROR_CODES = new Set([
  "ECONNREFUSED",
  "ENOTFOUND",
  "EAI_AGAIN",
  "UND_ERR_CONNECT_TIMEOUT",
]);

/** True when `err` (or a cause up the chain) is a connect-phase failure. */
function isConnectFailure(err: unknown): boolean {
  let e: unknown = err;
  for (let i = 0; i < 4 && e && typeof e === "object"; i++) {
    const code = (e as { code?: unknown }).code;
    if (typeof code === "string" && CONNECT_ERROR_CODES.has(code)) return true;
    e = (e as { cause?: unknown }).cause;
  }
  return false;
}
export const DEFAULT_BASE_URL = "https://api.forgebench.ai";

export class Transport {
  private readonly baseUrl: string;
  private readonly token?: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly defaultHeaders: Record<string, string>;
  private readonly fetchImpl: FetchLike;

  constructor(opts: TransportOptions) {
    this.baseUrl = (opts.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.token = opts.token;
    this.timeoutMs = opts.timeoutMs ?? 60_000;
    this.maxRetries = opts.maxRetries ?? 2;
    this.defaultHeaders = opts.defaultHeaders ?? {};
    const f = opts.fetch ?? globalThis.fetch;
    if (typeof f !== "function") {
      throw new Error(
        "Forgebench: global fetch is not available. Use Node >=18, or pass a `fetch` implementation.",
      );
    }
    this.fetchImpl = f.bind(globalThis);
  }

  private buildUrl(path: string, query?: RequestOptions["query"]): string {
    const url = new URL(this.baseUrl + (path.startsWith("/") ? path : `/${path}`));
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v === undefined || v === null) continue;
        // Arrays repeat the key (`tags=a&tags=b`), never `a,b`.
        if (Array.isArray(v)) for (const item of v) url.searchParams.append(k, String(item));
        else url.searchParams.set(k, String(v));
      }
    }
    return url.toString();
  }

  private headers(extra?: Record<string, string>, hasBody = false): Headers {
    const h = new Headers(this.defaultHeaders);
    h.set("Accept", "application/json");
    h.set("User-Agent", USER_AGENT);
    if (this.token) h.set("Authorization", `Bearer ${this.token}`);
    if (hasBody) h.set("Content-Type", "application/json");
    if (extra) for (const [k, v] of Object.entries(extra)) h.set(k, v);
    return h;
  }

  /** Perform a single fetch with timeout + abort composition. */
  private async fetchOnce(
    url: string,
    init: RequestInit,
    timeoutMs: number,
    callerSignal?: AbortSignal,
  ): Promise<Response> {
    const controller = new AbortController();
    const onAbort = () => controller.abort(callerSignal?.reason);
    if (callerSignal) {
      if (callerSignal.aborted) controller.abort(callerSignal.reason);
      else callerSignal.addEventListener("abort", onAbort, { once: true });
    }
    const timer = setTimeout(() => controller.abort(new Error("timeout")), timeoutMs);
    try {
      return await this.fetchImpl(url, { ...init, signal: controller.signal });
    } catch (err) {
      if (controller.signal.aborted && !callerSignal?.aborted) {
        throw new ForgebenchTimeoutError(`Request timed out after ${timeoutMs}ms`, err);
      }
      throw new ForgebenchConnectionError(
        err instanceof Error ? err.message : "Network request failed",
        err,
      );
    } finally {
      clearTimeout(timer);
      if (callerSignal) callerSignal.removeEventListener("abort", onAbort);
    }
  }

  /** JSON request returning a typed body. Retries transient failures. */
  async request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const res = await this.send(path, opts, false);
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    if (!text) return undefined as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new ForgebenchConnectionError(`Failed to parse JSON response from ${path}`);
    }
  }

  /**
   * Streaming request: returns the raw `Response` whose body is an SSE stream.
   * Callers pipe it through {@link parseSSE}. Streaming requests are not retried
   * after the first byte (no idempotent replay of a partial stream).
   */
  async stream(path: string, opts: RequestOptions = {}): Promise<Response> {
    return this.send(path, opts, true);
  }

  private async send(path: string, opts: RequestOptions, streaming: boolean): Promise<Response> {
    const method = opts.method ?? (opts.body !== undefined ? "POST" : "GET");
    const url = this.buildUrl(path, opts.query);
    const hasBody = opts.body !== undefined && opts.body !== null;
    const headers = this.headers(opts.headers, hasBody);
    if (streaming) headers.set("Accept", "text/event-stream");
    const init: RequestInit = {
      method,
      headers,
      body: hasBody ? JSON.stringify(opts.body) : undefined,
    };
    const timeoutMs = opts.timeoutMs ?? this.timeoutMs;

    const unsafe = UNSAFE_METHODS.has(method.toUpperCase());
    let lastErr: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const res = await this.fetchOnce(url, init, timeoutMs, opts.signal);
        if (res.ok) return res;

        const retryable = unsafe ? res.status === 429 : RETRYABLE_STATUS.has(res.status);
        const canRetry = !streaming && retryable && attempt < this.maxRetries;
        if (canRetry) {
          await sleep(backoffMs(attempt, res.headers.get("retry-after")));
          // Drain the body so the connection can be reused.
          await res.text().catch(() => undefined);
          continue;
        }
        throw await this.toApiError(res);
      } catch (err) {
        lastErr = err;
        const transient =
          err instanceof ForgebenchConnectionError &&
          !(err instanceof ForgebenchTimeoutError) &&
          (!unsafe || isConnectFailure(err));
        if (transient && !streaming && attempt < this.maxRetries) {
          await sleep(backoffMs(attempt, null));
          continue;
        }
        throw err;
      }
    }
    // Unreachable in practice; satisfies the type checker.
    throw lastErr instanceof Error ? lastErr : new ForgebenchConnectionError("Request failed");
  }

  private async toApiError(res: Response) {
    const requestId =
      res.headers.get("x-request-id") ?? res.headers.get("x-correlation-id") ?? null;
    const raw = await res.text().catch(() => "");
    let body: ErrorBody | string | null = raw || null;
    if (raw) {
      try {
        body = JSON.parse(raw) as ErrorBody;
      } catch {
        body = raw;
      }
    }
    return errorFromResponse(res.status, body, requestId);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Exponential backoff with full jitter; honors a numeric Retry-After header. */
function backoffMs(attempt: number, retryAfter: string | null): number {
  if (retryAfter) {
    const secs = Number(retryAfter);
    if (Number.isFinite(secs) && secs >= 0) return Math.min(secs * 1000, 20_000);
  }
  const base = Math.min(500 * 2 ** attempt, 8_000);
  return Math.floor(base / 2 + Math.random() * (base / 2));
}

/**
 * Parse a `text/event-stream` Response body into an async iterable of decoded
 * `data:` payloads. Strips SSE framing and stops at the `[DONE]` sentinel.
 * Runtime-agnostic: uses the WHATWG ReadableStream reader.
 */
export async function* parseSSE(res: Response): AsyncGenerator<string, void, void> {
  if (!res.body) throw new ForgebenchConnectionError("Streaming response has no body");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep: number;
      // SSE events are separated by a blank line. Handle \n\n and \r\n\r\n.
      while ((sep = indexOfEventBoundary(buffer)) !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep).replace(/^(\r?\n)+/, "");
        const data = extractData(rawEvent);
        if (data === null) continue;
        if (data === "[DONE]") return;
        yield data;
      }
    }
    // Flush any trailing event without a terminating blank line.
    const tail = extractData(buffer);
    if (tail !== null && tail !== "[DONE]") yield tail;
  } finally {
    reader.releaseLock();
  }
}

function indexOfEventBoundary(buf: string): number {
  const a = buf.indexOf("\n\n");
  const b = buf.indexOf("\r\n\r\n");
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

/** Concatenate the `data:` lines of one SSE event; null if there are none. */
function extractData(rawEvent: string): string | null {
  const lines = rawEvent.split(/\r?\n/);
  const parts: string[] = [];
  for (const line of lines) {
    if (line.startsWith("data:")) parts.push(line.slice(5).replace(/^ /, ""));
  }
  return parts.length ? parts.join("\n") : null;
}
