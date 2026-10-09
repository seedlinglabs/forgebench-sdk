package ai.forgebench.errors;

/**
 * 402 — the pre-call budget gate fired before any provider call.
 *
 * <p>This is the SDK surface of the governed chokepoint's budget gate: the
 * tenant's monthly spend cap was reached, so the request was rejected
 * <em>before</em> reaching the model gateway. No tokens were spent and nothing
 * was metered.
 */
public class BudgetExceededException extends ApiException {
    private static final long serialVersionUID = 1L;

    public BudgetExceededException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }
}
