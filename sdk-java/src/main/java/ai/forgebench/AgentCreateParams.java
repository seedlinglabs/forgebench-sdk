package ai.forgebench;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Parameters of {@code agents().create(...)}.
 *
 * <p>{@code ownerIdentityId} — the accountable person for this agent — is
 * REQUIRED: the control plane rejects an agent with no named owner. Resolve it
 * from {@code client.whoami().getIdentityId()} (or an operator picker) rather
 * than hardcoding a value.
 */
public final class AgentCreateParams {
    final String name;
    final String ownerIdentityId;
    final String model;
    final String description;
    final String systemPrompt;
    final Map<String, Object> config;
    final List<String> tags;

    private AgentCreateParams(Builder b) {
        this.name = b.name;
        this.ownerIdentityId = b.ownerIdentityId;
        this.model = b.model;
        this.description = b.description;
        this.systemPrompt = b.systemPrompt;
        this.config = b.config;
        this.tags = b.tags;
    }

    public static Builder builder() {
        return new Builder();
    }

    Map<String, Object> toBody() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", name);
        body.put("owner_identity_id", ownerIdentityId);
        body.put("model", model);
        body.put("description", description);
        body.put("system_prompt", systemPrompt);
        body.put("config", config == null ? Map.of() : config);
        if (tags != null && !tags.isEmpty()) body.put("tags", tags);
        return body;
    }

    public static final class Builder {
        private String name;
        private String ownerIdentityId;
        private String model = "mock-gpt";
        private String description;
        private String systemPrompt;
        private Map<String, Object> config;
        private List<String> tags;

        private Builder() {}

        public Builder name(String name) { this.name = name; return this; }
        public Builder ownerIdentityId(String id) { this.ownerIdentityId = id; return this; }
        /** Defaults to {@code mock-gpt}. */
        public Builder model(String model) { this.model = Objects.requireNonNull(model, "model"); return this; }
        public Builder description(String description) { this.description = description; return this; }
        public Builder systemPrompt(String systemPrompt) { this.systemPrompt = systemPrompt; return this; }
        public Builder config(Map<String, ?> config) {
            this.config = config == null ? null : new LinkedHashMap<>(config);
            return this;
        }
        public Builder tags(List<String> tags) {
            this.tags = tags == null ? null : new ArrayList<>(tags);
            return this;
        }

        public AgentCreateParams build() {
            Objects.requireNonNull(name, "name");
            Objects.requireNonNull(ownerIdentityId, "ownerIdentityId (the accountable owner) is required");
            return new AgentCreateParams(this);
        }
    }
}
