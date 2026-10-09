package ai.forgebench.errors;

/** 422 — request body failed control-plane validation. */
public class ValidationException extends ApiException {
    private static final long serialVersionUID = 1L;

    public ValidationException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
