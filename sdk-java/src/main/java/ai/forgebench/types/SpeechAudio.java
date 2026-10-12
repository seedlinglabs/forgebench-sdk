package ai.forgebench.types;

import java.util.List;
import java.util.Map;

/**
 * Result of {@code audio().speech().create}: the synthesized audio, its content
 * type, the Governed Call's trace id ({@code X-Forgebench-Trace-Id}), the
 * disclosure applied ({@code X-Forgebench-Disclosure}) and all response headers.
 * The control plane never stores the audio.
 */
public final class SpeechAudio {
    private final byte[] audio;
    private final String contentType;
    private final String traceId;
    private final String disclosure;
    private final Map<String, List<String>> headers;

    public SpeechAudio(byte[] audio, String contentType, String traceId, String disclosure,
                       Map<String, List<String>> headers) {
        this.audio = audio;
        this.contentType = contentType;
        this.traceId = traceId;
        this.disclosure = disclosure;
        this.headers = headers;
    }

    public byte[] getAudio() { return audio; }
    public String getContentType() { return contentType; }
    public String getTraceId() { return traceId; }
    /** The {@code X-Forgebench-Disclosure} header, or null. */
    public String getDisclosure() { return disclosure; }
    /** Every response header (lower-cased names). */
    public Map<String, List<String>> getHeaders() { return headers; }
}
