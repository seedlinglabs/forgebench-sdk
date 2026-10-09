package ai.forgebench.errors;

/** 5xx — the control plane errored. */
public class ServerException extends ApiException {
    private static final long serialVersionUID = 1L;

    public ServerException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
