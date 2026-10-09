/**
 * Ledger traces (`/v1/traces`): one row per governed call — audit entry +
 * metering, newest first. Needs admin, `traces:read`, or the
 * `observability:view` permission. The rich span view is `langfuse.traces`.
 */

import type { Transport } from "../http.js";
import { validateTraceId } from "../trace.js";

export interface LedgerTrace {
  id: string;
  audit_id: string;
  action: string;
  status: string;
  trace_id: string | null;
  created_at: string | null;
  metering: Record<string, unknown> | null;
  [key: string]: unknown;
}

export class TracesResource {
  constructor(private readonly transport: Transport) {}

  /** List governed calls; `traceId` narrows to the calls of one trace. */
  async list(
    opts: { traceId?: string; limit?: number; offset?: number; after?: string; before?: string } = {},
  ): Promise<LedgerTrace[]> {
    validateTraceId(opts.traceId);
    return this.transport.request<LedgerTrace[]>("/v1/traces", {
      method: "GET",
      query: {
        trace_id: opts.traceId,
        limit: opts.limit,
        offset: opts.offset,
        after: opts.after,
        before: opts.before,
      },
    });
  }

  /** One governed call by its `call_id` (the audit row id). */
  async get(callId: string): Promise<LedgerTrace> {
    return this.transport.request<LedgerTrace>(`/v1/traces/${encodeURIComponent(callId)}`, {
      method: "GET",
    });
  }
}
