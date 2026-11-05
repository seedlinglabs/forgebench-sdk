/**
 * Environments — `/v1/environments`: the named stages (dev, staging, prod) agents are placed in.
 * Reads work on any plan; writes need an admin on a Pro or Enterprise plan (402 `plan_required`).
 */

import type { Transport } from "../http.js";
import type { CreateEnvironmentRequest, EnvironmentInfo, EnvironmentList } from "../types.js";

const BASE = "/v1/environments";
const p = (s: string) => encodeURIComponent(s);

export class EnvironmentsResource {
  constructor(private readonly transport: Transport) {}

  /** In promotion order, plus whether this plan may change them. */
  list(): Promise<EnvironmentList> {
    return this.transport.request<EnvironmentList>(BASE, { method: "GET" });
  }

  create(body: CreateEnvironmentRequest): Promise<EnvironmentInfo> {
    return this.transport.request<EnvironmentInfo>(BASE, { method: "POST", body });
  }

  update(slug: string, body: { name?: string; is_production?: boolean }): Promise<EnvironmentInfo> {
    return this.transport.request<EnvironmentInfo>(`${BASE}/${p(slug)}`, { method: "PATCH", body });
  }

  /** Set the promotion path: every slug, first stage first. */
  reorder(order: string[]): Promise<EnvironmentInfo[]> {
    return this.transport.request<EnvironmentInfo[]>(`${BASE}/order`, { method: "PUT", body: { order } });
  }

  /** Delete an environment that has no agents (409 otherwise). */
  delete(slug: string): Promise<void> {
    return this.transport.request<void>(`${BASE}/${p(slug)}`, { method: "DELETE" });
  }
}
