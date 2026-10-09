/**
 * `forgebench.langfuse` — your workspace's Langfuse data through the governed
 * passthrough (`/v1/langfuse/*`). The control plane holds the Langfuse keys
 * and exposes an allowlisted subset of Langfuse's public API (pinned OpenAPI).
 * Reads need admin, `traces:read` or `observability:view`; writes (scores,
 * comments, prompts, datasets, dataset items, annotation-queue items) also
 * need builder+ and are redacted + audited server side. Anything else is 404.
 *
 * Thin by design: query params and bodies use Langfuse's own camelCase names,
 * responses are Langfuse's JSON unchanged (typed `unknown`; narrow as needed).
 */

import type { Transport, RequestOptions } from "../http.js";

const PREFIX = "/v1/langfuse";
type Query = RequestOptions["query"];
type Body = Record<string, unknown>;

/** One path segment, percent-encoded (prompt/dataset names may contain `/`). */
const seg = (v: string): string => encodeURIComponent(v);

class Group {
  constructor(protected readonly t: Transport) {}
  protected get(path: string, query?: Query): Promise<unknown> {
    return this.t.request(PREFIX + path, { method: "GET", query });
  }
  protected send(method: "POST" | "PATCH", path: string, body: Body): Promise<unknown> {
    return this.t.request(PREFIX + path, { method, body });
  }
}

class Traces extends Group {
  list(query?: Query) { return this.get("/traces", query); }
  retrieve(traceId: string) { return this.get(`/traces/${seg(traceId)}`); }
}

class Sessions extends Group {
  list(query?: Query) { return this.get("/sessions", query); }
  retrieve(sessionId: string) { return this.get(`/sessions/${seg(sessionId)}`); }
}

class Observations extends Group {
  list(query?: Query) { return this.get("/observations", query); }
  listV2(query?: Query) { return this.get("/v2/observations", query); }
  retrieve(observationId: string) { return this.get(`/observations/${seg(observationId)}`); }
}

class Metrics extends Group {
  /** Langfuse metrics query (v1 `/metrics`). */
  query(query: Body) { return this.get("/metrics", { query: JSON.stringify(query) }); }
  queryV2(query: Body) { return this.get("/v2/metrics", { query: JSON.stringify(query) }); }
}

class Scores extends Group {
  list(query?: Query) { return this.get("/v2/scores", query); }
  listV3(query?: Query) { return this.get("/v3/scores", query); }
  retrieve(scoreId: string) { return this.get(`/v2/scores/${seg(scoreId)}`); }
  /** e.g. `{ traceId, name: "helpful", value: 1, comment }`. */
  create(body: Body) { return this.send("POST", "/scores", body); }
}

class ScoreConfigs extends Group {
  list(query?: Query) { return this.get("/score-configs", query); }
  retrieve(configId: string) { return this.get(`/score-configs/${seg(configId)}`); }
}

class Prompts extends Group {
  list(query?: Query) { return this.get("/v2/prompts", query); }
  /** `query`: `{ version }` or `{ label }`. */
  retrieve(name: string, query?: Query) { return this.get(`/v2/prompts/${seg(name)}`, query); }
  create(body: Body) { return this.send("POST", "/v2/prompts", body); }
}

class Datasets extends Group {
  list(query?: Query) { return this.get("/v2/datasets", query); }
  retrieve(name: string) { return this.get(`/v2/datasets/${seg(name)}`); }
  create(body: Body) { return this.send("POST", "/v2/datasets", body); }
  listRuns(name: string, query?: Query) { return this.get(`/datasets/${seg(name)}/runs`, query); }
  retrieveRun(name: string, runName: string) { return this.get(`/datasets/${seg(name)}/runs/${seg(runName)}`); }
}

class DatasetItems extends Group {
  list(query?: Query) { return this.get("/dataset-items", query); }
  retrieve(id: string) { return this.get(`/dataset-items/${seg(id)}`); }
  create(body: Body) { return this.send("POST", "/dataset-items", body); }
}

class DatasetRunItems extends Group {
  list(query?: Query) { return this.get("/dataset-run-items", query); }
}

class Comments extends Group {
  list(query?: Query) { return this.get("/comments", query); }
  retrieve(commentId: string) { return this.get(`/comments/${seg(commentId)}`); }
  create(body: Body) { return this.send("POST", "/comments", body); }
}

class AnnotationQueues extends Group {
  list(query?: Query) { return this.get("/annotation-queues", query); }
  retrieve(queueId: string) { return this.get(`/annotation-queues/${seg(queueId)}`); }
  listItems(queueId: string, query?: Query) { return this.get(`/annotation-queues/${seg(queueId)}/items`, query); }
  retrieveItem(queueId: string, itemId: string) {
    return this.get(`/annotation-queues/${seg(queueId)}/items/${seg(itemId)}`);
  }
  createItem(queueId: string, body: Body) { return this.send("POST", `/annotation-queues/${seg(queueId)}/items`, body); }
  updateItem(queueId: string, itemId: string, body: Body) {
    return this.send("PATCH", `/annotation-queues/${seg(queueId)}/items/${seg(itemId)}`, body);
  }
}

export class LangfuseResource {
  readonly traces: Traces;
  readonly sessions: Sessions;
  readonly observations: Observations;
  readonly metrics: Metrics;
  readonly scores: Scores;
  readonly scoreConfigs: ScoreConfigs;
  readonly prompts: Prompts;
  readonly datasets: Datasets;
  readonly datasetItems: DatasetItems;
  readonly datasetRunItems: DatasetRunItems;
  readonly comments: Comments;
  readonly annotationQueues: AnnotationQueues;

  constructor(private readonly transport: Transport) {
    this.traces = new Traces(transport);
    this.sessions = new Sessions(transport);
    this.observations = new Observations(transport);
    this.metrics = new Metrics(transport);
    this.scores = new Scores(transport);
    this.scoreConfigs = new ScoreConfigs(transport);
    this.prompts = new Prompts(transport);
    this.datasets = new Datasets(transport);
    this.datasetItems = new DatasetItems(transport);
    this.datasetRunItems = new DatasetRunItems(transport);
    this.comments = new Comments(transport);
    this.annotationQueues = new AnnotationQueues(transport);
  }

  /** Escape hatch for an allowlisted op without a wrapper; `path` is Langfuse's minus `/api/public`. */
  request(method: string, path: string, opts: { query?: Query; body?: Body } = {}): Promise<unknown> {
    return this.transport.request(PREFIX + path, { method, query: opts.query, body: opts.body });
  }
}
