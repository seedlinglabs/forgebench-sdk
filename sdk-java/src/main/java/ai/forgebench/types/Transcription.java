package ai.forgebench.types;

import java.util.Map;

/**
 * Result of {@code audio().transcriptions().create}. {@code raw} holds the full
 * JSON body (segments, duration, ... for {@code verbose_json}); empty when the
 * server returned plain text. {@code traceId} comes from {@code X-Forgebench-Trace-Id}.
 */
public final class Transcription {
    private final String text;
    private final Map<String, Object> raw;
    private final String traceId;

    public Transcription(String text, Map<String, Object> raw, String traceId) {
        this.text = text;
        this.raw = raw;
        this.traceId = traceId;
    }

    public String getText() { return text; }
    public Map<String, Object> getRaw() { return raw; }
    public String getTraceId() { return traceId; }
}
