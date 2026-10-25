package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/** Parameters of {@code audio().speech().create(...)}. */
public final class SpeechParams {
    final String model;
    final String input;
    final String voice;
    final String responseFormat;
    final Double speed;
    final Map<String, Object> metadata;
    final String session;

    private SpeechParams(Builder b) {
        this.model = Objects.requireNonNull(b.model, "model");
        this.input = Objects.requireNonNull(b.input, "input");
        this.voice = Objects.requireNonNull(b.voice, "voice");
        this.responseFormat = b.responseFormat;
        this.speed = b.speed;
        this.metadata = b.metadata;
        this.session = b.session;
    }

    public static Builder builder() {
        return new Builder();
    }

    Map<String, Object> toBody() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("model", model);
        body.put("input", input);
        body.put("voice", voice);
        if (responseFormat != null) body.put("response_format", responseFormat);
        if (speed != null) body.put("speed", speed);
        if (metadata != null && !metadata.isEmpty()) body.put("metadata", metadata);
        return body;
    }

    public static final class Builder {
        private String model;
        private String input;
        private String voice;
        private String responseFormat;
        private Double speed;
        private Map<String, Object> metadata;
        private String session;

        private Builder() {}

        /** Required: the speech model. */
        public Builder model(String model) { this.model = model; return this; }
        /** Required: the text to synthesize. */
        public Builder input(String input) { this.input = input; return this; }
        /** Required: the voice (a registered Voice Asset or provider voice id). */
        public Builder voice(String voice) { this.voice = voice; return this; }
        /** Audio format such as {@code mp3} or {@code wav}. */
        public Builder responseFormat(String responseFormat) { this.responseFormat = responseFormat; return this; }
        /** Playback speed multiplier. */
        public Builder speed(double speed) { this.speed = speed; return this; }

        /** Free-form metadata, sent as a JSON object. */
        public Builder metadata(Map<String, ?> metadata) {
            this.metadata = metadata == null ? null : new LinkedHashMap<>(metadata);
            return this;
        }

        /** Sets {@code X-Forgebench-Session}, the Voice Conversation key. */
        public Builder session(String session) { this.session = session; return this; }

        public SpeechParams build() {
            return new SpeechParams(this);
        }
    }
}
