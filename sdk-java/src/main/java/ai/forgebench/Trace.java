package ai.forgebench;

import java.util.UUID;

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

    /**
     * Request header naming the observation (a tracing step) a governed call
     * was made from; the server nests the call's generation under it. Set by
     * the SDK itself inside a step ({@link ai.forgebench.tracing.Tracing}).
     */
    public static final String PARENT_OBSERVATION_HEADER = "X-Parent-Observation";

    private Trace() {}

    /**
     * A fresh 32-hex-char id — the same format the control plane mints
     * server-side when a caller does not supply one.
     */
    public static String newTraceId() {
        return UUID.randomUUID().toString().replace("-", "");
    }
}
