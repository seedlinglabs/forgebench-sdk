package ai.forgebench.tracing;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One trace as this process sees it, shared by all of its observations. */
final class TraceState {
    final String id;
    final boolean sampled;
    final Tracing tracing;
    private String name;
    private String userId;
    private String sessionId;
    private final List<String> tags = new ArrayList<>();
    private final Map<String, JsonNode> metadata = new LinkedHashMap<>();
    private boolean dirty;

    TraceState(String id, boolean sampled, Tracing tracing) {
        this.id = id;
        this.sampled = sampled;
        this.tracing = tracing;
    }

    synchronized void update(TraceAttributes attributes) {
        if (attributes.name != null) name = attributes.name;
        if (attributes.userId != null) userId = attributes.userId;
        if (attributes.sessionId != null) sessionId = attributes.sessionId;
        for (String tag : attributes.tags) if (!tags.contains(tag)) tags.add(tag);
        attributes.metadata.forEach((k, v) -> metadata.put(k, tracing.prepare(v)));
        dirty = true;
    }

    synchronized String userId() {
        return userId;
    }

    synchronized String sessionId() {
        return sessionId;
    }

    /**
     * The trace record to send with an ending observation: when the fields
     * changed since the last one, or always for the root ({@code force}).
     */
    synchronized Map<String, Object> takeRecord(boolean force, String defaultName,
                                                String environment, String release) {
        if (!dirty && !force) return null;
        dirty = false;
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("id", id);
        putIfPresent(record, "name", name != null ? name : defaultName);
        putIfPresent(record, "user_id", userId);
        putIfPresent(record, "session_id", sessionId);
        if (!tags.isEmpty()) record.put("tags", new ArrayList<>(tags));
        if (!metadata.isEmpty()) record.put("metadata", new LinkedHashMap<>(metadata));
        putIfPresent(record, "environment", environment);
        putIfPresent(record, "release", release);
        return record;
    }

    private static void putIfPresent(Map<String, Object> map, String key, Object value) {
        if (value != null) map.put(key, value);
    }
}
