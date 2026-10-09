package ai.forgebench.errors;

/**
 * Non-2xx HTTP response from the control plane.
 *
 * <p>Mirrors the control plane's {@code ErrorResponse} shape
 * ({@code {"detail": str, "code": str | null}}) when present.
 */
public class ApiException extends ForgebenchException {
    private static final long serialVersionUID = 1L;

    private final int statusCode;
    private final String code;
    private final transient Object body;
    private final String requestId;

    public ApiException(String message, int statusCode, String code, Object body, String requestId) {
        super(message);
        this.statusCode = statusCode;
        this.code = code;
        this.body = body;
        this.requestId = requestId;
    }

    public int getStatusCode() {
        return statusCode;
    }

    /** Machine-readable error code (e.g. {@code budget_exceeded}), or null. */
    public String getCode() {
        return code;
    }

    /** The parsed JSON body (a Map/List), the raw text, or null. */
    public Object getBody() {
        return body;
    }

    public String getRequestId() {
        return requestId;
    }

    /** The server's detail message, without the status/code decoration of {@link #toString()}. */
    public String getDetail() {
        return super.getMessage();
    }

    @Override
    public String toString() {
        StringBuilder sb = new StringBuilder(getClass().getSimpleName())
                .append(": [").append(statusCode).append("] ").append(getMessage());
        if (code != null) sb.append(" (code=").append(code).append(')');
        if (requestId != null) sb.append(" (request_id=").append(requestId).append(')');
        return sb.toString();
    }
}
