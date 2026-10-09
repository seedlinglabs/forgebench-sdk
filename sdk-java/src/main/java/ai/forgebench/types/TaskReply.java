package ai.forgebench.types;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * What a {@code serve()} handler returns to finish, pause, or fail a task.
 * Build one with {@link Task#done}, {@link Task#ask}, or {@link Task#fail}.
 */
public final class TaskReply {
    private final String state; // completed | input_required | failed
    private final List<Map<String, Object>> artifacts;
    private final Map<String, Object> message;
    private final String error;

    public TaskReply(String state, List<Map<String, Object>> artifacts, Map<String, Object> message, String error) {
        this.state = state;
        this.artifacts = artifacts;
        this.message = message;
        this.error = error;
    }

    public String getState() { return state; }
    public List<Map<String, Object>> getArtifacts() { return artifacts; }
    public Map<String, Object> getMessage() { return message; }
    public String getError() { return error; }

    /** The wire body of {@code POST /v1/agents/me/tasks/{id}/result}. */
    public Map<String, Object> toMap() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("state", state);
        out.put("artifacts", artifacts);
        out.put("message", message);
        out.put("error", error);
        return out;
    }
}
