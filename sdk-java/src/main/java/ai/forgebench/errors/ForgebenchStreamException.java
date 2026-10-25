package ai.forgebench.errors;

/**
 * A stream failed after it opened: an SSE {@code event: error} frame, or an
 * agent-run stream that ended without {@code agent.run.completed} /
 * {@code [DONE]} (code {@code stream_truncated}).
 */
public class ForgebenchStreamException extends ForgebenchException {
    private static final long serialVersionUID = 1L;

    private final String code;

    public ForgebenchStreamException(String message, String code) {
        super(message);
        this.code = code;
    }

    /** The server's error code, {@code "stream_truncated"} for a cut-off stream, or null. */
    public String getCode() {
        return code;
    }
}
