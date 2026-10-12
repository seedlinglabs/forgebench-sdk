import { newTraceId, validateTraceId } from "../trace.js";
import type { TracingConfig } from "./config.js";
import { resolveTracingConfig } from "./config.js";
import { createContextStore } from "./context.js";
import { BatchExporter } from "./exporter.js";
import type { TraceRecord, WireObservation } from "./observation.js";
import {
  OBSERVATION_TYPES,
  Observation,
  TraceState,
  type ObservationType,
  type ObservationUpdate,
  type TraceUpdate,
} from "./observation.js";
import { capped, toJsonable } from "./serialize.js";

export const BATCH_PATH = "/v1/observations/batch";
const MAX_STATUS_CHARS = 1_000;

export interface StartOptions {
  asType?: ObservationType;
  input?: unknown;
  metadata?: Record<string, unknown>;
  /** Start a root in this trace (to continue a trace begun elsewhere). */
  traceId?: string;
}

export interface ObserveOptions {
  /** Defaults to the function's name. */
  name?: string;
  asType?: ObservationType;
  /** Record the arguments (default true). */
  captureInput?: boolean;
  /** Record the return value (default true). */
  captureOutput?: boolean;
}

export interface BatchPayload {
  observations: WireObservation[];
  traces: TraceRecord[];
  sdk: { name: string; version: string };
}

export interface BatchReply {
  accepted?: number;
  rejected?: { id?: string | null; reason: string }[];
  dropped_reason?: string | null;
}

const store = createContextStore<Observation>();

// The tracer module-level functions use to START a trace, worked out from the
// clients that are OPEN right now, by credential. One credential: its newest
// open client. Several: ambiguous — guessing could send one workspace's steps
// with another's key — so module-level tracing refuses to start traces and
// asks for a client-bound `forgebench.observe(...)`. Shutting a client down
// takes it out, so the answer follows the clients actually in use.
const live = new Map<string, Tracer[]>();

/** The observation the running code is inside of, if any. */
export function activeObservation(): Observation | undefined {
  return store.getStore();
}

/**
 * Count `tracer` as an open client. `identity` names its credential (base URL
 * + key); clients sharing one are interchangeable.
 */
export function registerDefaultTracer(tracer: Tracer, identity: string): void {
  const tracers = live.get(identity) ?? [];
  tracers.push(tracer);
  live.set(identity, tracers);
}

/** `tracer`'s client is shut down: it no longer counts. */
function releaseDefaultTracer(tracer: Tracer): void {
  for (const [identity, tracers] of live) {
    const at = tracers.indexOf(tracer);
    if (at >= 0) tracers.splice(at, 1);
    if (!tracers.length) live.delete(identity);
  }
}

/** The newest open client of the only credential in use, and whether there are several. */
function defaultTracer(): { tracer: Tracer | undefined; ambiguous: boolean } {
  const first = live.values().next().value as Tracer[] | undefined;
  return { tracer: first?.at(-1), ambiguous: live.size > 1 };
}

/** Records observations for one client and sends them in the background. */
export class Tracer {
  private exporter: BatchExporter | undefined;
  private readonly warned = new Set<string>();

  constructor(
    readonly config: TracingConfig,
    private readonly sendBatch: (payload: BatchPayload) => Promise<BatchReply | undefined>,
    private readonly sdk: { name: string; version: string } = { name: "forgebench-typescript", version: "0" },
  ) {}

  /**
   * A new observation under `parent` (default: the active one), or the root of
   * a new trace. It does not become active — call `end()` on it.
   */
  startObservation(name: string, options: StartOptions & { parent?: Observation } = {}): Observation {
    const asType = options.asType ?? "span";
    if (!(OBSERVATION_TYPES as readonly string[]).includes(asType)) {
      throw new RangeError(`asType must be one of ${OBSERVATION_TYPES.join(", ")}; got ${String(asType)}`);
    }
    // Steps of ANOTHER client's trace are not nested under: each client only
    // ever sends its own traces, with its own key.
    const active = store.getStore();
    const parent =
      options.traceId === undefined ? (options.parent ?? (active?.trace.tracer === this ? active : undefined)) : undefined;
    let trace: TraceState;
    if (parent) {
      trace = parent.trace;
    } else {
      validateTraceId(options.traceId);
      trace = new TraceState(options.traceId ?? newTraceId(), this.sample(), this);
    }
    const observation = new Observation(trace, parent?.id ?? null, asType, String(name));
    if (options.input !== undefined) observation.update({ input: options.input });
    if (options.metadata) observation.update({ metadata: options.metadata });
    return observation;
  }

  /**
   * Run `fn` with a new observation as the active one: observations and model
   * calls made inside nest under it. Returns what `fn` returns (a promise is
   * awaited before the step ends). An error is recorded as `level: "ERROR"`
   * and re-thrown.
   */
  startActiveObservation<R>(name: string, fn: (observation: Observation) => R, options: StartOptions = {}): R {
    const observation = this.startObservation(name, options);
    return store.run(observation, () => {
      let result: R;
      try {
        result = fn(observation);
      } catch (err) {
        fail(observation, err);
        observation.end();
        throw err;
      }
      if (isPromiseLike(result)) {
        return Promise.resolve(result).then(
          (value) => {
            observation.end();
            return value;
          },
          (err: unknown) => {
            fail(observation, err);
            observation.end();
            throw err;
          },
        ) as R;
      }
      observation.end();
      return result;
    });
  }

  /** Wrap `fn` so every call is recorded as an observation. See {@link observe}. */
  observe<F extends (...args: never[]) => unknown>(fn: F, options: ObserveOptions = {}): F {
    return wrapObserved(fn, options, () => this);
  }

  updateActiveObservation(update: ObservationUpdate): void {
    this.activeOrWarn("updateActiveObservation")?.update(update);
  }

  updateActiveTrace(update: TraceUpdate): void {
    const active = this.activeOrWarn("updateActiveTrace");
    if (!active?.recording) return;
    const metadata = update.metadata ? this.prepare(update.metadata) : undefined;
    active.trace.update(update, isRecord(metadata) ? metadata : undefined);
  }

  /** Serialise, mask and size-cap one input / output / metadata value. */
  prepare(value: unknown): unknown {
    let data = toJsonable(value);
    const mask = this.config.mask;
    if (mask) {
      try {
        data = toJsonable(mask(data));
      } catch (err) {
        this.warnOnce("mask", `forgebench tracing: mask failed, value withheld: ${describe(err)}`);
        data = "[withheld: mask failed]";
      }
    }
    return capped(data);
  }

  /**
   * A status message through the mask (error messages often carry the very
   * values the mask exists for), cut to the server's limit.
   */
  prepareText(text: string): string {
    let out = text;
    const mask = this.config.mask;
    if (mask) {
      try {
        const masked = mask(text);
        out = typeof masked === "string" ? masked : String(masked);
      } catch (err) {
        this.warnOnce("mask", `forgebench tracing: mask failed, value withheld: ${describe(err)}`);
        return "[withheld: mask failed]";
      }
    }
    return out.slice(0, MAX_STATUS_CHARS);
  }

  /** Queue an ended observation (and, when due, its trace record). */
  finish(observation: Observation): void {
    if (!observation.recording) return;
    const trace = observation.trace.takeRecord(observation.parentId === null, {
      name: observation.name,
      environment: this.config.environment,
      release: this.config.release,
    });
    this.exporterOrStart().add(observation.toWire(), trace);
  }

  flush(): Promise<void> {
    return this.exporter?.flush() ?? Promise.resolve();
  }

  async shutdown(): Promise<void> {
    releaseDefaultTracer(this);
    if (this.exporter) liveExporters.delete(this.exporter);
    await this.exporter?.shutdown();
  }

  private sample(): boolean {
    return this.config.enabled && Math.random() < this.config.sampleRate;
  }

  private exporterOrStart(): BatchExporter {
    if (!this.exporter) {
      this.exporter = new BatchExporter((o, t) => this.send(o, t), this.config);
      flushBeforeExit(this.exporter);
    }
    return this.exporter;
  }

  private async send(observations: WireObservation[], traces: TraceRecord[]): Promise<void> {
    const reply = (await this.sendBatch({ observations, traces, sdk: this.sdk })) ?? {};
    for (const rejection of reply.rejected ?? []) {
      this.warnOnce(
        `rejected:${rejection.reason}`,
        `forgebench tracing: the server rejected observation ${rejection.id ?? "?"} (${rejection.reason})`,
      );
    }
    if (reply.dropped_reason) {
      this.warnOnce(`dropped:${reply.dropped_reason}`, `forgebench tracing: traces are not stored (${reply.dropped_reason})`);
    }
  }

  private activeOrWarn(method: string): Observation | undefined {
    const active = store.getStore();
    if (!active) this.warnOnce(method, `forgebench tracing: ${method}() called outside any observation; ignored`);
    return active;
  }

  /** @internal */
  warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    console.warn(message);
  }
}

// One process-wide hook flushes every live client when Node is about to exit,
// so a script that never calls flush() still sends its last steps.
const liveExporters = new Set<BatchExporter>();
let exitHookInstalled = false;

function flushBeforeExit(exporter: BatchExporter): void {
  liveExporters.add(exporter);
  if (exitHookInstalled) return;
  const proc = (globalThis as { process?: { on?: (event: string, fn: () => void) => void } }).process;
  if (typeof proc?.on !== "function") return;
  proc.on("beforeExit", () => {
    for (const live of liveExporters) void live.flush();
  });
  exitHookInstalled = true;
}

/** Records nothing; used by module-level functions before any client exists. */
const noopTracer = new Tracer(resolveTracingConfig({ enabled: false }), async () => undefined);

/** The active trace's tracer, else the default client's, else a no-op one. */
export function resolveTracer(): Tracer {
  const active = store.getStore();
  if (active) return active.trace.tracer;
  const { tracer, ambiguous } = defaultTracer();
  if (ambiguous) {
    tracer?.warnOnce(
      "ambiguous-default",
      "forgebench tracing: open clients for several API keys exist, so module-level observe() " +
        "does not start traces; use forgebench.observe(...) instead",
    );
    return noopTracer;
  }
  return tracer ?? noopTracer;
}

export function wrapObserved<F extends (...args: never[]) => unknown>(
  fn: F,
  options: ObserveOptions,
  resolve: () => Tracer,
): F {
  const name = options.name ?? (fn.name || "anonymous");
  const captureInput = options.captureInput ?? true;
  const captureOutput = options.captureOutput ?? true;
  const wrapped = function (this: unknown, ...args: Parameters<F>): unknown {
    return resolve().startActiveObservation(
      name,
      (observation) => {
        if (captureInput && observation.recording) {
          observation.update({ input: args.length === 1 ? args[0] : args });
        }
        const result = fn.apply(this, args);
        if (!captureOutput || !observation.recording) return result;
        if (isPromiseLike(result)) {
          return Promise.resolve(result).then((value) => {
            observation.update({ output: value });
            return value;
          });
        }
        observation.update({ output: result });
        return result;
      },
      { asType: options.asType },
    );
  };
  Object.defineProperty(wrapped, "name", { value: fn.name });
  return wrapped as unknown as F;
}

/**
 * A new 32-hex trace id; with `seed` (e.g. your request id) always the same
 * one (SHA-256 of the seed, via Web Crypto).
 */
export async function createTraceId(seed?: string): Promise<string> {
  if (seed === undefined) return newTraceId();
  const subtle = (globalThis as { crypto?: Crypto }).crypto?.subtle;
  if (!subtle) throw new Error("createTraceId(seed) needs Web Crypto (globalThis.crypto.subtle)");
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(seed));
  return Array.from(new Uint8Array(digest).slice(0, 16), (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * A governed call made inside an observed step joins that step's trace, as
 * its child: the trace id, the trace's user / session as defaults, and the
 * parent observation id for the header. An explicit `trace_id` of another
 * trace wins, and the call is then not nested.
 */
export function currentTraceScope(
  tracer: Tracer,
  traceId: string | null | undefined,
): { traceId: string; parentObservationId: string; userId?: string; sessionId?: string } | undefined {
  const active = store.getStore();
  // Only a step of THIS client's tracer: another client's trace (another
  // workspace) never lends its ids or user.
  if (!active?.recording || active.trace.tracer !== tracer) return undefined;
  if (traceId && traceId !== active.traceId) return undefined;
  return {
    traceId: active.traceId,
    parentObservationId: active.id,
    userId: active.trace.userId,
    sessionId: active.trace.sessionId,
  };
}

function fail(observation: Observation, err: unknown): void {
  const message = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
  observation.update({ level: "ERROR", statusMessage: message.slice(0, MAX_STATUS_CHARS) });
}

function isPromiseLike(value: unknown): value is PromiseLike<unknown> {
  return typeof (value as { then?: unknown } | null)?.then === "function";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
