/**
 * Workflows — `/v1/workflows`: a named group of agents (steps) registered and
 * observed together; plus `client.workflow(slug, keys)` to drive one, where
 * every step calls with its own key and each call names the previous call of
 * the run as its parent (`X-Parent-Call`).
 */

import type { Transport } from "../http.js";
import type { ChatResource, ChatStream } from "./chat.js";
import { ForgebenchError } from "../errors.js";
import type {
  AddWorkflowStepRequest,
  ChatCompletionRequest,
  ChatCompletionResponse,
  RegisterWorkflowRequest,
  WorkflowChecks,
  WorkflowCreated,
  WorkflowDetail,
  WorkflowEdgeInfo,
  WorkflowInfo,
  WorkflowMutationResult,
} from "../types.js";
import type { ChatCallOptions } from "./chat.js";

const BASE = "/v1/workflows";
const p = (s: string) => encodeURIComponent(s);

export class WorkflowsResource {
  constructor(private readonly transport: Transport) {}

  list(): Promise<WorkflowInfo[]> {
    return this.transport.request<WorkflowInfo[]>(BASE, { method: "GET" });
  }

  /** Steps, declared and observed edges. `days` (1-365) is the window observed traffic is read over. */
  get(slug: string, opts: { days?: number } = {}): Promise<WorkflowDetail> {
    return this.transport.request<WorkflowDetail>(`${BASE}/${p(slug)}`, { method: "GET", query: { days: opts.days } });
  }

  /** Is it wired, threaded (X-Parent-Call) and seeing traffic? Each non-passing check has a `fix`. */
  checks(slug: string): Promise<WorkflowChecks> {
    return this.transport.request<WorkflowChecks>(`${BASE}/${p(slug)}/checks`, { method: "GET" });
  }

  /**
   * Register atomically: every step's agent and key, the steps and the edges, or nothing.
   * Raw keys are in `credentials[].credential.api_key`, ONCE.
   */
  register(body: RegisterWorkflowRequest): Promise<WorkflowCreated> {
    const { issue_credential, ...rest } = body;
    return this.transport.request<WorkflowCreated>(BASE, {
      method: "POST",
      body: issue_credential === false ? { ...rest, issue_credential: false } : rest,
    });
  }

  update(slug: string, body: { name?: string; description?: string | null }): Promise<WorkflowInfo> {
    return this.transport.request<WorkflowInfo>(`${BASE}/${p(slug)}`, { method: "PATCH", body });
  }

  /** Archive it and release its steps. Agents and keys are untouched. */
  archive(slug: string): Promise<WorkflowMutationResult> {
    return this.transport.request<WorkflowMutationResult>(`${BASE}/${p(slug)}`, { method: "DELETE" });
  }

  addStep(slug: string, body: AddWorkflowStepRequest): Promise<WorkflowCreated> {
    return this.transport.request<WorkflowCreated>(`${BASE}/${p(slug)}/steps`, { method: "POST", body });
  }

  /** Detach a step (its edges go with it). The agent is untouched. */
  removeStep(slug: string, step: string): Promise<WorkflowMutationResult> {
    return this.transport.request<WorkflowMutationResult>(`${BASE}/${p(slug)}/steps/${p(step)}`, { method: "DELETE" });
  }

  addEdge(slug: string, edge: { from: string; to: string; kind: "sequence" | "task" }): Promise<WorkflowEdgeInfo> {
    return this.transport.request<WorkflowEdgeInfo>(`${BASE}/${p(slug)}/edges`, { method: "POST", body: edge });
  }

  /** Remove a declared edge. A task edge's agent binding is kept unless `revokeBinding` is true. */
  removeEdge(slug: string, edgeId: string, opts: { revokeBinding?: boolean } = {}): Promise<WorkflowMutationResult> {
    return this.transport.request<WorkflowMutationResult>(`${BASE}/${p(slug)}/edges/${p(edgeId)}`, {
      method: "DELETE",
      query: opts.revokeBinding ? { revoke_binding: "true" } : undefined,
    });
  }
}

/** Per-call options of a step; `parentCallId` and `headers` are set by the run. */
export type StepCallOptions = Omit<ChatCallOptions, "parentCallId" | "headers">;

export class WorkflowStep {
  constructor(
    private readonly run: WorkflowRun,
    private readonly chatResource: ChatResource,
    readonly name: string,
    private readonly key: string,
  ) {}

  private opts(opts: StepCallOptions): ChatCallOptions {
    return { ...opts, parentCallId: this.run.lastCallId, headers: { Authorization: `Bearer ${this.key}` } };
  }

  /**
   * A governed chat call with THIS step's key, recorded as the child of the
   * run's previous call (the first call is a root).
   */
  async chat(body: ChatCompletionRequest, opts: StepCallOptions = {}): Promise<ChatCompletionResponse> {
    const res = await this.chatResource.create(body, this.opts(opts));
    this.run.record(res);
    return res;
  }

  /**
   * A streamed call. It becomes the next call's parent once its `callId` is
   * known, so iterate it to the end before the next step.
   */
  stream(body: ChatCompletionRequest, opts: StepCallOptions = {}): ChatStream {
    const s = this.chatResource.stream(body, this.opts(opts));
    this.run.record(s);
    return s;
  }
}

/** One execution of a workflow. `root_call_id` is the first call's id. */
export class WorkflowRun {
  private readonly calls: Array<{ call_id?: string | null } | { callId: string | null }> = [];

  constructor(
    readonly slug: string,
    private readonly chatResource: ChatResource,
    private readonly keys: Readonly<Record<string, string>>,
  ) {}

  step(name: string): WorkflowStep {
    const key = this.keys[name];
    if (!key) {
      throw new ForgebenchError(`workflow ${this.slug}: no api key for step ${name} (keys: ${Object.keys(this.keys).join(", ")})`);
    }
    return new WorkflowStep(this, this.chatResource, name, key);
  }

  /** @internal */
  record(call: { call_id?: string | null } | { callId: string | null }): void {
    this.calls.push(call);
  }

  private static id(c: { call_id?: string | null } | { callId: string | null }): string | null {
    return ("call_id" in c ? c.call_id : (c as { callId: string | null }).callId) ?? null;
  }

  /** The `call_id` of every call so far, in order (a stream's, once known). */
  get callIds(): string[] {
    return this.calls.map(WorkflowRun.id).filter((x): x is string => !!x);
  }

  /** The newest call's id that is known: a call without one does not break the chain. */
  get lastCallId(): string | null {
    for (let i = this.calls.length - 1; i >= 0; i--) {
      const id = WorkflowRun.id(this.calls[i]!);
      if (id) return id;
    }
    return null;
  }

  get rootCallId(): string | null {
    return this.calls.length ? WorkflowRun.id(this.calls[0]!) : null;
  }
}

/** `forgebench.workflow(slug, keys)`: a registered workflow with one key per step. */
export class Workflow {
  readonly steps: string[];

  constructor(
    readonly slug: string,
    private readonly chatResource: ChatResource,
    private readonly keys: Readonly<Record<string, string>>,
  ) {
    const bad = Object.entries(keys).filter(([, k]) => !k).map(([n]) => n);
    if (bad.length) throw new ForgebenchError(`workflow ${slug}: no api key for step(s) ${bad.join(", ")}`);
    this.steps = Object.keys(keys);
  }

  run(): WorkflowRun {
    return new WorkflowRun(this.slug, this.chatResource, this.keys);
  }
}
