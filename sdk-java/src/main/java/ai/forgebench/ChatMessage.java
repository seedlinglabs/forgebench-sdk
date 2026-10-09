package ai.forgebench;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * One chat message. Build one with the factories; a plain
 * {@code Map<String, Object>} with the same wire fields is accepted anywhere a
 * message is.
 *
 * <p>A tool loop needs every field the chokepoint accepts: an assistant turn
 * that carries {@code tool_calls} (and often a null {@code content}), and
 * {@code role: tool} replies keyed by {@code tool_call_id}.
 */
public final class ChatMessage {
    private final String role;
    private final Object content;
    private final List<Map<String, Object>> toolCalls;
    private final String toolCallId;
    private final String name;

    private ChatMessage(String role, Object content, List<Map<String, Object>> toolCalls,
                        String toolCallId, String name) {
        this.role = role;
        this.content = content;
        this.toolCalls = toolCalls;
        this.toolCallId = toolCallId;
        this.name = name;
    }

    public static ChatMessage system(String content) {
        return new ChatMessage("system", content, null, null, null);
    }

    public static ChatMessage user(String content) {
        return new ChatMessage("user", content, null, null, null);
    }

    /** A user message with OpenAI content parts (text, image_url, ...). */
    public static ChatMessage user(List<Map<String, Object>> contentParts) {
        return new ChatMessage("user", contentParts, null, null, null);
    }

    public static ChatMessage assistant(String content) {
        return new ChatMessage("assistant", content, null, null, null);
    }

    /**
     * Echo an assistant turn that called tools back into the history — pass
     * {@code response.getChoices().get(0).getMessage().getToolCalls()}.
     */
    public static ChatMessage assistant(String content, List<Map<String, Object>> toolCalls) {
        return new ChatMessage("assistant", content, toolCalls, null, null);
    }

    /** A tool's result, answering the tool_call with id {@code toolCallId}. */
    public static ChatMessage tool(String toolCallId, String content) {
        return new ChatMessage("tool", content, null, toolCallId, null);
    }

    /** A tool's result, with the tool's name. */
    public static ChatMessage tool(String toolCallId, String name, String content) {
        return new ChatMessage("tool", content, null, toolCallId, name);
    }

    public String getRole() { return role; }
    public Object getContent() { return content; }
    public List<Map<String, Object>> getToolCalls() { return toolCalls; }
    public String getToolCallId() { return toolCallId; }
    public String getName() { return name; }

    /** The wire shape: role, content (null kept), and the tool fields when set. */
    public Map<String, Object> toMap() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("role", role);
        m.put("content", content);
        if (toolCalls != null) m.put("tool_calls", toolCalls);
        if (toolCallId != null) m.put("tool_call_id", toolCallId);
        if (name != null) m.put("name", name);
        return m;
    }
}
