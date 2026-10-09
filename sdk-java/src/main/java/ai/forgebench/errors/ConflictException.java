package ai.forgebench.errors;

/** 409 — conflicting state (e.g. duplicate). */
public class ConflictException extends ApiException {
    private static final long serialVersionUID = 1L;

    public ConflictException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
