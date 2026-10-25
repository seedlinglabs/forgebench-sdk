package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/**
 * Parameters of {@code agents().run(agentId, MessageRunParams)} and
 * {@code agents().runStream(...)}: one governed, grounded answer from an agent
 * ({@code POST /v1/agents/{id}/run}).
 */
public final class MessageRunParams {
    final String message;
    final Integer topK;
    final Map<String, String> dimensions;
    final String session;

    private MessageRunParams(Builder b) {
        this.message = Objects.requireNonNull(b.message, "message");
        this.topK = b.topK;
        this.dimensions = b.dimensions;
        this.session = b.session;
    }

    public static Builder builder(String message) {
        return new Builder(message);
    }

    Map<String, Object> toBody(boolean stream) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("message", message);
        if (stream) body.put("stream", true);
        if (topK != null) body.put("top_k", topK);
        if (dimensions != null && !dimensions.isEmpty()) body.put("dimensions", dimensions);
        return body;
    }

    public static final class Builder {
        private final String message;
        private Integer topK;
        private Map<String, String> dimensions;
        private String session;

        private Builder(String message) { this.message = message; }

        public Builder topK(int topK) { this.topK = topK; return this; }

        public Builder dimensions(Map<String, String> dimensions) {
            this.dimensions = dimensions == null ? null : new LinkedHashMap<>(dimensions);
            return this;
        }

        /** Sets {@code X-Forgebench-Session} so the agent's calls group into one Voice Conversation. */
        public Builder session(String session) { this.session = session; return this; }

        public MessageRunParams build() { return new MessageRunParams(this); }
    }
}
