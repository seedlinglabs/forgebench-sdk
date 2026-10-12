/**
 * How a client records and sends traces. Every field is optional; an unset
 * field falls back to its environment variable (where `process.env` exists),
 * then to the default.
 */
export interface TracingOptions {
  /** Off makes every tracing call a no-op; model calls still work. `FORGEBENCH_TRACING_ENABLED`, default true. */
  enabled?: boolean;
  /** Share of traces kept (0–1), decided once per trace. `FORGEBENCH_SAMPLE_RATE`, default 1. */
  sampleRate?: number;
  /** Stamped on every trace, for filtering. `FORGEBENCH_ENVIRONMENT`. */
  environment?: string;
  /** Your app version, stamped on every trace. `FORGEBENCH_RELEASE`. */
  release?: string;
  /** Runs on every input, output and metadata before it leaves the process. */
  mask?: (data: unknown) => unknown;
  /** Send once this many steps are waiting (default 50, at most 100). */
  flushAt?: number;
  /** ...or this long after the first one (default 1000 ms). */
  flushIntervalMs?: number;
  /** Beyond this many waiting steps the oldest are dropped (default 10000). */
  maxQueueSize?: number;
}

export interface TracingConfig {
  enabled: boolean;
  sampleRate: number;
  environment?: string;
  release?: string;
  mask?: (data: unknown) => unknown;
  flushAt: number;
  flushIntervalMs: number;
  maxQueueSize: number;
}

export function resolveTracingConfig(options: TracingOptions = {}): TracingConfig {
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};
  const enabledEnv = env.FORGEBENCH_TRACING_ENABLED?.trim().toLowerCase();
  const config: TracingConfig = {
    enabled: options.enabled ?? !["0", "false", "no", "off"].includes(enabledEnv ?? ""),
    sampleRate: options.sampleRate ?? envSampleRate(env.FORGEBENCH_SAMPLE_RATE),
    environment: options.environment ?? (env.FORGEBENCH_ENVIRONMENT || undefined),
    release: options.release ?? (env.FORGEBENCH_RELEASE || undefined),
    mask: options.mask,
    flushAt: options.flushAt ?? 50,
    flushIntervalMs: options.flushIntervalMs ?? 1_000,
    maxQueueSize: options.maxQueueSize ?? 10_000,
  };
  // Only explicit options can throw here; environment values were checked above.
  if (!(config.sampleRate >= 0 && config.sampleRate <= 1)) {
    throw new RangeError("tracing.sampleRate must be between 0 and 1");
  }
  if (!(config.flushAt >= 1 && config.flushIntervalMs > 0 && config.maxQueueSize >= 1)) {
    throw new RangeError("tracing.flushAt, flushIntervalMs and maxQueueSize must be positive");
  }
  return config;
}

/**
 * `FORGEBENCH_SAMPLE_RATE`, or 1 when it is unset, blank or not a rate — a
 * typo in the environment is warned about, never fatal (and never a silent 0).
 */
function envSampleRate(raw: string | undefined): number {
  const text = raw?.trim();
  if (!text) return 1;
  const rate = Number(text);
  if (rate >= 0 && rate <= 1) return rate;
  console.warn(`forgebench tracing: ignoring FORGEBENCH_SAMPLE_RATE=${JSON.stringify(raw)} (want 0-1); using 1`);
  return 1;
}
