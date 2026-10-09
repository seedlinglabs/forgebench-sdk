package ai.forgebench;

import java.util.UUID;

/**
 * Correlating a model call with the calls and tool calls it leads to.
 *
 * <p>MCP itself carries no notion of "which model call caused this tool call",
 * so the control plane's chokepoint accepts a caller-supplied {@code trace_id}.
 * Generate ONE id per logical turn, pass it as
 * {@link ChatCompletionRequest.Builder#traceId(String)}, and thread the same id
 * into every tool call the response leads to.
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

    private Trace() {}

    /**
     * A fresh 32-hex-char id — the same format the control plane mints
     * server-side when a caller does not supply one.
     */
    public static String newTraceId() {
        return UUID.randomUUID().toString().replace("-", "");
    }
}
