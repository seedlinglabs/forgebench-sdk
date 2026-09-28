/**
 * Governance read APIs: tamper-evident audit log (`/v1/audit`) and usage
 * metering (`/v1/metering/summary`, `/v1/traces`). These prove the chokepoint
 * actually recorded each call (hash-chained audit row + metering event).
 */

import type { Transport } from "../http.js";
import type { AuditEntry, MeteringSummary } from "../types.js";

export class AuditResource {
  constructor(private readonly transport: Transport) {}

  /**
   * List audit-log entries for the calling tenant, newest first.
   * Each entry carries `prev_hash`/`row_hash` forming a per-tenant hash chain.
   */
  async list(opts: { limit?: number; offset?: number } = {}): Promise<AuditEntry[]> {
    return this.transport.request<AuditEntry[]>("/v1/audit", {
      method: "GET",
      query: { limit: opts.limit, offset: opts.offset },
    });
  }
}

export class MeteringResource {
  constructor(private readonly transport: Transport) {}

  /** Aggregate usage + remaining budget for the calling tenant. */
  async summary(): Promise<MeteringSummary> {
    return this.transport.request<MeteringSummary>("/v1/metering/summary", { method: "GET" });
  }
}
