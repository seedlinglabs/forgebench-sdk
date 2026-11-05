package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Environments ({@code /v1/environments}): the named stages (dev, staging, prod) agents are placed in.
 * Reads work on any plan; writes need an admin on a Pro or Enterprise plan (402 {@code plan_required}).
 * Records are plain maps with the server's snake_case keys.
 */
public final class Environments {
    private final Transport t;

    Environments(Transport transport) {
        this.t = transport;
    }

    /** {@code {environments: [...] in promotion order, entitlement: {allowed, required_plan}}}. */
    public Map<String, Object> list() {
        return Transport.toMap(t.request("GET", "/v1/environments", null, null, null));
    }

    /** Create at the end of the promotion path. {@code slug} (default: from the name) and {@code isProduction} may be null. */
    public Map<String, Object> create(String name, String slug, Boolean isProduction) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", name);
        if (slug != null) body.put("slug", slug);
        if (isProduction != null) body.put("is_production", isProduction);
        return Transport.toMap(t.request("POST", "/v1/environments", body, null, null));
    }

    /** Rename and/or flip {@code is_production}; null leaves a field alone. */
    public Map<String, Object> update(String slug, String name, Boolean isProduction) {
        Map<String, Object> body = new LinkedHashMap<>();
        if (name != null) body.put("name", name);
        if (isProduction != null) body.put("is_production", isProduction);
        return Transport.toMap(t.request("PATCH", "/v1/environments/" + Transport.seg(slug), body, null, null));
    }

    /** Set the promotion path: every slug, first stage first. */
    public List<Map<String, Object>> reorder(List<String> order) {
        return Transport.toList(t.request("PUT", "/v1/environments/order", Map.of("order", order), null, null));
    }

    /** Delete an environment that has no agents (409 otherwise). */
    public void delete(String slug) {
        t.request("DELETE", "/v1/environments/" + Transport.seg(slug), null, null, null);
    }
}
