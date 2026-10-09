package ai.forgebench.errors;

/** 404 — the resource does not exist (or is not visible to this tenant). */
public class NotFoundException extends ApiException {
    private static final long serialVersionUID = 1L;

    public NotFoundException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
