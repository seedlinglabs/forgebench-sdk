package ai.forgebench.errors;

/** Network-level failure talking to the control plane (DNS, refused, timeout). */
public class ForgebenchConnectionException extends ForgebenchException {
    private static final long serialVersionUID = 1L;

    public ForgebenchConnectionException(String message) {
        super(message);
    }

    public ForgebenchConnectionException(String message, Throwable cause) {
        super(message, cause);
    }
}
