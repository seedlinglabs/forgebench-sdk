package ai.forgebench.errors;

import java.util.Map;

/**
 * 402 {@code token_cap_exceeded} — a token cap (tenant, model, agent or API key;
 * monthly or daily) would be exceeded by this call.
 *
 * <p>A {@link BudgetExceededException}, so existing 402 handlers keep catching it;
 * catch this subclass first to tell a token cap from a spend cap. Raised before any
 * provider call, so nothing was spent or metered.
 */
public class TokenCapExceededException extends BudgetExceededException {
    private static final long serialVersionUID = 1L;

    public TokenCapExceededException(String message, int statusCode, String code, Object body, String requestId) {
        super(message, statusCode, code, body, requestId);
    }

    private Object field(String name) {
        Object body = getBody();
        if (body instanceof Map) {
            Object detail = ((Map<?, ?>) body).get("detail");
            if (detail instanceof Map) {
                return ((Map<?, ?>) detail).get(name);
            }
        }
        return null;
    }

    private String text(String name) {
        Object v = field(name);
        return v instanceof String ? (String) v : null;
    }

    private Long number(String name) {
        Object v = field(name);
        return v instanceof Number ? Long.valueOf(((Number) v).longValue()) : null;
    }

    /** Which scope's cap fired: {@code tenant}, {@code model}, {@code agent} or {@code key}. */
    public String getLevel() {
        return text("level");
    }

    /** {@code monthly} or {@code daily}. */
    public String getPeriod() {
        return text("period");
    }

    /** The model / agent id / key id the cap belongs to; null for the tenant cap. */
    public String getScopeId() {
        return text("scope_id");
    }

    public Long getLimitTokens() {
        return number("limit_tokens");
    }

    public Long getUsedTokens() {
        return number("used_tokens");
    }

    public Long getAttemptedTokens() {
        return number("attempted_tokens");
    }
}
