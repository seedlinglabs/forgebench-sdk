package ai.forgebench;

import ai.forgebench.types.MessageParts;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * The message of an A2A task: {@code agents().call(callee, CallParams.text("..."))}.
 * At least one of text, data or parts.
 */
public final class CallParams {
    final String text;
    final Map<String, Object> data;
    final List<Map<String, Object>> parts;
    final String contextId;
    final Duration wait;
    final String parentCallId;

    private CallParams(Builder b) {
        this.text = b.text;
        this.data = b.data;
        this.parts = b.parts;
        this.contextId = b.contextId;
        this.wait = b.wait;
        this.parentCallId = b.parentCallId;
    }

    public static Builder builder() {
        return new Builder();
    }

    /** A text message, with the defaults (wait 30s, new context). */
    public static Builder text(String text) {
        return builder().text(text);
    }

    Map<String, Object> toBody() {
        Map<String, Object> message = new LinkedHashMap<>();
        message.put("role", "user");
        message.put("parts", MessageParts.of(text, data, parts));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("message", message);
        if (contextId != null && !contextId.isEmpty()) body.put("context_id", contextId);
        return body;
    }

    public static final class Builder {
        private String text;
        private Map<String, Object> data;
        private List<Map<String, Object>> parts;
        private String contextId;
        private Duration wait = Duration.ofSeconds(30);
        private String parentCallId;

        private Builder() {}

        public Builder text(String text) {
            this.text = text;
            return this;
        }

        public Builder data(Map<String, ?> data) {
            this.data = data == null ? null : new LinkedHashMap<>(data);
            return this;
        }

        public Builder parts(List<Map<String, Object>> parts) {
            this.parts = parts == null ? null : new ArrayList<>(parts);
            return this;
        }

        /** Answer a callee's question: the {@code contextId} of its input_required task. */
        public Builder contextId(String contextId) {
            this.contextId = contextId;
            return this;
        }

        /**
         * How long to block for an answer (completed, failed, or a question).
         * A task still working after that is returned as-is — poll it with
         * {@code agents().task(...)}. Default 30s.
         */
        public Builder wait(Duration wait) {
            this.wait = Objects.requireNonNull(wait, "wait");
            return this;
        }

        /** Nests this task under the governed call that led to it. */
        public Builder parentCallId(String parentCallId) {
            this.parentCallId = parentCallId;
            return this;
        }

        public CallParams build() {
            MessageParts.of(text, data, parts); // fail fast on an empty message
            return new CallParams(this);
        }
    }
}
