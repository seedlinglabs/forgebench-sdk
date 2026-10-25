package ai.forgebench;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/** Parameters of {@code audio().transcriptions().create(...)}. */
public final class TranscriptionParams {
    final byte[] file;
    final String filename;
    final String contentType;
    final String model;
    final String language;
    final String prompt;
    final String responseFormat;
    final Map<String, Object> metadata;
    final String session;

    private TranscriptionParams(Builder b) {
        this.file = Objects.requireNonNull(b.file, "file");
        this.filename = b.filename;
        // The content type goes into a multipart part header verbatim.
        if (b.contentType != null && (b.contentType.indexOf('\r') >= 0 || b.contentType.indexOf('\n') >= 0)) {
            throw new IllegalArgumentException("contentType must not contain line breaks");
        }
        this.contentType = b.contentType == null || b.contentType.isBlank() ? null : b.contentType;
        this.model = Objects.requireNonNull(b.model, "model");
        this.language = b.language;
        this.prompt = b.prompt;
        this.responseFormat = b.responseFormat;
        this.metadata = b.metadata;
        this.session = b.session;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private byte[] file;
        private String filename = "audio";
        private String contentType;
        private String model;
        private String language;
        private String prompt;
        private String responseFormat;
        private Map<String, Object> metadata;
        private String session;

        private Builder() {}

        /** The audio bytes, filename "audio". */
        public Builder file(byte[] bytes) {
            this.file = bytes;
            return this;
        }

        /** The audio bytes with a filename and a content type (e.g. {@code audio/wav}; null = octet-stream). */
        public Builder file(byte[] bytes, String filename, String contentType) {
            this.file = bytes;
            if (filename != null) this.filename = filename;
            this.contentType = contentType;
            return this;
        }

        /** Reads the whole file into memory (so retries can replay the body). */
        public Builder file(Path path) {
            try {
                this.file = Files.readAllBytes(path);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            this.filename = path.getFileName().toString();
            return this;
        }

        /** Required: the transcription model (must be in the key's allowlist). */
        public Builder model(String model) { this.model = model; return this; }
        /** Optional BCP-47 language tag such as {@code en} or {@code pt-BR}. */
        public Builder language(String language) { this.language = language; return this; }
        /** Optional prompt to bias the transcript. */
        public Builder prompt(String prompt) { this.prompt = prompt; return this; }
        /** {@code json} (default), {@code text}, {@code verbose_json}, ... */
        public Builder responseFormat(String responseFormat) { this.responseFormat = responseFormat; return this; }

        /** Free-form metadata, sent as a JSON object. */
        public Builder metadata(Map<String, ?> metadata) {
            this.metadata = metadata == null ? null : new LinkedHashMap<>(metadata);
            return this;
        }

        /** Sets {@code X-Forgebench-Session}, the Voice Conversation key. */
        public Builder session(String session) { this.session = session; return this; }

        public TranscriptionParams build() {
            return new TranscriptionParams(this);
        }
    }
}
