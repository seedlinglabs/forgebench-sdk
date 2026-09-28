/** Runs resource — asynchronous executions over `/v1/runs`. */

import type { Transport } from "../http.js";
import type { CreateRunRequest, RunInfo } from "../types.js";
import { ForgebenchTimeoutError } from "../errors.js";

const BASE = "/v1/runs";
const TERMINAL = new Set(["succeeded", "failed"]);

export class RunsResource {
  constructor(private readonly transport: Transport) {}

  /** Enqueue a run. Executes through the same governed (budget/audit/meter) path. */
  async create(body: CreateRunRequest): Promise<RunInfo> {
    return this.transport.request<RunInfo>(BASE, { method: "POST", body });
  }

  /** Fetch the current state of a run. */
  async retrieve(runId: string): Promise<RunInfo> {
    return this.transport.request<RunInfo>(`${BASE}/${encodeURIComponent(runId)}`, {
      method: "GET",
    });
  }

  /**
   * Poll a run until it reaches a terminal state (`succeeded`/`failed`).
   *
   * @param runId id returned by {@link create}
   * @param opts.intervalMs poll interval (default 1000)
   * @param opts.timeoutMs overall deadline (default 120000)
   * @param opts.signal abort polling early
   */
  async waitForCompletion(
    runId: string,
    opts: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
  ): Promise<RunInfo> {
    const interval = opts.intervalMs ?? 1_000;
    const deadline = Date.now() + (opts.timeoutMs ?? 120_000);
    for (;;) {
      const run = await this.retrieve(runId);
      if (TERMINAL.has(run.status)) return run;
      if (Date.now() + interval > deadline) {
        throw new ForgebenchTimeoutError(`Run ${runId} did not finish before timeout`);
      }
      await delay(interval, opts.signal);
    }
  }
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signalError(signal));
    const t = setTimeout(() => {
      cleanup();
      resolve();
    }, ms);
    const onAbort = () => {
      cleanup();
      reject(signalError(signal));
    };
    const cleanup = () => {
      clearTimeout(t);
      signal?.removeEventListener("abort", onAbort);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function signalError(signal?: AbortSignal): Error {
  const reason = signal?.reason;
  return reason instanceof Error ? reason : new Error("Aborted");
}
