package ai.forgebench;

import ai.forgebench.types.SpeechAudio;
import ai.forgebench.types.Transcription;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;

import java.io.ByteArrayOutputStream;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Transcription and Speech Synthesis through the governed chokepoint. Both are
 * Governed Calls: budget-gated before any provider call (402 raises
 * {@code BudgetExceededException}), metered in their native unit, audited.
 * Audio is never stored.
 */
public final class Audio {
    private final Transcriptions transcriptions;
    private final Speech speech;

    Audio(Transport t) {
        this.transcriptions = new Transcriptions(t);
        this.speech = new Speech(t);
    }

    /** Speech to text. */
    public Transcriptions transcriptions() { return transcriptions; }

    /** Text to speech. */
    public Speech speech() { return speech; }

    private static Map<String, String> headers(String session) {
        Map<String, String> h = new HashMap<>();
        h.put("Accept", "*/*");
        h.put(Trace.SESSION_HEADER, session); // null is dropped by the transport
        return h;
    }

    public static final class Transcriptions {
        private final Transport t;

        private Transcriptions(Transport t) { this.t = t; }

        /**
         * {@code POST /v1/audio/transcriptions} (multipart). Raises
         * {@code PermissionDeniedException} with code {@code model_not_allowed}
         * for a model outside the allowlist.
         */
        public Transcription create(TranscriptionParams p) {
            String boundary = "----forgebench" + UUID.randomUUID().toString().replace("-", "");
            ByteArrayOutputStream out = new ByteArrayOutputStream(p.file.length + 1024);
            String ctype = p.contentType != null ? p.contentType : "application/octet-stream";
            write(out, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\""
                    + p.filename.replace("\"", "%22").replaceAll("\\p{Cntrl}", "")
                    + "\"\r\nContent-Type: " + ctype + "\r\n\r\n");
            out.write(p.file, 0, p.file.length);
            write(out, "\r\n");
            Map<String, String> fields = new LinkedHashMap<>();
            fields.put("model", p.model);
            if (p.language != null) fields.put("language", p.language);
            if (p.prompt != null) fields.put("prompt", p.prompt);
            if (p.responseFormat != null) fields.put("response_format", p.responseFormat);
            if (p.metadata != null && !p.metadata.isEmpty()) {
                try {
                    fields.put("metadata", Json.WRITER.writeValueAsString(p.metadata));
                } catch (JsonProcessingException e) {
                    throw new ai.forgebench.errors.ForgebenchException("could not serialize metadata: " + e.getMessage(), e);
                }
            }
            fields.forEach((k, v) -> write(out, "--" + boundary + "\r\nContent-Disposition: form-data; name=\""
                    + k + "\"\r\n\r\n" + v + "\r\n"));
            write(out, "--" + boundary + "--\r\n");

            HttpResponse<byte[]> resp = t.requestRaw("POST", "/v1/audio/transcriptions", null, out.toByteArray(),
                    "multipart/form-data; boundary=" + boundary, headers(p.session));
            String traceId = resp.headers().firstValue(Trace.AUDIO_TRACE_ID_HEADER).orElse(null);
            String body = new String(resp.body(), StandardCharsets.UTF_8);
            if (resp.headers().firstValue("content-type").orElse("").contains("json")) {
                try {
                    JsonNode node = Json.READER.readTree(body);
                    Map<String, Object> raw = Transport.toMap(node);
                    JsonNode text = node.get("text");
                    return new Transcription(text == null || text.isNull() ? "" : text.asText(), raw, traceId);
                } catch (java.io.IOException e) {
                    throw new ai.forgebench.errors.ForgebenchException("control plane returned a non-JSON body: " + e.getMessage(), e);
                }
            }
            return new Transcription(body, new LinkedHashMap<>(), traceId);
        }

        private static void write(ByteArrayOutputStream out, String s) {
            byte[] b = s.getBytes(StandardCharsets.UTF_8);
            out.write(b, 0, b.length);
        }
    }

    public static final class Speech {
        private final Transport t;

        private Speech(Transport t) { this.t = t; }

        /**
         * {@code POST /v1/audio/speech}: the audio bytes plus response headers.
         * The voice policy may refuse the call: {@code ValidationException}
         * ({@code disclosure_missing}) or {@code PermissionDeniedException}
         * ({@code voice_not_allowed}, {@code marking_required}).
         */
        public SpeechAudio create(SpeechParams p) {
            HttpResponse<byte[]> resp = t.requestRaw("POST", "/v1/audio/speech", p.toBody(), null, null,
                    headers(p.session));
            return new SpeechAudio(resp.body(),
                    resp.headers().firstValue("content-type").orElse(null),
                    resp.headers().firstValue(Trace.AUDIO_TRACE_ID_HEADER).orElse(null),
                    resp.headers().firstValue(Trace.DISCLOSURE_HEADER).orElse(null),
                    resp.headers().map());
        }
    }
}
