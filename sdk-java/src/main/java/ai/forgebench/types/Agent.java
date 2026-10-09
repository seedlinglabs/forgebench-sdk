package ai.forgebench.types;

import java.util.LinkedHashMap;
import java.util.Map;

/** An agent registered on the control plane. */
public final class Agent {
    private String id;
    private String name;
    private String model;
    private String description;
    private String systemPrompt;
    private Map<String, Object> config = new LinkedHashMap<>();
    private String createdAt;

    public String getId() { return id; }
    public String getName() { return name; }
    public String getModel() { return model; }
    public String getDescription() { return description; }
    public String getSystemPrompt() { return systemPrompt; }
    public Map<String, Object> getConfig() { return config; }
    public String getCreatedAt() { return createdAt; }

    @Override
    public String toString() {
        return "Agent{id=" + id + ", name=" + name + ", model=" + model + "}";
    }
}
