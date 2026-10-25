package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * Correlating the governed calls of one turn.
 *
 * <p>{@code trace_id} GROUPS the calls of one turn/run: pass the same id as
 * {@link ChatCompletionRequest.Builder#traceId(String)} to each call of that
 * turn and they share one trace. Inside a traced step
 * ({@link ai.forgebench.tracing.Tracing}) the SDK fills it in for you. MCP tool
 * calls the model makes are attributed to the model call automatically.
 */
public final class Trace {
    /**
     * Request header that nests a governed call under a previous one. Its value
     * is that call's {@code call_id}. Where {@code trace_id} GROUPS the calls of
     * one run, this gives them STRUCTURE: cost rolls up to the root and the
     * console draws which call caused which. Correlation only — it never
     * affects whether a call is allowed or what it costs.
     */
    public static final String PARENT_CALL_HEADER = "X-Parent-Call";

    /** Response header carrying the call's ledger id on the streaming path. */
    public static final String CALL_ID_HEADER = "X-Call-Id";

    /** Trace-id response header of chat (the {@code /v1/audio/*} routes use {@link #AUDIO_TRACE_ID_HEADER}). */
    public static final String TRACE_ID_HEADER = "X-Trace-Id";

    /** Trace-id response header of the {@code /v1/audio/*} routes. */
    public static final String AUDIO_TRACE_ID_HEADER = "X-Forgebench-Trace-Id";

    /** Disclosure applied to a synthesized speech response. */
    public static final String DISCLOSURE_HEADER = "X-Forgebench-Disclosure";

    /** Voice Conversation key for calls that do not carry {@code metadata.session}. */
    public static final String SESSION_HEADER = "X-Forgebench-Session";

    /** Explicit conversation turn (with {@link #SESSION_HEADER}): one trace per turn. */
    public static final String TURN_HEADER = "X-Forgebench-Turn";

    /** The control plane stores trace ids in a 64-char column and 422s longer ones. */
    public static final int MAX_TRACE_ID_LENGTH = 64;

    /** The Voice Conversation key is bounded server-side too. */
    public static final int MAX_SESSION_LENGTH = 128;

    // Printable ASCII, no leading/trailing space; internal runs of spaces are fine.
    private static final Pattern SESSION = Pattern.compile("[\\x21-\\x7e]+( +[\\x21-\\x7e]+)*");

    /**
     * Request header naming the observation (a tracing step) a governed call
     * was made from; the server nests the call's generation under it. Set by
     * the SDK itself inside a step ({@link ai.forgebench.tracing.Tracing}).
     */
    public static final String PARENT_OBSERVATION_HEADER = "X-Parent-Observation";

    private Trace() {}

    /** {@link #voiceHeaders(String, Integer, String)} with no turn or traceparent. */
    public static Map<String, String> voiceHeaders(String session) {
        return voiceHeaders(session, null, null);
    }

    /** {@link #voiceHeaders(String, Integer, String)} with a turn and no traceparent. */
    public static Map<String, String> voiceHeaders(String session, Integer turn) {
        return voiceHeaders(session, turn, null);
    }

    /**
     * Headers that tie a voice agent's {@code /v1/audio/*} and chat calls to one
     * conversation: {@code X-Forgebench-Session} always, {@code X-Forgebench-Turn}
     * when {@code turn} is given, {@code traceparent} (W3C) when given. Do NOT put
     * a phone number or other personal data in {@code session}: it is stored and shown.
     *
     * @throws IllegalArgumentException for a session that is not 1-128 printable
     *         ASCII characters, or a negative turn
     */
    public static Map<String, String> voiceHeaders(String session, Integer turn, String traceparent) {
        if (session == null || session.length() > MAX_SESSION_LENGTH || !SESSION.matcher(session).matches()) {
            throw new IllegalArgumentException("session must be 1-" + MAX_SESSION_LENGTH + " printable ASCII characters");
        }
        Map<String, String> h = new LinkedHashMap<>();
        h.put(SESSION_HEADER, session);
        if (turn != null) {
            if (turn < 0) throw new IllegalArgumentException("turn must be a non-negative integer");
            h.put(TURN_HEADER, String.valueOf(turn));
        }
        if (traceparent != null && !traceparent.isEmpty()) h.put("traceparent", traceparent);
        return h;
    }

    /** Rejects a trace id the server would 422, before spending a round trip. */
    public static String validateTraceId(String traceId) {
        if (traceId != null && (traceId.isEmpty() || traceId.length() > MAX_TRACE_ID_LENGTH)) {
            throw new IllegalArgumentException("trace_id must be a 1-" + MAX_TRACE_ID_LENGTH + " character string");
        }
        return traceId;
    }

    /**
     * A fresh 32-hex-char id — the same format the control plane mints
     * server-side when a caller does not supply one.
     */
    public static String newTraceId() {
        return UUID.randomUUID().toString().replace("-", "");
    }
}
