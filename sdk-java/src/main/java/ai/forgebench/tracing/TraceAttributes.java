package ai.forgebench.tracing;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Trace-level fields, set with {@link Tracing#updateCurrentTrace(TraceAttributes)}.
 * Only the fields you set change; tags and metadata are added to what is there.
 *
 * <pre>{@code
 * tracing.updateCurrentTrace(TraceAttributes.builder()
 *     .userId("u_42").sessionId("chat_7").tags("support").build());
 * }</pre>
 */
public final class TraceAttributes {
    final String name;
    final String userId;
    final String sessionId;
    final List<String> tags;
    final Map<String, Object> metadata;

    private TraceAttributes(Builder b) {
        this.name = b.name;
        this.userId = b.userId;
        this.sessionId = b.sessionId;
        this.tags = Collections.unmodifiableList(new ArrayList<>(b.tags));
        this.metadata = Collections.unmodifiableMap(new LinkedHashMap<>(b.metadata));
    }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private String name;
        private String userId;
        private String sessionId;
        private final List<String> tags = new ArrayList<>();
        private final Map<String, Object> metadata = new LinkedHashMap<>();

        private Builder() {}

        /** The trace's name; defaults to the root step's name. */
        public Builder name(String name) {
            this.name = name;
            return this;
        }

        /** Your end user. Model calls in the trace inherit it. */
        public Builder userId(String userId) {
            this.userId = userId;
            return this;
        }

        /** Groups the traces of one conversation. Model calls in the trace inherit it. */
        public Builder sessionId(String sessionId) {
            this.sessionId = sessionId;
            return this;
        }

        public Builder tags(String... tags) {
            this.tags.addAll(Arrays.asList(tags));
            return this;
        }

        public Builder tags(List<String> tags) {
            this.tags.addAll(tags);
            return this;
        }

        public Builder metadata(String key, Object value) {
            this.metadata.put(key, value);
            return this;
        }

        public Builder metadata(Map<String, ?> metadata) {
            this.metadata.putAll(metadata);
            return this;
        }

        public TraceAttributes build() {
            return new TraceAttributes(this);
        }
    }
}
