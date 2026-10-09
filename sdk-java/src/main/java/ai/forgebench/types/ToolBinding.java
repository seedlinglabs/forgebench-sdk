package ai.forgebench.types;

/** One tool the calling agent may currently call — an entry from {@code GET /v1/agent-tools}. */
public final class ToolBinding {
    private String server;
    private String tool;
    private String description = "";

    public String getServer() { return server; }
    public String getTool() { return tool; }
    public String getDescription() { return description; }

    @Override
    public String toString() {
        return "ToolBinding{server=" + server + ", tool=" + tool + "}";
    }
}
