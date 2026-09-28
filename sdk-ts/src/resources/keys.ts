/** API keys resource — `/v1/keys` (+ admin create at `/v1/auth/keys`). */

import type { Transport } from "../http.js";
import type { ApiKeyCreated, ApiKeyInfo, CreateApiKeyRequest } from "../types.js";

const BASE = "/v1/keys";
// Key creation lives under the auth namespace server-side; only list/revoke
// hang off BASE. POST /v1/keys does not exist (would 404/405).
const CREATE_PATH = "/v1/auth/keys";

export class KeysResource {
  constructor(private readonly transport: Transport) {}

  /**
   * Create a new API key. The raw secret (`api_key`) is present in the response
   * exactly ONCE — store it immediately, it is unrecoverable afterwards.
   * Requires `admin` role or higher.
   */
  async create(body: CreateApiKeyRequest): Promise<ApiKeyCreated> {
    return this.transport.request<ApiKeyCreated>(CREATE_PATH, { method: "POST", body });
  }

  /** List the tenant's API keys (safe view — no secrets). */
  async list(): Promise<ApiKeyInfo[]> {
    return this.transport.request<ApiKeyInfo[]>(BASE, { method: "GET" });
  }

  /** Revoke a key by id. Idempotent server-side. */
  async revoke(keyId: string): Promise<void> {
    await this.transport.request<void>(`${BASE}/${encodeURIComponent(keyId)}/revoke`, {
      method: "POST",
    });
  }
}
