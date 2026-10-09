package ai.forgebench.types;

import java.util.List;
import java.util.Map;

/** The assistant message of one choice. */
public final class ChatChoiceMessage {
    private String role;
    private String content;
    // OpenAI-shaped tool_calls the model emitted, passed through by the
    // control plane AFTER its response gates ran — every entry here was
    // allowed for this agent. Feed them to agentTools().dispatch(...) to
    // execute and report them. Null when the model called no tool.
    private List<Map<String, Object>> toolCalls;

    public String getRole() { return role; }
    public String getContent() { return content; }
    public List<Map<String, Object>> getToolCalls() { return toolCalls; }

    @Override
    public String toString() {
        return "ChatChoiceMessage{role=" + role + ", content=" + content + ", toolCalls=" + toolCalls + "}";
    }
}
