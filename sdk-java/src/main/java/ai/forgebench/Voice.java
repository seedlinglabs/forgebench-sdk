package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Voice governance admin: the workspace voice policy, Voice Assets and Consent
 * Records ({@code /v1/voice/*}). Reads need viewer+, writes need admin.
 * Records are returned as plain maps with the server's snake_case keys.
 */
public final class Voice {
    private final Policy policy;
    private final Assets assets;
    private final Consents consents;

    Voice(Transport t) {
        this.policy = new Policy(t);
        this.assets = new Assets(t);
        this.consents = new Consents(t);
    }

    /** The workspace voice policy. */
    public Policy policy() { return policy; }

    /** Voice Assets (the voices an agent may speak with). */
    public Assets assets() { return assets; }

    /** Consent Records (who agreed to be recorded or cloned). */
    public Consents consents() { return consents; }

    /** Drops nulls: an omitted field means "default"/"unset" on create. */
    private static Map<String, Object> omitNulls(Map<String, ?> in, String... also) {
        Map<String, Object> out = new LinkedHashMap<>();
        in.forEach((k, v) -> out.put(k, v));
        out.values().removeIf(v -> v == null);
        for (String k : also) out.remove(k);
        return out;
    }

    public static final class Policy {
        private final Transport t;

        private Policy(Transport t) { this.t = t; }

        /** The workspace voice policy (defaults when none is saved). */
        public Map<String, Object> get() {
            return Transport.toMap(t.request("GET", "/v1/voice/policy", null, null, null));
        }

        /**
         * Replace the policy. Fields: {@code disclosure_mode}, {@code disclosure_phrases},
         * {@code inject_text}, {@code default_language}, {@code marking_required},
         * {@code marking_min_level}, {@code transcript_guardrails},
         * {@code voice_assets_required_for}, {@code voice_trace_content}. Omitted
         * fields reset to defaults. {@code update(get())} works: the read-only
         * {@code updated_at} is dropped.
         */
        public Map<String, Object> update(Map<String, ?> fields) {
            return Transport.toMap(t.request("PUT", "/v1/voice/policy", omitNulls(fields, "updated_at"), null, null));
        }
    }

    public static final class Assets {
        private final Transport t;

        private Assets(Transport t) { this.t = t; }

        /** All Voice Assets of the workspace. */
        public List<Map<String, Object>> list() {
            return Transport.toList(t.request("GET", "/v1/voice/assets", null, null, null));
        }

        /**
         * Register a Voice Asset. Fields: {@code provider}, {@code provider_voice_id},
         * {@code display_name}, {@code kind} ({@code stock|brand|cloned}), and optionally
         * {@code consent_record_id} (required for cloned), {@code licensed_uses},
         * {@code allowed_agent_ids}, {@code expires_at}. A duplicate
         * (provider, provider_voice_id) raises {@code ConflictException}.
         */
        public Map<String, Object> create(Map<String, ?> fields) {
            return Transport.toMap(t.request("POST", "/v1/voice/assets", omitNulls(fields), null, null));
        }

        /** Patch a Voice Asset; see {@link VoiceAssetUpdate} for null-clears semantics. */
        public Map<String, Object> update(String assetId, VoiceAssetUpdate update) {
            return Transport.toMap(t.request("PATCH", "/v1/voice/assets/" + Transport.seg(assetId),
                    update.toBody(), null, null));
        }

        /** Revoke a Voice Asset (admin); it can no longer be used for speech. */
        public Map<String, Object> revoke(String assetId) {
            return Transport.toMap(t.request("POST", "/v1/voice/assets/" + Transport.seg(assetId) + "/revoke",
                    null, null, null));
        }
    }

    public static final class Consents {
        private final Transport t;

        private Consents(Transport t) { this.t = t; }

        /** All Consent Records of the workspace. */
        public List<Map<String, Object>> list() {
            return Transport.toList(t.request("GET", "/v1/voice/consents", null, null, null));
        }

        /**
         * Record a Consent Record. Fields: {@code subject_name}, {@code purpose}
         * ({@code voice_clone|recording}), {@code scope}, and optionally
         * {@code subject_contact}, {@code evidence_uri}, {@code granted_at}
         * (defaults to now), {@code expires_at}.
         */
        public Map<String, Object> create(Map<String, ?> fields) {
            return Transport.toMap(t.request("POST", "/v1/voice/consents", omitNulls(fields), null, null));
        }

        /** Revoke a Consent Record (admin). */
        public Map<String, Object> revoke(String consentId) {
            return Transport.toMap(t.request("POST", "/v1/voice/consents/" + Transport.seg(consentId) + "/revoke",
                    null, null, null));
        }
    }
}
