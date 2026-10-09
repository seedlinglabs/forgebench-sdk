package ai.forgebench.types;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Builds A2A message parts. */
public final class MessageParts {
    private MessageParts() {}

    /**
     * A2A message parts from the convenient forms: {@code text} becomes a text
     * part, {@code data} a data part, {@code parts} is passed through. At least
     * one must be given.
     *
     * @throws IllegalArgumentException when all three are null/empty
     */
    public static List<Map<String, Object>> of(String text, Map<String, Object> data, List<Map<String, Object>> parts) {
        List<Map<String, Object>> out = parts == null ? new ArrayList<>() : new ArrayList<>(parts);
        if (text != null) {
            Map<String, Object> p = new LinkedHashMap<>();
            p.put("text", text);
            out.add(0, p);
        }
        if (data != null) {
            Map<String, Object> p = new LinkedHashMap<>();
            p.put("data", data);
            out.add(p);
        }
        if (out.isEmpty()) {
            throw new IllegalArgumentException("a task message needs text, data, or parts");
        }
        return out;
    }
}
