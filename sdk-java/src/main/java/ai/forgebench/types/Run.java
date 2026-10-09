package ai.forgebench.types;

import java.util.LinkedHashMap;
import java.util.Map;

/** One execution in the runs subsystem ({@code /v1/runs}). */
public final class Run {
    private String id;
    private String status;
    private String model;
    private String agentId;
    private Map<String, Object> input = new LinkedHashMap<>();
    private Map<String, Object> output;
    private String error;
    private String createdAt;
    private String startedAt;
    private String finishedAt;

    public String getId() { return id; }
    public String getStatus() { return status; }
    public String getModel() { return model; }
    public String getAgentId() { return agentId; }
    public Map<String, Object> getInput() { return input; }
    public Map<String, Object> getOutput() { return output; }
    public String getError() { return error; }
    public String getCreatedAt() { return createdAt; }
    public String getStartedAt() { return startedAt; }
    public String getFinishedAt() { return finishedAt; }

    /** {@code succeeded} or {@code failed}. */
    public boolean isTerminal() {
        return "succeeded".equals(status) || "failed".equals(status);
    }

    @Override
    public String toString() {
        return "Run{id=" + id + ", status=" + status + ", model=" + model + ", agentId=" + agentId + "}";
    }
}
