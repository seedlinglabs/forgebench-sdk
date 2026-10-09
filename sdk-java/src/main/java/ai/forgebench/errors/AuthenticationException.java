package ai.forgebench.errors;

/** 401 — missing/invalid/revoked API key or token. */
public class AuthenticationException extends ApiException {
    private static final long serialVersionUID = 1L;

    public AuthenticationException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
