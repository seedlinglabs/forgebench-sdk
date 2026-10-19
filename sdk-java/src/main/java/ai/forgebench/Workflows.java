package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Workflows ({@code /v1/workflows}): a named group of agents (steps) registered
 * and observed together. Records are plain maps with the server's snake_case keys.
 */
public final class Workflows {
    private final Transport t;

    Workflows(Transport transport) {
        this.t = transport;
    }

    /** Workflows with step count, month spend and calls. */
    public List<Map<String, Object>> list() {
        return Transport.toList(t.request("GET", "/v1/workflows", null, null, null));
    }

    /** One workflow: steps, declared and observed edges. */
    public Map<String, Object> get(String slug) {
        return Transport.toMap(t.request("GET", "/v1/workflows/" + Transport.seg(slug), null, null, null));
    }

    /** As {@link #get(String)}, reading observed traffic over the last {@code days} (1-365). */
    public Map<String, Object> get(String slug, int days) {
        return Transport.toMap(t.request("GET", "/v1/workflows/" + Transport.seg(slug), null,
                Map.of("days", days), null));
    }

    /** Is it wired, threaded (X-Parent-Call) and seeing traffic? Each non-passing check has a {@code fix}. */
    public Map<String, Object> checks(String slug) {
        return Transport.toMap(t.request("GET", "/v1/workflows/" + Transport.seg(slug) + "/checks", null, null, null));
    }

    /**
     * Register atomically: every step's agent and key, the steps and the edges, or nothing.
     * {@code body} is {@code name}, {@code owner_identity_id}, {@code steps}
     * ({@code [{name, model, ...}]}), optional {@code slug}, {@code description},
     * {@code edges} ({@code [{from, to, kind}]}, kind {@code sequence} or {@code task}) and
     * {@code issue_credential} ({@code false} registers every agent sealed). The raw keys are in
     * {@code credentials[].credential.api_key}, once. Null values are dropped.
     */
    public Map<String, Object> register(Map<String, ?> body) {
        Map<String, Object> out = new LinkedHashMap<>();
        body.forEach((k, v) -> {
            if (v != null) out.put(k, v);
        });
        return Transport.toMap(t.request("POST", "/v1/workflows", out, null, null));
    }
}
