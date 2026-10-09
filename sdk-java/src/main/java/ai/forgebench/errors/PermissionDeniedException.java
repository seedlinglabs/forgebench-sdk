package ai.forgebench.errors;

/** 403 — authenticated but lacking the required role/scope. */
public class PermissionDeniedException extends ApiException {
    private static final long serialVersionUID = 1L;

    public PermissionDeniedException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
