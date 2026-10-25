package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A PATCH of a Voice Asset. Only the setters you call are sent; passing
 * {@code null} to one sends an explicit JSON null, which CLEARS that field
 * (e.g. {@code expiresAt(null)}). A setter you never call leaves it unchanged.
 */
public final class VoiceAssetUpdate {
    private final Map<String, Object> fields = new LinkedHashMap<>();

    private VoiceAssetUpdate() {}

    public static VoiceAssetUpdate builder() {
        return new VoiceAssetUpdate();
    }

    public VoiceAssetUpdate displayName(String v) { fields.put("display_name", v); return this; }
    public VoiceAssetUpdate consentRecordId(String v) { fields.put("consent_record_id", v); return this; }
    public VoiceAssetUpdate licensedUses(List<String> v) { fields.put("licensed_uses", v); return this; }
    public VoiceAssetUpdate allowedAgentIds(List<String> v) { fields.put("allowed_agent_ids", v); return this; }
    public VoiceAssetUpdate expiresAt(String v) { fields.put("expires_at", v); return this; }

    Map<String, Object> toBody() {
        return new LinkedHashMap<>(fields);
    }
}
