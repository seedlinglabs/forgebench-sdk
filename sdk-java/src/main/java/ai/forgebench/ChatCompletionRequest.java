package ai.forgebench;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Parameters of one governed chat completion. Pass it to
 * {@code chat().completions().create(...)} or {@code createStream(...)}.
 *
 * <pre>{@code
 * ChatCompletionRequest req = ChatCompletionRequest.builder()
 *     .model("mock-gpt")
 *     .addMessage(ChatMessage.user("Prove the governed path works."))
 *     .build();
 * }</pre>
 */
public final class ChatCompletionRequest {
    // The message fields the chokepoint's ChatMessage model accepts.
    private static final List<String> TOOL_FIELDS = List.of("tool_calls", "tool_call_id", "name");

    private final String model;
    private final List<Object> messages;
    private final Double temperature;
    private final Integer maxTokens;
    private final String traceId;
    private final String parentCallId;
    private final Map<String, Object> extraBody;

    private ChatCompletionRequest(Builder b) {
        this.model = b.model;
        this.messages = Collections.unmodifiableList(new ArrayList<>(b.messages));
        this.temperature = b.temperature;
        this.maxTokens = b.maxTokens;
        this.traceId = b.traceId;
        this.parentCallId = b.parentCallId;
        this.extraBody = b.extraBody == null ? null : Collections.unmodifiableMap(new LinkedHashMap<>(b.extraBody));
    }

    public static Builder builder() {
        return new Builder();
    }

    /** A builder pre-filled with this request — tweak one field for the next turn. */
    public Builder toBuilder() {
        Builder b = new Builder();
        b.model = model;
        b.messages.addAll(messages);
        b.temperature = temperature;
        b.maxTokens = maxTokens;
        b.traceId = traceId;
        b.parentCallId = parentCallId;
        b.extraBody = extraBody == null ? null : new LinkedHashMap<>(extraBody);
        return b;
    }

    public String getModel() { return model; }
    public List<Object> getMessages() { return messages; }
    public Double getTemperature() { return temperature; }
    public Integer getMaxTokens() { return maxTokens; }
    public String getTraceId() { return traceId; }
    public String getParentCallId() { return parentCallId; }
    public Map<String, Object> getExtraBody() { return extraBody; }

    /** The JSON body of {@code POST /v1/chat/completions}. */
    Map<String, Object> toPayload(boolean stream) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("model", model);
        payload.put("messages", normalizeMessages(messages));
        payload.put("stream", stream);
        if (temperature != null) payload.put("temperature", temperature);
        if (maxTokens != null) payload.put("max_tokens", maxTokens);
        // The chokepoint stamps a caller-supplied trace_id on the audit/metering
        // row and the trace, so tool calls threaded with the same id nest under
        // this model call in the trace view.
        if (traceId != null) payload.put("trace_id", traceId);
        if (extraBody != null) {
            // The chokepoint uses extra='ignore' and strips api_key/api_base, so
            // passthrough never leaks to the provider. Named fields win.
            extraBody.forEach(payload::putIfAbsent);
        }
        return payload;
    }

    private static List<Map<String, Object>> normalizeMessages(List<Object> messages) {
        List<Map<String, Object>> out = new ArrayList<>(messages.size());
        for (Object m : messages) {
            Map<?, ?> src;
            if (m instanceof ChatMessage) {
                src = ((ChatMessage) m).toMap();
            } else if (m instanceof Map) {
                src = (Map<?, ?>) m;
            } else {
                throw new IllegalArgumentException(
                        "a message must be a ChatMessage or a Map, got " + (m == null ? "null" : m.getClass().getName()));
            }
            Map<String, Object> msg = new LinkedHashMap<>();
            msg.put("role", String.valueOf(src.get("role")));
            Object content = src.get("content");
            // Null stays null (an assistant tool-call turn); anything else that
            // is not already a content-parts list is sent as text.
            msg.put("content", content == null || content instanceof String || content instanceof List
                    ? content : content.toString());
            for (String key : TOOL_FIELDS) {
                Object v = src.get(key);
                if (v != null) msg.put(key, v);
            }
            out.add(msg);
        }
        return out;
    }

    public static final class Builder {
        private String model = "mock-gpt";
        private final List<Object> messages = new ArrayList<>();
        private Double temperature;
        private Integer maxTokens;
        private String traceId;
        private String parentCallId;
        private Map<String, Object> extraBody;

        private Builder() {}

        /** Defaults to {@code mock-gpt}, which needs no provider key. */
        public Builder model(String model) {
            this.model = Objects.requireNonNull(model, "model");
            return this;
        }

        /** Replaces the messages. Each is a {@link ChatMessage} or a wire-shaped Map. */
        public Builder messages(List<?> messages) {
            this.messages.clear();
            this.messages.addAll(messages);
            return this;
        }

        public Builder addMessage(ChatMessage message) {
            this.messages.add(Objects.requireNonNull(message, "message"));
            return this;
        }

        /** Adds a wire-shaped message ({@code role}, {@code content}, tool fields). */
        public Builder addMessage(Map<String, ?> message) {
            this.messages.add(Objects.requireNonNull(message, "message"));
            return this;
        }

        public Builder temperature(double temperature) {
            this.temperature = temperature;
            return this;
        }

        public Builder maxTokens(int maxTokens) {
            this.maxTokens = maxTokens;
            return this;
        }

        /**
         * Pass the SAME value here and into the agent's own MCP client calls the
         * response leads to, so those tool calls nest under this model call in
         * the trace view. Generate one with {@link Trace#newTraceId()}. Omit it
         * and the server mints one (read it back from the response).
         */
        public Builder traceId(String traceId) {
            this.traceId = traceId;
            return this;
        }

        /**
         * The {@code call_id} of the governed call that CAUSED this one — the
         * orchestrating turn, or the previous turn of a tool loop. The server
         * records this call as its child, so cost rolls up to the root and the
         * console draws who called what. Where traceId groups, this structures.
         * Never affects whether the call is allowed or what it costs.
         */
        public Builder parentCallId(String parentCallId) {
            this.parentCallId = parentCallId;
            return this;
        }

        /**
         * Extra top-level body fields (e.g. {@code tools}, {@code tool_choice},
         * {@code response_format}). Never overrides a field set by name.
         */
        public Builder extraBody(Map<String, ?> extraBody) {
            this.extraBody = extraBody == null ? null : new LinkedHashMap<>(extraBody);
            return this;
        }

        /** Shortcut for {@code extraBody({"tools": tools})}, merged into any existing extra body. */
        public Builder tools(List<?> tools) {
            if (extraBody == null) extraBody = new LinkedHashMap<>();
            extraBody.put("tools", tools);
            return this;
        }

        public ChatCompletionRequest build() {
            if (messages.isEmpty()) throw new IllegalArgumentException("a chat completion needs at least one message");
            return new ChatCompletionRequest(this);
        }
    }
}
