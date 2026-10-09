package ai.forgebench.errors;

/** 429 — too many requests. */
public class RateLimitException extends ApiException {
    private static final long serialVersionUID = 1L;

    public RateLimitException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
