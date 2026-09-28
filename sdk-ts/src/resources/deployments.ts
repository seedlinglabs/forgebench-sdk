/** Deployments resource — `/v1/deployments`. */

import type { Transport } from "../http.js";
import type { CreateDeploymentRequest, DeploymentInfo } from "../types.js";

const BASE = "/v1/deployments";

export class DeploymentsResource {
  constructor(private readonly transport: Transport) {}

  /** Deploy an agent. Requires `builder` role or higher. */
  async create(body: CreateDeploymentRequest): Promise<DeploymentInfo> {
    return this.transport.request<DeploymentInfo>(BASE, { method: "POST", body });
  }

  /** List the tenant's deployments. */
  async list(): Promise<DeploymentInfo[]> {
    return this.transport.request<DeploymentInfo[]>(BASE, { method: "GET" });
  }

  /** Retrieve a single deployment by id. */
  async retrieve(deploymentId: string): Promise<DeploymentInfo> {
    return this.transport.request<DeploymentInfo>(
      `${BASE}/${encodeURIComponent(deploymentId)}`,
      { method: "GET" },
    );
  }
}
