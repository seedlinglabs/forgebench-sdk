/**
 * Background sender: finished observations are queued here and posted to
 * `/v1/observations/batch` on a timer or once a batch is full, so tracing
 * never makes the agent wait. Never throws into callers; a failed send is
 * logged and dropped.
 */

import type { TraceRecord, WireObservation } from "./observation.js";

/** The server takes at most this many observations per batch. */
export const MAX_BATCH = 100;

/** The server 413s a body over 1,000,000 bytes; stay under it with room for the envelope. */
export const MAX_BATCH_BYTES = 900_000;

const encoder = new TextEncoder();
const byteLength = (value: unknown): number => encoder.encode(JSON.stringify(value)).length;

export type SendBatch = (observations: WireObservation[], traces: TraceRecord[]) => Promise<void>;

export interface ExporterOptions {
  flushAt: number;
  flushIntervalMs: number;
  maxQueueSize: number;
}

export class BatchExporter {
  private readonly queue: WireObservation[] = [];
  private readonly traces = new Map<string, TraceRecord>();
  private readonly flushAt: number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sending: Promise<void> = Promise.resolve();
  private closed = false;
  dropped = 0;

  constructor(
    private readonly send: SendBatch,
    private readonly options: ExporterOptions,
  ) {
    this.flushAt = Math.min(options.flushAt, MAX_BATCH);
  }

  add(observation: WireObservation, trace?: TraceRecord): void {
    if (this.closed) return;
    if (this.queue.length >= this.options.maxQueueSize) {
      this.queue.shift();
      if (++this.dropped === 1) {
        console.warn(`forgebench tracing: queue full (${this.options.maxQueueSize}), dropping the oldest observations`);
      }
    }
    this.queue.push(observation);
    if (trace) this.traces.set(trace.id, { ...this.traces.get(trace.id), ...trace });
    if (this.queue.length >= this.flushAt) void this.flush();
    else this.schedule();
  }

  /** Send everything queued now; resolves once it has all gone out (or failed). */
  flush(): Promise<void> {
    this.clearTimer();
    this.sending = this.sending.then(() => this.drain());
    return this.sending;
  }

  /** Flush, then ignore later observations. */
  async shutdown(): Promise<void> {
    await this.flush();
    this.closed = true;
  }

  private async drain(): Promise<void> {
    while (this.queue.length || this.traces.size) {
      const [observations, traces] = this.take();
      try {
        await this.send(observations, traces);
      } catch (err) {
        console.warn(`forgebench tracing: sending ${observations.length} observations failed: ${describe(err)}`);
      }
    }
  }

  /**
   * The next batch. A trace record always travels with steps of its own
   * trace, since the server attaches trace fields to a step in the same batch.
   */
  private take(): [WireObservation[], TraceRecord[]] {
    let size = 0;
    let count = 0;
    while (count < Math.min(MAX_BATCH, this.queue.length)) {
      const n = byteLength(this.queue[count]);
      if (count > 0 && size + n > MAX_BATCH_BYTES) break;
      size += n;
      count++;
    }
    const observations = this.queue.splice(0, count);
    const inBatch = new Set(observations.map((o) => o.trace_id));
    const traces: TraceRecord[] = [];
    for (const [id, record] of this.traces) {
      if (traces.length >= MAX_BATCH) break;
      if (inBatch.has(id) || this.queue.length === 0) {
        const n = byteLength(record);
        if (traces.length > 0 && size + n > MAX_BATCH_BYTES) break;
        size += n;
        traces.push(record);
        this.traces.delete(id);
      }
    }
    return [observations, traces];
  }

  private schedule(): void {
    if (this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.options.flushIntervalMs);
    // Never keep a Node process alive just for tracing; the beforeExit hook flushes.
    (this.timer as { unref?: () => void }).unref?.();
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
