/**
 * Voice governance admin: the workspace voice policy, Voice Assets and
 * Consent Records. Reads need viewer+, writes need admin.
 */

import type { Transport } from "../http.js";
import type {
  ConsentRecord,
  CreateConsentRecordRequest,
  CreateVoiceAssetRequest,
  UpdateVoiceAssetRequest,
  VoiceAsset,
  VoicePolicy,
  VoicePolicyUpdate,
} from "../types.js";

export class VoicePolicyResource {
  constructor(private readonly transport: Transport) {}

  /** The workspace voice policy (defaults when none is saved). */
  async get(): Promise<VoicePolicy> {
    return this.transport.request<VoicePolicy>("/v1/voice/policy", { method: "GET" });
  }

  /**
   * Replace the policy. Omitted fields reset to their defaults. `update(await get())`
   * works: the read-only `updated_at` is dropped.
   */
  async update(body: VoicePolicyUpdate & { updated_at?: unknown }): Promise<VoicePolicy> {
    const { updated_at: _readOnly, ...rest } = body;
    return this.transport.request<VoicePolicy>("/v1/voice/policy", { method: "PUT", body: rest });
  }
}

export class VoiceAssetsResource {
  constructor(private readonly transport: Transport) {}

  async list(): Promise<VoiceAsset[]> {
    return this.transport.request<VoiceAsset[]>("/v1/voice/assets", { method: "GET" });
  }

  /** Register a Voice Asset. A cloned one needs `consent_record_id`; a duplicate (provider, provider_voice_id) is a ConflictError. */
  async create(body: CreateVoiceAssetRequest): Promise<VoiceAsset> {
    return this.transport.request<VoiceAsset>("/v1/voice/assets", { method: "POST", body });
  }

  async update(assetId: string, body: UpdateVoiceAssetRequest): Promise<VoiceAsset> {
    return this.transport.request<VoiceAsset>(`/v1/voice/assets/${encodeURIComponent(assetId)}`, {
      method: "PATCH",
      body,
    });
  }

  async revoke(assetId: string): Promise<VoiceAsset> {
    return this.transport.request<VoiceAsset>(
      `/v1/voice/assets/${encodeURIComponent(assetId)}/revoke`,
      { method: "POST" },
    );
  }
}

export class VoiceConsentsResource {
  constructor(private readonly transport: Transport) {}

  async list(): Promise<ConsentRecord[]> {
    return this.transport.request<ConsentRecord[]>("/v1/voice/consents", { method: "GET" });
  }

  async create(body: CreateConsentRecordRequest): Promise<ConsentRecord> {
    return this.transport.request<ConsentRecord>("/v1/voice/consents", { method: "POST", body });
  }

  async revoke(consentId: string): Promise<ConsentRecord> {
    return this.transport.request<ConsentRecord>(
      `/v1/voice/consents/${encodeURIComponent(consentId)}/revoke`,
      { method: "POST" },
    );
  }
}

export class VoiceResource {
  readonly policy: VoicePolicyResource;
  readonly assets: VoiceAssetsResource;
  readonly consents: VoiceConsentsResource;
  constructor(transport: Transport) {
    this.policy = new VoicePolicyResource(transport);
    this.assets = new VoiceAssetsResource(transport);
    this.consents = new VoiceConsentsResource(transport);
  }
}
