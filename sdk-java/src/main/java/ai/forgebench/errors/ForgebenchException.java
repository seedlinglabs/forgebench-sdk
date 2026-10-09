package ai.forgebench.errors;

/**
 * Base class for every error raised by the SDK.
 *
 * <p>Unchecked, so governed calls compose inside lambdas (a {@code serve()}
 * handler, a tool executor) without wrapping.
 */
public class ForgebenchException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    public ForgebenchException(String message) {
        super(message);
    }

    public ForgebenchException(String message, Throwable cause) {
        super(message, cause);
    }
}
