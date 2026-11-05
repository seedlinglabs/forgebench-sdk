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

    /** As {@link #get(String, int)}, for one environment (its slug; default: the first with steps). {@code days} may be null. */
    public Map<String, Object> get(String slug, Integer days, String environment) {
        Map<String, Object> q = new LinkedHashMap<>();
        if (days != null) q.put("days", days);
        if (environment != null) q.put("environment", environment);
        return Transport.toMap(t.request("GET", "/v1/workflows/" + Transport.seg(slug), null, q, null));
    }

    /** As {@link #checks(String)}, for one environment. */
    public Map<String, Object> checks(String slug, String environment) {
        return Transport.toMap(t.request("GET", "/v1/workflows/" + Transport.seg(slug) + "/checks", null,
                Map.of("environment", environment), null));
    }

    /** Place every step agent of a workflow that is in no environment into {@code environment}, together (Pro or Enterprise). */
    public Map<String, Object> placeEnvironment(String slug, String environment) {
        return Transport.toMap(t.request("POST", "/v1/workflows/" + Transport.seg(slug) + "/environment",
                Map.of("environment", environment), null, null));
    }

    /**
     * Promote steps, edges and step settings from one environment to another. {@code addMissing} adds an instance of
     * a step agent that has none in {@code to} (its raw key is in {@code credentials}, once); {@code dryRun} writes nothing.
     */
    public Map<String, Object> promote(String slug, String from, String to, boolean addMissing, boolean dryRun) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("from", from);
        body.put("to", to);
        body.put("add_missing", addMissing);
        body.put("dry_run", dryRun);
        return Transport.toMap(t.request("POST", "/v1/workflows/" + Transport.seg(slug) + "/promote", body, null, null));
    }

    /** Is it wired, threaded (X-Parent-Call) and seeing traffic? Each non-passing check has a {@code fix}. */
    public Map<String, Object> checks(String slug) {
        return Transport.toMap(t.request("GET", "/v1/workflows/" + Transport.seg(slug) + "/checks", null, null, null));
    }

    /**
     * Register atomically: every step's agent and key, the steps and the edges, or nothing.
     * {@code body} is {@code name}, {@code owner_identity_id}, {@code steps}
     * ({@code [{name, model, ...}]}; or {@code {agent_id, name?}} to attach an existing agent, which
     * gets no new key and a null {@code credential}), optional {@code slug}, {@code description},
     * {@code edges} ({@code [{from, to, kind}]}, kind {@code sequence} or {@code task}) and
     * {@code environment} (a slug; Pro/Enterprise; attached agents must already be in it) and
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

    /** Groups of existing agents the platform thinks form a workflow (from scanned code and observed traffic). */
    public List<Map<String, Object>> suggestions() {
        return Transport.toList(t.request("GET", "/v1/workflows/suggestions", null, null, null));
    }

    /**
     * Create a workflow from a suggestion: its existing agents become steps (no agent or key is created).
     * {@code body} may hold {@code name}, {@code slug}, {@code description}, {@code owner_identity_id} and
     * {@code edges} ({@code [{from_agent_id, to_agent_id, kind}]}, replaces the suggested ones); may be null.
     * Null values are dropped.
     */
    public Map<String, Object> acceptSuggestion(String fingerprint, Map<String, ?> body) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (body != null) {
            body.forEach((k, v) -> {
                if (v != null) out.put(k, v);
            });
        }
        return Transport.toMap(t.request("POST",
                "/v1/workflows/suggestions/" + Transport.seg(fingerprint) + "/accept", out, null, null));
    }

    /** Hide a suggestion; it stays hidden. */
    public Map<String, Object> dismissSuggestion(String fingerprint) {
        return Transport.toMap(t.request("POST",
                "/v1/workflows/suggestions/" + Transport.seg(fingerprint) + "/dismiss", null, null, null));
    }
}
