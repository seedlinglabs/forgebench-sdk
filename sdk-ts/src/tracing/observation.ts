import type { Tracer } from "./tracer.js";

export const OBSERVATION_TYPES = [
  "span",
  "agent",
  "tool",
  "chain",
  "retriever",
  "embedding",
  "evaluator",
  "guardrail",
  "event",
] as const;

/** What kind of step an observation is. `generation` is reserved for model calls. */
export type ObservationType = (typeof OBSERVATION_TYPES)[number];
export type ObservationLevel = "DEBUG" | "DEFAULT" | "WARNING" | "ERROR";

export interface ObservationUpdate {
  name?: string;
  input?: unknown;
  output?: unknown;
  /** Merged into the metadata already there. */
  metadata?: Record<string, unknown>;
  level?: ObservationLevel;
  statusMessage?: string;
}

export interface TraceUpdate {
  /** Defaults to the root step's name. */
  name?: string;
  /** Your end user. Model calls in the trace inherit it. */
  userId?: string;
  /** Groups the traces of one conversation. Model calls in the trace inherit it. */
  sessionId?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
}

/** The wire form of a trace record (`/v1/observations/batch` `traces[]`). */
export interface TraceRecord {
  id: string;
  name?: string;
  user_id?: string;
  session_id?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
  environment?: string;
  release?: string;
}

/** The wire form of one step (`/v1/observations/batch` `observations[]`). */
export interface WireObservation {
  id: string;
  trace_id: string;
  parent_observation_id: string | null;
  type: ObservationType;
  name: string;
  start_time: string;
  end_time: string;
  input: unknown;
  output: unknown;
  level: ObservationLevel;
  status_message: string | null;
  metadata: Record<string, unknown>;
}

/** One trace as this process sees it, shared by all of its observations. */
export class TraceState {
  private fields: Omit<TraceRecord, "id" | "environment" | "release"> = {};
  private dirty = false;

  constructor(
    readonly id: string,
    readonly sampled: boolean,
    readonly tracer: Tracer,
  ) {}

  update(update: TraceUpdate, metadata: Record<string, unknown> | undefined): void {
    if (update.name !== undefined) this.fields.name = update.name;
    if (update.userId !== undefined) this.fields.user_id = update.userId;
    if (update.sessionId !== undefined) this.fields.session_id = update.sessionId;
    if (update.tags?.length) {
      const known = (this.fields.tags ??= []);
      for (const tag of update.tags) if (!known.includes(tag)) known.push(tag);
    }
    if (metadata) this.fields.metadata = { ...this.fields.metadata, ...metadata };
    this.dirty = true;
  }

  get userId(): string | undefined {
    return this.fields.user_id;
  }

  get sessionId(): string | undefined {
    return this.fields.session_id;
  }

  /**
   * The trace record to send with an ending observation: when the fields
   * changed since the last one, or always for the root (`force`).
   */
  takeRecord(force: boolean, defaults: Omit<TraceRecord, "id">): TraceRecord | undefined {
    if (!this.dirty && !force) return undefined;
    this.dirty = false;
    const record: TraceRecord = { id: this.id };
    for (const [k, v] of Object.entries(defaults)) if (v !== undefined) (record as unknown as Record<string, unknown>)[k] = v;
    return Object.assign(record, this.fields);
  }
}

/**
 * One step of a trace: the agent, a function, a tool call, a retrieval...
 *
 * Sent once, when it ends; until then {@link update} can change it.
 * `recording` is false when tracing is off or the trace was sampled out —
 * every call still works, nothing is sent.
 */
export class Observation {
  readonly id: string;
  readonly startTime = new Date();
  endTime: Date | null = null;
  input: unknown = null;
  output: unknown = null;
  metadata: Record<string, unknown> = {};
  level: ObservationLevel = "DEFAULT";
  statusMessage: string | null = null;

  constructor(
    readonly trace: TraceState,
    readonly parentId: string | null,
    readonly type: ObservationType,
    public name: string,
  ) {
    this.id = randomHex(8);
  }

  get traceId(): string {
    return this.trace.id;
  }

  get recording(): boolean {
    return this.trace.sampled;
  }

  get ended(): boolean {
    return this.endTime !== null;
  }

  /** Change this step before it ends; values are captured right away. */
  update(update: ObservationUpdate): this {
    if (!this.recording) return this;
    const prepare = (v: unknown) => this.trace.tracer.prepare(v);
    if (update.name !== undefined) this.name = update.name;
    if ("input" in update) this.input = prepare(update.input);
    if ("output" in update) this.output = prepare(update.output);
    if (update.metadata) {
      const prepared = prepare(update.metadata);
      if (prepared && typeof prepared === "object" && !Array.isArray(prepared)) Object.assign(this.metadata, prepared);
    }
    if (update.level !== undefined) this.level = update.level;
    if (update.statusMessage !== undefined) this.statusMessage = this.trace.tracer.prepareText(update.statusMessage);
    return this;
  }

  /**
   * A child of this step that does NOT become the active one — for work
   * started and finished in different places. Call `end()` on it.
   */
  startObservation(name: string, options: { asType?: ObservationType; input?: unknown; metadata?: Record<string, unknown> } = {}): Observation {
    return this.trace.tracer.startObservation(name, { ...options, parent: this });
  }

  /** Finish and queue this step for sending. Later calls do nothing. */
  end(): void {
    if (this.endTime !== null) return;
    this.endTime = new Date();
    this.trace.tracer.finish(this);
  }

  toWire(): WireObservation {
    return {
      id: this.id,
      trace_id: this.trace.id,
      parent_observation_id: this.parentId,
      type: this.type,
      name: this.name,
      start_time: this.startTime.toISOString(),
      end_time: (this.endTime ?? this.startTime).toISOString(),
      input: this.input,
      output: this.output,
      level: this.level,
      status_message: this.statusMessage,
      metadata: this.metadata,
    };
  }
}

export function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes);
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (typeof c?.getRandomValues === "function") c.getRandomValues(buf);
  else for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}
